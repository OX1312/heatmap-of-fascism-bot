#!/usr/bin/env python3
# Heatmap of Fascism - Minimal Ingest + Review via FAV
# - Fetch hashtag posts
# - Validate: image + (coords OR address OR crossing-with-city)
# - Geocode via Nominatim (cached)
# - Store all valid as PENDING in pending.json
# - If favourited by allowed reviewer -> publish to reports.geojson

import json
import re
import time
import pathlib
from typing import Optional, Tuple, Dict, Any, List

import requests

# =========================
# FILES
# =========================
ROOT = pathlib.Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"
CACHE_PATH = ROOT / "cache_geocode.json"
PENDING_PATH = ROOT / "pending.json"
REPORTS_PATH = ROOT / "reports.geojson"


# =========================
# PARSING (MINIMAL RULES)
# =========================
RE_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")  # Street + number, City
RE_CROSS = re.compile(r"^(.+?)\s*(?:/| x | & )\s*(.+?)\s*,\s*(.+)$", re.IGNORECASE)  # A / B, City


def load_json(path: pathlib.Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: pathlib.Path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_reports_file():
    if REPORTS_PATH.exists():
        return
    save_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})


def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def has_image(attachments: List[Dict[str, Any]]) -> bool:
    for a in attachments or []:
        if a.get("type") == "image" and a.get("url"):
            return True
    return False


def parse_location(text: str) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """
    Returns:
      (lat, lon) if coords found
      OR geocode query string if address/crossing found
      OR (None, None) if invalid
    """
    m = RE_COORDS.search(text)
    if m:
        lat = float(m.group(1))
        lon = float(m.group(2))
        return (lat, lon), None

    # first non-hashtag, non-empty line as candidate
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidate = None
    for ln in lines:
        if ln.lower().startswith("#"):
            continue
        candidate = ln
        break
    if not candidate:
        return None, None

    m = RE_ADDRESS.match(candidate)
    if m:
        street = m.group(1).strip()
        number = m.group(2).strip()
        city = m.group(3).strip()
        return None, f"{street} {number}, {city}"

    m = RE_CROSS.match(candidate)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        city = m.group(3).strip()
        return None, f"{a} & {b}, {city}"

    return None, None


def geocode_nominatim(query: str, user_agent: str) -> Optional[Tuple[float, float]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": user_agent}
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    return (lat, lon)


def get_hashtag_timeline(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    instance = cfg["instance_url"].rstrip("/")
    tag = cfg["hashtag"].lstrip("#")
    url = f"{instance}/api/v1/timelines/tag/{tag}"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def get_favourited_by(cfg: Dict[str, Any], status_id: str) -> List[Dict[str, Any]]:
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/statuses/{status_id}/favourited_by"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def is_approved_by_fav(cfg: Dict[str, Any], status_id: str) -> bool:
    allowed = set(a.lower() for a in cfg.get("allowed_reviewers", []))
    if not allowed:
        return False
    fav_accounts = get_favourited_by(cfg, status_id)
    for acc in fav_accounts:
        # acct may contain "name" or "name@instance"
        acct = (acc.get("acct") or "").split("@")[0].lower()
        username = (acc.get("username") or "").lower()
        if acct in allowed or username in allowed:
            return True
    return False


def make_feature(item: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    # GeoJSON requires [lon, lat]
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [item["lon"], item["lat"]]},
        "properties": {
            "id": item["id"],
            "date": (item.get("created_at") or "")[:10],
            "source": item["source"],
            "notes": item.get("notes") or "",
            "accuracy_m": int(cfg.get("accuracy_m", 25)),
            "media": item.get("media", []),
        },
    }


def main():
    cfg = load_json(CFG_PATH, None)
    if not cfg:
        raise SystemExit("Missing config.json")

    cache: Dict[str, Any] = load_json(CACHE_PATH, {})
    pending: List[Dict[str, Any]] = load_json(PENDING_PATH, [])
    ensure_reports_file()
    reports = load_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})
    reports_ids = set((f.get("properties") or {}).get("id") for f in reports.get("features", []))

    # Dedupe pending by source URL
    pending_by_source = {p.get("source"): p for p in pending if p.get("source")}
    statuses = get_hashtag_timeline(cfg)

    added_pending = 0
    published = 0

    # ---- ingest new to pending ----
    for st in statuses:
        status_id = st.get("id")
        url = st.get("url")
        if not status_id or not url:
            continue
        if url in pending_by_source:
            continue

        text = strip_html(st.get("content", ""))
        attachments = st.get("media_attachments", [])
        if not has_image(attachments):
            continue  # image required

        coords, q = parse_location(text)
        if not coords and not q:
            continue  # location required

        if not coords and q:
            if q in cache:
                coords = (cache[q]["lat"], cache[q]["lon"])
            else:
                coords2 = geocode_nominatim(q, cfg["user_agent"])
                time.sleep(1.0)  # be polite
                if not coords2:
                    continue
                coords = coords2
                cache[q] = {"lat": coords[0], "lon": coords[1], "ts": int(time.time())}

        lat, lon = coords
        item = {
            "id": f"masto-{status_id}",
            "status_id": status_id,
            "status": "PENDING",
            "source": url,
            "created_at": st.get("created_at"),
            "notes": "",
            "lat": lat,
            "lon": lon,
            "location_query": q,  # None if coords used
            "media": [a.get("url") for a in attachments if a.get("type") == "image" and a.get("url")],
        }
        pending.append(item)
        pending_by_source[url] = item
        added_pending += 1

    # ---- publish approved (fav by allowed reviewer) ----
    still_pending: List[Dict[str, Any]] = []
    for item in pending:
        if item.get("status") != "PENDING":
            continue

        item_id = item.get("id")
        if item_id in reports_ids:
            # already published previously
            continue

        ok = is_approved_by_fav(cfg, item["status_id"])
        if ok:
            feat = make_feature(item, cfg)
            reports["features"].append(feat)
            reports_ids.add(item_id)
            published += 1
        else:
            still_pending.append(item)

        # be polite to API
        time.sleep(0.4)

    # overwrite pending with only those not yet approved
    pending = still_pending

    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, pending)
    save_json(REPORTS_PATH, reports)

    print(f"Added pending: {added_pending} | Published: {published} | Pending left: {len(pending)}")


if __name__ == "__main__":
    main()
