"""VendLog entry point: python app.py"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from vendlog import config, preview
from vendlog import search as search_mod
from vendlog.gitops import Git
from vendlog.store import Store, StoreError, norm_tags

MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def create_app(cfg: dict | None = None) -> Flask:
    cfg = cfg or config.load()
    app = Flask(__name__)
    app.config["VENDLOG"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    store = Store(Path(cfg["repo_path"]) / cfg["logs_dir"])
    app.extensions["store"] = store
    git = Git(store.root, allow_local=bool(cfg.get("allow_local_remotes")))
    app.extensions["git"] = git

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
            request.args.get("mode", "and"), request.args.get("content") == "1"))

    def _preview_path():
        rel = request.args.get("path", "")
        return store.resolve(store.get(rel)["path"])

    @app.get("/api/preview")
    def api_preview():
        p = _preview_path()
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
        return jsonify(files=store.list_logs())

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


if __name__ == "__main__":
    cfg = config.load()
    create_app(cfg).run(host=cfg["host"], port=cfg["port"])
