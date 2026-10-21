# agent-workflow

A file-based operating manual for running several AI coding agents (Claude Code, Codex and similar
CLI/desktop agents) on one project without losing track of who is allowed to do what.

It is plain Markdown plus a few small tools. There is no server you must run, no model API dependency
and no lock-in to one vendor: any agent that reads `CLAUDE.md` or `AGENTS.md` at startup picks it up.

## What it solves

Long-running agent work tends to fail in the same few ways:

- an agent treats an old roadmap, a passing test or a previous approval as permission for new work;
- several sessions edit the same tree, and nobody can tell which change is whose or what was pushed;
- state, decisions and product progress end up mixed in one ever-growing log;
- "the audit passed" means the author reviewed their own work;
- a handoff loses the stage, the next observable result and how to resume.

The workflow answers these with a short set of rules:

- **Explicit authorization.** An agent does only what the user asked for in the current request. Push, deployment, migrations, production configuration, real data, paid calls and deleting
  user files each need their own explicit approval.
- **One startup contract, deployed byte-identically.** `CLAUDE.md` and `AGENTS.md` are copies of a single
  canonical file, checked by a script, so every agent starts from the same instructions.
- **A single home for each kind of content.** Rules, current state, decisions, open risks and product
  status each live in exactly one place.
- **Event-driven Git checkpoints.** Every handoff reports each touched repository as COMMITTED, CLEAN,
  CHECKPOINT_PENDING or NOT_TOUCHED, without sweeping in other writers' changes.
- **Independent audits with an explicit loop.** Four access channels, and a three-step
  `AUDIT_REQUEST` → `AUDIT_RESULT` → `AUDIT_RECEPTION` exchange between two named sessions.
- **Explicit joint development.** One integration owner, exact candidate SHAs, a shared contract and a
  defined way to resolve colliding fixes.
- **An optional local progress board** (loopback web page + CLI, Python standard library only) and an
  on-demand per-task execution plan.

## Layout

```text
CLAUDE.md, AGENTS.md          startup copies (byte-identical to the canonical contract)
.ai-workflow/
  startup-contract/           the canonical STARTUP-CONTRACT.md
  WORKFLOW.md                 the single authority for current rules
  README.md                   navigation: what to read for which task
  STATE.md, OPEN.md,          current state, open risks, decision sources
  DECISIONS.md, RATIONALE.md  and the reasoning behind the design
  projects/example/           per-project configuration: paths, startup targets, board settings
  runbooks/                   on-demand manuals: audits, joint development, messaging, boards
  integrations/agent-board/   optional local progress board and task board (Python, no dependencies)
  bin/, tests/                startup-contract sync tool and focused tests
```

The startup chain an agent follows is: `CLAUDE.md`/`AGENTS.md` → `.ai-workflow/STATE.md` →
`.ai-workflow/README.md` → the project configuration STATE points to → only the runbooks the task needs.

## Using it in your project

1. Copy `.ai-workflow/`, `CLAUDE.md`, `AGENTS.md`, and the entries of `.gitignore` and `.gitattributes`
   into the root of your repository.
2. Copy `.ai-workflow/projects/example/` to `.ai-workflow/projects/<your-project>/`, edit its README
   (where your status document, design docs and scratch space live) and `startup-targets.json` (which
   startup copies to deploy), and point `project_configuration` in `.ai-workflow/STATE.md` at it.
3. Check that every startup copy matches the canonical contract (Windows PowerShell 5.1 or PowerShell 7):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .ai-workflow/bin/sync-startup-contract.ps1 -Check
   ```

   After an approved change to `startup-contract/STARTUP-CONTRACT.md`, run the same script with `-Apply`
   and then `-Check`. Without `-ConfigPath` the script reads `startup-targets.json` next to the project
   configuration that STATE's `project_configuration` names, so step 2 is all it needs.
4. Replace the example entries in `STATE.md`, `OPEN.md` and `DECISIONS.md` with your own as work starts.

## Optional progress board

Requires Python 3.12+; the service, CLI and tests use only the standard library. The board is a
per-project setting: your project README says whether it is enabled (the example enables it), and its
`agent-board.json` sits next to that README. The CLI finds it through STATE's `project_configuration`;
`--config <json>` overrides it and goes before the subcommand.

```powershell
py -3 -B .ai-workflow/integrations/agent-board/board.py start
py -3 -B .ai-workflow/integrations/agent-board/board.py status
py -3 -B .ai-workflow/integrations/agent-board/board.py --config .ai-workflow/projects/<your-project>/agent-board.json status
py -3 -B .ai-workflow/integrations/agent-board/board.py stop
```

To turn it off, mark it disabled in the project README and then `stop` the service; stopping alone does
not release agents from reporting. `display_time_zone` in `agent-board.json` (an IANA name, default
`UTC`) sets the zone the page shows times in.

The page listens on `127.0.0.1` only. How agents join, report stages and recover offline is described in
[runbooks/AGENT-BOARD.md](.ai-workflow/runbooks/AGENT-BOARD.md); the per-task plan is in
[runbooks/TASK-BOARD.md](.ai-workflow/runbooks/TASK-BOARD.md).

## Tests

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .ai-workflow/tests/test-startup-contract.ps1
python -B -m unittest discover -s .ai-workflow/tests -p "test_*.py"
python -B -m unittest discover -s .ai-workflow/integrations/agent-board/tests -p "test_*.py"
```

The optional browser checks under `integrations/agent-board/tests/*.mjs` need Node and Playwright and are
not required by the service.

## Status

This is a working system extracted from a real multi-agent project and generalized: project-specific
history, decisions and paths were removed, and `projects/example/` is a template rather than a live
project. Rules are versioned in `WORKFLOW.md`.

## License

[MIT](LICENSE)
