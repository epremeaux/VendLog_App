"use strict";

const $ = id => document.getElementById(id);
let pending = null;
let busy = false;

async function syncRequest(action, data = {}) {
  const response = await fetch("/api/sync/run", {
    method: "POST",
    headers: { "X-VendLog": "1", "Content-Type": "application/json" },
    body: JSON.stringify({ ...data, action })
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || response.statusText);
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read().catch(() => {
        throw new Error("Lost the sync progress connection. The operation may still be running; check repository status.");
      });
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.progress) {
          const p = event.progress;
          if (p.total > 0) {
            $("sync-progress-bar").max = p.total;
            $("sync-progress-bar").value = p.current;
            $("sync-progress-detail").textContent = `${p.phase}: ${p.current} / ${p.total} (current Git phase)`;
          }
        }
        if (event.error) throw new Error(event.error);
        if (event.result) return event.result;
      }
      if (done) throw new Error("Lost the sync progress connection. The operation may still be running; check repository status.");
    }
  } finally {
    reader.releaseLock();
  }
}

function say(text, isError) {
  const m = $("msg");
  m.textContent = text || "";
  m.className = isError ? "error" : "muted";
}

async function run(label, fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("button").forEach(b => b.disabled = true);
  say("");
  $("sync-progress-label").textContent = label;
  $("sync-progress-bar").removeAttribute("value");
  $("sync-progress-detail").textContent = "Waiting for Git progress...";
  $("sync-progress").hidden = false;
  try { await fn(); }
  catch (e) { say(e.message, true); await refresh(false).catch(() => {}); }
  finally {
    $("sync-progress").hidden = true;
    busy = false;
    document.querySelectorAll("button").forEach(b => b.disabled = false);
    updateLock();
  }
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
  const st = fetchRemote ? await syncRequest("fetch") : await api("/api/git/status");
  render(st);
  if (st.connected && st.remote && !$("repo-url").value) $("repo-url").value = st.remote;
  return st;
}

function done(res) {
  render(res);
  say(res.message || "Done.");
  loadVersions().catch(() => {});
  if (typeof updateBanners === "function") updateBanners();
}

async function loadVersions() {
  const v = await api("/api/version");
  const dl = $("ver-body");
  dl.replaceChildren();
  const rows = [["App version", v.app_version]];
  rows.push(["Data version", v.has_manifest
    ? `written by app ${v.data_app_version || "?"} (data format ${v.data_format ?? "?"})`
    : "no version recorded yet (it is added on your next push)"]);
  if (appLatest) rows.push(["Latest app on GitHub", appLatest]);
  rows.forEach(([k, t]) => dl.append(el("dt", k), el("dd", t)));
  const w = $("ver-warn");
  w.hidden = !(v.app_too_old || v.format_unsupported);
  w.textContent = v.format_unsupported
    ? `This data uses format ${v.data_format}, which this app (version ${v.app_version}) cannot read safely. Update the app before changing anything.`
    : v.app_too_old
      ? `The data was last written by a newer app (${v.data_app_version}); you are running ${v.app_version}. Update the app.`
      : "";
  return v;
}

let appLatest = "";

$("b-check").onclick = () => run("Checking for app updates...", async () => {
  const r = await syncRequest("app-check");
  appLatest = r.latest_version || "";
  $("b-update").hidden = !r.can_update;
  $("ver-msg").textContent = r.message
    || `A newer app is available${r.latest_version ? " (" + r.latest_version + ")" : ""}. Click "Update app from GitHub".`;
  await loadVersions();
  say("");
});

$("b-update").onclick = () => run("Updating the app...", async () => {
  if (!confirm("Download the latest app code from GitHub now?\n\nYour logs are not affected. You will need to restart VendLog afterwards.")) return;
  const r = await syncRequest("app-update");
  $("b-update").hidden = true;
  $("ver-msg").textContent = r.message;
  $("ver-msg").className = r.restart ? "warn" : "muted";
  say("");
});

$("b-refresh").onclick = () => run("Checking GitHub for changes...", async () => { await refresh(true); say("Checked GitHub. Status updated; no files were changed."); });
$("b-pull").onclick = () => run("Pulling logs and metadata...", async () => done(await syncRequest("pull")));
$("b-push").onclick = () => run("Committing and pushing logs...", async () => {
  done(await syncRequest("push", { message: $("commit-msg").value }));
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

$("b-prepare").onclick = () => run("Checking the data repository...", async () => {
  const res = await syncRequest("prepare", { url: $("repo-url").value });
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

$("b-connect").onclick = () => run("Replacing local data...", async () => {
  if (!pending) return;
  const res = await syncRequest("connect", { url: pending.url, token: pending.token, confirm: $("confirm-text").value });
  cancel();
  $("unlock").checked = false;
  done(res);
});

updateLock();
loadVersions().catch(() => {});
run("Checking GitHub for changes...", async () => { await refresh(true).catch(() => refresh(false)); say(""); });
