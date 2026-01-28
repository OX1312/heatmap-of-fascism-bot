import math
import re
import time
from typing import Optional, Tuple, List, Dict, Any
# Import requests directly since we implemented helpers inline or use requests
import requests
# from ..adapters.umap_api import api_get, api_post <--- REMOVED
# Re-implementing simplified versions here using requests directly might be easier if adapters are too thin.
import requests
from .dedup import haversine_m
from ..core.constants import (
    OVERPASS_TIMEOUT_S, NOMINATIM_TIMEOUT_S, 
    MAX_GEOM_POINTS_PER_STREET, MAX_SEARCH_RADIUS_M
)
from ..utils.log import log_line

# Constants
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
)

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

def geocode_nominatim(query: str, user_agent: str) -> Optional[Tuple[float, float]]:
    headers = {"User-Agent": user_agent}
    params = {"q": query, "format": "json", "limit": 1}
    try:
        r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=NOMINATIM_TIMEOUT_S)
        if r.status_code == 200:
            data = r.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None

def geocode_query_worldwide(query: str, user_agent: str) -> Tuple[Optional[Tuple[float, float]], str]:
    """
    Returns (coords, method).
    Simplification of original logic.
    """
    # 1. Try Nominatim
    c = geocode_nominatim(query, user_agent)
    if c:
        return c, "nominatim"
    
    # 2. Try Overpass Intersection if generic query fails? 
    # (Original had regex for 'intersection of A and B')
    # For now, let's keep it simple.
    
    return None, "none"

# =========================
# COORDINATE PROJECTION HELPERS
# =========================

def _xy_m(lat0: float, lon0: float, lat: float, lon: float) -> Tuple[float, float]:
    """
    Equirectangular projection around (lat0, lon0) -> meters.
    
    Convert lat/lon differences to local x,y coordinates in meters
    for fast distance calculations in small areas.
    
    Args:
        lat0, lon0: Reference point (origin)
        lat, lon: Point to project
        
    Returns:
        (x, y) coordinates in meters relative to (lat0, lon0)
    """
    R = 6371000.0  # Earth radius in meters
    x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R
    return x, y

def _latlon_from_xy(lat0: float, lon0: float, x: float, y: float) -> Tuple[float, float]:
    """
    Inverse equirectangular projection: meters -> lat/lon.
    
    Convert local x,y meters back to lat/lon coordinates.
    
    Args:
        lat0, lon0: Reference point (origin)
        x, y: Coordinates in meters
        
    Returns:
        (lat, lon) coordinates
    """
    R = 6371000.0
    lat = lat0 + math.degrees(y / R)
    lon = lon0 + math.degrees(x / (R * math.cos(math.radians(lat0))))
    return lat, lon

def _nearest_point_on_polyline_m(
    lat0: float, lon0: float,
    pts: List[Tuple[float,float]],
    qlat: float, qlon: float
) -> Tuple[float, float, float, Tuple[float, float]]:
    """
    Find nearest point on a polyline to a query point.
    
    Uses equirectangular projection for fast distance calculations.
    Projects onto each line segment and finds the global minimum.
    
    Args:
        lat0, lon0: Reference point for projection
        pts: List of (lat, lon) tuples forming the polyline
        qlat, qlon: Query point coordinates
        
    Returns:
        (best_lat, best_lon, best_dist_m, best_seg_dir_xy_unit)
        - best_lat, best_lon: Closest point on polyline
        - best_dist_m: Distance in meters
        - best_seg_dir_xy_unit: (ux, uy) unit vector of segment direction
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
        if seg2 <= 1e-9:  # Skip degenerate segments
            continue
        
        # Project query point onto line segment (parameterized as a + t*(b-a))
        t = ((qx - ax)*dx + (qy - ay)*dy) / seg2
        if t < 0.0: t = 0.0  # Clamp to segment endpoints
        if t > 1.0: t = 1.0
        px, py = ax + t*dx, ay + t*dy
        dist = ((qx - px)**2 + (qy - py)**2) ** 0.5

        # Unit direction vector of segment
        seg_len = (seg2 ** 0.5)
        ux, uy = dx/seg_len, dy/seg_len

        if best is None or dist < best[2]:
            plat, plon = _latlon_from_xy(lat0, lon0, px, py)
            best = (plat, plon, dist, (ux, uy))

    if best is None:
        return qlat, qlon, float("inf"), (1.0, 0.0)
    return best

# =========================
# LOCATION SNAPPING
# =========================

def snap_to_public_way(lat: float, lon: float, user_agent: str) -> Tuple[float, float, str]:
    """
    Snap a GPS point onto the nearest public walkable area using OpenStreetMap data.
    
    This prevents reports from being placed in the middle of roads, inside private
    properties, or on top of buildings. The strategy is:
    
    1. **Prefer street furniture POIs** (benches, waste bins, lamps) within 15m
       - Most precise placement for sticker reports
       
    2. **Prefer walkable ways** (footways, paths, pedestrian areas) within 120m
       - Where people actually walk and see stickers
       
    3. **Accept roads as fallback** with 10m sideways offset within 120m
       - Offset to approximate sidewalk location
       
    4. **Wider search for walkways** if only roads found (up to 220m)
       - Don't give up too easily on finding proper walkways
       
    5. **Avoid buildings** with additional 14m offset if detected within 6m
       - Prevent "inside building" placements
    
    Uses Overpass API queries to OpenStreetMap, respecting the user_agent for
    rate limiting and politeness. Multiple fallback endpoints are tried.
    
    Args:
        lat: Original latitude
        lon: Original longitude  
        user_agent: User-Agent string for OSM API requests
        
    Returns:
        (snapped_lat, snapped_lon, note) where:
        - snapped_lat, snapped_lon: Adjusted coordinates (or original if no snap)
        - note: Empty string if no snap, otherwise describes what happened:
            - "snap_poi:bench" / "snap_poi:waste" / "snap_poi:lamp"
            - "snap_walk:footway" / "snap_walk:path" / etc.
            - "snap_road_offset:residential" (offset to sidewalk)
            - "|avoid_building" appended if building avoidance was applied
    
    Implementation Notes:
        - Uses equirectangular projection for fast distance calculations
        - All Overpass queries have 25s timeout
        - Filters out private/indoor ways based on OSM tags
        - Road offset uses perpendicular vector pointing toward original location
    """
    # Search radii (meters)
    R_M = 120         # Base radius for highways
    R_WALK_M = 220    # Extended radius for walkways-only search
    
    # Offset distances (meters)
    OFFSET_ROAD_M = 10.0       # Sideways offset for roads
    OFFSET_BUILDING_M = 14.0   # Additional push away from buildings
    
    lat0, lon0 = lat, lon

    # ----- Helper: Check if OSM way is publicly accessibly -----
    def is_public(tags: Dict[str, Any]) -> bool:
        """Check if an OSM way is publicly accessible based on tags."""
        if not isinstance(tags, dict):
            return True
        
        # Check access restrictions
        acc = (tags.get("access") or "").strip().lower()
        if acc in {"private", "no"}:
            return False
        foot = (tags.get("foot") or "").strip().lower()
        if foot in {"no", "private"}:
            return False
        
        # Private service roads (driveways, parking aisles)
        if (tags.get("highway") or "").strip().lower() == "service":
            svc = (tags.get("service") or "").strip().lower()
            if svc in {"driveway", "parking_aisle"}:
                return False
        
        # Indoor ways
        indoor = (tags.get("indoor") or "").strip().lower()
        if indoor in {"yes", "1", "true"}:
            return False
        
        return True

    # ----- Helper: Check if point is near a building -----
    def building_nearby(qlat: float, qlon: float, r_m: int = 6) -> bool:
        """
        Check if there's a building within r_m meters of the point.
        Very small radius: only catches "landed on building" cases.
        """
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

    # ----- Helper: Fetch street furniture POIs -----
    def fetch_pois(r_m: int) -> List[Dict[str, Any]]:
        """
        Fetch public-ish street furniture POIs suitable for sticker reports.
        Includes benches, waste bins, and street lamps.
        """
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

    # ----- Helper: Find nearest POI -----
    def nearest_public_poi(r_m: int = 15) -> Optional[Tuple[float, float, str]]:
        """
        Find nearest public street furniture POI.
        Returns (lat, lon, note) or None.
        """
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
                # Determine POI type for note
                note = "poi"
                if (tags.get("leisure") or "").strip().lower() == "bench":
                    note = "bench"
                elif (tags.get("amenity") or "").strip().lower() in {"waste_basket", "waste_disposal"}:
                    note = "waste"
                elif (tags.get("highway") or "").strip().lower() == "street_lamp":
                    note = "lamp"
                best = (d, plat, plon, note)
        return None if best is None else (best[1], best[2], best[3])

    # ----- Helper: Fetch highways -----
    def fetch_highways(r_m: int, only_walk: bool) -> List[Dict[str, Any]]:
        """
        Fetch highway ways from OSM.
        If only_walk=True, only fetch walkable types (footway, path, etc).
        Otherwise fetch all highways.
        """
        if only_walk:
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

    # Highway type categories
    walk_hw = {"footway", "path", "pedestrian", "steps", "cycleway"}
    road_hw = {"living_street", "residential", "service", "unclassified", "tertiary", "secondary", "primary"}

    # ----- Helper: Collect candidates from OSM elements -----
    def collect_candidates(elems: List[Dict[str, Any]]) -> List[Tuple[str, List[Tuple[float, float]], Dict[str, Any]]]:
        """
        Parse OSM way elements into candidate ways for snapping.
        Returns list of (highway_type, points, tags) tuples.
        """
        cands = []
        for e in elems:
            if e.get("type") != "way":
                continue
            tags = e.get("tags") or {}
            hw = (tags.get("highway") or "").strip().lower()
            if not hw:
                continue
            if not is_public(tags):
                continue
            
            # Extract geometry
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
            
            # Categorize as walk, road, or other
            kind = "walk" if hw in walk_hw else ("road" if hw in road_hw else "other")
            if kind == "other":
                continue
            
            cands.append((hw, pts, tags))
        return cands

    # =========================
    # MAIN SNAPPING LOGIC
    # =========================

    # Step 0: Prefer nearby public street-furniture POIs (most precise)
    poi = nearest_public_poi(r_m=15)
    if poi:
        plat, plon, pnote = poi
        # Sanity check: ignore POIs that would land on/inside buildings
        if not building_nearby(plat, plon, r_m=4):
            return plat, plon, f"snap_poi:{pnote}"

    # Step 1: Fetch highways in base radius
    elems = fetch_highways(R_M, only_walk=False)
    cands = collect_candidates(elems)
    if not cands:
        return lat, lon, ""  # No ways found, return original

    # Step 2: Find best candidate (prefer walkable over roads)
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

    plat, plon, dist, (ux, uy), hw, kind = best

    # Step 3: If only road found, try wider search for walkways
    if kind == "road":
        elems2 = fetch_highways(R_WALK_M, only_walk=True)
        cands2 = collect_candidates(elems2)
        best_walk = None
        for hw2, pts2, tags2 in cands2:
            plat2, plon2, dist2, segdir2 = _nearest_point_on_polyline_m(lat0, lon0, pts2, lat0, lon0)
            if best_walk is None or dist2 < best_walk[2]:
                best_walk = (plat2, plon2, dist2, segdir2, hw2, "walk")
        # Accept walkway if not too far
        if best_walk is not None and best_walk[2] <= 45.0:
            plat, plon, dist, (ux, uy), hw, kind = best_walk

    note = f"snap_{kind}:{hw}"

    # Step 4: Road offset - push sideways off centerline
    if kind == "road":
        # Perpendicular normal to segment
        nx, ny = (-uy, ux)
        
        # Choose side that points toward original location (reduces wrong-side jumps)
        sx, sy = _xy_m(lat0, lon0, plat, plon)   # snapped -> meters
        ox, oy = _xy_m(lat0, lon0, lat0, lon0)   # original -> (0,0)
        vx, vy = (ox - sx), (oy - sy)            # snapped -> original
        if (vx*nx + vy*ny) < 0:
            nx, ny = (-nx, -ny)
        
        # Apply offset
        sx2, sy2 = (sx + nx*OFFSET_ROAD_M), (sy + ny*OFFSET_ROAD_M)
        plat, plon = _latlon_from_xy(lat0, lon0, sx2, sy2)
        note = f"snap_road_offset:{hw}"

    # Step 5: Building avoidance - push further away if still too close
    if building_nearby(plat, plon, r_m=6):
        if kind == "road":
            # Reuse perpendicular direction
            nx, ny = (-uy, ux)
            sx, sy = _xy_m(lat0, lon0, plat, plon)
            sx2, sy2 = (sx + nx*OFFSET_BUILDING_M), (sy + ny*OFFSET_BUILDING_M)
            plat, plon = _latlon_from_xy(lat0, lon0, sx2, sy2)
            note += "|avoid_building"
        else:
            # Walk: minimal nudge
            nx, ny = (-uy, ux)
            sx, sy = _xy_m(lat0, lon0, plat, plon)
            sx2, sy2 = (sx + nx*4.0), (sy + ny*4.0)
            plat, plon = _latlon_from_xy(lat0, lon0, sx2, sy2)
            note += "|avoid_building"

    return plat, plon, note

