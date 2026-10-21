# OPEN

Unresolved and deferred risks. Every O-ID is permanent; a recurrence of the same kind reopens the
original ID. OPEN, DEFERRED and CLOSED entries are kept apart. Product facts are referenced from
project authority, never copied here.

## OPEN

### O-001 · Outbound material has no automated secret scan

- status: OPEN
- opened: 2026-01-01T00:00-05:00 (example entry)
- risk: pushes, pull requests and release packages rely on a manual review for secrets, licensing and
  data boundaries; nothing blocks a mistaken push automatically.
- current boundary: every push/release needs explicit user authorization (WORKFLOW section 2).
- next step: decide whether a pre-push scanner is worth adding; until then keep the manual check.

## DEFERRED

_None._

## CLOSED

_None._
