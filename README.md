# Heatmap of Fascism

Heatmap of Fascism documents fascist sticker propaganda in public space.
Reports are submitted via Mastodon, reviewed, and mapped worldwide to reveal hotspots, persistence, and removals over time.

## Submit a report (Mastodon)

A report is processed only if it contains:

1) **One photo** (sticker image)
2) **One location** (choose one):
   - **Coordinates:** `lat, lon`
   - **Street + city:** `Street 12, City`
   - **Crossing + city:** `StreetA / StreetB, City`

Optional (recommended):
- **Sticker type:** `#sticker_type: <text>` (e.g. party / symbol / slogan)
- Notes in plain text

## Hashtags

- **Everyone:** `#sticker_report`  (sticker present)
- **Confirmed removal:** `#sticker_removed` (sticker removed)

Only **reviewed** reports appear on the public map.

## Review (anti-spam)

A report becomes public only after a **Like/Favourite** by:
- the project account, or
- a reviewer in the allowlist.

## Processing rules

- Reports without **photo + location** are ignored.
- All locations are normalized to coordinates with an estimated accuracy **10–50 m**.
- Repeated reports update the **same spot** (no duplicate spam).

Each spot stores:
- `first_seen`, `last_seen`, `seen_count`
- `status`: `present` | `removed` | `stale`

### Status logic (map colors)

- **present** → **orange/red**
- **removed** → **green**
- **stale** → **gray** (no confirmation for **30 days**)

`last_seen` updates on every reviewed report.
If a spot is `present` and not re-confirmed for **30 days**, it becomes `stale`.

### Accuracy → circle size

Circle radius visualizes **location uncertainty**:
- **10–15 m**: explicit coordinates (GPS)
- **~25 m**: street/crossing geocoded
- **up to 50 m**: vague/low-confidence location

## Map output

- Public GeoJSON (`reports.geojson`) as single source of truth
- OpenStreetMap / uMap visualization:
  - points (with uncertainty circles)
  - heatmap of active locations
  - status over time

## Local secrets (required)

This project requires a local `secrets.json` (NOT committed):

```json
{
  "access_token": "<your Mastodon access token>"
}
