"use strict";

/* ===================================================================== state */
const S = {
  db: null,            // resources/skills.json
  attrByEn: {},        // "Marksmanship" -> attribute id
  folders: [],         // [{path, files:[{name, code, tpl}], note}]
  current: null,       // folder path or "decode:<code>"
  lang: load("lang", "en"),
  view: load("view", "full"),
  filter: "",
};

const T = {
  en: { decode: "Show", viewFull: "Full", viewConcise: "Concise", viewBars: "Bars", copy: "Copy",
        copied: "Code copied", notes: "Original post / notes", noAttr: "No attributes",
        invalid: "Not a valid skill template", unknown: "Unknown skill", empty: "No templates match.",
        pasted: "Pasted template", elite: "Elite", otherWiki: "DE", search: "Filter builds or skills…",
        paste: "Paste a template code…", pick: "Pick a build on the left (↑/↓ or j/k to step through)." },
  de: { decode: "Zeigen", viewFull: "Voll", viewConcise: "Kurz", viewBars: "Leisten", copy: "Kopieren",
        copied: "Code kopiert", notes: "Originalbeitrag / Notizen", noAttr: "Keine Attribute",
        invalid: "Keine gültige Fertigkeitenvorlage", unknown: "Unbekannte Fertigkeit", empty: "Keine Treffer.",
        pasted: "Eingefügte Vorlage", elite: "Elite", otherWiki: "EN", search: "Builds oder Fertigkeiten filtern…",
        paste: "Vorlagen-Code einfügen…", pick: "Links einen Build wählen (↑/↓ oder j/k zum Durchblättern)." },
};
const t = (k) => T[S.lang][k];

function load(k, d) { try { return localStorage.getItem("gwtv." + k) || d; } catch { return d; } }
function save(k, v) { try { localStorage.setItem("gwtv." + k, v); } catch { /* ignore */ } }
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/* ===================================================================== template decoding */
// "https://guildwars.magical.ch/<code>" → "<code>" (codes themselves may contain "/")
const stripUrl = (s) => String(s).trim().replace(/^https?:\/\/[^/]+\//i, "");
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function decodeTemplate(code) {
  code = stripUrl(code).replace(/[^A-Za-z0-9+/]/g, "");
  const bits = [];
  for (const c of code) {
    const v = B64.indexOf(c);
    for (let i = 0; i < 6; i++) bits.push((v >> i) & 1);
  }
  let pos = 0;
  const read = (n) => {
    if (pos + n > bits.length) throw new Error("template too short");
    let v = 0;
    for (let i = 0; i < n; i++) v |= bits[pos + i] << i;
    pos += n;
    return v;
  };
  let type = read(4);
  if (type === 14) read(4);           // version
  else pos = 0;                        // legacy codes without the type header
  const pl = read(2) * 2 + 4;
  const primary = read(pl), secondary = read(pl);
  const nAttr = read(4), al = read(4) + 4;
  const attrs = [];
  for (let i = 0; i < nAttr; i++) attrs.push({ id: read(al), rank: read(4) });
  const sl = read(4) + 8;
  const skills = [];
  for (let i = 0; i < 8; i++) skills.push(read(sl));
  if (primary > 10 || secondary > 10) throw new Error("bad profession");
  return { code, primary, secondary, attrs, skills };
}

/* ===================================================================== skill text */
function skillText(sk, field) {
  // field: "name" | "desc" | "concise" | "type" | "attr"
  const de = S.lang === "de";
  const deKey = { name: "de_name", desc: "de_desc", concise: "de_concise", type: "de_type", attr: "de_attr" }[field];
  if (de && sk[deKey]) return { text: sk[deKey], fallback: false };
  return { text: sk[field] || "", fallback: de && !!sk[field] && field !== "attr" };
}

function renderDesc(text, rank) {
  // «a|b» → value at rank (or range), ‹x› → highlighted literal
  return esc(text)
    .replace(/«([^|»]*)\|([^»]*)»/g, (_, a, b) => {
      const x = parseFloat(a), y = parseFloat(b);
      if (rank == null || isNaN(x) || isNaN(y)) return `<span class="prog">${a}...${b}</span>`;
      const v = Math.round(x + ((y - x) * rank) / 15);
      return `<span class="prog" title="${a}...${b} (0...15)">${v}</span>`;
    })
    .replace(/‹([^›]*)›/g, '<span class="prog">$1</span>');
}

function wikiLinks(sk) {
  const en = sk.en_page ? "https://wiki.guildwars.com/wiki/" + encodeURIComponent(sk.en_page.replace(/ /g, "_")) : null;
  const de = sk.de_page ? "https://www.guildwiki.de/wiki/" + encodeURIComponent(sk.de_page.replace(/ /g, "_")) : null;
  return S.lang === "de" ? { main: de || en, other: de ? en : null } : { main: en, other: de };
}

function statsHtml(sk) {
  const icons = S.db.stat_icons;
  const parts = [];
  const add = (k, v, suffix = "") => {
    if (!v) return;
    parts.push(`<span class="stat">${icons[k] ? `<img src="resources/${icons[k]}" alt="${k}">` : k + " "}${esc(v)}${suffix}</span>`);
  };
  add("upkeep", sk.upkeep);
  add("energy", sk.energy);
  add("adrenaline", sk.adrenaline);
  add("sacrifice", sk.sacrifice, sk.sacrifice && !String(sk.sacrifice).includes("%") ? "%" : "");
  add("overcast", sk.overcast);
  add("activation", sk.activation);
  add("recharge", sk.recharge);
  return `<span class="stats">${parts.join("")}</span>`;
}

function rankFor(sk, tpl) {
  const aid = S.attrByEn[sk.attr];
  if (aid == null) return null;                  // PvE title tracks etc.
  const a = tpl.attrs.find((x) => x.id === aid);
  return a ? a.rank : 0;
}

function skillInfoHtml(sid, tpl, mode) {
  const sk = S.db.skills[sid];
  if (!sk) return `<div class="skill-name">${t("unknown")} #${sid}</div>`;
  const name = skillText(sk, "name");
  const type = skillText(sk, "type");
  const attr = skillText(sk, "attr");
  const d = skillText(sk, mode === "concise" ? "concise" : "desc");
  const desc = d.text || skillText(sk, "desc").text;
  const rank = rankFor(sk, tpl);
  const links = wikiLinks(sk);
  const fb = (x) => (x.fallback ? '<span class="fallback">EN</span>' : "");
  return `
    <div class="skill-name">
      ${links.main ? `<a href="${links.main}" target="_blank" rel="noopener">${esc(name.text)}</a>` : esc(name.text)}${fb(name)}
      ${sk.elite ? `<span class="elite-tag">${t("elite")}</span>` : ""}
      ${links.other ? `<a class="alt" href="${links.other}" target="_blank" rel="noopener">${t("otherWiki")} ↗</a>` : ""}
    </div>
    <div class="skill-meta">
      <span>${esc(type.text)}${attr.text ? " · " + esc(attr.text) + (rank != null ? ` (${rank})` : "") : ""}</span>
      ${statsHtml(sk)}
    </div>
    <div class="desc">${renderDesc(desc, rank)}${fb(d)}</div>`;
}

function slotHtml(sid, i, size) {
  if (!sid) return `<span class="slot empty"></span>`;
  const sk = S.db.skills[sid];
  const name = sk ? skillText(sk, "name").text : "#" + sid;
  const link = sk ? wikiLinks(sk).main : null;
  const inner = sk && sk.icon
    ? `<img src="resources/${sk.icon}" alt="${esc(name)}" loading="lazy">`
    : `<span class="noicon">${esc(name)}</span>`;
  const tag = link ? "a" : "span";
  return `<${tag} class="slot${sk && sk.elite ? " elite" : ""}" data-sid="${sid}" data-i="${i}"
    ${link ? `href="${link}" target="_blank" rel="noopener"` : ""}>${inner}</${tag}>`;
}

/* ===================================================================== rendering */
function profHtml(id) {
  const p = S.db.professions[id];
  if (!p || !p.icon) return "";
  return `<img src="resources/${p.icon}" alt="${esc(p[S.lang])}" title="${esc(p[S.lang])}">`;
}

function cardHtml(file, idx) {
  const tpl = file.tpl;
  if (!tpl) {
    return `<section class="card"><div class="card-head"><span class="card-title">${esc(file.name)}</span>
      <span class="code">${esc(file.code)}</span></div><div class="error">${t("invalid")}</div></section>`;
  }
  const P = S.db.professions;
  const pn = `${P[tpl.primary]?.[S.lang] ?? "?"}${tpl.secondary ? " / " + (P[tpl.secondary]?.[S.lang] ?? "?") : ""}`;
  const attrs = tpl.attrs.length
    ? tpl.attrs.map((a) => `<span class="attr">${esc(S.db.attributes[a.id]?.[S.lang] ?? "#" + a.id)}<b>${a.rank}</b></span>`).join("")
    : `<span class="attr">${t("noAttr")}</span>`;
  const mode = S.view;
  return `
  <section class="card" data-idx="${idx}">
    <div class="card-head">
      <span class="profs">${profHtml(tpl.primary)}${profHtml(tpl.secondary)}</span>
      <span class="card-title">${esc(file.name)}</span>
      <span class="prof-names">${esc(pn)}</span>
      <span class="code"><span>${esc(file.code)}</span><button data-copy="${esc(file.code)}">${t("copy")}</button></span>
    </div>
    <div class="attrs">${attrs}</div>
    <div class="bar">${tpl.skills.map((s, i) => slotHtml(s, i)).join("")}</div>
    ${mode === "bars" ? "" : `<div class="details">${tpl.skills.map((sid, i) => !sid ? "" : `
      <div class="skill">${slotHtml(sid, i)}<div class="skill-body">${skillInfoHtml(sid, tpl, mode)}</div></div>`).join("")}</div>`}
  </section>`;
}

function renderMain() {
  const main = $("#main");
  $("#tooltip").hidden = true;   // the hovered slot is about to be replaced
  main.className = "main view-" + S.view;
  let folder;
  if (S.current && S.current.startsWith("decode:")) {
    const code = S.current.slice(7);
    folder = { path: t("pasted"), files: [withTpl({ name: t("pasted"), code })], note: null };
  } else {
    folder = S.folders.find((f) => f.path === S.current);
  }
  if (!folder) { main.innerHTML = `<p class="empty">${t("pick")}</p>`; return; }
  const parts = folder.path.split("/");
  main.innerHTML = `
    <div class="crumbs">${esc(parts.slice(0, -1).join(" / "))}</div>
    <h1>${esc(parts[parts.length - 1])}</h1>
    ${folder.note ? `<details class="note"><summary>${t("notes")}</summary><pre>${linkify(esc(folder.note))}</pre></details>` : ""}
    <div class="cards">${folder.files.map(cardHtml).join("")}</div>`;
  main._folder = folder;
  main.scrollTop = 0;
}

function linkify(s) {
  return s.replace(/https?:\/\/[^\s<)]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
}

function renderTree() {
  const tree = $("#tree");
  const q = S.filter.trim().toLowerCase();
  const groups = new Map();
  for (const f of S.folders) {
    if (q && !folderMatches(f, q)) continue;
    const top = f.path.split("/").slice(0, -1).join("/") || "/";
    if (!groups.has(top)) groups.set(top, []);
    groups.get(top).push(f);
  }
  if (!groups.size) { tree.innerHTML = `<p class="empty">${t("empty")}</p>`; return; }
  const curTop = S.current && !S.current.startsWith("decode:") ? S.current.split("/").slice(0, -1).join("/") : null;
  tree.innerHTML = [...groups].map(([top, fs]) => `
    <details class="channel" ${q || top === curTop ? "open" : ""}>
      <summary><span class="label" title="${esc(top)}">${esc(top.replace(/^Skills\//, ""))}</span><span class="count">${fs.length}</span></summary>
      ${fs.map((f) => `<button class="build${f.path === S.current ? " active" : ""}" data-path="${esc(f.path)}" title="${esc(f.path)}">
        <span class="label">${esc(f.path.split("/").pop())}</span><span class="count">${f.files.length}</span></button>`).join("")}
    </details>`).join("");
  const act = $(".build.active", tree);
  if (act) act.scrollIntoView({ block: "nearest" });
}

function folderMatches(f, q) {
  if (!f._search) {
    const names = [];
    for (const file of f.files) {
      names.push(file.name, file.code);
      for (const sid of file.tpl?.skills ?? []) {
        const sk = S.db.skills[sid];
        if (sk) names.push(sk.name, sk.de_name || "");
      }
    }
    f._search = (f.path + "\n" + names.join("\n")).toLowerCase();
  }
  return q.split(/\s+/).every((w) => f._search.includes(w));
}

function select(path, { push = true } = {}) {
  S.current = path;
  if (push) history.replaceState(null, "", "#" + encodeURIComponent(path));
  renderTree();
  renderMain();
}

function applyChrome() {
  document.documentElement.lang = S.lang;
  for (const b of document.querySelectorAll("#lang-toggle button")) b.classList.toggle("on", b.dataset.lang === S.lang);
  for (const b of document.querySelectorAll("#view-toggle button")) b.classList.toggle("on", b.dataset.view === S.view);
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  $("#search").placeholder = t("search");
  $("#decode-input").placeholder = t("paste");
}

/* ===================================================================== tooltip */
function showTip(slot, ev) {
  const sid = +slot.dataset.sid;
  const folder = $("#main")._folder;
  const card = slot.closest(".card");
  const tpl = folder && card ? folder.files[+card.dataset.idx].tpl : null;
  if (!sid || !tpl) return;
  const tip = $("#tooltip");
  tip.innerHTML = skillInfoHtml(sid, tpl, S.view === "concise" ? "concise" : "desc");
  tip.hidden = false;
  moveTip(ev);
}
function moveTip(ev) {
  const tip = $("#tooltip");
  if (tip.hidden) return;
  const pad = 14, r = tip.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg; el.hidden = false;
  clearTimeout(el._t); el._t = setTimeout(() => (el.hidden = true), 1400);
}

/* ===================================================================== wiring */
function withTpl(file) {
  try { file.tpl = decodeTemplate(file.code); } catch { file.tpl = null; }
  return file;
}

async function init() {
  const [db, tree] = await Promise.all([
    fetch("resources/skills.json").then((r) => r.json()),
    fetch("templates.json").then((r) => r.json()),
  ]);
  S.db = db;
  for (const [id, a] of Object.entries(db.attributes)) S.attrByEn[a.en] = +id;
  S.folders = tree.folders.map((f) => ({ ...f, files: f.files.map(withTpl) }));

  const hash = decodeURIComponent(location.hash.slice(1));
  S.current = hash && (hash.startsWith("decode:") || S.folders.some((f) => f.path === hash)) ? hash : null;
  applyChrome();
  renderTree();
  renderMain();

  $("#tree").addEventListener("click", (e) => {
    const b = e.target.closest(".build");
    if (b) select(b.dataset.path);
  });
  $("#search").addEventListener("input", (e) => { S.filter = e.target.value; renderTree(); });
  $("#decode-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#decode-input").value.trim();
    if (v) select("decode:" + stripUrl(v));
  });
  $("#lang-toggle").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.lang = b.dataset.lang; save("lang", S.lang);
    for (const f of S.folders) delete f._search;
    applyChrome(); renderTree(); renderMain();
  });
  $("#view-toggle").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.view = b.dataset.view; save("view", S.view);
    applyChrome(); renderMain();
  });
  $("#main").addEventListener("click", async (e) => {
    const c = e.target.closest("[data-copy]");
    if (!c) return;
    try { await navigator.clipboard.writeText(c.dataset.copy); toast(t("copied")); }
    catch { prompt("Copy:", c.dataset.copy); }
  });
  $("#main").addEventListener("mouseover", (e) => {
    const s = e.target.closest(".bar .slot[data-sid]");
    if (s) showTip(s, e);
  });
  $("#main").addEventListener("mousemove", moveTip);
  $("#main").addEventListener("mouseout", (e) => {
    if (e.target.closest(".bar .slot")) $("#tooltip").hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
    const dir = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1 }[e.key];
    if (e.key === "/") { e.preventDefault(); $("#search").focus(); return; }
    if (!dir) return;
    e.preventDefault();
    const list = S.folders.filter((f) => !S.filter.trim() || folderMatches(f, S.filter.trim().toLowerCase())).map((f) => f.path);
    if (!list.length) return;
    const i = list.indexOf(S.current);
    select(list[i < 0 ? 0 : Math.min(list.length - 1, Math.max(0, i + dir))]);
  });
}

init().catch((err) => {
  $("#main").innerHTML = `<p class="error">Failed to load: ${esc(err.message)}</p>`;
});
