import datetime as dt
import re
import pathlib
import json
import os

def read_lines(path: pathlib.Path):
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()

def main():
    now = dt.datetime.now().astimezone()
    cut = now - dt.timedelta(minutes=60)
    
    # Paths are relative to CWD (project root when run via ox)
    bot_launchd_log = pathlib.Path("bot.launchd.log")
    support_log = pathlib.Path("support/support.log")
    aud_dir = pathlib.Path("support")

    # ---------- BOT (launchd log) ----------
    rx_bot = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*//\s*(\d{2}:\d{2}:\d{2})([+-]\d{2}:\d{2})\s*-\s*(.*)$")
    bot = {"reply_ok":0,"reply_fail":0,"reply_err":0,"rate":0,"starts":0,"checks":0,"summary":0}
    bot_lines=[]
    
    for line in read_lines(bot_launchd_log):
        m = rx_bot.match(line)
        if not m:
            continue
        try:
            ts = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{m.group(3)}")
        except ValueError:
            continue
            
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
    rx_sup = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})([+-]\d{4})\s+(.*)$")
    sup = {"del_ok":0,"gone_ok":0,"del_fail":0,"rate_429":0,"batches":0}
    sup_lines=[]
    
    for line in read_lines(support_log):
        m = rx_sup.match(line)
        if not m:
            continue
        try:
            tz = m.group(3); tz = tz[:3] + ":" + tz[3:]  # +0100 -> +01:00
            ts = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{tz}")
        except ValueError:
            continue

        if ts < cut:
            continue
        
        rest = m.group(4)
        if rest.startswith("BATCH "): sup["batches"] += 1; sup_lines.append(line)
        if "DEL OK" in rest: sup["del_ok"] += 1; sup_lines.append(line)
        if "GONE OK" in rest: sup["gone_ok"] += 1; sup_lines.append(line)
        if ("DEL FAIL" in rest) or ("WAIT/FAIL" in rest) or ("FETCH FAIL" in rest): sup["del_fail"] += 1; sup_lines.append(line)
        if (" 429" in rest) or ("rate_limited" in rest) or ("Too many requests" in rest): sup["rate_429"] += 1; sup_lines.append(line)

    # ---------- audits overview ----------
    audits = []
    if aud_dir.exists():
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

if __name__ == "__main__":
    main()
