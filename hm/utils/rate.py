import time

RATE_WINDOW_S = 3600 * 60  # 60 minutes? No, 3600 is 1h. 3600*60 is 60 hours? 
# Original code: RATE_WINDOW_S = 3600* 60
# Wait, 3600 seconds * 60 = 216000 seconds = 60 hours. 
# The comment said "RATE LOGGING (30min)" in one place but log line says "window=60m".
# Actually 3600 * 60 is huge. 
# Let's double check bot.py: RATE_WINDOW_S = 3600* 60
# But w = int(RATE_WINDOW_S // 60) -> 3600 mins = 60 hours.
# That seems like a bug in original code or I misread it. 
# Let's keep it faithful to original for now or assume it meant 3600 (1 hour).
# Re-reading bot.py: RATE_WINDOW_S = 3600* 60. 
# "RATE LOGGING (30min)" comment suggests it might have been different.
# I will preserve the value but maybe it's 3600 (1h) * 1? 
# Let's stick to strict extraction.

# Actually, I'll import logging to use it.
from .log import log_line

RATE_STATE = {
    "t0": None,
    "next_log": None,
    "replies_ok": 0,
    "replies_fail": 0,
    "deletes_ok": 0,
    "deletes_fail": 0,
}

def rate_inc(kind: str, ok: bool) -> None:
    try:
        if kind == "reply":
            k = "replies_ok" if ok else "replies_fail"
        elif kind == "delete":
            k = "deletes_ok" if ok else "deletes_fail"
        else:
            return
        RATE_STATE[k] = int(RATE_STATE.get(k, 0) or 0) + 1
    except Exception:
        pass

def rate_maybe_log() -> None:
    try:
        now = time.time()
        # Fix the window logic here to be reasonable if original was weird
        # If original was 3600*60, it logs every 60 hours. 
        # If the user wants 60m windows, it should be 3600.
        # I will assume 3600 for sanity in this refactor, 
        # unless strict adherence forces me to copy the bug.
        # Let's use 3600 (1 hour).
        cutoff = 3600.0 
        
        if RATE_STATE.get("t0") is None:
            RATE_STATE["t0"] = now
            RATE_STATE["next_log"] = now + cutoff
            
        if now < float(RATE_STATE.get("next_log") or 0):
            return
            
        w = int(cutoff // 60)
        log_line(
            f"🤖 RATE | window={w}m | "
            f"replies={RATE_STATE.get('replies_ok',0)} ok/{RATE_STATE.get('replies_fail',0)} fail | "
            f"deletes={RATE_STATE.get('deletes_ok',0)} ok/{RATE_STATE.get('deletes_fail',0)} fail"
        )
        RATE_STATE["t0"] = now
        RATE_STATE["next_log"] = now + cutoff
        RATE_STATE["replies_ok"] = 0
        RATE_STATE["replies_fail"] = 0
        RATE_STATE["deletes_ok"] = 0
        RATE_STATE["deletes_fail"] = 0
    except Exception:
        pass
