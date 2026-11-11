#!/usr/bin/env python3
"""Step 2: turn the cached wiki dumps into resources/skills.json and download icons.

Run tools/fetch_wikis.py first. Re-running is cheap: icons that already exist are skipped.

skills.json layout:
{
  "skills": { "<id>": { name, de_name, desc, de_desc, type, de_type, prof, attr, elite,
                         campaign, energy, activation, recharge, adrenaline, upkeep,
                         sacrifice, overcast, icon, en_page, de_page } },
  "attributes": { "<attr id>": { en, de, prof } },
  "professions": { "<prof id>": { en, de, abbr, icon } },
  "stat_icons": { energy: "...", ... }
}

Description strings contain progression tokens that the UI evaluates per attribute rank:
  «a|b»   linear progression, value a at rank 0 and b at rank 15
  ‹text›  fixed highlighted text (non-standard ranges like 10...100)
"""
import concurrent.futures as cf
import collections
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "tools" / ".cache"
RES = ROOT / "resources"
ICONS = RES / "icons"
EN_API = "https://wiki.guildwars.com/api.php"
UA = "gw-skill-template-visualizer/1.0 (personal, non-commercial)"

# Template attribute ids (from the official skill template format).
ATTRIBUTES = {
    0: ("Fast Casting", "Schnellwirkung", 5), 1: ("Illusion Magic", "Illusionsmagie", 5),
    2: ("Domination Magic", "Beherrschungsmagie", 5), 3: ("Inspiration Magic", "Inspirationsmagie", 5),
    4: ("Blood Magic", "Blutmagie", 4), 5: ("Death Magic", "Todesmagie", 4),
    6: ("Soul Reaping", "Seelensammlung", 4), 7: ("Curses", "Flüche", 4),
    8: ("Air Magic", "Luftmagie", 6), 9: ("Earth Magic", "Erdmagie", 6),
    10: ("Fire Magic", "Feuermagie", 6), 11: ("Water Magic", "Wassermagie", 6),
    12: ("Energy Storage", "Energiespeicherung", 6), 13: ("Healing Prayers", "Heilgebete", 3),
    14: ("Smiting Prayers", "Peinigungsgebete", 3), 15: ("Protection Prayers", "Schutzgebete", 3),
    16: ("Divine Favor", "Gunst der Götter", 3), 17: ("Strength", "Stärke", 1),
    18: ("Axe Mastery", "Axtbeherrschung", 1), 19: ("Hammer Mastery", "Hammerbeherrschung", 1),
    20: ("Swordsmanship", "Schwertkunst", 1), 21: ("Tactics", "Taktik", 1),
    22: ("Beast Mastery", "Tierbeherrschung", 2), 23: ("Expertise", "Fachkenntnis", 2),
    24: ("Wilderness Survival", "Überleben in der Wildnis", 2), 25: ("Marksmanship", "Treffsicherheit", 2),
    29: ("Dagger Mastery", "Dolchbeherrschung", 7), 30: ("Deadly Arts", "Tödliche Künste", 7),
    31: ("Shadow Arts", "Schattenkünste", 7), 32: ("Communing", "Zwiesprache", 8),
    33: ("Restoration Magic", "Wiederherstellungsmagie", 8), 34: ("Channeling Magic", "Kanalisierungsmagie", 8),
    35: ("Critical Strikes", "Kritische Stöße", 7), 36: ("Spawning Power", "Macht des Herbeirufens", 8),
    37: ("Spear Mastery", "Speerbeherrschung", 9), 38: ("Command", "Befehlsgewalt", 9),
    39: ("Motivation", "Motivation", 9), 40: ("Leadership", "Führung", 9),
    41: ("Scythe Mastery", "Sensenbeherrschung", 10), 42: ("Wind Prayers", "Windgebete", 10),
    43: ("Earth Prayers", "Erdgebete", 10), 44: ("Mysticism", "Mystik", 10),
}
PROFESSIONS = {
    0: ("None", "Keine", "X"), 1: ("Warrior", "Krieger", "W"), 2: ("Ranger", "Waldläufer", "R"),
    3: ("Monk", "Mönch", "Mo"), 4: ("Necromancer", "Nekromant", "N"), 5: ("Mesmer", "Mesmer", "Me"),
    6: ("Elementalist", "Elementarmagier", "E"), 7: ("Assassin", "Assassine", "A"),
    8: ("Ritualist", "Ritualist", "Rt"), 9: ("Paragon", "Paragon", "P"), 10: ("Dervish", "Derwisch", "D"),
}
STAT_ICONS = {
    "energy": "Tango-energy.png", "activation": "Tango-activation-darker.png",
    "recharge": "Tango-recharge-darker.png", "adrenaline": "Tango-adrenaline.png",
    "upkeep": "Tango-upkeep.png", "sacrifice": "Tango-sacrifice.png", "overcast": "Tango-overcast.png",
}


# --------------------------------------------------------------------------- wikitext parsing
def find_template(text, names):
    """Return the raw inner text of the first {{name ...}} whose name is in `names`."""
    norm = {n.lower().replace("_", " ") for n in names}
    for m in re.finditer(r"\{\{\s*([^|{}]+?)\s*(?=\||\}\})", text):
        if m.group(1).lower().replace("_", " ") not in norm:
            continue
        end = match_braces(text, m.start())
        if end:
            return text[m.start() + 2:end - 2]
    return None


def match_braces(text, start):
    """Given text[start:] starting with '{{', return index after the matching '}}'."""
    depth, i = 0, start
    while i < len(text):
        two = text[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            i += 2
        elif two in ("}}", "]]"):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return None


def split_top(s, sep="|"):
    """Split on `sep` outside of nested {{ }} and [[ ]]."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(s):
        two = s[i:i + 2]
        if two in ("{{", "[["):
            depth += 1; cur.append(two); i += 2; continue
        if two in ("}}", "]]"):
            depth -= 1; cur.append(two); i += 2; continue
        if s[i] == sep and depth == 0:
            parts.append("".join(cur)); cur = []; i += 1; continue
        cur.append(s[i]); i += 1
    parts.append("".join(cur))
    return parts


def template_params(inner):
    parts = split_top(inner)
    named, positional = {}, []
    for p in parts[1:]:
        k, eq, v = p.partition("=")
        if eq and re.fullmatch(r"\s*[\w \-äöüÄÖÜß]+\s*", k):
            named[k.strip().lower()] = v.strip()
        else:
            positional.append(p.strip())
    return parts[0].strip(), named, positional


FRACTIONS = {"1/4": "¼", "1/2": "½", "3/4": "¾", "1/3": "⅓", "2/3": "⅔"}


def render_template(inner):
    name, named, pos = template_params(inner)
    key = name.lower()
    if key in FRACTIONS:
        return FRACTIONS[key]
    if key in ("gr", "p1"):
        if len(pos) >= 2:
            return f"«{clean(pos[0])}|{clean(pos[1])}»"
    if key in ("gr2", "p2", "gr bonus mission pack"):
        if len(pos) >= 2:
            sign = pos[2] if len(pos) > 2 and pos[2] in "+-" else ""
            return f"‹{sign}{clean(pos[0])}...{clean(pos[1])}›"
    if key == "gray":
        return clean(pos[0]) if pos else ""
    return ""  # sic, refs, unknown formatting templates


def clean(s):
    """Wikitext → plain text with progression tokens."""
    if not s:
        return ""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>|<ref.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", " ", s)
    # templates, innermost first
    for _ in range(10):
        m = None
        for m in re.finditer(r"\{\{((?:(?!\{\{|\}\}).)*)\}\}", s, flags=re.S):
            break
        if not m:
            break
        s = s[:m.start()] + render_template(m.group(1)) + s[m.end():]
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)  # links
    s = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", s)
    s = re.sub(r"'{2,}", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def num(s):
    s = clean(s)
    return s if s and s not in ("0",) else None


# --------------------------------------------------------------------------- build
def parse_en(pages):
    skills = {}
    for title, text in pages.items():
        if "/" in title:
            continue
        inner = find_template(text, ["Skill infobox"])
        if not inner:
            continue
        _, p, _ = template_params(inner)
        ids = [int(x) for x in re.findall(r"\d+", re.sub(r"<!--.*?-->", "", p.get("id", ""), flags=re.S))]
        if not ids:
            continue
        name = clean(p.get("name")) or title
        image = re.sub(r"^(?:File|Image):", "", clean(p.get("image")), flags=re.I) or f"{name}.jpg"
        image = image.replace("_", " ")
        entry = {
            "name": name,
            "desc": clean(p.get("description")),
            "concise": clean(p.get("concise description")),
            "type": clean(p.get("type")),
            "prof": clean(p.get("profession")) or "None",
            "attr": clean(p.get("attribute")) or None,
            "elite": p.get("elite", "").strip().lower() in ("y", "yes", "true", "1"),
            "campaign": clean(p.get("campaign")),
            "energy": num(p.get("energy")),
            "activation": num(p.get("activation")),
            "recharge": num(p.get("recharge")),
            "adrenaline": num(p.get("adrenaline")),
            "upkeep": num(p.get("upkeep")),
            "sacrifice": num(p.get("sacrifice")),
            "overcast": num(p.get("overcast")),
            "special": clean(p.get("special")),
            "en_page": title,
            "_image": image,
        }
        for sid in ids:
            if sid not in skills:
                skills[sid] = dict(entry)
    return skills


def parse_de(pages):
    by_en = {}
    for title, text in pages.items():
        if "/" in title:
            continue
        inner = find_template(text, ["Infobox Fertigkeit"])
        if not inner:
            continue
        _, p, _ = template_params(inner)
        en = re.search(r"\{\{\s*en\s*\|\s*(?:gww\s*=\s*)?([^|}]+)", text)
        if not en:
            continue
        en_name = en.group(1).strip().replace("_", " ")
        by_en[en_name] = {
            "de_name": clean(p.get("name")) or title,
            "de_desc": clean(p.get("beschreibung")),
            "de_concise": clean(p.get("kurzbeschreibung")),
            "de_type": clean(p.get("typ")),
            "de_attr": clean(p.get("attribut")),
            "de_page": title,
        }
    return by_en


def api(params):
    url = EN_API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            if "error" in d:
                raise RuntimeError(d["error"].get("code"))
            return d
        except Exception as e:  # noqa: BLE001
            print("  retry", attempt, e)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("failed: " + url)


def resolve_file_urls(filenames):
    """File names → direct image URLs (batched imageinfo queries)."""
    cache_file = CACHE / "file_urls.json"
    urls = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    names = sorted(set(filenames) - set(urls))
    for i in range(0, len(names), 50):
        batch = names[i:i + 50]
        try:
            d = api({"action": "query", "prop": "imageinfo", "iiprop": "url",
                     "titles": "|".join("File:" + n for n in batch)})
        except RuntimeError as e:
            print("  skipping batch:", e)
            continue
        q = d.get("query", {})
        back = {}
        for n in q.get("normalized", []):
            back[n["to"]] = n["from"]
        for r in q.get("redirects", []):
            back[r["to"]] = back.get(r["from"], r["from"])
        for page in q.get("pages", {}).values():
            info = page.get("imageinfo")
            if not info:
                continue
            t = page["title"]
            while t in back:
                t = back[t]
            urls[t.split(":", 1)[1]] = info[0]["url"]
        print(f"  resolved {min(i + 50, len(names))}/{len(names)} file urls")
        cache_file.write_text(json.dumps(urls, ensure_ascii=False))
        time.sleep(0.5)
    return urls


def download(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                dest.write_bytes(r.read())
            return True
        except Exception as e:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
            err = e
    print("  download failed", url, err)
    return False


def main():
    en_pages = json.loads((CACHE / "en_pages.json").read_text())
    de_pages = json.loads((CACHE / "de_pages.json").read_text())
    skills = parse_en(en_pages)
    de = parse_de(de_pages)
    print(f"EN skills with id: {len(skills)}; DE pages with EN link: {len(de)}")

    matched = 0
    attr_votes = collections.defaultdict(collections.Counter)
    for s in skills.values():
        d = de.get(s["name"]) or de.get(s["en_page"])
        if d:
            matched += 1
            s.update({k: v for k, v in d.items() if k != "de_attr"})
            if s["attr"] and d["de_attr"]:
                attr_votes[s["attr"]][d["de_attr"]] += 1
    print(f"matched German: {matched}")

    attributes = {}
    for aid, (en_name, de_name, prof) in ATTRIBUTES.items():
        voted = attr_votes.get(en_name)
        if voted and voted.most_common(1)[0][0] != de_name:
            print(f"  note: attribute {en_name}: table says {de_name!r}, wiki says {voted.most_common(1)[0][0]!r}")
        attributes[aid] = {"en": en_name, "de": de_name, "prof": prof}
    de_attr_by_en = {a["en"]: a["de"] for a in attributes.values()}
    for s in skills.values():
        s["de_attr"] = de_attr_by_en.get(s["attr"]) if s["attr"] else None

    # ---- icons
    ICONS.mkdir(parents=True, exist_ok=True)
    stat_dir = RES / "ui"
    stat_dir.mkdir(exist_ok=True)
    prof_files = {pid: f"{en}-tango-icon-48.png" for pid, (en, _, _) in PROFESSIONS.items() if pid}
    wanted = [s["_image"] for s in skills.values()] + list(STAT_ICONS.values()) + list(prof_files.values())
    print("resolving icon urls…")
    urls = resolve_file_urls(wanted)

    jobs = []
    for sid, s in skills.items():
        url = urls.get(s.pop("_image").replace("_", " ")) or None
        if url:
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"
            s["icon"] = f"icons/{sid}{ext}"
            jobs.append((url, RES / s["icon"]))
        else:
            s["icon"] = None
    stat_icons = {}
    for k, f in STAT_ICONS.items():
        if f in urls:
            stat_icons[k] = f"ui/{k}.png"
            jobs.append((urls[f], RES / stat_icons[k]))
    professions = {}
    for pid, (en, dn, ab) in PROFESSIONS.items():
        icon = None
        if pid and prof_files[pid] in urls:
            icon = f"ui/prof-{ab}.png"
            jobs.append((urls[prof_files[pid]], RES / icon))
        professions[pid] = {"en": en, "de": dn, "abbr": ab, "icon": icon}

    print(f"downloading {len(jobs)} images (existing ones skipped)…")
    with cf.ThreadPoolExecutor(6) as ex:
        ok = list(ex.map(lambda j: download(*j), jobs))
    print(f"  {sum(ok)}/{len(jobs)} ok")
    for s in skills.values():
        if s["icon"] and not (RES / s["icon"]).exists():
            s["icon"] = None

    out = {
        "generated": time.strftime("%Y-%m-%d"),
        "sources": ["https://wiki.guildwars.com (CC BY-NC-SA 2.5)", "https://www.guildwiki.de"],
        "skills": {str(k): v for k, v in sorted(skills.items())},
        "attributes": {str(k): v for k, v in attributes.items()},
        "professions": {str(k): v for k, v in professions.items()},
        "stat_icons": stat_icons,
    }
    (RES / "skills.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print("wrote", RES / "skills.json")


if __name__ == "__main__":
    main()
