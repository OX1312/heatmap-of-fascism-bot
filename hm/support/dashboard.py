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
STATS_PATH = ROOT / "stats.jsonl"
AUDIT_DIR = ROOT / "support"
MUTE_FLAG = ROOT / ".bot_muted"

# 8-bit Robot ASCII Art
ROBOT_ON = [
    "    💡    ",
    "   ┌───┐  ",
    "   │◉ ◉│  ",
    "   │ ▽ │  ",
    "   └─┬─┘  ",
    "  ┌─┴─┬─┐ ",
    "  │ ◊ ◊ │ ",
    "  └┬───┬┘ ",
    "   │   │  ",
]

ROBOT_OFF = [
    "    ○     ",
    "   ┌───┐  ",
    "   │- -│  ",
    "   │ _ │  ",
    "   └─┬─┘  ",
    "  ┌─┴─┬─┐ ",
    "  │ · · │ ",
    "  └┬───┬┘ ",
    "   │   │  ",
]

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

def get_stats_table():
    # Read stats.jsonl
    # Format: {"ts": 123, "event": "type", "id": "..."}
    now = time.time()
    day_start = now - (now % 86400) # Simple midnight (UTC approx, acceptable)
    week_start = now - (7 * 86400)
    month_start = now - (30 * 86400)
    
    counts = {
        "request": [0, 0, 0, 0],   # Day, Week, Month, Total
        "pending": [0, 0, 0, 0],
        "published": [0, 0, 0, 0]
    }
    
    try:
        if STATS_PATH.exists():
            with STATS_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        ts = d.get("ts", 0)
                        evt = d.get("event")
                        if evt in counts:
                            # Total
                            counts[evt][3] += 1
                            # Intervals
                            if ts >= month_start:
                                counts[evt][2] += 1
                                if ts >= week_start:
                                    counts[evt][1] += 1
                                    if ts >= day_start:
                                        counts[evt][0] += 1
                    except:
                        pass
    except Exception:
        pass
        
    return counts

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
        draw_box(stdscr, 2, 1, box_h, w//2-2, "System Status")
        
        # --- ROBOT CONTROL ---
        is_muted = MUTE_FLAG.exists()
        robot_art = ROBOT_OFF if is_muted else ROBOT_ON
        robot_box_x = w//2 + 1
        robot_box_w = w//2 - 3
        draw_box(stdscr, 2, robot_box_x, box_h, robot_box_w, "Bot Control [M]")
        
        # Draw robot
        robot_start_x = robot_box_x + 2
        for idx, line in enumerate(robot_art[:4]):
            try:
                color = curses.color_pair(2) if not is_muted else curses.color_pair(1)
                stdscr.addstr(3 + idx, robot_start_x, line, color)
            except curses.error:
                pass
        
        # Status text
        status = "SENDING" if not is_muted else "MUTED"
        status_color = curses.color_pair(2) if not is_muted else curses.color_pair(1)
        try:
            stdscr.addstr(4, robot_start_x + 14, status, status_color | curses.A_BOLD)
            hint = "Press [M] to toggle"
            stdscr.addstr(5, robot_start_x + 12, hint, curses.A_DIM)
        except curses.error:
            pass
        
        stdscr.addstr(3, 3, f"Bot Status: ", curses.A_BOLD)
        stdscr.addstr(status_txt, status_color)
        stdscr.addstr(3, w - len(load_txt) - 5, load_txt)
        
        gh_label = "GitHub Server: "
        ms_label = "Mastodon Server: "
        
        def get_col(s):
            if "🟢" in s: return curses.color_pair(2)
            if "🔴" in s: return curses.color_pair(1)
            return curses.color_pair(4)
            
        stdscr.addstr(4, 3, gh_label, curses.A_BOLD)
        stdscr.addstr(github_st, get_col(github_st))
        
        mid_x = w // 2
        stdscr.addstr(4, mid_x, ms_label, curses.A_BOLD)
        stdscr.addstr(masto_st, get_col(masto_st))
        

        # --- STATISTICS TABLE ---
        # "Mastodon Statistics"
        # Columns: Metric | Day | Week | Month | Total
        
        stats = get_stats_table()
        
        # Also need CURRENT state counts
        pending_list = load_json_safe(PENDING_PATH, [])
        reports = load_json_safe(REPORTS_PATH, {"features": []})
        current_pending = sum(1 for p in pending_list if p.get("status", "").upper() == "PENDING")
        current_requests = len(pending_list) - current_pending

        # FIX: Override Total Published (Stats) with actual DB count
        # Because stats.jsonl only has new events, but Total should show full history.
        stats["published"][3] = current_published

        # Table Layout
        col_w = 9
        # Metric(12) | Day(9) | Week(9) | Month(9) | Total(9)
        # Total Width = ~50
        
        stats_h = 9
        draw_box(stdscr, 2 + box_h, 1, stats_h, w-2, "Activity Statistics")
        
        base_y = 2 + box_h + 1
        
        # Headings
        # Metric      Day      Week     Month    Total
        head_fmt = "{:<12} {:>8} {:>8} {:>8} {:>8}"
        stdscr.addstr(base_y, 3, head_fmt.format("METRIC", "DAY", "WEEK", "MONTH", "TOTAL"), curses.A_BOLD | curses.A_UNDERLINE)
        
        # Rows
        # Requests
        row_y = base_y + 2
        req_vals = stats["request"]
        stdscr.addstr(row_y, 3, head_fmt.format("Requests", 
            format_number(req_vals[0]), format_number(req_vals[1]), format_number(req_vals[2]), format_number(req_vals[3])))
            
        # New Pending
        row_y += 1
        pen_vals = stats["pending"]
        stdscr.addstr(row_y, 3, head_fmt.format("New Pending", 
            format_number(pen_vals[0]), format_number(pen_vals[1]), format_number(pen_vals[2]), format_number(pen_vals[3])))

        # Published
        row_y += 1
        pub_vals = stats["published"]
        stdscr.addstr(row_y, 3, head_fmt.format("Published", 
            format_number(pub_vals[0]), format_number(pub_vals[1]), format_number(pub_vals[2]), format_number(pub_vals[3])))

        # Divider
        stdscr.hline(row_y + 1, 3, curses.ACS_HLINE, 50)
        
        # Active State
        # Current: Pending 0 | Published 6
        state_y = row_y + 2
        stdscr.addstr(state_y, 3, f"ACTIVE STATE:  Pending: {current_pending}   Published: {current_published}", curses.A_BOLD)


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
            elif k == ord('m') or k == ord('M'):
                # Toggle mute
                if MUTE_FLAG.exists():
                    MUTE_FLAG.unlink()
                else:
                    MUTE_FLAG.touch()
        except Exception: pass
        time.sleep(2)

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
