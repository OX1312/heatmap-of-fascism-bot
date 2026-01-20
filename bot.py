#!/usr/bin/env python3
# Heatmap of Fascism — Product Bot (Mastodon → Review via FAV → GeoJSON)
#
# RULES (PRODUCT)
# - Anyone can post.
# - Only a FAV by an allowed reviewer publishes/updates the map.
# - Multiple hashtags supported via config.json:
#     "hashtags": { "sticker_report": "present", "sticker_removed": "removed" }
# - Location required: (coords) OR (street+city) OR (crossing+city)
# - Geocoding worldwide:
#     - intersections: Overpass (multi-endpoint) → fallback to Nominatim
#     - other queries: Nominatim
# - Output: reports.geojson is the single source of truth (FeatureCollection)
# - Schema: every published feature has radius_m, accuracy_m, etc.
# - Dupe merge (on publish/update):
#     If distance <= max(existing.radius_m, new.radius_m) AND types match OR one is "unknown"
#     then update existing feature instead of creating a new one.
#
# FILES
# - config.json (tracked)
# - secrets.json (local, NOT tracked): {"access_token":"..."}  (gitignored)
# - cache_geocode.json (tracked or local): {}  (geocode cache)
# - pending.json (local): []  (waiting for FAV approval)
# - reports.geojson (tracked): FeatureCollection
#
# NOTE
# - If reports.geojson is empty (features: []), uMap will show “no data”.
# - Run bot regularly (launchd/cron) AND push reports.geojson to GitHub for uMap remote URL.

import json
import re
import time
import pathlib
import math
from typing import Optional, Tuple, Dict, Any, List, Iterable
from datetime import datetime, timezone

import requests


# ============================================================
# HEADER / PATHS
# ============================================================
ROOT = pathlib.Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"
SECRETS_PATH = ROOT / "secrets.json"
CACHE_PATH = ROOT / "cache_geocode.json"
PENDING_PATH = ROOT / "pending.json"
REPORTS_PATH = ROOT / "reports.geojson"


# ============================================================
# REGEX / PARSING
# ============================================================
RE_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")  # "Street 12, City"
RE_CROSS = re.compile(r"^(.+?)\s*(?:/| x | & )\s*(.+?)\s*,\s*(.+)$", re.IGNORECASE)  # "A / B, City"
RE_INTERSECTION = re.compile(r"^\s*intersection of\s+(.+?)\s+and\s+(.+?)\s*,\s*(.+?)\s*$", re.IGNORECASE)

# optional line like: "#sticker_type: party" (anywhere in post body)
RE_STICKER_TYPE = re.compile(r"(?im)^\s*#sticker_type\s*:\s*([^\n#]{1,80})\s*$")


# ============================================================
# NETWORK ENDPOINTS / POLITENESS
# ============================================================
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
)

DELAY_TAG_FETCH = 0.15      # between tag timeline fetches
DELAY_FAV_CHECK = 0.35      # between favourited_by calls
DELAY_NOMINATIM = 1.0       # be polite to Nominatim


# ============================================================
# IO
# ============================================================
def load_json(path: pathlib.Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return default
        return json.loads(content)

def save_json(path: pathlib.Path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_reports_file():
    if not REPORTS_PATH.exists():
        save_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})

def ensure_pending_file():
    if not PENDING_PATH.exists():
        save_json(PENDING_PATH, [])

def ensure_cache_file():
    if not CACHE_PATH.exists():
        save_json(CACHE_PATH, {})


# ============================================================
# UTILS
# ============================================================
def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def iso_date_from_created_at(created_at: Optional[str]) -> str:
    # Mastodon created_at is ISO8601; keep YYYY-MM-DD
    return created_at[:10] if created_at else today_iso()

def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()

def normalize_query(q: str) -> str:
    # do NOT force country; keep worldwide. Only normalize fragile glyphs.
    q = q.replace("ß", "ss")
    q = q.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    q = q.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    return q

def has_image(attachments: List[Dict[str, Any]]) -> bool:
    for a in attachments or []:
        if a.get("type") == "image" and a.get("url"):
            return True
    return False

def parse_sticker_type(text: str) -> str:
    m = RE_STICKER_TYPE.search(text)
    if not m:
        return "unknown"
    t = (m.group(1) or "").strip()
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


# ============================================================
# LOCATION PARSE
# ============================================================
def parse_location(text: str) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """
    Returns:
      (lat, lon) if coords found anywhere in text
      OR query string if address/crossing found in first non-hashtag line
      OR (None, None) if invalid
    """
    m = RE_COORDS.search(text)
    if m:
        return (float(m.group(1)), float(m.group(2))), None

    # first non-empty line not starting with "#"
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


# ============================================================
# GEOCODING
# ============================================================
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
    Try to find a shared node between two named highways inside an administrative area.
    Robust: tries multiple Overpass endpoints, never raises.
    """
    query = f"""
[out:json][timeout:25];
area["name"="{city}"]["boundary"="administrative"]->.area;
way(area.area)["highway"]["name"="{a}"]->.w1;
way(area.area)["highway"]["name"="{b}"]->.w2;
node(w.w1)(w.w2);
out body;
""".strip()

    headers = {"User-Agent": user_agent}
    for ep in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(ep, data=query, headers=headers, timeout=35)
            if r.status_code != 200:
                continue
            data = r.json()
            for el in data.get("elements", []):
                if el.get("type") == "node" and "lat" in el and "lon" in el:
                    return float(el["lat"]), float(el["lon"])
        except Exception:
            continue
    return None

def geocode_query_worldwide(query: str, user_agent: str) -> Tuple[Optional[Tuple[float, float]], str]:
    """
    Returns (coords, method)
    method: overpass | nominatim | fallback | none
    """
    m = RE_INTERSECTION.match(query)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        city = m.group(3).strip()

        res = overpass_intersection(city=city, a=a, b=b, user_agent=user_agent)
        if res:
            return res, "overpass"

        # try full string in nominatim
        try:
            res = geocode_nominatim(query, user_agent)
            if res:
                return res, "nominatim"
        except Exception:
            pass

        # fallback: one street in city (less precise)
        for q2 in (f"{a}, {city}", f"{b}, {city}"):
            try:
                res = geocode_nominatim(q2, user_agent)
                if res:
                    return res, "fallback"
            except Exception:
                pass

        return None, "none"

    # non-intersection
    try:
        res = geocode_nominatim(query, user_agent)
        if res:
            return res, "nominatim"
    except Exception:
        pass
    return None, "none"


# ============================================================
# MASTODON API
# ============================================================
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
        # acct may be "name" or "name@instance"
        acct = (acc.get("acct") or "").split("@")[0].lower()
        username = (acc.get("username") or "").lower()
        if acct in allowed or username in allowed:
            return True
    return False


# ============================================================
# REPORTS (PRODUCT SCHEMA)
# ============================================================
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
    present -> stale if last_seen older than stale_after_days.
    (Stale means: not confirmed recently; you said "unknown-ish".)
    """
    def parse_date(s: str) -> Optional[datetime]:
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None

    changed = 0
    today = datetime.now(timezone.utc).date()

    for f in reports.get("features", []):
        p = f.get("properties") or {}
        if p.get("status") != "present":
            continue
        last_seen_dt = parse_date(str(p.get("last_seen", "")))
        if not last_seen_dt:
            continue
        delta = (today - last_seen_dt.date()).days
        if delta >= stale_after_days:
            p["status"] = "stale"
            p["stale_after_days"] = int(stale_after_days)
            changed += 1

    return changed

def make_product_feature(
    *,
    item_id: str,
    source_url: str,
    status: str,  # present | removed | stale
    sticker_type: str,
    seen_date: str,
    lat: float,
    lon: float,
    accuracy_m: int,
    radius_m: int,
    geocode_method: str,  # gps | nominatim | overpass | fallback
    location_text: str,
    media: List[str],
    stale_after_days: int,
    removed_at: Optional[str],
) -> Dict[str, Any]:
    # FULL PRODUCT SCHEMA (always includes radius_m etc.)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
        "properties": {
            "id": item_id,
            "source": source_url,

            "status": status,
            "sticker_type": sticker_type,

            "first_seen": seen_date,
            "last_seen": seen_date,
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


# ============================================================
# INGEST ITERATION
# ============================================================
def iter_statuses(cfg: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    tags_map = cfg.get("hashtags") or {}
    if not isinstance(tags_map, dict) or not tags_map:
        tags_map = {"sticker_report": "present"}

    for tag, event in tags_map.items():
        statuses = get_hashtag_timeline(cfg, tag)
        for st in statuses:
            yield tag, event, st
        time.sleep(DELAY_TAG_FETCH)


# ============================================================
# DEDUPE / MERGE
# ============================================================
def match_existing_feature(
    reports: Dict[str, Any],
    *,
    new_lat: float,
    new_lon: float,
    new_radius_m: int,
    new_type: str,
    default_radius_m: int
) -> Optional[Dict[str, Any]]:
    """
    Return existing feature dict if match found, else None.
    Match rule:
      dist <= max(existing.radius_m, new.radius_m)
      AND (types match OR one side is "unknown")
    """
    for f in reports.get("features", []):
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) != 2:
            continue
        ex_lon, ex_lat = float(coords[0]), float(coords[1])

        p = f.get("properties") or {}
        ex_radius = int(p.get("radius_m") or p.get("accuracy_m") or default_radius_m)
        ex_type = norm_type(p.get("sticker_type"))

        if not (new_type == "unknown" or ex_type == "unknown" or new_type == ex_type):
            continue

        dist = haversine_m(new_lat, new_lon, ex_lat, ex_lon)
        if dist <= max(ex_radius, int(new_radius_m)):
            return f
    return None

def merge_into_existing(
    existing: Dict[str, Any],
    *,
    new_status: str,
    seen_date: str,
    new_removed_at: Optional[str],
    new_accuracy_m: int,
    new_radius_m: int,
    new_type: str,
    new_media: List[str],
):
    p = existing.get("properties") or {}
    existing["properties"] = p

    # dates + counts
    p["last_seen"] = seen_date
    p["seen_count"] = int(p.get("seen_count", 1)) + 1

    # status transitions
    if new_status == "present":
        p["status"] = "present"
        p["removed_at"] = None
    elif new_status == "removed":
        p["status"] = "removed"
        p["removed_at"] = new_removed_at

    # promote type if existing unknown
    if norm_type(p.get("sticker_type")) == "unknown" and new_type != "unknown":
        p["sticker_type"] = new_type

    # precision: accuracy gets tighter if possible; radius should be conservative (uncertainty)
    p["accuracy_m"] = min(int(p.get("accuracy_m", new_accuracy_m)), int(new_accuracy_m))
    p["radius_m"] = max(int(p.get("radius_m", new_radius_m)), int(new_radius_m))

    # merge media unique
    media = list(p.get("media") or [])
    seen = set(media)
    for u in new_media:
        if u and u not in seen:
            media.append(u)
            seen.add(u)
    p["media"] = media


# ============================================================
# MAIN
# ============================================================
def main():
    # ---- load config + secrets ----
    cfg = load_json(CFG_PATH, None)
    if not cfg:
        raise SystemExit("Missing config.json")

    secrets = load_json(SECRETS_PATH, None)
    if not secrets or not secrets.get("access_token"):
        raise SystemExit('Missing secrets.json (needs: {"access_token":"..."})')

    cfg["access_token"] = secrets["access_token"]

    # ---- product constants ----
    stale_after_days = int(cfg.get("stale_after_days", 30))

    acc_cfg = cfg.get("accuracy") or {}
    ACC_INTERSECTION = int(acc_cfg.get("intersection_m", 10))  # best case
    ACC_DEFAULT = int(acc_cfg.get("default_m", 25))
    ACC_FALLBACK = int(acc_cfg.get("fallback_m", 50))          # worst case
    MAX_RADIUS = int(acc_cfg.get("max_radius_m", 50))          # cap for mapping circles (optional)

    ensure_cache_file()
    ensure_pending_file()
    ensure_reports_file()

    cache: Dict[str, Any] = load_json(CACHE_PATH, {})
    pending: List[Dict[str, Any]] = load_json(PENDING_PATH, [])
    reports = load_reports()
    reports_ids = reports_id_set(reports)

    # dedupe pending by source
    pending_by_source = {p.get("source"): p for p in pending if p.get("source")}

    added_pending = 0
    published = 0

    # ========================================================
    # INGEST → pending.json
    # ========================================================
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

        seen_date = iso_date_from_created_at(st.get("created_at"))
        sticker_type = parse_sticker_type(text)

        # resolve coords
        geocode_method = "gps"
        accuracy_m = ACC_DEFAULT
        radius_m = ACC_DEFAULT
        location_text = ""
        if coords:
            lat, lon = coords
            geocode_method = "gps"
            accuracy_m = ACC_INTERSECTION
            radius_m = ACC_INTERSECTION
            location_text = f"{lat}, {lon}"
        else:
            location_text = q or ""
            q_key = q or ""
            q_norm = normalize_query(q_key)

            if q_key in cache and "lat" in cache[q_key] and "lon" in cache[q_key]:
                lat, lon = float(cache[q_key]["lat"]), float(cache[q_key]["lon"])
                geocode_method = str(cache[q_key].get("method", "cache"))
                accuracy_m = int(cache[q_key].get("accuracy_m", ACC_DEFAULT))
                radius_m = int(cache[q_key].get("radius_m", accuracy_m))
            else:
                coords2, method = geocode_query_worldwide(q_norm, cfg.get("user_agent", "HeatmapOfFascismBot/1.0"))
                time.sleep(DELAY_NOMINATIM)
                if not coords2:
                    continue
                lat, lon = coords2
                geocode_method = method

                if method == "overpass":
                    accuracy_m = ACC_INTERSECTION
                elif method == "fallback":
                    accuracy_m = ACC_FALLBACK
                else:
                    accuracy_m = ACC_DEFAULT

                radius_m = accuracy_m

                cache[q_key] = {
                    "lat": lat,
                    "lon": lon,
                    "ts": int(time.time()),
                    "method": geocode_method,
                    "accuracy_m": accuracy_m,
                    "radius_m": radius_m,
                    "q_norm": q_norm,
                }

        # cap radius if configured (keeps map sane)
        radius_m = min(int(radius_m), int(MAX_RADIUS)) if MAX_RADIUS > 0 else int(radius_m)
        accuracy_m = min(int(accuracy_m), int(MAX_RADIUS)) if MAX_RADIUS > 0 else int(accuracy_m)

        media_urls = [
            a.get("url")
            for a in attachments
            if a.get("type") == "image" and a.get("url")
        ]

        pending_item = {
            "id": item_id,
            "status_id": str(status_id),
            "status": "PENDING",
            "event": event,          # "present" or "removed"
            "tag": tag,
            "source": url,
            "created_at": st.get("created_at"),
            "seen_date": seen_date,

            "lat": float(lat),
            "lon": float(lon),

            "accuracy_m": int(accuracy_m),
            "radius_m": int(radius_m),
            "geocode_method": geocode_method,
            "location_text": location_text,

            "sticker_type": sticker_type,
            "media": media_urls,
        }

        pending.append(pending_item)
        pending_by_source[url] = pending_item
        added_pending += 1

    # ========================================================
    # PUBLISH (approved via FAV) → reports.geojson
    # ========================================================
    still_pending: List[Dict[str, Any]] = []
    for item in pending:
        if item.get("status") != "PENDING":
            continue

        item_id = str(item.get("id", ""))
        if not item_id or item_id in reports_ids:
            continue

        ok = is_approved_by_fav(cfg, str(item["status_id"]))
        if not ok:
            still_pending.append(item)
            time.sleep(DELAY_FAV_CHECK)
            continue

        # build new feature (candidate)
        seen_date = str(item.get("seen_date") or today_iso())
        new_status = "removed" if item.get("event") == "removed" else "present"
        new_removed_at = seen_date if new_status == "removed" else None

        new_lat = float(item["lat"])
        new_lon = float(item["lon"])
        new_accuracy = int(item.get("accuracy_m", ACC_DEFAULT))
        new_radius = int(item.get("radius_m", new_accuracy))
        new_radius = min(new_radius, int(MAX_RADIUS)) if MAX_RADIUS > 0 else new_radius
        new_type = norm_type(item.get("sticker_type"))
        new_media = list(item.get("media") or [])

        existing = match_existing_feature(
            reports,
            new_lat=new_lat,
            new_lon=new_lon,
            new_radius_m=new_radius,
            new_type=new_type,
            default_radius_m=ACC_DEFAULT,
        )

        if existing:
            merge_into_existing(
                existing,
                new_status=new_status,
                seen_date=seen_date,
                new_removed_at=new_removed_at,
                new_accuracy_m=new_accuracy,
                new_radius_m=new_radius,
                new_type=new_type,
                new_media=new_media,
            )
        else:
            feat = make_product_feature(
                item_id=item_id,
                source_url=str(item.get("source") or ""),
                status=new_status,
                sticker_type=(item.get("sticker_type") or "unknown"),
                seen_date=seen_date,
                lat=new_lat,
                lon=new_lon,
                accuracy_m=new_accuracy,
                radius_m=new_radius,
                geocode_method=str(item.get("geocode_method") or "nominatim"),
                location_text=str(item.get("location_text") or ""),
                media=new_media,
                stale_after_days=stale_after_days,
                removed_at=new_removed_at,
            )
            reports["features"].append(feat)
            reports_ids.add(item_id)

        published += 1
        time.sleep(DELAY_FAV_CHECK)

    # ========================================================
    # STALE PASS
    # ========================================================
    apply_stale_rule(reports, stale_after_days)

    # ========================================================
    # SAVE
    # ========================================================
    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, still_pending)
    save_json(REPORTS_PATH, reports)

    print(f"Added pending: {added_pending} | Published: {published} | Pending left: {len(still_pending)}")


if __name__ == "__main__":
    main()
