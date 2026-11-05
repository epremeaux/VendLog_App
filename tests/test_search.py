import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import create_app  # noqa: E402
from vendlog import preview  # noqa: E402


def meta(tags, machine="", description=""):
    return json.dumps({"tags": tags, "machine": machine, "datetime": "", "description": description})


@pytest.fixture
def client(tmp_path):
    logs = tmp_path / "logs"
    (logs / "Cheerio").mkdir(parents=True)
    (logs / "Cheerio" / "run1.log").write_text("boot\nKeyboard Entry: 12\ncoin inserted\n", encoding="utf-8")
    (logs / "Cheerio" / "run1.log.meta.json").write_text(meta(["JVMA", "Keyboard entry"], "Cheerio"))
    (logs / "door.log").write_text("DOOR OPEN at 10:00\nnothing\n", encoding="utf-8")
    (logs / "door.log.meta.json").write_text(meta(["JVMA", "Door open"]))
    (logs / "plain.log").write_text("hello\n", encoding="utf-8")
    return create_app({"repo_path": str(tmp_path), "logs_dir": "logs"}).test_client()


def paths(client, **params):
    r = client.get("/api/search", query_string=params)
    assert r.status_code == 200
    return sorted(f["path"] for f in r.json["files"])


def test_pages(client):
    for url in ("/", "/manage", "/search"):
        r = client.get(url)
        assert r.status_code == 200 and b"VendLog" in r.data


def test_empty_query_lists_all(client):
    assert paths(client) == ["Cheerio/run1.log", "door.log", "plain.log"]


def test_and_or_tags_case_insensitive(client):
    assert paths(client, q="keyboard entry, jvma") == ["Cheerio/run1.log"]
    assert paths(client, q="keyboard entry, door open", mode="or") == ["Cheerio/run1.log", "door.log"]
    assert paths(client, q="keyboard entry, door open") == []
    assert paths(client, q="JVMA") == ["Cheerio/run1.log", "door.log"]


def test_filename_and_machine(client):
    assert paths(client, q="PLAIN") == ["plain.log"]
    assert paths(client, q="cheerio") == ["Cheerio/run1.log"]


def test_tag_filters_exact(client):
    assert paths(client, tag="JVMA") == ["Cheerio/run1.log", "door.log"]
    assert paths(client, tag=["JVMA", "door open"]) == ["door.log"]
    assert paths(client, tag=["door open", "keyboard entry"], mode="or") == ["Cheerio/run1.log", "door.log"]
    assert paths(client, tag="JVM") == []  # exact, not substring
    assert paths(client, q="plain", tag="JVMA") == []


def test_content_search(client):
    assert paths(client, q="coin inserted") == []
    r = client.get("/api/search", query_string={"q": "coin inserted", "content": "1"}).json
    assert [f["path"] for f in r["files"]] == ["Cheerio/run1.log"]
    assert r["files"][0]["hits"] == [{"line": 3, "text": "coin inserted"}]
    assert paths(client, q="door open", content="1") == ["door.log"]  # tag + text
    assert paths(client, q="hello, coin inserted", content="1") == []
    assert paths(client, q="hello, coin inserted", content="1", mode="or") == ["Cheerio/run1.log", "plain.log"]


def test_preview_window(tmp_path):
    p = tmp_path / "big.log"
    p.write_bytes(b"".join(b"line %d\r\n" % i for i in range(1, 2503)))
    d = preview.window(p, 0, 3)
    assert d["total"] == 2502 and d["lines"] == ["line 1", "line 2", "line 3"]
    assert preview.window(p, 1999, 3)["lines"] == ["line 2000", "line 2001", "line 2002"]
    assert preview.window(p, 2500, 50)["lines"] == ["line 2501", "line 2502"]
    assert preview.window(p, 9999, 5)["lines"] == []


def test_preview_edge_cases(tmp_path):
    empty = tmp_path / "e.log"
    empty.write_bytes(b"")
    assert preview.window(empty, 0, 10) == {"encoding": "utf-8", "total": 0, "offset": 0, "lines": []}
    no_nl = tmp_path / "n.log"
    no_nl.write_bytes(b"a\nb")
    assert preview.window(no_nl, 0, 10)["lines"] == ["a", "b"]
    bom = tmp_path / "b.log"
    bom.write_bytes(b"\xef\xbb\xbfhello\n")
    assert preview.window(bom, 0, 5)["lines"] == ["hello"]


def test_encodings(tmp_path):
    u16 = tmp_path / "u16.log"
    u16.write_bytes("first\r\nsecond \u00e9\r\nthird\r\n".encode("utf-16"))
    d = preview.window(u16, 1, 5)
    assert d["encoding"] == "utf-16" and d["total"] == 3 and d["lines"] == ["second \u00e9", "third"]
    cp = tmp_path / "cp.log"
    cp.write_bytes("caf\u00e9\n".encode("cp1252"))
    d = preview.window(cp, 0, 5)
    assert d["encoding"] == "cp1252" and d["lines"] == ["caf\u00e9"]
    assert preview.find(cp, "CAF\u00c9")["matches"] == [{"line": 1, "text": "caf\u00e9"}]


def test_find_matches_window_line_numbers(tmp_path):
    p = tmp_path / "f.log"
    p.write_bytes(b"a\rb\nneedle\r\nx\nNEEDLE again\n")  # lone \r is not a line break
    m = preview.find(p, "needle")["matches"]
    assert [x["line"] for x in m] == [2, 4]
    assert preview.window(p, 1, 1)["lines"] == ["needle"]


def test_preview_api(client):
    r = client.get("/api/preview", query_string={"path": "Cheerio/run1.log", "offset": 1, "limit": 1})
    assert r.json["lines"] == ["Keyboard Entry: 12"] and r.json["total"] == 3
    r = client.get("/api/preview/find", query_string={"path": "Cheerio/run1.log", "q": "keyboard"})
    assert r.json["matches"][0]["line"] == 2
    assert client.get("/api/preview", query_string={"path": "../app.py"}).status_code in (400, 404)
    assert client.get("/api/preview", query_string={"path": "logs/tags.json"}).status_code == 404
    assert client.get("/api/preview", query_string={"path": "door.log", "offset": "x"}).status_code == 400


def test_maker_field(client):
    H = {"X-VendLog": "1"}
    assert client.get("/api/makers").json["makers"] == ["Fuji Denki", "JVMA", "Panasonic", "Sanden", "UNKNOWN"]
    import io
    r = client.post("/api/files", headers=H, content_type="multipart/form-data",
                    data={"files": (io.BytesIO(b"x\n"), "m.log"), "maker": "  Acme   Corp "})
    assert r.json["saved"][0]["maker"] == "Acme Corp"
    assert "Acme Corp" in client.get("/api/makers").json["makers"]
    assert paths(client, q="acme") == ["m.log"]
    r = client.put("/api/files/m.log", headers=H, json={"maker": "Sanden"})
    assert r.json["maker"] == "Sanden" and r.json["tags"] == []
    assert client.get("/api/files").json["files"][0]["maker"] == ""


def test_machine_change_moves_file(client, tmp_path):
    H = {"X-VendLog": "1"}
    logs = tmp_path / "logs"
    r = client.put("/api/files/door.log", headers=H, json={"machine": "cheerio"})
    assert r.status_code == 200 and r.json["path"] == "Cheerio/door.log"  # reuses existing folder case
    assert not (logs / "door.log").exists() and not (logs / "door.log.meta.json").exists()
    assert (logs / "Cheerio" / "door.log.meta.json").exists() and r.json["machine"] == "cheerio"
    # already in the right folder: no move
    assert client.put("/api/files/Cheerio/door.log", headers=H, json={"machine": "Cheerio"}).json["path"] == "Cheerio/door.log"
    # new machine creates a folder; emptied old folder is removed
    r = client.put("/api/files/plain.log", headers=H, json={"machine": "New One"})
    assert r.json["path"] == "New One/plain.log"
    r = client.put("/api/files/New One/plain.log", headers=H, json={"machine": "Other"})
    assert r.json["path"] == "Other/plain.log" and not (logs / "New One").exists()
    # name clash: nothing changes
    (logs / "Other" / "run1.log").write_text("x")
    r = client.put("/api/files/Cheerio/run1.log", headers=H, json={"machine": "Other", "tags": ["zzz"]})
    assert r.status_code == 409
    assert (logs / "Cheerio" / "run1.log").exists() and "zzz" not in (logs / "Cheerio" / "run1.log.meta.json").read_text()
    # clearing the machine leaves the file where it is
    assert client.put("/api/files/Other/plain.log", headers=H, json={"machine": ""}).json["path"] == "Other/plain.log"
    # editing other fields without a machine key never moves
    assert client.put("/api/files/Cheerio/door.log", headers=H, json={"description": "d"}).json["path"] == "Cheerio/door.log"


def test_bulk_move(client, tmp_path):
    H = {"X-VendLog": "1"}
    logs = tmp_path / "logs"
    r = client.post("/api/bulk-move", headers=H, json={"paths": ["door.log", "plain.log"], "machine": "cheerio"})
    assert r.status_code == 200
    assert sorted(f["path"] for f in r.json["files"]) == ["Cheerio/door.log", "Cheerio/plain.log"]
    assert all(f["machine"] == "cheerio" for f in r.json["files"])
    assert (logs / "Cheerio" / "plain.log.meta.json").exists() and not (logs / "door.log").exists()
    # clash: nothing at all is moved
    (logs / "Other").mkdir()
    (logs / "Other" / "door.log").write_text("x")
    r = client.post("/api/bulk-move", headers=H, json={"paths": ["Cheerio/plain.log", "Cheerio/door.log"], "machine": "Other"})
    assert r.status_code == 409
    assert (logs / "Cheerio" / "plain.log").exists() and (logs / "Cheerio" / "door.log").exists()
    # two sources with the same name going to one folder
    (logs / "A").mkdir(); (logs / "B").mkdir()
    (logs / "A" / "same.log").write_text("1"); (logs / "B" / "same.log").write_text("2")
    assert client.post("/api/bulk-move", headers=H, json={"paths": ["A/same.log", "B/same.log"], "machine": "C"}).status_code == 409
    assert (logs / "A" / "same.log").exists() and not (logs / "C").exists()
    # validation
    assert client.post("/api/bulk-move", headers=H, json={"paths": [], "machine": "X"}).status_code == 400
    assert client.post("/api/bulk-move", headers=H, json={"paths": ["a.log"], "machine": " "}).status_code == 400
    assert client.post("/api/bulk-move", headers=H, json={"paths": ["../app.py"], "machine": "X"}).status_code in (400, 404)


def test_image_preview_and_search(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import create_app
    logs = tmp_path / "logs"
    logs.mkdir()
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")
    (logs / "shot.PNG").write_bytes(png)
    (logs / "note.txt").write_text("hello\n")
    (logs / "x.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    c = create_app({"repo_path": str(tmp_path), "logs_dir": "logs"}).test_client()
    kinds = {f["name"]: f["kind"] for f in c.get("/api/files").get_json()["files"]}
    assert kinds == {"shot.PNG": "image", "note.txt": "text", "x.svg": "image"}
    r = c.get("/api/image?path=shot.PNG")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == png
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in c.get("/api/image?path=x.svg").headers["Content-Security-Policy"]
    assert c.get("/api/image?path=note.txt").status_code == 400
    assert c.get("/api/preview?path=shot.PNG").status_code == 400
    assert c.get("/api/image?path=../app.py").status_code in (400, 404)
    res = c.get("/api/search?q=hello&content=1").get_json()
    assert [f["name"] for f in res["files"]] == ["note.txt"]


def test_external_editor_settings_and_open(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import app as app_module
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").write_text("hi\n")
    cfgfile = tmp_path / "cfg.json"
    cfgfile.write_text('{"port": 9999}')
    editor = tmp_path / "editor.exe"
    editor.write_text("x")
    c = app_module.create_app({"repo_path": str(tmp_path), "logs_dir": "logs",
                               "config_file": str(cfgfile)}).test_client()
    H = {"X-VendLog": "1"}
    assert c.post("/api/open-external", json={"path": "a.log"}, headers=H).status_code == 400  # not configured
    assert c.post("/api/settings", json={"external_editor": str(tmp_path / "nope.exe")}, headers=H).status_code == 400
    assert c.post("/api/settings", json={"external_editor": str(tmp_path)}, headers=H).status_code == 400
    assert c.post("/api/settings", json={"external_editor": f'"{editor}"'}, headers=H).status_code == 200
    saved = json.loads(cfgfile.read_text())
    assert saved == {"port": 9999, "external_editor": str(editor)}
    assert c.get("/api/settings").get_json()["external_editor"] == str(editor)

    calls = []
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    assert c.post("/api/open-external", json={"path": "a.log"}, headers=H).status_code == 200
    assert calls == [[str(editor), str(logs / "a.log")]]
    assert c.post("/api/open-external", json={"path": "../x"}, headers=H).status_code in (400, 404)
    assert c.post("/api/open-external", json={"path": "a.log"}).status_code == 403  # needs header


def test_browse_program_endpoint(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import app as app_module
    (tmp_path / "logs").mkdir()
    c = app_module.create_app({"repo_path": str(tmp_path), "logs_dir": "logs"}).test_client()
    monkeypatch.setattr(app_module.dialog, "pick_program", lambda: "C:\\x\\notepad++.exe")
    H = {"X-VendLog": "1"}
    assert c.post("/api/browse-program", headers=H).get_json() == {"path": "C:\\x\\notepad++.exe"}
    assert c.post("/api/browse-program").status_code == 403


def test_search_session_and_date_filters(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import create_app
    logs = tmp_path / "logs"
    logs.mkdir()
    def mk(name, **meta):
        (logs / name).write_text("x\n")
        (logs / (name + ".meta.json")).write_text(json.dumps(meta))
    mk("old.log", uploaded="2026-01-10T12:00:00+00:00", datetime="2025-12-25T08:00")
    mk("new.log", uploaded="2999-01-01T12:00:00+00:00", datetime="2026-03-14T09:30")
    mk("norec.log", uploaded="2999-01-02T12:00:00+00:00")
    c = create_app({"repo_path": str(tmp_path), "logs_dir": "logs"}).test_client()
    names = lambda qs: sorted(f["name"] for f in c.get("/api/search?" + qs).get_json()["files"])
    assert names("") == ["new.log", "norec.log", "old.log"]
    assert names("session=1") == ["new.log", "norec.log"]
    assert names("datekind=recorded&from=2026-01-01") == ["new.log"]
    assert names("datekind=recorded&to=2025-12-31") == ["old.log"]
    assert names("datekind=upload&from=2026-01-09&to=2026-01-11") == ["old.log"]
    assert names("q=old&mode=or&session=1") == []  # session narrows even in OR mode


def test_search_folder_filter(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import create_app
    logs = tmp_path / "logs"
    (logs / "Coke Red").mkdir(parents=True)
    (logs / "Pepsi").mkdir()
    for rel in ("root.log", "Coke Red/a.log", "Pepsi/b.log"):
        (logs / rel).write_text("x\n")
    c = create_app({"repo_path": str(tmp_path), "logs_dir": "logs"}).test_client()
    names = lambda qs: sorted(f["path"] for f in c.get("/api/search?" + qs).get_json()["files"])
    assert names("folder=coke red") == ["Coke Red/a.log"]
    assert names("folder=.") == ["root.log"]
    assert names("folder=Pepsi&q=b") == ["Pepsi/b.log"]
    assert names("folder=Pepsi&q=a") == []
    assert len(names("")) == 3
