#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hm.domain.enrichment import enrich_entity
from hm.utils.files import load_json

def main():
    entities_path = ROOT / "entities.json"
    sources_path = ROOT / "docs/sources.json"
    
    print("Loading entities...")
    entities = load_json(entities_path, {})
    
    print(f"Found {len(entities)} entities. Starting enrichment...")
    
    updated_count = 0
    for key in entities:
        print(f"Enriching '{key}'...")
        if enrich_entity(key, entities_path, sources_path):
            print(f"  -> UPDATED {key}")
            updated_count += 1
        else:
            print(f"  -> no change or no source")
            
    print(f"Done. Updated {updated_count} entities.")

if __name__ == "__main__":
    main()
