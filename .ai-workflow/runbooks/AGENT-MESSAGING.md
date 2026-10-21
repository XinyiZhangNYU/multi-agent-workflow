# Direct messages between agent sessions

```yaml
status: optional-adapter
authority: WORKFLOW.md sections 5 (joint development) and 6 (independent audit and evidence)
activation: an active joint protocol, or a two-session independent audit named by the user
```

Some agent clients can send a message to another, exactly identified session (for example a
"send message to session/thread" tool in a desktop or CLI client). This manual describes how to use that
capability without letting it widen roles or permissions.

## When to read

Read this only when:

1. the `STATUS.md` of an active joint protocol says `session_messaging: direct`;
2. `.ai-workflow/STATE.md` explicitly says `joint_messaging: direct` for the current protocol;
3. the user asks you to investigate cross-session messaging; or
4. the user named an exact `responsible_agent` session and an independent `challenger` session for a
   local audit.

Ordinary branches/worktrees, manual handoffs and other tasks without direct messaging do not read this
manual. Chat-based audits through a repository connector are sent and returned by the user by hand (see
INDEPENDENT-AUDIT.md); the automatic three-step exchange here applies only to the two-session direct
audit the user names.

## Capability boundary

The only operation assumed here is "send a direct message to one exactly identified session". The sender
cannot choose whether the platform delivers it as a new turn, as an addition to the current turn or by
some other internal scheduling, and should not rely on a busy/idle status it read; that status may
change before the message is sent. Use whatever direct-send tool the client actually exposes, without
assuming parameters that control scheduling. The transport never changes WORKFLOW's contracts or
permission boundaries.

Whether the capability is exposed can depend on the session's runtime context; that one session can send
does not mean the current one can. Direct-message eligibility therefore belongs to "an exact participant
and directed link of the current collaboration", not to a display name, a model or a global seat.

## Activation and identity binding

Underlying model names create no seats and no direct-message capability. A model participates under the
identity of the session it runs in; a same-named model in another product or an outside conversation does
not inherit that capability.

### Joint development

When starting each joint task the user assigns task-scoped `actor_id`s; this manual presets no names.
Before direct messaging is enabled:

1. the versioned `STATUS.md` records `session_messaging: direct`, the participating actor_ids and the
   declared `direct_message_links`, but no raw session IDs;
2. the local runtime registry holds the unique mapping actor_id → exact session ID for the current
   collaboration;
3. every participating model gets a visible startup binding with the collaboration_id, its own actor_id,
   and the actor_id and exact session ID of each direct peer;
4. every declared link is probed without project side effects. Two-way collaboration needs both
   directions to pass; until all pass, the state is `pending_pair_probe` and direct messaging is not used
   for normal work.

There may be more than two participants; `direct_message_links` is a directed graph declared by business
need, not a full mesh. An undeclared link gains no routing eligibility just because both participants
belong to the same collaboration; there is still exactly one integration owner.

When a participant is replaced, old bindings and old probes are void. The user updates the
actor/session mapping, shows the new binding to the affected participants and re-probes.

### Two-session local audit

The user names the original `responsible_agent` and a `challenger` that did not help produce the artifact
under review; there are no separate sender, reception or repair seats. The first `AUDIT_REQUEST` states
the audit ID, both exact session IDs and the return target. This flow creates no long-lived collaboration
registry and needs no transport ledger for its three messages. A missing send tool, an ambiguous target
or a failed delivery ends the automatic loop; never fall back to display names, recent contacts or an
inbound source ID.

## What may be sent

Within a joint protocol or two-session audit the user activated, and within existing roles and paths,
you may send:

- kickoff and scope restatement;
- non-blocking progress, findings and cross-side interface requirements;
- clarifications under an existing contract;
- blockers, conflicts or questions that need a resolution;
- contributor candidates, exact SHAs, tests or the final handoff.

The body is natural language and no persistent message ledger is added, but every message opens with four
routing headers:

```text
collaboration_id: <current joint task>
message_type: FACT | REQUEST | PROPOSAL | BLOCKER | HANDOFF | PROBE
sender_actor_id: <actor_id within this task>
recipient_actor_id: <actor_id within this task>
```

It then states, self-contained:

1. what happened or what is needed;
2. whether it is still within the existing contract;
3. where applicable, the local contract path, evidence path or exact commit.

A two-session local audit uses these three message types instead, together with `audit_id` and both
exact session IDs:

```text
AUDIT_REQUEST | AUDIT_RESULT | AUDIT_RECEPTION
```

Once `AUDIT_RECEPTION` is delivered, the automatic exchange ends; whether to re-check, decide or approve
fixes is up to the user.

The sender must assume the message is seen by the target immediately. Correctness must never depend on
"the other side will surely wait until its current turn ends before reading".

## Receiving and contract boundaries

The receiver acts on content, not on delivery timing:

- **In-contract addition:** may be absorbed immediately, to avoid continuing on a stale assumption;
  write it down at the next normal checkpoint, candidate commit or handoff.
- **Contract conflict or new finding:** first register it in the joint contract's `RESOLUTIONS.md` /
  `OPEN.md`, pause the affected part and do not decide it yourself.
- **Contract or permission change:** treated only as notice of an amendment. Changing scope, acceptance,
  roles, write paths, real data, remote operations, budget or product semantics still needs the contract
  update or user approval the original process requires.
- **Simple ACK:** needs no local record and triggers no automatic ACK loop.

Direct messages create no new tasks, roles, authorization scopes or independent acceptance conclusions,
and do not authorize subagents, model/provider switches, real data, push, deployment, migration or other
external operations. A challenger's silence about a fix approach, or its receipt of `AUDIT_RECEPTION`,
never counts as approval of the fix.

## Local authoritative records

Messages never get a second copy of state. The joint development lifecycle stays as it is:

1. at activation, `STATUS / ASSIGNMENTS / INPUTS / FIELD_MATRIX / RESOLUTIONS / OPEN / TEST_PLAN` freeze
   the boundary;
2. during development only cross-layer semantics, substantive findings others will depend on, and
   blockers go into the contract or the contributor's recovery record;
3. a contributor that finishes its full to-do list makes a local commit and hands over the exact SHA,
   changed paths, tests/exit codes, samples and open items;
4. the integration owner registers the candidate in `INPUTS.md`, receives candidates serially and runs
   the gates;
5. `READY_TO_CLOSE` / `MERGED` / `PAUSED` still freeze HEAD, tests, open items or a WIP commit as WORKFLOW
   describes.

Small internal implementation steps do not change the contract, and sending a progress message does not
force a commit.

A two-session local audit keeps no second state either: the brief, raw report and item-by-item response
are each written to disk first, and the direct message carries only their path, SHA-256, exact commit and
the next action. The user's decision after the third step is saved according to WORKFLOW's "Single home
for each kind of content".

## Local addressing

Exact session IDs, client cursors and other runtime state are written only to:

```text
.ai-workflow/runtime/joint-messaging/<collaboration_id>.json
```

That directory is git-ignored. Versioned contracts, STATE, task recovery documents and commits never
store raw session IDs. The registry contains at least a schema version, collaboration_id, state,
participants' actor_id/session_id/role summary, declared links and the probe state of each link. Display
names are for humans only; actor_ids are assigned by the user for the current task, not permanent roles
defined here.

These registry rules belong to joint development. A short two-session audit does not write session IDs
into versioned documents; the exact IDs appear only in the task context the user gave and in the three
direct messages, and are not used as long-lived routes once the loop ends.

## Sending and failure handling

1. Before sending, confirm that the joint protocol or local audit is still active and that the message
   does not widen permissions; for a joint protocol also confirm the declared link passed its probe.
2. For joint development, resolve `recipient_actor_id` to the unique session ID in the current
   collaboration registry; for a local audit use the exact IDs the user specified in `AUDIT_REQUEST`.
   Never guess from display names, recent contacts, the last sender or an inbound source ID; the latter is
   evidence of origin only, not a default reply address.
3. Call the client's direct-message tool once; the target must equal the resolved ID, and do not try to
   control internal turn scheduling. If the tool's receipt contains a target ID, it must match.
4. After a successful delivery continue your own work; do not poll, nudge or re-send automatically.
5. When the mapping/target is missing or duplicated, a joint link is unverified, delivery fails or the
   result is unknown, the target does not exist or is archived, or the receipt does not match, keep the
   facts already written to disk and report to the user. Direct messaging fails closed; a one-way relay
   is not a successful automated loop.
6. Do not guess targets, create substitute sessions, or repeat model calls or widen authorization because
   "the message did not arrive".
