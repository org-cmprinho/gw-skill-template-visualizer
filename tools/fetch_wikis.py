#!/usr/bin/env python3
"""Step 1: dump raw wikitext of all skill pages from the English and German GW wikis.

Output: tools/.cache/en_pages.json, tools/.cache/de_pages.json  ({title: wikitext})
"""
import json, time, urllib.parse, urllib.request
from pathlib import Path

CACHE = Path(__file__).parent / ".cache"
UA = "gw-skill-template-visualizer/1.0 (personal, non-commercial)"

SOURCES = {
    "en": ("https://wiki.guildwars.com/api.php", "Template:Skill infobox"),
    "de": ("https://www.guildwiki.de/gwiki/api.php", "Vorlage:Infobox Fertigkeit"),
}


def api(base, params):
    params = {**params, "format": "json"}
    url = base + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print("  retry", attempt, e)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("failed: " + url)


def dump(lang):
    base, template = SOURCES[lang]
    out = {}
    cont = {}
    while True:
        params = {
            "action": "query", "generator": "embeddedin", "geititle": template,
            "geinamespace": 0, "geilimit": 50,
            "prop": "revisions", "rvprop": "content", "rvslots": "main",
            **cont,
        }
        d = api(base, params)
        if "error" in d or "warnings" in d:
            print("  api says:", d.get("error") or d.get("warnings"))
            if "error" in d:
                time.sleep(10)
                continue
        for p in d.get("query", {}).get("pages", {}).values():
            revs = p.get("revisions")
            if revs:
                out[p["title"]] = revs[0]["slots"]["main"]["*"]
        print(f"{lang}: {len(out)} pages")
        if "continue" not in d:
            break
        cont = d["continue"]
        time.sleep(0.3)
    return out


if __name__ == "__main__":
    CACHE.mkdir(exist_ok=True)
    for lang in SOURCES:
        f = CACHE / f"{lang}_pages.json"
        if f.exists():
            print("cached:", f)
            continue
        f.write_text(json.dumps(dump(lang), ensure_ascii=False))
