#!/usr/bin/env python3
# Heatmap of Fascism - Minimal Ingest + Review via FAV (multi-hashtag, worldwide)
#
# - Fetch posts from multiple hashtags (cfg["hashtags"])
# - Validate: image + (coords OR address OR crossing-with-city)
# - Geocode:
#     * Crossings -> Overpass intersection (≈10 m)
#     * Fallbacks -> Nominatim intersection / street (≈25–50 m)
# - Cache geocodes
# - Store as PENDING in pending.json
# - If favourited by allowed reviewer -> publish to reports.geojson

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
    if not REPORTS_PATH.exists():
        save_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})

# =========================
# TEXT / MEDIA
# =========================
def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()

def normalize_query(q: str) -> str:
    # mild normalization; keep worldwide neutrality
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
    m = RE_COORDS.search(text)
    if m:
        return (float(m.group(1)), float(m.group(2))), None

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
        street, number, city = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return None, f"{street} {number}, {city}"

    m = RE_CROSS.match(candidate)
    if m:
        a, b, city = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return None, f"intersection of {a} and {b}, {city}"

    return None, None

# =========================
# OSM / GEOCODING
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
    return float(data[0]["lat"]), float(data[0]["lon"])

def geocode_intersection_overpass(q: str, user_agent: str) -> Optional[Tuple[float, float]]:
    """
    Input: 'intersection of A and B, City'
    Output: exact intersection node (lat, lon) if present in OSM.
    Worldwide, no country hardcoding.
    """
    m = re.search(r"^\s*intersection of\s+(.+?)\s+and\s+(.+?)\s*,\s*(.+?)\s*$", q, re.IGNORECASE)
    if not m:
        return None
    a, b, city = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    query = f"""
[out:json][timeout:25];
area["name"="{city}"]["boundary"="administrative"]->.a;
way(area.a)["highway"]["name"="{a}"]->.w1;
way(area.a)["highway"]["name"="{b}"]->.w2;
node(w.w1)(w.w2);
out body;
""".strip()
    headers = {"User-Agent": user_agent}
    r = requests.post("https://overpass-api.de/api/interpreter", data=query, headers=headers, timeout=35)
    r.raise_for_status()
    data = r.json()
    for el in data.get("elements", []):
        if el.get("type") == "node" and "lat" in el and "lon" in el:
            return float(el["lat"]), float(el["lon"])
    return None

def geocode_query_worldwide(q: str, user_agent: str) -> Tuple[Optional[Tuple[float, float]], int]:
    """
    Returns (coords, accuracy_m)
      10 = Overpass exact intersection
      25 = Nominatim good
      50 = fallback street-only
    """
    # 1) exact intersection via Overpass
    res = geocode_intersection_overpass(q, user_agent)
    if res:
        return res, 10

    # 2) Nominatim as-is
    res = geocode_nominatim(q, user_agent)
    if res:
        return res, 25

    # 3) fallback for intersections: street A / street B
    m = re.search(r"^\s*intersection of\s+(.+?)\s+and\s+(.+?)\s*,\s*(.+?)\s*$", q, re.IGNORECASE)
    if m:
        a, b, city = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        res = geocode_nominatim(f"{a}, {city}", user_agent)
        if res:
            return res, 50
        res = geocode_nominatim(f"{b}, {city}", user_agent)
        if res:
            return res, 50

    return None, 50

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
    for acc in get_favourited_by(cfg, status_id):
        acct = (acc.get("acct") or "").split("@")[0].lower()
        username = (acc.get("username") or "").lower()
        if acct in allowed or username in allowed:
            return True
    return False

# =========================
# GEOJSON
# =========================
def make_feature(item: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [item["lon"], item["lat"]]},
        "properties": {
            "id": item["id"],
            "date": (item.get("created_at") or "")[:10],
            "source": item["source"],
            "status": item.get("event", "present"),
            "accuracy_m": int(item.get("accuracy_m", cfg.get("accuracy_m", 25))),
            "notes": item.get("notes") or "",
            "media": item.get("media", []),
            "tag": item.get("tag") or "",
        },
    }

# =========================
# MAIN
# =========================
def iter_statuses(cfg: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    tags_map = cfg.get("hashtags") or {"sticker_report": "present"}
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

    pending_by_source = {p.get("source"): p for p in pending if p.get("source")}
    added_pending = 0
    published = 0

    # ingest
    for tag, event, st in iter_statuses(cfg):
        status_id = st.get("id")
        url = st.get("url")
        if not status_id or not url or url in pending_by_source:
            continue

        text = strip_html(st.get("content", ""))
        attachments = st.get("media_attachments", [])
        if not has_image(attachments):
            continue

        coords, q = parse_location(text)
        if not coords and not q:
            continue

        if not coords and q:
            q_norm = normalize_query(q)
            coords2, acc_m = geocode_query_worldwide(q_norm, cfg["user_agent"])
            time.sleep(1.0)
            if not coords2:
                continue
            coords = coords2
            cache[q] = {"lat": coords[0], "lon": coords[1], "ts": int(time.time()), "accuracy_m": acc_m}

        lat, lon = coords
        item = {
            "id": f"masto-{status_id}",
            "status_id": status_id,
            "status": "PENDING",
            "event": event,
            "tag": tag,
            "source": url,
            "created_at": st.get("created_at"),
            "notes": "",
            "lat": lat,
            "lon": lon,
            "accuracy_m": int(cache.get(q, {}).get("accuracy_m", cfg.get("accuracy_m", 25))),
            "media": [a.get("url") for a in attachments if a.get("type") == "image" and a.get("url")],
        }

        pending.append(item)
        pending_by_source[url] = item
        added_pending += 1
        time.sleep(0.2)

    # publish approved
    still_pending: List[Dict[str, Any]] = []
    for item in pending:
        if item.get("status") != "PENDING":
            continue
        if item.get("id") in reports_ids:
            continue
        if is_approved_by_fav(cfg, item["status_id"]):
            reports["features"].append(make_feature(item, cfg))
            reports_ids.add(item["id"])
            published += 1
        else:
            still_pending.append(item)
        time.sleep(0.4)

    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, still_pending)
    save_json(REPORTS_PATH, reports)

    print(f"Added pending: {added_pending} | Published: {published} | Pending left: {len(still_pending)}")

if __name__ == "__main__":
    main()
