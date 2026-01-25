#!/usr/bin/env python3
import json, sys, subprocess, urllib.parse
from pathlib import Path

def _curl_json(url: str, timeout_s: int = 15) -> dict:
    cmd = ["curl", "-fsSL", "--max-time", str(int(timeout_s)), url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed rc={r.returncode} stderr={r.stderr.strip()[:200]}")
    return json.loads(r.stdout)

def _wikidata_desc_en(search_term: str) -> str:
    q = urllib.parse.quote(search_term)
    url = (
        "https://www.wikidata.org/w/api.php"
        f"?action=wbsearchentities&search={q}&language=en&format=json&limit=1"
    )
    data = _curl_json(url, timeout_s=15)
    hits = (data or {}).get("search") or []
    if not hits:
        return ""
    desc = (hits[0].get("description") or "").strip()
    if len(desc) > 240:
        desc = desc[:237].rstrip() + "…"
    return desc

def main():
    if len(sys.argv) < 2:
        print("USAGE: tools/entity_enrich.py <entity_key>")
        raise SystemExit(2)

    key = sys.argv[1].strip().lower()
    p = Path("entities.json")
    ent = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if key not in ent:
        raise SystemExit(f"ERROR: key not found: {key}")

    e = ent.get(key) or {}
    # choose best search term (EN-only)
    term = (e.get("display") or "").strip() or (e.get("aliases") or [""])[0].strip() or key

    desc = _wikidata_desc_en(term)
    if not desc:
        # fallback: try raw key if display not found
        if term != key:
            desc = _wikidata_desc_en(key)

    if not desc:
        print(f"SKIP: no EN description found (wikidata) key={key} term={term!r}")
        raise SystemExit(0)

    ent[key]["desc"] = desc
    p.write_text(json.dumps(ent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: enriched {key} desc_len={len(desc)} source=wikidata term={term!r}")

if __name__ == "__main__":
    main()
