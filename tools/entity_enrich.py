#!/usr/bin/env python3
import json, sys, urllib.parse, urllib.request
from pathlib import Path

def fetch_wiki_summary(lang: str, title: str) -> str:
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
    req = urllib.request.Request(url, headers={"User-Agent":"HeatmapOfFascismBot-EntityEnrich/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    txt = (data.get("extract") or "").strip()
    if len(txt) > 240:
        txt = txt[:237].rstrip() + "…"
    return txt

def main():
    if len(sys.argv) < 2:
        print("USAGE: tools/entity_enrich.py <entity_key> [--lang de|en]")
        raise SystemExit(2)
    key = sys.argv[1].strip().lower()
    lang = "de"
    if "--lang" in sys.argv:
        i = sys.argv.index("--lang")
        if i+1 < len(sys.argv):
            lang = sys.argv[i+1].strip().lower()

    p = Path("entities.json")
    ent = json.loads(p.read_text(encoding="utf-8"))
    if key not in ent:
        raise SystemExit(f"ERROR: key not found: {key}")

    title = ent[key].get(f"wiki_{lang}") or ent[key].get("wiki_de") or ent[key].get("wiki_en")
    if not title:
        raise SystemExit("ERROR: no wiki_* title set for this key")

    desc = fetch_wiki_summary(lang, title)
    if not desc:
        raise SystemExit("ERROR: empty summary (page?)")

    ent[key]["desc"] = desc
    p.write_text(json.dumps(ent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: enriched {key} desc_len={len(desc)} lang={lang} title={title}")

if __name__ == "__main__":
    main()
