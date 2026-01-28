import requests
import re
from typing import Dict, Any, Optional, List, Tuple
from ..utils.log import log_line

# Constants matching bot.py
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
)

NOMINATIM_TIMEOUT_S = 25
OVERPASS_TIMEOUT_S = 45

def geocode_nominatim(query: str, user_agent: str) -> Optional[Tuple[float, float]]:
    headers = {"User-Agent": user_agent}
    params = {"q": query, "format": "json", "limit": 1}
    try:
        r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=NOMINATIM_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        log_line(f"WARN | geocode_nominatim failed | err={e!r}")
        return None

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

    # If matching failed, split roughly (fallback)
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

    # Brute-force min distance (simple n*m)
    min_d2 = float("inf")
    best_pair = None

    for (lat_a, lon_a) in pts_a:
        for (lat_b, lon_b) in pts_b:
            d2 = (lat_a - lat_b)**2 + (lon_a - lon_b)**2
            if d2 < min_d2:
                min_d2 = d2
                best_pair = ((lat_a, lon_a), (lat_b, lon_b))
    
    if best_pair:
        (la, loa), (lb, lob) = best_pair
        return ((la+lb)/2.0, (loa+lob)/2.0), "overpass_nearest"

    return None
