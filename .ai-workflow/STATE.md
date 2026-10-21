# STATE

Read this file first, then follow README. The current direction and project roadmap are not
authorization to execute anything.

~~~yaml
workflow_version: v1
project_configuration: projects/example/README.md
joint_integration: none
joint_messaging: manual
~~~

## Now

- Work happens only on the user's explicit request. The user assigns tasks and roles; one agent may
  implement and self-test within an authorized scope, and independent audits follow the contract of each
  request.
- No joint protocol is active.
- The single home for product, data and implementation progress is the product status document named in
  the project configuration; this file does not track product gates or research numbers.

## Maintained capabilities

- Optional local progress board: enabled per project, with a shared CLI and HTTP short-report API; entry
  point runbooks/AGENT-BOARD.md. At start/resume, check the project configuration first; multi-stage or
  handoff tasks in an enabled project join it as the manual describes, identically for every client. It
  is an observation index only: it never writes back to product authority, approves anything or
  dispatches work.
- Startup contract: configuration-driven sync and byte check (bin/sync-startup-contract.ps1).
- On-demand task board: separate from progress reporting, read only when the user explicitly enables it
  for the current task or when resuming the same enabled task. Entry points are the WORKFLOW section
  "On-demand task board" and runbooks/TASK-BOARD.md. Mainlines stay frozen; finished branches may be
  removed after their results are folded in. A task plan grants no product, audit or execution
  authorization.
- Direct messaging between agent sessions: for confirmed links of an active joint protocol, or for a
  user-named two-session lightweight audit (runbooks/AGENT-MESSAGING.md). A joint protocol may have many
  contributors but one integration owner and declares only the directed links it needs.
- Chat-based audits through a repository connector: the user picks the chat, model and effort and sends
  the prompt; results come back to the responsible agent by hand.
- New outbound material and remote operations stay under manual authorization (see OPEN O-001).

## Open items and recovery boundaries

- Active and deferred risks are in OPEN.
- runtime/checkpoints/pending is a local recovery index; whoever takes over must inspect it rather than
  infer from this file that it is empty.

## Next (not authorization)

Follow the user's current request.
