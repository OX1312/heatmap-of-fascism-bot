import json
from pathlib import Path
from typing import Dict, Any, Optional

def normalize_reports_geojson(reports: Dict[str, Any], entities_path: Path) -> None:
    """
    Normalize all features in the reports dictionary (in-place).
    
    Ported from bot.py.bak to ensure consistency in:
    - Entity fields (display, desc, category)
    - Date fields (year, month)
    - Verification status
    """
    
    # Load entities
    entities = {}
    try:
        if entities_path.exists():
            entities = json.loads(entities_path.read_text(encoding="utf-8"))
    except Exception:
        entities = {}

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
    import re
    
    for f in feats:
        if not isinstance(f, dict):
            continue
        g = f.get("geometry") or {}
        coords = g.get("coordinates")
        if not (isinstance(coords, (list, tuple)) and len(coords) == 2):
            continue
        try:
            # Just verify they are floats
            _ = float(coords[0])
            _ = float(coords[1])
        except Exception:
            continue

        p = f.get("properties") or {}
        
        # RECOVERY: If properties are missing but description has raw data, recover it.
        # Description format: kind=sticker\ncat=1161\n...
        desc_raw = str(p.get("description") or "")
        if desc_raw:
             # Recover sticker_type (cat)
             if not p.get("sticker_type"):
                 m_cat = re.search(r"cat=([^\n]+)", desc_raw)
                 if m_cat:
                     p["sticker_type"] = m_cat.group(1).strip()
             
             # Recover medium (kind)
             if not p.get("medium"):
                 m_kind = re.search(r"kind=([^\n]+)", desc_raw)
                 if m_kind:
                     p["medium"] = m_kind.group(1).strip()

        # Ensure medium is set (default to sticker if missing)
        if not p.get("medium"):
            p["medium"] = "sticker"

        # entity fields (stable for filter/search)
        ek = str(p.get("entity_key") or "").strip()

        # Resolve entity_key from sticker_type ONLY if it is a VERIFIED key in entities.json
        st = str(p.get("sticker_type") or "").strip()
        
        # Case insensitive lookup logic
        matched_key = None
        if st:
            if st in entities:
                matched_key = st
            elif st.lower() in entities:
                matched_key = st.lower()
            else:
                # Iterate to find case-insensitive match
                sl = st.lower()
                for k in entities:
                    if k.lower() == sl:
                        matched_key = k
                        break
        
        if (not ek) and matched_key:
            p["entity_key"] = matched_key
            ek = matched_key

        # HARD POLICY: verify-or-unknown
        # - Only verified keys in entities.json may populate display/desc/category.
        # - Unknown/unverified keys must NEVER be "interpreted".
        if isinstance(entities, dict) and ek and (ek in entities):
            ent = entities.get(ek) or {}
            p["needs_verification"] = False
            p["entity_display"] = str(ent.get("display") or "")
            p["entity_desc"] = str(ent.get("desc_en") or ent.get("desc") or "")
            # category: stable key for filtering/search (never long display text)
            p["category"] = ek
            # category_display: short UI label (e.g. "AfD")
            p["category_display"] = p["entity_display"] or ek
        else:
            # keep the raw key for filtering/statistics, but do not assign meaning
            if ek:
                p["entity_key"] = ek
            p["needs_verification"] = True
            p["entity_display"] = "Unknown"
            p["entity_desc"] = "Unknown (needs verification)"
            p["category"] = "Unknown"
            p["category_display"] = "Unknown"
            
        # Derived date fields for statistics (avoid duplicate truth)
        fs = p.get("first_seen") or ""
        ls = p.get("last_seen") or ""
        
        # If created_date exists but first_seen doesn't, backfill
        if not fs and p.get("created_date"):
            fs = p["created_date"]
            p["first_seen"] = fs
            
        # If last_seen is empty, default to first_seen
        if not ls and fs:
            ls = fs
            p["last_seen"] = ls
            
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
