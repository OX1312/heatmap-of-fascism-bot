import json
from pathlib import Path
from typing import Optional, Dict, Any

# In bot.py, entities logic was mixed with IO. 
# Here we define the logic to lookup/create entities, but separating IO if possible?
# Actually bot.py loads entities.json. 
# We can make a class that holds the lookup table.

class EntityRegistry:
    def __init__(self, data: Dict[str, Any]):
        self.data = data
    
    @classmethod
    def from_file(cls, path: Path):
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return cls({})

    def lookup(self, key: str) -> Optional[Dict[str, Any]]:
        return self.data.get(key)
    
    # ... more logic as needed for wikidata/enrichment ...
