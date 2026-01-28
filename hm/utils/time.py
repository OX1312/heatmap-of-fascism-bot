from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

TZ_BERLIN = ZoneInfo("Europe/Berlin")

def now_berlin() -> datetime:
    return datetime.now(TZ_BERLIN)

def now_iso() -> str:
    """Local time, human readable (no 'T', no timezone suffix)."""
    return datetime.now().strftime("%Y-%m-%d // %H:%M:%S")

def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def iso_date_from_created_at(created_at: Optional[str]) -> str:
    if not created_at:
        return today_iso()
    return created_at[:10]
