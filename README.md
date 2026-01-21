# Heatmap of Fascism

**Heatmap of Fascism documents fascist sticker propaganda in public space.**

The project collects *verified, location-based reports* via Mastodon and maps them worldwide  
to make **hotspots, persistence, and removals** visible over time.

⚠️ This is **not passive scraping**.  
Only **explicitly submitted reports** are processed.

---

## How to submit a report (Mastodon) — REQUIRED

A report is processed **only** if it contains **all** of the following:

1) **One photo** (sticker image)
2) **One location** (choose exactly one):
   - **Coordinates:** `lat, lon`
   - **Street + city:** `Street 12, City`
   - **Crossing + city:** `StreetA / StreetB, City`
3) **@HeatmapOfFascism mention**  
   (in the post **or** in a reply)

Reports **without an @mention** may be ignored to avoid ambiguity and noise.

### Optional (recommended)
- **Date**
- **Sticker type:** `#sticker_type:<text>`  
  (e.g. party / symbol / slogan)
- Plain-text notes

---

## Hashtags

- **Everyone:** `#sticker_report` — sticker present
- **Members only:** `#sticker_removed` — confirmed removal

---

## Review & anti-spam policy

- No report appears on the map without **manual review**.
- A report becomes public only after a **Like/Favourite** by:
  - the project account, or
  - a reviewer on the allowlist.

This ensures:
- no silent scraping
- no drive-by spam
- clear user intent

---

## Processing rules (technical)

- Reports without **photo + location + @mention** are ignored.
- All locations are normalized to coordinates.
- Estimated positional accuracy is **10–50 m**.
- Repeated reports update the **same spot** (no duplicates).

Each spot stores:
- `first_seen`
- `last_seen`
- `seen_count`
- `status`: `present` | `removed` | `stale`

### Status logic

- **present** → orange / red  
- **removed** → green  
- **stale** → gray (no confirmation for **30 days**)

If a spot marked `present` is not re-confirmed for **30 days**, it becomes `stale`.

---

## Accuracy → map circle size

Circle radius visualizes **location uncertainty**:

- **10–15 m** — explicit GPS coordinates
- **~25 m** — street or crossing
- **up to 50 m** — low-confidence / vague location

---

## Map output

- **Single source of truth:** `reports.geojson`
- OpenStreetMap / uMap visualization:
  - points with uncertainty circles
  - heatmap of active locations
  - temporal status changes

---

## Roadmap

**Short-term**
- Enforce @mention as explicit intent marker
- Improve geocoding confidence scoring
- Clear user feedback on rejected reports

**Mid-term**
- Trust levels for reporters (A/B/C)
- Better duplicate & proximity detection
- Public statistics (removals vs persistence)

**Long-term**
- Federated moderation model
- Read-only public API
- Historical timelines per location

---

## Local setup (developers)

This project requires a local `secrets.json`  
(**never commit this file**):

```json
{
  "access_token": "<your Mastodon access token>"
}
