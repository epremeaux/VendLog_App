"use strict";

const $ = id => document.getElementById(id);

api("/api/settings").then(s => { $("editor-path").value = s.external_editor || ""; }).catch(() => {});

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
