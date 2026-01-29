#!/usr/bin/env python3
import curses
import time
import os
import json
import sys
import requests
from pathlib import Path
from datetime import datetime

# Resolve paths
ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = ROOT / "logs"
PENDING_PATH = ROOT / "pending.json"
REPORTS_PATH = ROOT / "reports.geojson"
AUDIT_DIR = ROOT / "support"

def load_json_safe(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

# Load Config (Global)
CFG = load_json_safe(ROOT / "config.json", {})
SECRETS = load_json_safe(ROOT / "secrets" / "secrets.json", {})
CFG.update(SECRETS)

# --- CACHED CHECKS ---
_cache = {
    "github": {"val": "UNKNOWN ⚪", "ts": 0},
    "mastodon": {"val": "UNKNOWN ⚪", "ts": 0}
}
CACHE_TTL = 900 # 15 minutes

def check_github():
    now = time.time()
    if now - _cache["github"]["ts"] < CACHE_TTL:
        return _cache["github"]["val"]
    
    try:
        # Check API status or just google
        r = requests.get("https://api.github.com", timeout=3)
        val = "YES 🟢" if r.status_code == 200 else "NO 🔴"
    except Exception:
        val = "NO 🔴"
        
    _cache["github"] = {"val": val, "ts": now}
    return val

def check_mastodon():
    now = time.time()
    if now - _cache["mastodon"]["ts"] < CACHE_TTL:
         return _cache["mastodon"]["val"]
         
    try:
        inst = CFG.get("instance_url", "").rstrip("/")
        token = CFG.get("access_token", "")
        if not inst or not token:
            val = "CONF ERR 🔴"
        else:
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.get(f"{inst}/api/v1/accounts/verify_credentials", headers=headers, timeout=3)
            val = "YES 🟢" if r.status_code == 200 else "NO 🔴"
    except Exception:
        val = "NO 🔴"
        
    _cache["mastodon"] = {"val": val, "ts": now}
    return val

def format_number(n):
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)

def get_bot_status():
    date_str = datetime.now().strftime("%Y-%m-%d")
    bot_log = LOG_DIR / f"bot-{date_str}.log"
    
    if bot_log.exists():
        mtime = bot_log.stat().st_mtime
        age = time.time() - mtime
        if age < 60:
            return "ONLINE 🟢", curses.color_pair(2)
        elif age < 300:
            return "IDLE 🟡", curses.color_pair(3)
        else:
            return f"OFFLINE ({int(age)}s) 🔴", curses.color_pair(1)
    return "NO LOGS ⚪", curses.color_pair(4)

def get_log_tail(lines_count=10):
    date_str = datetime.now().strftime("%Y-%m-%d")
    bot_log = LOG_DIR / f"bot-{date_str}.log"
    
    if not bot_log.exists():
        return ["No log file for today."]
        
    try:
        file_size = bot_log.stat().st_size
        read_size = min(file_size, 10000)
        
        with bot_log.open("rb") as f:
            if file_size > read_size:
                f.seek(-read_size, 2)
            content = f.read().decode("utf-8", errors="replace")
            lines = content.splitlines()
            return lines[-lines_count:]
    except Exception as e:
        return [f"Error reading logs: {e}"]

def draw_box(stdscr, y, x, h, w, title=""):
    try:
        stdscr.attron(curses.color_pair(5))
        stdscr.hline(y, x, curses.ACS_HLINE, w)
        stdscr.hline(y+h-1, x, curses.ACS_HLINE, w)
        stdscr.vline(y, x, curses.ACS_VLINE, h)
        stdscr.vline(y, x+w-1, curses.ACS_VLINE, h)
        stdscr.addch(y, x, curses.ACS_ULCORNER)
        stdscr.addch(y, x+w-1, curses.ACS_URCORNER)
        stdscr.addch(y+h-1, x, curses.ACS_LLCORNER)
        stdscr.addch(y+h-1, x+w-1, curses.ACS_LRCORNER)
        stdscr.attroff(curses.color_pair(5))
        if title:
            stdscr.addstr(y, x+2, f" {title} ", curses.A_BOLD)
    except curses.error:
        pass

def main(stdscr):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_WHITE, -1)
    curses.init_pair(5, curses.COLOR_BLUE, -1)
    curses.init_pair(6, curses.COLOR_CYAN, -1)

    curses.curs_set(0)
    stdscr.nodelay(True)

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        
        # --- HEADER ---
        title = " 🔥 HEATMAP OF FASCISM BOT MONITOR 🔥 "
        stdscr.addstr(0, (w - len(title)) // 2, title, curses.A_BOLD | curses.color_pair(6))
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stdscr.addstr(1, (w - len(time_str)) // 2, time_str)

        # --- SYSTEM STATUS ---
        status_txt, status_color = get_bot_status()
        github_st = check_github()
        masto_st = check_mastodon()
        
        load1, _, _ = os.getloadavg()
        load_txt = f"Load: {load1:.2f}"
        
        box_h = 6 
        draw_box(stdscr, 2, 1, box_h, w-2, "System Status")
        
        # Row 1: Bot Status
        stdscr.addstr(3, 3, f"Bot Status: ", curses.A_BOLD)
        stdscr.addstr(status_txt, status_color)
        stdscr.addstr(3, w - len(load_txt) - 5, load_txt)
        
        # Row 2: GitHub / Mastodon
        # GitHub: YES 🟢   Mastodon: YES 🟢
        # Calculate positions
        # "GitHub: " len=8. "YES 🟢" len=6.
        gh_label = "GitHub Server: "
        ms_label = "Mastodon Server: "
        
        # Color parsing is tricky with return strings. 
        # I'll just print specific parts. 
        # For simplicity, check string content for color
        
        def get_col(s):
            if "🟢" in s: return curses.color_pair(2)
            if "🔴" in s: return curses.color_pair(1)
            return curses.color_pair(4)
            
        stdscr.addstr(4, 3, gh_label, curses.A_BOLD)
        stdscr.addstr(github_st, get_col(github_st))
        
        mid_x = w // 2
        stdscr.addstr(4, mid_x, ms_label, curses.A_BOLD)
        stdscr.addstr(masto_st, get_col(masto_st))
        

        # --- DATABASE ---
        pending_list = load_json_safe(PENDING_PATH, [])
        reports = load_json_safe(REPORTS_PATH, {"features": []})
        published_count = len(reports.get("features", []))
        
        pending_fav_count = 0
        requests_count = 0
        for p in pending_list:
            if p.get("status", "").upper() == "PENDING":
                pending_fav_count += 1
            else:
                requests_count += 1
        
        stats_h = 7
        draw_box(stdscr, 2 + box_h, 1, stats_h, w-2, "Mastodon & Database")
        
        # Helper to draw aligned row
        def draw_row(row_idx, label, count):
            y_pos = 2 + box_h + 1 + row_idx
            # Label
            stdscr.addstr(y_pos, 3, f"{label}:", curses.A_BOLD)
            # Value (Aligned to column 25?)
            val_str = format_number(count)
            # " 1234" (4 digits space + k support)
            # Actually user wants "untereinander"
            # Let's fix position e.g. x=20
            # Right align 4 chars?
            stdscr.addstr(y_pos, 22, f"{val_str:>5}")

        draw_row(0, "Requests", requests_count)
        draw_row(1, "Pending", pending_fav_count)
        draw_row(2, "Published", published_count)


        # --- LOGS ---
        log_h = h - (2 + box_h + stats_h) - 1
        if log_h > 4:
            draw_box(stdscr, 2 + box_h + stats_h, 1, log_h, w-2, "Live Logs")
            logs = get_log_tail(log_h - 2)
            for idx, line in enumerate(logs):
                try:
                    attr = curses.A_NORMAL
                    if "ERROR" in line: attr = curses.color_pair(1)
                    elif "WARNING" in line: attr = curses.color_pair(3)
                    elif "INFO" in line: attr = curses.color_pair(2)
                    line = line[:w-5]
                    stdscr.addstr(2 + box_h + stats_h + 1 + idx, 3, line, attr)
                except curses.error: pass

        stdscr.refresh()
        try:
            k = stdscr.getch()
            if k == ord('q'): break
        except Exception: pass
        time.sleep(2)

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
