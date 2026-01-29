#!/usr/bin/env python3
import sys
import json
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hm.utils.files import save_json, load_json

def main():
    reports_path = ROOT / "reports.geojson"
    print(f"Loading reports from {reports_path}...")
    reports = load_json(reports_path, {"type": "FeatureCollection", "features": []})
    
    target_id = "masto-115967049464871568"
    patched = False
    
    for f in reports.get("features", []):
        p = f.get("properties", {})
        if p.get("id") == target_id:
            print(f"Found target item {target_id}")
            # Patch missing data based on pending.json recovery
            # Content was: #sticker_type AfD
            p["sticker_type"] = "AfD"
            # We can also add a basic description to support future recovery
            p["description"] = "kind=sticker\ncat=AfD\nstatus=present\nid=" + target_id
            
            # Fix broken link
            url = "https://mastodon.social/@troete_one/115967049464871568"
            p["url"] = url
            p["source"] = url
            patched = True
            break
            
    if patched:
        print("Patched item! Saving...")
        save_json(reports_path, reports)
    else:
        print("Item not found!")

if __name__ == "__main__":
    main()
