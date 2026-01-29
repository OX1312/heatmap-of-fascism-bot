#!/usr/bin/env python3
import curses
import time
import os
import json
import sys
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

def get_bot_status():
    # Simple check if python process with 'bot.py' or 'hm' is running
    # This is rough; a better way is checking a PID file if we had one.
    # We'll rely on the log file modification time as a proxy for "alive"
    
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
            return f"OFFLINE (Last log {int(age)}s ago) 🔴", curses.color_pair(1)
    return "NO LOGS ⚪", curses.color_pair(4)

def get_log_tail(lines_count=10):
    date_str = datetime.now().strftime("%Y-%m-%d")
    bot_log = LOG_DIR / f"bot-{date_str}.log"
    
    if not bot_log.exists():
        return ["No log file for today."]
        
    try:
        # Read last N bytes
        file_size = bot_log.stat().st_size
        read_size = min(file_size, 10000) # 10kb
        
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
        # Draw borders
        stdscr.attron(curses.color_pair(5)) # Border color
        
        # Horizontal
        stdscr.hline(y, x, curses.ACS_HLINE, w)
        stdscr.hline(y+h-1, x, curses.ACS_HLINE, w)
        # Vertical
        stdscr.vline(y, x, curses.ACS_VLINE, h)
        stdscr.vline(y, x+w-1, curses.ACS_VLINE, h)
        
        # Corners
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
    # Setup colors
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_WHITE, -1)
    curses.init_pair(5, curses.COLOR_BLUE, -1) # Borders
    curses.init_pair(6, curses.COLOR_CYAN, -1) # Headers

    curses.curs_set(0) # Hide cursor
    stdscr.nodelay(True) # Non-blocking input

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        
        # --- HEADER ---
        title = " 🔥 HEATMAP OF FASCISM BOT MONITOR 🔥 "
        stdscr.addstr(0, (w - len(title)) // 2, title, curses.A_BOLD | curses.color_pair(6))
        
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stdscr.addstr(1, (w - len(time_str)) // 2, time_str)

        # --- SERVER & BOT STATUS ---
        status_txt, status_color = get_bot_status()
        
        # Load average
        load1, load5, load15 = os.getloadavg()
        load_txt = f"Load: {load1:.2f} {load5:.2f} {load15:.2f}"
        
        # Draw Status Box
        box_h = 5
        draw_box(stdscr, 2, 1, box_h, w-2, "System Status")
        stdscr.addstr(3, 3, f"Bot Status: ", curses.A_BOLD)
        stdscr.addstr(status_txt, status_color)
        stdscr.addstr(3, w - len(load_txt) - 5, load_txt)
        
        # --- MASTODON & DB STATS ---
        pending = load_json_safe(PENDING_PATH, [])
        reports = load_json_safe(REPORTS_PATH, {"features": []})
        reports_count = len(reports.get("features", []))
        
        # Check audit/audits for recent deletes (not implemented fully in this view yet, using placeholder)
        
        stats_h = 7
        draw_box(stdscr, 2 + box_h, 1, stats_h, w-2, "Mastodon & Database")
        
        # Columns
        col1_x = 3
        col2_x = w // 2
        
        stdscr.addstr(2 + box_h + 1, col1_x, "INBOX (Pending Review):", curses.A_BOLD)
        stdscr.addstr(f" {len(pending)}")
        
        stdscr.addstr(2 + box_h + 2, col1_x, "OUTBOX (Published):", curses.A_BOLD)
        stdscr.addstr(f"     {reports_count}")
        
        # Maybe add simple audit check
        # stdscr.addstr(2 + box_h + 3, col1_x, "Recent Deletes: ...")

        # --- LOGS ---
        log_h = h - (2 + box_h + stats_h) - 1
        if log_h > 4:
            draw_box(stdscr, 2 + box_h + stats_h, 1, log_h, w-2, "Live Logs")
            
            logs = get_log_tail(log_h - 2)
            for idx, line in enumerate(logs):
                try:
                    # Simple coloring for log levels
                    attr = curses.A_NORMAL
                    if "ERROR" in line: attr = curses.color_pair(1)
                    elif "WARNING" in line: attr = curses.color_pair(3)
                    elif "INFO" in line: attr = curses.color_pair(2)
                    
                    # Truncate to fit
                    line = line[:w-5]
                    stdscr.addstr(2 + box_h + stats_h + 1 + idx, 3, line, attr)
                except curses.error:
                    pass

        stdscr.refresh()
        
        # Check input to exit
        try:
            k = stdscr.getch()
            if k == ord('q'):
                break
        except Exception:
            pass
            
        time.sleep(2)

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
