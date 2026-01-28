import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from .time import TZ_BERLIN

# Globals to be set by the main application
LOG_DIR: Optional[Path] = None
BOT_LOG_PATH: Optional[Path] = None
EVENT_LOG_PATH: Optional[Path] = None
EVENT_STATE_PATH: Optional[Path] = None

_LOG_LOCK = threading.Lock()
_EVENT_LAST_BY_KEY = {}

def setup_logging(log_dir: Path, bot_log_name: str = "bot.log") -> None:
    global LOG_DIR, BOT_LOG_PATH, EVENT_LOG_PATH, EVENT_STATE_PATH
    LOG_DIR = log_dir
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # We can use a daily rotating log if needed, or just append as per original bot.py
    # Original bot.py used: LOG_DIR / f"bot-{_LOG_DATE}.log"
    # But ARCHITECTURE.md implies we might want simpler logs or we stick to the original pattern.
    # For now, let's allow passing the path or default to a simple one.
    # The original logic used dynamic dates in filenames, which is tricky to "setup" once-for-all 
    # if it runs for days. But let's assume valid setup.
    pass # Real setup happens in main or we keep it dynamic in log_line

def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def log_line(msg: Any, sep: str = " ") -> None:
    """
    Logging wrapper (single timestamp, readable):
    - Prefix every line with: YYYY-MM-DD // HH:MM:SS -
    - No ISO 'T' and no '+01:00' noise.
    """
    global BOT_LOG_PATH
    
    # Check if BOT_LOG_PATH is set, otherwise maybe fail or print only?
    # In the refactor, we should ensure paths are known.
    # For now, we will rely on values being injected or just doing print if not file-bound.
    
    line = str(msg).strip()
    
    with _LOG_LOCK:
        ts = datetime.now(TZ_BERLIN)
        prefix = ts.strftime("%Y-%m-%d // %H:%M:%S%z")
        if len(prefix) >= 5:
            prefix = prefix[:-2] + ":" + prefix[-2:]
        
        full = f"{prefix} - {line}" if line else f"{prefix} -"
        
        if BOT_LOG_PATH:
             _append(BOT_LOG_PATH, full)
        
        print(full, flush=True)

# Note: The complex event dedup logic from bot.py (fav_check etc) 
# might belong better in the Domain layer or a specific adapter wrapper, 
# rather than generic utils. For now, I'll keep this simple generic logger 
# and we can migrate the specific "fav_check" dedup logic to where it's used.
