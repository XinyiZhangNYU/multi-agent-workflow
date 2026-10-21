# Local multi-agent progress board

The board is an observation and aggregation layer. It is not a scheduler, an approval system, an
independent audit or the authority on product status.
Use it only when the project enables it. The general rules are in [WORKFLOW.md](../WORKFLOW.md),
section "### Optional local progress board"; this manual covers integration and the tool contract.
Source: `integrations/agent-board/`. Project configuration: the `agent-board.json` next to the project
README that [STATE.md](../STATE.md) points to (see [projects/example/README.md](../projects/example/README.md)).
Progress reaches the board only through the short reports in this manual; there is no read-only refresh relay.
Database, credentials, outbox, instance file and logs live in `.ai-workflow/runtime/agent-board/` under
the project root (ignored by Git).
The on-demand task board (execution plan) shows in the "Plan" section of the task detail on the same
page; it has no separate page and is not a default step of progress reporting. Plan or resume a plan only
when the user explicitly enabled it for the current task, or when resuming the same enabled task, as
described in WORKFLOW section "### On-demand task board" and the manual `runbooks/TASK-BOARD.md`.
Enabling the progress board for a project never creates a plan.

## Installation and lifetime

Python 3.12+ is required. The service, CLI and unit tests use only the standard library; there is no
resident dependency on models, Docker, Node or paid APIs. Node with an existing Playwright module and a
Chrome/Chromium build is used only for the optional browser checks; it is never a service dependency and
is never installed automatically.
`project_root` in the configuration is relative to the configuration file; for a configuration in
`projects/<name>/` it is usually `../../..`. The root must contain `.ai-workflow/WORKFLOW.md`.
An isolated development worktree cannot reuse the installed relative path: pass
`--project-root <absolute project root>` explicitly.
`--runtime` is only for your own isolated test data directory; never point it at someone else's runtime.
Another project supplies its own project configuration and does not copy another project's snapshots.

Examples use PowerShell with the Windows `py` launcher. On other systems replace `py -3` with `python3`
and use your shell's variable syntax. Variables are set for the current session only; no global
configuration is changed.

~~~powershell
$board = '.ai-workflow/integrations/agent-board/board.py'
py -3 -B $board start
py -3 -B $board status
# Open the url printed by start; the default is http://127.0.0.1:8765
py -3 -B $board stop
# If the port is taken, do not stop other programs; pick a free port of your own:
py -3 -B $board start --port 8766
~~~

`start` checks for an existing instance and the port; the child process has no console and is detached
from the terminal.
`start` does not replay the outbox. Short reports resend only the same task's queue automatically, keep
the cross-run enqueue order and never touch other tasks.
For an explicit recovery use `py -3 -B $board flush --task example-task`. `flush` without `--task` acts
on the whole queue, needs matching authorization and is never part of an ordinary start or diagnosis.
Exit codes: confirmed conflict 3, input rejected 1, pending confirmation 2, offline with no registration
basis 4. Never switch IDs to hide a failure.
The service usually keeps running after the maintaining session ends, but shutdown, sign-out or the host
reclaiming the process stops it; survival across host lifetimes is not guaranteed.
There is no autostart, scheduled task or automatic restarter.
`stop` sends a stop request to the same verified service using the local write credential and the
instance_id; it never kills Python processes in bulk or ends a process by an old PID.
STOPPED_OR_UNVERIFIED means offline or identity unproven; it does not mean an old PID still belongs to
the service. Never force-kill by an old PID.
Closing the page does not stop the service. Enabling or disabling the board is a project setting: the
project README says whether it is enabled, and agents follow that line, not whether a service is running.
To disable it, change that line and then `stop` the service; to enable it, change the line and `start`.
Keep the runtime directory for recovery; do not delete history or credentials without authorization.

## The page: four views

Tabs at the top switch between four views of the same state. The page is plain HTML/CSS/JavaScript
(`web/`; `views.js` renders the first three views) and loads no external fonts or CDNs. Text status colors
use higher-contrast variants. The database schema and start/stop commands are the same for every view.
Manual requests use the `ask/resolve/withdraw` ops; a run can also declare `human_input` (see
"Run-level declarations" below).

- The current view is kept in the URL `?view=brief|agents|feed|list` and remembered in this browser; the
  URL parameter wins. The default is "Brief".
  All four views are read-only; the only write is "Handled" (see "Manual requests" below). Clicking a
  task in any view opens the same side detail.
- Brief: a headline gives the number of questions waiting for your decision and of tasks in progress.
  "Waiting for your decision" shows one card per question (text, options, what happens if unanswered,
  time waiting, [Handled] [Details]). Possibly outdated questions, "needs you" declarations with no
  text, and runs that only wrote a pending decision are each folded. Below come unfinished tasks updated
  in the last 48 hours, tasks that ended in the last 48 hours (folded beyond 5), and unfinished tasks
  silent for over two days (folded; not necessarily a problem). Old tasks past the display period are
  only counted.
- Who's working: one card per unfinished run (agent, execution/audit/parallel, status, task, phase; the
  first line of an open question when there is one). A run counts as active only if it reported in the
  last 24 hours; other registered runs are folded under "Registered but silent for over 24 h". Ended runs
  are not shown unless they still have an open question.
- Feed: `GET /api/feed?limit=1..500` (default 200; the page asks for 300) lists reports, takeovers,
  questions, answers, withdrawals, page closes and delivery records from newest to oldest by business
  time. It excludes run/task registrations and document snapshots; each entry keeps its first line, at
  most 240 characters. Entries are grouped by day in the display time zone and can be filtered by "Questions
  and closes / Finished / Deliveries". Reports that arrived while you were away from "Feed" are marked
  above a divider when you return. The endpoint is read-only and checks Host and the instance header.
- All tasks: the workstream navigation, inbox, task list and filters described below.

- When some reports are not yet confirmed as delivered, a red banner at the top shows the number of
  reports and tasks involved; related cards and the detail say the state below may not be the latest and
  name the oldest queued event. Unconfirmed does not mean the service never received it (it may be
  recorded with a lost receipt). Unreadable queue files are counted separately; their content is unknown.
  The banner only reads runtime queue files and their summaries; it never resends, edits the queue or
  deletes bad files. Handle it with the `pending` / `flush --task` steps below.
- The left column shows only workstream names and "Needs you N" (N = open questions with text; hidden
  when zero). The top "Needs you" covers every workstream and ignores local filters.
  Runs that wrote a pending decision but have no open question are listed under the folded "Other pending
  decisions" for checking only; they do not count as needing you.
  The page does not repeat the operating boundaries or source disclaimers; with no requests it shows only
  "Nothing needs you."
- "Needs you" inbox: one row per open question with text: task · first line of the question · waiting N ·
  [Handled] [Expand], newest first. The text, options, unanswered policy and asking run are in "Expand"
  and in the task detail. Questions whose asking run ended, was handed off or has been silent for 48 hours
  (unknown time does not count) fold into "Possibly outdated (N)"; folding never closes them. A run-level
  required declaration with no text does not enter the inbox; the task row shows a grey
  "Needs you (unstated)" tag (with × to close). Closing rules are in "Manual requests" below.
  Zero means no manual request has been reported; it does not prove the task is healthy or finished, and
  needs an agent never raised cannot be found.
- Task list, one row per task: name · status (" · stale" when stale) · phase (or the next step) · tags
  (Needs you N, the unstated tag, undelivered, old task, a "Plan" button when there is a plan) · last
  update. Audit, parallel-run, candidate applicability, conflict and staleness hints are all in the
  detail, whose top "Notes" section lists the former card hints. Tasks in a workstream that ended or had
  no update for 48 hours (unknown time does not count) and have no open question fold into "N more";
  filtering by status, search or manual to-do disables folding. Audit state, dependencies/blockers,
  staleness and source differences are not manual to-dos: the responsible agent submits a request
  explicitly only after checking that the user really must act. The right side can still filter by them.
- Times use `display_time_zone` from the board configuration (an IANA name, default `UTC`): cards show
  relative times (hover for the full time and zone), the detail shows full times.
- The default order is newest effective update first: tasks sort by `display_updated_at`, workstreams by
  the same time of their newest task in the current display scope. The left column and the main area use
  the same workstream order; local search/status/agent filters do not change workstream priority. Every
  change in data re-sorts without switching the selected workstream, clearing filters or closing the
  detail. Ties sort by stable task_id / workstream name; items with no valid time go last and stay.
  A card's "last update" is the sort time; the executor's own business time, audit time and
  observation/snapshot times stay separate in the expanded view and the detail.
  The time rules of the display period below apply: page refreshes, receive times and newly created runs
  do not raise priority and never imply activity or completion.
- The side detail, top to bottom: the task's next step (with its source); "Needs you" (open questions;
  closed and unmatched answers/withdrawals folded separately); the plan section (replaces "Execution"
  when there is a plan; completion criteria, linked execution/audit runs, branches and history folded
  under each mainline); "Unassigned runs" (with a plan; recently active and stale/never reported folded
  separately); then audit, observation, dependencies, snapshots and deliveries.
  The detail keeps every run, candidate applicability, document sources, observation times, delivery
  records and the event timeline. Stale or unreported audits, deliveries not for the current candidate
  and ended runs are folded separately; the disclaimer appears once at the top of the detail.
  Document snapshots older than the executor's latest report are no longer compared with the current
  self-report; they are only counted ("N documents predate the latest execution report").
  Evidence paths can be copied; apart from the "Handled" close, the page cannot write, approve or
  execute anything. At narrow widths the detail is a modal panel that can be left with the keyboard.
- Automatic refresh keeps filters, focus and expanded sections; closing the detail returns to the
  previous reading position. A new service instance requires reloading the whole page; late responses
  from another instance or task are never shown as current. A running service or a page refresh still
  does not mean a business update.

Moving a UI change into the page does not mean it passed an independent audit.

### Seven-day display period (records are never deleted)

By default the list shows only tasks with an effective update in the last 7 days; tick "Show old tasks"
to see everything. The detail and history are always kept.
Sidebar and list counts follow the current display scope; manual to-dos still cover every workstream.
A task with an open question (an ask or a required declaration) is never hidden by age, even when
the asking run ended or was handed off.
A task whose update time is unknown stays visible; expiry, failure or completion is never inferred.

The display update is the latest of the valid `business_at` of the current execution run, every audit
run and every never-claimed parallel execution run, and the actual `source_at` of program observations.
Only when there is no known progress/observation time does it fall back to the original `source_at` of
document snapshots; a current or unfinished audit with an unknown time keeps the task visible.
After the owner changes, new reports from the old execution run do not extend the display period; nor do
late low-sequence events, page refreshes, service heartbeats, new or takeover records, delivery receive
times or re-imported old documents. Valid new progress or new audit progress makes a task reappear.
The historical business time of an ended audit may count as a display update, but it inherits no audit
conclusion or execution state.

`display_ttl_seconds=604800` in the configuration is a positive integer and defaults to 7 days; it is
separate from `stale_seconds=86400`. At 604800 seconds a task is hidden from the default list, measured
in actual elapsed UTC time; the page shows times in `display_time_zone`.
The API still returns every task, with the read-only derived fields `display_updated_at` /
`display_time_unknown` / `display_expired`. Reported fields, the SQLite schema, history events and the
outbox are unchanged; there is no scheduled cleanup, lease, agent heartbeat or model polling.
This only reduces noise in the default list; it does not cancel a task or settle its dependencies.

`tests/browser_workstream_checks.mjs` and `tests/browser_ttl_checks.mjs` check this in isolation (see
"Local boundaries and verification").

## Short reports: tasks and runs are separate

Report briefly at start, stage completion, substantial blockers, pending decisions, pause/handoff and
close. When a stage ends, get the receipt first and then give the final reply; if the receipt is not
confirmed, say explicitly that it is pending sync or in conflict, and do not save all stages for one
report at the end. Before a long program runs, record the stage and the next observable result, and save
this task's exact resume entry point in next_step/progress/evidence (when the task has an execution plan
the task's next step lives on the plan item; see "Plan links and the next step"). Do not invent fixed
heartbeats when there is nothing new.
Progress reports and task-board writes are verified separately; one succeeding does not stand in for the
other. An ordinary failure does not stop unrelated authorized work, and nothing nudges models.

A task_id is a durable product/slice ID and stays the same across models and sessions. A run_id is one
execution or audit and is unique within the project.
IDs start with an ASCII letter or digit, continue with letters, digits, `_`, `.` or `-`, and have at most
96 characters; never put personal data or chat text in them.
IDs are case-sensitive. The helper maps each exact ID to a per-purpose file name; on any physical path
collision it keeps the existing bytes and refuses to overwrite, and it never lowercases IDs, so IDs that
differ only by case keep separate queues, sequence counters and quarantine archives.
agent/client are labels only; they authenticate nothing and grant no permission.

### Agent names (a soft convention, no client allowlist)

`agent` is the tool/client family name, for example `Claude`, `Codex`, `Gemini` or `OpenCode`; ordinary
scripts may use `Script`.
Keep the tool name when the model changes within the same tool. Never fold a model version, effort level,
execution/audit role or task description into the name; for example `model-x-large`,
`ToolName-model-x-max` or `ToolName-independent-reviewer` are not valid agent names.
`client` is the concrete client, such as `Codex-Desktop`, `Claude-Code`, `internal-subagent` or a script
entry point; do not store a model version or effort there either. Use the existing `role` for
execution/audit and the `run_id` to tell sessions apart.

This is a soft requirement of the integration docs and the report help, not a fixed enumeration of the
interface; new tools need no allowlist entry.
The interface keeps its text, safety and length checks and never guesses identity from the name, converts
aliases or changes filter semantics.
Ordinary agents do not edit registered labels. A wrong existing name is corrected once by a maintainer
the user explicitly authorizes: check the exact task/run and the expected old value, stop this instance
and hold the lock, back up first, then correct `runs.agent` in a transaction and keep a correction
record. Do not recreate the run or claim, and do not change status, role, candidate, business time,
history events or their digests, or rewrite the outbox.
The original name in history events is kept as evidence of the input at the time; it is no longer the
current agent label or a basis for alias conversion.

### Register before reporting (CLI precheck)

Before allocating a sequence number or writing the queue, `run/claim/report/ask/resolve/withdraw/delivery`
check with the service that the task and run are registered: a missing task returns
`TASK_NOT_REGISTERED`, a run not registered under that task returns `RUN_NOT_REGISTERED` (exit 1, not
queued, no sequence number used). `submit` applies the same check to the same kinds of events, and
`run`/`retag` check the task. A task/run counts as registered when its registration is still in the
local queue (it will be delivered first) or when this machine once confirmed it online or delivered it
successfully (recorded in `runtime/registrations/`).
When the service is unreachable and none of these applies, the result is `UNVERIFIED_NOT_QUEUED`
(exit 4): nothing is queued and no sequence number is used; the original input is kept in
`runtime/held/` (never replayed automatically; `status` shows the count). Once the service is back, run
the same command again unchanged. Never bypass the precheck by writing the queue by hand.

When `report`/`ask` carry a plan link (`--mainline [--branch]` or JSON `item`), the CLI also reads the
task's current plan from the service: a task with no plan returns `NO_PLAN`; a mainline or branch that is
not in the plan, or a branch that does not belong to that mainline, returns `UNKNOWN_ITEM` (both exit 1,
not queued, no sequence number used). Check the IDs with `task-board summary` and run again. When the
registration has a local basis but the service is temporarily unreachable, the link is queued as usual
and judged on read; an invalid link shows as an "unassigned run". `submit` applies the same check to
`item` in progress/ask events.

An unconfirmed receipt always contains `board_updated: false`, `pending_for_task` and a `warning`, in one
of three cases: this event was explicitly rejected by the service (a confirmed 400/409; fix the cause and
retry with the same event_id); an earlier queue head of the same task was rejected and blocks all later
reports (check with `pending`; the task's owner handles it); or the service is unavailable or the receipt
was lost and it cannot be confirmed whether it was recorded (after recovery run `flush --task`, keeping
the same event_id and content). With a warning you must not report "reported".

### Choosing and correcting a workstream

Workstreams are not preconfigured and new names are never rejected. The existing workstreams are those
used by the tasks currently shown in the default list (within the display TTL), computed on every read
and listed by `/api/workstreams` (newest update first, with task counts). Look at this list before
registering a task and reuse an existing workstream for the same work. `task`/`retag` check
automatically: a name that differs from an existing one only by whitespace, full/half width or case is
filed under the existing spelling; any other name is registered as a new workstream, and the receipt's
`workstream` field says "New workstream" and lists similar existing workstreams for you to confirm.
When the service is unavailable the name is registered as given, with a note that existing workstreams
could not be checked. Old workstreams whose tasks have all passed the TTL drop out of the list by
themselves; nothing needs cleaning.

When a registered task is in the wrong workstream or its title needs a fix, use `retag` with explicit
approval from the user or a maintainer; a reason is required:

~~~powershell
py -3 -B $board retag example-task --workstream 'Frontend' --reason 'User approved: registered under the wrong workstream'
~~~

`retag` creates an audited `task_meta` event that changes only title/workstream; the task_id, runs,
claims, progress, task board and history stay the same. When nothing changes it records
`task_meta_unchanged`. Ordinary agents do not change the workstream of other people's tasks.

~~~powershell
py -3 -B $board task example-task --title 'Example task (replace with the real name)' --workstream 'Frontend'
py -3 -B $board run example-task example-run-01 --agent Codex --client Codex-Desktop
py -3 -B $board show example-task
py -3 -B $board claim example-task example-run-01 --expected-version 0
py -3 -B $board report example-task example-run-01 --status running --phase 'Implementation' --progress 'Adapter entry point done' --next-step 'Targeted verification'
py -3 -B $board ask example-task example-run-01 --question 'Please choose approach A or B' --option A --option B --unanswered-policy 'Pause this branch; continue the other authorized items'
py -3 -B $board report example-task example-run-01 --status waiting_dependency --human-input not_required --blocker 'Waiting for the agreed candidate' --next-step 'Check the contract once the candidate arrives'
# When the task has an execution plan, declare the plan item this run works on (--unlink removes it):
py -3 -B $board report example-task example-run-01 --status running --mainline main-1 --branch side-1
py -3 -B $board report example-task example-run-01 --status paused --phase 'Preparing handoff'
~~~

Every client uses exactly the same commands with its own labels, for example
`--agent Claude --client Claude-Code`. Scripts use the same entry point too.
A simulation with two producers proves protocol compatibility only, not that a given client was tested
for real.
Report at start, stage completion, substantial blockers, pending decisions, pause/close and handoff; do
not nudge models on a timer or report every grep or function edit.
Reports are field increments: an old blocker/decision does not disappear just because status becomes
running. Clear it explicitly with JSON `null`.

A takeover must read the current version first and then claim explicitly; never bump expected_version
by one after a 409:

~~~powershell
py -3 -B $board show example-task
py -3 -B $board run example-task example-run-02 --agent Claude --client Claude-Code
# Suppose version=1 was just read and the takeover of this task is approved:
py -3 -B $board claim example-task example-run-02 --expected-version 1
py -3 -B $board report example-task example-run-02 --status running --phase 'Continue verification'
py -3 -B $board report example-task example-run-02 --status ended --phase 'Stage closed'
py -3 -B $board flush --task example-task
~~~

A claim shows the current owner; it is not a business authorization. Late progress from an old run stays
in its own record and cannot overwrite the current run.
An audit run uses `run ... --role audit` and cannot claim execution ownership; an execution run ending
does not remove audit records.
Several collaborators on one task each register a run, but there is only one current execution owner;
the board is not a seat-management platform.
An unfinished execution run that is not the owner is a "parallel execution run" (an ended one whose
request was not withdrawn is kept too). Its progress is recorded only on its own run (events marked
`not_current_execution_run`) and does not change the task state. The overview returns the total
`parallel_run_count` and the latest 8 (plus all with unhandled manual requests) as `parallel_runs`;
cards fold the count, the number recently active and the latest activity time, and the detail lists each
one. A former owner that claimed and was replaced is labelled "former owner". Open questions of any
parallel run (including former owners and ended runs) enter the manual to-dos, marked as handed off or
"run ended, not withdrawn", until they are closed explicitly as described in "Manual requests"; this
never rewrites the current owner's state.
For long-term parallel work, prefer separate tasks over piling runs into one task.

The overview's execution state represents only the current owner. Every audit run that has not ended
(including waiting, paused, unknown and different candidates) is listed separately, as are ended audit
runs whose manual request has not been withdrawn, each with its agent, phase, candidate binding and
business time. Other ended audits and former executors remain in the detail and are never merged into
"task done".
Execution and audit can overlap and are never added into one total; combined status, agent and
"Needs you" filters must match the same run (including parallel runs).
A normal running audit does not enter the manual to-dos; `waiting_input` says only that the run waits for
input, not that the input must come from the user.
Only open questions (`ask`, or a run-level `human_input=required`) enter "Needs you" at the top
and in the sidebar. A declaration takes its request text from `decision`; a required declaration
with no request text shows "needs your action, request not stated yet" and never invents content.
An audit's own blockers, decisions and staleness stay on its card/detail and are not assigned to the
user automatically.
With no execution owner but a fresh unfinished audit, the task is not marked stale or needing attention
just for lacking an owner; execution state and time stay unknown and are not filled in from the audit.
Staleness, blockers or pending decisions of any audit still raise notices; with neither an owner nor an
unfinished audit, an "unknown, to check" notice remains.
Audit state is not an audit conclusion. Different or unbound candidates are marked explicitly; an ended
audit creates no pass record and changes neither the executor's time nor ownership.

## Manual requests: questions, answers and withdrawals

This is a dimension separate from run status. When you need the user to approve, choose, provide
material or act, raise one concrete question with `ask`; each question is one event, and one run may have
several open at once:

~~~powershell
py -3 -B $board ask example-task example-run-01 --question 'Please choose approach A or B' --option A --option B --unanswered-policy 'Pause this branch; continue the other authorized items'
# The receipt's question_id (the ask's event_id; also given when the ask is only queued) closes this question
py -3 -B $board resolve example-task example-run-01 --question-id <question_id> --answer 'User chose A'
py -3 -B $board withdraw example-task example-run-01 --question-id <question_id> --reason 'No longer needed: handled with the approved approach'
~~~

`--option` is repeatable, at most 8 options of at most 200 characters each; `--mainline [--branch]` links
the question to a plan item (same precheck as above).
Questions are identified only by question_id, never by matching text. `resolve` records the user's
answer and `withdraw` records why it is no longer needed. Both close only this one question; neither is
an approval, and neither changes run status, owner or authorization. Formal conclusions still go to the
authoritative files.

A close may come from any registered run of the same task: a successor can close questions its
predecessor left, without the original asking run. The earliest close to arrive wins; later closes stay as
duplicates under the closed item. A close that names a question_id not in this task, a run of another
task, or a run with no open declaration is listed in the detail under "Unmatched resolves /
withdrawals" and affects no question.
Handing off, ending or going stale, or the task passing its display period, never closes a question; it
only folds into "Possibly outdated". Without a close it stays.
Close only questions the user really answered or that are truly no longer needed. Before closing a
question someone else left, check that it really was handled; never empty the to-dos just to reach zero.

### The user closes a request on the page

When done, the user can click "Handled" (and click again to confirm) on an inbox row, in the task detail
or on the unstated tag. It is recorded as closed by you on the board page (op `user_close`, source
`user_web`, no run). It closes only that question or that run's declaration, again earliest close
wins. It is not an answer, approval or authorization and changes no run status, owner, plan or display
time; the original text stays under "Closed" in the detail.
A later required declaration from the same run opens a new one. This is the page's only write:
`POST /api/user-close` accepts only the local page (matching Host/Origin, page token and instance; the
page token changes on every start and guards against cross-site requests; it is not a credential).
Agents must never call this endpoint or forge the `user_web` source; the credentialed `/api/events` also
rejects user_close.
An agent that sees a question the user closed on the page need not resolve/withdraw it; anything that
needs the user's answer must still be asked in the session.

### Run-level declarations (`human_input`)

`human_input` takes required / not_required / unknown (CLI `--human-input`, the same field with an
underscore in JSON/HTTP). It is a coarse, per-run flag; prefer `ask` when the request has its own text
and options. A required declaration is one open request of that run, with no question ID; its text is
the run's current `decision`. It closes when either the same run later reports something other than required, or
any registered run then submits a resolve/withdraw that targets the run (`--target-run`, used instead of
`--question-id`):

~~~powershell
py -3 -B $board withdraw example-task example-run-02 --target-run example-run-01 --reason 'Request left by the predecessor; checked that the user answered'
~~~

A later required declaration from the same run opens a new one. Reports without the field show as
"not declared"; nothing is inferred from status or natural language, and original events are never
rewritten. not_required is not an execution authorization; unknown means
the owner is not sure yet or has not declared.
Fields update incrementally; changing only status does not withdraw a request. A run whose declared
request was handled reports not_required explicitly and clears the request text with `decision:null`, for example with UTF-8 JSON through `report --data`:

~~~json
{"status":"running","human_input":"not_required","decision":null,"unanswered_policy":null}
~~~

When not_required coexists with a leftover decision, the card keeps a contradiction notice for the owner
to check; the original text is never hidden silently.
Do not use "no user needed" text instead of an explicit close, and never widen a task's permissions because
the page shows zero.

An ended audit with an open question stays in the overview as an unresolved request, explicitly ended;
it does not count as a running audit and never becomes the execution owner. Execution takeovers still
check the task version, and old execution runs cannot overwrite a new owner; the successor checks related
requests and, when needed, asks again from its own run instead of inheriting authorization. A stale
request keeps its original business time and a stale-source marker; it is only folded, never cleared
automatically.

Combined status, agent and manual to-do filters match the same execution/audit run. The service's
`needs_attention` means broad informational attention, not human input; `human_input_required`
and `open_questions` reflect only open questions.
Apart from "Handled" (the user close) the page has no button to approve, answer, dispatch, execute or
change real state.

## Plan links and the next step

This applies only to tasks with an execution plan (on-demand task board). A run may declare explicitly
which plan item it is working on (an audit run: reviewing): `report --mainline main-1 [--branch side-1]`,
in JSON `"item":{"mainline":"main-1","branch":"side-1"}`; `--unlink` (JSON `"item":null`) removes it. A
link says only what this run is doing; it never implies mainline progress or completion, and plan state
changes only through task-board updates. Links are never guessed from text, branch names or times. The
detail lists linked execution/audit runs under their mainline; when the linked plan item has finished or
been cancelled it is marked "The linked work item has finished". An unfinished run with no link, or whose
linked item does not exist, shows under "Unassigned runs": it is still a normal run of the task, not an
error, and needs no link added. Tasks without a plan show no links or unassigned runs.

A task has exactly one next step, and cards, the detail and the `next_step` field of `/api/state` and
`/api/task` agree: when the plan is in effect (frozen, active or awaiting confirmation) and has a focus,
it is the next step of the focused item (the focused branch's when that branch is unfinished and has a
next step, otherwise its mainline's), with the plan focus as its source; otherwise it is the next_step
self-reported by the current execution owner run; with neither it shows as not specified.
With a plan, `report --next-step` is only a note on that run (the detail shows it inside the run as
"Next step (self-reported by this run)"), and the receipt carries a note saying so. To change the task's
next step, follow `runbooks/TASK-BOARD.md`: update the plan item's next_step with `task-board progress`,
or move the focus with `focus`.

## Reports, candidates and sources

Put complex fields in a UTF-8 JSON file and send it with `report <task> <run> --data <file>`. The file is
edited normally by the responsible agent and is not a shell command. Example (replace the full SHA and
paths with real values):

~~~json
{
  "status": "running", "phase": "Targeted verification", "blocker": null, "decision": null, "human_input": "not_required",
  "next_step": "Check evidence, then submit", "unanswered_policy": null,
  "candidate_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "worktree": "worktrees/example", "audit_id": null,
  "evidence": ["scratch/incoming/example/REPORT.md"],
  "dependencies": ["other-task"], "count": {"done": 4, "total": 9, "unit": "tests"}
}
~~~

Use null for unknown nullable fields; human_input must use unknown, never null. Leave out unrelated
fields. A count is shown only for a real done/total/unit; no project percentage is derived.
Formal progress stays in the file the project configuration names; formal reports and candidates remain
the source of truth. Within a stage, do not also rewrite STATE, the decision log or long reports.
`draft <task>` only prints a handoff draft to stdout for checking; it never writes authoritative files.

Run states: running/waiting_input/waiting_dependency/paused/ended/unknown.
Deliveries are separate, through `delivery <task> <run> --data <file>`:

~~~json
{"stage":"self_test","verdict":"PASS","candidate_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","evidence":"scratch/incoming/example/TESTS.md","audit_id":null}
~~~

stage is implementation/self_test/independent_audit/deployment/post_deploy. An independent audit entry
must come from a separate audit run, with an audit_id, an evidence path and a full SHA; the system does
not verify the identity behind the label or the report content, and the page shows only "Independent
audit record (not verified automatically)".
Without a report you cannot claim an audit pass. After the candidate changes, old records show as not
for the current candidate; there are no automatic green checks or production GO.

Sources are agent_report / document_snapshot / program_observation. A CLI self-report never becomes a
program observation.
`import-snapshots` reads only the file sections named in the configuration, explicitly; it does not scan
recursively or import periodically, and running it again does not recreate tasks.
A snapshot records the excerpt, the whole file's SHA-256, the source time, the read time and the receive
time. The root document can keep changing; imported excerpts are never overwritten.
Leave source_at null when unknown; "just imported" does not mean "just happened". Documents never fill in
state for the current run.
The importer initializes tasks with an idempotent `ensure_task`: snapshot metadata never renames an
existing task or moves its workstream, and new source times can still append snapshots.
Displayed excerpts normalize CRLF/CR to LF; the SHA-256 is still taken over the original bytes, and
source files are never rewritten.
By default one task shows only the latest 200 distinct document sources; beyond that, truncation and the
total source count are stated, and no difference among the shown sources is never read as agreement of
all of them.
Abnormal future source/read times, and historical entries whose source time is later than the read time,
are excluded from "latest activity/snapshot" selection and marked for checking on the page; the original
history events are kept. Newly submitted source/read times may be at most five minutes ahead of local UTC.
Actual program observations are submitted as complete activity events and must cite the observation file
and the actual observation time; never use page refreshes or service liveness as business activity.
Businesses with no connected monitoring show "not connected / unknown".

## HTTP protocol, idempotency and resending

`submit <complete event JSON>` may resend the same event_id and content; never recreate the ID/seq to hide
a retry. It shares the registration precheck above. The overview `/api/state` no longer carries delivery
records (the `/api/task` detail keeps them) and includes a read-only `outbox` summary (total queued, per
task count with the queue head op/run, task-board queued count); a missing task returns a 404 with the
service identity.
All data is validated first, before the outbox is touched; HTTP and offline use the same protocol. The
ordinary CLI allocates a monotonic seq per run, and the program stamps UTC times.
HTTP callers guarantee stable IDs and run seq themselves; never let independent producers share hand-made
sequence numbers of one run.

~~~json
{"event_id":"stable-event-01","task_id":"example-task","run_id":"example-run-01","seq":8,"op":"progress","occurred_at":"2026-01-01T17:00:00Z","source":{"kind":"agent_report","label":"script"},"data":{"status":"paused","phase":"Handoff"}}
~~~

POST /api/events with Content-Type application/json, `Authorization: Bearer <contents of the local
write-token file>` and `X-Agent-Board-Instance: <instance_id from instance.json>`. Plain HTTP callers must
also address the current instance, and treat as recorded only a 200 response whose
`service=agent-board-v1`, `instance_id`, original `event_id`, normalized event `digest` and
`recorded=true` all match; a plain HTTP 200 or a redirect is not a confirmation. The CLI never follows
redirects that would forward the credential.
Successful GET /api/state and GET /api/task responses also carry service and instance_id. The CLI's
show/draft capture the current instance.json before the request, send X-Agent-Board-Instance and check the
identity of that same response; on a mismatch they return an error and print no data or draft. Direct
HTTP readers must check the response identity too; the page itself reads without the header. A local
source change does not change a running service until it is restarted.
The helper reads the credential in-process; it never appears on the command line, in URLs, on the page or
in ordinary logs. Never copy the token into prompts or versioned files.
In normal reporting only the service writes board.sqlite3; the CLI writes the queue and sequence numbers,
and agents never edit the database or the page directly.
Only a label correction the user explicitly authorized may stop the service and maintain it under the
lock, as in the naming section above; that is not a data-write permission for ordinary agents.

Events are persisted to the outbox first and then sent; a queue file is deleted only after success. If an
HTTP receipt is lost the event stays and the service deduplicates.
Across runs, recovery follows the persisted enqueue order, so the causal order of task/run/claim/progress
never depends on run names.
An `independent_audit` delivery from a registered execution run is an immutable role conflict and returns
a 409 with the instance identity; the delivery is rejected and recorded, and can be recovered after
checking through the explicit evidence-keeping flow below. Ordinary schema/capacity errors still cannot
be quarantined this way.
A real 409 keeps the original and blocks the task's later events, while other tasks continue; the version
is never changed automatically, the conflict is never dropped, and retries are not endless.
`pending` is read-only; its `local_validation` marks each queued event as currently valid or rejected,
with the rejection reason.
Never treat "the queue exists" or "the service is online" as resent. On a real 409, first check the
current version with `show <task_id>`, then use `pending` to see that task's earliest queued event and
its `queue_sha256`.
Only after confirming that the event really is stale, and explicitly deciding to keep it without
replaying it, may an authorized operator run:

~~~powershell
py -3 -B $board quarantine <event_id> --expected-sha256 <queue_sha256 from pending> --reason 'Checked the current version; this old claim is not replayed'
py -3 -B $board flush --task <task_id checked this time>
~~~

By default `quarantine` asks the exact instance again to confirm that the queue head returns a real 409,
saves the original queue content, the Base64 of the original bytes, their SHA-256 and the reason in
`quarantine/` of the ignored runtime, and only then removes it from the queue. It never changes
expected_version, resends or cleans up the original automatically.
The quarantine check uses the same captured URL/instance identity from the health check through the
conflict response to the archive; if the service was replaced or is unreachable the queue is kept and the
quarantine fails. After recovery, check again and retry explicitly; it never switches to a new instance
automatically.
A non-head, changed, already accepted, mismatched or unproven-409 event is refused. The rest of the queue
still needs an explicit `flush --task <task_id>`; a corrected claim must use a new event_id/seq and be
submitted explicitly against the current version. A successful `quarantine` only means this one conflict
was preserved as evidence, not that the whole task is synced. Never quarantine unclear conflicts in bulk.

When recovering, run `flush --task <task_id>` explicitly within this authorization; `start` only starts
the service and **does not flush the queue**.
Short reports resend only the same task; `flush` without `--task` is an explicit global flush that needs
its own check of everything queued and is never an example of single-task recovery.
In flush output, `remaining/scoped_remaining` is what remains in the selected scope and
`global_remaining` reports the whole queue separately; other tasks' backlog never turns this task's
success into a failure.
exit 0 = recorded/read successfully, or the selected scope was fully resent; exit 2 =
QUEUED_NOT_RECORDED (pending confirmation, not necessarily unrecorded) or the selected scope still has
queued items; exit 4 = UNVERIFIED_NOT_QUEUED (offline with no registration basis, not queued); exit 1 =
error/unproven; keep the input and check it.
A late smaller seq of the same run only enters the timeline and never rolls the state back. Business time
keeps the maximum; receive time is recorded separately.
To resolve a real conflict, do not just append a correction after the old queue head; it would still be
blocked. Recover through the explicit evidence-keeping flow above.

Defaults: one event 64 KiB, 300 tasks, 100,000 events, a combined DB/WAL threshold of 256 MiB; queue
1,000 events / 8 MiB; HTTP timeout 1.5 seconds, lock contention at most 2 seconds; logs 256 KiB each with
two rotated backups. At capacity the board reports an explicit error and never deletes history
automatically. Capacity is checked before commit, so one commit or SQLite page overhead may slightly
exceed a threshold. Only a visible page reads the light API every 10 seconds; project files are never
scanned.
The stale threshold defaults to 24 h; a task with no current report is also listed for checking. No new
report never proves a failure, a hang or a need to restart.

## Local boundaries and verification

The service listens on IPv4 loopback only; no LAN, internet or tunnel. Host/Origin checks, a write
credential, side-effect-free GETs, CSP and textContent escaping.
There is no arbitrary command execution or general file download; evidence shows only copyable project
paths, out-of-bounds and sensitive directories are refused, and a path existing does not mean its source
was verified.
Other processes of the same OS user can still read the credential; this is not a sandbox against
malicious local processes. Never collect full chats, reasoning, environment variables, secrets or raw
sensitive commands.
The page can only let the user close a request (see "The user closes a request on the page"); it cannot
answer, approve or write anything else. A dependency reported as finished is only for people to judge;
it never wakes a session, dispatches work or relaxes a gate.

Unit and process tests:

~~~powershell
py -3 -B -m unittest discover -s .ai-workflow/integrations/agent-board/tests -v
~~~

The tests create their own temporary directories and random loopback ports and never touch a live
board. The browser checks are optional and equally isolated: each `tests/browser_*_checks.mjs` builds a
synthetic database, starts its own instance on a free ephemeral port, lets the browser reach only that
instance, and stops it exactly when done. Run them with Node:

~~~powershell
$env:AGENT_BOARD_PLAYWRIGHT_MODULE = 'file:///path/to/node_modules/playwright-core/index.mjs'
$env:AGENT_BOARD_CHROME = 'C:/path/to/chrome.exe'   # optional; otherwise Playwright's own browser
$env:AGENT_BOARD_PYTHON = 'C:/path/to/python.exe'   # optional; otherwise py -3 (Windows) or python3
node .ai-workflow/integrations/agent-board/tests/browser_overview_checks.mjs <new evidence directory>
~~~

`AGENT_BOARD_PLAYWRIGHT_MODULE` points at an existing module; nothing is installed and no global
environment is changed. What each check covers:

- `browser_checks.mjs`: basic listing, literal text against injection, a real CLI report appearing after
  the periodic refresh, a 390 px layout and local time display.
- `browser_overview_checks.mjs`: multi-run overview, out-of-order requests and closing-detail boundaries.
- `browser_views_checks.mjs`: the Brief / Who's working / Feed views and the "Handled" close.
- `browser_sorting_checks.mjs`: newest-first task/workstream order, stable ties, unknown times, the
  old-task toggle and refreshes that keep filters and the detail.
- `browser_workstream_checks.mjs` and `browser_ttl_checks.mjs`: workstream navigation and the display period.
- `browser_parallel_checks.mjs`, `browser_unified_checks.mjs`, `browser_task_board_checks.mjs`: parallel
  runs, the unified detail and the plan section.

Evidence directories and test runtimes do not go into Git. Synthetic multi-run checks do not prove that a
real audit client is connected. `tests/verify.py <output dir>` records the author checks and their exit
codes; author checks are not an independent audit.

## Integration note the user can forward to an existing task

> This project has enabled the local board. Read only the AGENT-BOARD manual and your task/direct
> dependencies; do not load the whole project history.
> Check or reuse the stable task_id; start a new run, read the current version, and claim explicitly once
> the takeover is approved.
> Set agent to the tool name (such as Claude / Codex / Gemini / OpenCode), not the model version, effort
> or task role; new tools are not limited by a fixed allowlist.
> Send short reports with the same CLI at start, stage completion, substantial blockers, pending
> decisions, pause/close and handoff.
> Keep run waiting apart from manual requests: when the user must act, raise a concrete question with
> ask and note the receipt's question_id; resolve it after the user answers or withdraw it when no longer
> needed, closing by question_id and never by guessing from text.
> When the task has an execution plan, --mainline/--branch declare the plan item this run works on; the
> task's next step follows the plan focus.
> Do not rewrite other people's document snapshots, and do not treat a report as an approval or an
> independent audit; formal conclusions still go to the authoritative files.
> When the board is unavailable, keep the helper's pending state and flush --task after recovery; do not
> restart business work, send messages or widen permissions because of it.
> I have not assigned you any new product task through the board. Continue within the scope you were
> already approved for.
