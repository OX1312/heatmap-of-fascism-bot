#!/usr/bin/env python3
import json, sys, subprocess, urllib.parse
from pathlib import Path

def _curl_json(url: str, timeout_s: int = 15) -> dict:
    cmd = ["curl", "-fsSL", "--max-time", str(int(timeout_s)), url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}

def _qid_from_wikipedia(wiki_lang: str, title: str) -> str:
    q = urllib.parse.quote(title)
    url = f"https://{wiki_lang}.wikipedia.org/w/api.php?action=query&format=json&prop=pageprops&ppprop=wikibase_item&titles={q}"
    data = _curl_json(url, timeout_s=15)
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for _pid, p in pages.items():
        pp = (p or {}).get("pageprops") or {}
        qid = (pp.get("wikibase_item") or "").strip()
        if qid.startswith("Q"):
            return qid
    return ""

def _en_desc_from_qid(qid: str) -> str:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    data = _curl_json(url, timeout_s=15)
    ent = ((data or {}).get("entities") or {}).get(qid) or {}
    desc = (((ent.get("descriptions") or {}).get("en") or {}).get("value") or "").strip()
    if len(desc) > 240:
        desc = desc[:237].rstrip() + "…"
    return desc

def main():
    if len(sys.argv) < 2:
        print("USAGE: tools/entity_enrich.py <entity_key>")
        raise SystemExit(2)

    key = sys.argv[1].strip().lower()
    p = Path("entities.json")
    ent = json.loads(p.read_text(encoding="utf-8"))
    if key not in ent:
        raise SystemExit(f"ERROR: key not found: {key}")

    e = ent[key] if isinstance(ent[key], dict) else {}
    # Prefer explicitly stored QID if you ever add it manually.
    qid = (e.get("qid") or "").strip()

    # Otherwise resolve QID from Wikipedia title if available.
    if not qid:
        wiki_lang = "en"
        title = (e.get("wiki_en") or "").strip()
        if not title:
            wiki_lang = "de"
            title = (e.get("wiki_de") or "").strip()
        if title:
            qid = _qid_from_wikipedia(wiki_lang, title)

    if not qid:
        raise SystemExit("ERROR: no qid and no wiki_(en|de) title to resolve qid")

    desc = _en_desc_from_qid(qid)
    if not desc:
        raise SystemExit("ERROR: empty EN description for qid")

    e["qid"] = qid
    e["desc"] = desc
    ent[key] = e
    p.write_text(json.dumps(ent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: enriched {key} qid={qid} desc_len={len(desc)} source=wikidata_via_wikipedia")

if __name__ == "__main__":
    main()
