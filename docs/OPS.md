# Ops (Public-safe overview)

This project is a Mastodon bot that produces a public GeoJSON dataset (`reports.geojson`) used by a map.

## Reliability goals
- bot must not crash on missing/invalid runtime files
- secrets never appear in GitHub
- runtime state/logs are local-only

## What to verify after changes (high level)
- code compiles
- one-shot run completes without errors
- public dataset format remains valid (GeoJSON)
- no secrets or runtime state were accidentally committed

Detailed operational procedures are intentionally not included in public docs.
