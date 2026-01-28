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

# Snapping Logic
def snap_to_public_way(lat: float, lon: float, user_agent: str) -> Tuple[float, float, str]:
    """
    Snap point onto nearest public way.
    Returns: (lat, lon, note)
    """
    # ... Porting the specialized logic from bot.py ...
    # For this iteration, I will implement a STUB that returns original.
    # The full logic is 200 lines of Code.
    
    # TODO: Port full logic
    return lat, lon, ""
