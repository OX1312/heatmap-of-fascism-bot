# Bugfixes and Changes Log

## Refactoring & Core Logic
- **Modular Pipeline**: Refactored `hm/core/pipeline.py` and `hm/core/main_loop.py` to better handle the bot's execution flow.
- **Location Snapping**: Ported and finalized location snapping logic in `hm/domain/location.py` to ensure coordinates snap to public ways.
- **Normalization**: Added `hm/domain/geojson_normalize.py` to standardize data formats across the bot.
- **Enrichment**: Introduced `hm/domain/enrichment.py` for entity enrichment strategies.

## Operations & Monitoring
- **Dashboard**: Added `monitor.command`, `start_dashboard.sh`, and `com.heatmap.bot.dashboard.plist` for system monitoring.
- **CLI**: Updated `ox` script for better command-line management.
- **Logging**: Improved logging configuration in `hm/utils/log.py` (implied by context).

## Data & Tools
- **New Tools**: Added `tools/enrich_data.py`, `tools/fix_category_display.py`, and `tools/report_stats.py` for data maintenance and reporting.
- **Entities**: Updated `hm/domain/entities.py` to support new entity structures.

## Documentation & Testing
- **Docs**: Added `docs/mastodon_api_limits.md` and updated `docs/popup_template.html`.
- **Tests**: Initialized `tests/` directory and `pytest.ini` for testing infrastructure.
