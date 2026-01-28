# Changelog

All notable changes to the "Heatmap of Fascism" bot project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-01-28
### Added
- **Refactored Modular Architecture**: Codebase split into `core`, `domain`, `adapters`, `support`.
- **Location Snapping**: Intelligent snapping to public walkways and POIs (benches, bins) to avoid roads/private property.
- **Improved Geocoding**: Regex-based parsing for coordinates, addresses, and street intersections.
- **Spam Prevention**: Guardrails for post content (required mentions, image checks).
- **Entity Normalization**: Consistent entity naming via `entities.json`.

### Changed
- Monolithic `bot.py` is now a thin wrapper around the `hm` package.
- Logging directory structure unified.
