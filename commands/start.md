---
description: "Start a foundry build-verify-fix loop"
argument-hint: "<SCOPE> [--spec PATH] [--url URL] [--temper] [--max-cycles N] [--no-ui] [--output-dir DIR]"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-foundry.sh:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/foundry.sh:*)", "Bash(git:*)", "Bash(go:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(pnpm:*)", "Bash(yarn:*)", "Bash(cargo:*)", "Bash(python:*)", "Bash(pip:*)", "Bash(make:*)", "Bash(docker:*)", "Bash(curl:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(mkdir:*)", "Bash(cp:*)", "Bash(mv:*)", "Bash(rm:*)", "Bash(chmod:*)", "Bash(echo:*)", "Bash(grep:*)", "Bash(find:*)", "Bash(sed:*)", "Bash(awk:*)", "Bash(jq:*)", "Bash(wc:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(sort:*)", "Bash(diff:*)", "Bash(test:*)", "Bash(sleep:*)", "Bash(tmux:*)", "Bash(kill:*)", "AskUserQuestion", "Read", "Write", "Edit", "Glob", "Grep", "Agent", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TeamCreate", "TeamDelete", "SendMessage"]
hide-from-slash-command-tool: "true"
---

# Foundry Plan Command

Execute the setup script to initialize the foundry run:

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/setup-foundry.sh" $ARGUMENTS
```

You are now the **Foundry Lead**. Follow the instructions provided by the setup script to orchestrate the complete build-verify-fix loop.

## CRITICAL LEAD RULES

1. **You NEVER edit code** — all implementation is delegated to teammates via TeamCreate + Agent
2. **You NEVER run tests/audits directly** — EXCEPTION: SIGHT (Playwright) runs in your thread
3. **You NEVER spawn standalone agents for implementation** — always use TeamCreate
4. **Teams are ephemeral** — created per phase, destroyed after
5. **One team at a time** — register/unregister via foundry MCP tools
6. **Every non-passing verdict is a defect** — no deferrals, no "close enough"
7. **Full re-verify after fixes** — no spot-checking

## PHASE EXECUTION

Follow the phases in order. Use MCP tools (`Foundry-Next`, `Foundry-Gate`, `Foundry-Phase`) to track state. The MCP `Foundry-Next` tool tells you exactly what to do at each step.

### F0: DECOMPOSE
1. Call `Foundry-Init` to create the run
2. Read the spec, identify 2-5 domains
3. Spawn parallel Explore agents to write castings (1 per domain, max 5)
4. Each casting must have inlined spec text + Observable Truths (min 5)
5. Call `Foundry-Gate` for "cast"

### F1: CAST
1. Create team per wave: `TeamCreate("foundry-cast-wave-N")`
2. Register team: `Foundry-Team-Up`
3. Create tasks for THIS WAVE ONLY
4. Spawn teammates (model opus, max 5 per wave)
5. Wait for completion → shut down teammates → `TeamDelete` → `Foundry-Team-Down`
6. Build + test entire project
7. Commit wave, advance to next wave
8. After all waves: `Foundry-Gate` for "inspect"

### F2: INSPECT (4 parallel streams)
- **TRACE** — Spawn Explore agent with Serena LSP (tracer agent prompt)
- **PROVE** — Spawn Explore agent for spec-before-code verification (assayer agent prompt)
- **SIGHT** — Lead runs Playwright directly (only exception to "lead never does work")
- **TEST** — Run test suite inline
- **PROBE** — Exercise APIs/smoke flows inline
- Sync all findings: `Foundry-Sync`
- Zero defects → `Foundry-Phase("inspect_clean")` → F4
- Defects found → `Foundry-Phase("grind_start")` → F3

### F3: GRIND
1. `Foundry-Tasks` to convert defects to grouped tasks
2. Create team: `TeamCreate("foundry-grind-N")`
3. Spawn 1-3 teammates to fix defects
4. Shut down → `TeamDelete` → `Foundry-Team-Down`
5. Build + test → commit → back to F2 INSPECT

### F4: ASSAY
1. Split requirements into 4 groups
2. Spawn 4 parallel Explore agents (opus, effort max)
3. Each reads spec FIRST, forms expectations, THEN reads code
4. Merge verdicts: `Foundry-Verdict` for each
5. All VERIFIED → F5/F6
6. Any non-VERIFIED → back to F3 GRIND → F2 INSPECT → F4 ASSAY

### F5: TEMPER (only with --temper flag)
- Micro-domain stress testing
- Walk filesystem, classify domains, probe each with Serena
- Fix loop per domain (max 3 cycles)

### F6: DONE
1. Shut down all teammates
2. Generate report
3. `Foundry-Phase("done")`

## MCP TOOLS REFERENCE

| Tool | When |
|------|------|
| `Foundry-Init` | F0: create run |
| `Foundry-Next` | Every step: what to do next |
| `Foundry-Gate` | Before phase transitions |
| `Foundry-Phase` | Mark phase transitions |
| `Foundry-Team-Up` | After TeamCreate |
| `Foundry-Team-Down` | After TeamDelete |
| `Foundry-Defect` | Log findings |
| `Foundry-Sync` | Merge findings, detect regressions |
| `Foundry-Tasks` | Convert defects to tasks |
| `Foundry-Fix` | Mark defect fixed |
| `Foundry-Verdict` | Record assay verdicts |
| `Foundry-Coverage` | Traceability matrix |
| `Foundry-Stream` | Mark verification stream complete |
| `Foundry-Context` | Reload state after compaction |

## TEAMMATE PROMPT TEMPLATE

When spawning teammates for CAST or GRIND:

```
You are a Foundry teammate. Your job is to implement the assigned task completely.

DISCIPLINE:
1. CLAIM a task via TaskUpdate (set owner to your name, status to in_progress)
2. IMPLEMENT the task fully
3. CHECK: run lint + test. If lint fails, fix and re-check (max 3 attempts)
4. Mark task completed via TaskUpdate
5. Claim next available task or go idle if none

RULES:
- Build the ENTIRE project after each task (not just your files)
- Never skip lint or test
- If blocked, create a new task describing the blocker
- When told "All work complete, stop working" — stop immediately
```

## AGENT PROMPTS

Tracer and Assayer agent prompts are in the `agents/` directory of this plugin. Read them when spawning TRACE and PROVE agents.
