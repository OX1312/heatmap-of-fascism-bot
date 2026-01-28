import re

# Regex Constants
RE_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
RE_ADDRESS = re.compile(r"^(.+?)\s+(\d+[a-zA-Z]?)\s*,\s*(.+)$")  # "Street 12, City"
RE_STREET_CITY = re.compile(r"^(.+?)\s*,\s*(.+)$")  # "Street, City"
RE_CROSS = re.compile(r"^(.+?)\s*(?:/| x | & )\s*(.+?)\s*,\s*(.+)$", re.IGNORECASE)  # "A / B, City"
RE_INTERSECTION = re.compile(r"^\s*intersection of\s+(.+?)\s+and\s+(.+?)\s*,\s*(.+?)\s*$", re.IGNORECASE)
RE_REPORT_TYPE = re.compile(
    r"(?im)^\s*#(?P<kind>sticker|graffiti|grafitti)_(?:type|typ)\s*:?\s*(?P<val>[^\n#@]{1,200}?)"
    r"(?=\s*(?:(ort|location|place)\s*:|@|#|$))"
)
RE_NOTE = re.compile(r"(?is)(?:^|\s)#note\s*:\s*(.+?)(?=(?:\s#[\w_]+)|$)")

# Limits
MAX_GEOM_POINTS_PER_STREET = 500
MAX_SEARCH_RADIUS_M = 120    # near match radius
WAIT_DAYS_UNKNOWN = 30       # days before marking unknown

# Timeouts
OVERPASS_TIMEOUT_S = 45
NOMINATIM_TIMEOUT_S = 25
MASTODON_TIMEOUT_S = 25

# Accuracy / Radius (Meters)
# GPS is high confidence.
ACC_GPS = 15
# Exact node from overpass is very high confidence.
ACC_NODE = 10
# Nearest point on street is medium.
ACC_NEAREST = 30
# Default fallback (geocoding without precise details)
ACC_DEFAULT = 50
# City-level fallback or vague location
ACC_FALLBACK = 2000
