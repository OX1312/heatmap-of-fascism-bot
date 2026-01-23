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


# =========================
# VERSION / MODES
# =========================
__version__ = "0.2.0"

import ssl
import certifi
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
import json
import re
import time
import pathlib
import math
import subprocess
from typing import Optional, Tuple, Dict, Any, List, Iterable
from datetime import datetime, timezone

import requests

# --- TIMESTAMP_PRINT ---
import builtins as _builtins
import datetime as _dt
_print = _builtins.print
def print(*args, **kwargs):
    _print(_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *args, **kwargs)
# --- /TIMESTAMP_PRINT ---

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
RE_STICKER_TYPE = re.compile(r"(?im)^\s*#sticker_type\s*:?\s*([^\n#@]{1,200}?)(?=\s*(?:(ort|location|place)\s*:|@|#|$))")

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
    # Mastodon liefert HTML (<p>, </p>, <br>, Links/Mentions). Wir normalisieren das zu Zeilen.
    s = s or ""
    s = re.sub(r"</p>\s*<p[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def contains_required_mention(text: str, required_mentions) -> bool:
    """True if text contains a mention like @HeatmapofFascism (case-insensitive)."""
    t = (text or "")
    for m in (required_mentions or []):
        if not m:
            continue
        base = str(m).strip().lstrip("@").split("@")[0]
        if not base:
            continue
        # Match @name or @name@instance anywhere (word boundary)
        if re.search(rf"(?i)(?:^|\s)@{re.escape(base)}(?:@[-\w\.]+)?\b", t):
            return True
    return False
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
# SNAP TO PUBLIC WAYS
# =========================
def _xy_m(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """Equirectangular projection around (lat0, lon0) -> meters."""
    R = 6371000.0
    x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R
    return x, y

def _latlon_from_xy(lat0: float, lon0: float, x: float, y: float) -> tuple[float, float]:
    R = 6371000.0
    lat = lat0 + math.degrees(y / R)
    lon = lon0 + math.degrees(x / (R * math.cos(math.radians(lat0))))
    return lat, lon

def _nearest_point_on_polyline_m(
    lat0: float, lon0: float,
    pts: list[tuple[float,float]],
    qlat: float, qlon: float
) -> tuple[float,float,float,tuple[float,float]]:
    """
    Returns: (best_lat, best_lon, best_dist_m, best_seg_dir_xy_unit)
    best_seg_dir_xy_unit is (ux,uy) of the segment direction in meters.
    """
    qx, qy = _xy_m(lat0, lon0, qlat, qlon)
    best = None

    for i in range(len(pts) - 1):
        a_lat, a_lon = pts[i]
        b_lat, b_lon = pts[i + 1]
        ax, ay = _xy_m(lat0, lon0, a_lat, a_lon)
        bx, by = _xy_m(lat0, lon0, b_lat, b_lon)
        dx, dy = bx - ax, by - ay
        seg2 = dx*dx + dy*dy
        if seg2 <= 1e-9:
            continue
        t = ((qx - ax)*dx + (qy - ay)*dy) / seg2
        if t < 0.0: t = 0.0
        if t > 1.0: t = 1.0
        px, py = ax + t*dx, ay + t*dy
        dist = ((qx - px)**2 + (qy - py)**2) ** 0.5

        seg_len = (seg2 ** 0.5)
        ux, uy = dx/seg_len, dy/seg_len

        if best is None or dist < best[2]:
            plat, plon = _latlon_from_xy(lat0, lon0, px, py)
            best = (plat, plon, dist, (ux, uy))

    if best is None:
        return qlat, qlon, float("inf"), (1.0, 0.0)
    return best

def snap_to_public_way(lat: float, lon: float, user_agent: str) -> tuple[float, float, str]:
    """
    Snap point onto nearest *public* way so we don't land in road center / private areas.
    Prefer walkable ways; if only road found, offset to the side.
    Also avoid ending up on/inside buildings (very small-radius building check).
    Returns: (lat, lon, note) where note is "" if no snap happened.
    """
    # Base search radius in meters (OSM density varies)
    R_M = 120
    # If no walkable way found, run a second pass for walkways with bigger radius
    R_WALK_M = 220

    # Offset when we only have a road (meters, sideways)
    OFFSET_ROAD_M = 10.0
    # Extra push if we still end up near a building
    OFFSET_BUILDING_M = 14.0

    lat0, lon0 = lat, lon

    def is_public(tags: dict) -> bool:
        if not isinstance(tags, dict):
            return True
        acc = (tags.get("access") or "").strip().lower()
        if acc in {"private","no"}:
            return False
        foot = (tags.get("foot") or "").strip().lower()
        if foot in {"no","private"}:
            return False
        # common private-ish service patterns
        if (tags.get("highway") or "").strip().lower() == "service":
            svc = (tags.get("service") or "").strip().lower()
            if svc in {"driveway","parking_aisle"}:
                return False
        indoor = (tags.get("indoor") or "").strip().lower()
        if indoor in {"yes","1","true"}:
            return False
        return True

    def building_nearby(qlat: float, qlon: float, r_m: int = 6) -> bool:
        # Very small radius: we only want to catch "landed on building" cases.
        q = f"""
[out:json][timeout:25];
(
  way(around:{r_m},{qlat},{qlon})["building"];
  relation(around:{r_m},{qlat},{qlon})["building"];
);
out ids;
""".strip()
        data = _overpass_post(q, user_agent)
        if not data or not isinstance(data, dict):
            return False
        elems = data.get("elements") or []
        return bool(elems)

    def fetch_highways(r_m: int, only_walk: bool) -> list:
        if only_walk:
            # only walkable highway types
            q = f"""
[out:json][timeout:25];
(
  way(around:{r_m},{lat0},{lon0})["highway"~"^(footway|path|pedestrian|steps|cycleway)$"];
);
out tags geom;
""".strip()
        else:
            q = f"""
[out:json][timeout:25];
(
  way(around:{r_m},{lat0},{lon0})["highway"];
);
out tags geom;
""".strip()
        data = _overpass_post(q, user_agent)
        if not data or not isinstance(data, dict):
            return []
        elems = data.get("elements") or []
        if not isinstance(elems, list):
            return []
        return elems

    # Preference order: walkable ways first
    walk_hw = {"footway","path","pedestrian","steps","cycleway"}
    road_hw  = {"living_street","residential","service","unclassified","tertiary","secondary","primary"}

    def collect_candidates(elems) -> list:
        cands: list[tuple[str, list[tuple[float,float]], dict]] = []
        for e in elems:
            if e.get("type") != "way":
                continue
            tags = e.get("tags") or {}
            hw = (tags.get("highway") or "").strip().lower()
            if not hw:
                continue
            if not is_public(tags):
                continue
            geom = e.get("geometry") or []
            if not isinstance(geom, list) or len(geom) < 2:
                continue
            pts = []
            ok = True
            for g in geom:
                if not isinstance(g, dict) or "lat" not in g or "lon" not in g:
                    ok = False
                    break
                pts.append((float(g["lat"]), float(g["lon"])))
            if not ok:
                continue
            kind = "walk" if hw in walk_hw else ("road" if hw in road_hw else "other")
            if kind == "other":
                continue
            cands.append((hw, pts, tags))
        return cands

    elems = fetch_highways(R_M, only_walk=False)
    cands = collect_candidates(elems)
    if not cands:
        return lat, lon, ""

    # Find best walk candidate, else best road candidate
    best = None  # (lat, lon, dist, seg_dir, hw, kind)
    for hw, pts, tags in cands:
        kind = "walk" if hw in walk_hw else "road"
        plat, plon, dist, segdir = _nearest_point_on_polyline_m(lat0, lon0, pts, lat0, lon0)
        if best is None:
            best = (plat, plon, dist, segdir, hw, kind)
        else:
            # Prefer walk over road; within same kind choose nearest
            if best[5] != "walk" and kind == "walk":
                best = (plat, plon, dist, segdir, hw, kind)
            elif best[5] == kind and dist < best[2]:
                best = (plat, plon, dist, segdir, hw, kind)

    if best is None:
        return lat, lon, ""

    plat, plon, dist, (ux,uy), hw, kind = best

    # If we ended up with road-only: try to find walkways in a second pass
    if kind == "road":
        elems2 = fetch_highways(R_WALK_M, only_walk=True)
        cands2 = collect_candidates(elems2)
        best_walk = None
        for hw2, pts2, tags2 in cands2:
            plat2, plon2, dist2, segdir2 = _nearest_point_on_polyline_m(lat0, lon0, pts2, lat0, lon0)
            if best_walk is None or dist2 < best_walk[2]:
                best_walk = (plat2, plon2, dist2, segdir2, hw2, "walk")
        # Accept if not crazy far
        if best_walk is not None and best_walk[2] <= 45.0:
            plat, plon, dist, (ux,uy), hw, kind = best_walk

    note = f"snap_{kind}:{hw}"

    # Road offset: push sideways off the centerline (towards reporter side if possible)
    if kind == "road":
        # perpendicular normal of segment
        nx, ny = (-uy, ux)

        # Choose side that points roughly towards the original point (reduces wrong-side jumps)
        sx, sy = _xy_m(lat0, lon0, plat, plon)   # snapped -> meters
        ox, oy = _xy_m(lat0, lon0, lat0, lon0)   # original -> (0,0)
        vx, vy = (ox - sx), (oy - sy)            # snapped -> original
        if (vx*nx + vy*ny) < 0:
            nx, ny = (-nx, -ny)

        sx2, sy2 = (sx + nx*OFFSET_ROAD_M), (sy + ny*OFFSET_ROAD_M)
        plat, plon = _latlon_from_xy(lat0, lon0, sx2, sy2)
        note = f"snap_road_offset:{hw}"

    # Building avoidance (tiny radius check)
    if building_nearby(plat, plon, r_m=6):
        # push further along last known normal if road, else small push perpendicular to walk segment
        if kind == "road":
            # reuse same perpendicular direction by recomputing approx from last segment dir
            nx, ny = (-uy, ux)
            sx, sy = _xy_m(lat0, lon0, plat, plon)
            sx2, sy2 = (sx + nx*OFFSET_BUILDING_M), (sy + ny*OFFSET_BUILDING_M)
            plat, plon = _latlon_from_xy(lat0, lon0, sx2, sy2)
            note += "|avoid_building"
        else:
            # walk: minimal nudge to reduce "inside building" hits
            nx, ny = (-uy, ux)
            sx, sy = _xy_m(lat0, lon0, plat, plon)
            sx2, sy2 = (sx + nx*4.0), (sy + ny*4.0)
            plat, plon = _latlon_from_xy(lat0, lon0, sx2, sy2)
            note += "|avoid_building"

    return plat, plon, note

# =========================
# LOCATION PARSE
# =========================

def heuristic_fix_crossing(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return q

    # A / B Hamburg  ->  A / B, Hamburg  (missing comma before city)
    if ("," not in q) and any(sep in q for sep in (" / ", " x ", " & ")):
        parts = q.rsplit(" ", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return f"{parts[0].strip()}, {parts[1].strip()}"

    # A, B Hamburg   ->  A / B, Hamburg  (comma-separated streets, city at end)
    if ("/" not in q) and (" x " not in q) and (" & " not in q) and ("," in q):
        parts = [p.strip() for p in q.split(",") if p.strip()]
        if len(parts) == 2:
            a = parts[0]
            rest = parts[1]
            rest_parts = rest.rsplit(" ", 1)
            if len(rest_parts) == 2 and rest_parts[0].strip() and rest_parts[1].strip():
                b = rest_parts[0].strip()
                city = rest_parts[1].strip()
                return f"{a} / {b}, {city}"
        elif len(parts) >= 3:
            a = parts[0]
            city = parts[-1]
            b = ", ".join(parts[1:-1]).strip()
            if b and city:
                return f"{a} / {b}, {city}"

    return q

def parse_location(text: str) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """
    Returns:
      (lat, lon) if coords found anywhere in text
      OR a query string if address/crossing/street+city found in ANY non-hashtag line
      OR (None, None) if invalid
    """
    m = RE_COORDS.search(text)
    if m:
        return (float(m.group(1)), float(m.group(2))), None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def is_pure_mentions(ln: str) -> bool:
        # "@user" or "@user @user2"
        import re
        return bool(re.fullmatch(r"(?:@\w+(?:@\w+)?)(?:\s+@\w+(?:@\w+)?)*", ln))

    # Scan for the first line that looks like a location (skip hashtags + pure-mention lines)
    for ln in lines:
        low = ln.lower()
        if low.startswith("#"):
            continue
        if low.startswith("@") and is_pure_mentions(ln):
            continue

        candidate = heuristic_fix_crossing(normalize_location_line(ln))

        # Heuristic: allow missing comma before city for crossings.
        # Examples: "A / B Hamburg" -> "A / B, Hamburg"
        if ("," not in candidate) and any(sep in candidate for sep in (" / ", " x ", " & ")):
            parts = candidate.rsplit(" ", 1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                candidate = f"{parts[0].strip()}, {parts[1].strip()}"

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

class StatusDeleted(Exception):
    pass


def status_exists(cfg: dict, status_id: str) -> bool:
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/statuses/{status_id}"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    r = requests.get(url, headers=headers, timeout=MASTODON_TIMEOUT_S)
    if r.status_code in (404, 410):
        return False
    r.raise_for_status()
    return True

def prune_deleted_published(cfg: dict, reports: dict) -> int:
    """
    Hotfix policy:
    - NEVER delete map pins when the source post disappears.
    - If we are confident the source is deleted, keep the feature but mark it:
        source_deleted=true, source_deleted_at=ISO timestamp
      and downgrade status to 'unknown' (keeps the pin but reflects uncertainty).
    Returns number of newly-marked deleted features.
    """
    from datetime import datetime, timezone

    feats = reports.get("features") or []
    marked = 0
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    for f in feats:
        props = (f.get("properties") or {})
        item_id = str(props.get("id") or "")
        status_id = str(props.get("status_id") or "")

        # fallback: derive from "masto-<digits>"
        if not status_id and item_id.startswith("masto-"):
            status_id = item_id.split("masto-", 1)[1].strip()

        if not status_id.isdigit():
            continue

        # already marked -> nothing to do
        if props.get("source_deleted") is True:
            continue

        # Use robust fetch_status if available; only mark when confident.
        try:
            st = fetch_status(cfg, cfg.get("instance_url", ""), status_id)
            # st != None means "exists or accessible" -> do nothing
            if st is not None:
                continue
        except StatusDeleted:
            # confident deletion
            props["source_deleted"] = True
            props["source_deleted_at"] = ts
            # keep pin, downgrade certainty
            props["status"] = "unknown"
            # keep id for traceability
            try:
                props["status_id"] = int(status_id)
            except Exception:
                props["status_id"] = status_id
            f["properties"] = props
            marked += 1
        except Exception:
            # API/network error -> do nothing (never delete/mark)
            pass

    return marked

def get_favourited_by(cfg: Dict[str, Any], status_id: str) -> List[Dict[str, Any]]:
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/statuses/{status_id}/favourited_by"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    r = requests.get(url, headers=headers, timeout=MASTODON_TIMEOUT_S)

    if r.status_code in (404, 410):
        raise StatusDeleted(f"status {status_id} deleted ({r.status_code})")

    r.raise_for_status()
    return r.json()


def fetch_status(cfg: Dict[str, Any], instance_url: str, status_id: str) -> Optional[Dict[str, Any]]:
    """
    Robust delete check:
    1) Public GET (no token). If 200 -> exists (even cross-instance).
       If 404/410 -> could be deleted OR private -> try auth if possible.
    2) Auth GET (only if same instance + token). If 200 -> exists.
       If 404/410 -> deleted.
    Returns status dict on success, None if cannot decide / transient error.
    Raises StatusDeleted only when we are confident (auth 404/410).
    """
    inst = (instance_url or "").rstrip("/")
    if not inst:
        return None

    url = f"{inst}/api/v1/statuses/{status_id}"
    maybe_deleted = False

    # --- 1) Public probe (no auth) ---
    try:
        r0 = requests.get(url, timeout=MASTODON_TIMEOUT_S)
        if r0.status_code == 200:
            return r0.json()
        if r0.status_code in (404, 410):
            maybe_deleted = True
        # 401/403/429/5xx -> inconclusive, fall through to auth if possible
    except Exception:
        # network/transient -> inconclusive
        pass

    # --- 2) Auth probe (only if we can) ---
    inst_cfg = (cfg.get("instance_url") or "").rstrip("/")
    token = cfg.get("access_token")

    if inst_cfg and token and inst == inst_cfg:
        try:
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.get(url, headers=headers, timeout=MASTODON_TIMEOUT_S)
            if r.status_code in (404, 410):
                raise StatusDeleted(f"status {status_id} deleted ({r.status_code})")
            if r.status_code == 200:
                return r.json()
            r.raise_for_status()
            return None
        except StatusDeleted:
            raise
        except Exception:
            return None

    # Public 404/410 is ambiguous (could be private/blocked).
    # Policy: Only treat as deleted if we can confirm via AUTH on the origin instance.
    if maybe_deleted:
        inst_cfg = (cfg.get("instance_url") or "").rstrip("/")
        if inst_cfg and inst.rstrip("/") == inst_cfg and cfg.get("access_token"):
            try:
                headers = {"Authorization": f"Bearer {cfg['access_token']}"}
                r = requests.get(url, headers=headers, timeout=MASTODON_TIMEOUT_S)
                if r.status_code in (404, 410):
                    raise StatusDeleted(f"status {status_id} deleted (auth {r.status_code})")
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (401, 403):
                    return None
                r.raise_for_status()
            except StatusDeleted:
                raise
            except Exception:
                return None
        return None

    return None

def verify_deleted_features(reports: Dict[str, Any], cfg: Dict[str, Any], budget: int = 200) -> tuple[int, int]:
    """
    Policy:
    - If the *source post is deleted* (404/410), the map pin MUST disappear.
      => HARD DROP the feature from reports.geojson.
    - #sticker_removed is handled elsewhere and keeps the pin (status="removed").
    Returns: (checked, removed)
    """
    feats = reports.get("features") or []
    if not isinstance(feats, list) or not feats:
        return 0, 0

    cands = []
    for idx, f in enumerate(feats):
        props = (f or {}).get("properties") or {}
        inst, sid = derive_status_ref(props)
        if not sid or not inst:
            continue
        if props.get("status") not in ("present", "removed", "unknown"):
            continue
        # keep ordering stable; if field missing -> 0
        lastv = int(props.get("last_verify_ts") or 0)
        cands.append((lastv, idx, sid, inst))

    if not cands:
        return 0, 0

    cands.sort(key=lambda x: x[0])  # oldest first
    budget = max(0, int(budget))
    checked = 0
    removed = 0
    to_drop = set()

    for _, idx, sid, inst in cands[:budget]:
        checked += 1
        try:
            _ = fetch_status(cfg, inst, str(sid))
            # NOTE: do NOT write last_verify_ts into reports.geojson (keeps repo clean)
        except StatusDeleted as e:
            to_drop.add(idx)
            removed += 1
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            print(f"{ts} verify_drop_deleted status_id={sid} reason={e}")
        except Exception:
            # transient error -> ignore (also don't stamp last_verify_ts)
            pass

    if to_drop:
        reports["features"] = [f for i, f in enumerate(feats) if i not in to_drop]

    if checked:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"{ts} verify_deleted checked={checked} removed={removed}")

    return checked, removed


def derive_status_ref(props: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (instance_url, status_id) from props.
    Prefers explicit fields; falls back to parsing props["source"] URL.
    """
    sid = props.get("status_id")
    inst = props.get("instance_url")
    if sid and inst:
        return (str(inst).rstrip("/"), str(sid).strip())

    src = (props.get("source") or "").strip()
    if not src:
        return (None, None)

    # patterns:
    # https://host/@user/123
    # https://host/web/statuses/123
    m = re.match(r"^https?://([^/]+)/(?:@[^/]+|web/statuses)/(\d+)", src)
    if m:
        host, sid2 = m.group(1), m.group(2)
        return (f"https://{host}", sid2)

    return (None, None)



def _strip_html_text(html_s: str) -> str:
    if not html_s:
        return ""
    # very small+robust: remove tags + collapse whitespace
    import html as _html
    t = re.sub(r"<[^>]+>", " ", str(html_s))
    t = _html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _extract_flags_and_type(text: str) -> tuple[bool, bool, str]:
    t = (text or "").lower()
    has_removed = "#sticker_removed" in t
    has_seen = "#sticker_seen" in t

    # sticker_type: allow "#sticker_type: xxx" anywhere
    m = re.search(r"#sticker_type\s*:\s*([^\n\r#]+)", text or "", flags=re.IGNORECASE)
    stype = (m.group(1).strip() if m else "")
    return has_removed, has_seen, stype

def fetch_context(cfg: Dict[str, Any], instance_url: str, status_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch status context to read replies (descendants).
    Public-first; if 401/403, try auth only if same instance.
    Returns dict or None if cannot check.
    """
    inst_cfg = (cfg.get("instance_url") or "").rstrip("/")
    inst = (instance_url or "").rstrip("/")
    if not inst:
        return None

    url = f"{inst}/api/v1/statuses/{status_id}/context"

    # 1) public
    try:
        r = requests.get(url, timeout=MASTODON_TIMEOUT_S)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (404, 410):
            raise StatusDeleted(f"context {status_id} deleted ({r.status_code})")
        if r.status_code not in (401, 403):
            r.raise_for_status()
    except StatusDeleted:
        raise
    except Exception:
        pass

    # 2) auth (only same instance)
    if not inst_cfg or inst != inst_cfg:
        return None

    try:
        headers = {"Authorization": f"Bearer {cfg['access_token']}"}
        r = requests.get(url, headers=headers, timeout=MASTODON_TIMEOUT_S)
        if r.status_code in (404, 410):
            raise StatusDeleted(f"context {status_id} deleted ({r.status_code})")
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401,403):
            return None
        r.raise_for_status()
        return r.json()
    except StatusDeleted:
        raise
    except Exception:
        return None

def update_features_from_context(reports: Dict[str, Any], cfg: Dict[str, Any], budget: int = 100) -> int:
    """
    Budget-limited:
    - checks edits on the source status (sticker_type / removed / seen tags)
    - checks replies via /context for #sticker_seen / #sticker_removed
    Updates last_seen / removed_at / status / sticker_type.
    Returns: number of features changed.
    """
    feats = reports.get("features") or []
    if not isinstance(feats, list) or not feats:
        return 0

    # pick candidates with status_id+instance_url
    cands = []
    for idx, f in enumerate(feats):
        props = (f or {}).get("properties") or {}
        inst, sid = derive_status_ref(props)
        if not sid or not inst:
            continue
        # rotate by oldest context-check first
        lastc = int(props.get("last_context_ts") or 0)
        cands.append((lastc, idx, str(sid), str(inst)))

    if not cands:
        return 0

    cands.sort(key=lambda x: x[0])
    budget = max(0, int(budget))
    now = int(time.time())
    changed = 0

    def _ts(siso: str) -> str:
        # keep ISO seconds from API ("2026-...Z")
        return (siso or "").replace("Z", "+00:00")

    for _, idx, sid, inst in cands[:budget]:
        f = feats[idx]
        props = f["properties"]
        old_status = props.get("status")
        old_last = props.get("last_seen")
        old_removed = props.get("removed_at")
        old_type = props.get("sticker_type") or ""

        # mark check timestamp regardless (prevents hammering)
        # --- 1) check source status (edits)
        try:
            st = fetch_status(cfg, inst, sid)
        except StatusDeleted as e:
            # source deleted: keep point, but mark unknown + note (do NOT delete)
            props["status"] = "unknown"
            n = (props.get("notes") or "")
            if "source deleted" not in n.lower():
                props["notes"] = (n + " | source deleted").strip(" |")
            changed += 1
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            print(f"{ts} source_deleted_keep status_id={sid} reason={e}")
            continue
        except Exception:
            st = None

        if st:
            txt = _strip_html_text(st.get("content") or "")
            has_removed, has_seen, stype = _extract_flags_and_type(txt)

            if stype and stype != old_type:
                props["sticker_type"] = stype
                changed += 1

            # If user edited source to removed/seen, treat as event at "edited_at" else created_at
            ev_time = _ts(st.get("edited_at") or st.get("created_at") or "")
            if has_removed and ev_time and ev_time != old_removed:
                props["status"] = "removed"
                props["removed_at"] = ev_time
                props["last_seen"] = ev_time
                changed += 1
            elif has_seen and ev_time and ev_time != old_last:
                props["last_seen"] = ev_time
                props["seen_count"] = int(props.get("seen_count") or 0) + 1
                if props.get("status") != "removed":
                    props["status"] = "present"
                changed += 1

        # --- 2) check replies (context descendants)
        try:
            ctx = fetch_context(cfg, inst, sid)
        except StatusDeleted:
            # already handled above if fetch_status caught it; ignore here
            ctx = None
        except Exception:
            ctx = None

        if ctx and isinstance(ctx, dict):
            desc = ctx.get("descendants") or []
            best_seen = None   # (iso, type)
            best_removed = None

            for d in desc:
                if not isinstance(d, dict):
                    continue
                d_txt = _strip_html_text(d.get("content") or "")
                has_removed, has_seen, stype = _extract_flags_and_type(d_txt)
                d_time = _ts(d.get("created_at") or "")
                if stype and stype != (props.get("sticker_type") or ""):
                    props["sticker_type"] = stype
                    changed += 1
                if has_seen and d_time:
                    if (best_seen is None) or (d_time > best_seen[0]):
                        best_seen = (d_time, )
                if has_removed and d_time:
                    if (best_removed is None) or (d_time > best_removed[0]):
                        best_removed = (d_time, )

            # Apply the newest event (removed wins if later)
            if best_removed and (not props.get("removed_at") or best_removed[0] > str(props.get("removed_at"))):
                props["status"] = "removed"
                props["removed_at"] = best_removed[0]
                props["last_seen"] = best_removed[0]
                changed += 1
            elif best_seen and (not props.get("last_seen") or best_seen[0] > str(props.get("last_seen"))):
                props["last_seen"] = best_seen[0]
                props["seen_count"] = int(props.get("seen_count") or 0) + 1
                if props.get("status") != "removed":
                    props["status"] = "present"
                changed += 1

        # normalize: removed implies not "present"
        if props.get("status") == "removed" and not props.get("removed_at"):
            props["removed_at"] = props.get("last_seen") or props.get("first_seen")

    if changed:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"{ts} context_update changed={changed} checked={min(len(cands), budget)}")

    return changed


def post_public_reply(cfg: Dict[str, Any], in_reply_to_id: str, text: str) -> bool:
    """Public reply under the original post (no DM). Best-effort."""
    try:
        instance = cfg["instance_url"].rstrip("/")
        url = f"{instance}/api/v1/statuses"
        headers = {"Authorization": f"Bearer {cfg['access_token']}"}
        data = {
            "status": text,
            "in_reply_to_id": str(in_reply_to_id),
            "visibility": "public",
        }
        r = requests.post(url, headers=headers, data=data, timeout=MASTODON_TIMEOUT_S)
        if r.status_code not in (200, 201):
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            body = (r.text or "")[:200].replace("\n", " ")
            print(f"{ts} reply FAILED in_reply_to={in_reply_to_id} http={r.status_code} body={body!r}")
            return False
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"{ts} reply OK in_reply_to={in_reply_to_id}")
        return True
    except Exception as e:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"{ts} reply ERROR in_reply_to={in_reply_to_id} err={e!r}")
        return False
def reply_once(cfg: Dict[str, Any], cache: Dict[str, Any], key: str, in_reply_to_id: str, text: str) -> bool:
    """Ensure we reply at most once per status+type. Persists in cache_geocode.json."""
    try:
        rep = cache.setdefault("_replies", {})
        if rep.get(key):
            return True
        ok = post_public_reply(cfg, in_reply_to_id, text)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        if ok:
            rep[key] = ts
            save_json(CACHE_PATH, cache)  # persist immediately to avoid spam on restart
            print(f"{ts} reply OK key={key} status={in_reply_to_id}")
        else:
            print(f"{ts} reply FAILED key={key} status={in_reply_to_id}")
        return ok
    except Exception as e:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"{ts} reply ERROR key={key} status={in_reply_to_id} err={e!r}")
        return False


def build_reply_ok() -> str:
    # A) Alles top
    return "Alles top – danke! Der Report ist drin und wird nach dem FAV auf die Karte übernommen. Alerta alerta 🖖"


def build_reply_pending() -> str:
    return "Report erkannt ✅ Bitte FAV (von trusted Accounts), dann kommt der Punkt auf die Karte. Alerta alerta 🖖"

def build_reply_improve(hints: list[str]) -> str:
    # B) Erkannt, aber besser möglich
    lines = ["Danke! Report erkannt ✅", "Optional für bessere Qualität:"]
    for h in (hints or [])[:5]:
        lines.append(f"• {h}")
    lines.append("Alerta alerta 🖖")
    return "\n".join(lines)

def build_reply_missing(missing: list[str]) -> str:
    # C) Es fehlt etwas -> neu posten
    lines = ["Ich kann den Report so noch nicht verarbeiten ❌", "Bitte neu posten mit:"]
    for m in (missing or [])[:6]:
        lines.append(f"• {m}")
    lines.append("Wichtig: Im Text muss @HeatmapofFascism stehen.")
    lines.append("Alerta alerta 🖖")
    return "\n".join(lines)

def build_needs_info_reply(location_text: str) -> str:
    # Geocode fehlgeschlagen -> präzisere Ortsangabe anfordern
    loc = (location_text or "").strip()
    loc_part = f' („{loc}“)' if loc else ""
    return (
        "Danke für die Meldung. Ich kann den Ort gerade nicht automatisch auflösen"
        + loc_part
        + ".\n"
        "Bitte antworte mit EINEM von diesen Formaten:\n"
        "• Koordinaten: 53.87, 10.68\n"
        "• Kreuzung: Straße A / Straße B, Stadt\n"
        "• Adresse: Straße 12, Stadt\n"
        "Und wichtig: Im Text muss @HeatmapofFascism stehen.\nWenn du den ursprünglichen Post nicht bearbeiten kannst: bitte löschen und neu posten (mit korrektem Ort).\n"
        "Alerta alerta 🖖"
    )
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
        print(f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} fav_check status={status_id} trusted_set=EMPTY")
        return False
    try:
        fav_accounts = get_favourited_by(cfg, status_id)
    except Exception as e:
        print(f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} fav_check status={status_id} ERROR={e!r}")
        return False

    fav_norm = []
    for a in (fav_accounts or []):
        acct = None
        if isinstance(a, dict):
            acct = a.get("acct") or a.get("username")
        else:
            acct = str(a)
        fav_norm.append((acct or '').split('@')[0].strip().lower())

    print(f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} fav_check status={status_id} favs={fav_norm} trusted={sorted(trusted_set)}")
    return any(x in trusted_set for x in fav_norm)

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
    status_id: Optional[str],
    instance_url: Optional[str],
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
            "status_id": (str(status_id) if status_id else None),
            "instance_url": (str(instance_url) if instance_url else None),

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

    # legacy alias support
    if "report_sticker" in tags_map and "sticker_report" not in tags_map:
        tags_map["sticker_report"] = tags_map["report_sticker"]
    if "sticker_report" in tags_map and "report_sticker" not in tags_map:
        tags_map["report_sticker"] = tags_map["sticker_report"]

    for tag, event in tags_map.items():
        statuses = get_hashtag_timeline(cfg, tag)
        for st in statuses:
            yield tag, event, st
        time.sleep(DELAY_TAG_FETCH)


def auto_git_push_reports(cfg: dict, relpath: str = "reports.geojson") -> None:
    """
    Optional: auto-commit + push reports.geojson after publish.
    Enabled by config.json: "auto_push_reports": true
    """
    try:
        if not cfg.get("auto_push_reports"):
            return

        remote = str(cfg.get("auto_push_remote", "origin"))
        branch = str(cfg.get("auto_push_branch", "main"))

        def run_git(args):
            r = subprocess.run(
                ["git"] + args,
                cwd=str(ROOT),
                capture_output=True,
                text=True
            )
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            return r.returncode, out

        # only if reports.geojson changed
        rc, out = run_git(["status", "--porcelain", "--", relpath])
        if rc != 0:
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            print(f"{ts} auto_push ERROR git_status rc={rc} out={out!r}")
            return
        if not out.strip():
            return


        rc, out = run_git(["add", "--", relpath])
        if rc != 0:
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            print(f"{ts} auto_push ERROR git_add rc={rc} out={out!r}")
            return

        tsmsg = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        msg = f"Auto-publish reports ({tsmsg})"
        rc, out = run_git(["commit", "-m", msg])
        if rc != 0:
            # allow "nothing to commit" etc.
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            print(f"{ts} auto_push WARN git_commit rc={rc} out={out!r}")
            return

        rc, out = run_git(["push", remote, f"HEAD:{branch}"])
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        if rc == 0:
            print(f"{ts} auto_push OK remote={remote} branch={branch} file={relpath}")
        else:
            print(f"{ts} auto_push ERROR git_push rc={rc} out={out!r}")

    except Exception as e:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"{ts} auto_push ERROR exc={e!r}")

def main():
    # Ensure baseline files exist
    ensure_object_file(CACHE_PATH)
    ensure_array_file(PENDING_PATH)
    ensure_reports_file()

    cfg = load_json(CFG_PATH, None)
    if not cfg:
        raise SystemExit("Missing config.json")

    # TEST MODE gate (stable spec):
    # - stays ON until stability gates pass
    # - forces AUTO_PUSH off (manual push fallback)
    test_mode = bool(cfg.get("test_mode", True))
    if test_mode:
        cfg["auto_push_reports"] = False

    print(f"START v={__version__} test_mode={test_mode} auto_push_reports={bool(cfg.get('auto_push_reports'))}")

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

    # RETRY NEEDS_INFO geocode (e.g. after better formatting / new token)
    for it in pending:
        if it.get('status') != 'NEEDS_INFO' or it.get('error') != 'geocode_failed':
            continue
        q = str(it.get('location_text') or '').strip()
        # Drop deleted posts (so pending can't get stuck forever)
        try:
            _ = get_favourited_by(cfg, str(it.get("status_id") or ""))
        except StatusDeleted as e:
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            print(f"{ts} pending_drop_deleted status_id={it.get('status_id')} reason={e}")
            it["status"] = "DROPPED"
            it["error"] = "status_deleted"
            continue
        except Exception:
            # Ignore transient API issues here; keep retrying later
            pass

        if not q:
            continue
        q_norm = normalize_query(q)
        coords2, method = geocode_query_worldwide(q, cfg['user_agent'])
        if not coords2 and q_norm and q_norm != q:
            coords2, method = geocode_query_worldwide(q_norm, cfg['user_agent'])
        if not coords2:
            coords2, method = geocode_query_worldwide(f"{q}, Germany", cfg['user_agent'])
        if coords2:
            lat, lon = coords2
            it['lat'] = float(lat)
            it['lon'] = float(lon)
            it['geocode_method'] = method
            if method == 'overpass_node':
                acc = ACC_NODE
            elif method == 'overpass_nearest':
                acc = ACC_NEAREST
            elif method == 'fallback':
                acc = ACC_FALLBACK
            else:
                acc = ACC_DEFAULT
            it['accuracy_m'] = int(acc)
            it['radius_m'] = int(acc)
            it['status'] = 'PENDING'
            it.pop('error', None)
            time.sleep(DELAY_NOMINATIM)
    reports = load_reports()
    # Stable policy checks (do NOT dirty repo on mere "checked")
    verify_budget = int(cfg.get("verify_budget", 200))
    v_checked, v_removed = verify_deleted_features(reports, cfg, verify_budget)

    context_budget = int(cfg.get("context_budget", 100))
    ctx_changed = update_features_from_context(reports, cfg, context_budget)

    # Persist ONLY when reports content changed (keeps repo clean on normal runs)
    if (v_removed > 0) or (ctx_changed > 0):
        save_json(REPORTS_PATH, reports)

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

        media_urls = [
            a.get("url")
            for a in attachments
            if a.get("type") == "image" and a.get("url")
        ]


        # REQUIRE mention (anti-spam / routing)
        require_mention = bool(cfg.get("require_mention", True))
        required_mentions = cfg.get("required_mentions") or ["HeatmapofFascism"]
        if require_mention and not contains_required_mention(text, required_mentions):
            needs_item = {
                "id": item_id,
                "status_id": str(status_id),
                "status": "NEEDS_INFO",
                "event": event,
                "tag": tag,
                "source": url,
                "created_at": st.get("created_at"),
                "created_date": iso_date_from_created_at(st.get("created_at")),
                "lat": 0.0,
                "lon": 0.0,
                "accuracy_m": int(ACC_FALLBACK),
                "radius_m": int(ACC_FALLBACK),
                "geocode_method": "none",
                "location_text": "",
                "sticker_type": parse_sticker_type(text),
                "removed_at": None,
                "media": media_urls,
                "error": "missing_mention",
                "replied": [],
            }
            if reply_once(cfg, cache, f"missing:{status_id}", str(status_id), build_reply_missing([
                "Foto",
                "#sticker_report oder #sticker_removed",
                "Ort: Koordinaten ODER Kreuzung ODER Adresse",
                "@HeatmapofFascism (Mention im Text)"
            ])):
                needs_item["replied"].append("missing")
            pending.append(needs_item)
            pending_by_source[url] = needs_item
            added_pending += 1
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

        snap_note = ""

        if coords:
            lat, lon = coords
            geocode_method = "gps"
            accuracy_m = ACC_GPS
            radius_m = ACC_GPS
            location_text = f"{lat}, {lon}"

            # Snap away from road center / private areas (prefer footways)
            _slat, _slon, _snote = snap_to_public_way(float(lat), float(lon), cfg["user_agent"])
            if _snote:
                lat, lon = _slat, _slon
                snap_note = _snote
                geocode_method = f"{geocode_method}+{_snote}"
        else:
            location_text = q or ""
            q_norm = normalize_query(q or "")

            if q in cache and "lat" in cache[q] and "lon" in cache[q]:
                lat, lon = float(cache[q]["lat"]), float(cache[q]["lon"])
                geocode_method = str(cache[q].get("method", "cache"))
                accuracy_m = int(cache[q].get("accuracy_m", ACC_DEFAULT))
                radius_m = int(cache[q].get("radius_m", accuracy_m))

                # Snap away from road center / private areas (prefer footways)
                _slat, _slon, _snote = snap_to_public_way(float(lat), float(lon), cfg["user_agent"])
                if _snote:
                    lat, lon = _slat, _slon
                    snap_note = _snote
                    geocode_method = f"{geocode_method}+{_snote}"
            else:
                coords2, method = geocode_query_worldwide(q, cfg["user_agent"])
                if not coords2:
                    coords2, method = geocode_query_worldwide(q_norm, cfg["user_agent"])
                time.sleep(DELAY_NOMINATIM)
                if not coords2:
                    # NEEDS_INFO: keep the report and ask publicly for better location
                    needs_item = {
                        "id": item_id,
                        "status_id": str(status_id),
                        "status": "NEEDS_INFO",
                        "event": event,
                        "tag": tag,
                        "source": url,
                        "created_at": st.get("created_at"),
                        "created_date": created_date,
                        # no reliable coords yet -> placeholders, will never be published while NEEDS_INFO
                        "lat": 0.0,
                        "lon": 0.0,
                        "accuracy_m": int(ACC_FALLBACK),
                        "radius_m": int(ACC_FALLBACK),
                        "geocode_method": "none",
                        "location_text": (q or "").strip(),
                        "sticker_type": sticker_type,
                        "removed_at": removed_at,
                        "media": media_urls,
                        "error": "geocode_failed",
                    }
                    pending.append(needs_item)
                    pending_by_source[url] = needs_item
                    added_pending += 1

                    # public reply under the post
                    ok = reply_once(cfg, cache, f"needs:{status_id}", str(status_id), build_needs_info_reply(q or ""))
                    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                    if ok:
                        print(f"{ts} needs_info_reply status={status_id} OK")
                    else:
                        print(f"{ts} needs_info_reply status={status_id} FAILED")
                    continue

                lat, lon = coords2
                geocode_method = method

                # Snap away from road center / private areas (prefer footways)
                _slat, _slon, _snote = snap_to_public_way(float(lat), float(lon), cfg["user_agent"])
                if _snote:
                    lat, lon = _slat, _slon
                    snap_note = _snote
                    geocode_method = f"{geocode_method}+{_snote}"

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
                    "snap_note": snap_note,
                    "q_norm": q_norm
                }

        if event == "removed":
            removed_at = created_date

        # media_urls computed above

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

        # One-time public ack for PENDING (avoid spam)
        if not item.get("replied_pending"):
            okp = reply_once(cfg, cache, f"pending:{status_id}", str(status_id), build_reply_pending())
            if okp:
                item["replied_pending"] = True

    # -------------------------
    # 2) Publish approved (FAV)
    # -------------------------
    still_pending: List[Dict[str, Any]] = []

    for item in pending:
        if item.get("status") != "PENDING":
            still_pending.append(item)
            continue

        item_id = str(item.get("id"))
        if item_id in reports_ids:
            continue

        # One-time public ack for existing PENDING items (avoid silent waiting)
        if not item.get("replied_pending"):
            okp = reply_once(cfg, cache, f"pending:{item['status_id']}", str(item["status_id"]), build_reply_pending())
            if okp:
                item["replied_pending"] = True

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
            status_id=str(item.get("status_id")) if item.get("status_id") else None,
            instance_url=str(cfg.get("instance_url")) if cfg.get("instance_url") else None,
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

        # OK reply disabled (pending reply is enough; prevents spam)
        # if not item.get("replied_ok"):
        #     if reply_once(cfg, cache, f"ok:{item['status_id']}", str(item["status_id"]), build_reply_ok()):
        #         item["replied_ok"] = True
        time.sleep(DELAY_FAV_CHECK)

    # 3) Stale rule: present -> unknown after N days
    # stale rule disabled (no auto-expiry)
    removed = prune_deleted_published(cfg, reports)
    if removed:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
        print(f"{ts} prune_deleted_published removed={removed}")

    # Write outputs
    save_json(CACHE_PATH, cache)
    still_pending = [it for it in still_pending if it.get('status') != 'DROPPED']
    save_json(PENDING_PATH, still_pending)
    save_json(REPORTS_PATH, reports)
    auto_git_push_reports(cfg)

    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    print(f"{ts} Added pending: {added_pending} | Published: {published} | Pending left: {len(still_pending)}")

if __name__ == "__main__":
    main()
