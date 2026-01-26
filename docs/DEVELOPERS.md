# Developers

This repository contains a single-file Mastodon bot that turns explicit user reports into a public GeoJSON dataset.

## What is tracked on GitHub
- `bot.py` — main bot logic
- `config.json` — non-secret configuration (no tokens)
- `reports.geojson` — public dataset (map source)
- `requirements.txt` — dependencies
- `docs/` — public documentation

## What is NOT tracked (local only)
- Secrets are stored locally and are not tracked.
- `logs/`, `errors/`, runtime caches/state files

## Local development (safe summary)
1) Create a virtual environment and install dependencies.
2) Provide required configuration in `config.json` (no secrets).
3) Provide secrets via local files (never commit them).
4) Run the bot in one-shot mode for testing.

> Operational details (deployment, automation, internal moderation tooling) are intentionally not documented publicly.


## Entity Policy (v1.0.1): Verify or Unknown

**Hard rule:** The bot must never guess meanings for new codes/names/numbers.

- Only entities explicitly curated in `entities.json` are shown with a real name/description.
- Unknown or unclear inputs remain:

  - `entity_display = "Unknown"`
  - `entity_desc = "Unknown (needs verification)"`
  - `needs_verification = true`

- New entities are added **only after verification** using trusted references in `docs/sources.json`.
- Automated enrichment must never overwrite curated `display/desc`.

This prevents incorrect merges (e.g. numeric codes vs. organizations).

