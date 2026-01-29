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

    # --- STARTUP NOTIFICATION ---
    from ..adapters.mastodon_api import send_dm, post_status
    last_start = cache.get("_last_startup_msg_ts", 0)
    now_ts = int(time.time())
    
    # Cooldown 10 min
    if now_ts - last_start > 600:
        # Public Post (Server UP)
        # Public Post (Server UP)
        # DISABLE PUBLIC STARTUP SPAM
        # if cfg.get("public_startup_msg"):
        #     if post_status(cfg, f"🟢 Server wieder up. (v{hm.__version__})", visibility="public"):
        #         log_line("STARTUP PUBLIC MSG SENT")

        # DM Managers
        if cfg.get("dm_welcome_managers"):
             managers = cfg.get("manager_accounts", [])
             count_sent = 0
             for mgr in managers:
                 if send_dm(cfg, mgr, f"🤖 Heatmap Bot Online v{hm.__version__}. Ready. ✊"):
                     count_sent += 1
             if count_sent > 0:
                 log_line(f"STARTUP DM sent to {count_sent} managers")
        
        cache["_last_startup_msg_ts"] = now_ts


    # 3. Loop
    loop_count = 0
    while True:
        try:
            # Auto-Update Check (Limit to every 15 loops ~ 30 mins)
            if loop_count % 15 == 0 and bool(cfg.get("auto_update", False)):
                 from ..adapters.git_ops import run_git_pull
                 run_git_pull(cfg, ROOT) 
            
            loop_count += 1

            # Run one cycle
            pipeline.run_cycle()
            
            # Entity Enrichment (Idle background task)
            # DISABLED (Import Error fix)
            # if bool(cfg.get("entity_enrich_enabled", True)):
            #     from ..domain.entities import enrich_entities_idle
            #     max_en = int(cfg.get("entity_enrich_max_per_run", 2))
            #     enr = enrich_entities_idle(cfg, reports, max_per_run=max_en)
            #     if enr:
            #         log_line(f"ENTITY_ENRICH | updated {enr}")
            #         # reports dirty, will be saved below
            
            # Normalize Report Data (Consistency Fix)
            try:
                from ..domain.geojson_normalize import normalize_reports_geojson
                normalize_reports_geojson(reports, Path("entities.json"))
            except Exception as e:
                pass # log_line(f"NORMALIZE ERROR | {e!r}", "ERROR")

            # Heartbeat / Cycle Stats (Visible in Dashboard)
            pending_count = len(pipeline.pending)
            reports_count = len(reports.get("features", []))
            log_line(f"CHECKS | pending={pending_count} published={reports_count}", "INFO")

            # --- MANAGER DAILY SUMMARY ---
            if cfg.get("manager_daily_summary"):
                from ..adapters.mastodon_api import send_dm
                hour = int(cfg.get("manager_daily_summary_hour_local", 9))
                today_str = now_berlin().strftime("%Y-%m-%d")
                last_summary = cache.get("_last_daily_summary_date", "")
                
                # If new day and past the target hour
                if today_str != last_summary and now_berlin().hour >= hour:
                     managers = cfg.get("manager_accounts", [])
                     msg = f"📊 Daily Summary ({today_str})\n\nReports: {reports_count}\nPending: {pending_count}\n\nFCK RACISM. ✊"
                     count_sum = 0
                     for mgr in managers:
                          if send_dm(cfg, mgr, msg):
                               count_sum += 1
                     
                     if count_sum > 0:
                         cache["_last_daily_summary_date"] = today_str
                         log_line(f"DAILY SUMMARY SENT to {count_sum} managers")

            # CRITICAL: Periodic Save (every loop)
            # We save locally frequently, but sync to Git rarely/never in loop
            try:
                save_json(CACHE_PATH, cache)
                save_json(PENDING_PATH, pipeline.pending)
                save_json(REPORTS_PATH, reports)
            except Exception as se:
                log_line(f"STATE SAVE ERROR | {se!r}", "ERROR")

            # Sleep
            time.sleep(60) # Check every 1 minute
            
        except KeyboardInterrupt:
            log_line("MAIN LOOP STOPPED (KeyboardInterrupt)", "INFO")
            break
        except Exception as e:
            log_line(f"MAIN LOOP ERROR | err={e!r}", "ERROR")
            if one_shot: break
            time.sleep(60)

    # --- SHUTDOWN / EXIT LOGIC ---
    # Runs only when loop breaks (manual stop)
    try:
        # Final Save
        save_json(CACHE_PATH, cache)
        save_json(PENDING_PATH, pipeline.pending)
        save_json(REPORTS_PATH, reports)
        log_line("STATE SAVED (Shutdown)", "INFO")
        
        # Auto-Push on Shutdown
        try:
            from ..adapters.git_ops import auto_git_push_reports
            auto_git_push_reports(cfg, ROOT, "reports.geojson", reason="shutdown-save")
        except Exception as ex:
            log_line(f"GIT PUSH FAILED | {ex!r}", "ERROR")

    except Exception as se:
        log_line(f"SHUTDOWN SAVE ERROR | {se!r}", "ERROR")
    
    # Optional: Public Shutdown Message (Disabled)
    # if cfg.get("public_shutdown_msg"): ...
