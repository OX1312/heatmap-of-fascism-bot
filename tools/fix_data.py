#!/usr/bin/env python3
"""
Script to fix common issues in the reports.geojson file.
This script loads `reports.geojson` and `entities.json`, then applies the
following fixes:
* Fill missing `entity_display` and `entity_desc` fields using the
  description in `entities.json` when a matching `entity_key` or
  `sticker_type` exists.
* Remove features with invalid coordinates (lat/lon not numeric or outside
  valid ranges).
* Deduplicate entries: if two features share the same URL or nearly identical
  coordinates, only the first occurrence is kept. Duplicate entries are
  dropped.
* Fill missing required properties (status, category, first_seen, last_seen)
  when possible from the entity or raw text (left blank otherwise).
The script writes the cleaned data back to the original file and prints a
summary of changes. A backup of the original file is stored in the same
directory with `.backup` appended to the filename.
Usage:
    python tools/fix_data.py --reports reports.geojson --entities entities.json
"""
import argparse, json, shutil
from pathlib import Path
from typing import Dict, Tuple, Any

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: Path, data: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\\n")
    shutil.move(tmp_path, path)

def fix_reports(reports: dict, entities: Dict[str, Any]) -> Tuple[dict, int, int, int]:
    features = reports.get("features", [])
    new_features = []
    seen_urls: set[str] = set()
    seen_coords: set[Tuple[int, int]] = set()
    fixed_count = 0
    removed_count = 0
    deduped_count = 0
    for feat in features:
        props = feat.get("properties", {})
        key = str(props.get("entity_key") or props.get("sticker_type") or "").strip()
        ent = entities.get(key)
        lat = props.get("lat")
        lon = props.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            removed_count += 1
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            removed_count += 1
            continue
        coord_key = (round(lat, 5), round(lon, 5))
        url = str(props.get("url") or "").strip()
        if url in seen_urls:
            deduped_count += 1
            continue
        if coord_key in seen_coords:
            deduped_count += 1
            continue
        seen_urls.add(url)
        seen_coords.add(coord_key)
        if ent:
            if not props.get("entity_display"):
                props["entity_display"] = ent.get("display") or ""
                fixed_count += 1
            if not props.get("entity_desc"):
                props["entity_desc"] = ent.get("desc") or ""
                fixed_count += 1
        if not props.get("category"):
            props["category"] = props.get("entity_display") or key
        if not props.get("status"):
            props["status"] = "present"
        if not props.get("first_seen"):
            props["first_seen"] = props.get("last_seen") or ""
        if not props.get("last_seen"):
            props["last_seen"] = props.get("first_seen") or ""
        new_features.append(feat)
    new_reports = reports.copy()
    new_reports["features"] = new_features
    return new_reports, fixed_count, removed_count, deduped_count

def main() -> None:
    parser = argparse.ArgumentParser(description="Fix reports GeoJSON file")
    parser.add_argument("--reports", default="reports.geojson")
    parser.add_argument("--entities", default="entities.json")
    args = parser.parse_args()
    reports = load_json(Path(args.reports))
    entities = load_json(Path(args.entities))
    cleaned, fixed_count, removed_count, deduped_count = fix_reports(reports, entities)
    backup_path = Path(args.reports).with_suffix(Path(args.reports).suffix + ".backup")
    Path(args.reports).replace(backup_path)
    save_json(Path(args.reports), cleaned)
    print(f"Applied fixes: {fixed_count} fields filled, removed {removed_count} entries, deduped {deduped_count} entries")
    print(f"Original file backed up as: {backup_path}")

if __name__ == "__main__":
    main()
