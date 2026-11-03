"use strict";

const state = { files: [], current: null, tags: [], selected: new Set() };

// ---- file list -----------------------------------------------------------
function renderList() {
  const tbody = document.querySelector("#files tbody");
  tbody.replaceChildren();
  document.getElementById("empty").hidden = state.files.length > 0;
  state.files.forEach(f => {
    const tr = el("tr");
    if (state.current && state.current.path === f.path) tr.className = "sel";
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = state.selected.has(f.path);
    cb.onclick = e => e.stopPropagation();
    cb.onchange = () => {
      if (cb.checked) state.selected.add(f.path); else state.selected.delete(f.path);
      updateBulkBar();
    };
    const cbTd = el("td");
    cbTd.appendChild(cb);
    tr.appendChild(cbTd);
    tr.appendChild(el("td", f.path));
    tr.appendChild(el("td", fmtSize(f.size)));
    tr.appendChild(el("td", f.datetime ? f.datetime.replace("T", " ") : "-"));
    const td = el("td");
    td.appendChild(tagSpans(f.tags));
    tr.appendChild(td);
    tr.onclick = () => select(f);
    tbody.appendChild(tr);
  });
  updateBulkBar();
}

async function loadFiles() {
  state.files = (await api("/api/files")).files;
  const present = new Set(state.files.map(f => f.path));
  state.selected = new Set([...state.selected].filter(p => present.has(p)));
  if (state.current) state.current = state.files.find(f => f.path === state.current.path) || null;
  renderList();
  renderDetail();
  renderExisting();
}

// Distinct machine names and machine folders already in use (case-insensitive).
function renderExisting() {
  const sel = document.getElementById("machine-options");
  const seen = new Map();
  state.files.forEach(f => {
    const parts = f.path.split("/");
    [f.machine, parts.length > 1 ? parts[0] : ""].forEach(n => {
      n = (n || "").trim();
      if (n && !seen.has(n.toLowerCase())) seen.set(n.toLowerCase(), n);
    });
  });
  const names = [...seen.values()].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  sel.replaceChildren();
  names.forEach(n => { const o = el("option"); o.value = n; sel.appendChild(o); });
  state.existing = names;
  updateUploadUi();
}

const simplify = s => s.toLowerCase().replace(/[^a-z0-9]/g, "");

// Existing names that look like `name` but are spelled differently.
function nearDuplicates(name) {
  const key = simplify(name);
  if (!key) return [];
  const have = state.existing || [];
  if (have.includes(name)) return [];
  return have.filter(n => simplify(n) === key);
}

function select(f) {
  state.current = f;
  renderList();
  renderDetail();
}

// ---- detail / editing ----------------------------------------------------
// field() and tagPicker() live in common.js.

function renderDetail() {
  const pane = document.getElementById("detail-pane");
  const f = state.current;
  if (!f) { pane.replaceChildren(el("p", "Select a file.", "muted")); return; }
  pane.replaceChildren(el("h2", f.name));
  const nameInput = el("input"); nameInput.value = f.name;
  const renameRow = el("div", undefined, "row");
  renameRow.append(nameInput, button("Rename file", async () => {
    const n = nameInput.value.trim();
    if (!n || n === f.name) return;
    try {
      state.current = await api("/api/rename", { method: "POST", json: { path: f.path, name: n } });
      state.selected.delete(f.path);
      await loadFiles();
    } catch (e) { fail(e); }
  }));
  pane.appendChild(renameRow);
  const viewLink = el("a", "Open in viewer \u2192");
  viewLink.href = "/search?open=" + encodeURIComponent(f.path);
  pane.appendChild(viewLink);
  pane.appendChild(el("p", `${f.path} \u2022 ${fmtSize(f.size)} \u2022 modified ${f.modified}`, "muted"));
  if (f.error) pane.appendChild(el("p", "Warning: " + f.error, "error"));

  const machine = el("input"); machine.value = f.machine || "";
  machine.setAttribute("list", "machine-options"); machine.autocomplete = "off";
  const machineField = field("Machine", machine);
  const hint = el("span", "Changing this moves the file to logs/<machine>/ when you save.", "muted");
  hint.style.fontWeight = "normal";
  machineField.appendChild(hint);
  const maker = el("input"); maker.value = f.maker || ""; maker.setAttribute("list", "maker-options");
  maker.autocomplete = "off";
  const when = el("input"); when.value = f.datetime || ""; when.placeholder = "2026-03-01T14:22:00+09:00";
  const desc = el("textarea"); desc.rows = 3; desc.value = f.description || "";
  pane.append(machineField, field("Machine maker", maker), field("Date/time", when), field("Description", desc));

  const picker = tagPicker(f.tags);
  pane.append(el("h3", "Tags"), picker.node);

  const actions = el("div", undefined, "row actions");
  actions.append(
    button("Save", async () => {
      try {
        state.current = await api(fileUrl(f.path), { method: "PUT", json: {
          tags: picker.get(), machine: machine.value, maker: maker.value, datetime: when.value, description: desc.value } });
        await loadFiles();
        await loadMakers();
        await loadTags();
      } catch (e) { fail(e); }
    }, "primary"),
    button("Delete file", async () => {
      if (!confirm(`Delete "${f.path}" and its metadata?\n\nThe file is removed from disk. It can be recovered only from Git history if it was already committed.`)) return;
      try {
        await api(fileUrl(f.path), { method: "DELETE" });
        state.selected.delete(f.path);
        state.current = null;
        await loadFiles();
        await loadTags();
      } catch (e) { fail(e); }
    }, "danger"),
  );
  pane.appendChild(actions);
}

// ---- bulk tagging of selected files ------------------------------------------
const bulkAdd = tagPicker([], "Tags to add");
const bulkRemove = tagPicker([], "Tags to remove");
document.getElementById("bulk-add").appendChild(bulkAdd.node);
document.getElementById("bulk-remove").appendChild(bulkRemove.node);

function updateBulkBar() {
  const n = state.selected.size;
  document.getElementById("bulk-bar").hidden = n === 0;
  document.getElementById("bulk-count").textContent = `${n} file(s) selected`;
  const all = document.getElementById("select-all");
  all.checked = n > 0 && n === state.files.length;
  all.indeterminate = n > 0 && n < state.files.length;
}

document.getElementById("select-all").onchange = e => {
  state.selected = new Set(e.target.checked ? state.files.map(f => f.path) : []);
  renderList();
};
document.getElementById("bulk-clear").onclick = () => { state.selected.clear(); renderList(); };
document.getElementById("bulk-apply").onclick = async () => {
  const add = bulkAdd.get(), remove = bulkRemove.get();
  if (!add.length && !remove.length) { alert("Choose tags to add or remove."); return; }
  const paths = [...state.selected];
  const what = [add.length ? "add: " + add.join(", ") : "", remove.length ? "remove: " + remove.join(", ") : ""]
    .filter(Boolean).join("; ");
  if (!confirm(`Apply to ${paths.length} file(s)?\n${what}`)) return;
  try {
    await api("/api/bulk-tags", { method: "POST", json: { paths, add, remove } });
    bulkAdd.clear(); bulkRemove.clear();
    await loadFiles(); await loadTags();
  } catch (e) { fail(e); }
};

const bulkMachine = document.getElementById("bulk-machine");
bulkMachine.oninput = bulkMachine.onchange = () => {
  const near = nearDuplicates(bulkMachine.value.trim());
  const w = document.getElementById("bulk-dup");
  w.hidden = !near.length;
  w.textContent = near.length ? `Warning: looks like the existing "${near.join('", "')}". Pick it from the list to avoid a similar folder.` : "";
};
document.getElementById("bulk-move").onclick = async () => {
  const machine = bulkMachine.value.trim();
  if (!machine) { alert("Enter a machine name."); return; }
  const paths = [...state.selected];
  const near = nearDuplicates(machine);
  if (near.length && !confirm(`"${machine}" looks like the existing "${near.join('", "')}".\nUse a separate new folder anyway?`)) return;
  if (!confirm(`Move ${paths.length} file(s) to logs/${machine}/ and set their Machine to "${machine}"?`)) return;
  try {
    const res = await api("/api/bulk-move", { method: "POST", json: { paths, machine } });
    state.selected = new Set(res.files.map(f => f.path));
    state.current = null;
    bulkMachine.value = "";
    await loadFiles();
  } catch (e) { fail(e); }
};

// ---- upload ----------------------------------------------------------------
function uploadPrefix() {
  return document.getElementById("up-custom").value.trim();
}

function updateUploadUi() {
  const dup = document.getElementById("up-dup");
  const p = uploadPrefix();
  const m = document.getElementById("up-machine").value.trim();
  const near = nearDuplicates(m);
  dup.hidden = !near.length;
  dup.textContent = near.length
    ? `Warning: "${m}" looks like the existing "${near.join('", "')}". Pick it from the Machine list to avoid creating a similar folder.`
    : "";
  document.getElementById("up-folder").textContent = m
    ? `Files will be stored in the folder logs/${m}/ (this is separate from the filename prefix).`
    : "Enter a machine name to store files in logs/<machine>/. Without one, files go in logs/. (Separate from the filename prefix.)";
  document.getElementById("up-preview").textContent =
    p ? `Example: ${p}_<filename>` : "Files keep their original names.";
}

const upTags = tagPicker([], "Tags to apply to uploaded files");
document.getElementById("up-tags").appendChild(upTags.node);

async function upload(input) {
  if (!input.files.length) return;
  const prefix = uploadPrefix();
  const near = nearDuplicates(document.getElementById("up-machine").value.trim());
  if (near.length && !confirm(`The machine name looks like the existing "${near.join('", "')}".\nUpload anyway and create a separate folder?`)) {
    input.value = "";
    return;
  }
  const fd = new FormData();
  fd.append("prefix", prefix);
  fd.append("machine", document.getElementById("up-machine").value.trim());
  fd.append("tags", JSON.stringify(upTags.get()));
  fd.append("captured", document.getElementById("up-date").value);
  fd.append("maker", document.getElementById("up-maker").value.trim());
  [...input.files].forEach(file => fd.append("files", file));
  try {
    const res = await api("/api/files", { method: "POST", body: fd });
    if (res.errors.length) alert("Not uploaded:\n" + res.errors.map(e => `${e.name}: ${e.error}`).join("\n"));
    if (res.saved.length) state.current = res.saved[0];
    await loadFiles();
    await loadMakers();
  } catch (e) { fail(e); }
  input.value = "";
}

["up-machine", "up-custom"].forEach(id => {
  const n = document.getElementById(id);
  n.oninput = n.onchange = updateUploadUi;
});
updateUploadUi();

async function loadMakers() {
  const dl = document.getElementById("maker-options");
  dl.replaceChildren();
  (await api("/api/makers")).makers.forEach(m => { const o = el("option"); o.value = m; dl.appendChild(o); });
}

// ---- tag manager -----------------------------------------------------------
async function loadTags() {
  state.tags = (await api("/api/tags")).tags;
  const dl = document.getElementById("tag-options");
  dl.replaceChildren();
  state.tags.forEach(t => { const o = el("option"); o.value = t.name; dl.appendChild(o); });
  const body = document.querySelector("#tag-table tbody");
  body.replaceChildren();
  state.tags.forEach(t => {
    const tr = el("tr");
    tr.appendChild(el("td", t.name));
    tr.appendChild(el("td", String(t.count)));
    const td = el("td");
    td.append(
      button("Rename", async () => {
        const n = prompt(`Rename "${t.name}" everywhere to:`, t.name);
        if (n === null || n.trim() === "" || n === t.name) return;
        try {
          await api("/api/tags/rename", { method: "POST", json: { old: t.name, new: n } });
          await loadFiles(); await loadTags();
        } catch (e) { fail(e); }
      }),
      button("Delete", async () => {
        if (!confirm(`Remove tag "${t.name}" from ${t.count} file(s)?`)) return;
        try {
          await api("/api/tags/delete", { method: "POST", json: { name: t.name } });
          await loadFiles(); await loadTags();
        } catch (e) { fail(e); }
      }, "danger"),
    );
    tr.appendChild(td);
    body.appendChild(tr);
  });
}

document.getElementById("upload-input").onchange = e => upload(e.target);
document.getElementById("new-tag-btn").onclick = async () => {
  const input = document.getElementById("new-tag");
  if (!input.value.trim()) return;
  try {
    await api("/api/tags", { method: "POST", json: { name: input.value } });
    input.value = "";
    await loadTags();
  } catch (e) { fail(e); }
};

Promise.all([loadFiles(), loadTags(), loadMakers()]).catch(fail);
