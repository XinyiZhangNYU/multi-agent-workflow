# Local multi-agent workflow

~~~yaml
version: v1
~~~

This file is the single authority for the current rules. README handles navigation, STATE records only
the current state, and DECISIONS keeps the approval sources. The version changes only when a rule
changes, never for progress. Project directories, data and host exceptions are described in the project
configuration that STATE points to. Optional tools are loaded only when a task needs them; they are not
required reading for every session.

## 1. Authorization, roles and completion

Do only the work the user explicitly asks for in the current request. Roadmaps, existing files, other agents' opinions
and passing tests never authorize starting work, widening scope or changing a product decision.
Restoring an old file does not restore the authority it once carried.

The user assigns participants, implementers and reviewers per task; no vendor, model or client is bound
to a permanent seat. Within an authorized scope one agent may implement and self-test.
An independent review that the user or a contract requires cannot be skipped, and anyone who helped
produce the artifact under review cannot call their own check an independent audit.
Report important risks that remain uncovered and suggest targeted checks; a green test run does not
replace judgment about method, product semantics or real-world effect.

First make the requirement, scope, known acceptance criteria and explicit unknowns clear, and write them
into the task's own document when needed; small tasks do not need a contract directory.
During implementation you may question the requirement, raise counterexamples and propose simpler
designs, but never silently change approved semantics or acceptance criteria.
When a new product decision, a permission expansion or an architectural fork appears, pause the affected
part and hand it to the user; authorized parts that do not depend on that decision may continue.
Stop after delivering what was asked with proportionate verification. Do not refactor on the side or
turn suggestions into new tasks.

When the responsible agent, inside an authorized task, identifies a design, research method, product
definition, cross-module contract or implementation route that has both significant downstream impact
and substantial unresolved uncertainty, it should first finish the local analysis that does not depend
on the decision and then proactively propose one concrete external consultation to the user. Whether to
propose it is the responsible agent's judgment based on actual impact and evidence, not a mechanical
trigger on file count, code size, model brand or an ordinary test failure. The proposal states the open
question, the evidence and candidate options so far, the unresolved disagreement, what the consultant
should challenge hardest, the suggested role and access method, how participating affects the
consultant's eligibility as an independent auditor, and which work can continue unaffected.

A consultation proposal is not authorization to call anyone. The user may also ask for a consultation
at any time; before it starts the user decides whether to call it and names the consultant/target task
and the method/access channel precisely enough to deliver. The responsible agent must not fill in,
substitute or silently use an internal subagent. If the user declines, continue with the current best
judgment and do not ask again unless substantial new evidence would change the decision. Existing
implementation authorization survives a consultation, but its result is only input; adopting it in a way
that widens scope or changes product semantics, a contract or acceptance criteria still goes back to the
user. A task or conversation that consulted on the design or implementation of an artifact cannot later
call its own review of that artifact independent; an independent audit needs an auditor and method the
user arranges, from someone who did not help produce it.

## 2. Permissions and resources

Within a task's authorization you may read locally, modify ordinary working files, test, build, create
isolated branches/worktrees and commit locally. The following each need explicit authorization: push,
pull requests, deployment, database migration, production configuration, operations on real data, paid
calls or purchases, account or permission changes, and deleting user files. Budgets and standing
authorizations for a product use come from that use's current contract and the user's decisions, never
from archived manuals.

Never bypass restrictions of the client, tool or operating system. When a sandbox cannot see host
credentials, containers or browser state, first separate identity from resource visibility; that is not
a reason to ask the user to reconfigure, and host privileges are not a way to widen the action.
New outbound material must be checked by a person for secrets, licensing and data boundaries; an auditor
reading an exact commit that was already approved for push is not a second release. Logs must not keep
credentials, secrets from raw commands or reversibly sensitive content.

One clearly named owner manages writes to a worktree; different contributors use separate worktrees.
Do not postpone your own commits forever because someone else is working on another branch, and never
stage someone else's in-flight changes.

There is no default deadline for a task, and you do not add concurrency or kill business jobs to fit a
terminal's wait window. Heavy operations run serially by default; a host's concrete resource limits are
project configuration, not permission to parallelize. Tell the user before starting a full test suite,
containers or a local database, or several heavy subprocesses.
Prefer targeted tests and run the necessary full verification once the candidate converges; do not
repeat runs without purpose, and do not lower acceptance criteria to save runs.
After two rounds without new evidence, stop retrying and explain the blocker, except for repeated runs
whose purpose is to measure flakiness or reproducibility.

For a large run whose cost depends on input size and that has no recent measurement on the same path,
first do a bounded probe on the same path; if a probe is impossible, say so and let the user decide.
Throughput from a different path is not a precise ETA. Reading a status only has value if it can change
the current action; after a read shows no change, state "condition → action" before observing further.
Runs that can be detached safely and have a process identity, logs and a resume/stop entry point use the
platform's wait or monitor facility; short-interval polling is not supervision. Only lightweight probes
that diagnose a hang may use a small liveness timeout; a business deadline set by the user is recorded
separately.

### Background tasks

A scheduled or long-running background task must not open windows or prompts on the user's desktop, and
a flag that only asks for a hidden window is not proof that none appears; verify it by observation. Its
arguments and command line hold only fixed local paths and non-secret switches, never keys, tokens,
connection strings or other credentials. The verified launch method for the host platform belongs in the
project configuration; reuse it rather than inventing a new one per task. Existing tasks follow this rule
when they are next changed or re-registered.

## 3. Git checkpoints and isolation

The following events trigger a recovery checkpoint; do not commit on a timer, file count or token count:

1. a task or slice is clearly finished, paused or abandoned, or the writing session is about to end;
2. handing over to another agent or an independent reviewer, or switching to an unrelated task;
3. joint development reaches a terminal state or pauses;
4. before and after a real network collection run;
5. the user makes a cross-task rule decision, or a conclusion is about to be depended on by the next
   piece of work.

Look at status/diff in every affected checkout and separate this round's changes from pre-existing,
concurrent and temporary ones; save what must be inherited according to "Single home for each kind of
content". Stage only the exact paths of this checkpoint; never use `git add -A` / `git add .` to sweep in
unknown changes. Unfinished work may be committed as an honest `wip:`; a commit proves the bytes are
saved, not that anything is approved, accepted or deployed.

Each relevant checkout gets one receipt: `COMMITTED <exact SHA>`, `CLEAN` or
`CHECKPOINT_PENDING <marker path>`; repositories you did not touch are `NOT_TOUCHED`.
Use pending only for a real blocker: concurrent writes in the same checkout, ownership or semantics that
cannot be split safely, or a required external candidate. The local marker goes in
`runtime/checkpoints/pending/`, stays out of Git, and records at least time, event, checkout, observed
HEAD, scope, owned paths, unclear paths, blocker, created_by and the checkpoint owner to be named by the
user. It is not an approval. Whoever takes over re-checks the current tree and ownership, commits the
exact paths or confirms clean, then closes the marker and reports. Unreviewed changes to rules or product
authority inside a recovery commit are marked `UNREVIEWED_CANONICAL_CHANGE` and are reconciled item by
item before they propagate.

After committing, also check the push conditions: is the remote/upstream explicit, which commits are
ahead, do they contain material not approved for release, and is pushing authorized this time. Report
"pushed", "not pushed: not authorized" or "conditions not met"; never describe a commit as a remote
backup. Without authorization you may state what could be pushed and ask; do not push on your own.
Network or host identity failures do not prevent a safe local commit.

Creating an isolated worktree inside an authorized task needs no extra approval, but first verify the
base, target path, existing branches with the same name and the worktree list; never force or overwrite
someone else's worktree. After creating it, report purpose, base SHA, branch, path and writer.
A one-off worktree you created yourself may be removed safely at the end once it is clean, its
untracked/ignored files hold no unique user material and all its commits are reachable; report it.
Retiring other people's, historical or joint worktrees requires confirming purpose, writer, live
references and user authorization, one by one, never by bulk deletion by name. Promote any evidence that
must be kept first, use `git worktree remove` from the owning repository, and never delete recursively or
force-remove a dirty tree. Keep branches by default; use `git branch -d` only when the merge relationship
and the cleanup authorization are both explicit, never `-D`.

## 4. Memory, documents and history

### Optional local progress board

At every task start or resume, first check in the project configuration that STATE points to whether
the board is enabled; "optional" does not mean an enabled project may ignore it. Multi-stage or handoff
tasks in an enabled project must read runbooks/AGENT-BOARD.md and, as it describes, reuse or create a
stable task, register this run, check the version they take over, and declare whether manual action is
currently needed. An existing task is not created again because the client or session changed. The
obligation is the same for every client.
Once a project enables the board, multi-stage or handoff tasks send short reports at start, stage
completion, substantial blockers, pending decisions, pause/close and handoff. Small tasks do not need a
record; do not nudge models on a timer or report every tool action. Startup reads only this task and its
direct dependencies, never the whole project history. A task_id is stable across sessions, runs are
separate, and taking over requires an explicit version check; the interface is neither a scheduler nor an
identity system.
The board stores only sourced run observations, self-reports, document snapshots and a display index. It
never writes back to project authority and never produces an approval, an audit pass or a production GO.
The user may mark a manual request as handled on the web page to close it (that is not an answer or an
approval); agents never use that control on the user's behalf, and stale requests are only collapsed, not
closed automatically.
The three kinds of source are shown separately with their source, read and receive times, and conflicts
are kept. Acceptance records are bound to their object; a new candidate does not inherit an old pass.
Short in-stage reports do not require rewriting long reports, STATE or DECISIONS at the same time; the
formal record obligations for handoff, pause and close are unchanged. When the board is unavailable,
keep the pending recovery information, continue other authorized work, and do not relax any approval.
Integration and failure boundaries are in runbooks/AGENT-BOARD.md.

Before a long program starts, report the stage being executed, the next observable result and the exact
resume entry point; do not invent heartbeats when there is no stage change or measurable progress. When a
stage ends, report first and then give the final reply; do not save all stages for one report at the end.
Report only this task, and an ordinary startup does not flush old queues. Check the receipts of progress
reports and task-board updates separately; on failure distinguish recorded, pending sync, conflict,
cached and unknown, and never switch IDs, bump versions or claim a save that did not happen. When a
client has no read/tool trace, record it honestly as unobservable; a document check is not proof that a
real session followed the rules.

### On-demand task board

Progress-board reporting and task-board planning are two independent switches. The task board applies
only when the user explicitly asks to use it for the current task, or when resuming the same, clearly
bound task for which it was explicitly enabled before. A task being complex, long, called a "big task",
or in a project with the progress board enabled does not enable the task board automatically. When it
does not apply, skip the dedicated manual, do not create an empty record and do not scan other task
boards; small tasks can simply be executed. The existing project-level progress reporting obligation is
not cancelled by this.

At startup, context recovery or handoff, check the applicability above first; only if it applies, read
this section, runbooks/TASK-BOARD.md and the bound task's persisted summary. When the user says "enable
the task board, plan first and then execute" and has explicitly authorized the implementation scope,
finish the necessary inspection, save the mainlines and their completion criteria, freeze the baseline,
and then continue within the original authorization without waiting for another confirmation. When the
user says "plan first, wait for my confirmation", only do the planning and wait. Saving, freezing, a
confirmation field or a web page state is never a new business authorization; new goals, product
decisions or expanded permissions are still handled under sections 1 and 2, pausing only the affected
part.
While awaiting confirmation, explicit revisions from the user may be applied with a change that keeps the
original item and the reason, or by adjusting owners, next steps, dependencies and focus. The plan stays
awaiting_confirmation; do not confirm a wrong plan first, delete and rebuild the board, or treat a planning
adjustment as authorization to implement.

Mainlines are split by deliverable result and have at least a stable identifier, goal, completion
criteria, owner or owning session, status and next step; an unknown owner is honestly left empty.
Record dependencies, parallelism and external blockers when needed, without building a dependency-graph
engine. After freezing, keep the original mainlines for the duration of the task, including completed
ones; never silently delete them, replace a goal, lower completion criteria, or rebuild the plan to hide
an unfinished commitment. Status, blockers, owner, next step and execution arrangements that do not change
the goal may be updated, and freezing does not require everything to be serial. Cancelling, replacing or
widening a mainline requires an explicit user decision, keeps the original item with a short reason, and
uses an explicit change operation distinct from ordinary progress updates; no permanent version history
or approval platform is built.
All branches being done, a run ending, a report being generated or branch counts never automatically
mean a mainline is complete.
The program computes completed / total active / unfinished mainlines; cancelled and replaced items are
listed separately and never count as completed. The frozen baseline count and additions, replacements and
cancellations remain visible; never shrink the denominator to manufacture a completion rate. With no
mainlines the plan shows "not planned yet". All running mainlines are visible; focus only marks the
current emphasis. Plans are grouped by the workstream of the original progress task, tasks in the same
workstream are independent, and counts are not effort, forecasts or acceptance authority.

There are only two levels: mainlines and branches. A necessary branch hangs under a specific mainline and
says what it resolves and which mainline it returns to. Necessary breakdowns within existing
authorization can be added on your own; optional improvements do not become delivery gates
automatically. Do not create branches for every tool call, test, report or subagent.
Completed branches may be deleted after their short result or the reference to their formal artifact has
been folded into the mainline. Unfinished items cannot be deleted without trace under the name of
"cleanup"; cancelling keeps a short reason. Deleting a branch never deletes source code, evidence, audit
reports or user files.

The lead reads the compact summary of all mainlines; an executor reads only the mainlines its role needs,
the related branches and direct dependencies.
Use the service summary and pagination by default; do not download the full plan and filter it locally.
The summary does not carry all branch results, changes or events; read history/show for those when needed.
Explicitly save read time, version, scope and continuation cursor; a cache proves only the last read, not
current sync. Read at start, resume and takeover; update briefly at stage completion, substantial
blockers, focus changes, branch additions/removals, pauses and handoffs. Do not report every action, wake
models on a timer, or dispatch a resident agent to maintain the board. When a branch is handled, return to
its mainline; keep advancing independent mainlines rather than stopping everything because one is
externally blocked, and do not hide a blocker behind endless preparation. The semantic boundaries of the
formal handoff, reports and progress reports stay the same, and they need not be rewritten every time.

A task board is bound to a stable task_id, separate from runs; a handoff records the exact read entry,
version and current focus. Resuming the same task board from another session, client or model does not
re-plan over the old content; resuming the same explicitly enabled task needs no second enablement, and an
old task board does not authorize a new task.
The interface and model summaries read the same persisted record; never maintain two plans by hand. The
task board records only the execution plan and progress; it is not authority for product facts, audit
conclusions or production approval, and checkmarks are never written back as a product PASS.

On sync failure, distinguish the valid baseline already read from updates not yet confirmed as saved, and
use the implementation's cache/pending/conflict recovery; never claim to be synced or bump a conflicting
version automatically. If the baseline cannot be confirmed, do not rebuild on your own; an ordinary
reporting failure does not stop unrelated authorized work. A task may be closed when it ends; the user
may keep the summary or explicitly delete this task board's record, which affects only that board and
does not clean up the original progress task/run, history, evidence or product files. Actual interfaces
and examples are in the dedicated manual; do not invent commands, review systems, loop/budget controls,
scheduling or permission systems.

### Single home for each kind of content

Before writing DECISIONS.md, STATE.md or OPEN.md, read this table in the same turn; do not decide from a
navigation summary alone. "Important, stable, approved, cross-module" does not mean "a cross-task workflow
rule"; a project root outside version control is not an exception that allows a second copy of a product
decision.

| Content | Single home |
|---|---|
| Approved cross-task workflow rules and rejected alternatives | Approval source appended to DECISIONS; current rule maintained in WORKFLOW |
| Tentative direction / current workflow state the next task must inherit | STATE (at most 150 lines) |
| Real risks that are unresolved or accepted as deferred | A permanent case in OPEN; product facts are only referenced from project authority |
| Product, data and implementation progress | The product status document named by the project configuration; STATE only navigates |
| This task's execution plan and focus, when the user enabled the task board | The same persisted task-board record; a handoff saves only the exact entry point, never a second plan |
| Facts about sources, interfaces and licenses | The project's research/evidence directory |
| Product architecture and long-term design | Project design documents, not DECISIONS |
| Detailed design tightly coupled to code commits | The code repository's docs or an enabled joint contract |
| Audit briefs, raw reports, unpromoted attachments and handoffs | The project's scratch/incoming; optionally review-packets for attachments that must be delivered |
| Closed incidents, superseded rules and experiments | A fixed Git snapshot or an explicit read-only archive, located by a thin index |

Never copy a summary, approval fact or hash of the same product conclusion into DECISIONS and STATE as a
second authority; summaries drift too. Before appending a rule, check it against current rules, the
relevant project authority/config, STATE and OPEN. Only the user's visible words or explicit confirmation
count as approval; an agent's paraphrase or a recovery commit cannot replace them. Whoever lacks write
access to a key file only proposes a patch, and a maintainer named by the user with that write access
applies it.

Existing DECISIONS entries are append-only and never rewritten in place. A new entry records the time
(with time zone), approval source, decision, what it supersedes, the reason, and rejected alternatives
with their reasons. Historical entries are not an operating manual; new sessions do not read them in full
by default.
OPEN keeps every O-ID permanently with its status and the source of closing/reopening; OPEN, DEFERRED and
CLOSED are kept apart. A recurrence of the same kind is appended to the original ID as a reopen; only a
different risk gets a new number. Closing cannot happen merely because nobody is working on it or because
it moved to an archive. With user approval, closed bodies may move from the current file to a fixed Git
snapshot while the active OPEN keeps a short index and the recovery location. Unclosed risks keep enough
current state, boundaries and next steps; old facts that have not been re-checked carry a date and scope.

Current rules may be condensed; original evidence is never rewritten. An approved cleanup may change
current rules, navigation, state and active risk summaries once the old bytes are fixed in a recoverable
commit/ref. Sent packages, raw data, manifests and old reports are not rewritten to "update links,
encoding or conclusions"; necessary corrections are added as new records. Archiving or moving out of HEAD
does not prove a risk is resolved, data is fully backed up, or historical tests still pass.
New historical material is preferably kept by normal Git checkpoints; when a separate body is needed, keep
only facts of lasting value and do not start an ever-growing log.

### Temporary material

Ordinary tests may use a system temp directory they created and clean it up safely; never mix user files
into it and then delete them as temp files.
Temporary handoff files inside long-lived project directories go into scratch/incoming with author,
consumer, purpose and time in the name:
`TEMP__<author>__TO-<consumer>__<purpose>__<timestamp>.<ext>` (`TEMP-RECONSTRUCTED` for rebuilt files).
The file header records artifact_class=temporary_handoff, authority=none, author, intended_consumer,
purpose, disposition, deletion_requires=user-confirmation and created_at (with time zone).
An audit result directory named by a dedicated brief may keep that name, but its entry file still states
author, consumer, purpose and that it carries no authority.
"Delete after reading/executing" only marks a deletion candidate: after consuming it, report the path,
SHA-256, the findings handled and pending and the basis for each disposition, then wait for the user to
confirm the deletion. Handling may be accepting, rejecting, deferring or keeping the report only; "handled"
never means "all adopted". Deleting a staged report does not delete conclusions already promoted from it;
whether the project root is versioned is decided by the project configuration.

## 5. Joint development

An ordinary branch/worktree is not joint development. A protocol in which agents jointly maintain a
shared contract, an integration branch and resolutions must be explicitly enabled by the user, who
arranges the participants; ordinary tasks do not use it automatically.
A joint protocol may have two or more contributors but always exactly one integration owner. The user,
who decides or provides inputs or acceptance criteria, does not thereby become a contributor.
Participants declare only the directed direct-message links the task actually needs; full mesh is not
required. The full enable/close rules are in runbooks/JOINT-DEVELOPMENT.md, which expands this section.
By default the initiator is also the mechanical integration owner; no third seat without context is
added, and its own domain implementation is still written first on a separate contributor branch.
The integration worktree has a single writer and receives exact SHAs; mechanical integration rights are
not product decision rights or authorization to merge into the target.
Exploration and convergence are reversible; responsibility for repairs is handed over based on the actual
contract state, not the number of audit rounds or model run time.

When several audit remediations run in parallel in the same repository, do not assume they stack just
because their area names differ. After each remediation's approach is defined and before its first write,
check the shared authorities/paths it is expected to touch; after candidate commits exist and before
merging, compute the exact intersection of changed paths against each candidate's own frozen base.
Without substantial overlap the original owners keep fixing independently. When shared source, schema,
migrations, time/identity rules or test invariants collide, freeze the input SHAs and let the user name
one existing agent to converge the overlapping scope; the other owners stop writing it. Escalate to the
full joint protocol only when an external contract or product semantics must be maintained jointly; branch
order, identical finding numbers or the absence of Git conflicts never replace semantic judgment.

When joint development or a two-session independent audit needs direct communication, read
runbooks/AGENT-MESSAGING.md. A joint protocol binds by actor/registry and declared links; a lightweight
audit uses the exact sessions the user named for the original responsible agent and the independent
challenger. Seats are never fixed by underlying model name, and recipients are never guessed from display
names, the most recent sender or the inbound source.
A direct message may be absorbed immediately as an in-contract fact, but it does not replace the local
contract or commit, widen permissions or change the baseline automatically.
If any required direction is unavailable, do not dress up repeated manual relaying by the user as
automated collaboration; stop and report the communication blocker.

## 6. Independent audit and evidence

Independent audits are enabled when the user asks for one in the current request, never automatically by
model, stage or historical risk level. Before starting, the user must specify this audit's auditor/target
task and the audit method/access channel, for example a pinned commit read directly from the code host, a
chat assistant reading local evidence through a repository connector, a peer agent session over direct
messages, or an internal subagent. When both are precise enough to deliver, do not ask again; when either
is unclear, the responsible agent stops before dispatch and asks. It must not choose or complete them on
its own, and must not silently substitute because of convenience, quota, an unavailable target task or a
missing direct-message capability.
An internal subagent may be used only when the user explicitly chooses it for this audit. Its process
usually does not appear as a separate task the user can inspect; explain that visibility boundary before
use, and label the result honestly so it never poses as a visible audit task the user named.
Provide only evidence pointers and audit requirements (the brief); do not re-package source code, diffs,
conversations or raw logs the auditor can read directly. Key evidence that cannot be read directly is
listed as inaccessible, or a minimal attachment is provided under release authorization.
The pinned-commit channel and the chat-plus-connector channel each produce, by default, a single
self-contained prompt that can be sent as is. That prompt is also the brief/guide: it pins the candidate,
questions, evidence starting points, permissions, output and stop conditions, and lets the auditor widen
the search on its own within the authorized scope. Do not by default also generate, or ask the user to
forward separately, a guide, an old-report mapping, a brief manifest or a transport list; existing
reports, reception records and evidence are referenced by reachable path/commit/hash. Create a separate
attachment only when it has independent machine-readable or evidentiary value, or when the target channel
cannot obtain necessary content directly, and say why in the prompt.
Prompt skeletons, access boundaries and result handoff for the four channels are in
runbooks/INDEPENDENT-AUDIT.md, which expands this section.
The two-session direct channel the user names uses a three-step automatic cross-session handoff. In the
chat-plus-connector channel the user sends the audit prompt in a chat they create, choosing model and
effort, and hands the result to the responsible agent. When item-by-item reception or a re-audit is
needed, the responsible agent writes its responses to disk and prepares the next self-contained prompt,
and the user decides whether to send it to the original auditor. A connector-based audit never discovers
or binds the chat automatically, never reads replies automatically and never sends reception or re-audit
messages to the chat; a prompt cannot enable that kind of follow-up transport. A messaging action the user
explicitly delegates once follows that one authorization and is never assumed to be standing
authorization from an audit prompt or an old binding.
There is no required number of findings, rounds, transport ledger, extra model relaying or uniform heavy
manifest. Reports should also challenge the method, the goal and the product definition and propose
alternative explanations and counterexamples, not just confirm that code and tests ran correctly.
Evidence pointers should locate the behavior under review, where it is defined, the production call
chain, the corresponding tests/counterexamples and how to recompute, rather than only broad directories;
the auditor first verifies along those entries and may still widen the search within the authorized
scope when it finds contradictions, evidence gaps or spill-over effects.

A re-audit of the same object after fixes cites the previous report, the previous audit baseline, the
item-by-item reception record and the current candidate; by default it focuses on the new diff, unclosed
findings and the affected call, data and test chains. Unchanged parts may reuse the previously recorded,
bounded coverage without re-reading only when the contract, dependencies, build/test infrastructure and
related evidence have not changed in applicability. A first audit, a changed audit goal, a user request
for a full re-audit, or changed product/research assumptions never use an incremental re-audit to narrow
scope; reused coverage does not inherit the author's judgment of correctness.

When the user explicitly arranges two peer agent sessions as a lightweight audit loop, the original
responsible agent handles initiation, item-by-item reception and later fixes; any available model may run
underneath. Automatic communication has exactly three steps: `AUDIT_REQUEST`, `AUDIT_RESULT` and
`AUDIT_RECEPTION`. The brief, report and item-by-item responses are each written to disk first, and the
direct message carries only the path, SHA-256, exact commit and action. After the third step, in which
the responsible agent asks to converge on the closing position, the automatic flow ends; the challenger is
not required to confirm, approve fixes or route to another seat, and the user decides what follows.
If direct messaging is unavailable, fall back to the manual process; never guess the target or re-send
automatically.

Distinguish static reading, actual execution, self-testing, independent recomputation and what remains
unverified; passing tests do not prove a method works or that nothing was missed.
Report the necessary baseline, command/input range, environment limits, exit status, evidence paths and
what was not run. A hash proves only that the compared bytes are identical, not that generation matched
the source's intent, that the command ran in the right directory or that the conclusion is true. Evidence
claimed to be immutable must be bound to the actual file set and the run version that produced it; later
results from a live tree never overwrite an old failure.
Do not inherit stale green results; a check result belongs only to the scope and bytes checked, and no
global pending verdict without a downstream consumer is set. When a formal use needs stronger evidence
constraints, that use's contract defines them.

## 7. Tools and maintenance

- The startup contract has a single versioned source, and its deployment targets come from the project
  configuration. After a change, sync with bin/sync-startup-contract.ps1 and verify every copy; a failed
  check must name the copy and can never degrade into "several wrong copies agree with each other".
- When platform or project configuration changes, run the corresponding focused tests and configuration,
  syntax, byte and reference checks. A host probe does not prove a client loaded something; conclusions
  about a real client's behavior need a matching real test.
- Keep established encodings when editing scripts: PowerShell sources use UTF-8 with BOM (Windows
  PowerShell 5.1 needs it for non-ASCII text); new data/JSON/logs use UTF-8 without BOM. Generators check
  content and encoding intent; never bulk re-encode old evidence.

The final receipt focuses on what was completed, what was verified and not verified, what awaits the
user's decision, and the checkpoint and push state of every affected repository; no wall-clock daily
reports are required.
