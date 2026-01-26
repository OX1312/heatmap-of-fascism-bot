#!/usr/bin/env python3
"""
Script to check the consistency of the reports.geojson file.
This script loads a GeoJSON file containing reported fascist sticker or graffiti
incidents and performs several validations:
* Missing descriptions: For each feature, if `entity_display` or `entity_desc`
  is missing but a matching entry exists in entities.json for the
  `sticker_type` or `entity_key`, it reports a missing description error.
* Coordinate validity: Ensures latitude and longitude are numeric and within
  valid ranges (–90 ≤ lat ≤ 90, –180 ≤ lon ≤ 180). Reports out-of-range values.
* Duplicate checks: Looks for duplicate URLs (same `url`) and duplicate
  coordinates (difference in lat/lon below a small threshold).
If any issues are detected, they are printed to standard output. Each line
contains the issue type and the report ID. The script exits with a non-zero
status if issues are found.
Usage:
    python tools/check_data.py --reports reports.geojson --entities entities.json
"""
import argparse, json, sys
from pathlib import Path

def load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"File not found: {path}")

def check_reports(reports: dict, entities: dict) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    seen_coords: list[tuple[float, float, str]] = []
    features = reports.get("features", [])
    for feat in features:
        props = feat.get("properties", {})
        report_id = props.get("id") or "unknown"
        sticker_type = str(props.get("entity_key") or props.get("sticker_type") or "").strip()
        ent = entities.get(sticker_type)
        if ent and (not props.get("entity_display") or not props.get("entity_desc")):
            errors.append(("missing_description", report_id))
        lat = props.get("lat")
        lon = props.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            errors.append(("invalid_coordinates", report_id))
        else:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                errors.append(("out_of_bounds_coordinates", report_id))
            for prev_lat, prev_lon, _ in seen_coords:
                if abs(lat - prev_lat) < 1e-4 and abs(lon - prev_lon) < 1e-4:
                    errors.append(("duplicate_coordinates", report_id))
            seen_coords.append((lat, lon, report_id))
        url = str(props.get("url") or "").strip()
        if url:
            if url in seen_urls:
                errors.append(("duplicate_url", report_id))
            seen_urls.add(url)
        required_fields = ["status", "sticker_type", "category", "first_seen", "last_seen"]
        for field in required_fields:
            if not props.get(field):
                errors.append((f"missing_{field}", report_id))
    return errors

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate reports GeoJSON file")
    parser.add_argument("--reports", default="reports.geojson")
    parser.add_argument("--entities", default="entities.json")
    args = parser.parse_args()
    reports = load_json(Path(args.reports))
    entities = load_json(Path(args.entities))
    errors = check_reports(reports, entities)
    if errors:
        for issue, rid in errors:
            print(f"{issue}\\t{rid}")
        print(f"\\nFound {len(errors)} issues")
        return 1
    else:
        print("No issues detected")
        return 0

if __name__ == "__main__":
    sys.exit(main())
