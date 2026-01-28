import json
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List
import re

from ..utils.log import log_line

def load_sources_map(sources_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load sources.json into a dict keyed by ID."""
    try:
        data = json.loads(sources_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {item["id"]: item for item in data if "id" in item}
        return {}
    except Exception:
        return {}

def fetch_wikipedia_summary(url: str) -> Optional[str]:
    """
    Fetch definition from Wikipedia URL.
    Extracts the first paragraph or description.
    """
    try:
        # Convert standard URL to API URL
        # e.g. https://de.wikipedia.org/wiki/AUF1 -> https://de.wikipedia.org/api/rest_v1/page/summary/AUF1
        match = re.search(r"https://([a-z]+)\.wikipedia\.org/wiki/(.+)$", url)
        if not match:
            return None
            
        lang, title = match.groups()
        api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
        
        headers = {"User-Agent": "HeatmapOfFascismBot/1.0.0 (Research)"}
        resp = requests.get(api_url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("extract")
    except Exception as e:
        log_line(f"ENRICH WARN | wiki fetch failed {e!r}")
    return None

def enrich_entity(entity_key: str, entities_path: Path, sources_path: Path) -> bool:
    """
    Attempt to enrich a specific entity with description from its sources.
    Returns True if updated.
    """
    try:
        entities = json.loads(entities_path.read_text(encoding="utf-8"))
        ent = entities.get(entity_key)
        if not ent:
            return False
            
        # If we already have a desc, skip (unless we want to force update)
        # if ent.get("desc") and len(ent.get("desc")) > 20:
        #     return False
            
        sources_map = load_sources_map(sources_path)
        source_keys = ent.get("sources", [])
        
        new_desc = ""
        
        for sk in source_keys:
            src = sources_map.get(sk)
            if not src: 
                continue
                
            url = src.get("url", "")
            if "wikipedia.org" in url:
                summary = fetch_wikipedia_summary(url)
                if summary:
                    new_desc = f"{summary} (Source: Wikipedia)"
                    break
        
        if new_desc and new_desc != ent.get("desc"):
            ent["desc"] = new_desc
            entities[entity_key] = ent
            
            # Save back
            # Use atomic save provided by utils.files if possible, else direct
            # Here assuming simple write for this tool
            entities_path.write_text(json.dumps(entities, indent=2, ensure_ascii=False), encoding="utf-8")
            log_line(f"ENRICH OK | key={entity_key} source=wiki")
            return True
            
    except Exception as e:
        log_line(f"ENRICH ERROR | {e!r}", "ERROR")
        
    return False
