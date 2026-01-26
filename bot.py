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
# - config.json            (tracked)  rules + instance_url + user_agent + hashtags + accuracy
# - cache_geocode.json     (tracked)  geocode cache
# - pending.json           (tracked)  pending items waiting for approval
# - reports.geojson        (tracked)  single source of truth (FeatureCollection)
#
# Status model:
# - "present"  = confirmed present
# - "removed"  = confirmed removed
# - "unknown"  = stale/uncertain (was present but not confirmed for 30 days)
#
# Dupe merge on publish:
# If distance <= max(existing.radius_m, new.radius_m) AND sticker_type matches OR one is "unknown"
# then UPDATE existing feature (last_seen, seen_count, status, removed_at, media, accuracy/radius tightened)


# =========================
# VERSION / MODES
# =========================
__version__ = "0.2.13"
import ssl
import certifi
import os
from zoneinfo import ZoneInfo

TZ_BERLIN = ZoneInfo("Europe/Berlin")
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
import json
import re
import time
import traceback
import pathlib
import math
import subprocess
from typing import Optional, Tuple, Dict, Any, List, Iterable
from datetime import datetime, timezone

import requests

# --- LOGGING (normal + event) ---
import builtins as _builtins
import threading as _threading
from zoneinfo import ZoneInfo as _ZoneInfo
from pathlib import Path

_print = _builtins.print
try:
    import signal as _signal
    _signal.signal(_signal.SIGPIPE, _signal.SIG_DFL)
except Exception:
    pass
_LOG_TZ = _ZoneInfo("Europe/Berlin")
_LOG_ROOT = pathlib.Path(__file__).resolve().parent
LOG_DIR = _LOG_ROOT / "logs"
ERRORS_DIR = _LOG_ROOT / "errors"
_LOG_DATE = datetime.now(_LOG_TZ).strftime("%Y-%m-%d")

NORMAL_LOG_PATH = LOG_DIR / f"normal-{_LOG_DATE}.log"
EVENT_LOG_PATH  = LOG_DIR / f"event-{_LOG_DATE}.log"
EVENT_STATE_PATH = LOG_DIR / "event_state.json"
ERROR_LOG_PATH = ERRORS_DIR / f"errors-{_LOG_DATE}.log"

# --- RETENTION ---
RETENTION_DAYS = 14

def _prune_old_logs(root: pathlib.Path, days: int = RETENTION_DAYS) -> None:
    """Delete normal-/event- logs older than N days (date from filename, Europe/Berlin)."""
    try:
        today = datetime.now(_LOG_TZ).date()
        for prefix in ("normal-", "event-"):
            for fp in root.glob(f"{prefix}*.log"):
                m = re.match(rf"^{prefix}(\d{{4}}-\d{{2}}-\d{{2}})\.log$", fp.name)
                if not m:
                    continue
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                if (today - d).days > days:
                    try:
                        fp.unlink()
                    except FileNotFoundError:
                        pass
    except Exception:
        pass

_prune_old_logs(LOG_DIR)
# --- /RETENTION ---

_LOG_LOCK = _threading.Lock()

_RE_CYCLE = re.compile(
    r"^Added pending:\s*(\d+)\s*\|\s*Published:\s*(\d+)\s*\|\s*Pending left:\s*(\d+)\s*$"
)

def _now_iso():
    # local time, human readable (no 'T', no timezone suffix)
    return datetime.now().strftime("%Y-%m-%d // %H:%M:%S")

def _append(path: pathlib.Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def _load_event_state() -> dict:
    # Reset state if the event log was truncated/rotated (so event log can repopulate).
    try:
        if (not EVENT_LOG_PATH.exists()) or EVENT_LOG_PATH.stat().st_size == 0:
            return {}
    except Exception:
        return {}
    try:
        if EVENT_STATE_PATH.exists():
            return json.loads(EVENT_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_event_state(d: dict) -> None:
    try:
        tmp = EVENT_STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(EVENT_STATE_PATH)  # atomic on same filesystem
    except Exception:
        pass

_EVENT_LAST_BY_KEY = _load_event_state()

def _event_key(msg: str) -> str:
    line = msg.strip()
    if _RE_CYCLE.match(line):
        return "cycle_summary"

    t = line.split()
    if not t:
        return ""
    head = t[0]

    if head in {"reply", "fav_check", "auto_push"}:
        for tok in t[1:6]:
            if tok.startswith(("status=", "in_reply_to=", "key=")):
                return f"{head}:{tok}"
        return head

    if head == "Added" and len(t) > 1:
        return f"{head} {t[1]}"

    return head

def _event_sig(k: str, msg: str) -> str:
    line = msg.strip()
    if k == "START":
        # keep full line; START usually changes with version/mode
        return line
    if k == "verify_deleted":
        # only numbers matter (checked/removed)
        mm = re.search(r"checked=(\d+)\s+removed=(\d+)", line)
        if mm:
            return f"{mm.group(1)}|{mm.group(2)}"
        return "PARSE_FAIL"
    if k == "cycle_summary":
        m = _RE_CYCLE.match(line)
        if m:
            # only numbers matter
            return f"{m.group(1)}|{m.group(2)}|{m.group(3)}"
        return "PARSE_FAIL"
    return line

def print(*args, **kwargs):
    """
    Logging wrapper (single timestamp, readable):
    - Prefix every line with: YYYY-MM-DD // HH:MM:SS -
    - No ISO 'T' and no '+01:00' noise.
    - Event-log dedup uses the *unprefixed* message line.
    - fav_check is noisy -> only print when it CHANGES (per status=...).
    """
    sep = kwargs.get("sep", " ")
    msg = sep.join(str(a) for a in args)

    with _LOG_LOCK:
        for raw in (msg.splitlines() or [""]):
            line = str(raw).rstrip()

            # compute event key once (used for dedup + event log)
            k = _event_key(line) if line else ""

            # suppress fav_check spam: only emit on change for same key
            if line.startswith("fav_check") and k:
                sig = _event_sig(k, line)
                if _EVENT_LAST_BY_KEY.get(k) == sig:
                    continue

            ts = datetime.now(TZ_BERLIN)
            prefix = ts.strftime("%Y-%m-%d // %H:%M:%S%z")
            # +0100 -> +01:00 for readability
            if len(prefix) >= 5:
                prefix = prefix[:-2] + ":" + prefix[-2:]
            full = f"{prefix} - {line}" if line else f"{prefix} -"

            _append(NORMAL_LOG_PATH, full)

            if k:
                sig = _event_sig(k, line)
                if _EVENT_LAST_BY_KEY.get(k) != sig:
                    _EVENT_LAST_BY_KEY[k] = sig
                    _append(EVENT_LOG_PATH, full)
                    _save_event_state(_EVENT_LAST_BY_KEY)

            _print(full, flush=True)
# --- /LOGGING ---


# =========================
# FILES
# =========================
ROOT = pathlib.Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"

# Professional structure
SECRETS_DIR = ROOT / "secrets"
SECRETS_PATH = SECRETS_DIR / "secrets.json"
MANAGER_UPDATE_MESSAGE_PATH = SECRETS_DIR / "manager_update_message.txt"

def load_manager_update_message() -> str:
    """Load manager-update DM text from ignored secrets/ file. Empty => no update DMs."""
    try:
        return MANAGER_UPDATE_MESSAGE_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""

TRUSTED_PATH = SECRETS_DIR / "trusted_accounts.json"
BLACKLIST_PATH = SECRETS_DIR / "blacklist_accounts.json"
MANAGER_DM_STATE_PATH = SECRETS_DIR / "manager_dm_state.json"
MANAGER_UPDATE_STATE_PATH = SECRETS_DIR / "manager_update_state.json"

SUPPORT_DIR = ROOT / "support"
SUPPORT_REQUESTS_PATH = SUPPORT_DIR / "support_requests.json"
SUPPORT_STATE_PATH = SUPPORT_DIR / "support_state.json"

CACHE_PATH = ROOT / "cache_geocode.json"
PENDING_PATH = ROOT / "pending.json"
TIMELINE_STATE_PATH = ROOT / "timeline_state.json"
REPORTS_PATH = ROOT / "reports.geojson"

# =========================
# REGEX
# =========================
RE_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")  # "Street 12, City"
RE_STREET_CITY = re.compile(r"^(.+?)\s*,\s*(.+)$")  # "Street, City"
RE_CROSS = re.compile(r"^(.+?)\s*(?:/| x | & )\s*(.+?)\s*,\s*(.+)$", re.IGNORECASE)  # "A / B, City"
RE_INTERSECTION = re.compile(r"^\s*intersection of\s+(.+?)\s+and\s+(.+?)\s*,\s*(.+?)\s*$", re.IGNORECASE)
RE_STICKER_TYPE = re.compile(r"(?im)^\s*#(?:sticker|graffiti|grafitti)_(?:type|typ)\s*:?\s*([^\n#@]{1,200}?)(?=\s*(?:(ort|location|place)\s*:|@|#|$))")
RE_NOTE = re.compile(r"(?is)(?:^|\s)#note\s*:\s*(.+?)(?=(?:\s#[\w_]+)|$)")

# =========================
# ENDPOINTS / POLITENESS
# =========================
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
)

DELAY_TAG_FETCH = 0.05
DELAY_FAV_CHECK = 0.20
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
    """Load JSON safely.
    If file is missing or invalid JSON, return default and keep the bot running.
    """
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        try:
            _append(EVENT_LOG_PATH, f"{_now_iso()} load_json WARN path={path.name} err={e!r} -> default")
        except Exception:
            pass
        return default

def save_json(path, obj) -> None:
    """
    Atomic JSON write.
    Important: temp file MUST be unique (launchd overlap can cause .tmp collisions).
    """
    import json
    import os
    import tempfile
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = json.dumps(obj, ensure_ascii=False, indent=2)
    if not data.endswith("\n"):
        data += "\n"

    fd = None
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        fd = None
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        try:
            if fd is not None:
                os.close(fd)
        except Exception:
            pass
        try:
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass

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

def parse_note(text: str) -> str:
    m = RE_NOTE.search(text or "")
    if not m:
        return ""
    t = (m.group(1) or "").strip()
    return t[:500]

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

    def fetch_pois(r_m: int) -> list:
        # Public-ish street furniture POIs for sticker-like reports
        q = f"""
[out:json][timeout:25];
(
  node(around:{r_m},{lat0},{lon0})["leisure"="bench"];
  node(around:{r_m},{lat0},{lon0})["amenity"~"^(waste_basket|waste_disposal)$"];
  node(around:{r_m},{lat0},{lon0})["highway"="street_lamp"];
);
out tags;
""".strip()
        data = _overpass_post(q, user_agent)
        if not data or not isinstance(data, dict):
            return []
        elems = data.get("elements") or []
        return elems if isinstance(elems, list) else []

    def nearest_public_poi(r_m: int = 15):
        elems = fetch_pois(r_m)
        best = None  # (dist_m, lat, lon, note)
        for e in elems:
            if e.get("type") != "node":
                continue
            tags = e.get("tags") or {}
            if not is_public(tags):
                continue
            if "lat" not in e or "lon" not in e:
                continue
            plat, plon = float(e["lat"]), float(e["lon"])
            d = haversine_m(lat0, lon0, plat, plon)
            if best is None or d < best[0]:
                note = "poi"
                if (tags.get("leisure") or "").strip().lower() == "bench":
                    note = "bench"
                elif (tags.get("amenity") or "").strip().lower() in {"waste_basket","waste_disposal"}:
                    note = "waste"
                elif (tags.get("highway") or "").strip().lower() == "street_lamp":
                    note = "lamp"
                best = (d, plat, plon, note)
        return None if best is None else (best[1], best[2], best[3])


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

    # 0) Prefer nearby public street-furniture POIs (bench/waste/lamp) when available
    poi = nearest_public_poi(r_m=15)
    if poi:
        plat, plon, pnote = poi
        # tiny building sanity: ignore POIs that would land on/inside buildings
        if not building_nearby(plat, plon, r_m=4):
            return plat, plon, f"snap_poi:{pnote}"


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

def maybe_snap_to_public_way(lat: float, lon: float, cfg: dict, geocode_method: str) -> tuple[float, float, str, str]:
    """Snap-to-public-way with hard max-distance guard (default 50m).
    HIERARCHY (correctness first):
      - GPS / explicit coordinates MUST NOT be moved by default.
      - Only non-GPS (fallback/geocode) may be snapped (guarded).
    Returns (lat, lon, geocode_method, snap_note).
    """
    orig_lat, orig_lon = float(lat), float(lon)

    if not bool(cfg.get("snap_enabled", True)):
        return orig_lat, orig_lon, geocode_method, "snap_disabled"

    gm = str(geocode_method or "")
    allow_gps = bool(cfg.get("snap_allow_gps", False))
    if gm.startswith("gps") and not allow_gps:
        return orig_lat, orig_lon, geocode_method, "snap_skipped:gps"

    _slat, _slon, _snote = snap_to_public_way(orig_lat, orig_lon, cfg["user_agent"])
    if not _snote:
        return orig_lat, orig_lon, geocode_method, ""

    SNAP_MAX_M = float(cfg.get("snap_max_m", 50.0))
    dist_m = haversine_m(orig_lat, orig_lon, float(_slat), float(_slon))
    if dist_m <= SNAP_MAX_M:
        return float(_slat), float(_slon), f"{geocode_method}+{_snote}", _snote

    # reject snap -> keep original coords, keep method (no silent wrong pins)
    return orig_lat, orig_lon, geocode_method, f"{_snote}+rejected:{int(dist_m)}m"


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

# =========================
# ENTITY / SOURCE NORMALIZE
# =========================

def normalize_entity_key(x: str) -> str:
    """
    Stable key for filtering/statistics.
    Rules: lowercase, umlauts normalized, keep only [a-z0-9].
    """
    x = (str(x or "")).strip().lower()
    x = (x.replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss"))
    x = re.sub(r"[^a-z0-9]+", "", x)
    return x

def load_entities_dict() -> dict:
    try:
        from pathlib import Path
        import json
        p = Path("entities.json")
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def parse_entity(text: str) -> tuple[str, str]:
    """
    Accept:
      #entity: X
      #source: X
      entity: X
      source: X
    Returns (raw, key) or ("","") if none.
    """
    import html as _html
    t = _html.unescape(text or "")
    for ln in t.splitlines():
        line = ln.strip()
        if not line:
            continue
        m = re.match(r"^\s*(?:#\s*)?(entity|source)\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if m:
            raw = m.group(2).strip()
            key = normalize_entity_key(raw)
            return raw, key
    return "", ""

# -------------------------
# IDLE ENTITY ENRICH (WIKI)
# -------------------------
_LAST_ENTITY_ENRICH_TS = 0.0
_ENTITY_ENRICH_CURSOR = 0

def _wiki_summary(lang: str, title: str, user_agent: str) -> str:
    # Wikipedia REST summary endpoint (short extract)
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{_up.quote(title)}"
    req = _ur.Request(url, headers={"User-Agent": user_agent or "HeatmapOfFascismBot/EntityEnrich"})
    with _ur.urlopen(req, timeout=20) as r:
        data = _json.loads(r.read().decode("utf-8"))
    txt = (data.get("extract") or "").strip()
    if len(txt) > 260:
        txt = txt[:257].rstrip() + "…"
    return txt

def maybe_idle_enrich_entities(cfg: dict) -> None:
    """
    Idle background task (EN-only, deterministic):
    - Find ONE entity with empty desc in entities.json
    - Resolve Wikidata QID via Wikipedia title (wiki_en preferred, else wiki_de)
    - Fetch Wikidata EN description for that QID
    - Cache into entities.json (qid + desc)
    Uses curl to avoid Python SSL trust-store issues on macOS.
    """
    try:
        import json, subprocess, urllib.parse

        if not bool(cfg.get("idle_entity_enrich", True)):
            return

        every_s = int(cfg.get("idle_entity_enrich_every_s", 600) or 600)
        throttle_path = ROOT / ".idle_entity_enrich_last.txt"
        now = time.time()
        try:
            last = float(throttle_path.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            last = 0.0
        if (now - last) < float(every_s):
            return

        p = ROOT / "entities.json"
        if not p.exists():
            return

        ent = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(ent, dict) or not ent:
            return

        pick_key = ""
        pick = None
        for k, v in ent.items():
            if not isinstance(v, dict):
                continue
            if str(v.get("desc") or "").strip():
                continue
            pick_key = str(k or "").strip().lower()
            pick = v
            break

        throttle_path.write_text(str(now), encoding="utf-8")
        if not pick_key or not isinstance(pick, dict):
            return

        def _curl_json(url: str, timeout_s: int = 15) -> dict:
            cmd = ["curl", "-fsSL", "--max-time", str(int(timeout_s)), url]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                return {}
            try:
                return json.loads(r.stdout)
            except Exception:
                return {}

        def _qid_from_wikipedia(wiki_lang: str, title: str) -> str:
            q = urllib.parse.quote(title)
            url = f"https://{wiki_lang}.wikipedia.org/w/api.php?action=query&format=json&prop=pageprops&ppprop=wikibase_item&titles={q}"
            data = _curl_json(url, timeout_s=15)
            pages = ((data or {}).get("query") or {}).get("pages") or {}
            for _pid, pg in pages.items():
                pp = (pg or {}).get("pageprops") or {}
                qid = (pp.get("wikibase_item") or "").strip()
                if qid.startswith("Q"):
                    return qid
            return ""

        def _en_desc_from_qid(qid: str) -> str:
            url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
            data = _curl_json(url, timeout_s=15)
            e = ((data or {}).get("entities") or {}).get(qid) or {}
            desc = (((e.get("descriptions") or {}).get("en") or {}).get("value") or "").strip()
            if len(desc) > 240:
                desc = desc[:237].rstrip() + "…"
            return desc

        qid = str(pick.get("qid") or "").strip()

        if not qid:
            title = str(pick.get("wiki_en") or "").strip()
            lang = "en"
            if not title:
                title = str(pick.get("wiki_de") or "").strip()
                lang = "de"
            if not title:
                log_line(f"idle_enrich SKIP key={pick_key} reason=no_wiki_title")
                return
            qid = _qid_from_wikipedia(lang, title)
            if not qid:
                log_line(f"idle_enrich SKIP key={pick_key} reason=no_qid_from_{lang}wiki")
                return

        desc = _en_desc_from_qid(qid)
        if not desc:
            log_line(f"idle_enrich SKIP key={pick_key} qid={qid} reason=no_en_desc")
            return

        pick["qid"] = qid
        pick["desc"] = desc
        ent[pick_key] = pick
        p.write_text(json.dumps(ent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_line(f"idle_enrich OK key={pick_key} qid={qid} desc_len={len(desc)} source=wikidata_via_wikipedia")
    except Exception as e:
        try:
            log_line(f"idle_enrich ERROR err={e!r}")
        except Exception:
            pass
        return
def parse_location(text: str) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """
    Returns:
      (lat, lon) if coords found anywhere in text
      OR a query string if address/crossing/street+city found in ANY non-hashtag line
      OR (None, None) if invalid
    """
    import html as _html
    text = _html.unescape(text)

    # Accept DMS coord formats (Google Maps) before RE_COORDS
    # Example: 52°31'20"N 13°22'14"E
    def _coords_dms(ss: str):
        import re
        pat = r"(\d{1,3})\s*[°º]\s*(\d{1,2})\s*[\'’′]\s*(\d{1,2}(?:[\.,]\d+)?)\s*(?:[\"”″])?\s*([NSEW])"
        hits = re.findall(pat, ss, flags=re.IGNORECASE)
        if not hits:
            return None
        def to_dd(deg, minutes, seconds, hemi):
            dd = float(deg) + float(minutes)/60.0 + float(str(seconds).replace(',', '.'))/3600.0
            h = str(hemi).upper()
            if h in ('S','W'):
                dd = -dd
            return dd, h
        lat = None
        lon = None
        for deg, mi, sec, hemi in hits:
            dd, h = to_dd(deg, mi, sec, hemi)
            if h in ('N','S') and lat is None and -90.0 <= dd <= 90.0:
                lat = dd
            if h in ('E','W') and lon is None and -180.0 <= dd <= 180.0:
                lon = dd
            if lat is not None and lon is not None:
                return (lat, lon)
        return None

    c_dms = _coords_dms(text)
    if c_dms:
        return (float(c_dms[0]), float(c_dms[1])), None

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

def _acct_base(a: str) -> str:
    """Normalize acct/username to local part (no domain), lowercased."""
    a = (a or "").strip().lstrip("@")
    a = a.split("@")[0].strip().lower()
    return a

def _api_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {cfg['access_token']}"}

def _api_get(cfg: Dict[str, Any], url: str, params: Dict[str, Any] | None = None) -> requests.Response:
    r = requests.get(url, headers=_api_headers(cfg), params=params, timeout=MASTODON_TIMEOUT_S)
    r.raise_for_status()
    return r

def _api_post(cfg: Dict[str, Any], url: str, data: Dict[str, Any]) -> requests.Response:
    r = requests.post(url, headers=_api_headers(cfg), data=data, timeout=MASTODON_TIMEOUT_S)
    r.raise_for_status()
    return r

def get_verify_credentials(cfg: Dict[str, Any]) -> Dict[str, Any]:
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/accounts/verify_credentials"
    r = _api_get(cfg, url)
    return r.json()

def _paginate_accounts(cfg: Dict[str, Any], first_url: str, params: Dict[str, Any] | None = None, max_pages: int = 50) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    url = first_url
    p = dict(params or {})
    for _ in range(int(max_pages)):
        r = _api_get(cfg, url, params=p if url == first_url else None)
        data = r.json()
        if isinstance(data, list):
            out.extend([x for x in data if isinstance(x, dict)])
        # Mastodon pagination via Link header
        nxt = None
        try:
            nxt = (r.links or {}).get("next", {}).get("url")
        except Exception:
            nxt = None
        if not nxt:
            break
        url = nxt
        p = None
        time.sleep(0.15)
    return out

def get_following_set(cfg: Dict[str, Any], my_id: str) -> set:
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/accounts/{my_id}/following"
    accs = _paginate_accounts(cfg, url, params={"limit": 80}, max_pages=int(cfg.get("sync_max_pages", 50) or 50))
    return set(_acct_base((a.get("acct") or a.get("username") or "")) for a in accs if (a.get("acct") or a.get("username")))

def get_blocked_set(cfg: Dict[str, Any]) -> set:
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/blocks"
    accs = _paginate_accounts(cfg, url, params={"limit": 80}, max_pages=int(cfg.get("sync_max_pages", 50) or 50))
    return set(_acct_base((a.get("acct") or a.get("username") or "")) for a in accs if (a.get("acct") or a.get("username")))

def load_blacklist_set() -> set:
    legacy = ROOT / "blacklist_accounts.json"
    data = load_json(BLACKLIST_PATH, None)
    if data is None:
        data = load_json(legacy, [])
    out = set()
    if isinstance(data, list):
        for a in data:
            if isinstance(a, str):
                out.add(_acct_base(a))
    out.discard("")
    return out

def _write_list_if_changed(path: Path, new_list: List[str]) -> bool:
    try:
        old = load_json(path, None)
        old_list = old if isinstance(old, list) else None
        if old_list == new_list:
            return False
    except Exception:
        pass
    save_json(path, new_list)
    return True

def send_direct_message(cfg: Dict[str, Any], to_user: str, body: str) -> bool:
    """Send a DM (visibility=direct) to @to_user. Returns True on success."""
    try:
        instance = cfg["instance_url"].rstrip("/")
        url = f"{instance}/api/v1/statuses"
        u = _acct_base(to_user)
        if not u:
            return False
        txt = body.strip()
        if not txt.startswith("@"):
            txt = f"@{u} " + txt
        data = {"status": txt, "visibility": "direct"}
        _api_post(cfg, url, data)
        return True
    except Exception as e:
        try:
            _append(ERROR_LOG_PATH, f"{_now_iso()} dm ERROR to={to_user} err={e!r}")
        except Exception:
            pass
        return False


def get_conversations(cfg: Dict[str, Any], limit: int = 40) -> List[Dict[str, Any]]:
    """Fetch DM conversations (threads containing 'direct' visibility posts)."""
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/conversations"
    r = _api_get(cfg, url, params={"limit": int(limit)})
    data = r.json()
    return data if isinstance(data, list) else []

def _ensure_support_files():
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not SUPPORT_REQUESTS_PATH.exists():
        save_json(SUPPORT_REQUESTS_PATH, [])
    if not SUPPORT_STATE_PATH.exists():
        save_json(SUPPORT_STATE_PATH, {"seen_status_ids": []})

def ingest_support_requests(cfg: Dict[str, Any], managers_set: set) -> int:
    """
    Collect manager support DMs:
      - DM to @HeatmapofFascism
      - contains hashtag #support_anfrage
      - sender must be a manager (account we follow)
    Writes to: support/support_requests.json (append, dedup by status_id)
    """
    try:
        _ensure_support_files()
        st = load_json(SUPPORT_STATE_PATH, {"seen_status_ids": []}) or {"seen_status_ids": []}
        seen = set(str(x) for x in (st.get("seen_status_ids") or []))

        reqs = load_json(SUPPORT_REQUESTS_PATH, []) or []
        if not isinstance(reqs, list):
            reqs = []

        added = 0
        convs = get_conversations(cfg, limit=int(cfg.get("support_poll_limit", 40) or 40))
        for c in convs:
            last = (c or {}).get("last_status") or {}
            sid = str(last.get("id") or "").strip()
            if not sid or sid in seen:
                continue

            acc = (last.get("account") or {}) if isinstance(last.get("account"), dict) else {}
            sender = _acct_base(str(acc.get("acct") or acc.get("username") or ""))
            if not sender or sender not in (managers_set or set()):
                seen.add(sid)
                continue

            txt = strip_html(last.get("content", "") or "")
            if "#support_anfrage" not in txt.lower():
                # not a support request, but mark seen to avoid reprocessing
                seen.add(sid)
                continue

            reqs.append({
                "status_id": sid,
                "created_at": last.get("created_at"),
                "sender": sender,
                "url": last.get("url"),
                "text": txt.strip(),
            })
            added += 1
            seen.add(sid)

        if added:
            save_json(SUPPORT_REQUESTS_PATH, reqs)
        st["seen_status_ids"] = sorted(seen)
        save_json(SUPPORT_STATE_PATH, st)
        return int(added)
    except Exception as e:
        try:
            _append(ERROR_LOG_PATH, f"{_now_iso()} support_ingest ERROR err={e!r}")
        except Exception:
            pass
        return 0
def build_manager_welcome_text(admin_handle: str = "@buntepanther") -> str:
    return (
        "🤖 🚀 Welcome — you’re now a Heatmap of Fascism Manager.\n\n"
        "You can approve reports for the public map:\n"
        "⭐ Favourite a report post to publish it.\n\n"
        "Rules (short):\n"
        "• Post must mention @HeatmapofFascism\n"
        "• Location required: lat, lon OR Street, City\n"
        "• Optional: #sticker_type: party/symbol/slogan\n"
        "• Optional: #sticker_removed (marks removed)\n"
        "• Optional: #sticker_seen (updates last confirmed)\n\n"
        f"Support: write a private message to @HeatmapofFascism with #support_anfrage.\n\nSupport/Admin: {admin_handle}\n"
        "FCK RACISM. ✊ ALERTA ALERTA."
    )

def build_manager_update_text(version: str, admin_handle: str = "@buntepanther", msg: str | None = None) -> str:
    base = (msg or "").strip()
    if not base:
        base = f"Update: bot is now v{version}."
    return (
        f"🤖 🚀 Heatmap of Fascism Update\n\n{base}\n\n"
        f"Support: write a private message to @HeatmapofFascism with #support_anfrage.\n\nSupport/Admin: {admin_handle}\n"
        "FCK RACISM. ✊ ALERTA ALERTA."
    )


# === MANAGER_UPDATE_DM_V1_START ===
def maybe_send_manager_update_dms(cfg: Dict[str, Any], managers_set: set) -> int:
    """
    One-shot private manager update DM.
    Security:
      - logs counts only (no usernames/IDs)
      - state in secrets/ (gitignored)
      - dedup via msg_hash
    """
    try:
        import hashlib
        msg = load_manager_update_message()  # already .strip()
        enabled = bool(cfg.get("dm_manager_updates", True))
        admin = str(cfg.get("admin_handle") or "@buntepanther").strip() or "@buntepanther"

        # Load previous state
        st = load_json(MANAGER_UPDATE_STATE_PATH, {}) or {}
        if not isinstance(st, dict):
            st = {}

        msg_hash = hashlib.sha256(msg.encode("utf-8")).hexdigest() if msg else ""
        prev_hash = str(st.get("msg_hash") or "")
        prev_sent = bool(st.get("sent") or 0)

        # Always write a fresh state record (even skip) so we can verify execution
        out = {
            "ts": _now_iso(),
            "enabled": enabled,
            "managers_count": int(len(managers_set or set())),
            "msg_len": int(len(msg)),
            "msg_hash": msg_hash,
            "sent": 0,
            "skipped": None,
        }

        if not enabled:
            out["skipped"] = "disabled"
            save_json(MANAGER_UPDATE_STATE_PATH, out)
            log_line(f"manager_update skip=disabled managers={out['managers_count']} msg_len={out['msg_len']}")
            return 0

        if not msg:
            out["skipped"] = "empty_message"
            save_json(MANAGER_UPDATE_STATE_PATH, out)
        # skip empty manager update silently (avoid log spam)
            return 0

        # Dedup: same message already sent
        if prev_sent and prev_hash and prev_hash == msg_hash:
            out["skipped"] = "dedup_same_message"
            out["sent"] = int(st.get("sent") or 0)
            save_json(MANAGER_UPDATE_STATE_PATH, out)
            log_line(f"manager_update skip=dedup managers={out['managers_count']} msg_len={out['msg_len']}")
            return 0

        body = build_manager_update_text(version=__version__, admin_handle=admin, msg=msg)

        sent = 0
        for u in sorted(managers_set or set()):
            ok = send_direct_message(cfg, u, body)
            if ok:
                sent += 1
            time.sleep(0.25)

        out["sent"] = int(sent)
        save_json(MANAGER_UPDATE_STATE_PATH, out)
        log_line(f"manager_update sent={sent} managers={out['managers_count']} msg_len={out['msg_len']}")
        return int(sent)

    except Exception as e:
        try:
            _append(ERROR_LOG_PATH, f"{_now_iso()} manager_update ERROR err={e!r}")
        except Exception:
            pass
        return 0
# === MANAGER_UPDATE_DM_V1_END ===

def sync_managers_and_blacklist(cfg: dict):
    """
    Sync managers/blacklist (best-effort) and ALWAYS return stable sets.

    Security / robustness goals:
    - Never crash the bot if sync fails (network, auth, missing helpers, etc.).
    - No NameError (following_list / blocked_list always defined).
    - Persisted state lives in secrets/*.json (ignored by git).
    """
    # default: load from disk (works even offline)
    try:
        following_list = load_json(SECRETS_DIR / "trusted_accounts.json", None) or []
    except Exception:
        following_list = []

    try:
        blocked_list = load_json(SECRETS_DIR / "blacklist_accounts.json", None) or []
    except Exception:
        blocked_list = []

    # best-effort refresh, only if helper functions exist
    try:
        do_sync = bool(cfg.get("dm_welcome_managers", True) or cfg.get("dm_manager_updates", True))

        if do_sync:
            # Optional helper hooks (only if they exist in this file)
            if "fetch_following_accounts" in globals() and callable(globals()["fetch_following_accounts"]):
                following_list = globals()["fetch_following_accounts"](cfg) or following_list

            if "fetch_blocked_accounts" in globals() and callable(globals()["fetch_blocked_accounts"]):
                blocked_list = globals()["fetch_blocked_accounts"](cfg) or blocked_list

            # Persist if we have something (still ignored by git)
            try:
                save_json(SECRETS_DIR / "trusted_accounts.json", sorted(set(following_list)))
            except Exception:
                pass
            try:
                save_json(SECRETS_DIR / "blacklist_accounts.json", sorted(set(blocked_list)))
            except Exception:
                pass

    except Exception as e:
        # log_line if available, otherwise silent
        try:
            log_line(f"sync_managers_and_blacklist WARN: {type(e).__name__}: {e}")
        except Exception:
            pass

    return set(following_list), set(blocked_list)
def _has_hashtag(text: str, tag: str) -> bool:
    t = (text or "").lower()
    h = (tag or "").strip().lower()
    if not h:
        return False
    if not h.startswith("#"):
        h = "#" + h
    return h in t

def get_conversations(cfg: Dict[str, Any], limit: int = 40) -> List[Dict[str, Any]]:
    """GET /api/v1/conversations (DM threads). Requires read:statuses."""
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/conversations"
    try:
        r = _api_get(cfg, url, params={"limit": int(limit)})
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        try:
            _append(ERROR_LOG_PATH, f"{_now_iso()} support_get_conversations ERROR err={e!r}")
        except Exception:
            pass
        return []

def mark_conversation_read(cfg: Dict[str, Any], conv_id: str) -> None:
    """POST /api/v1/conversations/:id/read. Requires write:conversations."""
    if not conv_id:
        return
    instance = cfg["instance_url"].rstrip("/")
    url = f"{instance}/api/v1/conversations/{conv_id}/read"
    try:
        _api_post(cfg, url, data={})
    except Exception as e:
        try:
            _append(ERROR_LOG_PATH, f"{_now_iso()} support_mark_read ERROR id={conv_id} err={e!r}")
        except Exception:
            pass

def process_support_requests(cfg: Dict[str, Any], trusted_set: set) -> int:
    """
    Managers can DM the bot with #support_anfrage.
    Stores requests in support/support_requests.json and marks the conversation as read.
    Stable behavior:
      - only unread conversations
      - only if sender is in trusted_set
      - dedupe by last_status.id
      - if API scopes missing: skip + log error, bot continues
    """
    if not bool(cfg.get("support_enabled", True)):
        return 0

    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_array_file(SUPPORT_REQUESTS_PATH)
    ensure_object_file(SUPPORT_STATE_PATH)

    tag = str(cfg.get("support_hashtag") or "support_anfrage").strip()
    if tag.startswith("#"):
        tag = tag[1:]

    state = load_json(SUPPORT_STATE_PATH, {}) or {}
    if not isinstance(state, dict):
        state = {}
    seen = set(str(x) for x in (state.get("seen_status_ids") or []))
    if len(seen) > 500:
        seen = set(list(seen)[-250:])

    reqs = load_json(SUPPORT_REQUESTS_PATH, []) or []
    if not isinstance(reqs, list):
        reqs = []

    added = 0
    convs = get_conversations(cfg, limit=int(cfg.get("support_limit", 40) or 40))
    for c in convs:
        if not isinstance(c, dict):
            continue
        if not c.get("unread"):
            continue
        conv_id = str(c.get("id") or "").strip()
        ls = c.get("last_status") or {}
        if not isinstance(ls, dict):
            continue
        sid = str(ls.get("id") or "").strip()
        if not sid or sid in seen:
            continue

        acc = (ls.get("account") or {}) if isinstance(ls.get("account"), dict) else {}
        sender = _acct_base(str(acc.get("acct") or acc.get("username") or ""))

        if not sender or (sender not in set(trusted_set or [])):
            continue

        txt = strip_html(str(ls.get("content") or ""))
        if not _has_hashtag(txt, tag):
            continue

        req = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "from": sender,
            "conversation_id": conv_id,
            "status_id": sid,
            "text": txt.strip(),
            "url": str(ls.get("url") or ""),
        }
        reqs.append(req)
        seen.add(sid)
        added += 1

        mark_conversation_read(cfg, conv_id)
        time.sleep(0.15)

    if added:
        save_json(SUPPORT_REQUESTS_PATH, reqs)
        state["seen_status_ids"] = list(seen)[-500:]
        save_json(SUPPORT_STATE_PATH, state)

    return int(added)
def get_hashtag_timeline(cfg: Dict[str, Any], tag: str, *, since_id: str | None = None, max_id: str | None = None, limit: int = 40) -> List[Dict[str, Any]]:
    instance = cfg["instance_url"].rstrip("/")
    tag = tag.lstrip("#")
    url = f"{instance}/api/v1/timelines/tag/{tag}"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}

    params: Dict[str, Any] = {"limit": int(limit)}
    if since_id:
        params["since_id"] = str(since_id)
    if max_id:
        params["max_id"] = str(max_id)

    r = requests.get(url, headers=headers, params=params, timeout=MASTODON_TIMEOUT_S)

    # Noise reduction:
    # - log http line only on non-200
    # - log items line only when we actually got items (>0)
    if r.status_code != 200:
        print(f"hashtag_timeline tag={tag} http={r.status_code} since_id={since_id or '-'} max_id={max_id or '-'}")

    # Rate limit safety: NEVER crash the main loop on 429.
    if r.status_code == 429:
        ra = (r.headers or {}).get("Retry-After")
        try:
            wait_s = max(1, min(300, int(str(ra).strip()))) if ra else 30
        except Exception:
            wait_s = 30
        print(f"hashtag_timeline tag={tag} http=429 rate_limited wait_s={wait_s}")
        time.sleep(wait_s)
        return []

    r.raise_for_status()
    data = r.json()

    n = len(data) if isinstance(data, list) else 0
    if n > 0:
        print(f"hashtag_timeline tag={tag} http=200 items={n} since_id={since_id or '-'} max_id={max_id or '-'}")

    return data

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
    Policy:
    - If we can CONFIRM the source status is deleted (auth 404/410 on origin instance),
      DROP the feature from reports.geojson (pin disappears).
    - #sticker_removed keeps the pin (handled elsewhere via status="removed").
    Returns number of removed features.
    """
    feats = reports.get("features") or []
    if not isinstance(feats, list) or not feats:
        return 0

    removed = 0
    kept = []

    for f in feats:
        props = (f or {}).get("properties") or {}
        
        # uMap popup helpers
        media = props.get("media")
        if isinstance(media, list) and media:
            props["media0"] = str(media[0])
        elif isinstance(media, str) and media:
            props["media0"] = media
        else:
            props["media0"] = ""

        # Optional aliases (so template can use Identity wording)
        if "identity_display" not in props:
            props["identity_display"] = (props.get("entity_display") or "")
        if "identity_desc" not in props:
            props["identity_desc"] = (props.get("entity_desc") or "")
        status_id = str(props.get("status_id") or "")

        # fallback: derive from "masto-<digits>"
        if not status_id and str(props.get("id") or "").startswith("masto-"):
            status_id = str(props.get("id")).split("masto-", 1)[1].strip()

        if not status_id.isdigit():
            kept.append(f)
            continue

        try:
            # fetch_status raises StatusDeleted only when auth-confirmed on origin instance
            _ = fetch_status(cfg, cfg.get("instance_url", ""), status_id)
            kept.append(f)
        except StatusDeleted as e:
            removed += 1
            from datetime import datetime, timezone

            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            print(f"prune_drop_deleted status_id={status_id} reason={e}")
        except Exception:
            # network/transient -> keep
            kept.append(f)

    if removed:
        reports["features"] = kept

    return removed


def prune_unfav_published(cfg: dict, reports: dict, cache: dict, trusted_set: set, grace_s: int = 60) -> tuple[int, int]:
    """
    Policy:
    - If a published feature loses ALL trusted ⭐ Favourite approvals, unpublish (remove from reports.geojson).
    - Use a grace window (seconds) to avoid transient API states.
    - Track timers in cache_geocode.json only (keeps repo clean).
    Returns (checked, removed).
    """
    try:
        feats = list(reports.get("features") or [])
        if not feats:
            return (0, 0)

        unfav = cache.setdefault("_unfav", {})  # status_id -> unix_ts
        kept = []
        checked = 0
        removed = 0
        now = time.time()

        for f in feats:
            props = (f or {}).get("properties") or {}
            sid = str(props.get("status_id") or "").strip()
            if not sid.isdigit():
                kept.append(f)
                continue

            checked += 1
            ok = is_approved_by_fav(cfg, sid, trusted_set)

            if ok:
                # approved again -> clear timer
                unfav.pop(sid, None)
                kept.append(f)
                continue

            t0 = unfav.get(sid)
            if not t0:
                unfav[sid] = now
                kept.append(f)
                continue

            try:
                age = now - float(t0)
            except Exception:
                age = 0.0

            if age >= float(grace_s):
                # unpublish
                unfav.pop(sid, None)
                removed += 1
                continue

            kept.append(f)

        if removed:
            reports["features"] = kept
        return (checked, removed)
    except Exception:
        return (0, 0)

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
            print(f"verify_drop_deleted status_id={sid} reason={e}")
        except Exception:
            # transient error -> ignore (also don't stamp last_verify_ts)
            pass

    if to_drop:
        reports["features"] = [f for i, f in enumerate(feats) if i not in to_drop]

    if checked:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"verify_deleted checked={checked} removed={removed}")

    return checked, removed


def refresh_types_features(cfg: Dict[str, Any], reports: Dict[str, Any], limit: int = 50) -> int:
    """
    On-demand maintenance:
    - Fetch source posts for features where sticker_type == "unknown"
    - If the source post now contains #sticker_type/#sticker_typ/#graffiti_type/#graffiti_typ (etc),
      update properties["sticker_type"] accordingly.
    - Does NOT push to git. Caller decides.
    Returns: updated_count
    """
    feats = reports.get("features") or []
    if not isinstance(feats, list) or not feats:
        return 0

    cands = []
    for idx, f in enumerate(feats):
        props = (f or {}).get("properties") or {}
        if (props.get("sticker_type") or "unknown") != "unknown":
            continue
        inst, sid = derive_status_ref(props)
        if not inst or not sid:
            continue
        key = str(props.get("last_seen") or props.get("first_seen") or "")
        cands.append((key, idx, inst, sid))

    if not cands:
        return 0

    cands.sort(key=lambda x: x[0])
    budget = max(0, int(limit))
    checked = 0
    updated = 0

    for _, idx, inst, sid in cands[:budget]:
        checked += 1
        try:
            st = fetch_status(cfg, inst, str(sid))
        except StatusDeleted:
            continue
        except Exception:
            continue

        if not st:
            continue

        text = strip_html(st.get("content") or "")
        stype = parse_sticker_type(text)

        if stype and stype != "unknown":
            props = (feats[idx] or {}).get("properties") or {}
            props["sticker_type"] = stype
            updated += 1

    if checked:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"{ts} refresh_types checked={checked} updated={updated}")

    return int(updated)

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
    m = re.search(r"#(?:sticker|graffiti|grafitti)_(?:type|typ)\s*:\s*([^\n\r#]+)", text or "", flags=re.IGNORECASE)
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
            print(f"source_deleted_keep status_id={sid} reason={e}")
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
        print(f"context_update changed={changed} checked={min(len(cands), budget)}")

    return changed


def delete_status(cfg: Dict[str, Any], status_id: str) -> bool:
    """Best-effort delete of a status posted by this bot. Silent on failure."""
    try:
        instance = cfg["instance_url"].rstrip("/")
        url = f"{instance}/api/v1/statuses/{str(status_id)}"
        headers = {"Authorization": f"Bearer {cfg['access_token']}"}
        r = requests.delete(url, headers=headers, timeout=MASTODON_TIMEOUT_S)
        if r.status_code in (200, 202, 204, 404, 410):
            return True
        return False
    except Exception:
        return False


def post_public_reply(cfg: Dict[str, Any], in_reply_to_id: str, text: str) -> Optional[str]:
    """Public reply under the original post (no DM). Returns reply status_id on success."""
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
            body = (r.text or "")[:200].replace("\n", " ")
            print(f"reply FAILED in_reply_to={in_reply_to_id} http={r.status_code} body={body!r}")
            return None
        try:
            js = r.json()
            rid = str(js.get("id") or "").strip()
        except Exception:
            rid = ""
        print(f"reply OK in_reply_to={in_reply_to_id} reply_id={rid or 'UNKNOWN'}")
        return rid or None
    except Exception as e:
        print(f"reply ERROR in_reply_to={in_reply_to_id} err={e!r}")
        return None


def reply_once(cfg: Dict[str, Any], cache: Dict[str, Any], key: str, in_reply_to_id: str, text: str) -> bool:
    """Ensure we reply at most once per status+type. Also keeps only ONE bot-reply per status via delete+replace."""
    try:
        rep = cache.setdefault("_replies", {})
        if rep.get(key):
            print(f"reply SKIP key={key} status={in_reply_to_id}")
            return True

        # keep exactly one bot reply per original post
        by_status = cache.setdefault("_reply_by_status", {})  # in_reply_to_id -> reply_id
        prev = str(by_status.get(str(in_reply_to_id)) or "").strip()
        if prev:
            _ = delete_status(cfg, prev)  # best-effort, silent

        rid = post_public_reply(cfg, in_reply_to_id, text)

        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        if rid:
            # persist BOTH: per-key dedup + per-status last-reply (needed for delete+replace after restart)
            by_status[str(in_reply_to_id)] = rid
            rep[key] = ts
            save_json(CACHE_PATH, cache)  # persist immediately to avoid spam on restart
            print(f"reply OK key={key} status={in_reply_to_id}")
            return True

        print(f"reply FAILED key={key} status={in_reply_to_id}")
        return False
    except Exception as e:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        print(f"reply ERROR key={key} status={in_reply_to_id} err={e!r}")
        return False

def build_reply_ok() -> str:

    return (
        "🤖 Report received. 🚀\n"
        "Trusted accounts: ⭐ Favourite this post to add your report to the map.\n"
        "\n"
        "Pro tips (optional):\n"
        "• Type: #sticker_type: party / symbol / slogan\n"
        "• Removed: #sticker_removed\n"
        "• Seen again: #sticker_seen\n"
        "\n"
        "FCK RACISM. ✊ ALERTA ALERTA."
    )


def build_reply_pending() -> str:
    return (
        "🤖 Report received. 🚀\n"
        "Trusted accounts: ⭐ Favourite this post to add your report to the map.\n"
        "FCK RACISM. ✊ ALERTA ALERTA."
    )


def build_reply_improve(hints: list[str]) -> str:
    # keep behaviour: show optional hints, but in EN + new style
    lines = [
        "🤖 Report received. 🚀",
        "Trusted accounts: ⭐ Favourite this post to add your report to the map.",
    ]
    if hints:
        lines.append("")
        lines.append("Pro tips (optional):")
        for h in (hints or [])[:5]:
            lines.append(f"• {h}")
    lines.append("")
    lines.append("FCK RACISM. ✊ ALERTA ALERTA.")
    return "\n".join(lines)


def build_reply_missing(missing: list[str]) -> str:
    # hard reject -> NEW post required
    lines = [
        "🤖 ⚠️ We can’t process this report yet.",
        "Please create a NEW post with:",
    ]
    for m in (missing or [])[:6]:
        lines.append(f"• {m}")
    lines.append("Important: the post must mention @HeatmapofFascism.")
    lines.append("")
    lines.append("FCK RACISM. ✊ ALERTA ALERTA.")
    return "\n".join(lines)


def build_needs_info_reply(location_text: str) -> str:
    # geocode failed / unclear -> EDIT allowed
    loc = (location_text or "").strip()
    loc_part = f' (“{loc}”)' if loc else ""
    return (
        "🤖 ⚠️ Location unclear — we can’t place this on the map yet"
        + loc_part
        + ".\n"
        "Please EDIT this post and add ONE of:\n"
        "• Coordinates: lat, lon\n"
        "• Crossing + city: Street A / Street B, City\n"
        "• Street + city (house number helps)\n"
        "Important: the post must mention @HeatmapofFascism.\n"
        "\n"
        "FCK RACISM. ✊ ALERTA ALERTA."
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
        print(f"fav_check status={status_id} trusted_set=EMPTY")
        return False
    try:
        fav_accounts = get_favourited_by(cfg, status_id)
    except Exception as e:
        print(f"fav_check status={status_id} ERROR={e!r}")
        return False

    fav_norm = []
    for a in (fav_accounts or []):
        acct = None
        if isinstance(a, dict):
            acct = a.get("acct") or a.get("username")
        else:
            acct = str(a)
        fav_norm.append((acct or '').split('@')[0].strip().lower())

    print(f"fav_check status={status_id} favs={fav_norm} trusted={sorted(trusted_set)}")
    return any(x in trusted_set for x in fav_norm)

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

def apply_stale_rule(reports: Dict[str, Any]) -> int:
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
        if (today - last_seen).days >= 30:
            p["status"] = "unknown"
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
    notes: str,
    removed_at: Optional[str],
) -> Dict[str, Any]:
    # Idle background: enrich one missing entity description (cached in entities.json)
    if (len(still_pending) == 0 and int(added_pending) == 0 and int(published) == 0
        and int(removed or 0) == 0 and int(fav_removed or 0) == 0 and int(v_removed or 0) == 0
        and int(ctx_changed or 0) == 0):
        try:
            maybe_idle_enrich_entities(cfg)
        except Exception as e:
            log_line(f"idle_enrich ERROR err={e!r}")

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
            "notes": notes,

            "first_seen": created_date,
            "last_seen": created_date,
            "seen_count": 1,

            "removed_at": removed_at,

            "accuracy_m": int(accuracy_m),
            "radius_m": int(radius_m),
            "geocode_method": geocode_method,

            "location_text": location_text,
            "media": media,
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
        # default: sticker + graffiti (future-proof)
        tags_map = {
            "sticker_report": "present",
            "sticker_removed": "removed",
            "graffiti_report": "present",
            "graffiti_removed": "removed",
        }

    # tolerant alias/typo support (accept user mistakes)
    alias = {
        # legacy sticker
        "report_sticker": "sticker_report",
        # graffiti aliases + common typo
        "report_graffiti": "graffiti_report",
        "grafitty_report": "graffiti_report",
        "grafitty_removed": "graffiti_removed",
    }

    normalized = {}
    for raw_tag, ev in tags_map.items():
        if not isinstance(raw_tag, str):
            continue
        tag = raw_tag.strip().lstrip("#").lower()
        tag = alias.get(tag, tag)
        if not tag:
            continue
        ev_s = str(ev).strip().lower()
        if ev_s in ("present", "report", "reported", "seen", "exists"):
            ev_s = "present"
        elif ev_s in ("removed", "remove", "gone", "deleted"):
            ev_s = "removed"
        normalized[tag] = ev_s
    # legacy hashtag polling: disabled by default (configure explicitly if needed)
    tags_map = normalized

    for tag, event in tags_map.items():
        # Cursor/backfill logic (timeline_state.json)
        st_state = load_json(TIMELINE_STATE_PATH, {}) or {}
        tag_key = str(tag).lstrip("#")

        since_id = str(st_state.get(tag_key) or "").strip() or None

        def backfill_pages(pages: int = 10, limit: int = 40):
            out = []
            seen = set()
            max_id = None
            for _ in range(int(pages)):
                batch = get_hashtag_timeline(cfg, tag_key, max_id=max_id, limit=limit)
                if not batch:
                    break
                for it in batch:
                    _id = str(it.get("id") or "")
                    if _id and _id not in seen:
                        out.append(it)
                        seen.add(_id)
                # paginate older
                max_id = str(batch[-1].get("id") or "")
                if not max_id:
                    break
            return out

        if since_id is None:
            # first run / after reset: pull older existing posts too
            statuses = backfill_pages(pages=int(cfg.get("timeline_backfill_pages", 10) or 10), limit=40)
            # set since_id to newest id seen (so next runs are incremental)
            newest = None
            for it in statuses:
                _id = str(it.get("id") or "")
                if _id and (newest is None or int(_id) > int(newest)):
                    newest = _id
            if newest:
                st_state[tag_key] = newest
                save_json(TIMELINE_STATE_PATH, st_state)
        else:
            statuses = get_hashtag_timeline(cfg, tag_key, since_id=since_id, limit=40)
            # update since_id to newest id in this batch
            newest = None
            for it in statuses:
                _id = str(it.get("id") or "")
                if _id and (newest is None or int(_id) > int(newest)):
                    newest = _id
            if newest:
                st_state[tag_key] = newest
                save_json(TIMELINE_STATE_PATH, st_state)


        # Process oldest -> newest for stable behavior
        statuses = sorted(statuses, key=lambda x: int(str(x.get("id") or "0")))

        for st in statuses:
            yield tag, event, st

def auto_git_push_reports(cfg: dict, relpath: str = "reports.geojson", reason: str = "dirty") -> None:
    """
    Auto Git push for reports.geojson ONLY.
    Behavior:
      - immediate push on first detected change
      - then debounce window grows on continued changes: 10s, 20s, 40s, ... (configurable)
      - optional backup push window (default 4h) if still dirty for long
    Enabled by config.json: auto_mode=true (or legacy auto_push_reports=true).
    """
    try:
        if not cfg.get("auto_push_reports"):
            return

        remote = str(cfg.get("auto_push_remote", "origin"))
        branch = str(cfg.get("auto_push_branch", "main"))

        stages = cfg.get("auto_push_debounce_stages") or [10, 20, 40, 80, 160]
        try:
            stages = [int(x) for x in stages]
        except Exception:
            stages = [10, 20, 40, 80, 160]
        stages = [max(1, int(x)) for x in stages] or [10]

        backup_s = int(cfg.get("auto_push_backup_seconds", 4 * 3600))
        state_path = Path(ROOT) / ".autopush_state.json"

        def load_state():
            st = load_json(state_path, {}) or {}
            if not isinstance(st, dict):
                st = {}
            st.setdefault("last_push_ts", 0.0)
            st.setdefault("next_push_ts", 0.0)
            st.setdefault("stage_i", 0)
            st.setdefault("last_backup_ts", 0.0)
            return st

        def save_state(st):
            try:
                save_json(state_path, st)
            except Exception:
                pass

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
            log_line(f"auto_push ERROR git_status rc={rc} out={out!r}")
            return
        if not out.strip():
            return  # clean

        st = load_state()
        now = time.time()

        last_push = float(st.get("last_push_ts") or 0.0)
        next_push = float(st.get("next_push_ts") or 0.0)
        stage_i = int(st.get("stage_i") or 0)
        stage_i = max(0, min(stage_i, len(stages) - 1))

        last_backup = float(st.get("last_backup_ts") or 0.0)
        backup_due = bool(backup_s > 0 and (now - last_backup) >= float(backup_s))

        # First hit after idle => push immediately
        if last_push <= 0.0:
            push_now = True
        else:
            # During debounce window: SKIP + extend window progressively
            if (now < next_push) and (not backup_due):
                stage_i = min(stage_i + 1, len(stages) - 1)
                wait_s = stages[stage_i]
                st["stage_i"] = stage_i
                st["next_push_ts"] = now + float(wait_s)
                save_state(st)
                log_line(f"auto_push DEBOUNCE reason={reason} wait_s={wait_s} stage={stage_i+1}/{len(stages)}")
                return
            push_now = True

        if not push_now:
            return

        # Stage window after push
        st["stage_i"] = 0

        rc, out = run_git(["add", "--", relpath])
        if rc != 0:
            log_line(f"auto_push ERROR git_add rc={rc} out={out!r}")
            return

        tsmsg = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        msg = f"Auto-publish reports ({tsmsg})"
        rc, out = run_git(["commit", "-m", msg])
        if rc != 0:
            # allow "nothing to commit"
            if "nothing to commit" in (out or "").lower():
                log_line("auto_push SKIP nothing_to_commit")
                return
            log_line(f"auto_push ERROR git_commit rc={rc} out={out!r}")
            return

        rc, out = run_git(["push", remote, f"HEAD:{branch}"])
        if rc == 0:
            st["last_push_ts"] = now
            st["next_push_ts"] = now + float(stages[0])
            if backup_due:
                st["last_backup_ts"] = now
            save_state(st)
            log_line(f"auto_push OK reason={reason} remote={remote} branch={branch} file={relpath}")
        else:
            log_line(f"auto_push ERROR git_push rc={rc} out={out!r}")

    except Exception as e:
        import traceback
        log_line(f"auto_push ERROR exc={e!r}\n{traceback.format_exc()}")

def log_line(msg: str) -> None:
    print(msg, flush=True)


# === ENTITY_ENRICH_WIKI_START ===
def _wiki_best_summary(q: str, *, lang: str = "en", user_agent: str | None = None) -> tuple[str, str]:
    """
    Wikipedia lookup (EN default):
      - search best title
      - fetch summary extract
    Returns (title, 1-2 sentence summary) or ("","") on miss.
    """
    q = (q or "").strip()
    if not q:
        return ("", "")

    # ALWAYS define headers (prevents NameError)
    ua = (str(user_agent).strip() if user_agent else "HeatmapOfFascismBot")
    headers = {"User-Agent": ua}

    try:
        import time as _time
        from urllib.parse import quote as _quote

        # 1) search
        surl = f"https://{lang}.wikipedia.org/w/rest.php/v1/search/title"
        r = requests.get(surl, params={"q": q, "limit": 1}, headers=headers, timeout=MASTODON_TIMEOUT_S)

        if r.status_code == 429:
            ra = (r.headers or {}).get("Retry-After")
            try:
                wait_s = max(1, min(300, int(str(ra).strip()))) if ra else 30
            except Exception:
                wait_s = 30
            print(f"wiki_search http=429 rate_limited wait_s={wait_s}")
            _time.sleep(wait_s)
            return ("", "")

        if r.status_code != 200:
            print(f"wiki_search http={r.status_code} q={q!r}")
            return ("", "")

        js = r.json() or {}
        pages = js.get("pages") or []
        if not pages or not isinstance(pages, list):
            return ("", "")

        title = str((pages[0] or {}).get("title") or "").strip()
        if not title:
            return ("", "")

        # 2) summary
        t_enc = _quote(title, safe="")
        u2 = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{t_enc}"
        r2 = requests.get(u2, headers=headers, timeout=MASTODON_TIMEOUT_S)
        if r2.status_code != 200:
            print(f"wiki_summary http={r2.status_code} title={title!r}")
            return (title, "")

        js2 = r2.json() or {}
        extract = str(js2.get("extract") or "").strip()
        if not extract:
            return (title, "")

        # keep 1-2 sentences max
        parts = re.split(r"(?<=[.!?])\s+", extract)
        out = " ".join([p for p in parts[:2] if p]).strip()
        if len(out) > 420:
            out = out[:417].rstrip() + "..."
        return (title, out)

    except Exception as e:
        print(f"wiki_error err={e!r}")
        return ("", "")

def _is_code_category(st: str) -> bool:
    st = (st or "").strip()
    return bool(re.match(r"^auf\d+$", st, flags=re.I))

def _entity_query_normalize(q: str) -> str:
    q = str(q or "").strip()
    if not q:
        return ""
    # hard-block pure numbers or very short junk
    if re.fullmatch(r"\d+", q):
        return ""
    # common abbrev normalizations (Wikipedia-friendly)
    uq = q.upper().strip()
    if uq in ("AFD", "A.F.D."):
        return "Alternative for Germany"
    return q

def enrich_entities_idle(cfg: dict, reports: dict, *, max_per_run: int = 2) -> int:
    """
    Idle enrichment for published features:
      - fills entity_display/entity_desc (EN Wikipedia)
      - fills category_display (UI only)
    Never guesses without a source.
    """
    feats = list((reports or {}).get("features") or [])
    if not feats:
        return 0

    changed_ui = 0
    changed_entity = 0
    budget = max(0, int(max_per_run))

    for f in feats:
        props = (f or {}).get("properties") or {}

        # category_display (UI only) — does NOT consume entity budget
        st = str(props.get("sticker_type") or "").strip()
        if st:
            cd = st.upper() if _is_code_category(st) else st
            if str(props.get("category_display") or "") != cd:
                props["category_display"] = cd
                changed_ui += 1

        # entity enrichment budget
        if budget and changed_entity >= budget:
            continue
        if str(props.get("entity_desc") or "").strip():
            continue

        q = _entity_query_normalize(str(props.get("entity_raw") or ""))
        if not q:
            # fallback: use category only if it's not a code-category
            if st and st.lower() != "unknown" and not _is_code_category(st):
                q = _entity_query_normalize(st)
        if not q:
            continue

        title, summ = _wiki_best_summary(q, lang="en", user_agent=str(cfg.get("user_agent") or ""))
        if not summ:
            continue

        if title and not str(props.get("entity_display") or "").strip():
            props["entity_display"] = title
        props["entity_desc"] = summ
        changed_entity += 1

    if (changed_ui or changed_entity):
        log_line(f"entity_enrich ui={changed_ui} entity={changed_entity}")

    return int(changed_ui + changed_entity)
# === ENTITY_ENRICH_WIKI_END ===


def main_once():
    # Ensure baseline files exist
    ensure_object_file(CACHE_PATH)
    ensure_array_file(PENDING_PATH)
    ensure_reports_file()
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_array_file(SUPPORT_REQUESTS_PATH)
    ensure_object_file(SUPPORT_STATE_PATH)

    cfg = load_json(CFG_PATH, None)
    if not isinstance(cfg, dict):
        cfg = {}

    # ENV overrides (ONLY if explicitly set)
    _ap_env = os.environ.get("AUTO_PUSH_REPORTS")
    if _ap_env is not None:
        _ap = _ap_env.strip().lower() in ("1", "true", "yes", "on")
        cfg["auto_push_reports"] = _ap
        print(f"cfg_override auto_push_reports={_ap} via ENV")

    _tm_env = os.environ.get("TEST_MODE")
    if _tm_env is not None:
        _tm = _tm_env.strip().lower() in ("1", "true", "yes", "on")
        cfg["test_mode"] = _tm
        print(f"cfg_override test_mode={_tm} via ENV")

    test_mode = bool(cfg.get("test_mode", True))

    # auto_mode = ONLY Git auto-push switch
    # Backwards compatible: if auto_mode missing but auto_push_reports==true, treat as auto_mode=true
    auto_mode = bool(cfg.get("auto_mode", False))
    if ("auto_mode" not in cfg) and bool(cfg.get("auto_push_reports", False)):
        auto_mode = True

    # MUTUAL EXCLUSION: test_mode and auto_mode cannot be ON at the same time.
    # Safety-first: if both are true, keep test_mode and force auto_mode OFF.
    if test_mode and auto_mode:
        auto_mode = False
        cfg["auto_mode"] = False
        cfg["auto_push_reports"] = False
        log_line("WARN: auto_mode disabled because test_mode=True (mutually exclusive).")
    else:
        cfg["auto_mode"] = bool(auto_mode)
        cfg["auto_push_reports"] = bool(auto_mode)

    log_line(f"RUN v={__version__} test_mode={test_mode} auto_mode={auto_mode} auto_push_reports={bool(cfg.get('auto_push_reports'))}")

    # If auto_mode is ON and reports.geojson is already dirty from earlier runs, push once.
    auto_git_push_reports(cfg, reason="startup_flush")


    # secrets (new: secrets/secrets.json, legacy fallback: ./secrets.json)
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    secrets = load_json(SECRETS_PATH, None)
    if not secrets or not secrets.get("access_token"):
        legacy = ROOT / "secrets.json"
        secrets = load_json(legacy, None)
    if not secrets or not secrets.get("access_token"):
        raise SystemExit('Missing secrets (local setup required).')

    cfg["access_token"] = secrets["access_token"]
    # auto-sync managers + blacklist (following/blocks)
    managers_set, blacklist_set = sync_managers_and_blacklist(cfg)
    # manager update (private DM) - counts only, no leaks
    maybe_send_manager_update_dms(cfg, managers_set)

    added_support = ingest_support_requests(cfg, managers_set)
    if added_support:
        log_line(f"support_requests added={added_support} file={str(SUPPORT_REQUESTS_PATH)}")

    # Accuracy/radius defaults
    acc_cfg = cfg.get("accuracy") or {}

    ACC_GPS = int(acc_cfg.get("gps_m", 10))
    ACC_NODE = int(acc_cfg.get("intersection_node_m", 10))
    ACC_NEAREST = int(acc_cfg.get("intersection_nearest_m", 25))
    ACC_DEFAULT = int(acc_cfg.get("default_m", 25))
    ACC_FALLBACK = int(acc_cfg.get("fallback_m", 50))

    trusted_set = load_trusted_set(cfg)
    blacklist_set = load_blacklist_set()
    # Manager support inbox (DM): #support_anfrage
    _support_added = process_support_requests(cfg, trusted_set)
    if _support_added:
        log_line(f"support_requests added={_support_added}")

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
            print(f"pending_drop_deleted status_id={it.get('status_id')} reason={e}")
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
        normalize_reports_geojson(reports)
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

        # reporter account (for blacklist / future policies)
        acc = (st.get("account") or {}) if isinstance(st.get("account"), dict) else {}
        reporter = _acct_base(str(acc.get("acct") or acc.get("username") or ""))
        if reporter and (reporter in blacklist_set):
            # blocked is blocked: ignore silently (no processing)
            continue

        text = strip_html(st.get("content", ""))
        attachments = st.get("media_attachments", [])
        if not has_image(attachments):
            # If user attached media but no IMAGE, it's usually video/gifv/audio -> deny clearly
            try:
                kinds = {(a or {}).get('type') for a in (attachments or [])}
            except Exception:
                kinds = set()
            if kinds and ('image' not in kinds):
                reply_once(
                    cfg, cache, f"no_image_media:{status_id}", str(status_id),
                    "🤖 ⚠️ Media not supported\n\n"
                    "Please repost with ONE photo image (no video / gif / audio).\n\n"
                    "FCK RACISM. ✊ ALERTA ALERTA."
                )
                continue
            reply_once(
                cfg, cache, f"no_image:{status_id}", str(status_id),
                "🤖 ⚠️ Missing photo\n\n"
                "Please repost with ONE photo image (no video).\n\n"
                "FCK RACISM. ✊ ALERTA ALERTA."
            )
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
                "entity_raw": parse_entity(text)[0],
                "entity_key": parse_entity(text)[1],
                "entity_display": "",
                "entity_desc": "",
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
        lowacc = False
        if q and str(q).startswith('LOWACC:'):
            lowacc = True
            q = str(q)[len('LOWACC:'):].strip()
        if lowacc:
            _sid = str(locals().get('status_id') or locals().get('sid') or '').strip()
            if _sid:
                reply_once(
                    cfg, cache, f"lowacc:{_sid}", _sid,
                    "🤖 ℹ️ Location is imprecise\n\n"
                    "Street + city only is allowed, but the pin may be far off. "
                    "For accuracy please add house number / crossing or coordinates (lat, lon).\n\n"
                    "FCK RACISM. ✊ ALERTA ALERTA."
                )
        if not coords and not q:
            # NEEDS_INFO: keep the report and ask publicly for a usable location
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
                "entity_raw": parse_entity(text)[0],
                "entity_key": parse_entity(text)[1],
                "entity_display": "",
                "entity_desc": "",
                "removed_at": iso_date_from_created_at(st.get("created_at")) if event == "removed" else None,
                "media": media_urls,
                "error": "missing_location",
                "replied": [],
            }
            if reply_once(cfg, cache, f"missing_loc:{status_id}", str(status_id), build_reply_missing([
                "Ort: Koordinaten (lat, lon) ODER Straße+Stadt ODER Kreuzung+Stadt"
            ])):
                needs_item["replied"].append("missing_location")
            pending.append(needs_item)
            pending_by_source[url] = needs_item
            added_pending += 1
            continue

        created_date = iso_date_from_created_at(st.get("created_at"))
        sticker_type = parse_sticker_type(text)
        notes = parse_note(text)

        entity_raw, entity_key = parse_entity(text)
        entities = load_entities_dict()
        ent = entities.get(entity_key) if entity_key else None
        entity_display = ""
        entity_desc = ""
        if isinstance(ent, dict):
            entity_display = str(ent.get("display") or "")
            entity_desc = str(ent.get("desc") or "")
        geocode_method = "gps"
        accuracy_m = ACC_DEFAULT
        radius_m = ACC_DEFAULT
        location_text = ""
        removed_at: Optional[str] = None

        snap_note = ""

        if coords:
            lat, lon = coords
            lat = float(lat)
            lon = float(lon)

            geocode_method = "gps"
            accuracy_m = ACC_GPS
            radius_m = ACC_GPS
            location_text = f"{lat}, {lon}"

            # Conflict check: if coords + a plausible address/crossing are both present, verify they match.
            # We DO NOT overwrite coords. We only block publish if mismatch is large.
            q_conf = None
            try:
                _lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
                # drop hashtag lines
                _lines = [ln for ln in _lines if not ln.lstrip().startswith("#")]
                # drop decimal coord lines (keep only potential address/crossing lines)
                _lines = [ln for ln in _lines if not RE_COORDS.search(ln)]
                _t2 = "\n".join(_lines)
                _c2, q_conf = parse_location(_t2)
            except Exception:
                q_conf = None

            if q_conf:
                qn = heuristic_fix_crossing(str(q_conf))
                qn_norm = normalize_query(qn)
                coords_q = None
                method_q = "none"

                if qn in cache and "lat" in cache[qn] and "lon" in cache[qn]:
                    coords_q = (float(cache[qn]["lat"]), float(cache[qn]["lon"]))
                    method_q = str(cache[qn].get("method", "cache"))
                else:
                    coords_q, method_q = geocode_query_worldwide(qn, cfg["user_agent"])
                    if not coords_q:
                        coords_q, method_q = geocode_query_worldwide(qn_norm, cfg["user_agent"])
                    time.sleep(DELAY_NOMINATIM)

                if coords_q:
                    dist_m = haversine_m(float(lat), float(lon), float(coords_q[0]), float(coords_q[1]))
                    conflict_m = float(cfg.get("loc_conflict_m", 150.0))
                    if dist_m > conflict_m:
                        _sid = str(status_id)
                        needs_item = {
                            "id": item_id,
                            "status_id": _sid,
                            "status": "NEEDS_INFO",
                            "event": event,
                            "tag": tag,
                            "source": url,
                            "created_at": st.get("created_at"),
                            "created_date": created_date,
                            "lat": 0.0,
                            "lon": 0.0,
                            "accuracy_m": int(ACC_FALLBACK),
                            "radius_m": int(ACC_FALLBACK),
                            "geocode_method": "location_conflict",
                            "location_text": str(qn).strip(),
                            "sticker_type": sticker_type,
                            "removed_at": iso_date_from_created_at(st.get("created_at")) if event == "removed" else None,
                            "media": media_urls,
                            "error": "location_conflict",
                            "replied": [],
                        }
                        msg = (
                            "🤖 ⚠️ Location mismatch\n\n"
                            "Your coordinates and your address/crossing do not match.\n"
                            f"Distance ≈ {int(dist_m)} m.\n\n"
                            "Please reply with ONE correct location:\n"
                            "• Coordinates (lat, lon) OR\n"
                            "• Street+city / Crossing+city\n\n"
                            "FCK RACISM. ✊ ALERTA ALERTA."
                        )
                        if reply_once(cfg, cache, f"loc_conflict:{_sid}", _sid, msg):
                            needs_item["replied"].append("location_conflict")
                        pending.append(needs_item)
                        pending_by_source[url] = needs_item
                        added_pending += 1
                        continue

            # Snap away from road center / private areas (prefer footways),
            # but NEVER accept a big move (keeps coords truth).
            orig_lat, orig_lon = lat, lon
            _slat, _slon, _snote = snap_to_public_way(orig_lat, orig_lon, cfg["user_agent"])
            if _snote:
                SNAP_MAX_M = float(cfg.get("snap_max_m", 50.0))
                dist_m = haversine_m(orig_lat, orig_lon, float(_slat), float(_slon))
                if dist_m <= SNAP_MAX_M:
                    lat, lon = float(_slat), float(_slon)
                    snap_note = _snote
                    geocode_method = f"{geocode_method}+{_snote}"
                else:
                    # reject snap -> keep original coords
                    snap_note = f"{_snote}+rejected:{int(dist_m)}m"
        else:
            location_text = q or ""
            q_norm = normalize_query(q or "")

            if q in cache and "lat" in cache[q] and "lon" in cache[q]:
                lat, lon = float(cache[q]["lat"]), float(cache[q]["lon"])
                geocode_method = str(cache[q].get("method", "cache"))
                accuracy_m = int(cache[q].get("accuracy_m", ACC_DEFAULT))
                radius_m = int(cache[q].get("radius_m", accuracy_m))

                # Snap away from road center / private areas (prefer footways) with guard
                lat, lon, geocode_method, snap_note = maybe_snap_to_public_way(float(lat), float(lon), cfg, geocode_method)
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
                        print(f"needs_info_reply status={status_id} OK")
                    else:
                        print(f"needs_info_reply status={status_id} FAILED")
                    continue

                lat, lon = coords2
                geocode_method = method

                # Snap away from road center / private areas (prefer footways) with guard
                lat, lon, geocode_method, snap_note = maybe_snap_to_public_way(float(lat), float(lon), cfg, geocode_method)

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
            "notes": notes,
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
        # Refresh edited NEEDS_INFO items (user added location after bot reply)
        if item.get("status") == "NEEDS_INFO":
            sid = str(item.get("status_id") or "").strip()
            if sid.isdigit():
                try:
                    st = fetch_status(cfg, cfg.get("instance_url", ""), sid) or {}
                    html = str(st.get("content") or "")
                    text = re.sub(r"<[^>]+>", " ", html)
                    text = re.sub(r"\s+", " ", text).strip()
                    coords, q = parse_location(text)
                    if coords:
                        lat, lon = coords
                        item["lat"] = float(lat)
                        item["lon"] = float(lon)
                        item["accuracy_m"] = int(ACC_GPS)
                        item["radius_m"] = int(ACC_GPS)
                        item["geocode_method"] = "gps"
                        item["location_text"] = str(lat) + ", " + str(lon)
                        item["sticker_type"] = parse_sticker_type(text)
                        item["error"] = None
                        item["status"] = "PENDING"
                        item["replied_pending"] = None
                        print("needs_info_promoted status=%s -> PENDING" % sid)
                except StatusDeleted:
                    log_line("drop_needs_info status_id=%s reason=deleted_404" % sid)
                    continue
                except Exception as e:
                    try:
                        sc = getattr(getattr(e, "response", None), "status_code", None)
                    except Exception:
                        sc = None
                    if sc in (404, 410):
                        log_line("drop_needs_info status_id=%s reason=http_%s" % (sid, sc))
                        continue
                    pass
            still_pending.append(item)
            continue

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

        try:
            ok = is_approved_by_fav(cfg, str(item["status_id"]), trusted_set)
        except StatusDeleted as e:
            # Source post deleted -> drop pending item (prevents endless 404 spam)
            log_line(f"drop_pending status_id={item.get('status_id')} reason=deleted_404")
            continue
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
            notes=str(item.get("notes") or ""),
            removed_at=new_removed_at,
        )

        # carry entity/notes into published feature (popup)
        new_feat["properties"]["entity_raw"] = str(item.get("entity_raw") or "")
        new_feat["properties"]["entity_key"] = str(item.get("entity_key") or "")
        new_feat["properties"]["entity_display"] = str(item.get("entity_display") or "")
        new_feat["properties"]["entity_desc"] = str(item.get("entity_desc") or "")

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
    # Unpublish if trusted ⭐ Favourite was removed (grace window)
    grace_s = int(cfg.get('unfav_grace_seconds', 60))
    fav_checked, fav_removed = prune_unfav_published(cfg, reports, cache, trusted_set, grace_s=grace_s)
    if fav_removed:
        log_line(f"verify_fav checked={fav_checked} removed={fav_removed}")

    removed = prune_deleted_published(cfg, reports)
    if removed:
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
        print(f"prune_deleted_published removed={removed}")

    # Write outputs
    save_json(CACHE_PATH, cache)
    still_pending = [it for it in still_pending if it.get('status') != 'DROPPED']
    save_json(PENDING_PATH, still_pending)

    # reports.geojson: write+push ONLY when content changed (keeps repo clean)
    reports_dirty = bool(published or removed or fav_removed or ctx_changed or v_removed)
    # 4) Idle enrichment (Wikipedia EN) — safe, no guessing
    try:
        max_en = int(cfg.get("entity_enrich_max_per_run", 2))
    except Exception:
        max_en = 2
    if bool(cfg.get("entity_enrich_enabled", True)):
        enr = enrich_entities_idle(cfg, reports, max_per_run=max_en)
        if enr:
            reports_dirty = True
            log_line(f"entity_enrich updated={enr}")
    if reports_dirty:
        for f in (reports.get("features") or []):
            props = (f or {}).get("properties") or {}
            props.pop("last_verify_ts", None)
            props.pop("last_context_ts", None)
        normalize_reports_geojson(reports)
        save_json(REPORTS_PATH, reports)
        # ensure trailing newline
        try:
            rp = Path(REPORTS_PATH)
            t = rp.read_text(encoding="utf-8")
            if not t.endswith("\n"):
                rp.write_text(t + "\n", encoding="utf-8")
        except Exception:
            pass
        auto_git_push_reports(cfg)

    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    log_line(f"Added pending: {added_pending} | Pending left: {len(still_pending)} | Published: {published}")

    return {
        'added_pending': int(added_pending),
        'published': int(published),
        'pending_left': int(len(still_pending)),
        'removed': int(removed or 0),
        'fav_removed': int(fav_removed or 0),
        'verify_deleted_removed': int(v_removed or 0),
        'ctx_changed': int(ctx_changed or 0),
    }


def main():
    # Adaptive polling:
    # - fast when there is PENDING work
    # - backoff when idle
    stages = [2, 4, 8, 15, 30]  # seconds
    idle_i = 0
    print(f"START v={__version__}")

    while True:
        stats = None
        try:
            stats = main_once()
        except Exception as e:
            # log exception + full traceback as separate log lines (grep-friendly)
            log_line(f"loop ERROR err={e!r}")
            try:
                _append(ERROR_LOG_PATH, f"{_now_iso()} loop ERROR err={e!r}")
            except Exception:
                pass
            for ln in traceback.format_exc().splitlines():
                log_line(ln)
                try:
                    _append(ERROR_LOG_PATH, ln)
                except Exception:
                    pass

        # Define "activity"
        pending_left = int((stats or {}).get("pending_left", 0) or 0)
        did_work = bool(
            (stats or {}).get("added_pending")
            or (stats or {}).get("published")
            or (stats or {}).get("removed")
            or (stats or {}).get("fav_removed")
            or (stats or {}).get("verify_deleted_removed")
            or (stats or {}).get("ctx_changed")
        )

        # Sleep logic
        if pending_left > 0:
            # keep approval/review responsive
            sleep_s = 5
            idle_i = 0
        elif did_work:
            sleep_s = stages[0]
            idle_i = 0
        else:
            idle_i = min(idle_i + 1, len(stages) - 1)
            sleep_s = stages[idle_i]

        time.sleep(float(sleep_s))


def normalize_reports_geojson(reports: dict) -> None:
    entities = load_entities_dict()

    def _ym_fields(d: str):
        # expects ISO date 'YYYY-MM-DD' (or empty)
        d = (str(d or "")).strip()
        if len(d) < 7:
            return None
        y = d[0:4]
        m = d[5:7] if len(d) >= 7 else ""
        if not (y.isdigit() and m.isdigit()):
            return None
        yi = int(y)
        mi = int(m)
        if yi < 1900 or yi > 2100 or mi < 1 or mi > 12:
            return None
        return yi, mi, f"{y}-{m}"

    # Hard safety: ensure properties.lat/lon exist and match geometry (GeoJSON is [lon,lat]).
    feats = (reports or {}).get("features") or []
    for f in feats:
        if not isinstance(f, dict):
            continue
        g = f.get("geometry") or {}
        coords = g.get("coordinates")
        if not (isinstance(coords, (list, tuple)) and len(coords) == 2):
            continue
        try:
            lon = float(coords[0]); lat = float(coords[1])
        except Exception:
            continue

        p = f.get("properties") or {}

        # entity fields (stable for filter/search)
        ek = str(p.get("entity_key") or "").strip()

        # If entity_key is missing, allow reviewed/code entities via whitelist:
        # If sticker_type matches a key in entities.json, we treat it as entity_key (no guessing).
        st = str(p.get("sticker_type") or "").strip()
        if (not ek) and st and isinstance(entities, dict) and (st in entities):
            p["entity_key"] = st
            ek = st
        if ek and isinstance(entities, dict) and ek in entities:
            ent = entities.get(ek) or {}
            if not p.get("entity_display"):
                p["entity_display"] = str(ent.get("display") or "")
            if not p.get("entity_desc"):
                p["entity_desc"] = str(ent.get("desc") or "")
        else:
            p.setdefault("entity_display", "")
            p.setdefault("entity_desc", "")

        # Derived date fields for statistics (avoid duplicate truth)
        fs = p.get("first_seen") or ""
        ls = p.get("last_seen") or ""
        _fs = _ym_fields(fs)
        _ls = _ym_fields(ls)
        if _fs:
            p["first_seen_year"], p["first_seen_month"], p["first_seen_ym"] = _fs
        else:
            p["first_seen_year"], p["first_seen_month"], p["first_seen_ym"] = None, None, ""
        if _ls:
            p["last_seen_year"], p["last_seen_month"], p["last_seen_ym"] = _ls
        else:
            p["last_seen_year"], p["last_seen_month"], p["last_seen_ym"] = None, None, ""

        # Always enforce props lat/lon from geometry (HIERARCHY: geometry is truth)
        p["lat"] = lat
        p["lon"] = lon

        # Professional property order (readable diffs)
        key_order = [
            "id","source","status_id","instance_url",
            "status","sticker_type","category_display",
            "entity_raw","entity_key","entity_display","entity_desc",
            "notes",
            "first_seen","last_seen","seen_count","removed_at",
            "first_seen_year","first_seen_month","first_seen_ym",
            "last_seen_year","last_seen_month","last_seen_ym",
            "accuracy_m","radius_m","geocode_method","snap_note",
            "location_text","lat","lon","media",
        ]
        p2 = {}
        for k in key_order:
            if k in p:
                p2[k] = p[k]
        # keep any remaining keys (sorted for stable output)
        for k in sorted(p.keys()):
            if k not in p2:
                p2[k] = p[k]

        f["properties"] = p2

if __name__ == "__main__":
    import sys

    if "--refresh-types" in sys.argv:
        # Usage: python3 bot.py --refresh-types 50
        try:
            i = sys.argv.index("--refresh-types")
            limit = 50
            if i + 1 < len(sys.argv):
                try:
                    limit = int(sys.argv[i + 1])
                except Exception:
                    limit = 50
        except Exception:
            limit = 50

        # Minimal init (NO auto push)
        ensure_reports_file()

        cfg = load_json(CFG_PATH, None)
        if not isinstance(cfg, dict):
            cfg = {}

        # secrets (new: secrets/secrets.json, legacy fallback: ./secrets.json)
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        secrets = load_json(SECRETS_PATH, None)
        if not secrets or not secrets.get("access_token"):
            legacy = load_json(ROOT / "secrets.json", None)
            if legacy and legacy.get("access_token"):
                secrets = legacy

        if not secrets or not secrets.get("access_token"):
            print("refresh_types ERROR: missing access_token in secrets.")
            raise SystemExit(2)

        cfg["access_token"] = secrets["access_token"]

        reports = load_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})
        updated = refresh_types_features(cfg, reports, limit=limit)
        if updated:
            normalize_reports_geojson(reports)
            save_json(REPORTS_PATH, reports)
        raise SystemExit(0)

    if "--once" in sys.argv:
        main_once()
    else:
        main()
