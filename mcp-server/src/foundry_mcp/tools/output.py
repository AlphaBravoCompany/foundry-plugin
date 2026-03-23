"""Output management tools — init_run, register_artifact, query_run."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def init_run(
    run_type: str,
    ticket: str = "",
    description: str = "",
    spec_path: str | None = None,
    output_dir: str | None = None,
    project_root: str = ".",
) -> dict:
    """Initialize a new run directory with structured output layout.

    Args:
        run_type: One of "marathon", "mill", "mill-ui".
        ticket: Ticket ID (e.g., "AQUA-123").
        description: Short description for the run.
        spec_path: Optional path to the spec file.
        output_dir: Custom base directory for the run. If provided, the run
                    is created at this path instead of foundry_reports/runs/.
                    Can be absolute or relative to project_root.
        project_root: Project root directory.

    Returns:
        {run_id, run_dir, paths{}, symlinks{}}
    """
    root = Path(project_root)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build run ID: ticket-description-date (user-friendly, ticket first)
    parts: list[str] = []
    if ticket:
        parts.append(ticket)
    if description:
        slug = description.lower().replace(" ", "-")[:40]
        # Remove non-alphanumeric except hyphens
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
        if slug:
            parts.append(slug)
    parts.append(today)
    run_id = "_".join(parts)

    # Create directory structure — respect user-provided output_dir
    if output_dir:
        p = Path(output_dir)
        run_dir = p if p.is_absolute() else root / p
    else:
        runs_dir = root / "foundry_reports" / "runs"
        run_dir = runs_dir / run_id
    persistent_dir = run_dir / "persistent"
    iterations_dir = run_dir / "iterations" / "0"

    for d in [persistent_dir, iterations_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Create persistent files
    persistent_files = [
        "mill-ledger.md",
        "mill-sub-spec.md",
        "mill-lessons.md",
        "suggestion-backlog.md",
        "churn-domains.md",
    ]
    for fname in persistent_files:
        fpath = persistent_dir / fname
        if not fpath.exists():
            fpath.write_text(f"# {fname.replace('.md', '').replace('-', ' ').title()}\n\n", encoding="utf-8")

    # Write manifest
    manifest = {
        "run_id": run_id,
        "run_type": run_type,
        "ticket": ticket,
        "description": description,
        "spec_path": spec_path,
        "created": datetime.now(timezone.utc).isoformat(),
        "iterations": 0,
        "artifacts": [],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Create backward-compat symlinks (quality_reports/ → foundry_reports/runs/{id}/persistent/)
    qr_dir = root / "quality_reports"
    qr_dir.mkdir(parents=True, exist_ok=True)
    symlinks: dict[str, str] = {}
    for fname in ["mill-ledger.md", "mill-sub-spec.md", "mill-lessons.md"]:
        link = qr_dir / fname
        target = persistent_dir / fname
        # Remove existing symlink or file
        if link.is_symlink() or link.exists():
            link.unlink()
        try:
            link.symlink_to(target.resolve())
            symlinks[str(link)] = str(target)
        except OSError:
            # Symlink creation may fail on some systems; copy instead
            shutil.copy2(str(target), str(link))
            symlinks[str(link)] = f"copied from {target}"

    # Also create plans/ and specs/ dirs
    (root / "foundry_reports" / "plans").mkdir(parents=True, exist_ok=True)
    (root / "foundry_reports" / "specs").mkdir(parents=True, exist_ok=True)

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "paths": {
            "persistent": str(persistent_dir),
            "iterations": str(run_dir / "iterations"),
            "manifest": str(run_dir / "manifest.json"),
        },
        "symlinks": symlinks,
    }


def register_artifact(
    run_id: str,
    artifact_type: str,
    iteration: int,
    file_path: str,
    project_root: str = ".",
) -> dict:
    """Register (move) an artifact into the structured run directory.

    Args:
        run_id: The run identifier.
        artifact_type: Type of artifact (e.g., "logical-audit", "critic", "plan").
        iteration: Iteration number.
        file_path: Path to the artifact file to register.
        project_root: Project root directory.

    Returns:
        {registered_path, moved}
    """
    root = Path(project_root)
    run_dir = root / "foundry_reports" / "runs" / run_id
    manifest_path = run_dir / "manifest.json"

    if not run_dir.exists():
        return {"error": f"Run not found: {run_id}", "moved": False}

    source = root / file_path if not Path(file_path).is_absolute() else Path(file_path)
    if not source.exists():
        return {"error": f"File not found: {file_path}", "moved": False}

    # Create iteration dir
    iter_dir = run_dir / "iterations" / str(iteration)
    iter_dir.mkdir(parents=True, exist_ok=True)

    # Determine destination filename
    suffix = source.suffix or ".md"
    dest = iter_dir / f"{artifact_type}{suffix}"

    # Move file
    shutil.move(str(source), str(dest))

    # Update manifest
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"].append({
            "type": artifact_type,
            "iteration": iteration,
            "path": str(dest.relative_to(run_dir)),
            "registered": datetime.now(timezone.utc).isoformat(),
        })
        manifest["iterations"] = max(manifest.get("iterations", 0), iteration)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "registered_path": str(dest),
        "moved": True,
    }


def query_run(
    run_id: str | None = None,
    iteration: int | None = None,
    artifact_type: str | None = None,
    include_content: bool = False,
    project_root: str = ".",
) -> dict:
    """Query artifacts across runs and iterations.

    Args:
        run_id: Filter to specific run. If None, lists all runs.
        iteration: Filter to specific iteration.
        artifact_type: Filter to specific artifact type.
        include_content: Include file content in results.
        project_root: Project root directory.

    Returns:
        {runs[]|artifacts[], total_iterations}
    """
    root = Path(project_root)
    runs_dir = root / "foundry_reports" / "runs"

    if not runs_dir.exists():
        return {"artifacts": [], "total_iterations": 0}

    # List all runs
    if run_id is None:
        runs = []
        for d in sorted(runs_dir.iterdir()):
            if d.is_dir():
                manifest_path = d / "manifest.json"
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    runs.append({
                        "run_id": d.name,
                        "run_type": manifest.get("run_type"),
                        "ticket": manifest.get("ticket"),
                        "description": manifest.get("description"),
                        "created": manifest.get("created"),
                        "iterations": manifest.get("iterations", 0),
                        "artifact_count": len(manifest.get("artifacts", [])),
                    })
        return {"runs": runs}

    # Query specific run
    run_dir = runs_dir / run_id
    if not run_dir.exists():
        return {"error": f"Run not found: {run_id}", "artifacts": []}

    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    artifacts_meta = manifest.get("artifacts", [])

    # Filter
    results = []
    for a in artifacts_meta:
        if iteration is not None and a.get("iteration") != iteration:
            continue
        if artifact_type is not None and a.get("type") != artifact_type:
            continue

        entry = dict(a)
        if include_content:
            full_path = run_dir / a["path"]
            if full_path.exists():
                entry["content"] = full_path.read_text(encoding="utf-8")[:10000]
        results.append(entry)

    return {
        "artifacts": results,
        "total_iterations": manifest.get("iterations", 0),
    }
