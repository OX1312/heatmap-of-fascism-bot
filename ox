#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/de.ox.heatmap-bot.plist"

cd "$REPO"

today() { date +%F; }

# ---- Pro post-check helpers (no POLL) ----
postcheck_cmd() {
  echo "cd \"$REPO\" && tail -n 200 bot.launchd.log | egrep 'SERVER ONLINE|START Version|RUNNING 20|CHECKS \\||ERROR \\||Traceback' || true"
}

wait_startup_check() {
  # Wait until START+RUNNING+CHECKS appear AFTER this function is called.
  local cut
  cut="$(TZ=Europe/Berlin date '+%Y-%m-%d // %H:%M:%S')"
  local deadline=$((SECONDS+20))

  while [ $SECONDS -lt $deadline ]; do
    # filter only lines >= cut (lexicographic works with YYYY-MM-DD // HH:MM:SS prefix)
    if tail -n 400 bot.launchd.log 2>/dev/null | awk -v cut="$cut" '$0 >= cut' | egrep -q 'START Version|RUNNING 20|CHECKS \|'; then
      # Require CHECKS specifically (strong signal the bot is fully up)
      if tail -n 400 bot.launchd.log 2>/dev/null | awk -v cut="$cut" '$0 >= cut' | egrep -q 'CHECKS \|'; then
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

startup_snapshot() {
  echo "— startup snapshot (last 200, filtered):"
  tail -n 200 bot.launchd.log 2>/dev/null | egrep 'SERVER ONLINE|START Version|RUNNING 20|CHECKS \||ERROR \||Traceback' || true
}

help_msg() {
  cat <<'TXT'
OX Heatmap Bot Ops

Service (launchd)
  status          – check if service is registered and running
  start           – start the bot service
  stop            – stop the bot service
  restart         – restart the bot service (stop + start + check)
  reset           – full reset: stop, backup logs/state, wipe log, restart
  reboot          – reboot the machine (safe +1min delay)

Logs & Monitoring
  monitor         – LIVE dashboard (bot activity + errors)
  log             – tail main runtime log (logs/bot-YYYY-MM-DD.log)
  log_service     – tail launchd service log (startup/crash info)
  show_errors     – grep errors from today's logs
  health          – quick health check (config + last activity)
  stats           – detailed statistics (replies, deletes, audits)

Dev & Data
  run             – run bot once manually (test run)
  info            – show version, git hash, and test_mode status
  check_data      – validate reports.geojson
  fix_data        – normalize and fix reports.geojson
  git_status      – show git status
TXT
}

cmd="${1:-h}"

case "$cmd" in
  h|help) help_msg ;;
  
  # --- Service Management ---
  status) launchctl list | grep -i heatmap || echo "Service not running (or not registered)" ;;
  
  start)  launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "Service started" ;;
  
  stop)   launchctl bootout  "gui/$(id -u)" "$PLIST" && echo "Service stopped" ;;
  
  restart)
    echo "Restarting service..."
    launchctl bootout  "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"

    if wait_startup_check; then
      echo "🚀 Bot restart completed successfully"
      startup_snapshot
    else
      echo "⚠️ Bot restart: no CHECKS seen within 20s"
      startup_snapshot
      echo "— run manually to debug:"
      postcheck_cmd
    fi
    ;;

  reset)
    echo "⚠️ RESET: This will stop the bot, backup logs/state, clear the service log, and restart."
    read -r -p "Type YES to continue: " ans
    if [ "${ans:-}" != "YES" ]; then echo "Canceled."; exit 0; fi
    ts="$(TZ=Europe/Berlin date +%F_%H%M%S)"
    mkdir -p "_backup/reset-$ts" || true
    # backup launchd log
    [ -f bot.launchd.log ] && cp -a bot.launchd.log "_backup/reset-$ts/bot.launchd.log" || true
    # backup runtime state
    for f in pending.json timeline_state.json; do
      [ -f "$f" ] && cp -a "$f" "_backup/reset-$ts/$f" || true
    done
    # clear launchd log (fresh view)
    : > bot.launchd.log
    # restart service
    launchctl bootout  "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "🚀 Reset completed. (Backup: _backup/reset-$ts/)"
    ;;

  reboot)
    ts="$(TZ=Europe/Berlin date +%FT%T%z)"
    msg="SERVER RESTART INITIATED | reason=maintenance | scheduled=+1min | cancel: sudo /sbin/shutdown -c"
    printf "%s %s\n" "$ts" "$msg" >> bot.launchd.log
    echo "⚠️ $msg"
    echo "— after reboot, verify with: ./ox health" 
    postcheck_cmd
    sudo /sbin/shutdown -r +1 "ox reboot"
    ;;

  # --- Logs & Monitoring ---
  log)
    f="logs/bot-$(today).log"
    [ -f "$f" ] || f="bot-$(today).log"
    if [ ! -f "$f" ]; then echo "No log file found for today ($f)"; exit 1; fi
    tail -n 250 -F "$f"
    ;;
    
  log_service) tail -n 120 -F bot.launchd.log ;;
  
  monitor)
    d=$(TZ=Europe/Berlin date +%F)
    b="logs/bot-$d.log"; [ -f "$b" ] || b="bot-$d.log"
    l="bot.launchd.log"
    echo "Monitoring $b and $l..."
    tail -n 0 -F "$b" "$l" 2>/dev/null | grep --line-buffered -i \
      '(SERVER ONLINE|START Version|RUNNING 20|CHECKS \||SUMMARY |SUBMISSION|REVIEWED|PUBLISHED|PENDING|fav_check|verify_deleted|VERIFY_DELETED|hashtag_timeline|reply OK|auto_push|git|push|rate_limited|http=429|ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout)' || true
    ;;

  show_errors)
    pat="(ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout)"
    for f in "logs/bot-$(today).log" "bot.launchd.log" "bot-$(today).log"; do
      [ -f "$f" ] || continue
      echo "---- $f ----"
      grep -nE "$pat" "$f" | tail -n 120 || true
    done
    ;;

  health|online)
    cd "$REPO" || exit 1
    d=$(TZ=Europe/Berlin date +%F)
    n="logs/bot-$d.log"; [ -f "$n" ] || n="bot-$d.log"

    echo "— launchd status:"
    launchctl list | grep -i heatmap || echo "  (not running)"

    echo "— config summary:"
    "$REPO/.venv/bin/python" - <<'PY'
import json
from pathlib import Path
try:
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    print(f"  test_mode={cfg.get('test_mode')}")
    print(f"  auto_push={cfg.get('auto_push_reports')}")
except:
    print("  (config error)")
PY

    echo "— last activity (log):"
    if [ -f "$n" ]; then
      grep -E 'SERVER ONLINE|START Version|RUNNING 20|CHECKS \||hashtag_timeline|reply OK|VERIFY_DELETED|git|push|rate_limited|ERROR \||Traceback|SUMMARY ' "$n" | tail -n 10 || true
    else
      echo "  (no log found for today)"
    fi
    ;;

  stats)
    "$REPO/.venv/bin/python" tools/report_stats.py
    ;;

  # --- Dev & Data ---
  run)
    echo "Running bot once (manual trigger)..."
    "$REPO/.venv/bin/python" -u "$REPO/bot.py" --once
    ;;

  info|bot_version)
    pyv="$("$REPO/.venv/bin/python" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || true)"
    v="$("$REPO/.venv/bin/python" -c "import re; print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('bot.py').read()).group(1))" 2>/dev/null || echo unknown)"
    
    # helper for config
    getStatus() {
        "$REPO/.venv/bin/python" -c "import json; print(bool(json.load(open('config.json')).get('$1', False)))" 2>/dev/null || echo "?"
    }
    
    gh="$(git rev-parse --short HEAD 2>/dev/null || echo no-git)"
    echo "Version: $v"
    echo "Python:  $pyv"
    echo "Git:     $gh"
    echo "Mode:    test_mode=$(getStatus test_mode), auto_push=$(getStatus auto_push_reports)"
    ;;

  check_data)
    "$REPO/.venv/bin/python" tools/check_data.py --reports reports.geojson --entities entities.json
    ;;
    
  fix_data)
    "$REPO/.venv/bin/python" tools/fix_data.py --reports reports.geojson --entities entities.json
    ;;

  git_status)  git status ;;
  
  git_diff)    git diff --stat ;;

  *)
    echo "Unknown command: $cmd"
    help_msg
    exit 2
    ;;
esac
