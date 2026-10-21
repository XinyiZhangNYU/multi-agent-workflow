# DECISIONS

Append-only log of approved cross-task workflow decisions and rejected alternatives. Existing entries
are never edited in place. Product decisions do not belong here (see WORKFLOW "Single home for each kind
of content"). New sessions do not read this file in full by default.

Entry template:

```text
## <YYYY-MM-DDTHH:MM±hh:mm> · <short title>

- approval source: <the user's own words or explicit confirmation, with where they appear>
- decision: <the rule now in force>
- supersedes: <earlier entry or rule text, or "none">
- reason: <why>
- rejected alternatives: <option — reason>
```

## 2026-01-01T00:00-05:00 · Adopt the explicit-authorization workflow (example entry)

- approval source: user, initial setup of this repository.
- decision: WORKFLOW.md v1 is the single authority for current rules; work starts only on explicit
  user requests.
- supersedes: none.
- reason: explicit per-request authorization plus event-driven checkpoints gives recoverable work
  without a heavyweight role/freeze platform.
- rejected alternatives: a strict state machine with fixed reviewer seats — high fixed cost, little
  downstream benefit for small teams.
