#!/usr/bin/env python3
# Heatmap of Fascism - Minimal Ingest + Review via FAV (multi-hashtag)
#
# Input:
# - Fetch posts from multiple hashtags defined in config.json: cfg["hashtags"] = {tag: "present"/"removed"}
# - Validate: image + (coords OR address OR crossing-with-city)
# - Geocode via Nominatim (cached)
# - Store as PENDING in pending.json
# - If favourited by allowed reviewer -> publish to reports.geojson
#
# Notes:
# - Hashtag names must be mastodon-safe (use underscore, not dots).
# - cache_geocode.json must be valid JSON object: {}
# - pending.json must be valid JSON array: []

import json
import re
import time
import pathlib
from typing import Optional, Tuple, Dict, Any, List, Iterable

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


# =========================
# IO HELPERS
# =========================
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


# =========================
# TEXT / MEDIA
# =========================
def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()

def normalize_query(q: str) -> str:
    q = q.replace("ß", "ss")
    q = q.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    q = q.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    return q

def has_image(attachments: List[Dict[str, Any]]) -> bool:
    for a in attachments or []:
        if a.get("type") == "image" and a.get("url"):
            return True
    return False


# =========================
# LOCATION PARSE
# =========================
def parse_location(text: str) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """
    Returns:
      (lat, lon) if coords found anywhere in text
      OR a geocode query string if address/crossing found in first non-hashtag line
      OR (None, None) if invalid
    """
    # 1) coords anywhere in text
    m = RE_COORDS.search(text)
    if m:
        lat = float(m.group(1))
        lon = float(m.group(2))
        return (lat, lon), None

    # 2) pick first non-hashtag line as location candidate
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidate = None
    for ln in lines:
        if ln.lower().startswith("#"):
            continue
        candidate = ln
        break
    if not candidate:
        return None, None

    # address: "Street 12, City"
    m = RE_ADDRESS.match(candidate)
    if m:
        street = m.group(1).strip()
        number = m.group(2).strip()
        city = m.group(3).strip()
        return None, f"{street} {number}, {city}"

    # crossing: "Street A / Street B, City"
    m = RE_CROSS.match(candidate)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        city = m.group(3).strip()
        return None, f"{a} & {b}, {city}"

    return None, None


# =========================
# OSM GEOCODING
# =========================
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


# =========================
# MASTODON API
# =========================
def get_hashtag_timeline(cfg: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    instance = cfg["instance_url"].rstrip("/")
    tag = tag.lstrip("#")
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
        acct = (acc.get("acct") or "").split("@")[0].lower()
        username = (acc.get("username") or "").lower()
        if acct in allowed or username in allowed:
            return True
    return False


# =========================
# GEOJSON
# =========================
def make_feature(item: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    # GeoJSON requires [lon, lat]
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [item["lon"], item["lat"]]},
        "properties": {
            "id": item["id"],
            "date": (item.get("created_at") or "")[:10],
            "source": item["source"],
            "status": item.get("event", "present"),
            "notes": item.get("notes") or "",
            "accuracy_m": int(cfg.get("accuracy_m", 25)),
            "media": item.get("media", []),
            "tag": item.get("tag") or "",
        },
    }


# =========================
# MAIN
# =========================
def iter_statuses(cfg: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    """
    Yields tuples: (tag, event, status_dict)
    event is "present" or "removed" (or any string you define in config)
    """
    tags_map = cfg.get("hashtags") or {}
    if not isinstance(tags_map, dict) or not tags_map:
        # fallback (keeps bot usable if config is incomplete)
        tags_map = {"sticker_report": "present"}

    for tag, event in tags_map.items():
        for st in get_hashtag_timeline(cfg, tag):
            yield tag, event, st


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

    added_pending = 0
    published = 0

    # ---- ingest new to pending (from multiple tags) ----
    for tag, event, st in iter_statuses(cfg):
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
                q_norm = normalize_query(q)
                coords2 = geocode_nominatim(q_norm, cfg["user_agent"])
                time.sleep(1.0)  # be polite to nominatim
                if not coords2:
                    continue
                coords = coords2
                cache[q] = {"lat": coords[0], "lon": coords[1], "ts": int(time.time())}

        lat, lon = coords
        item = {
            "id": f"masto-{status_id}",
            "status_id": status_id,
            "status": "PENDING",
            "event": event,   # "present" or "removed"
            "tag": tag,       # which hashtag triggered this ingest
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

        # be polite to API a bit (tag timelines can be bursty)
        time.sleep(0.2)

    # ---- publish approved (fav by allowed reviewer) ----
    still_pending: List[Dict[str, Any]] = []
    for item in pending:
        if item.get("status") != "PENDING":
            continue

        item_id = item.get("id")
        if item_id in reports_ids:
            continue  # already published previously

        ok = is_approved_by_fav(cfg, item["status_id"])
        if ok:
            feat = make_feature(item, cfg)
            reports["features"].append(feat)
            reports_ids.add(item_id)
            published += 1
        else:
            still_pending.append(item)

        time.sleep(0.4)  # be polite to API

    pending = still_pending

    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, pending)
    save_json(REPORTS_PATH, reports)

    print(f"Added pending: {added_pending} | Published: {published} | Pending left: {len(pending)}")


if __name__ == "__main__":
    main()
