# Heatmap of Fascism (BETA)

Heatmap of Fascism documents **fascist propaganda in public space** (currently: stickers, soon graffiti)
via **explicit user reports** on Mastodon.

The project visualizes **where**, **how often**, and **how persistently** such material appears — and when it is removed.

**No scraping. No surveillance. No automation without intent.**  
Only posts that explicitly report to the project are processed.

---

## Links
- 🗺️ Map: https://umap.openstreetmap.de/de/map/heatmap_121255#6/52.194/11.646  
- 🐘 Mastodon: https://mastodon.social/@HeatmapofFascism  
- 💻 Repository: https://github.com/OX1312/heatmap-of-fascism-bot  

---

## How reporting works (Mastodon)

A valid report must include:

1) **Exactly one photo**
2) **Exactly one location**
   - Coordinates (`lat, lon`) — preferred  
   - or `Street, City` (optional ~house number)
3) **Mention `@HeatmapofFascism`**
   - in the post **or** in a reply

If the location is missing or too vague, the bot replies publicly and marks the report as **NEEDS_INFO**.

Nothing appears on the map without **manual review**.

---

## Safety & legality (mandatory)

- If an image contains **illegal / unconstitutional extremist symbols**,  
  **you must blur or censor them before posting**.
- Uncensored illegal symbols → **automatic rejection**.
- Do not publish private data:
  faces, license plates, private homes, or personal identifiers.
  Blur when in doubt.

---

## Review & moderation (anti-spam)

- All reports are **manually reviewed**.
- During beta, moderation rules may evolve.
- See: `docs/MODERATION.md`

---

## Data model (high-level)

- **Single source of truth:** `reports.geojson`
- Locations are stored with **~10–50 m uncertainty**
  (rounding / jitter for safety).
- Bot feedback is posted as a **public reply** by default.

Supported report kinds:
- `sticker` (current)
- `graffiti` (supported by schema, rolling out)

Each report has exactly **one kind** and one **free-form category**.

---

## Repository structure (overview)

**Tracked (GitHub):**
- `bot.py` – main application entry
- `config.json` – runtime config (no secrets)
- `reports.geojson` – public dataset
- `requirements.txt` – dependencies
- `ox` – helper CLI (no business logic)

**Not tracked (runtime, local only):**
- `logs/`, `errors/`
- `_backup/`
- `pending.json`, `timeline_state.json`, `cache_geocode.json`
- `support/` runtime state files

Rules:
- Secrets are never committed.
- Runtime files are never imported as logic.
- Internal ops stay out of public docs.

---

## Architecture & internals

This repository follows a strict separation of concerns:
- pure domain logic
- isolated adapters
- pausable delete operations
- explicit, time-correct logging

**Canonical architecture specification:**
→ `docs/ARCHITECTURE.md`

If documentation conflicts:
`ARCHITECTURE.md` always wins over README.

---

## Roadmap (short)

- Clearer reporter feedback and fix hints
- Better duplicate & proximity detection
- Trust levels for reporters and reviewers
- Multi-category map filters (stickers + graffiti)
- Optional messenger ingestion (Telegram / Signal)

Details:
→ `docs/ROADMAP.md`

---

## Developer setup

See:
→ `docs/DEVELOPERS.md`

---

## Project status

This project is in **active beta**.
Stability, correctness, and auditability take priority over speed.

Heatmap of Fascism is built to be **slow, careful, and correct** —
not fast and wrong.
