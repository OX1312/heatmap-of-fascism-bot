#!/usr/bin/env python3
# Heatmap of Fascism - Product Ingest + Review via FAV (multi-hashtag, worldwide, robust)
#
# Product rules:
# - config.json (tracked): rules, hashtags, reviewers, accuracy targets
# - secrets.json (local, NOT tracked): {"access_token":"..."}  (gitignored)
#
# Output:
# - reports.geojson = single source of truth (FeatureCollection)
# - Each published feature follows the fixed "product schema"
#
# Step 2 implemented:
# - Dupe matching on publish:
#   If distance <= max(existing.radius_m, new.radius_m) AND sticker_type matches OR one is "unknown"
#   => update existing feature (last_seen, seen_count, status, removed_at, media)

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
CACHE_PATH = ROOT / "cache_geocode.json"
PENDING_PATH = ROOT / "pending.json"
REPORTS_PATH = ROOT / "reports.geojson"

# =========================
# REGEX
# =========================
RE_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")  # "Street 12, City"
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

def geocode_query_worldwide(query: str, user_agent: str) -> Tuple[Optional[Tuple[float, float]], str]:
    """
    Returns (coords, method)
    method: "overpass" | "nominatim" | "fallback" | "none"
    """
    m = RE_INTERSECTION.match(query)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        city = m.group(3).strip()

        res = overpass_intersection(city=city, a=a, b=b, user_agent=user_agent)
        if res:
            return res, "overpass"

        try:
            res = geocode_nominatim(query, user_agent)
            if res:
                return res, "nominatim"
        except Exception:
            pass

        try:
            res = geocode_nominatim(f"{a}, {city}", user_agent)
            if res:
                return res, "fallback"
        except Exception:
            pass
        try:
            res = geocode_nominatim(f"{b}, {city}", user_agent)
            if res:
                return res, "fallback"
        except Exception:
            pass

        return None, "none"

    try:
        res = geocode_nominatim(query, user_agent)
        if res:
            return res, "nominatim"
    except Exception:
        pass
    return None, "none"


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
    created_date: str,
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

    cfg["access_token"] = secrets["access_token"]

    stale_after_days = int(cfg.get("stale_after_days", 30))
    acc_cfg = cfg.get("accuracy") or {}
    ACC_INTERSECTION = int(acc_cfg.get("intersection_m", 10))
    ACC_DEFAULT = int(acc_cfg.get("default_m", 25))
    ACC_FALLBACK = int(acc_cfg.get("fallback_m", 50))

    cache: Dict[str, Any] = load_json(CACHE_PATH, {})
    pending: List[Dict[str, Any]] = load_json(PENDING_PATH, [])
    reports = load_reports()
    reports_ids = reports_id_set(reports)

    pending_by_source = {p.get("source"): p for p in pending if p.get("source")}

    added_pending = 0
    published = 0

    # ---- ingest new to pending ----
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
            accuracy_m = ACC_INTERSECTION
            radius_m = ACC_INTERSECTION
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

                if method == "overpass":
                    accuracy_m = ACC_INTERSECTION
                elif method == "fallback":
                    accuracy_m = ACC_FALLBACK
                else:
                    accuracy_m = ACC_DEFAULT

                radius_m = accuracy_m  # product rule: circle size = uncertainty

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
            "status_id": status_id,
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

    # ---- publish approved -> reports.geojson (with dupe matching) ----
    still_pending: List[Dict[str, Any]] = []
    for item in pending:
        if item.get("status") != "PENDING":
            continue

        item_id = str(item.get("id"))
        if item_id in reports_ids:
            continue

        ok = is_approved_author = ((st.get("account") or {}).get("acct") or "").split("@")[0].lower()
        ok = is_approved_by_fav(cfg, str(item["status_id"]))
        if ok:
            new_status = ("removed" if item.get("event") == "removed" else "present")
            new_removed_at = item.get("removed_at", None)

            new_feat = make_product_feature(
                item_id=item_id,
                source_url=str(item["source"]),
                status=new_status,
                sticker_type=str(item.get("sticker_type") or "unknown"),
                created_date=str(item.get("created_date") or today_iso()),
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

            new_p = new_feat["properties"]
            new_lat = float(item["lat"])
            new_lon = float(item["lon"])
            new_r = int(new_p.get("radius_m") or new_p.get("accuracy_m") or ACC_DEFAULT)
            new_type = norm_type(new_p.get("sticker_type"))

            matched = False

            for f in reports["features"]:
                p = f.get("properties") or {}
                coords = (f.get("geometry") or {}).get("coordinates") or []
                if len(coords) != 2:
                    continue

                ex_lon, ex_lat = float(coords[0]), float(coords[1])
                ex_r = int(p.get("radius_m") or p.get("accuracy_m") or ACC_DEFAULT)
                ex_type = norm_type(p.get("sticker_type"))

                # type rule: must match OR one side is unknown
                if not (new_type == "unknown" or ex_type == "unknown" or new_type == ex_type):
                    continue

                dist = haversine_m(new_lat, new_lon, ex_lat, ex_lon)
                if dist <= max(ex_r, new_r):
                    # UPDATE existing feature
                    created_date = str(new_p.get("last_seen") or new_p.get("first_seen") or today_iso())

                    # first_seen stays
                    p["last_seen"] = created_date
                    p["seen_count"] = int(p.get("seen_count", 1)) + 1

                    # if it was stale but new report confirms, bring back
                    if new_status == "present":
                        p["status"] = "present"
                        p["removed_at"] = None
                    elif new_status == "removed":
                        p["status"] = "removed"
                        p["removed_at"] = new_removed_at

                    # if existing type unknown but new provides something, promote it
                    if ex_type == "unknown" and new_type != "unknown":
                        p["sticker_type"] = new_p.get("sticker_type")

                    # keep the tighter radius if we have more precise info
                    p["accuracy_m"] = min(int(p.get("accuracy_m", ex_r)), int(new_p.get("accuracy_m", new_r)))
                    p["radius_m"] = min(int(p.get("radius_m", ex_r)), int(new_p.get("radius_m", new_r)))

                    # merge media without duplicates
                    media = list(p.get("media") or [])
                    seen = set(media)
                    for u in list(new_p.get("media") or []):
                        if u and u not in seen:
                            media.append(u)
                            seen.add(u)
                    p["media"] = media

                    matched = True
                    published += 1
                    break

            if not matched:
                reports["features"].append(new_feat)
                reports_ids.add(item_id)
                published += 1
        else:
            still_pending.append(item)

        time.sleep(DELAY_FAV_CHECK)

    apply_stale_rule(reports, stale_after_days)

    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, still_pending)
    save_json(REPORTS_PATH, reports)

    print(f"Added pending: {added_pending} | Published: {published} | Pending left: {len(still_pending)}")


if __name__ == "__main__":
    main()
