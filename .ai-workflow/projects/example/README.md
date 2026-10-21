# Example project configuration

Copy this directory to `projects/<your-project>/`, edit it, and point STATE's `project_configuration`
at the new README. This file says only where the project's things live and which exceptions apply; it
never redefines product fields, algorithms or data rules. General rules are in ../../WORKFLOW.md.

## Paths and repositories

`../../..` from this directory is the project root (the directory that contains `.ai-workflow/`).
In this example the project root is a single Git repository that also contains the workflow. A project
may instead keep `.ai-workflow/` as its own repository next to one or more code repositories; then commit
each repository separately and authorize each push separately. Always read the actual branch and HEAD of
the current task; never infer them from a default branch.
New worktrees go to `<project root>/worktrees/<topic>-<actor>/` unless the project says otherwise.

| Content | Authoritative entry point, relative to the project root |
|---|---|
| Product, data and implementation status | `docs/STATUS.md` (replace with your status document) |
| Product overview and long-term design | `docs/design/` |
| Contracts tightly bound to code | `docs/contracts/` or the `contracts/` directory of an enabled joint branch |
| Source, license and interface research | `docs/research/` |
| Temporary handoffs, audit briefs and raw reports | `scratch/incoming/` (usually git-ignored) |
| Attachments that must be delivered separately for a review | `review-packets/` |

## Host and outbound exceptions

- Progress board: **enabled** in this example. The optional [local progress board](../../runbooks/AGENT-BOARD.md)
  is configured by [agent-board.json](agent-board.json) and never writes back to the status document
  above. To disable it, change "enabled" here to "disabled" (agents then skip joining it) and stop a
  running service with `board.py stop`; stopping alone leaves agents still required to report.
  To enable it later, set "enabled" again and start the service. When the board is installed in `.ai-workflow/`, its root path is relative to this
  directory; an isolated development worktree must pass `--project-root` explicitly.
  The [on-demand task board](../../runbooks/TASK-BOARD.md) on the same site is a per-task execution
  plan, not a project switch; it is used only when the user explicitly enables it for the current task
  or the same enabled task is resumed, and it does not cancel progress reporting.
- List here any host limits and verified launch methods the rules refer to (for example how background
  tasks start without a window), with the path and commit where they were verified.
- Outbound material (accounts/auth, data-source endpoints, model thresholds/weights, rate strategies)
  must be checked and redacted before it leaves the machine; a private Git remote still counts as
  leaving the machine.

## Startup copies

`startup-targets.json` configures the startup deployment: `project_root` and `canonical` are relative
to this configuration's directory, and `targets[].path` is relative to `project_root`. This example
deploys two copies: the root `CLAUDE.md` and `AGENTS.md`. Changing the targets grants no new permission;
first confirm the directories exist and that there are no duplicates, self-references or paths outside
the root, then sync with authorization.
