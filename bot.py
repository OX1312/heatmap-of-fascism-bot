#!/usr/bin/env python3
# Heatmap of Fascism – Product Bot (Mastodon ingest → review via FAV → GeoJSON)
#
# HARD RULES (product):
# - Anyone can POST reports (public).
# - NOTHING is published unless the post is FAV’d by an allowed reviewer.
# - Locations:
#   - coords "lat, lon" anywhere in text
#   - OR "Street 12, City" (first non-hashtag line)
#   - OR "Street A / Street B, City" (first non-hashtag line)
# - Hashtags (exact, mastodon-safe):
#   - #sticker_report  -> present
#   - #sticker_removed -> removed
#
# Files:
# - config.json            (tracked)  rules + instance_url + user_agent + hashtags + stale_after_days + accuracy
# - secrets.json           (local, NOT tracked) {"access_token":"..."}
# - trusted_accounts.json  (local, NOT tracked) ["heatmapoffascism","buntepanther", ...]  (reviewers who may FAV-approve)
# - cache_geocode.json     (tracked)  geocode cache
# - pending.json           (tracked)  pending items waiting for approval
# - reports.geojson        (tracked)  single source of truth (FeatureCollection)
#
# Status model:
# - "present"  = confirmed present
# - "removed"  = confirmed removed
# - "unknown"  = stale/uncertain (was present but not confirmed for stale_after_days)
#
# Dupe merge on publish:
# If distance <= max(existing.radius_m, new.radius_m) AND sticker_type matches OR one is "unknown"
# then UPDATE existing feature (last_seen, seen_count, status, removed_at, media, accuracy/radius tightened)

import json
import re
import time
import pathlib
import math
from typing import Optional, Tuple, Dict, Any, List, Iterable
from datetime import datetime, timezone

import requests

# =========================
# FILES
# =========================
ROOT = pathlib.Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"
SECRETS_PATH = ROOT / "secrets.json"
TRUSTED_PATH = ROOT / "trusted_accounts.json"
CACHE_PATH = ROOT / "cache_geocode.json"
PENDING_PATH = ROOT / "pending.json"
REPORTS_PATH = ROOT / "reports.geojson"

# =========================
# REGEX
# =========================
RE_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")  # "Street 12, City"
RE_STREET_CITY = re.compile(r"^(.+?)\s*,\s*(.+)$")  # "Street, City"
RE_CROSS = re.compile(r"^(.+?)\s*(?:/| x | & )\s*(.+?)\s*,\s*(.+)$", re.IGNORECASE)  # "A / B, City"
RE_INTERSECTION = re.compile(r"^\s*intersection of\s+(.+?)\s+and\s+(.+?)\s*,\s*(.+?)\s*$", re.IGNORECASE)
RE_STICKER_TYPE = re.compile(r"(?im)^\s*#sticker_type\s*:\s*([^\n#]{1,80})\s*$")

# =========================
# ENDPOINTS / POLITENESS
# =========================
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
)

DELAY_TAG_FETCH = 0.15
DELAY_FAV_CHECK = 0.35
DELAY_NOMINATIM = 1.0

OVERPASS_TIMEOUT_S = 45
NOMINATIM_TIMEOUT_S = 25
MASTODON_TIMEOUT_S = 25

# Limit points used in nearest computation (protect runtime)
MAX_GEOM_POINTS_PER_STREET = 500

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

def ensure_object_file(path: pathlib.Path):
    if not path.exists():
        save_json(path, {})

def ensure_array_file(path: pathlib.Path):
    if not path.exists():
        save_json(path, [])

# =========================
# HELPERS
# =========================
def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def iso_date_from_created_at(created_at: Optional[str]) -> str:
    if not created_at:
        return today_iso()
    return created_at[:10]

def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()

def normalize_query(q: str) -> str:
    # Avoid fragile glyph mismatches in OSM search; keep worldwide
    q = q.replace("ß", "ss")
    q = q.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    q = q.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    return q



def normalize_location_line(s: str) -> str:
    s = (s or "").strip()

    # Strip common prefixes (multi-language)
    s = re.sub(r"(?i)^\s*(ort|location|place)\s*:\s*", "", s)


    # DE: "...str." / "...str" -> "...straße" (Arminstr., Hauptstr, etc.)
    s = re.sub(r"(?i)(?<=\w)str\.\b", "straße", s)
    s = re.sub(r"(?i)(?<=\w)str\b", "straße", s)

    # Cleanup
    # Fix punctuation artifacts (e.g. "straße.," -> "straße,")
    s = re.sub(r"\.,", ",", s)

    s = re.sub(r"\s+", " ", s)
    return s

def has_image(attachments: List[Dict[str, Any]]) -> bool:
    for a in attachments or []:
        if a.get("type") == "image" and a.get("url"):
            return True
    return False

def parse_sticker_type(text: str) -> str:
    m = RE_STICKER_TYPE.search(text)
    if not m:
        return "unknown"
    t = m.group(1).strip()
    return t if t else "unknown"

def norm_type(s: str) -> str:
    s = (s or "unknown").strip().lower()
    return s if s else "unknown"

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

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

    candidate = normalize_location_line(candidate)

    m = RE_ADDRESS.match(candidate)
    if m:
        street, number, city = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return None, f"{street} {number}, {city}"

    m = RE_CROSS.match(candidate)
    if m:
        a, b, city = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return None, f"intersection of {a} and {b}, {city}"

    m = RE_STREET_CITY.match(candidate)
    if m:
        street, city = m.group(1).strip(), m.group(2).strip()
        return None, f"{street}, {city}"

    return None, None

# =========================
# GEOCODING
# =========================
def geocode_nominatim(query: str, user_agent: str) -> Optional[Tuple[float, float]]:
    headers = {"User-Agent": user_agent}
    params = {"q": query, "format": "json", "limit": 1}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=NOMINATIM_TIMEOUT_S)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])

def _overpass_post(query: str, user_agent: str) -> Optional[Dict[str, Any]]:
    headers = {"User-Agent": user_agent}
    for ep in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(ep, data=query, headers=headers, timeout=OVERPASS_TIMEOUT_S)
            if r.status_code != 200:
                continue
            return r.json()
        except Exception:
            continue
    return None

def overpass_intersection(city: str, a: str, b: str, user_agent: str) -> Optional[Tuple[Tuple[float, float], str]]:
    """
    Returns:
      ((lat, lon), method)
      method:
        - "overpass_node"    (exact shared node)
        - "overpass_nearest" (nearest points between both street geometries; midpoint)
    """
    # 1) exact shared node
    q_node = f"""
[out:json][timeout:25];
area["name"="{city}"]["boundary"="administrative"]->.a;
way(area.a)["highway"]["name"="{a}"]->.w1;
way(area.a)["highway"]["name"="{b}"]->.w2;
node(w.w1)(w.w2);
out body;
""".strip()

    data = _overpass_post(q_node, user_agent)
    if data:
        for el in data.get("elements", []):
            if el.get("type") == "node" and "lat" in el and "lon" in el:
                return (float(el["lat"]), float(el["lon"])), "overpass_node"

    # 2) nearest geometry points between both sets of ways (midpoint of closest pair)
    q_geom = f"""
[out:json][timeout:25];
area["name"="{city}"]["boundary"="administrative"]->.a;
(
  way(area.a)["highway"]["name"="{a}"];
)->.wa;
(
  way(area.a)["highway"]["name"="{b}"];
)->.wb;
.wa out geom;
.wb out geom;
""".strip()

    data = _overpass_post(q_geom, user_agent)
    if not data:
        return None

    pts_a: List[Tuple[float, float]] = []
    pts_b: List[Tuple[float, float]] = []

    # Collect points by whether element matches a or b street name (best-effort)
    # Note: ways can be split; we accept all parts.
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        name = ((el.get("tags") or {}).get("name") or "").strip()
        geom = el.get("geometry") or []
        if not geom:
            continue

        if name == a:
            for p in geom:
                if "lat" in p and "lon" in p:
                    pts_a.append((float(p["lat"]), float(p["lon"])))
        elif name == b:
            for p in geom:
                if "lat" in p and "lon" in p:
                    pts_b.append((float(p["lat"]), float(p["lon"])))

    # If name matching failed (variants, casing), fallback: split by first/second half of ways list
    if not pts_a or not pts_b:
        ways = [el for el in data.get("elements", []) if el.get("type") == "way" and "geometry" in el]
        if len(ways) >= 2:
            mid = len(ways) // 2
            for el in ways[:mid]:
                for p in el.get("geometry", []):
                    if "lat" in p and "lon" in p:
                        pts_a.append((float(p["lat"]), float(p["lon"])))
            for el in ways[mid:]:
                for p in el.get("geometry", []):
                    if "lat" in p and "lon" in p:
                        pts_b.append((float(p["lat"]), float(p["lon"])))

    if not pts_a or not pts_b:
        return None

    pts_a = pts_a[:MAX_GEOM_POINTS_PER_STREET]
    pts_b = pts_b[:MAX_GEOM_POINTS_PER_STREET]

    best = None
    best_pair = None
    for (la, loa) in pts_a:
        for (lb, lob) in pts_b:
            d = haversine_m(la, loa, lb, lob)
            if best is None or d < best:
                best = d
                best_pair = (la, loa, lb, lob)

    if not best_pair:
        return None

    la, loa, lb, lob = best_pair
    lat = (la + lb) / 2.0
    lon = (loa + lob) / 2.0
    return (lat, lon), "overpass_nearest"

def geocode_query_worldwide(query: str, user_agent: str) -> Tuple[Optional[Tuple[float, float]], str]:
    """
    Returns (coords, method)
    method:
      - gps
      - overpass_node
      - overpass_nearest
      - nominatim
      - fallback
      - none
    """
    m = RE_INTERSECTION.match(query)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        city = m.group(3).strip()

        res = overpass_intersection(city=city, a=a, b=b, user_agent=user_agent)
        if res:
            coords, method = res
            return coords, method

        # If Overpass fails, try nominatim on full string
        try:
            res2 = geocode_nominatim(query, user_agent)
            if res2:
                return res2, "nominatim"
        except Exception:
            pass

        # fallback: one street in city (low confidence)
        try:
            res3 = geocode_nominatim(f"{a}, {city}", user_agent)
            if res3:
                return res3, "fallback"
        except Exception:
            pass
        try:
            res4 = geocode_nominatim(f"{b}, {city}", user_agent)
            if res4:
                return res4, "fallback"
        except Exception:
            pass

        return None, "none"

    # Non-intersection queries
    try:
        res = geocode_nominatim(query, user_agent)
        if res:
            return res, "nominatim"
    except Exception:
        pass
    return None, "none"

# =========================
# MASTODON API
# =========================
def get_hashtag_timeline(cfg: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    instance = cfg["instance_url"].rstrip("/")
    tag = tag.lstrip("#")
    url = f"{instance}/api/v1/timelines/tag/{tag}"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    r = requests.get(url, headers=headers, timeout=MASTODON_TIMEOUT_S)
    r.raise_for_status()
    return r.json()

def get_favourited_by(cfg: Dict[str, Any], status_id: str) -> List[Dict[str, Any]]:
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/statuses/{status_id}/favourited_by"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    r = requests.get(url, headers=headers, timeout=MASTODON_TIMEOUT_S)
    r.raise_for_status()
    return r.json()

def load_trusted_set(cfg: Dict[str, Any]) -> set:
    # Union of (config allowed_reviewers) + (local trusted_accounts.json)
    allowed = set((a or "").split("@")[0].strip().lower() for a in (cfg.get("allowed_reviewers") or []))
    trusted_local = load_json(TRUSTED_PATH, [])
    if isinstance(trusted_local, list):
        for a in trusted_local:
            if isinstance(a, str):
                allowed.add(a.split("@")[0].strip().lower())
    allowed.discard("")
    return allowed

def is_approved_by_fav(cfg: Dict[str, Any], status_id: str, trusted_set: set) -> bool:
    if not trusted_set:
        return False
    try:
        fav_accounts = get_favourited_by(cfg, status_id)
    except Exception:
        return False

    for acc in fav_accounts:
        acct = (acc.get("acct") or "").split("@")[0].strip().lower()
        username = (acc.get("username") or "").strip().lower()
        if acct in trusted_set or username in trusted_set:
            return True
    return False

# =========================
# REPORTS (PRODUCT SCHEMA)
# =========================
def load_reports() -> Dict[str, Any]:
    ensure_reports_file()
    data = load_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        return {"type": "FeatureCollection", "features": []}
    if "features" not in data or not isinstance(data["features"], list):
        data["features"] = []
    return data

def reports_id_set(reports: Dict[str, Any]) -> set:
    ids = set()
    for f in reports.get("features", []):
        p = f.get("properties") or {}
        if p.get("id"):
            ids.add(p["id"])
    return ids

def apply_stale_rule(reports: Dict[str, Any], stale_after_days: int) -> int:
    """
    Product rule:
      - present -> unknown if not confirmed for N days
      - removed stays removed (no auto-change)
    """
    from datetime import date

    def parse_date(s: str) -> Optional[date]:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    changed = 0
    today = datetime.now(timezone.utc).date()

    for f in reports.get("features", []):
        p = f.get("properties") or {}
        if p.get("status") != "present":
            continue
        last_seen = parse_date(str(p.get("last_seen", "")))
        if not last_seen:
            continue
        if (today - last_seen).days >= stale_after_days:
            p["status"] = "unknown"
            p["stale_after_days"] = int(stale_after_days)
            changed += 1

    return changed

def make_product_feature(
    *,
    item_id: str,
    source_url: str,
    status: str,  # present | removed | unknown
    sticker_type: str,
    created_date: str,
    lat: float,
    lon: float,
    accuracy_m: int,
    radius_m: int,
    geocode_method: str,  # gps | nominatim | overpass_node | overpass_nearest | fallback
    location_text: str,
    media: List[str],
    stale_after_days: int,
    removed_at: Optional[str],
) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
        "properties": {
            "id": item_id,
            "source": source_url,

            "status": status,
            "sticker_type": sticker_type,

            "first_seen": created_date,
            "last_seen": created_date,
            "seen_count": 1,

            "removed_at": removed_at,
            "stale_after_days": int(stale_after_days),

            "accuracy_m": int(accuracy_m),
            "radius_m": int(radius_m),
            "geocode_method": geocode_method,

            "location_text": location_text,
            "media": media,
            "notes": ""
        }
    }

# =========================
# MAIN
# =========================
def iter_statuses(cfg: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    """
    Yields: (tag, event, status_dict)
    event is "present" or "removed" (from cfg["hashtags"])
    """
    tags_map = cfg.get("hashtags") or {}
    if not isinstance(tags_map, dict) or not tags_map:
        tags_map = {"sticker_report": "present", "sticker_removed": "removed"}

    for tag, event in tags_map.items():
        statuses = get_hashtag_timeline(cfg, tag)
        for st in statuses:
            yield tag, event, st
        time.sleep(DELAY_TAG_FETCH)

def main():
    # Ensure baseline files exist
    ensure_object_file(CACHE_PATH)
    ensure_array_file(PENDING_PATH)
    ensure_reports_file()

    cfg = load_json(CFG_PATH, None)
    if not cfg:
        raise SystemExit("Missing config.json")

    secrets = load_json(SECRETS_PATH, None)
    if not secrets or not secrets.get("access_token"):
        raise SystemExit('Missing secrets.json (needs: {"access_token":"..."})')

    cfg["access_token"] = secrets["access_token"]

    # Accuracy/radius defaults
    stale_after_days = int(cfg.get("stale_after_days", 30))
    acc_cfg = cfg.get("accuracy") or {}

    ACC_GPS = int(acc_cfg.get("gps_m", 10))
    ACC_NODE = int(acc_cfg.get("intersection_node_m", 10))
    ACC_NEAREST = int(acc_cfg.get("intersection_nearest_m", 25))
    ACC_DEFAULT = int(acc_cfg.get("default_m", 25))
    ACC_FALLBACK = int(acc_cfg.get("fallback_m", 50))

    trusted_set = load_trusted_set(cfg)

    cache: Dict[str, Any] = load_json(CACHE_PATH, {})
    pending: List[Dict[str, Any]] = load_json(PENDING_PATH, [])
    reports = load_reports()
    reports_ids = reports_id_set(reports)

    # Dedupe pending by source URL
    pending_by_source = {p.get("source"): p for p in pending if p.get("source")}

    added_pending = 0
    published = 0

    # -------------------------
    # 1) Ingest into pending
    # -------------------------
    for tag, event, st in iter_statuses(cfg):
        status_id = st.get("id")
        url = st.get("url")
        if not status_id or not url:
            continue

        item_id = f"masto-{status_id}"
        if item_id in reports_ids:
            continue
        if url in pending_by_source:
            continue

        text = strip_html(st.get("content", ""))
        attachments = st.get("media_attachments", [])
        if not has_image(attachments):
            continue

        coords, q = parse_location(text)
        if not coords and not q:
            continue

        created_date = iso_date_from_created_at(st.get("created_at"))
        sticker_type = parse_sticker_type(text)

        geocode_method = "gps"
        accuracy_m = ACC_DEFAULT
        radius_m = ACC_DEFAULT
        location_text = ""
        removed_at: Optional[str] = None

        if coords:
            lat, lon = coords
            geocode_method = "gps"
            accuracy_m = ACC_GPS
            radius_m = ACC_GPS
            location_text = f"{lat}, {lon}"
        else:
            location_text = q or ""
            q_norm = normalize_query(q or "")

            if q in cache and "lat" in cache[q] and "lon" in cache[q]:
                lat, lon = float(cache[q]["lat"]), float(cache[q]["lon"])
                geocode_method = str(cache[q].get("method", "cache"))
                accuracy_m = int(cache[q].get("accuracy_m", ACC_DEFAULT))
                radius_m = int(cache[q].get("radius_m", accuracy_m))
            else:
                coords2, method = geocode_query_worldwide(q_norm, cfg["user_agent"])
                time.sleep(DELAY_NOMINATIM)
                if not coords2:
                    continue
                lat, lon = coords2
                geocode_method = method

                if method == "overpass_node":
                    accuracy_m = ACC_NODE
                elif method == "overpass_nearest":
                    accuracy_m = ACC_NEAREST
                elif method == "fallback":
                    accuracy_m = ACC_FALLBACK
                else:
                    accuracy_m = ACC_DEFAULT

                radius_m = accuracy_m  # circle size = uncertainty

                cache[q] = {
                    "lat": lat,
                    "lon": lon,
                    "ts": int(time.time()),
                    "method": geocode_method,
                    "accuracy_m": accuracy_m,
                    "radius_m": radius_m,
                    "q_norm": q_norm
                }

        if event == "removed":
            removed_at = created_date

        media_urls = [
            a.get("url")
            for a in attachments
            if a.get("type") == "image" and a.get("url")
        ]

        item = {
            "id": item_id,
            "status_id": str(status_id),
            "status": "PENDING",
            "event": event,  # present/removed
            "tag": tag,
            "source": url,
            "created_at": st.get("created_at"),
            "created_date": created_date,

            "lat": float(lat),
            "lon": float(lon),

            "accuracy_m": int(accuracy_m),
            "radius_m": int(radius_m),
            "geocode_method": geocode_method,
            "location_text": location_text,

            "sticker_type": sticker_type,
            "removed_at": removed_at,

            "media": media_urls,
        }

        pending.append(item)
        pending_by_source[url] = item
        added_pending += 1

    # -------------------------
    # 2) Publish approved (FAV)
    # -------------------------
    still_pending: List[Dict[str, Any]] = []

    for item in pending:
        if item.get("status") != "PENDING":
            continue

        item_id = str(item.get("id"))
        if item_id in reports_ids:
            continue

        ok = is_approved_by_fav(cfg, str(item["status_id"]), trusted_set)
        if not ok:
            still_pending.append(item)
            time.sleep(DELAY_FAV_CHECK)
            continue

        new_status = ("removed" if item.get("event") == "removed" else "present")
        new_removed_at = item.get("removed_at", None)
        created_date = str(item.get("created_date") or today_iso())

        new_feat = make_product_feature(
            item_id=item_id,
            source_url=str(item["source"]),
            status=new_status,
            sticker_type=str(item.get("sticker_type") or "unknown"),
            created_date=created_date,
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            accuracy_m=int(item.get("accuracy_m", ACC_DEFAULT)),
            radius_m=int(item.get("radius_m", item.get("accuracy_m", ACC_DEFAULT))),
            geocode_method=str(item.get("geocode_method") or "nominatim"),
            location_text=str(item.get("location_text") or ""),
            media=list(item.get("media") or []),
            stale_after_days=int(stale_after_days),
            removed_at=new_removed_at,
        )

        # Dupe merge:
        new_p = new_feat["properties"]
        new_lat = float(item["lat"])
        new_lon = float(item["lon"])
        new_r = int(new_p.get("radius_m") or new_p.get("accuracy_m") or ACC_DEFAULT)
        new_type = norm_type(new_p.get("sticker_type"))

        merged = False

        for f in reports.get("features", []):
            p = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or []
            if len(coords) != 2:
                continue

            ex_lon, ex_lat = float(coords[0]), float(coords[1])
            ex_r = int(p.get("radius_m") or p.get("accuracy_m") or ACC_DEFAULT)
            ex_type = norm_type(p.get("sticker_type"))

            # type rule: must match OR one side unknown
            if not (new_type == "unknown" or ex_type == "unknown" or new_type == ex_type):
                continue

            dist = haversine_m(new_lat, new_lon, ex_lat, ex_lon)
            if dist <= max(ex_r, new_r):
                # UPDATE existing
                p["last_seen"] = created_date
                p["seen_count"] = int(p.get("seen_count", 1)) + 1

                if new_status == "present":
                    p["status"] = "present"      # can revive from unknown
                    p["removed_at"] = None
                else:
                    p["status"] = "removed"
                    p["removed_at"] = new_removed_at

                # promote type if existing unknown
                if ex_type == "unknown" and new_type != "unknown":
                    p["sticker_type"] = new_p.get("sticker_type", "unknown")

                # tighten uncertainty if we got better info
                p["accuracy_m"] = min(int(p.get("accuracy_m", ex_r)), int(new_p.get("accuracy_m", new_r)))
                p["radius_m"] = min(int(p.get("radius_m", ex_r)), int(new_p.get("radius_m", new_r)))

                # merge media
                media = list(p.get("media") or [])
                seen = set(media)
                for u in list(new_p.get("media") or []):
                    if u and u not in seen:
                        media.append(u)
                        seen.add(u)
                p["media"] = media

                merged = True
                published += 1
                break

        if not merged:
            reports["features"].append(new_feat)
            reports_ids.add(item_id)
            published += 1

        time.sleep(DELAY_FAV_CHECK)

    # 3) Stale rule: present -> unknown after N days
    apply_stale_rule(reports, stale_after_days)

    # Write outputs
    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, still_pending)
    save_json(REPORTS_PATH, reports)

    print(f"Added pending: {added_pending} | Published: {published} | Pending left: {len(still_pending)}")

if __name__ == "__main__":
    main()
