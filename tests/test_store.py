import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import create_app  # noqa: E402
from vendlog.store import Store, StoreError  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").write_text("hello\n")
    (logs / "a.log.meta.json").write_text(json.dumps({"tags": ["JVMA"], "machine": "M1"}))
    (logs / "sub").mkdir()
    (logs / "sub" / "b.log").write_text("x")
    (logs / "tags.json").write_text("[]")
    (logs / "bad.log").write_text("x")
    (logs / "bad.log.meta.json").write_text("{not json")
    return tmp_path


def test_list_excludes_sidecars_and_vocab(repo):
    names = [f["path"] for f in Store(repo / "logs").list_logs()]
    assert names == ["a.log", "bad.log", "sub/b.log"]


def test_metadata_and_defaults(repo):
    s = Store(repo / "logs")
    assert s.get("a.log")["tags"] == ["JVMA"]
    assert s.get("sub/b.log")["tags"] == []
    assert "error" in s.get("bad.log")


def test_path_traversal_blocked(repo):
    s = Store(repo / "logs")
    for bad in ["../x", "..\\x", "sub/../../x"]:
        with pytest.raises(StoreError):
            s.get(bad)


def test_api(repo):
    app = create_app({"repo_path": str(repo), "logs_dir": "logs"})
    c = app.test_client()
    assert len(c.get("/api/files").json["files"]) == 3
    assert c.get("/api/files/a.log").json["machine"] == "M1"
    assert c.get("/api/files/..%2Fx").status_code in (400, 404)
    assert c.get("/").status_code == 200


H = {"X-VendLog": "1"}


@pytest.fixture
def client(repo):
    return create_app({"repo_path": str(repo), "logs_dir": "logs"}).test_client()


def test_writes_require_header(client):
    assert client.delete("/api/files/a.log").status_code == 403
    assert client.post("/api/tags", json={"name": "x"}).status_code == 403


def test_upload_and_no_overwrite(client, repo):
    from io import BytesIO
    r = client.post("/api/files", data={"files": (BytesIO(b"data"), "new.log")}, headers=H)
    assert r.json["saved"][0]["path"] == "new.log"
    r = client.post("/api/files", data={"files": (BytesIO(b"other"), "new.log")}, headers=H)
    assert r.json["saved"] == [] and "already exists" in r.json["errors"][0]["error"]
    assert (repo / "logs" / "new.log").read_bytes() == b"data"
    r = client.post("/api/files", data={"files": (BytesIO(b"x"), "tags.json")}, headers=H)
    assert r.json["errors"]
    r = client.post("/api/files", data={"files": (BytesIO(b"x"), "../../evil.log")}, headers=H)
    assert r.json["saved"][0]["path"] == "evil.log"
    assert not (repo / "evil.log").exists()


def test_rename(client, repo):
    r = client.post("/api/rename", json={"path": "a.log", "name": "RED-01_a.log"}, headers=H)
    assert r.json["path"] == "RED-01_a.log" and r.json["tags"] == ["JVMA"]
    assert not (repo / "logs" / "a.log").exists()
    assert not (repo / "logs" / "a.log.meta.json").exists()
    assert (repo / "logs" / "RED-01_a.log.meta.json").exists()
    # collision with an existing file, rename to a bad name, missing source
    assert client.post("/api/rename", json={"path": "RED-01_a.log", "name": "bad.log"}, headers=H).status_code == 409
    assert client.post("/api/rename", json={"path": "RED-01_a.log", "name": "tags.json"}, headers=H).status_code == 400
    assert client.post("/api/rename", json={"path": "nope.log", "name": "x.log"}, headers=H).status_code == 404
    # directory is preserved; path separators in the new name are stripped
    r = client.post("/api/rename", json={"path": "sub/b.log", "name": "../evil.log"}, headers=H)
    assert r.json["path"] == "sub/evil.log"
    # case-only rename
    r = client.post("/api/rename", json={"path": "bad.log", "name": "BAD.log"}, headers=H)
    assert r.json["name"] == "BAD.log"


def test_upload_prefix_and_machine(client, repo):
    from io import BytesIO
    r = client.post("/api/files", headers=H, data={
        "prefix": "RED-01", "machine": "RED-01",
        "files": [(BytesIO(b"1"), "run.log"), (BytesIO(b"2"), "other.log")]})
    assert [f["path"] for f in r.json["saved"]] == ["RED-01/RED-01_run.log", "RED-01/RED-01_other.log"]
    assert r.json["saved"][0]["machine"] == "RED-01"
    # the same original name from another machine no longer collides
    r = client.post("/api/files", headers=H, data={"prefix": "BLUE-02", "machine": "BLUE-02",
                                                    "files": (BytesIO(b"3"), "run.log")})
    assert r.json["saved"][0]["path"] == "BLUE-02/BLUE-02_run.log"
    # same prefix + name is still refused
    r = client.post("/api/files", headers=H, data={"prefix": "RED-01", "machine": "red-01",
                                                    "files": (BytesIO(b"4"), "run.log")})
    assert r.json["errors"] and (repo / "logs" / "RED-01" / "RED-01_run.log").read_bytes() == b"1"
    # no machine: stays in the logs root
    r = client.post("/api/files", headers=H, data={"prefix": "../x", "files": (BytesIO(b"5"), "z.log")})
    assert r.json["saved"][0]["path"] == "_x_z.log"
    assert not (repo / "x_z.log").exists()
    # a machine name cannot smuggle a path
    r = client.post("/api/files", headers=H, data={"machine": "../evil", "files": (BytesIO(b"6"), "q.log")})
    assert r.json["saved"][0]["path"] == "evil/q.log"
    assert not (repo / "evil").exists()
    # machine name that is a reserved file is refused, and a file blocking the folder name is a conflict
    r = client.post("/api/files", headers=H, data={"machine": "tags.json", "files": (BytesIO(b"7"), "q.log")})
    assert r.json["errors"]
    r = client.post("/api/files", headers=H, data={"machine": "a.log", "files": (BytesIO(b"8"), "q.log")})
    assert r.json["errors"] and (repo / "logs" / "a.log").is_file()


def test_upload_with_tags(client):
    from io import BytesIO
    r = client.post("/api/files", headers=H, data={
        "tags": json.dumps(["JVMA", "jvma", "Door open"]), "files": (BytesIO(b"1"), "t.log")})
    assert r.json["saved"][0]["tags"] == ["Door open", "JVMA"]
    r = client.post("/api/files", headers=H, data={"tags": "not json", "files": (BytesIO(b"1"), "u.log")})
    assert r.status_code == 400


def test_upload_capture_date(client):
    from io import BytesIO
    r = client.post("/api/files", headers=H, data={"captured": "2026-03-01", "files": (BytesIO(b"1"), "d.log")})
    assert r.json["saved"][0]["datetime"] == "2026-03-01"
    r = client.post("/api/files", headers=H, data={"captured": "yesterday", "files": (BytesIO(b"1"), "e.log")})
    assert r.json["errors"] and not r.json["saved"]


def test_bulk_tags(client):
    r = client.post("/api/bulk-tags", headers=H, json={
        "paths": ["a.log", "sub/b.log"], "add": ["Door open"], "remove": ["jvma"]})
    by = {f["path"]: f["tags"] for f in r.json["files"]}
    assert by == {"a.log": ["Door open"], "sub/b.log": ["Door open"]}
    # one bad path means nothing is changed
    before = client.get("/api/files/a.log").json["tags"]
    r = client.post("/api/bulk-tags", headers=H, json={"paths": ["a.log", "missing.log"], "add": ["X"]})
    assert r.status_code == 404
    assert client.get("/api/files/a.log").json["tags"] == before
    assert client.post("/api/bulk-tags", headers=H, json={"paths": [], "add": ["X"]}).status_code == 400
    assert client.post("/api/bulk-tags", headers=H, json={"paths": ["a.log"]}).status_code == 400


def test_delete(client, repo):
    assert client.delete("/api/files/a.log", headers=H).status_code == 200
    assert not (repo / "logs" / "a.log").exists()
    assert not (repo / "logs" / "a.log.meta.json").exists()
    assert client.delete("/api/files/a.log", headers=H).status_code == 404
    assert client.delete("/api/files/tags.json", headers=H).status_code == 404


def test_update_meta(client, repo):
    r = client.put("/api/files/sub/b.log", json={"tags": ["Door open", "jvma", "JVMA", " Coin  inserted "],
                                                  "machine": " M2 "}, headers=H)
    assert r.json["tags"] == ["Coin inserted", "Door open", "jvma"]
    assert r.json["machine"] == "M2" and r.json["path"] == "M2/b.log"
    saved = json.loads((repo / "logs" / "M2" / "b.log.meta.json").read_text())
    assert list(saved) == ["tags", "machine", "maker", "datetime", "description"]
    assert client.put("/api/files/a.log", json={"tags": "x"}, headers=H).status_code == 400
    assert client.put("/api/files/a.log", json={"tags": [""]}, headers=H).status_code == 400


def test_tag_rename_delete(client, repo):
    client.put("/api/files/sub/b.log", json={"tags": ["JVMA", "Door open"]}, headers=H)
    client.post("/api/tags", json={"name": "Unused"}, headers=H)
    tags = {t["name"]: t["count"] for t in client.get("/api/tags").json["tags"]}
    assert tags == {"JVMA": 2, "Door open": 1, "Unused": 0}
    r = client.post("/api/tags/rename", json={"old": "jvma", "new": "JVMA-2"}, headers=H)
    assert r.json["changed"] == 2
    assert client.get("/api/files/a.log").json["tags"] == ["JVMA-2"]
    client.post("/api/tags/rename", json={"old": "Door open", "new": "JVMA-2"}, headers=H)
    assert client.get("/api/files/sub/b.log").json["tags"] == ["JVMA-2"]
    r = client.post("/api/tags/delete", json={"name": "JVMA-2"}, headers=H)
    assert r.json["changed"] == 2
    assert client.get("/api/files/a.log").json["tags"] == []
    assert [t["name"] for t in r.json["tags"]] == ["Unused"]
    assert client.post("/api/tags/delete", json={"name": "nope"}, headers=H).status_code == 404
