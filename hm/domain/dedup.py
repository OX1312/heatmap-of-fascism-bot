from typing import Dict, Any, Tuple
import math

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def attempt_dedup(
    new_feat: Dict[str, Any], 
    existing_reports: Dict[str, Any]
) -> Tuple[bool, bool]:
    """
    Try to merge `new_feat` into `existing_reports`.
    Returns (merged_bool, reports_dirty_bool).
    """
    # Logic from bot.py lines 4187-4248
    
    new_p = new_feat["properties"]
    new_lat = float(new_feat["geometry"]["coordinates"][1])
    new_lon = float(new_feat["geometry"]["coordinates"][0])
    new_r = int(new_p.get("radius_m") or 50)
    new_type = (new_p.get("sticker_type") or "unknown").lower()
    new_status = new_p.get("status")

    for f in existing_reports.get("features", []):
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) != 2: continue
        
        ex_lon, ex_lat = float(coords[0]), float(coords[1])
        ex_r = int(p.get("radius_m") or 50)
        ex_type = (p.get("sticker_type") or "unknown").lower()

        # Type match rule: match OR one side unknown
        if not (new_type == "unknown" or ex_type == "unknown" or new_type == ex_type):
            continue

        dist = haversine_m(new_lat, new_lon, ex_lat, ex_lon)
        
        # Radius overlap check
        if dist <= max(ex_r, new_r):
            # UPDATE existing
            p["last_seen"] = new_p.get("created_date")
            p["seen_count"] = int(p.get("seen_count", 1)) + 1
            
            # Status update
            if new_status == "present":
                p["status"] = "present"
                p["removed_at"] = None
            else:
                p["status"] = "removed"
                p["removed_at"] = new_p.get("removed_at")

            # Promote type
            if ex_type == "unknown" and new_type != "unknown":
                p["sticker_type"] = new_type

            # Merge media
            media = list(p.get("media") or [])
            seen = set(media)
            for u in list(new_p.get("media") or []):
                if u and u not in seen:
                    media.append(u)
                    seen.add(u)
            p["media"] = media
            
            return True, True

    return False, False
