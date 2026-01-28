#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/de.ox.heatmap-bot.plist"
LABEL="de.ox.heatmap-bot"

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
  log_normal      – tail logs/bot-YYYY-MM-DD.log (main runtime log)
  log_event       – alias of log_normal (events are in bot- log)
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

    if wait_startup_check; then
      echo "🚀 Bot restart completed successfully"
      startup_snapshot
    else
      echo "⚠️ Bot restart: no CHECKS seen within 20s"
      startup_snapshot
      echo "— run manually:"
      postcheck_cmd
    fi
    ;;

  bot_reset)
    echo "⚠️ bot_reset will stop the service, backup logs/state, reset launchd, then start again."
    read -r -p "Type YES to continue: " ans
    if [ "${ans:-}" != "YES" ]; then echo "OK: canceled"; exit 0; fi
    ts="$(TZ=Europe/Berlin date +%F_%H%M%S)"
    mkdir -p "_backup/reset-$ts" || true
    # backup launchd log
    [ -f bot.launchd.log ] && cp -a bot.launchd.log "_backup/reset-$ts/bot.launchd.log" || true
    # backup runtime state (if present)
    for f in pending.json timeline_state.json; do
      [ -f "$f" ] && cp -a "$f" "_backup/reset-$ts/$f" || true
    done
    # clear launchd log (fresh view)
    : > bot.launchd.log
    # restart service
    launchctl bootout  "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "🚀 bot_reset completed (backup in _backup/reset-$ts/)"
    ;;
  server_restart)
    ts="$(TZ=Europe/Berlin date +%FT%T%z)"
    msg="SERVER RESTART INITIATED | reason=maintenance | scheduled=+1min | cancel: sudo /sbin/shutdown -c"
    printf "%s %s\n" "$ts" "$msg" >> bot.launchd.log
    echo "⚠️ $msg"
    echo "— after reboot, run this check:" 
    postcheck_cmd
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
  log_normal)  f="logs/bot-$(today).log";   [ -f "$f" ] || f="bot-$(today).log";   tail -n 250 -F "$f" ;;
  log_event)   f="logs/bot-$(today).log";    [ -f "$f" ] || f="bot-$(today).log";    tail -n 250 -F "$f" ;;
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
    b="logs/bot-$d.log"; [ -f "$b" ] || b="bot-$d.log"
    l="bot.launchd.log"

    tail -n 0 -F "$b" "$l" 2>/dev/null | grep --line-buffered -i \
      '(SERVER ONLINE|START Version|RUNNING 20|CHECKS \||SUMMARY |SUBMISSION|REVIEWED|PUBLISHED|PENDING|fav_check|verify_deleted|VERIFY_DELETED|hashtag_timeline|reply OK|auto_push|git|push|rate_limited|http=429|ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout)' || true
    ;;

  monitor_errors)
    d=$(TZ=Europe/Berlin date +%F)
    b="logs/bot-$d.log"; [ -f "$b" ] || b="bot-$d.log"
    l="bot.launchd.log"
    tail -n 0 -F "$b" "$l" 2>/dev/null | grep --line-buffered -i \
      '(ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout|rate_limited|http=429)' || true
    ;;
  show_errors)
    pat="(ERROR|WARN|FAILED|Exception|Traceback|\b(401|403|404|410|429)\b|\b5[0-9]{2}\b|timeout)"
    for f in "logs/bot-$(today).log" "bot.launchd.log" "bot-$(today).log" "logs/normal-$(today).log" "logs/event-$(today).log" "normal-$(today).log" "event-$(today).log"; do
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
    n="logs/bot-$d.log";   [ -f "$n" ] || n="bot-$d.log"

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

    echo "— last activity (bot):"
    if [ -f "$n" ]; then
      egrep -n 'SERVER ONLINE|START Version|RUNNING 20|CHECKS \||hashtag_timeline|reply OK|VERIFY_DELETED|git|push|rate_limited|http=429|ERROR \||Traceback|SUMMARY ' "$n" | tail -n 30 || true
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
  check_bot)
    cd "$REPO" && python3 - <<'PY'
import datetime as dt, re, pathlib, json
now = dt.datetime.now().astimezone()
cut = now - dt.timedelta(minutes=60)
def read_lines(path: pathlib.Path):
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()

# ---------- BOT (launchd log) ----------
bot_log = pathlib.Path("bot.launchd.log")
rx_bot = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*//\s*(\d{2}:\d{2}:\d{2})([+-]\d{2}:\d{2})\s*-\s*(.*)$")
bot = {"reply_ok":0,"reply_fail":0,"reply_err":0,"rate":0,"starts":0,"checks":0,"summary":0}
bot_lines=[]
for line in read_lines(bot_log):
    m = rx_bot.match(line)
    if not m:
        continue
    ts = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{m.group(3)}")
    if ts < cut:
        continue
    rest = m.group(4)
    if rest.startswith("START Version"): bot["starts"] += 1; bot_lines.append(line)
    elif rest.startswith("CHECKS |"): bot["checks"] += 1; bot_lines.append(line)
    elif rest.startswith("SUMMARY "): bot["summary"] += 1; bot_lines.append(line)
    if "reply OK in_reply_to=" in rest: bot["reply_ok"] += 1; bot_lines.append(line)
    elif "reply FAILED in_reply_to=" in rest: bot["reply_fail"] += 1; bot_lines.append(line)
    elif "reply ERROR in_reply_to=" in rest: bot["reply_err"] += 1; bot_lines.append(line)
    elif "🤖 RATE | window=" in rest: bot["rate"] += 1; bot_lines.append(line)

# ---------- DELETE RUNNER (support/support.log) ----------
sup_log = pathlib.Path("support/support.log")
rx_sup = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})([+-]\d{4})\s+(.*)$")
sup = {"del_ok":0,"gone_ok":0,"del_fail":0,"rate_429":0,"batches":0}
sup_lines=[]
for line in read_lines(sup_log):
    m = rx_sup.match(line)
    if not m:
        continue
    tz = m.group(3); tz = tz[:3] + ":" + tz[3:]  # +0100 -> +01:00
    ts = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{tz}")
    if ts < cut:
        continue
    rest = m.group(4)
    if rest.startswith("BATCH "): sup["batches"] += 1; sup_lines.append(line)
    if "DEL OK" in rest: sup["del_ok"] += 1; sup_lines.append(line)
    if "GONE OK" in rest: sup["gone_ok"] += 1; sup_lines.append(line)
    if ("DEL FAIL" in rest) or ("WAIT/FAIL" in rest) or ("FETCH FAIL" in rest): sup["del_fail"] += 1; sup_lines.append(line)
    if (" 429" in rest) or ("rate_limited" in rest) or ("Too many requests" in rest): sup["rate_429"] += 1; sup_lines.append(line)

# ---------- audits overview ----------
aud_dir = pathlib.Path("support")
audits = []
for p in sorted(aud_dir.glob("deleted_*json"), key=lambda x: x.stat().st_mtime, reverse=True)[:6]:
    try:
        js = json.loads(p.read_text(encoding="utf-8", errors="ignore")) or {}
        targets = js.get("targets")
        audits.append({
            "name": p.name,
            "targets": len(targets) if isinstance(targets, list) else None,
            "deleted_ok": js.get("deleted_ok"),
            "deleted_fail": js.get("deleted_fail"),
            "mode": js.get("mode"),
        })
    except Exception:
        audits.append({"name": p.name, "targets": None, "deleted_ok": None, "deleted_fail": None, "mode": None})

print(f"🤖 CHECK_BOT | window=60m | {cut.strftime('%Y-%m-%d %H:%M:%S%z')} .. {now.strftime('%Y-%m-%d %H:%M:%S%z')}")
print(f"BOT: starts={bot['starts']} checks={bot['checks']} summary_lines={bot['summary']} | replies ok={bot['reply_ok']} fail={bot['reply_fail']} err={bot['reply_err']} | rate_lines={bot['rate']}")
print(f"DELETE (support.log): del_ok={sup['del_ok']} gone_ok={sup['gone_ok']} del_fail={sup['del_fail']} | 429_hits={sup['rate_429']} | batches={sup['batches']}")
print("AUDITS (latest):")
for a in audits:
    print(f"- {a['name']} | targets={a['targets']} | deleted_ok={a['deleted_ok']} | deleted_fail={a['deleted_fail']} | mode={a['mode']}")
print("--- BOT last 25 relevant ---")
for l in bot_lines[-25:]: print(l)
print("--- DELETE last 40 relevant ---")
for l in sup_lines[-40:]: print(l)
PY
    ;;

  *)
    echo "unknown command: $cmd"
    help_msg
    exit 2
    ;;
esac
