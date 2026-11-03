"use strict";

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function button(label, onclick, cls) {
  const b = el("button", label, cls);
  b.type = "button";
  b.onclick = onclick;
  return b;
}

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

function tagSpans(tags) {
  const frag = document.createDocumentFragment();
  tags.forEach(t => frag.appendChild(el("span", t, "tag")));
  return frag;
}

async function api(url, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (init.method !== "GET") init.headers["X-VendLog"] = "1";
  if (opts.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  }
  if (opts.body) init.body = opts.body;
  const r = await fetch(url, init);
  let data = {};
  try { data = await r.json(); } catch (_) { /* non-JSON error page */ }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function field(label, input) {
  const wrap = el("label", undefined, "field");
  wrap.appendChild(el("span", label));
  wrap.appendChild(input);
  return wrap;
}

// Chip editor with autocomplete from the shared vocabulary (datalist #tag-options).
function tagPicker(initial, placeholder) {
  let tags = [...initial];
  const node = el("div");
  const chips = el("div", undefined, "chips");
  const input = el("input");
  input.setAttribute("list", "tag-options");
  input.placeholder = placeholder || "Add tag (type or pick)";
  const draw = () => {
    chips.replaceChildren();
    tags.forEach(t => {
      const s = el("span", t + " ", "tag");
      s.appendChild(button("\u00d7", () => { tags = tags.filter(x => x !== t); draw(); }, "x"));
      chips.appendChild(s);
    });
  };
  const add = () => {
    const v = input.value.split(/\s+/).join(" ").trim();
    if (v && !tags.some(t => t.toLowerCase() === v.toLowerCase())) { tags.push(v); draw(); }
    input.value = "";
  };
  input.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); add(); } };
  const row = el("div", undefined, "row");
  row.append(input, button("Add", add));
  node.append(chips, row);
  draw();
  return { node, get: () => { add(); return [...tags]; }, clear: () => { tags = []; draw(); } };
}

const fileUrl = path => "/api/files/" + path.split("/").map(encodeURIComponent).join("/");
const fail = e => alert(e.message);

// Banners shown on every page: unpushed work, new data on GitHub, newer app.
async function updateBanners() {
  const box = document.getElementById("banners");
  if (!box) return;
  let st;
  try { st = await api("/api/git/auto", { method: "POST", json: {} }); } catch (_) { return; }
  const items = [];
  if (st.connected) {
    const n = (st.changes || []).length;
    if (st.conflicts && st.conflicts.length) items.push(["error", "Merge conflict in " + st.conflicts.join(", ") + ". See Synchronise."]);
    if (n || st.ahead) {
      const parts = [];
      if (n) parts.push(n + " uncommitted file change(s)");
      if (st.ahead) parts.push(st.ahead + " commit(s) not yet pushed");
      items.push(["warn", "Not pushed to GitHub yet: " + parts.join(", ") + ". Go to Synchronise and push before closing VendLog so your colleague gets them."]);
    }
    if (st.pulled) items.push(["muted", st.message]);
    else if (st.behind) items.push(["warn", "GitHub has " + st.behind + " new commit(s). Go to Synchronise and pull."]);
    if (st.offline) items.push(["muted", "Could not reach GitHub; showing the last known status."]);
  }
  if (st.app && st.app.update_available) items.push(["warn", "A newer version of the app is available. See Synchronise."]);
  box.replaceChildren();
  items.forEach(([cls, text]) => {
    const d = el("div", undefined, "banner " + cls);
    d.appendChild(document.createTextNode(text + " "));
    if (location.pathname !== "/sync") {
      const a = el("a", "Open Synchronise"); a.href = "/sync"; d.appendChild(a);
    }
    box.appendChild(d);
  });
}
updateBanners();
setInterval(updateBanners, 30000);
window.addEventListener("focus", updateBanners);
