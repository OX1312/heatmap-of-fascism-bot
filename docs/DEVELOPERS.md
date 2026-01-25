# Developers

This repository contains a single-file Mastodon bot that turns explicit user reports into a public GeoJSON dataset.

## What is tracked on GitHub
- `bot.py` — main bot logic
- `config.json` — non-secret configuration (no tokens)
- `reports.geojson` — public dataset (map source)
- `requirements.txt` — dependencies
- `docs/` — public documentation

## What is NOT tracked (local only)
- `secrets/` — tokens and other sensitive runtime data
- `logs/`, `errors/`, runtime caches/state files

## Local development (safe summary)
1) Create a virtual environment and install dependencies.
2) Provide required configuration in `config.json` (no secrets).
3) Provide secrets via local files (never commit them).
4) Run the bot in one-shot mode for testing.

> Operational details (deployment, automation, internal moderation tooling) are intentionally not documented publicly.
