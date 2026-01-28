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
ACC_FALLBACK = 2000
