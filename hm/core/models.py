from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple

class Kind(str, Enum):
    STICKER = "sticker"
    GRAFFITI = "graffiti"
    UNKNOWN = "unknown"

class Status(str, Enum):
    PRESENT = "present"
    REMOVED = "removed"
    UNKNOWN = "unknown"

@dataclass
class ParsedPost:
    text: str
    created_at: str
    status_id: str
    instance_url: str
    account_acct: str
    media_attachments: List[Dict[str, Any]] = field(default_factory=list)
    replies: List[Dict[str, Any]] = field(default_factory=list)
    
    # extracted info
    kind: Kind = Kind.UNKNOWN
    category: str = "unknown" # free text (e.g. sticker type)
    lat: Optional[float] = None
    lon: Optional[float] = None
    location_text: Optional[str] = None
    is_removal: bool = False
    is_seen: bool = False

@dataclass
class Report:
    type: str = "Feature"
    geometry: Dict[str, Any] = field(default_factory=dict)
    properties: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PipelineResult:
    processed_count: int = 0
    published_count: int = 0
    pending_count: int = 0
    errors: List[str] = field(default_factory=list)
