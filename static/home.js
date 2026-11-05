"use strict";

const $ = id => document.getElementById(id);

api("/api/settings").then(s => { $("editor-path").value = s.external_editor || ""; }).catch(() => {});

$("editor-browse").onclick = async () => {
  const msg = $("editor-msg");
  msg.className = "muted";
  msg.textContent = "A file dialog should have opened (it may be behind this window)\u2026";
  $("editor-browse").disabled = true;
  try {
    const r = await api("/api/browse-program", { method: "POST", json: {} });
    if (r.path) { $("editor-path").value = r.path; msg.textContent = "Chosen. Click Save to keep it."; }
    else msg.textContent = "";
  } catch (e) {
    msg.className = "error";
    msg.textContent = e.message;
  } finally { $("editor-browse").disabled = false; }
};

$("editor-save").onclick = async () => {
  const msg = $("editor-msg");
  msg.className = "muted";
  msg.textContent = "Saving\u2026";
  try {
    const s = await api("/api/settings", { method: "POST", json: { external_editor: $("editor-path").value } });
    $("editor-path").value = s.external_editor;
    msg.textContent = s.external_editor ? "Saved." : "Saved. No external editor.";
  } catch (e) {
    msg.className = "error";
    msg.textContent = e.message;
  }
};
