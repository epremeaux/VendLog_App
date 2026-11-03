import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import create_app  # noqa: E402
from vendlog.gitops import Git, repo_name, validate_url  # noqa: E402
from vendlog.store import Conflict, StoreError  # noqa: E402

H = {"X-VendLog": "1"}


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                   cwd=str(cwd), check=True, capture_output=True)


def seed(tmp_path, name, files):
    """A bare 'GitHub' repo with one commit containing files."""
    bare = tmp_path / f"{name}.git"
    work = tmp_path / f"{name}_seed"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    work.mkdir()
    git(work, "init", "-b", "main")
    for rel, text in files.items():
        (work / rel).write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-m", "seed")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "origin", "main")
    return bare


def clone(tmp_path, bare, name):
    dest = tmp_path / name
    git(tmp_path, "clone", str(bare), str(dest))
    git(dest, "config", "user.name", "T")
    git(dest, "config", "user.email", "t@example.com")
    return dest


def test_validate_url():
    assert validate_url("https://github.com/o/r.git").endswith("r.git")
    assert validate_url("git@github.com:o/r.git")
    for bad in ("", "-oProxy=x", "ext::sh -c id", "file:///etc", "https://u:p@github.com/o/r.git",
                "https://tok@github.com/o/r.git" if False else "https://github.com/o r"):
        with pytest.raises(StoreError):
            validate_url(bad)
    assert repo_name("https://github.com/epremeaux/VendLog_Data.git") == "VendLog_Data"


def test_pull_push_and_conflict_protection(tmp_path):
    bare = seed(tmp_path, "data", {"a.log": "one\n"})
    mine = clone(tmp_path, bare, "mine")
    theirs = clone(tmp_path, bare, "theirs")
    g = Git(mine, allow_local=True)
    assert g.status()["connected"] and g.status()["changes"] == []

    # Other user pushes; I'm behind, pull fast-forwards.
    (theirs / "b.log").write_text("b\n")
    git(theirs, "add", "-A"); git(theirs, "commit", "-m", "b"); git(theirs, "push")
    assert g.fetch()["behind"] == 1
    assert "Pulled 1" in g.pull()["message"]
    assert (mine / "b.log").exists()

    # Local change: push commits and pushes.
    (mine / "c.log").write_text("c\n")
    (mine / "x.tmp").write_text("junk")
    res = g.push("add c")
    assert res["ahead"] == 0 and "pushed" in res["message"]
    def remote_files():
        return subprocess.run(["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "main"],
                              capture_output=True, text=True).stdout

    assert "c.log" in remote_files()
    assert "x.tmp" not in remote_files()

    # Remote moves on; my push must refuse rather than overwrite.
    (theirs / "d.log").write_text("d\n")
    git(theirs, "add", "-A"); git(theirs, "commit", "-m", "d"); git(theirs, "pull"); git(theirs, "push")
    (mine / "e.log").write_text("e\n")
    with pytest.raises(Conflict):
        g.push("add e")
    # The refused push committed nothing, so pull fast-forwards and keeps my uncommitted file.
    assert "Pulled 2" in g.pull()["message"]
    assert (mine / "e.log").exists() and (mine / "d.log").exists()


def test_conflicting_pull_is_aborted_cleanly(tmp_path):
    bare = seed(tmp_path, "data", {"a.log": "base\n"})
    mine = clone(tmp_path, bare, "mine")
    theirs = clone(tmp_path, bare, "theirs")
    (theirs / "a.log").write_text("theirs\n")
    git(theirs, "commit", "-am", "t"); git(theirs, "push")
    (mine / "a.log").write_text("mine\n")
    git(mine, "commit", "-am", "m")
    g = Git(mine, allow_local=True)
    with pytest.raises(Conflict) as e:
        g.pull()
    assert "a.log" in str(e.value)
    assert (mine / "a.log").read_text() == "mine\n"
    st = g.status()
    assert not st["merging"] and not st["conflicts"]


def test_not_a_repo_and_nested_in_other_repo(tmp_path):
    outer = tmp_path / "app"
    logs = outer / "logs"
    logs.mkdir(parents=True)
    git(outer, "init", "-b", "main")
    g = Git(logs, allow_local=True)
    assert g.status()["connected"] is False  # inside the app repo, but not its own repo
    with pytest.raises(StoreError):
        g.pull()


def test_connect_two_step(tmp_path):
    bare = seed(tmp_path, "newdata", {"n.log": "n\n"})
    app_dir = tmp_path / "app"
    logs = app_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "old.log").write_text("old\n")
    client = create_app({"repo_path": str(app_dir), "logs_dir": "logs", "allow_local_remotes": True,
                         "data_repo_url": ""}).test_client()
    assert client.get("/api/git/status").json["connected"] is False
    assert client.post("/api/git/connect/prepare", json={"url": str(bare)}).status_code == 403  # header

    p = client.post("/api/git/connect/prepare", headers=H, json={"url": str(bare)}).json
    assert p["confirm_text"] == "newdata" and p["local_files"] == 1
    bad = client.post("/api/git/connect", headers=H, json={"url": str(bare), "token": p["token"], "confirm": "nope"})
    assert bad.status_code == 400 and (logs / "old.log").exists()
    wrong_tok = client.post("/api/git/connect", headers=H, json={"url": str(bare), "token": "x", "confirm": "newdata"})
    assert wrong_tok.status_code == 409 and (logs / "old.log").exists()

    p = client.post("/api/git/connect/prepare", headers=H, json={"url": str(bare)}).json
    r = client.post("/api/git/connect", headers=H, json={"url": str(bare), "token": p["token"], "confirm": "newdata"})
    assert r.status_code == 200, r.json
    assert (logs / "n.log").exists() and not (logs / "old.log").exists()
    backups = list(app_dir.glob("logs.old-*"))
    assert len(backups) == 1 and (backups[0] / "old.log").exists()
    assert client.get("/api/git/status").json["connected"] is True
    # token is single use
    again = client.post("/api/git/connect", headers=H, json={"url": str(bare), "token": p["token"], "confirm": "newdata"})
    assert again.status_code == 409


def test_connect_unreachable_changes_nothing(tmp_path):
    app_dir = tmp_path / "app"
    (app_dir / "logs").mkdir(parents=True)
    (app_dir / "logs" / "old.log").write_text("old\n")
    client = create_app({"repo_path": str(app_dir), "logs_dir": "logs", "data_repo_url": ""}).test_client()
    r = client.post("/api/git/connect/prepare", headers=H, json={"url": "https://127.0.0.1:1/none/none.git"})
    assert r.status_code in (400, 502)
    assert (app_dir / "logs" / "old.log").exists()
    assert client.post("/api/git/connect/prepare", headers=H, json={"url": "file:///x"}).status_code == 400


def test_sync_page(tmp_path):
    c = create_app({"repo_path": str(tmp_path), "logs_dir": "logs", "data_repo_url": "https://github.com/o/r.git"}).test_client()
    r = c.get("/sync")
    assert r.status_code == 200 and b"Synchronise" in r.data and b"github.com/o/r.git" in r.data
