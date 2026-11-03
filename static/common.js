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

const fileUrl = path => "/api/files/" + path.split("/").map(encodeURIComponent).join("/");
const fail = e => alert(e.message);
