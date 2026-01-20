#!/usr/bin/env python3
# Heatmap of Fascism — Product Bot (FINAL, CLEAN)
# Approval ONLY via FAV by allowed_reviewers
# NO trusted_author, NO author-based logic

import json
import re
import time
import math
import pathlib
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List, Iterable

# =========================
# PATHS
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
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")
RE_CROSS = re.compile(r"^(.+?)\s*(?:/| x | & )\s*(.+?)\s*,\s*(.+)$", re.I)
RE_INTERSECTION = re.compile(r"intersection of (.+?) and (.+?), (.+)", re.I)
RE_STICKER_TYPE = re.compile(r"(?im)^#sticker_type\s*:\s*([^\n#]+)")

# =========================
# CONSTANTS
# =========================
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

DELAY_TAG = 0.2
DELAY_FAV = 0.4
DELAY_GEOCODE = 1.0

# =========================
# IO
# =========================
def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_reports():
    if not REPORTS_PATH.exists():
        save_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})

# =========================
# HELPERS
# =========================
def today():
    return datetime.now(timezone.utc).date().isoformat()

def strip_html(s):
    s = re.sub(r"<br\s*/?>", "\n", s)
    return re.sub(r"<[^>]+>", "", s).strip()

def normalize(q):
    return (
        q.replace("ß", "ss")
         .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
         .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    )

def has_image(media):
    return any(m.get("type") == "image" for m in media or [])

def parse_sticker_type(text):
    m = RE_STICKER_TYPE.search(text)
    return m.group(1).strip().lower() if m else "unknown"

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# =========================
# LOCATION PARSE
# =========================
def parse_location(text):
    m = RE_COORDS.search(text)
    if m:
        return (float(m.group(1)), float(m.group(2))), None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        m = RE_ADDRESS.match(line)
        if m:
            return None, f"{m.group(1)} {m.group(2)}, {m.group(3)}"

        m = RE_CROSS.match(line)
        if m:
            return None, f"intersection of {m.group(1)} and {m.group(2)}, {m.group(3)}"

    return None, None

# =========================
# GEOCODING
# =========================
def geocode_nominatim(q, ua):
    r = requests.get(
        NOMINATIM_URL,
        params={"q": q, "format": "json", "limit": 1},
        headers={"User-Agent": ua},
        timeout=20
    )
    r.raise_for_status()
    j = r.json()
    if not j:
        return None
    return float(j[0]["lat"]), float(j[0]["lon"])

def geocode_intersection(city, a, b, ua):
    query = f"""
[out:json][timeout:25];
area["name"="{city}"]["boundary"="administrative"]->.a;
way(area.a)["name"="{a}"]["highway"]->.w1;
way(area.a)["name"="{b}"]["highway"]->.w2;
node(w.w1)(w.w2);
out body;
"""
    for ep in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(ep, data=query, headers={"User-Agent": ua}, timeout=35)
            if r.status_code != 200:
                continue
            for el in r.json().get("elements", []):
                if el["type"] == "node":
                    return el["lat"], el["lon"]
        except Exception:
            pass
    return None

def geocode(query, ua):
    m = RE_INTERSECTION.match(query)
    if m:
        a, b, city = m.groups()
        res = geocode_intersection(city, a, b, ua)
        if res:
            return res, "overpass"
    try:
        res = geocode_nominatim(query, ua)
        if res:
            return res, "nominatim"
    except Exception:
        pass
    return None, "none"

# =========================
# MASTODON
# =========================
def get_timeline(cfg, tag):
    url = f"{cfg['instance_url'].rstrip('/')}/api/v1/timelines/tag/{tag}"
    r = requests.get(url, headers={"Authorization": f"Bearer {cfg['access_token']}"})
    r.raise_for_status()
    return r.json()

def fav_by(cfg, status_id):
    url = f"{cfg['instance_url'].rstrip('/')}/api/v1/statuses/{status_id}/favourited_by"
    r = requests.get(url, headers={"Authorization": f"Bearer {cfg['access_token']}"})
    r.raise_for_status()
    return r.json()

def approved(cfg, status_id):
    allowed = {a.lower() for a in cfg.get("allowed_reviewers", [])}
    if not allowed:
        return False
    try:
        for acc in fav_by(cfg, status_id):
            name = (acc.get("acct") or "").split("@")[0].lower()
            if name in allowed:
                return True
    except Exception:
        pass
    return False

# =========================
# MAIN
# =========================
def main():
    cfg = load_json(CFG_PATH, None)
    secrets = load_json(SECRETS_PATH, None)
    if not cfg or not secrets:
        raise SystemExit("Missing config.json or secrets.json")

    cfg["access_token"] = secrets["access_token"]
    ensure_reports()

    reports = load_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})
    cache = load_json(CACHE_PATH, {})
    pending = load_json(PENDING_PATH, [])

    published = added = 0

    for tag, event in cfg["hashtags"].items():
        for st in get_timeline(cfg, tag):
            sid, url = st.get("id"), st.get("url")
            if not sid or not url:
                continue

            text = strip_html(st.get("content", ""))
            if not has_image(st.get("media_attachments", [])):
                continue

            coords, q = parse_location(text)
            if not coords and not q:
                continue

            created = st.get("created_at", "")[:10] or today()
            stype = parse_sticker_type(text)

            if coords:
                lat, lon = coords
                acc = rad = 10
                method = "gps"
            else:
                qn = normalize(q)
                if q in cache:
                    lat, lon = cache[q]["lat"], cache[q]["lon"]
                    acc = rad = cache[q]["accuracy_m"]
                    method = cache[q]["method"]
                else:
                    res, method = geocode(qn, cfg["user_agent"])
                    if not res:
                        continue
                    lat, lon = res
                    acc = rad = 25 if method != "overpass" else 10
                    cache[q] = {"lat": lat, "lon": lon, "accuracy_m": acc, "method": method}

            if not approved(cfg, sid):
                pending.append({"status_id": sid, "url": url})
                continue


            reports["features"].append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": f"masto-{sid}",
                    "source": url,
                    "status": "removed" if event == "removed" else "present",
                    "sticker_type": stype,
                    "first_seen": created,
                    "last_seen": created,
                    "seen_count": 1,
                    "removed_at": created if event == "removed" else None,
                    "stale_after_days": 30,
                    "accuracy_m": acc,
                    "radius_m": rad,
                    "geocode_method": method,
                    "location_text": q or f"{lat},{lon}",
                    "media": [m["url"] for m in st["media_attachments"] if m.get("url")],
                    "notes": ""
                }
            })
            published += 1
        time.sleep(DELAY_TAG)
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


    save_json(REPORTS_PATH, reports)
    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, pending)

    print(f"Published: {published}")

if __name__ == "__main__":
    main()
