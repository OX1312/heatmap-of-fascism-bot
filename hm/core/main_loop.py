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
    log_line("MAIN LOOP STARTED (Refactored Bot)", "INFO")
    
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
            
            # Save state
            # Note: Pipeline modifies objects in place
            save_json(CACHE_PATH, cache)
            save_json(PENDING_PATH, pipeline.pending)
            save_json(REPORTS_PATH, reports)
            
            if one_shot:
                break
                
            # Sleep
            time.sleep(60) # configurable delay
            
        except KeyboardInterrupt:
            log_line("MAIN LOOP STOPPED (KeyboardInterrupt)", "INFO")
            break
        except Exception as e:
            log_line(f"MAIN LOOP ERROR | err={e!r}", "ERROR")
            if one_shot: break
            time.sleep(60)
