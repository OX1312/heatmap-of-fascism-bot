#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/Oscar_Berngruber/heatmap-of-fascism-bot"
PLIST="$HOME/Library/LaunchAgents/de.ox.heatmap-bot.plist"
LABEL="de.ox.heatmap-bot"

cd "$REPO"

today() { date +%F; }

help_msg() {
  cat <<'TXT'
h               – help (kurz)
bot_status      – läuft? (launchctl list)
bot_start       – start (bootstrap)
bot_stop        – stop (bootout)
bot_restart     – restart (stop+start)
test_run        – sofortlauf (kickstart)
bot_version     – version + modes (+ git hash)
test_report     – test_mode status
auto_report     – auto_push_reports status
log_launchd     – tail bot.launchd.log
log_normal      – tail normal-YYYY-MM-DD.log
log_event       – tail event-YYYY-MM-DD.log
show_errors     – fehler grep (heute)
compile_py      – "$REPO/.venv/bin/python" -m py_compile bot.py
git_status      – git status
git_diff        – git diff --stat
py_dir          – zeigt plist python + workingdir
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
  log_normal)  tail -n 200 -F "normal-$(today).log" ;;
  log_event)   tail -n 200 -F "event-$(today).log" ;;
  show_errors) grep -nE "(ERROR|WARN|FAILED|Exception|Traceback|\\b(401|403|404|410|429)\\b|\\b5[0-9]{2}\\b|timeout)" "normal-$(today).log" | tail -n 120 || true ;;
  compile_py)  "$REPO/.venv/bin/python" -m py_compile bot.py && echo ok ;;
  git_status)  git status ;;
  git_diff)    git diff --stat ;;
  py_dir)
    echo "plist=$PLIST"
    plutil -p "$PLIST" | sed -n '1,120p' | grep -E "WorkingDirectory|ProgramArguments" -n -A3 || true
    ;;
  *)
    echo "unknown command: $cmd"
    help_msg
    exit 2
    ;;
esac
