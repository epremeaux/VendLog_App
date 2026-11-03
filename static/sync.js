"use strict";

const $ = id => document.getElementById(id);
let pending = null;
let busy = false;

function say(text, isError) {
  const m = $("msg");
  m.textContent = text || "";
  m.className = isError ? "error" : "muted";
}

async function run(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("button").forEach(b => b.disabled = true);
  say("Working...");
  try { await fn(); }
  catch (e) { say(e.message, true); await refresh(false).catch(() => {}); }
  finally { busy = false; document.querySelectorAll("button").forEach(b => b.disabled = false); updateLock(); }
}

function render(st) {
  const body = $("st-body");
  body.replaceChildren();
  $("sync-actions").hidden = !st.connected;
  $("changes").hidden = true;
  if (!st.connected) {
    body.textContent = "No repository is connected to the logs folder. Unlock below and enter the data repository URL.";
    if (!$("repo-url").value && st.default_url) $("repo-url").value = st.default_url;
    return;
  }
  const rows = [
    ["Remote", st.remote || "(none)"],
    ["Branch", st.branch + (st.upstream ? " \u2192 " + st.upstream : " (no upstream)")],
    ["Local changes", st.changes.length ? st.changes.length + " file(s) not yet committed" : "none"],
    ["Ahead of GitHub", st.ahead === null ? "unknown" : st.ahead + " commit(s) to push"],
    ["Behind GitHub", st.behind === null ? "unknown" : st.behind + " commit(s) to pull"],
  ];
  const dl = el("dl");
  rows.forEach(([k, v]) => { dl.append(el("dt", k), el("dd", v)); });
  body.appendChild(dl);
  if (st.conflicts.length || st.merging) body.appendChild(el("p", "Merge conflict in: " + (st.conflicts.join(", ") || "(merge in progress)") + ". Resolve with git in the logs folder.", "error"));
  else if (st.diverged) body.appendChild(el("p", "Both you and GitHub have new commits. Pull will merge them; if the same file changed on both sides, nothing is changed and you are told which.", "warn"));
  else if (st.behind) body.appendChild(el("p", "GitHub has newer changes. Pull before pushing.", "warn"));
  if (st.changes.length) {
    const c = $("changes");
    c.hidden = false;
    c.textContent = st.changes.slice(0, 200).map(x => x.code.padEnd(2) + " " + x.path).join("\n")
      + (st.changes.length > 200 ? "\n..." : "");
  }
}

async function refresh(fetchRemote) {
  const st = await api(fetchRemote ? "/api/git/fetch" : "/api/git/status", fetchRemote ? { method: "POST", json: {} } : {});
  render(st);
  if (st.connected && st.remote && !$("repo-url").value) $("repo-url").value = st.remote;
  return st;
}

function done(res) {
  render(res);
  say(res.message || "Done.");
}

$("b-refresh").onclick = () => run(async () => { await refresh(true); say("Status refreshed."); });
$("b-pull").onclick = () => run(async () => done(await api("/api/git/pull", { method: "POST", json: {} })));
$("b-push").onclick = () => run(async () => {
  done(await api("/api/git/push", { method: "POST", json: { message: $("commit-msg").value } }));
  $("commit-msg").value = "";
});

function updateLock() {
  const on = $("unlock").checked;
  $("repo-url").disabled = !on || !!pending;
  $("b-prepare").disabled = !on || !!pending;
}
$("unlock").onchange = () => { if (!$("unlock").checked) cancel(); updateLock(); };

function cancel() {
  pending = null;
  $("confirm-box").hidden = true;
  $("confirm-text").value = "";
  updateLock();
}
$("b-cancel").onclick = cancel;

$("b-prepare").onclick = () => run(async () => {
  const res = await api("/api/git/connect/prepare", { method: "POST", json: { url: $("repo-url").value } });
  pending = res;
  let msg = `This will replace the ${res.local_files} log file(s) currently in your logs folder with the contents of ${res.url}. `
    + "The current folder is kept as a backup (logs.old-...), not deleted.";
  if (res.unsaved) msg += " WARNING: you have changes that have not been pushed. They are only kept in that backup folder.";
  msg += `\nType "${res.confirm_text}" to confirm:`;
  $("confirm-msg").textContent = msg;
  $("confirm-msg").style.whiteSpace = "pre-line";
  $("confirm-box").hidden = false;
  $("confirm-text").value = "";
  say("Repository reachable. Confirm to continue.");
});

$("b-connect").onclick = () => run(async () => {
  if (!pending) return;
  const res = await api("/api/git/connect", { method: "POST",
    json: { url: pending.url, token: pending.token, confirm: $("confirm-text").value } });
  cancel();
  $("unlock").checked = false;
  done(res);
});

updateLock();
run(async () => { await refresh(false); say(""); });
