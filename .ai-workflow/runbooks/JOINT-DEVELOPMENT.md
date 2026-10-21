# Explicit joint development

This manual expands the WORKFLOW rules on joint development; read the full protocol only when the user
explicitly enables it. Isolating work in a branch/worktree inside an ordinary task does not start this
protocol. When audit remediations collide substantially, INDEPENDENT-AUDIT.md routes only to
"Collisions between parallel remediation candidates" below, which does not start the full protocol.

## Enabling

The user approves or confirms: collaboration_id, common base commit, target branch, contributor
branches/input SHAs, integration branch and worktree, integration owner, shared contract path, the split
of work between participants, and whether communication is `manual` or `direct`.
Actor names are assigned by the user for this task; there is no fixed vendor or frontend/backend
mapping. Direct communication additionally follows AGENT-MESSAGING.md.

There may be two or more contributors; each still has its own scope, branch/worktree and candidate
commits. The whole protocol has exactly one integration owner. A user who provides requirements, real
inputs, metric definitions or acceptance expectations remains the decider and input provider unless the user
separately names themselves a contributor. Participants set up only the directed communication links the
implementation needs; full mesh is not required.

Unless the user says otherwise, the initiator, who holds the context, is also the integration owner; no
third mechanical integration agent is added. This is a duty of an existing participant and grants no
priority in product decisions and no right to merge into the target branch.
STATE only points to the contract's STATUS; ordinary short-lived worktrees are not written to global
STATE.

## Contract and writes

The shared contract lives in the code repository under `contracts/<topic>/vN` and must make at least the
following reconstructable:

- STATUS: current phase, owner/participants, scope, blockers, next step;
- inputs/assignments: exact base/input SHAs, each contributor's paths and constraints;
- field matrix, language-neutral schema and synthetic samples where applicable: source, semantics,
  null/unknown, version and permissions;
- reviews/resolutions/OPEN: substantive comments, counterexamples, reasons for accepting/rewriting/
  deferring/rejecting, items awaiting the user;
- test plan/results: how each contributor's candidate is verified, actual results, what is unverified,
  and evidence pointers.

Do not create every file empty for every topic, and do not copy every chat line, raw reasoning or diff
already in Git into the contract. Code stays in the normal source/test directories. Freeze the
requirement boundary at creation; write substantive findings, candidates and handoffs at normal
checkpoints; simple ACKs need no record. A contract conflict that affects later implementation is
recorded first, and only then does the affected part continue.

Each contributor implements in its own branch/worktree and produces exact commits. When the integration
owner also implements a domain part, it too commits in a separate contributor branch/worktree first; it
cannot implement directly in the integration tree and declare the contract satisfied.
The integration tree has one writer, receives exact SHAs serially, maintains the shared contract and runs
cross-side checks. It may resolve mechanical conflicts that do not change the contract's meaning;
disagreements about fields, authoritative producers, lineage, missing values, permissions or product
behavior go to RESOLUTIONS/OPEN first, and those needing the user cannot be settled by merge order.

Rebasing is still an explicitly recorded mechanical action: if a shared schema is added in an integration
commit, contributors consume it only after receiving a new base or input method that they can rebuild
independently; they never copy or privately edit the shared schema to keep an old parent commit.
Messages carry only facts and requests; they never automatically approve new semantics, permissions or
the acceptance of a candidate.

## Exploration, convergence and repair responsibility

Takeover is never decided by "first version / nth rework", model vendor or minutes of continuous run
time. Mark CONVERGENCE only when all of the following hold; otherwise stay in EXPLORATION:

1. the relevant contract, schema and samples/probes have exact commits;
2. known failures are reproducible and map to the existing contract;
3. no product semantics, authoritative producer or architecture decision is pending within the
   affected scope;
4. what remains is mainly local implementation defects that do not change the external contract.

A new contract contradiction returns the state to EXPLORATION immediately. In convergence one
repair-owner handoff may be proposed; a cross-domain takeover needs one explicit user approval, recorded
in STATUS with from/to actor, contract/candidate commit, scope_paths, accepted_findings and
approval_ref. The original contributor stops writing that scope.
The approved repair owner keeps fixing local defects in scope instead of sending the whole package back
for every error; domain implementation still goes through a separate contributor tree. A new product or
contract fork pauses the related part; a takeover never grants new decision rights.

Reviews focus on the diff against the baseline, risky seams and frozen counterexamples. Unchanged bytes
may skip a repeated line-by-line audit only when contract, dependencies, build and test infrastructure
have not changed in applicability; when dependencies or the contract change, affected old code is still
checked. During development run targeted tests; at READY_TO_CLOSE or for a merge candidate run the full
relevant cross-side acceptance and a production build. Do not restart whole-package full audits for local
errors. A narrow review by someone who implemented part of the work is not an independent review.

## Collisions between parallel remediation candidates

When two or more audit remediations land in the same target repository, each responsible agent first
finishes its own finding reception and records its fix approach. Before implementing, compare the shared
authorities/paths each expects to touch; after candidates exist and before merging, compute the exact
intersection of changed paths against each candidate's own frozen base, and use a trial merge to help
spot content conflicts. No Git conflict does not prove there is no semantic collision, and identical
finding numbers do not prove the problems are the same.

Without substantial overlap, joint development is not entered. When shared production code, schema,
migrations, time/identity rules, external contracts or test invariants collide, freeze the original
candidates and let the user name one agent that already has the context as owner of the overlapping
scope; the other agents stop writing that scope but may continue authorized work that does not depend on
it. The owner receives exact SHAs in a separate integration worktree, records which findings from each
side were accepted, rewritten or kept, and runs combined tests; it does not declare a winner by
cherry-pick order.

If the collision only requires integrating existing fixes and no new contract is maintained jointly, a
single task reception note is enough and no full contract directory is created. Only shared product
semantics, authoritative producers, wire/schema negotiation or sustained multi-round contributions
justify the user upgrading it to the full protocol in this manual. Collision handling ends once the
integrated candidate exists, the affected tests ran and unverified items are recorded; merging into the
target, re-auditing, pushing and deploying are each authorized separately.

## Closing and branch disposition

States: ACTIVE / FROZEN / READY_TO_CLOSE / MERGED / PAUSED / ABANDONED / SUPERSEDED.

- MERGED: first freeze the input SHAs, integration HEAD, verification and open items; merge into the
  target after the user approves, keeping history and not squashing by default. Re-run the integration
  checks on the target branch and verify the integration HEAD is reachable from the target HEAD.
- PAUSED: save a resumable WIP commit, the blocker and the next step; keep the branch/worktree and the
  state pointer.
- ABANDONED/SUPERSEDED: commit the reason, reusable findings and the successor; unmerged branches are
  kept by default.

Each terminal or paused state triggers an event checkpoint in every relevant checkout: contributors each
report COMMITTED/CLEAN/PENDING, and the integration owner also records the contract, target merge and
workflow closing state. Committing one line of STATE does not mean other dirty trees are sealed.
Concurrent writes in the same workflow tree may be registered as pending, with an explicit receipt;
never silently claim the whole joint development is closed.

"Development finished" does not automatically delete branches or worktrees. After the user approves
closing and cleanup, check tracked/untracked/ignored files, live references and unique evidence one by
one, then use `git worktree remove`; delete a branch with `git branch -d` only when it is confirmed merged
and cleanup is authorized. Never force, never delete unmerged results, never push automatically. Old
contracts stay in Git history, and STATE does not keep expanding a log of closed projects.
