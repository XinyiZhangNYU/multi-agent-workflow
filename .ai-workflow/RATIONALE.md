# Why the workflow looks the way it does

Read on demand. WORKFLOW.md is the rule; DECISIONS.md holds approval sources. This file keeps only the
reasons that still shape current choices.

- **User authorization and real results are the main line.** Explicit per-request authorization is
  enough; a strict platform of roles, freezes and qualification runs has a high fixed cost and little
  downstream benefit.
- **Independence comes from not having helped make the artifact and from a clear access scope, not from
  a vendor label.** Self-testing by the implementer is valuable but never passes as an independent
  audit.
- **Separate business risk from governance ritual.** A high-risk task needs suitable evidence; it does
  not automatically need another state machine, several model round trips or a long pending state.
- **Local commits make stage results and context recovery reproducible.** Event-triggered checkpoints
  match delivery boundaries better than timers or file counts. A commit is not authorization,
  verification or a push, so each gets its own receipt.
- **Where something is written depends on what kind of content it is, not how important it is.**
  Product architecture belongs in project design; the workflow decision log records only cross-task
  ways of working.
- **A single writer per worktree prevents concurrent overwrites.** Contributors use separate branches,
  and the initiator, who already has the context, doubles as mechanical integrator; no third seat
  without context is needed.
- **A joint protocol can hold many contributors but keeps one integration owner.** Communication is
  organized as the directed links that are actually needed, so adding participants does not create a
  pointless full mesh of probes and messages. Providing inputs or acceptance criteria does not occupy a
  contributor seat.
- **Contradictions found while exploring are valuable; once the rules are clear, local fixes should
  converge under a continuing owner** instead of bouncing the whole package back repeatedly.
- **Do not ship source code to an auditor who can read it directly.** The brief names the problem, the
  baseline, entry points along the whole chain and what cannot be seen; the method and goal are open to
  challenge too.
- **Two agent sessions can finish a lightweight audit with three direct messages.** Model names create
  no seats, and the automatic round trip ends when the responsible agent submits its response, so the
  challenger never becomes an approver of fixes and the audit never turns into an endless dialogue.
- **Independent green results do not stack when remediations share time, identity, schema, migration
  or test invariants.** A rough check before work and an exact diff after candidates exist, escalating
  to one existing owner only on a real collision, costs less than starting a full joint protocol by
  default.
- **History can be recoverable and day-to-day rules readable at the same time.** Immutable originals
  stay in fixed Git snapshots, active rules can be rewritten, and cases keep permanent IDs. Hashes do not
  prove semantic correctness, and Git does not back up untracked raw material.
- **General rules, project configuration and platform adapters are layered.** The quality bar is that
  the rules carry over to an unrelated project unchanged; everything project-specific lives under
  projects/.
