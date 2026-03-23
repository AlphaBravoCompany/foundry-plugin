---
description: "Explain the Foundry workflow and available commands"
---

# Foundry Help

Please explain the following to the user:

## What is Foundry?

**Forge plans. Foundry builds.**

Foundry is an autonomous build-verify-fix loop for Claude Code. One command takes a spec and produces verified, working code through iterative building, multi-stream verification, defect fixing, and final assay.

## How It Works

```
F0: DECOMPOSE → F1: CAST → F2: INSPECT → F3: GRIND → F4: ASSAY → F5: TEMPER → F6: DONE
                            ↑              ↓
                            └──────────────┘  (loop until zero defects)
```

### Phases

| Phase | What Happens |
|-------|-------------|
| **F0: DECOMPOSE** | Breaks spec into castings (independently buildable chunks) with Observable Truths |
| **F1: CAST** | Builds castings with parallel teams in dependency-ordered waves |
| **F2: INSPECT** | 4 parallel verification streams check the implementation |
| **F3: GRIND** | Fixes defects found by INSPECT, loops back to INSPECT |
| **F4: ASSAY** | 4 parallel agents do fresh spec-before-code verification |
| **F5: TEMPER** | Optional micro-domain stress testing |
| **F6: DONE** | Report and archive |

### Verification Streams (F2: INSPECT)

| Stream | Method | Checks |
|--------|--------|--------|
| **TRACE** | Serena LSP | Every function exists and is wired (called from expected entry points) |
| **PROVE** | Spec citations | Every requirement has code evidence (spec-before-code) |
| **SIGHT** | Playwright | UI renders, buttons work, no console errors |
| **TEST** | Test suite | All tests pass |
| **PROBE** | Runtime | APIs respond, smoke flows work end-to-end |

## Available Commands

### /foundry:start \<SCOPE\> [OPTIONS]

Start a new foundry run.

```
/foundry:start "user auth" --spec docs/specs/auth.md
/foundry:start "dashboard" --spec docs/specs/dashboard.md --url http://localhost:3000
/foundry:start "api" --spec docs/specs/api.md --temper
```

**Options:**
- `--spec <path>` — Spec file (strongly recommended)
- `--url <url>` — Browser URL for SIGHT verification
- `--temper` — Enable stress testing (F5)
- `--max-cycles <n>` — Cap verify-fix loops
- `--no-ui` — Skip browser verification
- `--ticket <id>` — For commit messages
- `--output-dir <dir>` — Custom output directory

### /foundry:resume

Resume an interrupted run. Lists all runs with their phase/cycle and lets you pick one.

### /foundry:status

Show current run status — phase, cycle, defects, verification streams.

### /foundry:stop

Gracefully stop the active run. Can resume later.

### /foundry:setup

Install all prerequisites: MCP server, required plugins, Serena, Playwright.

### /foundry:help

Show this help.

## Prerequisites

Run `/foundry:setup` once per machine to install:
- **Foundry MCP server** — phase gates, defect tracking, orchestration
- **Playwright MCP** — browser automation for SIGHT
- **Serena MCP** — LSP wiring for TRACE
- **ralph-loop plugin** — teammate execution engine

## Complete Workflow

```
1. Forge plans:    /forge:plan "my feature"
2. Foundry builds: /foundry:start "my feature" --spec docs/specs/my-feature.md
```

## Key Properties

- **One command, zero approval gates** — fully autonomous
- **Lead never edits code** — delegates everything to teammates
- **Every non-passing verdict is a defect** — no deferrals
- **Full re-verify after fixes** — no spot-checking
- **Teams are ephemeral** — created per phase, destroyed after
- **MCP-guided** — `Foundry-Next` tells the lead exactly what to do

## Comparison with Other Approaches

| | Manual | Forge + Foundry |
|---|--------|----------------|
| Codebase research | You read code | 4 parallel agents |
| Requirements | You write spec | Adaptive interview |
| Implementation | You code | Parallel teams |
| Verification | You review | 4 automated streams |
| Bug fixing | You debug | Automated grind loop |
| Final check | You hope | 4 fresh assay agents |

Forge plans. Foundry builds. Ship with confidence.
