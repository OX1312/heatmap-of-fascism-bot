#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/Oscar_Berngruber/heatmap-of-fascism-bot"
PLIST="$HOME/Library/LaunchAgents/de.ox.heatmap-bot.plist"
LABEL="de.ox.heatmap-bot"

cd "$REPO"

today() { date +%F; }

help_msg() {
  cat <<'TXT'
OX Heatmap Bot Ops

Core
  h               – help
  online          – quick live check (config + last RUN/START/ERROR + normal tail)

Service (launchd)
  bot_status      – launchctl list (is it registered?)
  bot_start       – start (bootstrap)
  bot_stop        – stop (bootout)
  bot_restart     – restart (stop+start)
  server_restart  – reboot machine (safe +1min, cancelable)

Run / Modes
  test_run        – run once now (kickstart)
  bot_version     – version + modes (+ git hash)
  test_report     – test_mode status
  auto_report     – auto_push_reports status

Logs (live)
  monitor         – LIVE: bot work + hourly + key ticks + errors (merged)
  monitor_errors  – LIVE: errors only (merged)
  log_launchd     – tail bot.launchd.log
  log_normal      – tail logs/normal-YYYY-MM-DD.log
  log_event       – tail logs/event-YYYY-MM-DD.log
  show_errors     – grep errors (today snapshot)

Dev / Data
  compile_py      – py_compile bot.py
  git_status      – git status
  git_diff        – git diff --stat
  py_dir          – plist python + workingdir
  data_check      – validate reports.geojson (missing fields, coords, duplicates)
  data_fix        – fix reports.geojson (fills desc, drops invalid, dedupe, backup)
TXT
}

cmd="${1:-h}"

case "$cmd" in
  h|help) help_msg ;;
  bot_status) launchctl list | grep -i heatmap || true ;;
  bot_start)  launchctl bootstrap "gui/$(id -u)" "$PLIST" ;;
  bot_stop)   launchctl bootout  "gui/$(id -u)" "$PLIST" ;;
  bot_restart)
    launchctl bootout  "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"

    echo "🚀 Bot restart completed successfully"
    ;;

  server_restart)
    ts="$(TZ=Europe/Berlin date +%FT%T%z)"
    msg="SERVER RESTART INITIATED | reason=maintenance | scheduled=+1min | cancel: sudo /sbin/shutdown -c"
    printf "%s %s\n" "$ts" "$msg" >> bot.launchd.log
    echo "⚠️ $msg"
    sudo /sbin/shutdown -r +1 "ox server_restart"
    ;;
  test_run)    "$REPO/.venv/bin/python" -u "$REPO/bot.py" --once ;;

  bot_version)
    pyv="$("$REPO/.venv/bin/python" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || true)"
    v="$("$REPO/.venv/bin/python" - <<'PY' 2>/dev/null || true
import re
s=open("bot.py","r",encoding="utf-8").read()
m=re.search(r'__version__\s*=\s*"([^"]+)"', s)
print(m.group(1) if m else "unknown")
PY
)"
    tm="$("$REPO/.venv/bin/python" - <<'PY' 2>/dev/null || true
import json
cfg=json.load(open("config.json","r",encoding="utf-8"))
print(bool(cfg.get("test_mode", False)))
PY
)"
    ap="$("$REPO/.venv/bin/python" - <<'PY' 2>/dev/null || true
import json
cfg=json.load(open("config.json","r",encoding="utf-8"))
print(bool(cfg.get("auto_push_reports", False)))
PY
)"
    gh="$(git rev-parse --short HEAD 2>/dev/null || true)"
    echo "bot=$v  py=$pyv  test_mode=$tm  auto_push_reports=$ap  git=$gh"
    ;;

  test_report)
    "$REPO/.venv/bin/python" - <<'PY'
import json
cfg=json.load(open("config.json","r",encoding="utf-8"))
print("test_mode=" + str(bool(cfg.get("test_mode", False))).lower())
PY
    ;;
  auto_report)
    "$REPO/.venv/bin/python" - <<'PY'
import json
cfg=json.load(open("config.json","r",encoding="utf-8"))
print("auto_push_reports=" + str(bool(cfg.get("auto_push_reports", False))).lower())
PY
    ;;

  log_launchd) tail -n 120 -F bot.launchd.log ;;
  log_normal)  f="logs/normal-$(today).log"; [ -f "$f" ] || f="normal-$(today).log"; tail -n 200 -F "$f" ;;
  log_event)   f="logs/event-$(today).log";  [ -f "$f" ] || f="event-$(today).log";  tail -n 200 -F "$f" ;;
  monitor)
    # Foreground + fullscreen (only if not already fullscreen)
    osascript >/dev/null 2>&1 <<'OSA' || true
tell application "Terminal" to activate
delay 0.05
tell application "System Events"
  tell process "Terminal"
    try
      set isFS to value of attribute "AXFullScreen" of front window
    on error
      set isFS to false
    end try
    if isFS is false then
      keystroke "f" using {control down, command down}
    end if
  end tell
end tell
OSA

    # Fullscreen the Terminal window (toggle). If it's already fullscreen, this will toggle back.
    osascript -e 'tell application "Terminal" to activate' >/dev/null 2>&1 || true
    osascript -e 'tell application "System Events" to keystroke "f" using {control down, command down}' >/dev/null 2>&1 || true
    d=$(TZ=Europe/Berlin date +%F)
    n="logs/normal-$d.log"; [ -f "$n" ] || n="normal-$d.log"
    e="logs/event-$d.log";  [ -f "$e" ] || e="event-$d.log"
    l="bot.launchd.log"
    # Merged live view: work + hourly + key ticks + errors
    tail -n 0 -F "$n" "$e" "$l" 2>/dev/null | grep --line-buffered -i \
      '(HOURLY|RUNNING|START|SUMMARY|SUBMISSION|REVIEWED|PUBLISHED|PENDING|fav_check|verify_deleted|VERIFY_DELETED|hashtag_timeline|rate_limited|http=429|ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout)' || true
    ;;

  monitor_errors)
    d=$(TZ=Europe/Berlin date +%F)
    n="logs/normal-$d.log"; [ -f "$n" ] || n="normal-$d.log"
    e="logs/event-$d.log";  [ -f "$e" ] || e="event-$d.log"
    l="bot.launchd.log"
    # Errors-only live view
    tail -n 0 -F "$n" "$e" "$l" 2>/dev/null | grep --line-buffered -i \
      '(ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout|rate_limited|http=429)' || true
    ;;
  show_errors)
    pat="(ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout)"
    for f in "logs/normal-$(today).log" "logs/event-$(today).log" "bot.launchd.log" "normal-$(today).log" "event-$(today).log"; do
      [ -f "$f" ] || continue
      echo "---- $f ----"
      grep -nE "$pat" "$f" | tail -n 120 || true
    done
    ;;
  compile_py)  "$REPO/.venv/bin/python" -m py_compile bot.py && echo ok ;;
  git_status)  git status ;;
  git_diff)    git diff --stat ;;
  py_dir)
    echo "plist=$PLIST"
    plutil -p "$PLIST" | sed -n '1,120p' | grep -E "WorkingDirectory|ProgramArguments" -n -A3 || true
    ;;
  online)
    cd "$REPO" || exit 1
    d=$(TZ=Europe/Berlin date +%F)
    n="logs/normal-$d.log"; [ -f "$n" ] || n="normal-$d.log"

    echo "— launchd:"
    launchctl list | grep -i heatmap || true

    echo "— config:"
    "$REPO/.venv/bin/python" - <<'PY2'
import json
from pathlib import Path
cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
for k in ["test_mode","auto_push_reports","user_agent"]:
    print(f"{k}={cfg.get(k)}")
PY2

    echo "— last activity (normal):"
    if [ -f "$n" ]; then
      egrep -n 'HOURLY|hashtag_timeline|reply OK|VERIFY_DELETED|SUMMARY|ERROR \| auto_push' "$n" | tail -n 20 || true
    else
      echo "(no normal log found)"
    fi
    ;;

  data_check)
    "$REPO/.venv/bin/python" tools/check_data.py --reports reports.geojson --entities entities.json
    ;;
  data_fix)
    "$REPO/.venv/bin/python" tools/fix_data.py --reports reports.geojson --entities entities.json
    ;;
  *)
    echo "unknown command: $cmd"
    help_msg
    exit 2
    ;;
esac
