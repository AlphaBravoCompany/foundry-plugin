"""Foundry orchestrator tools — phase enforcement, team lifecycle, and guided execution.

Replaces bash script enforcement with typed MCP tools that guide the lead agent
through the foundry loop. Every critical action (phase transitions, team management,
stream verification) goes through these tools instead of raw bash commands.

All operations are local file reads/writes. Zero API calls. Zero cost.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.tools.foundry_state import (
    clear_active_run,
    get_run_dir,
)
from foundry_mcp.tools.display import foundry_hammer, FOUNDRY_SEP

# ANSI colors — shared with display.py
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_BCYAN = f"{_BOLD}{_CYAN}"
_BGREEN = f"{_BOLD}{_GREEN}"
_BYELLOW = f"{_BOLD}{_YELLOW}"
_BRED = f"{_BOLD}{_RED}"
_BWHITE = f"{_BOLD}{_WHITE}"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    """Atomic JSON write — write to .tmp then rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_spec_requirements(project_root: str) -> int:
    """Count requirement IDs (US-N, FR-N, NFR-N, AC-N, VC-N) in the spec file."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return 0
    spec_path = fdir / "spec.md"
    if not spec_path.exists():
        state = _load_json(fdir / "state.json")
        sp = state.get("spec_path", "")
        if sp:
            candidate = Path(project_root) / sp
            if candidate.exists():
                spec_path = candidate
            else:
                return 0
        else:
            return 0

    text = spec_path.read_text(encoding="utf-8")
    req_ids = set(re.findall(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b", text))
    return len(req_ids)


# --- Phase gate ---


def foundry_gate(
    phase: str,
    project_root: str = ".",
) -> dict:
    """Check if preconditions are met to enter a phase."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"phase": phase, "passed": False, "reason": "No active foundry run", "hint": "Call Foundry-Init first"}

    if not fdir.exists():
        return {"phase": phase, "passed": False, "reason": "foundry directory not found", "hint": "Run foundry_init first"}

    checklist: list[dict] = []
    passed = True
    reason = ""
    hint = ""

    nac = fdir / ".next-action-called"
    if not nac.exists():
        return {
            "phase": phase,
            "passed": False,
            "reason": "Must call Foundry-Next before any gate check",
            "hint": "Call Foundry-Next first — it shows the status display and tells you what to do next.",
            "checklist": [{"check": "next_action_called", "ok": False}],
        }
    nac.unlink(missing_ok=True)

    if phase == "cast":
        manifest = fdir / "castings" / "manifest.json"
        if not manifest.exists():
            return {"phase": phase, "passed": False, "reason": "No manifest.json", "hint": "Run foundry_init and add castings"}
        data = _load_json(manifest)
        count = len(data.get("castings", []))
        checklist.append({"check": "manifest_exists", "ok": True})
        if count < 1:
            return {"phase": phase, "passed": False, "reason": "No castings in manifest", "hint": "Add castings before CAST"}
        checklist.append({"check": f"castings_count={count}", "ok": True})

        oversized = []
        for c in data.get("castings", []):
            kf = len(c.get("key_files", []))
            if kf > 8:
                oversized.append({"id": c.get("id"), "title": c.get("title", ""), "key_files": kf})
        if oversized:
            passed = False
            names = ", ".join(f"#{c['id']} ({c['key_files']} files)" for c in oversized)
            reason = f"Oversized castings: {names}. Max 8 key_files per casting."
            hint = "Split large castings into smaller ones (2-5 tasks, 2-8 files each). No teammate should get 1000 lines of work."
            checklist.append({"check": "casting_size", "ok": False, "oversized": oversized})

        file_to_casting: dict[str, list[int]] = {}
        for c in data.get("castings", []):
            cid = c.get("id", 0)
            for f in c.get("key_files", []):
                file_to_casting.setdefault(f, []).append(cid)
        overlaps = {f: cids for f, cids in file_to_casting.items() if len(cids) > 1}
        if overlaps:
            overlap_details = [f"{f}: castings {cids}" for f, cids in overlaps.items()]
            passed = False
            reason = f"File overlap between castings: {'; '.join(overlap_details)}"
            hint = "Two castings editing the same file will cause conflicts. Move shared files to an earlier casting or merge the overlapping castings."
            checklist.append({"check": "no_file_overlap", "ok": False, "overlaps": overlaps})
        else:
            checklist.append({"check": "no_file_overlap", "ok": True})

    elif phase == "inspect":
        if not (fdir / ".cast-complete").exists():
            passed = False
            reason = "CAST not complete"
            hint = "Complete all CAST tasks and call Foundry-Phase(phase='cast')"
            checklist.append({"check": "cast_complete", "ok": False})
        else:
            checklist.append({"check": "cast_complete", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            reason = f"Active teams: {', '.join(teams_result['teams'])}"
            hint = "Shut down all teammates and call Foundry-Team-Down for each"
            checklist.append({"check": "no_active_teams", "ok": False, "teams": teams_result["teams"]})
        else:
            checklist.append({"check": "no_active_teams", "ok": True})

        sight = _check_sight_required(project_root)
        if sight.get("required") and sight.get("blocked"):
            passed = False
            reason = sight["reason"]
            hint = "Provide --url for SIGHT audit or update manifest.json target_url"
            checklist.append({"check": "sight_url", "ok": False, "reason": sight["reason"]})
        else:
            checklist.append({"check": "sight_url", "ok": True})

    elif phase == "grind":
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count < 1:
            passed = False
            reason = "No open defects to grind"
            hint = "Nothing to fix — skip to ASSAY"
        checklist.append({"check": f"open_defects={open_count}", "ok": open_count >= 1})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            reason = f"Active teams: {', '.join(teams_result['teams'])}"
            hint = "Shut down INSPECT teams first"
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})

        if not (fdir / ".tasks-generated").exists():
            passed = False
            reason = "defects-to-tasks has not been run"
            hint = "Call Foundry-Tasks before entering GRIND"
        checklist.append({"check": "tasks_generated", "ok": (fdir / ".tasks-generated").exists()})

    elif phase == "assay":
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count > 0:
            passed = False
            reason = f"{open_count} open defect(s) remain"
            hint = "Fix all defects in GRIND first"
        checklist.append({"check": f"zero_open_defects (have {open_count})", "ok": open_count == 0})

        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            passed = False
            reason = f"Verification streams incomplete: {streams.get('missing', '')}"
            hint = "All streams (trace, prove, sight, test) must complete before ASSAY"
        checklist.append({"check": "all_streams_complete", "ok": streams["complete"],
                         "missing": streams.get("missing", "")})

        if not (fdir / ".inspect-clean").exists():
            has_fixed = sum(1 for d in defects.get("defects", []) if d.get("status") == "fixed")
            if has_fixed > 0:
                passed = False
                reason = "GRIND fixed defects but INSPECT has not re-verified"
                hint = "Run full INSPECT cycle after GRIND. Call foundry_mark_inspect_clean when clean."
            checklist.append({"check": "inspect_clean", "ok": False})
        else:
            checklist.append({"check": "inspect_clean", "ok": True})

        teams_result = _check_active_teams(project_root)
        if teams_result["active"]:
            passed = False
            reason = f"Active teams: {', '.join(teams_result['teams'])}"
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})

    elif phase == "temper":
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        if non_verified > 0:
            passed = False
            reason = f"{non_verified} requirement(s) not verified"
        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})

    elif phase == "done":
        verdicts = _load_json(fdir / "verdicts.json")
        verdict_list = verdicts.get("requirements", [])
        non_verified = sum(1 for r in verdict_list if r.get("verdict") != "VERIFIED")
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        teams_result = _check_active_teams(project_root)

        if non_verified > 0:
            passed = False
            reason = f"{non_verified} requirement(s) not VERIFIED \u2014 THIN/PARTIAL are defects, not follow-ups"
            hint = "Fix all non-VERIFIED requirements. Every THIN item must be fully implemented."
        if open_count > 0:
            passed = False
            reason = f"{open_count} open defect(s) remain"
        if teams_result["active"]:
            passed = False
            reason = f"Active teams: {', '.join(teams_result['teams'])}"

        spec_count = _count_spec_requirements(project_root)
        verdict_count = len(verdict_list)
        verdicts_complete = True
        if spec_count > 0 and verdict_count < spec_count:
            passed = False
            skipped = spec_count - verdict_count
            reason = f"Only {verdict_count} verdicts but spec has {spec_count} requirements. {skipped} skipped."
            hint = "ASSAY must write ALL verdicts to verdicts.json \u2014 including THIN/PARTIAL, not just VERIFIED."
            verdicts_complete = False

        checklist.append({"check": f"all_verified (non_verified={non_verified})", "ok": non_verified == 0})
        checklist.append({"check": f"zero_defects (open={open_count})", "ok": open_count == 0})
        checklist.append({"check": "no_active_teams", "ok": not teams_result["active"]})
        checklist.append({"check": f"verdict_coverage ({verdict_count}/{spec_count})", "ok": verdicts_complete})

    else:
        return {"phase": phase, "passed": False, "reason": f"Unknown phase: {phase}",
                "hint": "Valid phases: cast, inspect, grind, assay, temper, done"}

    result = {"phase": phase, "passed": passed, "checklist": checklist}
    if not passed:
        result["reason"] = reason
        result["hint"] = hint
    return result


# --- Stream markers ---


def foundry_mark_stream(
    stream: str,
    cycle: int,
    items_checked: int = 0,
    items_total: int = 0,
    findings_count: int = 0,
    project_root: str = ".",
) -> dict:
    """Mark a verification stream as complete for this cycle."""
    valid = {"trace", "prove", "sight", "test", "probe"}
    if stream not in valid:
        return {"error": f"Invalid stream: {stream}. Must be one of: {', '.join(sorted(valid))}"}

    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    if items_checked <= 0:
        return {
            "error": f"Cannot mark {stream} complete with items_checked={items_checked}. "
                     "You must report how many items were actually checked. "
                     "trace: symbols checked. prove: requirements checked. "
                     "sight: pages/elements exercised. test: tests run. "
                     "probe: endpoints hit.",
            "hint": "If the stream genuinely checked 0 items, the scope may be wrong.",
        }

    if stream == "prove" and items_total > 0:
        spec_count = _count_spec_requirements(project_root)
        if spec_count > 0 and items_checked < spec_count * 0.95:
            return {
                "error": f"PROVE checked {items_checked} requirements but spec has {spec_count}. "
                         f"Coverage is {items_checked/spec_count*100:.0f}% \u2014 must be \u226595%.",
                "hint": "Re-run PROVE and check ALL requirements in the spec. No skipping.",
                "spec_requirements": spec_count,
                "checked": items_checked,
            }

    if stream == "trace" and items_total > 0 and items_checked < items_total * 0.95:
        return {
            "error": f"TRACE checked {items_checked}/{items_total} symbols ({items_checked/items_total*100:.0f}%). "
                     "Must check \u226595% of declared symbols.",
            "hint": "Re-run TRACE on unchecked symbols.",
        }

    coverage_pct = f"{items_checked / items_total * 100:.0f}%" if items_total > 0 else "N/A"

    marker = fdir / f".{stream}-complete"
    coverage_warning = ""
    if marker.exists():
        prev_text = marker.read_text(encoding="utf-8")
        for line in prev_text.split("\n"):
            if line.startswith("items_checked="):
                try:
                    prev_checked = int(line.split("=")[1])
                    if items_checked < prev_checked * 0.7:
                        coverage_warning = (
                            f"Coverage dropped: checked {items_checked} items vs "
                            f"{prev_checked} in previous cycle. Are you rushing?"
                        )
                except (ValueError, IndexError):
                    pass

    marker.write_text(
        f"{_now()} cycle={cycle}\n"
        f"items_checked={items_checked}\n"
        f"items_total={items_total}\n"
        f"coverage={coverage_pct}\n"
        f"findings={findings_count}\n",
        encoding="utf-8",
    )

    result: dict = {
        "ok": True,
        "stream": stream,
        "cycle": cycle,
        "items_checked": items_checked,
        "items_total": items_total,
        "coverage": coverage_pct,
        "findings": findings_count,
    }
    if coverage_warning:
        result["warning"] = coverage_warning

    return result


def _check_streams_complete(project_root: str) -> dict:
    """Check if all required verification streams have completed."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"complete": False, "missing": "all", "required": []}
    manifest = fdir / "castings" / "manifest.json"

    required = ["trace", "prove", "test"]

    no_ui = False
    url = ""
    if manifest.exists():
        mdata = _load_json(manifest)
        no_ui = mdata.get("no_ui", False)
        url = mdata.get("target_url", "")

    if not no_ui:
        required.append("sight")
    else:
        sight = _check_sight_required(project_root)
        if sight.get("required"):
            required.append("sight")

    if url:
        required.append("probe")

    missing = [s for s in required if not (fdir / f".{s}-complete").exists()]

    return {"complete": len(missing) == 0, "missing": " ".join(missing), "required": required}


# --- Phase lifecycle markers ---


def _update_phase(fdir: Path, new_phase: str) -> None:
    """Update state.json with the new phase. Tracks timing per phase."""
    state_path = fdir / "state.json"
    state = _load_json(state_path)
    now = _now()

    prev_phase = state.get("phase", "")
    phase_times = state.get("phase_times", {})
    if prev_phase and prev_phase in phase_times:
        phase_times[prev_phase]["ended_at"] = now
        try:
            start = datetime.fromisoformat(phase_times[prev_phase]["started_at"])
            end = datetime.fromisoformat(now)
            delta = end - start
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            phase_times[prev_phase]["duration"] = f"{mins}m {secs}s"
        except (ValueError, KeyError):
            pass

    phase_times[new_phase] = {"started_at": now}

    state["phase"] = new_phase
    state["updated_at"] = now
    state["phase_times"] = phase_times
    history = state.get("phase_history", [])
    history.append({"phase": new_phase, "entered_at": now})
    state["phase_history"] = history

    if new_phase == "F6":
        state["ended_at"] = now
        started = state.get("started_at", "")
        if started:
            try:
                start = datetime.fromisoformat(started)
                end = datetime.fromisoformat(now)
                delta = end - start
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                secs = int(delta.total_seconds() % 60)
                state["total_duration"] = f"{hours}h {mins}m {secs}s"
            except ValueError:
                pass

    _save_json(state_path, state)


def foundry_mark_phase_complete(
    phase: str,
    project_root: str = ".",
) -> dict:
    """Mark a phase transition. Validates preconditions AND updates state.json.phase."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    nac = fdir / ".next-action-called"
    if not nac.exists():
        return {
            "error": "Must call Foundry-Next before phase transitions",
            "hint": "Call Foundry-Next first \u2014 it shows status and guides you.",
        }
    nac.unlink(missing_ok=True)

    if phase == "start_cast":
        _update_phase(fdir, "F1")
        return {"ok": True, "phase": "F1", "message": "Phase is now F1 (CAST). Create team and build."}

    elif phase == "cast":
        teams = _check_active_teams(project_root)
        if teams["active"]:
            return {"error": f"Cannot mark CAST complete \u2014 active teams: {', '.join(teams['teams'])}",
                    "hint": "Shut down all teammates and TeamDelete before marking CAST complete"}
        (fdir / ".cast-complete").write_text(f"{_now()}\n", encoding="utf-8")
        _update_phase(fdir, "F2")
        return {"ok": True, "phase": "F2", "message": "CAST complete \u2192 phase is now F2 (INSPECT)"}

    elif phase == "inspect_clean":
        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            return {"error": f"Cannot mark INSPECT clean \u2014 streams incomplete: {streams['missing']}",
                    "hint": "Run all required verification streams first"}
        defects = _load_json(fdir / "defects.json")
        open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")
        if open_count > 0:
            return {"error": f"Cannot mark INSPECT clean \u2014 {open_count} open defect(s) remain",
                    "hint": "All defects must be fixed before marking clean"}
        (fdir / ".inspect-clean").write_text(f"{_now()}\n", encoding="utf-8")
        _update_phase(fdir, "F4")
        return {"ok": True, "phase": "F4", "message": "INSPECT clean \u2192 phase is now F4 (ASSAY)"}

    elif phase == "grind_start":
        for marker in [".trace-complete", ".prove-complete", ".sight-complete",
                       ".test-complete", ".probe-complete", ".inspect-clean", ".tasks-generated"]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "All markers cleared \u2192 phase is now F3 (GRIND). Full INSPECT must re-run after."}

    elif phase == "assay_fail":
        for marker in [".trace-complete", ".prove-complete", ".sight-complete",
                       ".test-complete", ".probe-complete", ".inspect-clean", ".tasks-generated"]:
            (fdir / marker).unlink(missing_ok=True)
        _update_phase(fdir, "F3")
        return {"ok": True, "phase": "F3",
                "message": "ASSAY failed \u2192 phase is now F3 (GRIND). Fix defects, then full INSPECT, then ASSAY again."}

    elif phase == "temper":
        _update_phase(fdir, "F5")
        return {"ok": True, "phase": "F5", "message": "Phase is now F5 (TEMPER)"}

    elif phase == "done":
        _update_phase(fdir, "F6")
        # Clear the active run — session is done with this run
        clear_active_run()
        return {"ok": True, "phase": "F6", "message": "Phase is now F6 (DONE). Run archived. Start a new run with foundry_init."}

    else:
        return {"error": f"Invalid phase: {phase}. Valid: cast, inspect_clean, grind_start, assay_fail, temper, done"}


# --- Team lifecycle ---


def _scan_tmux_panes() -> dict:
    """Scan all tmux panes and classify them.

    Claude Code spawns teammates as PANES within the lead's tmux session
    (via split-window). Pane titles are set to the agent name (e.g., "@cast-c1").

    IMPORTANT: pane_current_command for a live teammate is the Claude Code
    VERSION NUMBER (e.g., "2.1.80"), NOT "claude" or "node" or "bash".
    A zombie pane shows "bash"/"zsh" because the agent exited and the shell
    is all that's left. But a live teammate's bash shell has the agent as a
    child process, so pane_current_command reflects the agent binary.

    We use pane title + child process check for definitive classification:
    - LEAD: the active pane
    - LIVE: teammate pane whose bash PID has child processes (agent running)
    - ZOMBIE: teammate pane that is dead OR whose bash PID has NO children
    - USER: non-lead pane that doesn't look like a teammate (left alone)

    Teammate detection: Claude Code sets pane titles via `select-pane -T`.
    Teammate panes have titles starting with "@" or matching agent naming
    patterns (cast-, grind-, etc.). User's personal panes are never touched.

    Returns {
        "available": bool,
        "live": [(id, title, cmd)],
        "zombie": [(id, title, cmd)],
        "user": [(id, title, cmd)],   # user's panes — never touched
        "lead": (id, title) | None,
    }
    """
    import subprocess
    import re

    empty: dict = {"available": False, "live": [], "zombie": [], "user": [], "lead": None}
    try:
        check = subprocess.run(["tmux", "list-sessions"], capture_output=True, timeout=5)
        if check.returncode != 0:
            return empty
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return empty

    # Patterns that identify a pane as a Claude Code teammate
    _TEAMMATE_RE = re.compile(
        r"^@|"                                 # Claude Code prefixes teammate titles with @
        r"cast[-_]|grind[-_]|inspect[-_]|"     # foundry phase agents
        r"assay[-_]|temper[-_]|decompose[-_]|" # foundry phase agents
        r"trace[-_]|prove[-_]|sight[-_]|"      # verification stream agents
        r"test[-_]|probe[-_]|"                 # verification stream agents
        r"^teammate-|^agent-",                 # generic teammate patterns
        re.IGNORECASE,
    )

    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}:#{window_index}.#{pane_index}\t"
             "#{pane_title}\t#{pane_dead}\t#{pane_current_command}\t"
             "#{pane_active}\t#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return empty
    except (subprocess.TimeoutExpired, OSError):
        return empty

    live = []
    zombie = []
    user = []
    lead = None

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 5)
        if len(parts) < 6:
            continue
        pane_id, title, dead, cmd, active, pid = parts

        if active == "1":
            lead = (pane_id, title)
            continue

        # Only touch panes that look like teammates
        if not _TEAMMATE_RE.search(title):
            user.append((pane_id, title, cmd))
            continue

        # Dead panes are always zombies
        if dead == "1":
            zombie.append((pane_id, title, cmd))
            continue

        # Check if the pane's process has children (= agent still running)
        has_children = _pid_has_children(pid)
        if has_children:
            live.append((pane_id, title, cmd))
        else:
            zombie.append((pane_id, title, cmd))

    return {"available": True, "live": live, "zombie": zombie, "user": user, "lead": lead}


def _pid_has_children(pid: str) -> bool:
    """Check if a PID has child processes (i.e., agent is still running)."""
    import subprocess

    if not pid or not pid.strip().isdigit():
        return False
    try:
        # pgrep -P returns 0 if children exist, 1 if none
        result = subprocess.run(
            ["pgrep", "-P", pid],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _kill_panes(panes: list[tuple[str, str, str]]) -> int:
    """Kill a list of (pane_id, title, cmd) tuples.

    Kills in REVERSE order to avoid index shifting — tmux reindexes
    panes when siblings are killed, so killing from highest index
    first prevents targeting the wrong pane.

    Returns count killed.
    """
    import subprocess

    # Sort by pane index descending so kills don't shift targets
    sorted_panes = sorted(panes, key=lambda p: p[0], reverse=True)
    killed = 0
    for pane_id, _title, _cmd in sorted_panes:
        try:
            subprocess.run(["tmux", "kill-pane", "-t", pane_id],
                           capture_output=True, timeout=5)
            killed += 1
        except (subprocess.TimeoutExpired, OSError):
            pass
    return killed


def _check_active_teams(project_root: str) -> dict:
    """Check if any registered teams still have directories."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"active": False, "teams": []}
    state = _load_json(fdir / "state.json")
    teams = state.get("active_teams", [])

    teams_dir = Path.home() / ".claude" / "teams"
    active = [t for t in teams if (teams_dir / t).is_dir()]

    return {"active": len(active) > 0, "teams": active}


def foundry_register_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Register a team for lifecycle tracking."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init first."}
    state_path = fdir / "state.json"
    state = _load_json(state_path)

    teams = state.get("active_teams", [])

    teams_dir = Path.home() / ".claude" / "teams"
    still_active = [t for t in teams if t != team_name and (teams_dir / t).is_dir()]
    if still_active:
        return {
            "error": f"Cannot register '{team_name}' \u2014 active teams exist: {', '.join(still_active)}",
            "hint": "Shut down existing teammates (SendMessage + TeamDelete) and Foundry-Team-Down before creating a new team. One team at a time.",
            "active_teams": still_active,
        }

    if team_name not in teams:
        teams.append(team_name)
    state["active_teams"] = teams
    _save_json(state_path, state)

    return {"ok": True, "registered": team_name, "total_teams": len(teams)}


def foundry_unregister_team(
    team_name: str,
    project_root: str = ".",
) -> dict:
    """Unregister a team with verified teardown.

    Three-phase verification:
    1. CHECK: team directory gone (TeamDelete was called)
    2. CHECK: no live claude processes in non-lead panes
    3. CLEAN: kill zombie panes (dead + idle shells)
    4. UNREGISTER: remove from foundry state

    Blocks if steps 1 or 2 fail — forces proper shutdown ordering.
    """
    import time

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}

    # ── Phase 1: Verify TeamDelete was called ────────────────────────
    teams_dir = Path.home() / ".claude" / "teams"
    if (teams_dir / team_name).is_dir():
        return {
            "error": f"Team directory still exists: ~/.claude/teams/{team_name}/",
            "hint": (
                "TeamDelete must be called BEFORE Foundry-Team-Down. "
                "Proper order: SendMessage(shutdown) to each teammate -> "
                "wait for acknowledgment -> TeamDelete -> Foundry-Team-Down."
            ),
            "phase": "team_dir_exists",
        }

    # ── Phase 2: Verify no live teammate processes ───────────────────
    scan = _scan_tmux_panes()
    if scan["available"] and scan["live"]:
        live_titles = [title for _, title, _cmd in scan["live"]]
        return {
            "error": f"{len(scan['live'])} teammate pane(s) still running: {', '.join(live_titles)}",
            "hint": (
                "Teammates are still alive — they have active claude processes. "
                "Send 'All work complete, stop working.' to each teammate, "
                "wait for them to exit, then call Foundry-Team-Down again."
            ),
            "phase": "live_teammates",
            "live_panes": live_titles,
        }

    # ── Phase 3: Kill zombie panes ───────────────────────────────────
    killed = 0
    if scan["available"] and scan["zombie"]:
        killed = _kill_panes(scan["zombie"])
        # Brief wait + re-scan to verify
        time.sleep(1)
        rescan = _scan_tmux_panes()
        remaining_zombie = len(rescan.get("zombie", []))
        remaining_live = len(rescan.get("live", []))
        if remaining_zombie > 0 or remaining_live > 0:
            # Retry once
            if rescan.get("zombie"):
                killed += _kill_panes(rescan["zombie"])
            time.sleep(1)
            rescan = _scan_tmux_panes()
            remaining_zombie = len(rescan.get("zombie", []))
            remaining_live = len(rescan.get("live", []))
            if remaining_zombie > 0 or remaining_live > 0:
                return {
                    "error": (
                        f"Panes still alive after cleanup: "
                        f"{remaining_live} live, {remaining_zombie} zombie. "
                        "Kill manually: tmux kill-server"
                    ),
                    "phase": "cleanup_failed",
                    "killed": killed,
                }

    # ── Phase 4: Unregister from foundry state ───────────────────────
    state_path = fdir / "state.json"
    state = _load_json(state_path)
    teams = state.get("active_teams", [])
    teams = [t for t in teams if t != team_name]
    state["active_teams"] = teams
    _save_json(state_path, state)

    return {
        "ok": True,
        "unregistered": team_name,
        "remaining_teams": len(teams),
        "tmux_panes_killed": killed,
        "verified_clean": True,
    }


# --- SIGHT enforcement ---


def _check_sight_required(project_root: str) -> dict:
    """Check if SIGHT audit is required based on frontend files in castings."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"required": False}
    manifest = fdir / "castings" / "manifest.json"

    if not manifest.exists():
        return {"required": False}

    data = _load_json(manifest)
    ui_exts = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html", ".astro"}

    ui_files = []
    for casting in data.get("castings", []):
        for f in casting.get("key_files", []):
            if any(f.endswith(ext) for ext in ui_exts):
                ui_files.append(f)

    if not ui_files:
        return {"required": False, "reason": "No frontend files in castings"}

    url = data.get("target_url", "")
    no_ui = data.get("no_ui", False)

    if no_ui:
        return {"required": True, "blocked": True, "ui_files": len(ui_files),
                "reason": f"--no-ui set but {len(ui_files)} frontend files in scope"}
    if not url:
        return {"required": True, "blocked": True, "ui_files": len(ui_files),
                "reason": f"No --url provided but {len(ui_files)} frontend files in scope"}

    return {"required": True, "blocked": False, "url": url, "ui_files": len(ui_files)}


# --- Defect lifecycle ---


def foundry_mark_defect_fixed(
    defect_id: str,
    cycle: int,
    project_root: str = ".",
) -> dict:
    """Mark a defect as fixed in this cycle."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    defects_path = fdir / "defects.json"
    data = _load_json(defects_path)

    found = False
    for d in data.get("defects", []):
        if d["id"] == defect_id:
            d["status"] = "fixed"
            d["fixed_in_cycle"] = cycle
            found = True
            break

    if not found:
        return {"error": f"Defect {defect_id} not found"}

    _save_json(defects_path, data)

    forge_log = fdir / "forge-log.md"
    if forge_log.exists():
        with open(forge_log, "a", encoding="utf-8") as f:
            f.write(f"\n**{defect_id} FIXED** in cycle {cycle} ({_now()})\n\n")

    open_count = sum(1 for d in data["defects"] if d.get("status") == "open")
    return {"ok": True, "defect_id": defect_id, "fixed_in_cycle": cycle, "remaining_open": open_count}


def foundry_sync_defects(
    cycle: int,
    findings: list[dict],
    project_root: str = ".",
) -> dict:
    """Sync new findings against existing defects. Detects regressions."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    defects_path = fdir / "defects.json"
    data = _load_json(defects_path)
    if "defects" not in data:
        data["defects"] = []

    fixed = [d for d in data["defects"] if d.get("status") == "fixed"]
    reopened = 0
    added = 0
    regressions: list[str] = []

    for finding in findings:
        symbol = finding.get("symbol", "")
        desc = finding.get("description", "")

        match_id = None
        for fd in fixed:
            fd_sym = fd.get("symbol", "")
            fd_desc = fd.get("description", "")
            if symbol and fd_sym and symbol == fd_sym:
                match_id = fd["id"]
                break
            if desc and fd_desc and desc == fd_desc:
                match_id = fd["id"]
                break

        if match_id:
            for d in data["defects"]:
                if d["id"] == match_id:
                    d["status"] = "open"
                    d["regression"] = True
                    d["reopened_in_cycle"] = cycle
                    d["fixed_in_cycle"] = None
                    break
            reopened += 1
            regressions.append(match_id)
        else:
            defect_id = f"D-{len(data['defects']) + 1:03d}"
            source = finding.get("source", "inspect")
            valid_sources = {"trace", "prove", "sight", "test", "assay", "temper"}
            if source not in valid_sources:
                source = "trace"

            defect = {
                "id": defect_id,
                "cycle": cycle,
                "source": source,
                "type": finding.get("type", "MISSING"),
                "description": desc,
                "spec_ref": finding.get("spec_ref", ""),
                "symbol": symbol,
                "file": finding.get("file", ""),
                "status": "open",
                "fixed_in_cycle": None,
                "created_at": _now(),
            }
            data["defects"].append(defect)
            added += 1

    _save_json(defects_path, data)

    if regressions:
        forge_log = fdir / "forge-log.md"
        if forge_log.exists():
            with open(forge_log, "a", encoding="utf-8") as f:
                f.write(f"\n### REGRESSIONS in cycle {cycle}\n")
                for r in regressions:
                    f.write(f"- **{r}** reopened \u2014 fix was fragile\n")
                f.write("\n")

    return {
        "ok": True,
        "cycle": cycle,
        "added": added,
        "reopened": reopened,
        "regressions": regressions,
        "total_open": sum(1 for d in data["defects"] if d.get("status") == "open"),
    }


def foundry_defects_to_tasks(
    project_root: str = ".",
) -> dict:
    """Convert ALL open defects to grouped task descriptions for GRIND."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    data = _load_json(fdir / "defects.json")
    open_defects = [d for d in data.get("defects", []) if d.get("status") == "open"]

    if not open_defects:
        return {"ok": True, "tasks": [], "count": 0}

    MAX_PER_GROUP = 3
    groups: dict[str, list[dict]] = {}
    for d in open_defects:
        key = d.get("file") or d.get("symbol") or d["id"]
        groups.setdefault(key, []).append(d)

    tasks = []
    for key, defects in groups.items():
        for i in range(0, len(defects), MAX_PER_GROUP):
            chunk = defects[i:i + MAX_PER_GROUP]
            task = {
                "defect_ids": [d["id"] for d in chunk],
                "description": "; ".join(d["description"] for d in chunk),
                "files": list({d["file"] for d in chunk if d.get("file")}),
                "symbols": list({d["symbol"] for d in chunk if d.get("symbol")}),
                "spec_refs": list({d["spec_ref"] for d in chunk if d.get("spec_ref")}),
                "regression": any(d.get("regression") for d in chunk),
                "source": chunk[0].get("source", "unknown"),
            }
            tasks.append(task)

    (fdir / ".tasks-generated").write_text(f"{_now()} count={len(tasks)}\n", encoding="utf-8")

    return {"ok": True, "tasks": tasks, "count": len(tasks)}


# --- The big one: next action ---


def foundry_next_action(
    project_root: str = ".",
) -> dict:
    """Determine what the lead should do next based on current foundry state."""
    result = _compute_next_action(project_root)

    directives = _read_directives(project_root)
    if directives["has_directives"]:
        result["directives"] = {
            "urgent": directives["urgent"],
            "normal": directives["normal"],
        }
        if directives["urgent"]:
            urgent_text = " | ".join(directives["urgent"])
            result["instructions"] = (
                f"HUMAN DIRECTIVE (urgent): {urgent_text}\n\n"
                f"Incorporate the above into your current action. Then:\n\n"
                + result["instructions"]
            )
        elif directives["normal"]:
            normal_text = " | ".join(directives["normal"])
            result["instructions"] = (
                result["instructions"]
                + f"\n\nHUMAN DIRECTIVE: {normal_text} \u2014 incorporate into your approach."
            )

    result["instructions"] += (
        "\n\nCRITICAL RULES:"
        "\n- NEVER ask 'Want me to proceed?' or 'Should I continue?' \u2014 just do it."
        "\n- NEVER stop between phases. Call Foundry-Next after each step and follow it."
        "\n- NEVER skip SIGHT because 'no URL.' If frontend files exist, you need a URL. Gate will block."
        "\n- If the user typed a message, treat it as a directive. Absorb and keep going."
        "\n- Zero approval gates. The foundry runs until F6 DONE or an error stops it."
    )

    result["display"] = _format_status_display(project_root)

    fdir = get_run_dir(project_root)
    if fdir and fdir.exists():
        (fdir / ".next-action-called").write_text(f"{_now()}\n", encoding="utf-8")

    return result


def _format_status_display(project_root: str) -> str:
    """Generate foundry status display with pixel-art hammer header."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return ""

    state = _load_json(fdir / "state.json")
    phase = state.get("phase", "F0")
    phase_times = state.get("phase_times", {})
    started = state.get("started_at", "")
    cycle = state.get("cycle", 0)

    elapsed = ""
    if started:
        try:
            start = datetime.fromisoformat(started)
            now = datetime.now(timezone.utc)
            delta = now - start
            elapsed_secs = int(delta.total_seconds())
            h = elapsed_secs // 3600
            m = (elapsed_secs % 3600) // 60
            s = elapsed_secs % 60
            if h > 0:
                elapsed = f"{h}h {m}m {s}s"
            elif m > 0:
                elapsed = f"{m}m {s}s"
            else:
                elapsed = f"{s}s"
        except ValueError:
            pass

    phases = [
        ("F0", "DECOMPOSE"), ("F1", "CAST"), ("F2", "INSPECT"),
        ("F3", "GRIND"), ("F4", "ASSAY"), ("F5", "TEMPER"), ("F6", "DONE"),
    ]

    phase_names = dict(phases)
    phase_name = phase_names.get(phase, phase)
    run_name = fdir.name

    lines = [foundry_hammer(f"F O U N D R Y  {_BCYAN}{phase} {phase_name}{_RESET}  Cycle: {cycle}  {elapsed}")]

    # Phase list
    for pid, pname in phases:
        timing = phase_times.get(pid, {})
        dur = timing.get("duration", "")

        if pid == phase:
            icon = f"{_BGREEN}\u25b6{_RESET}"
            label = f"{_BWHITE}{pid} {pname}{_RESET}"
            right = f"  {_BGREEN}\u25c0 {elapsed}{_RESET}"
        elif dur or timing.get("started_at"):
            icon = f"{_GREEN}\u2713{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"  {_DIM}{dur}{_RESET}" if dur else ""
        elif pid == "F5" and not state.get("temper", False):
            icon = f"{_DIM}\u2500{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"  {_DIM}skip{_RESET}"
        else:
            icon = f"{_DIM}\u25cb{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = ""

        lines.append(f"  {icon} {label}{right}")

    # Defects
    defects = _load_json(fdir / "defects.json")
    all_d = defects.get("defects", [])
    open_d = sum(1 for d in all_d if d.get("status") == "open")
    fixed_d = sum(1 for d in all_d if d.get("status") == "fixed")
    regressed = sum(1 for d in all_d if d.get("regression"))

    if all_d:
        defect_line = f"  {_BWHITE}Defects:{_RESET} {_BYELLOW}{open_d} open{_RESET}  {_BGREEN}{fixed_d} fixed{_RESET}"
        if regressed:
            defect_line += f"  {_BRED}{regressed} regressed{_RESET}"
        lines.append(defect_line)

    # Verdicts
    verdicts = _load_json(fdir / "verdicts.json")
    reqs = verdicts.get("requirements", [])
    if reqs:
        verified = sum(1 for r in reqs if r.get("verdict") == "VERIFIED")
        v_bar_len = 15
        v_filled = int((verified / len(reqs)) * v_bar_len) if reqs else 0
        v_bar = f"{_BGREEN}{'\u2588' * v_filled}{_DIM}{'\u2591' * (v_bar_len - v_filled)}{_RESET}"
        lines.append(f"  {_BWHITE}Verdicts:{_RESET} {v_bar} {verified}/{len(reqs)}")

    # Streams
    streams = _check_streams_complete(project_root)
    if phase in ("F2", "F4") or streams.get("required"):
        req_streams = streams.get("required", [])
        missing_s = streams.get("missing", "").split()
        stream_icons = []
        for s in ["trace", "prove", "sight", "test", "probe"]:
            if s in req_streams:
                if s not in missing_s:
                    stream_icons.append(f"[{_GREEN}\u2713{_RESET}]{s}")
                else:
                    stream_icons.append(f"[{_DIM} {_RESET}]{s}")
        if stream_icons:
            lines.append(f"  {_BWHITE}Streams:{_RESET}  {' '.join(stream_icons)}")

    # Teams
    teams = _check_active_teams(project_root)
    if teams["active"]:
        team_str = ", ".join(teams["teams"])
        if len(team_str) > 40:
            team_str = team_str[:37] + "..."
        lines.append(f"  {_BWHITE}Teams:{_RESET}    {_BCYAN}{team_str}{_RESET}")

    lines.append(FOUNDRY_SEP)

    return "\n".join(lines)


def _compute_next_action(project_root: str) -> dict:
    """Internal: compute next action without directive overlay."""
    fdir = get_run_dir(project_root)

    if not fdir or not fdir.exists():
        return {
            "phase": "none",
            "action": "init",
            "instructions": "No active foundry run. Call Foundry-Init to start a new run, or foundry_init(resume='run-name') to resume.",
            "details": {},
        }

    state = _load_json(fdir / "state.json")
    phase = state.get("phase", "F0")

    teams = _check_active_teams(project_root)
    if teams["active"]:
        return {
            "phase": phase,
            "action": "cleanup_teams",
            "instructions": (
                f"Active teams detected: {', '.join(teams['teams'])}. "
                "Shut down all teammates (SendMessage 'All work complete, stop working' + TeamDelete), "
                "then call Foundry-Team-Down for each team name. "
                "Unregister automatically kills lingering tmux panes."
            ),
            "details": {"active_teams": teams["teams"]},
        }

    defects = _load_json(fdir / "defects.json")
    open_count = sum(1 for d in defects.get("defects", []) if d.get("status") == "open")

    # --- Agent config per phase (ENFORCED, not suggestions) ---
    # These are the exact parameters the lead MUST use when spawning agents.
    CAST_AGENT_CONFIG = {
        "model": "opus",
        "subagent_type": "general-purpose",
        "mode": "bypassPermissions",
    }
    INSPECT_TRACE_CONFIG = {
        "model": "opus",
        "subagent_type": "Explore",
        "description": "TRACE: LSP wiring verification",
    }
    INSPECT_PROVE_CONFIG = {
        "model": "opus",
        "subagent_type": "Explore",
        "description": "PROVE: spec-to-code citation verification",
    }
    GRIND_AGENT_CONFIG = {
        "model": "opus",
        "subagent_type": "general-purpose",
        "mode": "bypassPermissions",
    }
    ASSAY_AGENT_CONFIG = {
        "model": "opus",
        "subagent_type": "Explore",
        "description": "ASSAY: fresh-eyes spec-before-code verification",
    }

    if phase == "F0":
        manifest = _load_json(fdir / "castings" / "manifest.json")
        casting_count = len(manifest.get("castings", []))
        if casting_count == 0:
            return {
                "phase": "F0",
                "action": "add_castings",
                "instructions": (
                    f"DECOMPOSE: Create a team to write casting files.\n"
                    f"1. Write the manifest: {fdir}/castings/manifest.json\n"
                    f"2. Create a decompose team (TeamCreate), spawn teammates to write casting-N.md files.\n"
                    f"   Each teammate writes 2-3 castings. DO NOT write castings in the main context.\n"
                    f"3. All files go under {fdir}/castings/\n"
                    f"   DO NOT write to castings/ at project root."
                ),
                "details": {"foundry_dir": str(fdir), "agent_config": CAST_AGENT_CONFIG},
            }
        return {
            "phase": "F0",
            "action": "transition_to_cast",
            "instructions": (
                f"Decomposition complete ({casting_count} castings). "
                "Call Foundry-Gate(phase='cast') to validate, then Foundry-Phase(phase='start_cast'). "
                "Create a CAST team (TeamCreate), register it (Foundry-Team-Up). "
                "Spawn ONE teammate per casting (or per wave of independent castings). "
                "Do NOT overload one teammate with many castings \u2014 distribute evenly."
            ),
            "details": {"casting_count": casting_count, "agent_config": CAST_AGENT_CONFIG},
        }

    elif phase == "F1":
        if not (fdir / ".cast-complete").exists():
            return {
                "phase": "F1",
                "action": "build_castings",
                "instructions": (
                    "CAST phase: teammates are building. Wait for all tasks to complete. "
                    "When done: shut down team, TeamDelete, Foundry-Team-Down, "
                    "then Foundry-Phase(phase='cast')."
                ),
                "details": {"agent_config": CAST_AGENT_CONFIG},
            }
        return {
            "phase": "F1",
            "action": "transition_to_inspect",
            "instructions": (
                "CAST complete. Call Foundry-Gate(phase='inspect') to validate preconditions, "
                "then update state to F2. Spawn verification agents for TRACE, PROVE. "
                "SIGHT runs in MAIN THREAD. TEST/PROBE run as background agents."
            ),
            "details": {
                "agent_configs": {
                    "trace": INSPECT_TRACE_CONFIG,
                    "prove": INSPECT_PROVE_CONFIG,
                    "test": {"model": "opus", "subagent_type": "general-purpose"},
                },
            },
        }

    elif phase == "F2":
        streams = _check_streams_complete(project_root)
        if not streams["complete"]:
            return {
                "phase": "F2",
                "action": "run_streams",
                "instructions": (
                    f"INSPECT phase: verification streams incomplete. Missing: {streams['missing']}. "
                    "Spawn agents using the agent_configs below (model and type are ENFORCED). "
                    "SIGHT runs in MAIN THREAD (Playwright MCP only works here) \u2014 "
                    "navigate to URL, snapshot every page, exercise all elements, check console. "
                    "After each stream, call Foundry-Stream(stream, cycle, items_checked)."
                ),
                "details": {
                    "missing_streams": streams["missing"].split(),
                    "required": streams["required"],
                    "agent_configs": {
                        "trace": INSPECT_TRACE_CONFIG,
                        "prove": INSPECT_PROVE_CONFIG,
                        "test": {"model": "opus", "subagent_type": "general-purpose"},
                    },
                },
            }

        if open_count > 0:
            return {
                "phase": "F2",
                "action": "transition_to_grind",
                "instructions": (
                    f"INSPECT complete: {open_count} open defect(s) found. "
                    "Call Foundry-Tasks to generate task list, "
                    "then Foundry-Gate(phase='grind'), then update state to F3. "
                    "Call Foundry-Phase(phase='grind_start') to clear markers. "
                    "Create grind team, assign tasks."
                ),
                "details": {"open_defects": open_count, "agent_config": GRIND_AGENT_CONFIG},
            }

        return {
            "phase": "F2",
            "action": "transition_to_assay",
            "instructions": (
                "INSPECT clean: zero defects. Call Foundry-Phase(phase='inspect_clean'), "
                "then Foundry-Gate(phase='assay'), then update state to F4. "
                "Spawn 4 parallel assayer agents using the config below (opus, Explore, effort max)."
            ),
            "details": {"open_defects": 0, "agent_config": ASSAY_AGENT_CONFIG},
        }

    elif phase == "F3":
        if open_count > 0:
            return {
                "phase": "F3",
                "action": "fix_defects",
                "instructions": (
                    f"GRIND phase: {open_count} defect(s) to fix. "
                    "Teammates are fixing. Wait for completion. "
                    "After each fix, call Foundry-Fix(defect_id, cycle). "
                    "When all done: shut down team, update state to F2, run full INSPECT again."
                ),
                "details": {"open_defects": open_count, "agent_config": GRIND_AGENT_CONFIG},
            }
        return {
            "phase": "F3",
            "action": "transition_to_inspect",
            "instructions": (
                "GRIND complete: all defects fixed. Shut down grind team, "
                "Foundry-Team-Down, update state to F2. "
                "Run FULL INSPECT again (all streams). No spot checking."
            ),
            "details": {
                "agent_configs": {
                    "trace": INSPECT_TRACE_CONFIG,
                    "prove": INSPECT_PROVE_CONFIG,
                    "test": {"model": "opus", "subagent_type": "general-purpose"},
                },
            },
        }

    elif phase == "F4":
        verdicts = _load_json(fdir / "verdicts.json")
        non_verified = sum(1 for r in verdicts.get("requirements", []) if r.get("verdict") != "VERIFIED")
        total = len(verdicts.get("requirements", []))

        if non_verified > 0:
            return {
                "phase": "F4",
                "action": "assay_failed_loop_back",
                "instructions": (
                    f"ASSAY found {non_verified}/{total} non-verified requirements. "
                    "Sync findings as defects (Foundry-Sync), "
                    "call Foundry-Phase(phase='grind_start') to clear ALL markers, "
                    "update state to F3 (GRIND). Fix defects, then FULL INSPECT, then ASSAY again. "
                    "NO SPOT CORRECTIONS \u2014 the entire verification stack re-runs."
                ),
                "details": {
                    "non_verified": non_verified, "total": total,
                    "agent_config": GRIND_AGENT_CONFIG,
                },
            }

        temper = state.get("temper", False)
        if temper:
            return {
                "phase": "F4",
                "action": "transition_to_temper",
                "instructions": (
                    "ASSAY passed: all requirements verified. --temper is set. "
                    "Call Foundry-Gate(phase='temper'), update state to F5. "
                    "Run TEMPER micro-domain stress testing."
                ),
                "details": {
                    "agent_config": {"model": "opus", "subagent_type": "general-purpose"},
                },
            }

        return {
            "phase": "F4",
            "action": "transition_to_done",
            "instructions": (
                "ASSAY passed: all requirements verified. "
                "Call Foundry-Gate(phase='done'), update state to F6. "
                "Generate report, append lessons, archive."
            ),
            "details": {},
        }

    elif phase == "F5":
        return {
            "phase": "F5",
            "action": "run_temper",
            "instructions": (
                "TEMPER phase: micro-domain stress testing. "
                "Decompose into domains (min 15), probe each, cross-domain test, "
                "continuous sweep. Defects go through GRIND \u2192 INSPECT \u2192 ASSAY loop. "
                "When clean, call Foundry-Gate(phase='done'), update to F6."
            ),
            "details": {},
        }

    elif phase == "F6":
        return {
            "phase": "F6",
            "action": "done",
            "instructions": "Foundry complete. Generate report, archive state.",
            "details": {},
        }

    return {
        "phase": phase,
        "action": "unknown",
        "instructions": f"Unknown phase: {phase}. Check state.json.",
        "details": {},
    }


# --- Directives (non-blocking human steering) ---


def foundry_inject_directive(
    directive: str,
    priority: str = "normal",
    project_root: str = ".",
) -> dict:
    """Inject a human directive that the lead reads at every phase transition."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}

    directives_path = fdir / "directives.md"
    if not directives_path.exists():
        directives_path.write_text(
            "# Foundry Directives\n\nHuman steering inputs \u2014 read at every phase transition.\n\n",
            encoding="utf-8",
        )

    with open(directives_path, "a", encoding="utf-8") as f:
        marker = "URGENT" if priority == "urgent" else "DIRECTIVE"
        f.write(f"\n### [{marker}] {_now()}\n\n{directive}\n")

    return {"ok": True, "priority": priority, "message": "Directive injected \u2014 lead will read it at next phase transition"}


def foundry_clear_directives(
    project_root: str = ".",
) -> dict:
    """Clear all directives after they've been addressed."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    directives_path = fdir / "directives.md"
    if directives_path.exists():
        directives_path.write_text(
            "# Foundry Directives\n\nHuman steering inputs \u2014 read at every phase transition.\n\n",
            encoding="utf-8",
        )
    return {"ok": True, "message": "Directives cleared"}


def _read_directives(project_root: str) -> dict:
    """Read active directives."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}
    directives_path = fdir / "directives.md"

    if not directives_path.exists():
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}

    text = directives_path.read_text(encoding="utf-8")

    urgent: list[str] = []
    normal: list[str] = []
    current_priority = None
    current_text: list[str] = []

    for line in text.split("\n"):
        if line.startswith("### [URGENT]"):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "urgent"
            current_text = []
        elif line.startswith("### [DIRECTIVE]"):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "normal"
            current_text = []
        elif current_priority:
            current_text.append(line)

    if current_priority and current_text:
        target = urgent if current_priority == "urgent" else normal
        target.append("\n".join(current_text).strip())

    has = len(urgent) > 0 or len(normal) > 0
    return {"has_directives": has, "urgent": urgent, "normal": normal, "raw_text": text if has else ""}


# --- Context reload ---


def foundry_get_context(
    project_root: str = ".",
) -> dict:
    """Return all foundry state in one call. Use after compaction or session start."""
    fdir = get_run_dir(project_root)

    if not fdir or not fdir.exists():
        return {"error": "No active foundry run. Call Foundry-Init or foundry_init(resume='run-name').", "initialized": False}

    state = _load_json(fdir / "state.json")
    defects = _load_json(fdir / "defects.json")
    verdicts = _load_json(fdir / "verdicts.json")

    all_defects = defects.get("defects", [])
    open_d = [d for d in all_defects if d.get("status") == "open"]
    fixed_d = [d for d in all_defects if d.get("status") == "fixed"]
    regression_d = [d for d in all_defects if d.get("regression")]

    all_reqs = verdicts.get("requirements", [])
    verified = sum(1 for r in all_reqs if r.get("verdict") == "VERIFIED")

    findings_excerpt = ""
    findings_path = fdir / "forge-findings.md"
    if findings_path.exists():
        text = findings_path.read_text(encoding="utf-8")
        findings_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    lessons_excerpt = ""
    lessons_path = fdir / "lessons.md"
    if lessons_path.exists():
        text = lessons_path.read_text(encoding="utf-8")
        lessons_excerpt = text[:2000] + ("..." if len(text) > 2000 else "")

    teams = _check_active_teams(project_root)
    streams = _check_streams_complete(project_root)
    next_act = foundry_next_action(project_root)

    return {
        "initialized": True,
        "state": {
            "phase": state.get("phase", "unknown"),
            "cycle": state.get("cycle", 0),
            "spec_path": state.get("spec_path", ""),
            "temper": state.get("temper", False),
            "no_ui": state.get("no_ui", False),
            "started_at": state.get("started_at", ""),
            "total_duration": state.get("total_duration", ""),
            "phase_times": state.get("phase_times", {}),
        },
        "defects": {
            "total": len(all_defects),
            "open": len(open_d),
            "fixed": len(fixed_d),
            "regressions": len(regression_d),
            "open_ids": [d["id"] for d in open_d],
        },
        "verdicts": {
            "total": len(all_reqs),
            "verified": verified,
            "non_verified": len(all_reqs) - verified,
        },
        "streams": streams,
        "active_teams": teams,
        "directives": _read_directives(project_root),
        "forge_findings_excerpt": findings_excerpt,
        "lessons_excerpt": lessons_excerpt,
        "next_action": next_act,
    }
