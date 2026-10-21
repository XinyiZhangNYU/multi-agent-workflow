# On-demand task board: implementation interface

Workflow maintainers reference this file as `integrations/agent-board/TASK-BOARD-INTERFACE.md`.
It describes the actual interface only. The rules for enabling, authorization, resuming and handoff
belong to WORKFLOW.md and `runbooks/TASK-BOARD.md`.

## Boundaries

- A plan is created only for a **current task the user explicitly enabled it for**. An enabled progress
  board or a large task is not a reason to create one.
- A plan reuses the registered `task_id`. A new run, model or client reads the same plan. The interface
  never creates progress tasks or runs and never bulk-imports old tasks.
- After `freeze`, a `plan_execute` plan becomes `active` with no extra confirmation; a `plan_wait` plan
  becomes `awaiting_confirmation` and needs the user's confirmation before `confirm`. These states are
  records, **not proof of authorization to implement**.
- After freezing, ordinary updates change only progress fields; they cannot change goals, completion
  criteria or order, or remove mainlines. An explicit `change` keeps the original item, the reason and a
  decision reference. The decision reference is context, not an identity or approval the service checks.
- Mainlines stay in plan order, including finished ones. This order is independent of the progress
  board's activity sort and old-task hiding period. Branches are one level deep; completing or
  explicitly cancelling one first files its result under the mainline, and only then may it be deleted.
- Product PASS, audit approval or deployment is never inferred from branch counts, ended runs or plan
  state. Nothing is written back to the existing tasks/runs/events.
- The page is read-only. Both views share the existing 10-second visible-page refresh; there is no
  resident model or background scheduler.

## Calling the CLI

Use Python 3.12+. Examples use PowerShell with the Windows `py` launcher; on other systems replace
`py -3` with `python3` and use your shell's variable syntax.

```powershell
$Board = '.ai-workflow/integrations/agent-board/board.py'
$Task = 'existing-explicitly-enabled-task-id'
py -3 -B $Board task-board list
py -3 -B $Board task-board show $Task
py -3 -B $Board task-board summary $Task
py -3 -B $Board task-board summary $Task --mainline main-1
```

`--config`, `--project-root` and `--runtime` are global options and go **before** `task-board`.
They default to the installed project. A development checkout must name its project root and an
isolated runtime explicitly; never demonstrate with real data.
`task-board --help` and `task-board progress --help` list the actual options.

### Write requests and responses

Every change uses the same envelope: UTF-8 JSON (a BOM is accepted), with no unknown fields.

```json
{
  "event_id": "stable-retry-id",
  "task_id": "existing-task",
  "expected_version": 0,
  "op": "create",
  "data": {"goal": "Delivery goal", "mode": "plan_execute", "user_request": "Where the user explicitly enabled it"}
}
```

```powershell
py -3 -B $Board task-board submit 'path/to/owned-request.json'
# Or store only the data in a file; --data takes the data file, not the whole envelope:
py -3 -B $Board task-board create $Task --expected-version 0 --data 'path/to/create-data.json' --event-id stable-retry-id
```

A confirmed response (the service generates the version):

```json
{"status":"RECORDED","recorded":true,"duplicate":false,"event_id":"stable-retry-id","version":1,"digest":"sha256","service":"agent-board-v1","instance_id":"current-instance"}
```

Read the current version before each new operation and set `expected_version`. A retry with the same
event_id and the same raw content is recorded once; the same ID with different content is rejected.
**Never change the ID for a retry or raise the version automatically after a conflict.**
A second `create` on an existing plan conflicts: read and resume instead of rebuilding. To enable again
after an explicit delete, first read the deleted version, then send a new request; retrying an old
`create` cannot revive a deleted plan.

## Operations and lifecycle

Every row is called as `task-board <operation> $Task --expected-version N --data DATA.json`;
`freeze` takes no `--data`.

| Operation | data / required fields | Conditions and result |
| --- | --- | --- |
| `create` | goal, mode, user_request | The task must be registered; creates a draft |
| `draft` | mainlines; optional goal | Only before freezing; stores the complete draft array |
| `freeze` | `{}` | Non-empty draft; freezes mainlines and enters active / awaiting_confirmation by mode |
| `confirm` | reason, decision_ref | Only in awaiting_confirmation; records that the user confirmed |
| `progress` | mainline_id, fields | Allowed while active; in awaiting_confirmation only planning fields, and status may only be planned; goals/criteria are not fields |
| `focus` | mainline_id; optional branch_id | Sets the focus explicitly; mainline_id may be null; the branch must belong to that mainline |
| `change` | action, reason, decision_ref and the fields below | An explicit change kept apart from ordinary progress; keeps the original item and a short reason |
| `add_branch` | mainline_id, id, goal, next_step; optional owner | Must belong to an unfinished mainline; creates no task/run |
| `branch_progress` | branch_id, fields | Cannot set done; use complete_branch to file the result |
| `complete_branch` | branch_id, result; optional evidence | Files result/evidence under the parent mainline first; does not complete the mainline |
| `cancel_branch` | branch_id, reason | Explicit cancel; the short reason is filed under the mainline |
| `delete_branch` | branch_id | Only completed/cancelled branches; the result stays on the mainline |
| `close` | reason | Closes this plan, keeps all mainlines, changes no business state |
| `reopen` | reason, decision_ref | Explicitly restores the state before closing; does not widen the original authorization |
| `delete` | reason, decision_ref | Only a closed plan; deletes plan content, never tasks/runs/events or any file |

Each draft mainline:

```json
{"mainlines":[
  {"id":"main-1","goal":"First deliverable","completion":"Objective completion criterion","owner":"agent-a","next_step":"Check the current state first","dependencies":[],"parallel_with":["main-2"]},
  {"id":"main-2","goal":"Second deliverable","completion":"Proportionate verification done","owner":null,"next_step":"Name the responsible session"}
]}
```

`owner` and `next_step` must be present but may be null when unknown. `dependencies` and
`parallel_with` are optional lists of stable mainline IDs; there is no dependency solver or automatic
execution.
Ordinary `fields` allow only `status`, `owner`, `next_step`, `blocker`, `result` and `evidence`.
Mainlines may also update the `dependencies` / `parallel_with` arrangement (referencing other existing
mainlines only; goals, criteria and display order do not change). Status is
`planned/running/blocked/done`; a done mainline needs an explicit result. No overall percentage is made up.
Evidence is an array of bounded project paths, shown and copyable only; there is no file serving.
Secret paths and paths outside the project are rejected.

The four `change` actions:

- `cancel`: mainline_id. The item becomes cancelled and keeps its goal and completion criterion.
- `replace`: mainline_id and replacement (a complete new draft mainline with a new ID). The old item
  becomes replaced and the new one is appended; the original commitment is not overwritten.
- `add`: replacement (a complete new mainline). Use only for an explicit scope decision; do not promote
  optional improvements to required work.
- `goal`: goal. The old task-level goal stays in changes. To change a mainline's completion criterion,
  use replace instead of weakening it in place.

`change`, planning arrangement and `focus` also work in awaiting_confirmation; the plan keeps waiting and
needs no `confirm` first. Execution progress cannot use this to bypass the user's wait requirement.

After a branch is completed, cancelled or deleted, its ID is not reused. Not every tool call needs a branch.

## Enabling and resuming: an example

Write these files into **a directory this task owns**; never into a frozen candidate, raw evidence or
another task.

1. `create-data.json` holds goal/mode/user_request. If implementation is authorized and the user says
   "plan, then execute", choose plan_execute; if the user says "plan and wait for my confirmation",
   choose plan_wait.
2. `draft-data.json` holds the mainlines above. Read `version` from a summary response before each
   step; use `show` for the full record only when needed:

```powershell
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board draft $Task --expected-version $Current.version --data 'path/to/draft-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board freeze $Task --expected-version $Current.version
py -3 -B $Board task-board summary $Task
```

3. Continuing within the original authorization needs no new create/draft. Example `progress-data.json`:

```json
{"mainline_id":"main-1","fields":{"status":"running","owner":"agent-b","next_step":"Continue the same mainline","blocker":null}}
```

4. A necessary branch: `add-data.json` is
   `{"mainline_id":"main-1","id":"side-1","goal":"Resolve a specific question","next_step":"Verify, then return to main-1"}`;
   `finish-data.json` is
   `{"branch_id":"side-1","result":"Resolved; the conclusion matches the parent completion criterion","evidence":["scratch/incoming/own-task/REPORT.md"]}`;
   `delete-data.json` is `{"branch_id":"side-1"}`.

```powershell
# Re-read the version before every step; none of these changes formal evidence.
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board add_branch $Task --expected-version $Current.version --data 'path/to/add-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board complete_branch $Task --expected-version $Current.version --data 'path/to/finish-data.json'
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board delete_branch $Task --expected-version $Current.version --data 'path/to/delete-data.json'
```

Minimum handoff: this interface file, the stable task_id, the exact CLI path/config/runtime, and the
version/state/mode/focus read from summary. The lead reads the summary of all mainlines; an executor
reads `summary --mainline ID`. Never use an old plan binding as authorization for a new task.

When pausing or handing off, `progress` updates the mainline status, owner and next step, and `focus`
takes data such as `{"mainline_id":"main-2","branch_id":null}`. When the task ends, `close-data.json` is
`{"reason":"Task finished; summary kept"}`:

```powershell
$Current = (py -3 -B $Board task-board summary $Task | ConvertFrom-Json)
py -3 -B $Board task-board close $Task --expected-version $Current.version --data 'path/to/close-data.json'
# Prepare delete-data.json (reason + decision_ref) only when the user explicitly asks for deletion.
# Reopening a closed plan needs reopen-data.json with reason + decision_ref as well; never create again.
```

## HTTP and the single data source

- GET `/api/task-boards`: `service,instance_id,boards[]`, summaries of explicitly created, undeleted plans only.
- GET `/api/task-board?task_id=ID`: `task_id,exists,deleted,version,board,updated_at,service,instance_id`.
  A missing plan returns exists=false/version=0 and writes nothing.
- GET `/api/task-board?task_id=ID&summary=1[&mainline=ID]`: a `summary` derived from the same body. By
  default it holds all compact mainlines plus the branches related to the current focus; with a mainline
  it reads only that mainline's scope. It does not include all events or other tasks.
- Summaries exclude branch_results/changes. `page={offset,next_offset,total}` pages by whole entries and
  never truncates goals, criteria or blockers. Continue with `&offset=N&version=V`; a version change
  returns 409, so pages of different versions are never mixed.
- GET `/api/task-board?task_id=ID&section=history[&mainline=ID][&offset=N&version=V]`: results filed
  under a mainline; without a mainline, the explicit changes. The default summary never downloads history.
- The list adds the task's workstream, the shared overview and the focus next_step. In progress
  state/task, `task_board` references the same derived overview and changes no task/run state or
  business time.
- Every task in progress state/task also has one `next_step` (text/source/item/run_id/at). When the plan
  is frozen, active or awaiting_confirmation and has a focus, source=`plan_focus`; otherwise it comes
  from the current owner run (source=`current_run`); with neither, text/source are null. A task with a
  plan also has `unassigned_run_count`. The `/api/task` detail adds `item_runs` (linked runs grouped by
  plan item ID) and `unassigned_runs`. A link is declared explicitly by the run's `item` and resolved
  against the plan on read; plan progress is never inferred from it.
- POST `/api/task-board-events`: the envelope above. It uses the existing local write credential, JSON
  body limit, Host/Origin checks and instance check. The credential never enters the page, URLs, Git,
  ordinary logs or reports.
- Page: the plan shows in the task detail's "Plan" section; there is no separate page. The link
  `/#ID` opens that task's detail. Times are stored in UTC and shown in the
  browser's local time zone.

The task board uses its own SQLite tables `task_boards` and `task_board_receipts` (plus one index) and
never rewrites the progress-board tables. Version checks run in a single transaction; neither the browser nor a helper
writes SQLite directly.
After a delete only a light tombstone (task_id/version/time) and the receipt of the last delete request
remain, so stale requests cannot revive the plan; deleted plan content and old receipts are not kept.

## Failure and recovery

| Status / exit code | Meaning |
| --- | --- |
| RECORDED / 0 | The verified current instance confirmed event/digest/version; not product approval |
| QUEUED_NOT_RECORDED / 2 | Saved for sending only; remote state unconfirmed; do not say it is synced |
| CONFLICT_NOT_RECORDED / 3 | Version/lifecycle conflict; the request is kept and the version is not changed automatically |
| REJECTED_NOT_RECORDED / 1 | Rejected by the confirmed instance; the error is kept, never silently dropped |
| ERROR / 1 | Input, capacity, identity or local error; trust the actual output and do not assume it was queued |
| CACHED_BASELINE / 0 | The last verified baseline, read explicitly; excludes pending updates and does not claim to be current |
| WITHDRAWN_LOCAL_ONLY / 0 | A queued local request was withdrawn explicitly and its failure evidence kept; remote writes are not undone |

```powershell
py -3 -B $Board task-board pending
py -3 -B $Board task-board flush --task $Task
py -3 -B $Board task-board summary $Task --cached
```

The task-board queue lives separately in the runtime's `task-board-outbox/` and is resent in trusted
enqueue order. A conflict blocks only later requests of the same task; other tasks can still be resent.
**It never flushes the progress board outbox along the way.**
`flush --task ID` resends only that task; `remaining/scoped_remaining` and the exit code use this scope,
and `global_remaining` reports the global backlog separately. When this task succeeds but other tasks
are still queued, status=SYNCED, remaining=0 and exit=0. Without `--task` it is an explicit global
flush, not the default single-task recovery.
On a real conflict, first read the same task's latest state and decide how to redo the change. If
needed, withdraw the stale request explicitly (pending returns its digest); the request is saved in
`task-board-withdrawn/`. Then submit the correction with a new ID and the current version. Never use a
withdrawal to hide a failure or pretend a remote write did not happen:

```powershell
py -3 -B $Board task-board withdraw OLD_EVENT_ID --expected-digest EXACT_DIGEST_FROM_PENDING --reason 'Read the current version and checked the conflict; withdrawing the stale request'
```

When the service is unavailable, `--cached` reads the known baseline; without a valid baseline, report
that recovery is pending and do not rebuild on your own. An ordinary sync failure does not stop
unrelated authorized work.
A helper talking to a service that lacks these routes does not pretend the feature works; write
requests are kept until the running version is verified and recovery can continue.

## Limits, integration and restrictions

Separate limits: a write request is still 64 KiB; one plan's `max_task_board_bytes` defaults to 10 MiB
(10485760 bytes); each summary/history page's `max_task_board_summary_bytes` defaults to 32768 bytes,
including reserved identity metadata; a full read is limited to one plan plus the closing reserve and
8192 metadata bytes.
The full cache is `task-board-cache/` (plan limit plus the closing reserve and 16384); summaries and
history use `task-board-summary-cache/` (page limit plus 8192); a full cache entry cannot pose as a
compact summary. The total cache `max_task_board_cache_bytes` defaults to 64 MiB; at the limit it
returns NOT_CACHED_CAPACITY and never evicts old entries.
CLI `summary` calls the service summary directly; `summary/history --offset N --version V` continues a
page. `--cached` is explicitly CACHED_BASELINE and keeps version/instance/read_at/cache_kind; it does not
mean current. A model gets a summary only by actually calling for it; browser polling never injects
anything into a model's context.
At most 60 mainlines / 200 existing branches / 100 explicit changes; IDs are at most 96 ASCII
characters. Receipts `max_task_board_receipts` default to 100000; the database still defaults to
256 MiB and the queue to 1000 events / 8 MiB. Capacity is not an account or disk guarantee; OS disk
errors are reported as they are.
`close` has a 9024-byte closing reserve (2000 characters at the worst case of four UTF-8 bytes, plus
1024 metadata), so a full plan can still close with a valid multi-byte or emoji reason; full reads and
caches keep the same margin, and it is never used to grow ordinary plans. When the plan, receipt or
database quota is reached, this task's close/delete control operations are still allowed (a truly full
disk cannot be guaranteed); delete reclaims only its own receipts and keeps the delete tombstone and
idempotent receipt. Other tasks' records and queues are never cleared.
In the overview, done/active/incomplete are item counts only; cancelled/replaced are listed apart.
baseline is the number of original IDs at freeze; added/replacements count explicit changes; records
without a baseline show null/unknown and nothing is invented. running_ids includes everything
in progress; focus does not restrict it.
Changing the code of a running board means checking the instance, writers and pending items, taking a
consistent backup and restarting exactly this board; `start` does not flush. An ordinary task-board write resends only the same task's plan queue and never
replays the progress outbox.
Rollback means running the previous code against the same incrementally compatible database;
**never overwrite writes made since with an old backup**. Never empty the database, delete queues or
stop Python processes in bulk.
This implementation never changes startup rules and never sends messages. Workflow maintainers should
reference this file and complete the routing that applies; until then, do not claim that new agent
sessions follow the new rules automatically.
