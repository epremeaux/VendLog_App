"""VendLog entry point: python app.py"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from vendlog import config, dialog, preview, version
from vendlog import search as search_mod
from vendlog.gitops import AppRepo, Git
from vendlog.store import IMAGE_TYPES, Store, StoreError, norm_tags

MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def _log_auto(res: dict, auto_pull: bool) -> None:
    stamp = time.strftime("%H:%M:%S")
    if not res.get("connected"):
        print(f"[{stamp}] GitHub check: no data repository connected", flush=True)
    elif res.get("offline"):
        print(f"[{stamp}] GitHub check: could not reach GitHub (offline?)", flush=True)
    else:
        extra = f"; auto-pulled {res['pulled']} commit(s)" if res.get("pulled") else ""
        print(f"[{stamp}] GitHub check OK: behind {res.get('behind')}, ahead {res.get('ahead')}, "
              f"{len(res.get('changes', []))} local change(s){extra}"
              f"{' (auto-pull on)' if auto_pull else ''}", flush=True)
    if res.get("message") and not res.get("pulled"):
        print(f"[{stamp}] {res['message']}", flush=True)


def create_app(cfg: dict | None = None) -> Flask:
    cfg = cfg or config.load()
    app = Flask(__name__)
    app.config["VENDLOG"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    store = Store(Path(cfg["repo_path"]) / cfg["logs_dir"])
    app.extensions["store"] = store
    git = Git(store.root, allow_local=bool(cfg.get("allow_local_remotes")),
              before_push=lambda: version.stamp(store.root))
    app.extensions["git"] = git
    app_repo = AppRepo(Path(cfg.get("app_dir") or config.APP_DIR), allow_local=True)

    @app.errorhandler(StoreError)
    def store_error(e):
        return jsonify(error=str(e)), e.status

    @app.errorhandler(413)
    def too_large(e):
        return jsonify(error="Upload too large"), 413

    @app.before_request
    def require_header():
        # A custom header can't be sent cross-site without a CORS preflight.
        if request.method not in ("GET", "HEAD") and request.headers.get("X-VendLog") != "1":
            return jsonify(error="Missing X-VendLog header"), 403

    @app.get("/")
    def home():
        return render_template("home.html", page="home")

    @app.get("/manage")
    def manage():
        return render_template("manage.html", page="manage")

    @app.get("/search")
    def search_page():
        return render_template("search.html", page="search")

    @app.get("/sync")
    def sync_page():
        return render_template("sync.html", page="sync", default_url=cfg.get("data_repo_url", ""))

    @app.get("/api/version")
    def api_version():
        return jsonify(version.data_info(store.root))

    @app.post("/api/app/check")
    def app_check():
        return jsonify(app_repo.check())

    @app.post("/api/app/update")
    def app_update():
        return jsonify(app_repo.update())

    auto = {"at": 0.0, "pulled_once": False, "last": None}

    @app.post("/api/git/auto")
    def git_auto():
        """Quiet check used on every page load. Fetches at most once a minute; auto-pull (opt-in) only once per run."""
        if auto["last"] is None or time.monotonic() - auto["at"] > 60:
            want_pull = bool(cfg.get("auto_pull_on_start")) and not auto["pulled_once"]
            auto["pulled_once"] = True
            res = git.auto_sync(want_pull)
            try:
                res["app"] = app_repo.check()
            except Exception:
                res["app"] = None
            auto["last"], auto["at"] = res, time.monotonic()
            _log_auto(res, want_pull)
            return jsonify(res)
        # Between fetches, only the local part is refreshed so unpushed work shows up straight away.
        last = auto["last"]
        if last.get("connected"):
            try:
                last = {**last, **git.status()}
            except Exception:
                pass
        return jsonify(last)

    @app.get("/api/git/status")
    def git_status():
        return jsonify(git.status())

    @app.post("/api/git/fetch")
    def git_fetch():
        return jsonify(git.fetch())

    @app.post("/api/git/pull")
    def git_pull():
        return jsonify(git.pull())

    @app.post("/api/git/push")
    def git_push():
        return jsonify(git.push((request.get_json(silent=True) or {}).get("message", "")))

    @app.post("/api/git/connect/prepare")
    def git_prepare():
        url = (request.get_json(silent=True) or {}).get("url", "")
        return jsonify(git.prepare_connect(url, len(store.list_logs())))

    @app.post("/api/git/connect")
    def git_connect():
        d = request.get_json(silent=True) or {}
        return jsonify(git.connect(d.get("url", ""), d.get("token", ""), d.get("confirm", "")))

    @app.get("/api/search")
    def api_search():
        return jsonify(search_mod.search(
            store, request.args.get("q", ""), request.args.getlist("tag"),
            request.args.get("mode", "and"), request.args.get("content") == "1",
            request.args.get("session") == "1", request.args.get("datekind", "upload"),
            request.args.get("from", ""), request.args.get("to", ""), request.args.get("folder", "")))

    def _preview_path():
        rel = request.args.get("path", "")
        return store.resolve(store.get(rel)["path"])

    @app.get("/api/settings")
    def get_settings():
        return jsonify(external_editor=cfg.get("external_editor", ""))

    @app.post("/api/settings")
    def set_settings():
        d = request.get_json(silent=True) or {}
        path = str(d.get("external_editor", "")).strip().strip('"')
        if path:
            if "\n" in path or "\0" in path:
                raise StoreError("Invalid path")
            p = Path(path).expanduser()
            if not p.exists():
                raise StoreError("That program was not found: " + path)
            if p.is_dir() and not (p.suffix == ".app" and sys.platform == "darwin"):
                raise StoreError("Enter the path of the program itself (for example notepad++.exe), not a folder")
        config.save_key("external_editor", path, Path(cfg.get("config_file") or config.CONFIG_FILE))
        cfg["external_editor"] = path
        return jsonify(external_editor=path)

    @app.post("/api/browse-program")
    def browse_program():
        return jsonify(path=dialog.pick_program())

    @app.post("/api/open-external")
    def open_external():
        """Open a log in the configured editor. Only the program path and the file path are passed; no shell."""
        exe = cfg.get("external_editor", "")
        if not exe:
            raise StoreError("No external editor is configured. Set one on the Home page.")
        p = store.resolve(store.get((request.get_json(silent=True) or {}).get("path", ""))["path"])
        cmd = ["open", "-a", exe, str(p)] if exe.endswith(".app") else [exe, str(p)]
        try:
            subprocess.Popen(cmd, close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise StoreError(f"Could not start the editor: {e}")
        print(f"Opened {p.name} in {exe}", flush=True)
        return jsonify(ok=True)

    @app.get("/api/image")
    def api_image():
        p = _preview_path()
        mime = IMAGE_TYPES.get(p.suffix.lower())
        if not mime:
            raise StoreError("Not an image file")
        resp = send_file(p, mimetype=mime, conditional=True, max_age=0)
        # SVG can carry scripts; sandbox it and stop type sniffing for every image.
        resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.get("/api/pdf")
    def api_pdf():
        p = _preview_path()
        if p.suffix.lower() != ".pdf":
            raise StoreError("Not a PDF file")
        resp = send_file(p, mimetype="application/pdf", conditional=True, max_age=0)
        resp.headers["Content-Disposition"] = "inline"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.get("/api/preview")
    def api_preview():
        p = _preview_path()
        if p.suffix.lower() in IMAGE_TYPES:
            raise StoreError("This is an image; use /api/image")
        if p.suffix.lower() == ".pdf":
            raise StoreError("This is a PDF; use /api/pdf")
        try:
            offset, limit = int(request.args.get("offset", 0)), int(request.args.get("limit", 500))
        except ValueError:
            raise StoreError("offset and limit must be integers")
        return jsonify(preview.window(p, offset, limit))

    @app.get("/api/preview/find")
    def api_preview_find():
        return jsonify(preview.find(_preview_path(), request.args.get("q", "")))

    @app.get("/api/files")
    def files():
        return jsonify(files=store.list_logs(), session_start=store.session_start)

    @app.post("/api/files")
    def upload():
        saved, errors = [], []
        prefix = request.form.get("prefix", "")
        machine = request.form.get("machine", "")
        try:
            up_tags = json.loads(request.form.get("tags") or "[]")
            norm_tags(up_tags)
        except (ValueError, StoreError):
            raise StoreError("Invalid tags")
        for f in request.files.getlist("files"):
            try:
                saved.append(store.save_upload(f.filename, f.stream, prefix, machine, up_tags,
                                               request.form.get("captured", ""),
                                               request.form.get("maker", "")))
            except StoreError as e:
                errors.append({"name": f.filename, "error": str(e)})
        if not saved and not errors:
            raise StoreError("No files provided")
        return jsonify(saved=saved, errors=errors)

    @app.get("/api/files/<path:rel>")
    def file_info(rel):
        return jsonify(store.get(rel))

    @app.put("/api/files/<path:rel>")
    def file_update(rel):
        return jsonify(store.update_meta(rel, request.get_json(silent=True) or {}))

    @app.post("/api/bulk-tags")
    def bulk_tags():
        d = request.get_json(silent=True) or {}
        files = store.bulk_tags(d.get("paths"), d.get("add"), d.get("remove"))
        return jsonify(files=files, tags=store.all_tags())

    @app.post("/api/bulk-move")
    def bulk_move():
        d = request.get_json(silent=True) or {}
        files = store.bulk_move(d.get("paths"), d.get("machine"))
        return jsonify(files=files)

    @app.post("/api/rename")
    def file_rename():
        d = request.get_json(silent=True) or {}
        if not isinstance(d.get("path"), str) or not isinstance(d.get("name"), str):
            raise StoreError("path and name are required")
        return jsonify(store.rename(d["path"], d["name"]))

    @app.delete("/api/files/<path:rel>")
    def file_delete(rel):
        store.delete(rel)
        return jsonify(ok=True)

    @app.get("/api/makers")
    def makers():
        return jsonify(makers=store.makers())

    @app.get("/api/tags")
    def tags():
        return jsonify(tags=store.all_tags())

    @app.post("/api/tags")
    def tag_add():
        store.add_tag((request.get_json(silent=True) or {}).get("name"))
        return jsonify(tags=store.all_tags())

    @app.post("/api/tags/rename")
    def tag_rename():
        d = request.get_json(silent=True) or {}
        n = store.rename_tag(d.get("old"), d.get("new"))
        return jsonify(changed=n, tags=store.all_tags())

    @app.post("/api/tags/delete")
    def tag_delete():
        n = store.delete_tag((request.get_json(silent=True) or {}).get("name"))
        return jsonify(changed=n, tags=store.all_tags())

    return app


def _already_running(host: str, port: int) -> str:
    """'vendlog' if our server answers on host:port, 'other' if something else holds the port, '' if free."""
    import socket
    import urllib.request
    s = socket.socket()
    s.settimeout(1)
    try:
        if s.connect_ex((host, port)) != 0:
            return ""
    finally:
        s.close()
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/version", timeout=2) as r:
            return "vendlog" if r.status == 200 else "other"
    except Exception:
        return "other"


def main(argv: list[str]) -> int:
    import threading
    import webbrowser
    cfg = config.load()
    host, port = cfg["host"], cfg["port"]
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    open_browser = "--no-browser" not in argv
    state = _already_running(host, port)
    if state == "vendlog":
        print(f"VendLog is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return 0
    if state == "other":
        print(f"Port {port} is in use by another program. Change \"port\" in vendlog.config.json and try again.")
        return 1
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"VendLog {version.__version__} running at {url}  (close this window or press Ctrl+C to stop)")
    create_app(cfg).run(host=host, port=port)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
