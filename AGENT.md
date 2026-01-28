# Agent Instructions (Heatmap of Fascism)

This file is the entrypoint for automated code agents.
If anything conflicts, `docs/ARCHITECTURE.md` wins.

## Non-negotiable rules
- Separation of concerns over convenience.
- Domain logic is pure: no IO, no API calls, no logging.
- Delete logic is isolated and pausable.
- Logs reflect execution time, never object creation time.
- `ox` CLI contains zero business logic (launcher only).
- Never commit secrets or runtime state.

## Canonical spec
Read and follow:
- `docs/ARCHITECTURE.md`

## Public-doc safety
- Do not add internal ops flows or security details to public docs.
- Do not print tokens, secrets, headers, or private identifiers in logs.

## Allowed module boundaries (summary)
- `hm/core` may import: `hm/adapters`, `hm/domain`, `hm/utils`
- `hm/domain` may import: `hm/core/models`, `hm/core/constants` (pure only)
- `hm/adapters` may import: `hm/utils` (transport only)
- `hm/support` may import: `hm/adapters`, `hm/utils`, `hm/support/state`, `hm/support/audit`

## Absolute “never do” list
- No guessing entity meanings.
- No auto-overwriting curated `entities.json` fields.
- No using `created_at` timestamps for monitoring.
- No business logic inside `ox`.
- No mixing delete logs with support replies logs.

## Work style (required)
- Small, safe steps.
- Prefer adding new modules over patching large blocks in `bot.py`.
- Keep changes minimal and testable.
