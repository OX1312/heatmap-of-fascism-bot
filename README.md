## Repository structure (what is what)

**Tracked (GitHub):**
- `bot.py` – main bot
- `config.json` – non-secret runtime config (NO tokens)
- `reports.geojson` – public dataset for the map
- `requirements.txt` – Python deps
- `ox` – helper commands (start/stop/status)

**NOT tracked (local runtime, gitignored):**
- Local secrets/runtime data (not tracked)
- `logs/`, `errors/` – runtime logs
- `_backup/` – local backups
- `pending.json`, `timeline_state.json`, `cache_geocode.json` – runtime state/cache

### Safety rules
- Never commit secrets or private operational data.
- Keep internal ops/private workflows out of public docs.

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
- Messenger inbound (Telegram/Signal): photo + hashtag + location, optional “Chat = City” default
- Multi-category reports beyond stickers (e.g., graffiti) with filters in the map/UI

### Mid-term
- Reliability hardening: attachment handling, image-hash dedupe, spam throttling
- Review workflow improvements (NEEDS_INFO loop, manager tooling)
- Optional: per-city/region moderation teams (trust tiers)

More: `docs/ROADMAP.md`


## Developer setup
See `docs/DEVELOPERS.md`
    ## Major update and roadmap (Version 1.0)

    The upcoming v1.0 release addresses data consistency, workflow automation and expansion beyond stickers. A detailed plan is documented in `docs/major_update_roadmap.md`.

    Key points:
    - **Alias handling**: synonyms and typos map to a single category.
    - **Data check**: run `python tools/check_data.py` to detect missing fields, invalid coordinates and duplicates.
    - **Data fix**: run `python tools/fix_data.py` to fill missing descriptions, remove invalid entries and merge duplicates. The original file is backed up automatically.
    - **Extended schema**: support for graffiti and multi-category features.
    - **Moderation tooling**: introduces a pending-review dashboard (CLI or web) for efficient validation of reports.
    - **CI/CD**: automated tests and data validation on each commit.

    See the roadmap document for more details.

## Version 1.0 (major update)

This release focuses on **data consistency**, **workflow automation**, and **multi-category support** (stickers + graffiti).

Key points:
- **Safe alias handling**: only spelling variants map to the *same* entity (never merge different organizations/parties).
- **Data check**: `python3 tools/check_data.py` validates required fields, coordinates, duplicates.
- **Data fix (deterministic only)**: `python3 tools/fix_data.py` normalizes formats and fills missing report fields from `entities.json` (never invents meanings).
- **Extended schema**: supports graffiti and multi-category features.
- **Moderation tooling**: pending-review workflow (CLI/web) for efficient validation.
- **CI/CD**: automated tests and data validation on each commit.
- **No self-modifying loops by default**: background entity enrichment is disabled to avoid unintended changes.

### Sources database (curated)
We maintain a curated sources list in:
- `docs/sources.json`

Rules:
- `entities.json` is the **single source of truth** for `display` and `desc`.
- Automated tooling must **never overwrite** curated `display/desc`.
- If enrichment is enabled later, it may only write to separate *auto* fields (e.g. `desc_en_auto`) or set `needs_desc=true`.

See also:
- `docs/DEVELOPERS.md`
- `docs/major_update_roadmap.md`


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

