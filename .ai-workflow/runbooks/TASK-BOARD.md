# On-demand task board: planning, resuming and handoff

Read this only when the user explicitly enabled the task board for the current task, or when resuming
the same, clearly bound task for which it was enabled before.
The authoritative rules are in [WORKFLOW.md](../WORKFLOW.md), section "### On-demand task board". This
manual covers startup routing, read entry points and real operation examples; it adds no permission or
acceptance system of its own. Progress reporting (`runbooks/AGENT-BOARD.md`) still follows the project
configuration; the two switches are independent.

Implementation contract: `integrations/agent-board/TASK-BOARD-INTERFACE.md`.
The detailed contract for the CLI, HTTP, JSON, version conflicts, capacity and queues is maintained in
that file; this manual does not copy a second interface definition.
The page is on the same loopback site as the verified progress board and shows in the "Plan" section of
the task detail (the "Plan" button on a task row opens and scrolls to it); there is no separate tab. The
old link `/?page=task-board&task=task_id` still works and opens that task's detail.
The page is read-only. The page and the summaries come from the same persisted record in the service;
mainlines are never kept separately in localStorage or chat memory.

## Startup routing (check the request first; do not list every task board)

| Current request / resume identity | What to do |
| --- | --- |
| Not explicitly enabled; the task is only complex or long, or the project enabled the progress board | Skip this manual and task-board queries, create no empty plan; progress reporting applies as before where it applies |
| "Enable the task board, plan first and then execute", with the implementation scope authorized | Necessary inspection → save deliverable mainlines and completion criteria → freeze `plan_execute` → execute within the original authorization, without waiting for another confirmation |
| "Enable the task board, plan first and wait for my confirmation" | Save and freeze `plan_wait` and only plan; record `confirm` and continue only after a real user confirmation |
| Resuming the same task that was explicitly enabled and clearly bound before | Read the summary/version/focus of the exact original task_id; no new create/draft, no second enablement |
| Only an old task name or board record, or unclear implementation authorization | Check the missing binding/authorization; an old plan is never permission for a new task, and an unclear baseline is never rebuilt on your own |

"Plan first and then execute" does not skip the necessary inspection; "wait for my confirmation" does not
gain execution authorization just because the plan is frozen.
The service cannot authenticate the user's words; `user_request` / `decision_ref` are traceable sources,
not proof of approval.
No task board is created automatically to manage task-board development, and small tasks do not use
this flow.

## Exact read and resume entry points

Examples use PowerShell with the Windows `py` launcher and an existing Python; nothing is installed. On
other systems replace `py -3` with `python3` and use your shell's variable syntax. Variables last for the
current session only:

```powershell
$Board = '.ai-workflow/integrations/agent-board/board.py'
$Task = 'stable-task-id-the-user-enabled-and-bound'
# Lead: compact summary of all mainlines + branches related to the current focus
py -3 -B $Board task-board summary $Task
# Executor: the mainline of its role and related branches; no other tasks are loaded
py -3 -B $Board task-board summary $Task --mainline main-1
# The full plan only when needed; ordinary updates take the version from summary
py -3 -B $Board task-board show $Task
```

Minimum handoff record: the stable task_id, the exact board path/project configuration/runtime, where the
user enabled it and the original authorization scope, the version/state/mode confirmed by reading, the
current mainline_id/branch_id, the next step and any unsaved updates. The default installation is set by
the project configuration; never guess the instance from old ports or worktree names. An isolated
worktree passes `--project-root` and its own `--runtime` explicitly, and these two global options go
before `task-board`. Never demonstrate the interface with real data.

A new session or model still reads the same task_id; new runs and progress-board takeovers follow
`runbooks/AGENT-BOARD.md`, and the plan is never copied.
Continuing a closed plan explicitly uses `reopen`, not a new `create`; check separately whether the
original goal and scope are still approved.
Read at start, resume and takeover; send short updates for stages, focus, substantial blockers, branch
additions/removals and pause/handoff, not a record per action.

## First planning and freezing (only after explicit enablement)

The JSON below is operation data written into **a directory this task owns**; it is not an interface for
replacing a whole plan at will.
Replace `path/to/` in the commands with your real file paths. The task must already be registered under
the progress board contract; never copy the examples to create demo tasks in a live service. Read the
version before each operation; a new request uses a new event_id, and a retry keeps the original request
and ID.

### create-data.json

```json
{"goal":"Deliver the two approved results of this task","mode":"plan_execute","user_request":"The user explicitly enabled the task board for this task: plan first and then execute; the original implementation scope still applies"}
```

If the user asked to wait for confirmation, change only mode to `plan_wait`; never switch mode while
resuming to get around a confirmation.

### draft-data.json

```json
{"mainlines":[
  {"id":"main-1","goal":"Result one is usable","completion":"Implemented and proportionately verified","owner":"agent-a","next_step":"Check the current state and entry points","dependencies":[],"parallel_with":["main-2"]},
  {"id":"main-2","goal":"Result two can be resumed and handed off","completion":"Handoff entry and resume verification done","owner":null,"next_step":"Name the responsible session"}
]}
```

Split mainlines by result, not by tool action. Leave unknowns honestly empty; use stable IDs for necessary
dependencies and parallel arrangements, without building a full dependency engine.

```powershell
py -3 -B $Board task-board create $Task --expected-version 0 --data 'path/to/create-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board draft $Task --expected-version $Current.version --data 'path/to/draft-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board freeze $Task --expected-version $Current.version
py -3 -B $Board task-board summary $Task
```

The first create starts from no plan / version=0. When a plan exists, or when enabling again after a
delete, read the current state / deleted version first and never use 0 blindly.
Run each of these write commands only after the previous step explicitly returned `RECORDED`. On failure,
pending sync or conflict, stop the writes that depend on it at once and keep the original input; a
successful read or the shell carrying on is not a successful save. Exit codes are in the implementation
contract.
Without an event_id the helper generates a unique ID and keeps it in the queue. After a failure, read
`pending` first and resend the kept request; do not rerun the same write command and create another ID.
When you choose an ID yourself, `--event-id` must be unique in the project, stable and within the
interface's ID limits.
After freezing, `plan_execute` is active; `plan_wait` is awaiting_confirmation and does not advance
implementation.
When the user asks to revise a plan that is awaiting confirmation, use `change` below to keep the
original item and the reason; planning arrangements may be adjusted while the state stays awaiting
confirmation. Do not confirm a wrong plan first, and do not delete/create to rebuild it. Continue only
after the revised plan is confirmed.

The default summary gives the item-level completion overview of the mainlines, the IDs of every running
mainline, the current focus and its related unfinished branches; it does not carry all branch history.
When page.next_offset appears, continue with `summary $Task --offset N --version V`; `--mainline` limits
the read to a role's scope.
For results already filed under a mainline use `task-board history $Task --mainline main-1`; for explicit
changes use history without --mainline; page with offset/version the same way. `show` is a full read on
demand, not the default input at every start; the page never pushes data into a model's context.
Report examples still use the real CLI; plan counts are never acceptance, effort or an overall project
percentage.

### confirm-data.json

```json
{"reason":"The user confirmed this plan; continue within the original scope","decision_ref":"Where the real user confirmation message is"}
```

Use it only for plan_wait after the user confirmed:

```powershell
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board confirm $Task --expected-version $Current.version --data 'path/to/confirm-data.json'
```

## Progress, focus and branches after freezing

Ordinary updates cannot delete mainlines, change goals or lower completion criteria; never re-draft the
whole table over frozen mainlines.
Finished items stay in plan order and are not affected by the progress board's activity sort or old-task
hiding period.
A mainline is done by its own completion criterion and result; this is never inferred from all branches
being done, a run ending or a report being generated.

A plan item's `owner` is only the owner text written in the plan (the page labels it "Plan owner (text)");
it authenticates nobody and does not correspond to a run.
The sessions actually working on an item link their own runs explicitly with the progress report
`report --mainline/--branch` (see "Plan links and the next step" in `runbooks/AGENT-BOARD.md`); the page
lists linked execution/audit runs under that mainline, and a link never implies mainline status.
The task's next step lives only on plan items: when the plan is in effect and has a focus, the focused
item's next_step is the next step of the whole task.
To change it, update that item's next_step with `progress` or move the focus with `focus`; a run's own
`--next-step` is then only a note on that run.

### progress-data.json

```json
{"mainline_id":"main-1","fields":{"status":"running","owner":"agent-b","next_step":"Continue targeted verification of the same result","blocker":null}}
```

### focus-data.json

```json
{"mainline_id":"main-1","branch_id":null}
```

### add-branch-data.json

```json
{"mainline_id":"main-1","id":"side-1","goal":"Resolve a specific question affecting result one","next_step":"Verify, then return to main-1"}
```

### finish-branch-data.json

```json
{"branch_id":"side-1","result":"Question resolved; result filed under main-1 first","evidence":["scratch/incoming/own-task/REPORT.md"]}
```

### delete-branch-data.json

```json
{"branch_id":"side-1"}
```

```powershell
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board progress $Task --expected-version $Current.version --data 'path/to/progress-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board focus $Task --expected-version $Current.version --data 'path/to/focus-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board add_branch $Task --expected-version $Current.version --data 'path/to/add-branch-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board complete_branch $Task --expected-version $Current.version --data 'path/to/finish-branch-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board delete_branch $Task --expected-version $Current.version --data 'path/to/delete-branch-data.json'
```

Completing a branch first files result/evidence under the mainline; deleting removes only that branch
record, never the referenced files.
Unfinished items cannot be cleaned up ordinarily: cancel explicitly with `cancel_branch` (branch_id +
reason), then delete.
Mainline scope changes use `change` (action + reason + decision_ref) and keep the original item;
cancelling, replacing or widening must rest on a real user decision.
Adding a branch is a necessary breakdown within the existing scope and needs no approval per branch;
optional improvements never become mainline gates automatically.
The exact fields of these operations follow the implementation interface; no other encoding or approval
parameter is added.

## Sync failures, pausing and closing

Only `RECORDED` is a confirmation by the current instance; `QUEUED_NOT_RECORDED` is pending sending;
`CONFLICT_NOT_RECORDED` is a conflict: never raise expected_version automatically or switch IDs to hide a
retry. `CACHED_BASELINE` is only the last verified read and excludes pending updates.

```powershell
py -3 -B $Board task-board pending
py -3 -B $Board task-board summary $Task --cached
# Resend explicitly only after checking the authorization, instance and conflicts of what is queued; this never flushes the progress board outbox
py -3 -B $Board task-board flush --task $Task
```

`remaining/scoped_remaining` and the exit code cover only the selected task; `global_remaining` is the
global backlog reported separately and does not require resending other tasks.
`flush` without `--task` is an explicit global flush and is never the default recovery for this task.
`start` resends no queue at all.

An ordinary sync failure does not stop unrelated authorized work; an unknown baseline is never rebuilt on
your own. Conflict handling, exact withdrawal and capacity errors are in the implementation contract;
never edit SQLite or delete queues directly, and never claim to be synced because a queue exists. A
handoff states the confirmed version and the unconfirmed updates, and the successor checks again.
When pausing, briefly update the mainline blocker/next_step/focus. When a branch ends, return to its
parent mainline; independent mainlines keep moving, and endless preparation never hides a blocker.

### close-data.json

```json
{"reason":"Task finished; mainline summary kept for the user"}
```

```powershell
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board close $Task --expected-version $Current.version --data 'path/to/close-data.json'
```

Closing is not product completion or an audit pass. The user may keep the summary; when the user
explicitly asks for deletion, close first, then use `delete` with reason/decision_ref. It deletes only
this task board's record, never the original task/run, code, evidence, other plans or product files.
To continue a closed plan explicitly, use `reopen` with reason/decision_ref; never create a new baseline
to replace the original, and never widen authorization automatically.

## Verification scope and maintenance

Workflow maintainers maintain the enablement/resume entry points in this manual and WORKFLOW; board
developers maintain the implementation, interface notes and tests in `integrations/agent-board/`.
Handoffs use the exact paths/commits the user passes along; nothing dispatches work or sends messages
across threads automatically.
`tests/test_task_board_workflow.py` checks routing/references, real argument parsing and the isolated
lifecycle of the JSON above; `tests/*startup-contract*.ps1` checks the deployment tool. Static or
synthetic passes never stand in for a real new-session startup.
There are no extra audit rounds, budget breakers, strict gates, schedulers, global hooks or new running
services.
