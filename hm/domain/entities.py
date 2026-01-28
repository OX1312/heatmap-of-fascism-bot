import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

class EntityRegistry:
    """
    Registry for known fascist entities/symbols with metadata.
    
    Entities are loaded from entities.json and can be matched against
    sticker type strings to enrich reports with structured information.
    """
    def __init__(self, data: Dict[str, Any]):
        self.data = data
    
    @classmethod
    def from_file(cls, path: Path):
        """Load entity registry from JSON file."""
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return cls({})

    def lookup(self, key: str) -> Optional[Dict[str, Any]]:
        """Look up entity by exact key (case-sensitive)."""
        return self.data.get(key)
    
    def match_entity_from_type(self, sticker_type: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Match a known entity from a sticker type string.
        
        Scans the sticker_type text for known entity keys (case-insensitive)
        and returns the first match found.
        
        Args:
            sticker_type: The type/category string from a report
                         (e.g., "NPD propaganda", "AUF1 sticker", "1161 code")
        
        Returns:
            (entity_key, entity_display) or (None, None) if no match
            - entity_key: The canonical key from entities.json
            - entity_display: The display name for that entity
        
        Examples:
            >>> registry.match_entity_from_type("NPD sticker")
            ("NPD", "NPD")  # if NPD in entities.json
            
            >>> registry.match_entity_from_type("auf1 propaganda")
            ("auf1", "AUF1")  # matches key auf1, display AUF1
            
            >>> registry.match_entity_from_type("unknown fascist symbol")
            (None, None)
        """
        if not sticker_type or sticker_type == "unknown":
            return None, None
        
        # Normalize input for matching (lowercase for case-insensitive search)
        text_lower = sticker_type.lower()
        
        # Scan all known entities
        for entity_key, entity_data in self.data.items():
            if not isinstance(entity_data, dict):
                continue
            
            # Check if entity key appears in text (case-insensitive)
            if entity_key.lower() in text_lower:
                display = entity_data.get("display", entity_key)
                return entity_key, display
        
        return None, None
