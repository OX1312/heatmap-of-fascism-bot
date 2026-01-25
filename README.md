# README.md

## Repository structure (what is what)

**Tracked (GitHub):**
- `bot.py` – main bot
- `config.json` – non-secret runtime config (NO tokens)
- `reports.geojson` – public dataset for the map
- `requirements.txt` – Python deps
- `ox` – helper commands (start/stop/status)

**NOT tracked (local runtime, gitignored):**
- `secrets/` – tokens + manager DM message/state + trusted/blacklist lists
- `logs/`, `errors/` – runtime logs
- `_backup/` – local backups
- `pending.json`, `timeline_state.json`, `cache_geocode.json` – runtime state/cache

### Safety rules
- **Never commit tokens**. Tokens live only in `secrets/secrets.json`.
- Manager update texts live in `secrets/manager_update_message.txt` (private).
- Runtime state is **always gitignored**.

# Heatmap of Fascism (BETA)

Heatmap of Fascism documents **fascist sticker propaganda in public space** via **explicit user reports** on Mastodon.
Reports are **manually reviewed** and then shown on a public map to visualize **hotspots, persistence, and removals**.

**No passive scraping.** Only posts that intentionally report to the project are processed.

## Links
- Map:         https://umap.openstreetmap.de/de/map/heatmap_121255#6/52.194/11.646
- Mastodon:    https://mastodon.social/@HeatmapofFascism
- Repo:        https://github.com/OX1312/heatmap-of-fascism-bot

## Report a sticker (Mastodon) — minimum requirements
Your post must include:

1) **1 photo**
2) **1 location** (exactly one)
   - `lat, lon` (best), or
   - `Street, City` (optional ~house number)
3) **@HeatmapofFascism** mention (in the post or in a reply)

If **location is missing** (or too vague), the bot replies publicly and marks the report as **NEEDS_INFO**.

## Safety + legality (important)
- If the photo contains **illegal / unconstitutional extremist symbols**, you must **blur / censor them before posting**.
  Uncensored illegal symbols → report is rejected.
- Don’t post private data (faces, license plates, addresses of private homes, etc.). Blur if needed.

## Review model (anti-spam)
Nothing appears on the public map without **manual review**.
During beta, moderation rules may evolve; see `docs/MODERATION.md`.

## Data + output (high-level)
- Single source of truth: `reports.geojson`
- Locations are normalized to coordinates with **~10–50 m** stored uncertainty (rounding/jitter).
- Bot feedback is posted as a **public reply** under the report by default.

## Roadmap (short)
- Better reporter feedback (clear rejection reasons + fix hints)
- Stronger duplicate/proximity matching
- Trust levels for reporters + distributed review
- Add Telegram group integration as an additional inbound reporting channel (photo + `#sticker_report` + location)
- Add Signal group integration as an additional inbound reporting channel (photo + `#sticker_report` + location; inbound-only in beta due to unofficial tooling)
- Support “Chat = City” mapping for messenger groups (per-group fixed city; allow street/crossing only)

  ### Mid-term
- Add **Signal group integration** as an additional *input channel* (same rules as Telegram).
- Reliability hardening:
  - Attachment handling + dedupe (image hash)
  - Abuse/spam throttling per sender/chat
  - Structured “NEEDS_INFO” feedback workflow

### Notes / constraints
- Signal integration is **unofficial tooling** (higher maintenance risk); keep it **inbound-only** and low-volume in beta.

More: `docs/ROADMAP.md`

## Developer setup
See `docs/DEVELOPERS.md`
