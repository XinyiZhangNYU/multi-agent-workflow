# Workflow entry

Read STATE first, then this file, and do only what the user explicitly asked for in the current
request. WORKFLOW.md is the single authority for current rules. This directory stores a recoverable way
of working, not a strict platform waiting to be switched on.

When starting or resuming a task, read the project configuration named by STATE's
`project_configuration` and check whether the progress board is enabled; applicable tasks in an
enabled project join through the board entry below, with the obligations described in the WORKFLOW
section "Optional local progress board". Report briefly when a stage ends before replying; before a
long program starts, record the stage, the next observable result and the resume entry point; an
ordinary startup never replays old queues. Separately check whether the user explicitly enabled the
task board for the current task, or whether you are resuming the same enabled and clearly bound task;
only then read the WORKFLOW section "On-demand task board", the TASK-BOARD manual and this task's
record. Otherwise skip it; complexity or progress reporting never creates a task board automatically.

## What to read for the task at hand

| You need to | Next entry point |
|---|---|
| Do an ordinary task, change a rule or decide a permission question | [WORKFLOW.md](WORKFLOW.md), the relevant section |
| Write DECISIONS / STATE / OPEN | First read the WORKFLOW section "Single home for each kind of content" in the same turn |
| Find the project's repositories, product authority or host exceptions | [Project configuration](projects/example/README.md) |
| Run an approved joint development, or resolve a collision between audit remediations | The contract/candidate state; [joint development and collisions](runbooks/JOINT-DEVELOPMENT.md) |
| Use direct messages between agent sessions (joint protocol or two-session audit) | [Messaging manual](runbooks/AGENT-MESSAGING.md); joint tasks also read the local registry |
| Run an independent audit the user asked for | [Four channel templates, manual chat handoff, remediation collisions](runbooks/INDEPENDENT-AUDIT.md) |
| Work in a project with the local progress board enabled | [Joining, short reports, handoff and offline recovery](runbooks/AGENT-BOARD.md); read only this task and its direct dependencies |
| Use the task board the user explicitly enabled, or resume the same enabled task | WORKFLOW "On-demand task board" → [planning, freezing, resuming and branch lifecycle](runbooks/TASK-BOARD.md) → this task's compact server summary, scope and continuation; history only as needed, skip other tasks |
| Verify the startup copies | The startup check below |

The full DECISIONS history and every optional manual are not required reading at startup.
The current state is not authorization to start work; without an explicit request no task is created
from the roadmap. Cross-task messages never rebind roles, recipients or permissions.

## What each file is for

| File/directory | Boundary |
|---|---|
| WORKFLOW.md | Current rules; the version is bumped after the user approves a substantive change, with the matching navigation and DECISIONS entry |
| DECISIONS.md | Only sources of cross-task workflow decisions and rejected alternatives; append-only, no product summaries |
| STATE.md | Current workflow state and pointer to any active joint protocol, at most 150 lines; product progress is only navigated |
| OPEN.md | Summary of unresolved/deferred risks with permanent O-IDs |
| RATIONALE.md | Why the current design is the way it is; read on demand |
| projects/ | Per-project installation and authority paths; project semantics never become general rules |
| startup-contract/ | The single versioned source of the startup contract; after a change, sync the copies the configuration lists |
| bin/, tests/ | Small active tools and focused tests |
| runbooks/ | On-demand expansions of rules and platform adapters; not read in full for every task |
| integrations/agent-board/ | Loopback progress observation and the explicitly enabled task plan, with a shared CLI, web page and tests; not a scheduler or approval system |
| runtime/ (ignored) | Local message bindings and checkpoint markers; not shared authority and never pushed |

## Startup contract sync and check

The deployment targets and root path come from the project's `startup-targets.json` (by default the one
next to the project configuration STATE names); nothing is hard-coded in the tool. Targets reached
through a junction or symbolic link are rejected before anything is written.
The check writes nothing:

~~~powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .ai-workflow/bin/sync-startup-contract.ps1 -Check
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .ai-workflow/tests/test-startup-contract.ps1
~~~

Only a task approved to change the canonical contract or its deployment runs the same entry point with
`-Apply`, followed by `-Check`. Another project passes `-ConfigPath <json>` with `project_root`,
`canonical` and a nonempty `targets` list; relative paths are resolved against the configuration file.
A missing, drifted or mis-configured copy fails and is named.
The focused checks of the task-board rules live in `tests/test_task_board_workflow.py`; they use only the
documents, real interface parsing and isolated synthetic stores, never create a task board for the
current task or write test data to a running service, and are not a substitute for observing a real new
agent session.

## Commits and delivery

Handle each repository according to the event-driven checkpoints in WORKFLOW. Stage your own exact paths
and keep concurrent or pre-existing changes; if there is a real blocker, register CHECKPOINT_PENDING and
tell the user. With no changes, report CLEAN/NOT_TOUCHED instead of forcing a commit.

Local commits are allowed by default; push, deployment, migration and real data still need explicit
authorization. After committing, check and report the push conditions and state, so that "committed"
never hides the absence of a remote recovery point.
