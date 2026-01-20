#!/usr/bin/env python3
# Heatmap of Fascism - Minimal Ingest + Review via FAV (multi-hashtag, worldwide, robust)
#
# PRODUCT SETUP
# - config.json (tracked): rules + hashtags + reviewers + accuracy targets
# - secrets.json (local, NOT tracked): {"access_token":"..."}  (gitignored)
#
# FEATURES
# - Multiple hashtags via config.json:  {"hashtags": {"sticker_report":"present","sticker_removed":"removed"}}
# - Requires: image + location (coords OR "Street 12, City" OR "StreetA / StreetB, City")
# - Crossings: try Overpass intersection (≈10 m). If Overpass is down/slow -> fallback to Nominatim + street fallback.
# - Cache geocoding in cache_geocode.json
# - Queue in pending.json
# - Publish to reports.geojson when favourited by allowed reviewer(s)

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
SECRETS_PATH = ROOT / "secrets.json"
CACHE_PATH = ROOT / "cache_geocode.json"
PENDING_PATH = ROOT / "pending.json"
REPORTS_PATH = ROOT / "reports.geojson"

# =========================
# REGEX
# =========================
RE_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")  # "Street 12, City"
RE_CROSS = re.compile(r"^(.+?)\s*(?:/| x | & )\s*(.+?)\s*,\s*(.+)$", re.IGNORECASE)  # "A / B, City"
RE_INTERSECTION = re.compile(
    r"^\s*intersection of\s+(.+?)\s+and\s+(.+?)\s*,\s*(.+?)\s*$",
    re.IGNORECASE,
)

# =========================
# CONSTANTS
# =========================
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
)

# polite delays (seconds)
DELAY_TAG_FETCH = 0.15
DELAY_FAV_CHECK = 0.35
DELAY_NOMINATIM = 1.0

# =========================
# IO
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
    # keep worldwide neutrality; only reduce fragile glyphs that often break matching
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
      OR a query string if address/crossing found in first non-hashtag line
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
# GEOCODING
# =========================
def geocode_nominatim(query: str, user_agent: str) -> Optional[Tuple[float, float]]:
    headers = {"User-Agent": user_agent}
    params = {"q": query, "format": "json", "limit": 1}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])

def overpass_intersection(city: str, a: str, b: str, user_agent: str) -> Optional[Tuple[float, float]]:
    """
    Exact street intersection via Overpass.
    Returns (lat, lon) for a shared node of both named street ways inside the admin area of 'city'.
    Robust: tries multiple endpoints, never raises (fails open to fallback).
    """
    q = f"""
[out:json][timeout:25];
area["name"="{city}"]["boundary"="administrative"]->.a;
way(area.a)["highway"]["name"="{a}"]->.w1;
way(area.a)["highway"]["name"="{b}"]->.w2;
node(w.w1)(w.w2);
out body;
""".strip()

    headers = {"User-Agent": user_agent}

    for ep in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(ep, data=q, headers=headers, timeout=35)
            if r.status_code != 200:
                continue
            data = r.json()
            for el in data.get("elements", []):
                if el.get("type") == "node" and "lat" in el and "lon" in el:
                    return float(el["lat"]), float(el["lon"])
        except Exception:
            continue
    return None

def geocode_query_worldwide(query: str, user_agent: str) -> Tuple[Optional[Tuple[float, float]], int, str]:
    """
    Returns (coords, accuracy_m, method)
      method:
        "overpass"  -> intersection_m
        "nominatim" -> default_m
        "fallback"  -> fallback_m
    """
    m = RE_INTERSECTION.match(query)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        city = m.group(3).strip()

        # 1) Overpass exact
        res = overpass_intersection(city=city, a=a, b=b, user_agent=user_agent)
        if res:
            return res, 10, "overpass"

        # 2) Nominatim with intersection query as-is
        try:
            res = geocode_nominatim(query, user_agent)
            if res:
                return res, 25, "nominatim"
        except Exception:
            pass

        # 3) Fallback: street A then street B within city
        try:
            res = geocode_nominatim(f"{a}, {city}", user_agent)
            if res:
                return res, 50, "fallback"
        except Exception:
            pass

        try:
            res = geocode_nominatim(f"{b}, {city}", user_agent)
            if res:
                return res, 50, "fallback"
        except Exception:
            pass

        return None, 50, "none"

    # Non-intersection: Nominatim
    try:
        res = geocode_nominatim(query, user_agent)
        if res:
            return res, 25, "nominatim"
    except Exception:
        pass
    return None, 25, "none"

# =========================
# MASTODON
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
    try:
        fav_accounts = get_favourited_by(cfg, status_id)
    except Exception:
        return False

    for acc in fav_accounts:
        acct = (acc.get("acct") or "").split("@")[0].lower()
        username = (acc.get("username") or "").lower()
        if acct in allowed or username in allowed:
            return True
    return False

# =========================
# GEOJSON
# =========================
def make_feature(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [item["lon"], item["lat"]]},
        "properties": {
            "id": item["id"],
            "date": (item.get("created_at") or "")[:10],
            "source": item["source"],
            "status": item.get("event", "present"),
            "accuracy_m": int(item.get("accuracy_m", 25)),
            "method": item.get("geocode_method", ""),
            "notes": item.get("notes") or "",
            "media": item.get("media", []),
            "tag": item.get("tag") or "",
        },
    }

# =========================
# MAIN
# =========================
def iter_statuses(cfg: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    tags_map = cfg.get("hashtags") or {}
    if not isinstance(tags_map, dict) or not tags_map:
        tags_map = {"sticker_report": "present"}

    for tag, event in tags_map.items():
        statuses = get_hashtag_timeline(cfg, tag)
        for st in statuses:
            yield tag, event, st
        time.sleep(DELAY_TAG_FETCH)

def main():
    cfg = load_json(CFG_PATH, None)
    if not cfg:
        raise SystemExit("Missing config.json")

    secrets = load_json(SECRETS_PATH, None)
    if not secrets or not secrets.get("access_token"):
        raise SystemExit('Missing secrets.json (needs: {"access_token":"..."})')

    # merge secret into cfg for API calls
    cfg["access_token"] = secrets["access_token"]

    # pull accuracy targets from config (product-level)
    acc_cfg = cfg.get("accuracy") or {}
    ACC_INTERSECTION = int(acc_cfg.get("intersection_m", 10))
    ACC_DEFAULT = int(acc_cfg.get("default_m", 25))
    ACC_FALLBACK = int(acc_cfg.get("fallback_m", 50))

    cache: Dict[str, Any] = load_json(CACHE_PATH, {})
    pending: List[Dict[str, Any]] = load_json(PENDING_PATH, [])
    ensure_reports_file()
    reports = load_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})

    reports_ids = set((f.get("properties") or {}).get("id") for f in reports.get("features", []))
    pending_by_source = {p.get("source"): p for p in pending if p.get("source")}

    added_pending = 0
    published = 0

    # ---- ingest ----
    for tag, event, st in iter_statuses(cfg):
        status_id = st.get("id")
        url = st.get("url")
        if not status_id or not url:
            continue

        # dedupe: same mastodon URL already pending OR already published
        if url in pending_by_source:
            continue
        if f"masto-{status_id}" in reports_ids:
            continue

        text = strip_html(st.get("content", ""))
        attachments = st.get("media_attachments", [])

        if not has_image(attachments):
            continue  # must have photo

        coords, q = parse_location(text)
        if not coords and not q:
            continue  # must have coords or parseable address/crossing

        accuracy_m = ACC_DEFAULT
        method = ""

        if not coords and q:
            if q in cache and "lat" in cache[q] and "lon" in cache[q]:
                coords = (float(cache[q]["lat"]), float(cache[q]["lon"]))
                accuracy_m = int(cache[q].get("accuracy_m", ACC_DEFAULT))
                method = str(cache[q].get("method", "cache"))
            else:
                q_norm = normalize_query(q)
                coords2, acc_m, mth = geocode_query_worldwide(q_norm, cfg["user_agent"])
                time.sleep(DELAY_NOMINATIM)
                if not coords2:
                    continue
                coords = coords2
                # translate method to product accuracies
                if mth == "overpass":
                    accuracy_m = ACC_INTERSECTION
                elif mth == "nominatim":
                    accuracy_m = ACC_DEFAULT
                elif mth == "fallback":
                    accuracy_m = ACC_FALLBACK
                else:
                    accuracy_m = ACC_DEFAULT
                method = mth
                cache[q] = {
                    "lat": coords[0],
                    "lon": coords[1],
                    "ts": int(time.time()),
                    "accuracy_m": accuracy_m,
                    "method": method,
                    "q_norm": q_norm,
                }

        lat, lon = coords
        item = {
            "id": f"masto-{status_id}",
            "status_id": status_id,
            "status": "PENDING",
            "event": event,      # present/removed
            "tag": tag,          # hashtag that caught it
            "source": url,
            "created_at": st.get("created_at"),
            "notes": "",
            "lat": float(lat),
            "lon": float(lon),
            "accuracy_m": int(accuracy_m),
            "geocode_method": method,
            "location_query": q,  # None if coords were used
            "media": [a.get("url") for a in attachments if a.get("type") == "image" and a.get("url")],
        }

        pending.append(item)
        pending_by_source[url] = item
        added_pending += 1

    # ---- publish approved ----
    still_pending: List[Dict[str, Any]] = []
    for item in pending:
        if item.get("status") != "PENDING":
            continue
        item_id = item.get("id")
        if item_id in reports_ids:
            continue

        if is_approved_by_fav(cfg, item["status_id"]):
            reports["features"].append(make_feature(item))
            reports_ids.add(item_id)
            published += 1
        else:
            still_pending.append(item)

        time.sleep(DELAY_FAV_CHECK)

    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, still_pending)
    save_json(REPORTS_PATH, reports)

    print(f"Added pending: {added_pending} | Published: {published} | Pending left: {len(still_pending)}")

if __name__ == "__main__":
    main()
