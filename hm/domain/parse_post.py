import re
from typing import Tuple, List, Dict, Any, Optional
from ..core.constants import RE_REPORT_TYPE, RE_NOTE, RE_COORDS, RE_ADDRESS, RE_CROSS, RE_STREET_CITY, RE_INTERSECTION
from ..core.models import Kind

def strip_html(s: str) -> str:
    s = s or ""
    s = re.sub(r"</p>\s*<p[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()

def parse_type_and_medium(text: str) -> Tuple[Optional[Kind], str, Optional[str]]:
    """Return (Kind, sticker_type, err)."""
    kinds = []
    vals = []
    for m in RE_REPORT_TYPE.finditer(text or ""):
        k = (m.group("kind") or "").strip().lower()
        if k == "grafitti":
            k = "graffiti"
        v = (m.group("val") or "").strip()
        if v:
            kinds.append(k)
            vals.append(v)
    
    if not kinds:
        return None, "unknown", None
    
    has_st = "sticker" in kinds
    has_gr = "graffiti" in kinds
    
    if has_st and has_gr:
        return None, "unknown", "conflict"
        
    kind = Kind.GRAFFITI if has_gr else Kind.STICKER
    val = vals[0] if vals and vals[0] else "unknown"
    return kind, val, None

def parse_note(text: str) -> str:
    m = RE_NOTE.search(text or "")
    if not m:
        return ""
    t = (m.group(1) or "").strip()
    return t[:500]

def normalize_location_line(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"(?i)^\s*(ort|location|place)\s*:\s*", "", s)
    s = re.sub(r"(?i)(?<=\w)str\.\b", "straße", s)
    s = re.sub(r"(?i)(?<=\w)str\b", "straße", s)
    s = re.sub(r"\.,", ",", s)
    s = re.sub(r"\s+", " ", s)
    return s

def heuristic_fix_crossing(candidate: str) -> str:
    # Heuristic: allow missing comma before city for crossings.
    # Examples: "A / B Hamburg" -> "A / B, Hamburg"
    if ("," not in candidate) and any(sep in candidate for sep in (" / ", " x ", " & ")):
        parts = candidate.rsplit(" ", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            candidate = f"{parts[0].strip()}, {parts[1].strip()}"
    return candidate

def parse_location(text: str) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    import html as _html
    text = _html.unescape(text)

    # Accept DMS coord formats (Google Maps) before RE_COORDS
    def _coords_dms(ss: str):
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
        return bool(re.fullmatch(r"(?:@\w+(?:@\w+)?)(?:\s+@\w+(?:@\w+)?)*", ln))

    for ln in lines:
        low = ln.lower()
        if low.startswith("#"):
            continue
        if low.startswith("@") and is_pure_mentions(ln):
            continue

        candidate = heuristic_fix_crossing(normalize_location_line(ln))

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

def has_image(attachments: List[Dict[str, Any]]) -> bool:
    for a in attachments or []:
        if a.get("type") == "image" and a.get("url"):
            return True
    return False
