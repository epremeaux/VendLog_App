"use strict";

const PAGE = 500;
const S = { tags: new Set(), terms: [], current: null };
const P = { path: null, total: 0, offset: 0, matches: [], mi: -1, query: "" };

const $ = id => document.getElementById(id);

// ---- search ------------------------------------------------------------------
function currentMode() {
  return document.querySelector("input[name=mode]:checked").value;
}

async function runSearch() {
  const params = new URLSearchParams();
  params.set("q", $("q").value);
  params.set("mode", currentMode());
  if ($("content").checked) params.set("content", "1");
  S.tags.forEach(t => params.append("tag", t));
  $("summary").textContent = "Searching\u2026";
  const res = await api("/api/search?" + params);
  S.terms = res.terms;
  renderResults(res);
}

function renderResults(res) {
  const box = $("results");
  box.replaceChildren();
  $("summary").textContent = `${res.files.length} file(s)` + (res.truncated ? " (showing the first results only; narrow your search)" : "");
  res.files.forEach(f => {
    const card = el("div", undefined, "result");
    if (P.path === f.path) card.classList.add("sel");
    card.dataset.path = f.path;
    card.appendChild(el("div", f.path, "r-name"));
    const meta = [fmtSize(f.size)];
    if (f.machine) meta.push(f.machine);
    if (f.maker) meta.push(f.maker);
    if (f.datetime) meta.push(f.datetime.replace("T", " "));
    card.appendChild(el("div", meta.join(" \u2022 "), "muted"));
    if (f.tags.length) { const d = el("div"); d.appendChild(tagSpans(f.tags)); card.appendChild(d); }
    if (f.description) card.appendChild(el("div", f.description, "muted"));
    (f.hits || []).forEach(h => {
      const line = el("div", undefined, "hit");
      line.appendChild(el("span", String(h.line), "n"));
      line.appendChild(highlight(h.text, S.terms));
      card.appendChild(line);
    });
    card.onclick = () => openFile(f, f.hits && f.hits.length ? f.hits[0].line : null);
    box.appendChild(card);
  });
}

// Wrap case-insensitive occurrences of any term in <mark>; text is always inserted as text nodes.
function highlight(text, terms) {
  const frag = document.createDocumentFragment();
  const low = text.toLowerCase();
  const usable = terms.filter(Boolean);
  let i = 0;
  while (i < text.length) {
    let best = -1, bestLen = 0;
    for (const t of usable) {
      const k = low.indexOf(t.toLowerCase(), i);
      if (k !== -1 && (best === -1 || k < best)) { best = k; bestLen = t.length; }
    }
    if (best === -1) { frag.appendChild(document.createTextNode(text.slice(i))); break; }
    if (best > i) frag.appendChild(document.createTextNode(text.slice(i, best)));
    frag.appendChild(el("mark", text.slice(best, best + bestLen)));
    i = best + bestLen;
  }
  return frag;
}

async function loadTagFilters() {
  const { tags } = await api("/api/tags");
  const box = $("tag-filters");
  box.replaceChildren();
  tags.forEach(t => {
    const b = el("button", t.name, "tag filter");
    b.type = "button";
    if (S.tags.has(t.name)) b.classList.add("on");
    b.onclick = () => {
      if (S.tags.has(t.name)) S.tags.delete(t.name); else S.tags.add(t.name);
      b.classList.toggle("on");
      runSearch().catch(fail);
    };
    box.appendChild(b);
  });
}

// ---- editable metadata under the title -------------------------------------------
let pvPicker = null;

async function loadOptions() {
  const fill = (id, values) => {
    const dl = $(id);
    dl.replaceChildren();
    values.forEach(v => { const o = el("option"); o.value = v; dl.appendChild(o); });
  };
  const [tags, makers, files] = await Promise.all([api("/api/tags"), api("/api/makers"), api("/api/files")]);
  fill("tag-options", tags.tags.map(t => t.name));
  fill("maker-options", makers.makers);
  const seen = new Map();
  files.files.forEach(f => {
    [f.machine, f.path.includes("/") ? f.path.split("/")[0] : ""].forEach(n => {
      n = (n || "").trim();
      if (n && !seen.has(n.toLowerCase())) seen.set(n.toLowerCase(), n);
    });
  });
  fill("machine-options", [...seen.values()].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())));
}

function showMeta(f) {
  $("pv-machine").value = f.machine || "";
  $("pv-maker").value = f.maker || "";
  $("pv-dt").value = f.datetime || "";
  $("pv-desc").value = f.description || "";
  pvPicker = tagPicker(f.tags || []);
  $("pv-tags").replaceChildren(pvPicker.node);
  $("pv-save-msg").textContent = "";
}

$("pv-save").onclick = async () => {
  if (!P.path) return;
  const msg = $("pv-save-msg");
  msg.className = "muted";
  msg.textContent = "Saving\u2026";
  try {
    const f = await api(fileUrl(P.path), { method: "PUT", json: {
      tags: pvPicker.get(), machine: $("pv-machine").value, maker: $("pv-maker").value,
      datetime: $("pv-dt").value, description: $("pv-desc").value } });
    const moved = f.path !== P.path;
    P.path = f.path;
    $("pv-name").textContent = f.path;
    showMeta(f);
    msg.textContent = moved ? "Saved. File moved to " + f.path + "." : "Saved.";
    await Promise.all([loadOptions(), loadTagFilters(), runSearch()]);
    if (moved) await loadWindow(P.offset);
  } catch (e) {
    msg.className = "error";
    msg.textContent = e.message;
  }
};

// ---- preview ------------------------------------------------------------------
async function openFile(f, line) {
  P.path = f.path;
  P.matches = []; P.mi = -1;
  const q = S.terms.length && $("content").checked ? S.terms[0] : "";
  P.query = q;
  document.querySelectorAll(".result").forEach(c => c.classList.toggle("sel", c.dataset.path === f.path));
  $("preview-empty").hidden = true;
  $("preview").hidden = false;
  $("pv-name").textContent = f.path;
  showMeta(f);
  $("pv-find").value = q;
  $("pv-matches").textContent = "";
  await loadWindow(line ? Math.floor((line - 1) / PAGE) * PAGE : 0, line);
  if (q) await runFind(q, false);
}

async function loadWindow(offset, markLine) {
  const url = `/api/preview?path=${encodeURIComponent(P.path)}&offset=${offset}&limit=${PAGE}`;
  const d = await api(url);
  P.total = d.total; P.offset = d.offset;
  $("pv-info").textContent = `${d.total.toLocaleString()} lines \u2022 ${d.encoding}`;
  $("pv-range").textContent = d.total
    ? `Lines ${(d.offset + 1).toLocaleString()}\u2013${(d.offset + d.lines.length).toLocaleString()}` : "Empty file";
  $("pv-pgprev").disabled = d.offset === 0;
  $("pv-pgnext").disabled = d.offset + d.lines.length >= d.total;
  const box = $("pv-lines");
  box.replaceChildren();
  const cur = P.mi >= 0 ? P.matches[P.mi].line : markLine;
  let target = null;
  d.lines.forEach((text, i) => {
    const n = d.offset + i + 1;
    const row = el("div", undefined, "ln");
    row.appendChild(el("span", String(n), "n"));
    const t = el("span", undefined, "t");
    t.appendChild(P.query ? highlight(text, [P.query]) : document.createTextNode(text));
    row.appendChild(t);
    if (n === cur) { row.classList.add("cur"); target = row; }
    box.appendChild(row);
  });
  if (target) target.scrollIntoView({ block: "center" });
  else box.scrollTop = 0;
}

async function runFind(q, jump = true) {
  P.query = q.trim();
  if (!P.query) { P.matches = []; P.mi = -1; $("pv-matches").textContent = ""; return loadWindow(P.offset); }
  const d = await api(`/api/preview/find?path=${encodeURIComponent(P.path)}&q=${encodeURIComponent(P.query)}`);
  P.matches = d.matches;
  P.mi = d.matches.length ? 0 : -1;
  $("pv-matches").textContent = d.matches.length
    ? `1 of ${d.matches.length}${d.truncated ? "+" : ""}` : "No matches";
  if (P.matches.length) await gotoMatch(0);
  else if (jump) await loadWindow(P.offset);
}

async function gotoMatch(i) {
  if (!P.matches.length) return;
  P.mi = (i + P.matches.length) % P.matches.length;
  $("pv-matches").textContent = `${P.mi + 1} of ${P.matches.length}`;
  await gotoLine(P.matches[P.mi].line);
}

async function gotoLine(line) {
  line = Math.max(1, Math.min(line, Math.max(P.total, 1)));
  await loadWindow(Math.floor((line - 1) / PAGE) * PAGE, line);
}

// ---- wiring --------------------------------------------------------------------
$("search-form").onsubmit = e => { e.preventDefault(); runSearch().catch(fail); };
document.querySelectorAll("input[name=mode]").forEach(r => r.onchange = () => runSearch().catch(fail));
$("content").onchange = () => runSearch().catch(fail);

$("pv-find-btn").onclick = () => runFind($("pv-find").value).catch(fail);
$("pv-find").onkeydown = e => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  if (P.query === $("pv-find").value.trim() && P.matches.length) gotoMatch(P.mi + 1).catch(fail);
  else runFind($("pv-find").value).catch(fail);
};
$("pv-next").onclick = () => gotoMatch(P.mi + 1).catch(fail);
$("pv-prev").onclick = () => gotoMatch(P.mi - 1).catch(fail);
$("pv-pgprev").onclick = () => { P.mi = -1; loadWindow(Math.max(0, P.offset - PAGE)).catch(fail); };
$("pv-pgnext").onclick = () => { P.mi = -1; loadWindow(P.offset + PAGE).catch(fail); };
$("pv-goto-btn").onclick = () => { const n = parseInt($("pv-goto").value, 10); if (n > 0) { P.mi = -1; gotoLine(n).catch(fail); } };
$("pv-goto").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); $("pv-goto-btn").click(); } };

(async () => {
  await loadTagFilters();
  await loadOptions();
  await runSearch();
  const open = new URLSearchParams(location.search).get("open");
  if (open) await openFile(await api(fileUrl(open)), null);
})().catch(fail);
