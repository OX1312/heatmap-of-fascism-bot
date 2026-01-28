import time
import os
from pathlib import Path
from typing import Dict, Any

from ..utils.log import log_line
from ..adapters.mastodon_api import verify_credentials
from ..utils.files import load_json, save_json, ensure_file
from .pipeline import Pipeline

# Paths (we can also make these configurable)
ROOT = Path(".").resolve()
CACHE_PATH = ROOT / "cache_geocode.json"
PENDING_PATH = ROOT / "pending.json"
REPORTS_PATH = ROOT / "reports.geojson"
CFG_PATH = ROOT / "config.json"
LOG_DIR = ROOT / "logs"

from ..utils.log import setup_logging
from ..utils.time import now_berlin

def setup_log_paths():
    # Setup dynamic log paths
    date_str = now_berlin().strftime("%Y-%m-%d")
    bot_log = LOG_DIR / f"bot-{date_str}.log"
    
    # We need to reach into utils.log to set the global. 
    # Since we can't easily import the global to write to it, 
    # we rely on setup_logging or direct assignment if imported.
    import hm.utils.log as log_module
    log_module.BOT_LOG_PATH = bot_log

def run_loop(cfg: Dict[str, Any], one_shot: bool = False) -> None:
    setup_log_paths()
    import hm
    log_line(f"MAIN LOOP STARTED (Refactored Bot v{hm.__version__})", "INFO")
    
    # 1. Credentials Check
    if not verify_credentials(cfg):
         log_line("CRITICAL | Mastodon credentials invalid or instance unreachable", "ERROR")
         if not cfg.get("test_mode"):
             time.sleep(30) # retry logic?
             return

    # 2. Load State
    cache = load_json(CACHE_PATH, {})
    pending = load_json(PENDING_PATH, [])
    reports = load_json(REPORTS_PATH, {"type": "FeatureCollection", "features": []})

    pipeline = Pipeline(cfg, cache, pending, reports)

    # 3. Loop
    while True:
        try:
            # Run one cycle
            pipeline.run_cycle()
            
            # Entity Enrichment (Idle background task)
            if bool(cfg.get("entity_enrich_enabled", True)):
                from ..domain.entities import enrich_entities_idle
                max_en = int(cfg.get("entity_enrich_max_per_run", 2))
                enr = enrich_entities_idle(cfg, reports, max_per_run=max_en)
                if enr:
                    log_line(f"ENTITY_ENRICH | updated {enr}")
                    # reports dirty, will be saved below
            
            # Normalize Report Data (Consistency Fix)
            try:
                normalize_reports_geojson(reports, Path("entities.json"))
            except Exception as e:
                log_line(f"NORMALIZE ERROR | {e!r}", "ERROR")

            # Sleep
            time.sleep(60) # configurable delay
            
        except KeyboardInterrupt:
            log_line("MAIN LOOP STOPPED (KeyboardInterrupt)", "INFO")
            break
        except Exception as e:
            log_line(f"MAIN LOOP ERROR | err={e!r}", "ERROR")
            if one_shot: break
            time.sleep(60)
        finally:
            # CRITICAL: Always save state, even on error!
            # This prevents losing "replied_to" cache and causing duplicates on restart.
            try:
                save_json(CACHE_PATH, cache)
                save_json(PENDING_PATH, pipeline.pending)
                save_json(REPORTS_PATH, reports)
                # log_line("STATE SAVED", "INFO")
                
                # Auto Push to GitHub
                try:
                    from ..adapters.git_ops import auto_git_push_reports
                    auto_git_push_reports(cfg, ROOT, "reports.geojson", reason="auto-update")
                except Exception as ex:
                    log_line(f"GIT AUTO PUSH FAILED | {ex!r}", "ERROR")

            except Exception as se:
                log_line(f"STATE SAVE ERROR | {se!r}", "ERROR")
