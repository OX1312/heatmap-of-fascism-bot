#!/usr/bin/env python3
"""
Tool to fix missing category_display and other inconsistent fields in reports.geojson.
Uses the ported logic from hm.domain.geojson_normalize.
"""
import sys
import json
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hm.domain.geojson_normalize import normalize_reports_geojson
from hm.utils.files import save_json, load_json

def main():
    reports_path = ROOT / "reports.geojson"
    entities_path = ROOT / "entities.json"
    
    print(f"Loading reports from {reports_path}...")
    reports = load_json(reports_path, {"type": "FeatureCollection", "features": []})
    
    count_before = len(reports.get("features", []))
    print(f"loaded {count_before} features.")
    
    print("Normalizing data...")
    try:
        normalize_reports_geojson(reports, entities_path)
    except Exception as e:
        print(f"Error during normalization: {e}")
        sys.exit(1)
        
    print("Saving normalized reports...")
    save_json(reports_path, reports)
    print("Done! ✅")
    
    # Verification check on first item
    if reports["features"]:
        f0 = reports["features"][0]["properties"]
        print("\nVerification check (first item):")
        print(f"ID: {f0.get('id')}")
        print(f"Category: {f0.get('category')}")
        print(f"Category Display: {f0.get('category_display')}")
        print(f"Type/Medium: {f0.get('medium')}")
        print(f"Notes: {f0.get('notes')}")

if __name__ == "__main__":
    main()
