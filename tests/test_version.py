import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as app_module  # noqa: E402
from app import create_app  # noqa: E402
from vendlog import DATA_FORMAT, __version__, version  # noqa: E402
from vendlog.store import StoreError  # noqa: E402
from test_git import clone, git, seed  # noqa: E402

H = {"X-VendLog": "1"}


def test_parse_and_newer():
    assert version.parse("v1.2.3") == (1, 2, 3) and version.parse("x") is None
    assert version.newer("0.10.0", "0.9.9") and not version.newer("0.1.0", "0.1.0")
    assert not version.newer("garbage", "0.1.0") and not version.newer("1.0", "")


def test_stamp_and_data_info(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    assert version.data_info(logs)["has_manifest"] is False
    assert version.stamp(logs) is True and version.stamp(logs) is False
    m = json.loads((logs / "vendlog.json").read_text())
    assert m == {"data_format": DATA_FORMAT, "app_version": __version__}
    info = version.data_info(logs)
    assert not info["app_too_old"] and not info["format_unsupported"]

    # data written by a newer app: flagged, and never downgraded by us
    (logs / "vendlog.json").write_text(json.dumps({"data_format": DATA_FORMAT, "app_version": "99.0.0"}))
    assert version.data_info(logs)["app_too_old"] is True
    version.stamp(logs)
    assert json.loads((logs / "vendlog.json").read_text())["app_version"] == "99.0.0"

    # a newer data format blocks stamping (and so pushing)
    (logs / "vendlog.json").write_text(json.dumps({"data_format": DATA_FORMAT + 1, "app_version": "99.0.0"}))
    assert version.data_info(logs)["format_unsupported"] is True
    with pytest.raises(StoreError):
        version.stamp(logs)


def test_manifest_is_not_a_log(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").write_text("x")
    version.stamp(logs)
    c = create_app({"repo_path": str(tmp_path), "logs_dir": "logs", "data_repo_url": "", "app_dir": str(tmp_path)}).test_client()
    assert [f["path"] for f in c.get("/api/files").json["files"]] == ["a.log"]
    assert c.delete("/api/files/vendlog.json", headers=H).status_code == 404
    assert c.get("/api/version").json["app_version"] == __version__


def test_push_stamps_manifest(tmp_path):
    bare = seed(tmp_path, "data", {"a.log": "one\n"})
    logs = clone(tmp_path, bare, "logs")
    c = create_app({"repo_path": str(tmp_path), "logs_dir": "logs", "data_repo_url": "",
                    "allow_local_remotes": True, "app_dir": str(tmp_path)}).test_client()
    r = c.post("/api/git/push", headers=H, json={"message": "stamp"})
    assert r.status_code == 200, r.json
    shown = subprocess.run(["git", "--git-dir", str(bare), "show", "main:vendlog.json"],
                           capture_output=True, text=True).stdout
    assert json.loads(shown)["app_version"] == __version__


def make_app_repo(tmp_path, ver="0.1.0"):
    bare = seed(tmp_path, "app", {"keep.txt": "k"})
    work = tmp_path / "app_seed"
    (work / "vendlog").mkdir()
    (work / "vendlog" / "__init__.py").write_text(f'__version__ = "{ver}"\n')
    git(work, "add", "-A"); git(work, "commit", "-m", "v"); git(work, "push", "origin", "main")
    return bare, clone(tmp_path, bare, "appcopy"), work


def app_client(tmp_path, app_dir):
    return create_app({"repo_path": str(tmp_path), "logs_dir": "logs", "data_repo_url": "",
                       "app_dir": str(app_dir)}).test_client()


def test_app_update_flow(tmp_path):
    bare, appcopy, work = make_app_repo(tmp_path)
    c = app_client(tmp_path, appcopy)
    r = c.post("/api/app/check", headers=H, json={}).json
    assert r["connected"] and not r.get("update_available") and r["message"] == "The app is up to date."

    (work / "vendlog" / "__init__.py").write_text('__version__ = "0.9.0"\n')
    (work / "requirements.txt").write_text("Flask\n")
    git(work, "add", "-A"); git(work, "commit", "-m", "v2"); git(work, "push", "origin", "main")
    r = c.post("/api/app/check", headers=H, json={}).json
    assert r["update_available"] and r["latest_version"] == "0.9.0" and r["can_update"] and r["behind"] == 1

    r = c.post("/api/app/update", headers=H, json={}).json
    assert r["restart"] and "pip install" in r["message"]
    assert '0.9.0' in (appcopy / "vendlog" / "__init__.py").read_text()
    # the running process still has the old code loaded until it is restarted
    r = c.post("/api/app/check", headers=H, json={}).json
    assert r["restart_needed"] and not r["can_update"] and "start it again" in r["message"]


def test_app_update_refuses_local_changes(tmp_path):
    bare, appcopy, work = make_app_repo(tmp_path)
    (work / "vendlog" / "__init__.py").write_text('__version__ = "0.9.0"\n')
    git(work, "commit", "-am", "v2"); git(work, "push", "origin", "main")
    (appcopy / "keep.txt").write_text("edited locally")
    c = app_client(tmp_path, appcopy)
    r = c.post("/api/app/check", headers=H, json={}).json
    assert r["update_available"] and r["dirty"] and not r["can_update"]
    assert c.post("/api/app/update", headers=H, json={}).status_code == 409
    assert (appcopy / "keep.txt").read_text() == "edited locally"


def test_app_not_a_checkout(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    c = app_client(tmp_path, plain)
    r = c.post("/api/app/check", headers=H, json={}).json
    assert r["connected"] is False and "not a Git checkout" in r["message"]


def test_launcher_port_detection(tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        assert app_module._already_running("127.0.0.1", port) == "other"  # not VendLog
    finally:
        s.close()
    assert app_module._already_running("127.0.0.1", port) == ""


def _auto_client(tmp_path, pull):
    bare = seed(tmp_path, "d", {"a.log": "x\n"})
    mine = clone(tmp_path, bare, "mine")
    other = clone(tmp_path, bare, "other")
    cfg = {"repo_path": str(tmp_path), "logs_dir": "mine", "allow_local_remotes": True,
           "app_dir": str(tmp_path / "noapp"), "auto_pull_on_start": pull}
    return create_app(cfg).test_client(), mine, other


def _push_other(other, name):
    (other / name).write_text("y\n")
    git(other, "add", "-A")
    git(other, "commit", "-m", "more")
    git(other, "push")


def test_auto_check_reports_without_changing_files(tmp_path):
    c, mine, other = _auto_client(tmp_path, pull=False)
    _push_other(other, "b.log")
    st = c.post("/api/git/auto", headers=H).get_json()
    assert st["behind"] == 1 and st["pulled"] == 0 and not (mine / "b.log").exists()


def test_auto_pull_fast_forwards_when_clean(tmp_path):
    c, mine, other = _auto_client(tmp_path, pull=True)
    _push_other(other, "b.log")
    st = c.post("/api/git/auto", headers=H).get_json()
    assert st["pulled"] == 1 and (mine / "b.log").exists() and st["behind"] == 0


def test_auto_pull_skipped_with_local_changes_and_local_banner_data(tmp_path):
    c, mine, other = _auto_client(tmp_path, pull=True)
    (mine / "new.log").write_text("z\n")
    _push_other(other, "b.log")
    st = c.post("/api/git/auto", headers=H).get_json()
    assert st["pulled"] == 0 and st["behind"] == 1 and not (mine / "b.log").exists()
    assert [x["path"] for x in st["changes"]] == ["new.log"]
    # between fetches the local part is still fresh
    (mine / "new2.log").write_text("q\n")
    st = c.post("/api/git/auto", headers=H).get_json()
    assert len(st["changes"]) == 2


def test_auto_offline_is_quiet(tmp_path):
    c, mine, other = _auto_client(tmp_path, pull=True)
    git(mine, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    st = c.post("/api/git/auto", headers=H).get_json()
    assert st["connected"] and st["offline"] is True
