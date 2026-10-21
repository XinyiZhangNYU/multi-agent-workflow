# Lightweight independent audits over four access and handoff channels

This manual expands WORKFLOW's audit rules. It is triggered only by the user's current request, and no
challenger vendor or seat is fixed. The auditor must not have helped produce the artifact under review; a
read-only audit never authorizes fixes, external operations or redoing the research.

Before dispatch, the responsible agent must have the user's explicit choice of this audit's `challenger`
and of the audit method/access channel. Reading a pinned commit on the code host, messaging an existing
agent session directly, creating a new agent or external task, and an internal subagent are different
methods and none implies another. An existing session must be addressed by its exact ID; a new task needs
separate authorization to create it. When both are precise enough to deliver, do not ask again; when
either is unclear, stop before sending and ask, never choosing or completing them yourself.

An internal subagent is not the default channel. Create one only when the user explicitly chooses "use an
internal subagent for this audit"; naming a model, a timing or permission to proceed does not fill in an
unspecified auditor or method. An internal subagent usually does not appear as a separate task the user
can inspect; explain that boundary before using it and label the result `internal_subagent_audit`. An
unavailable target, a failed direct message, quota or tool convenience never authorizes silently switching
channels; stop before sending, report, and wait for the user to choose.

## Common brief

Provide only evidence pointers and audit requirements; do not re-package source, diffs, conversations or
logs the auditor can read directly. The brief states:

- the audit goal and core questions;
- the object under review, its scope and explicit exclusions;
- known risks, unknowns, and author claims that must not be taken on trust;
- an index of key design, implementation, tests, raw evidence and recomputation entry points, located as
  closely as possible to the behavior under review, where it is defined, the production call chain, the
  corresponding tests/counterexamples and how to recompute, rather than broad directories; and what is
  inaccessible;
- the expected output: verdict, highest-severity findings, what was actually read/recomputed, what is
  unverified;
- permission boundaries: read-only, network/provider, database, deployment, real data, and so on.

There is no closed list of labels, required number of findings, fixed number of rounds, manifest,
transport ledger or vendor-specific format. The auditor should also challenge the method, the goal and the
product definition, and offer alternative explanations, proactive counterexamples and simpler routes; it
must neither stop at checking that code and tests ran correctly nor conclude correctness from finding no
defect. Whether the method/goal/product definition is sound and whether the code executes correctly are
different questions; the brief states which depth and recomputation this audit needs, and "full code
review" never implies that every research assumption has been accepted.

The pinned-commit channel and the chat-plus-connector channel use, by default, one self-contained prompt
that can be sent as is; that prompt is the brief/guide, and the user only sends or pastes this one item.
It pins the exact candidate, audit questions, first evidence entry points, permissions, deliverables and
stop conditions; the auditor may look up related files on its own within what the channel authorizes.
Existing source, reports, item-by-item receptions, tests and evidence are referenced by reachable path,
commit or hash, not copied into a parallel guide, reception map, brief manifest or list for transport.
Create an attachment only when it has independent machine-readable or evidentiary value, or when the
target channel cannot obtain necessary content directly, and say in the prompt what it is for. This never
degrades into "audit this repository/directory": pinning the object and giving actionable evidence
starting points remain mandatory.

## The four channels at a glance

| Channel | Who starts it | How the raw result comes back | Automatic three-step cross-session handoff? |
|---|---|---|---|
| Pinned commit on the code host | The user sends the prompt in the chosen chat or external audit task | The audit task's reply; the user hands the raw report or its path to the responsible agent | No |
| Chat + repository connector | The user sends the prompt in a chat they created, choosing model/effort | The user returns the raw report or a readable report path; the responsible agent receives it item by item | No; a re-audit is a new prompt the user sends |
| Direct messages between two agent sessions | The responsible agent sends `AUDIT_REQUEST` to the challenger session the user named | The challenger returns `AUDIT_RESULT` by direct message | Yes, strictly three steps |
| Internal subagent | The responsible agent creates it after the user explicitly chose it | Returned to the parent task internally | No; three logical stages are not three messages |

All four channels share this manual's independence, common brief, re-audit and retention rules. The
channel only decides how evidence arrives and how results are handed over; it does not change the audit
depth and grants no fix, release, network, provider, database, deployment or push permission.

## Channel 1: pinned commit on the code host

For a chat or external auditor, named by the user, that reads bytes already pushed to the code host
directly. Provide the repository (owner/name or exact URL), the full `audit_commit` that is pushed and
accessible to the auditor, a branch/ref for navigation only, entry points to files/design/evidence on the
host, and `host_excludes`. Uncommitted, unpushed or ignored material, local databases and local run
results are not naturally visible to a remote auditor; key local evidence is explicitly marked unverified,
or a minimal attachment is provided under new release authorization. Later changes on a live branch do not
widen the conclusion about the pinned commit, and nobody needs to relay source the auditor can read.

By default the responsible agent delivers only the single self-contained prompt below, which also serves
as brief/guide. A first audit lists the starting points directly; a re-audit cites the previous report,
item-by-item reception and previous baseline in the same prompt, with no separate mapping file.

Prompt skeleton:

~~~yaml
audit_id: <stable id>
repository: <owner/name or URL>
audit_commit: <full pushed SHA>
branch_or_ref_navigation_only: <optional>
audit_goal_and_questions: <questions to challenge>
evidence_entrypoints: <index of design/implementation/tests/raw evidence>
prior_audit_and_reception: <re-audits only: previous report, reception record and previous baseline>
host_excludes: <local, ignored, unpushed or inaccessible material>
required_challenge: <implementation + relevant method/goal/product definition + counterexamples/alternative routes>
permissions: read_only_repository
deliver: <verdict, findings, read scope, unverified items and citations>
~~~

This channel has no direct three-step loop. When the audit task finishes, the user gives its raw output or
a stable path to the responsible agent, which writes its item-by-item reception and hands it to the user to
decide. Unless the user asks, the reception is not sent back to the external auditor.

## Channel 2: chat + repository connector reading local evidence

For a chat audit task, named by the user, that reads local evidence through a connector to the user's
machine or repository. A broad evidence project may register the project root; the brief is navigation
and a list of claims to check, not a read allowlist. The auditor first verifies along the entry points and
may widen the search within the approved root when it finds contradictions or gaps; the project does not
need to be registered again for each candidate. The object under review is still pinned by an exact
worktree, commit and tree/clean or WIP byte boundary; other worktrees are only labeled background and are
never mixed into the candidate.

Do not assume the connector exposes a task mode literally named `read_only`. The permission contract states
directly what may and may not be done: the static phase allows reading/searching; tests, recomputation or
writing audit artifacts first need the matching authorization, with caches, scripts and results confined
to a designated audit output directory. The frozen candidate, raw evidence and rules stay unmodifiable;
being able to read the project root grants no shell, jobs, network, provider, database, deployment or push.

By default the responsible agent delivers only the single self-contained prompt below. It points to
reports, reception records and evidence already inside the project root; the auditor reads along the entry
points and may widen the search. Only independent machine-readable evidence, or necessary material the
auditor really cannot reach in the authorized project, becomes an attachment with a stated reason.

Prompt skeleton:

~~~yaml
audit_id: <stable id>
connector_evidence_project: <project ID + absolute project root path>
frozen_candidate: <absolute worktree path + full SHA + tree/clean or WIP boundary>
initial_dispatch: user_manual
model_and_effort: user_selected
audit_goal_and_questions: <questions to challenge and author claims not to take on trust>
evidence_entrypoints: <design/implementation/tests/raw evidence and recomputation entry points>
prior_audit_and_reception: <re-audits only: previous report, reception record and previous baseline>
evidence_scope: <roots and entry points that may be searched>
evidence_exclusions: <secrets/private/inaccessible material>
static_phase: read_and_search_only
validation_phase: <not authorized / authorized commands, cwd, expected writes and network boundary>
audit_output_directory: <if writing is approved, a directory isolated from the candidate>
forbidden_effects: <rewriting candidate/evidence, providers, live databases, deployment, push, ...>
required_challenge: <implementation + relevant method/goal/product definition + counterexamples/alternative routes>
deliver: <report identified by audit_id + candidate SHA; then verdict, findings, actual tools/commands/exit status, evidence hashes, unverified items>
~~~

The user creates or picks the chat, chooses model/effort and sends that one prompt. When the chat
finishes, the user gives the raw report or a readable path to the responsible agent, which checks the
candidate identity and the report and writes its item-by-item reception. For a re-audit, the responsible
agent cites the original report, the reception record and the new candidate in the next self-contained
prompt, and the user decides whether to send it to the same chat.

This channel has no automatic follow-up transport: transport or round fields in old prompts grant no
permission to send. Because the user sent a first prompt, the responsible agent does not enumerate/bind the
chat, read its replies or send reception/re-audit messages; old runtime bindings grant no permission to keep
communicating. A messaging action the user explicitly requests once is carried out as requested that once.
Substantive disagreements, needs to widen evidence/execution permissions, or changes to product semantics go
to the user. The chat's opinion approves no fix, deployment, push, database or provider operation. A
"read-only" statement in the prompt does not replace checking what the connector actually exposes, and the
absence of a `read_only` startup flag does not mean giving up the operation-level boundaries above.

## Channel 3: direct messages between two local agent sessions

The user may name two exact agent sessions for one audit: the `responsible_agent` and an independent
`challenger`. Eligibility depends on both sessions running in a client that can send direct messages to an
exact session, not on which model runs underneath. The `responsible_agent` must be the same agent that was
responsible for the area under review; it initiates the audit, receives the comments, responds item by item
and, after the user approves, fixes. No separate audit sender, reception owner or repair owner is split off.

The automatic round trip has exactly three steps:

1. `AUDIT_REQUEST`: the responsible agent writes the brief to disk, then sends the challenger the brief
   path, SHA-256, audit commit, permission boundaries and its own exact return session ID;
2. `AUDIT_RESULT`: the challenger performs the read-only audit, writes the raw report to disk, and sends the
   responsible agent the report path, SHA-256, findings and a request for item-by-item reception;
3. `AUDIT_RECEPTION`: the responsible agent writes its item-by-item response
   (`ACCEPTED / REJECTED / PARTIALLY_ACCEPTED / DEFERRED`, reasons, proposed fix scope and open items),
   then sends the challenger the response path and SHA-256 and asks to converge on the closing position.

Once step three is delivered, the automatic flow ends. The challenger is not required to confirm again, it
does not approve fixes, and implementation does not start automatically; the user then decides whether to
ask for a re-check, settle disagreements, approve fixes or defer. The direct message is only a
notification and never replaces the brief, report and response files. The target must be the exact session
ID the user specified or the request stated, never inferred from a display name, recent contact or inbound
source. If either required direction cannot send or delivery fails, stop the automatic loop, report it and
fall back to manual handling.

This loop applies only to local audits between sessions that have direct messaging. Chat products, the
pinned-commit channel and other external challengers use whatever delivery they actually have; a
same-named underlying model does not imply direct-message capability.

The kickoff prompt gives at least:

~~~yaml
audit_id: <stable id>
responsible_session_id: <exact session id>
challenger_session_id: <exact session id named by the user>
brief: <path + SHA-256>
audit_object: <worktree/repository + commit + tree state>
permissions: <allowed_checks + forbidden_effects>
return_contract: AUDIT_RESULT goes back to responsible_session_id
~~~

## Channel 4: internal subagent

Use only when the user explicitly chose an internal subagent as this audit's method. Before creating it,
the responsible agent explains that a subagent usually does not appear as a separate task the user can
inspect and that its process is received mainly by the parent task; the result must be labeled
`internal_subagent_audit`. Give it only the brief, the frozen object and necessary evidence entry points,
so the author's self-assessment is not taken as fact; context isolation, model and depth follow the user's
choice and the tool's actual capabilities.

Prompt skeleton:

~~~yaml
audit_id: <stable id>
channel: internal_subagent_audit
selected_by_user: <source of this explicit authorization>
model_or_agent: <named by the user or explicitly allowed>
brief: <path + SHA-256>
audit_object: <worktree/repository + commit + tree state>
evidence_entrypoints: <readable design/implementation/tests/raw evidence>
permissions: <allowed_checks + forbidden_effects + output directory>
visibility: no_separate_sidebar_task
required_challenge: <implementation + relevant method/goal/product definition + counterexamples/alternative routes>
deliver: <raw report, findings, actual read/recompute scope, unverified items>
~~~

This channel performs no cross-session direct messages. `AUDIT_REQUEST` is contained in the creation
instruction and `AUDIT_RESULT` is returned to the parent internally; the responsible agent then writes its
own item-by-item `AUDIT_RECEPTION` and hands it to the user, without waking the subagent again for
confirmation. Only when the user explicitly asks for a re-check or further convergence may a follow-up turn
go to that subagent; the subagent never approves fixes.

## Re-auditing the same object after fixes

A re-audit brief cites the previous report, the previous `audit_commit`, the responsible agent's
item-by-item reception and the current candidate commit. By default it checks the new diff, unclosed
findings and the affected call, data and test chains; it first verifies along explicit entry points, and
when it finds contradictions, evidence gaps or spill-over effects the auditor may explain why and widen the
search within the authorized scope.

Unchanged parts may reuse previously recorded, bounded coverage without re-reading only when contract,
dependencies, build/test infrastructure and related evidence have not changed in applicability. What is
reused is the location of material and the recorded coverage, not the author's confidence or a blanket old
green result. A first audit cannot assume unchanged code was already reviewed; when the audit goal changes,
the user asks for a full re-audit or product/research assumptions change, use the new scope and do not
narrow it by calling it a post-fix re-audit.

## Collision check between parallel remediations

After the item-by-item response exists, and before asking for fix authorization or the first write, the
responsible agent checks the shared authorities and paths that other active remediations in the same target
repository expect to touch. After candidate commits exist and before merging, compute the exact diff
intersection with each remediation's own frozen base. Do not first force different bases onto one common
ancestor, or existing mainline drift will be miscounted as remediation overlap.

- No substantial overlap: each responsible agent keeps fixing and committing independently.
- Only docs, generated files or code blocks provably independent of each other: the receiver may integrate
  mechanically with proportionate checks.
- Shared production code, schema, migrations, time/identity rules, external contracts or the same test
  invariant: freeze each candidate and report, tell the user the exact intersection and semantic risk, and
  let the user name one existing agent for the overlapping scope; the other agents stop writing it.
- A collision that could not be predicted beforehand triggers at the candidate checkpoint; passing tests on
  each side never justifies cherry-picking directly.

Collision handling records at least each side's base/candidate SHA, the intersection, the finding mapping,
the choices made, combined tests and unverified items. Audit or self-test conclusions about the original
candidates belong only to their original bytes and do not transfer to the integrated candidate. Enable full
joint development only when a shared contract and resolutions must really be maintained together; an
ordinary collision does not require a full contract directory.

## Results and retention

A report is not an automatic decision. Before changing anything, check the actionable code/runtime claims;
if they cannot be reproduced, keep the gap. Method and product judgments do not lose validity because they
are hard to reproduce mechanically; disagreements go to the user, and the author's self-assessment is not
proof. Distinguish accepted, rejected, deferred and kept-under-observation; "all comments handled" never
means "all adopted".

Briefs, raw reports and unpromoted attachments go to the project's scratch/incoming by default;
review-packets is used only when a local attachment must be delivered separately. Lasting conclusions are
saved according to WORKFLOW's "Single home for each kind of content"; raw reports keep their original bytes
as the task requires, and deleting them after consumption still needs the user's confirmation. Packages and
manifests already sent are not rewritten when the process is simplified.
