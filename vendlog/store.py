"""Log files and their sidecar metadata (<name>.meta.json)."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import shutil
from pathlib import Path

META_SUFFIX = ".meta.json"
VOCAB_FILE = "tags.json"
MANIFEST_FILE = "vendlog.json"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp", ".svg": "image/svg+xml"}
META_FIELDS = ("tags", "machine", "maker", "datetime", "description", "uploaded")
DEFAULT_MAKERS = ("Fuji Denki", "Sanden", "Panasonic", "JVMA", "UNKNOWN")


class StoreError(ValueError):
    status = 400


class NotFound(StoreError):
    status = 404


class Conflict(StoreError):
    status = 409


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_TAG_LEN = 64


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_filename(name: str) -> str:
    name = _BAD_CHARS.sub("_", os.path.basename((name or "").replace("\\", "/"))).strip(" .")
    if not name or name.startswith("."):
        raise StoreError("Invalid filename")
    if name.endswith(META_SUFFIX) or name.lower() in (VOCAB_FILE, MANIFEST_FILE) or name.endswith(".tmp"):
        raise StoreError("Reserved filename: " + name)
    if name.split(".")[0].upper() in _RESERVED:
        raise StoreError("Reserved filename: " + name)
    return name


def norm_tag(tag) -> str:
    if not isinstance(tag, str):
        raise StoreError("Tags must be strings")
    tag = " ".join(tag.split())
    if not tag or len(tag) > MAX_TAG_LEN:
        raise StoreError(f"Tag must be 1-{MAX_TAG_LEN} characters")
    return tag


def norm_tags(tags) -> list[str]:
    """Normalise, dedupe case-insensitively (first spelling wins) and sort."""
    if not isinstance(tags, list):
        raise StoreError("tags must be a list")
    seen: dict[str, str] = {}
    for t in tags:
        t = norm_tag(t)
        seen.setdefault(t.lower(), t)
    return sorted(seen.values(), key=str.lower)


class Store:
    def __init__(self, logs_root: Path):
        self.root = Path(logs_root).resolve()
        self.session_start = _now_utc()  # "this session" = since the server started

    def resolve(self, rel: str) -> Path:
        """Map a relative posix path to a file inside the logs folder."""
        if not rel or "\x00" in rel:
            raise StoreError("Invalid path")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise StoreError("Path outside logs folder")
        return p

    def _is_log(self, p: Path) -> bool:
        rel = p.relative_to(self.root)
        if any(part.startswith(".") for part in rel.parts):
            return False
        return (p.is_file() and not p.name.endswith(META_SUFFIX)
                and rel.as_posix() not in (VOCAB_FILE, MANIFEST_FILE, "README.md"))

    def read_meta(self, p: Path) -> dict:
        meta = {"tags": [], "machine": "", "maker": "", "datetime": "", "description": "", "uploaded": ""}
        mp = p.with_name(p.name + META_SUFFIX)
        if mp.exists():
            try:
                data = json.loads(mp.read_text(encoding="utf-8"))
                for k in META_FIELDS:
                    if k in data:
                        meta[k] = data[k]
            except (OSError, ValueError):
                meta["error"] = "unreadable metadata"
        return meta

    def info(self, p: Path) -> dict:
        st = p.stat()
        return {
            "path": p.relative_to(self.root).as_posix(),
            "name": p.name,
            "size": st.st_size,
            "kind": "image" if p.suffix.lower() in IMAGE_TYPES else "text",
            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            **self.read_meta(p),
        }

    def list_logs(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        return [self.info(p) for p in sorted(self.root.rglob("*")) if self._is_log(p)]

    def get(self, rel: str) -> dict:
        return self.info(self._log_path(rel))

    def _log_path(self, rel: str) -> Path:
        p = self.resolve(rel)
        if not p.exists() or not self._is_log(p):
            raise NotFound("Not found")
        return p

    @staticmethod
    def _sidecar(p: Path) -> Path:
        return p.with_name(p.name + META_SUFFIX)

    @staticmethod
    def _write_json(path: Path, data) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    # ---- upload / delete -------------------------------------------------
    def save_upload(self, filename: str, stream, prefix: str = "", machine: str = "", tags=None,
                    captured: str = "", maker: str = "") -> dict:
        name = safe_filename(filename)
        maker = " ".join((maker or "").split())
        tags = norm_tags(tags or [])
        captured = (captured or "").strip()
        if captured:
            try:
                datetime.fromisoformat(captured)
            except ValueError:
                raise StoreError("Capture date must look like 2026-03-01 or 2026-03-01T14:22")
        prefix = _BAD_CHARS.sub("_", (prefix or "").strip()).strip(" .")
        if prefix:
            name = safe_filename(f"{prefix}_{name}")
        machine = (machine or "").strip()
        folder = self.root
        if machine:
            folder_name = safe_filename(machine)  # same rules as filenames: no separators, no leading dot
            folder = self.root / folder_name
            if folder.exists() and not folder.is_dir():
                raise Conflict(f"'{folder_name}' exists and is not a folder")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        label = dest.relative_to(self.root).as_posix()
        if dest.exists() or self._sidecar(dest).exists():
            raise Conflict(f"'{label}' already exists; rename the file and try again")
        try:
            with open(dest, "xb") as out:  # "x" refuses to overwrite, even in a race
                shutil.copyfileobj(stream, out, 1024 * 1024)
        except FileExistsError:
            raise Conflict(f"'{label}' already exists")
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        self._write_json(self._sidecar(dest), {"tags": tags, "machine": machine, "maker": maker,
                                               "datetime": captured, "description": "",
                                               "uploaded": _now_utc()})
        return self.info(dest)

    def bulk_tags(self, paths: list, add: list, remove: list) -> list[dict]:
        """Add and/or remove tags on many files. Validates everything before writing."""
        if not isinstance(paths, list) or not paths:
            raise StoreError("No files selected")
        add, remove = norm_tags(add or []), norm_tags(remove or [])
        if not add and not remove:
            raise StoreError("No tags given")
        targets = [self._log_path(p) for p in paths if isinstance(p, str)]
        if len(targets) != len(paths):
            raise StoreError("Invalid path list")
        drop = {t.lower() for t in remove}
        results = []
        for p in targets:
            meta = self.read_meta(p)
            meta.pop("error", None)
            kept = [t for t in meta["tags"] if isinstance(t, str) and t.lower() not in drop]
            meta["tags"] = norm_tags(kept + add)
            self._write_json(self._sidecar(p), {k: meta[k] for k in META_FIELDS})
            results.append(self.info(p))
        return results

    def bulk_move(self, paths: list, machine: str) -> list[dict]:
        """Set the machine on many files and move them into logs/<machine>/. All-or-nothing on conflicts."""
        if not isinstance(paths, list) or not paths:
            raise StoreError("No files selected")
        machine = " ".join((machine or "").split())
        if not machine:
            raise StoreError("Enter a machine name")
        targets = [self._log_path(p) for p in paths if isinstance(p, str)]
        if len(targets) != len(paths):
            raise StoreError("Invalid path list")
        plan, taken = [], set()
        for p in dict.fromkeys(targets):
            dest = self._machine_destination(p, machine)
            if dest is not None:
                label = dest.relative_to(self.root).as_posix()
                if dest.exists() or self._sidecar(dest).exists() or label.lower() in taken:
                    raise Conflict(f"'{label}' already exists; rename a file first. Nothing was moved.")
                taken.add(label.lower())
            plan.append((p, dest))
        results = []
        for p, dest in plan:
            meta = self.read_meta(p)
            meta.pop("error", None)
            meta["machine"] = machine
            self._write_json(self._sidecar(p), {k: meta[k] for k in META_FIELDS})
            results.append(self.info(self._move(p, dest) if dest is not None else p))
        return results

    def rename(self, rel: str, new_name: str) -> dict:
        p = self._log_path(rel)
        name = safe_filename(new_name)
        dest = p.with_name(name)
        same_file = name.lower() == p.name.lower()
        if not same_file and (dest.exists() or self._sidecar(dest).exists()):
            raise Conflict(f"'{name}' already exists")
        if name == p.name:
            return self.info(p)
        old_meta, new_meta = self._sidecar(p), self._sidecar(dest)
        os.rename(p, dest)
        if old_meta.exists():
            try:
                os.rename(old_meta, new_meta)
            except OSError:
                os.rename(dest, p)
                raise StoreError("Could not rename metadata file")
        return self.info(dest)

    def delete(self, rel: str) -> None:
        p = self._log_path(rel)
        self._sidecar(p).unlink(missing_ok=True)
        p.unlink()

    # ---- per-file metadata -----------------------------------------------
    def update_meta(self, rel: str, data: dict) -> dict:
        p = self._log_path(rel)
        meta = self.read_meta(p)
        meta.pop("error", None)
        for key in META_FIELDS:
            if key not in data or key == "uploaded":  # the upload time is recorded once and never edited
                continue
            if key == "tags":
                meta["tags"] = norm_tags(data["tags"])
            elif isinstance(data[key], str):
                meta[key] = data[key].strip()
            else:
                raise StoreError(f"{key} must be a string")
        dest = self._machine_destination(p, meta["machine"]) if "machine" in data else None
        if dest is not None and (dest.exists() or self._sidecar(dest).exists()):
            raise Conflict(f"'{dest.relative_to(self.root).as_posix()}' already exists; rename the file first")
        self._write_json(self._sidecar(p), {k: meta[k] for k in META_FIELDS})
        if dest is not None:
            p = self._move(p, dest)
        return self.info(p)

    def _machine_destination(self, p: Path, machine: str) -> Path | None:
        """Where the file belongs for this machine, or None if it is already there or no machine is set."""
        if not machine:
            return None
        folder_name = safe_filename(machine)
        current = p.parent
        if current != self.root and current.name.lower() == folder_name.lower() and current.parent == self.root:
            return None
        # Reuse an existing folder that differs only by case so we don't create near-duplicates.
        if self.root.is_dir():
            for d in self.root.iterdir():
                if d.is_dir() and d.name.lower() == folder_name.lower():
                    folder_name = d.name
                    break
        return self.root / folder_name / p.name

    def _move(self, p: Path, dest: Path) -> Path:
        if dest.parent.exists() and not dest.parent.is_dir():
            raise Conflict(f"'{dest.parent.name}' exists and is not a folder")
        dest.parent.mkdir(parents=True, exist_ok=True)
        old_meta, new_meta = self._sidecar(p), self._sidecar(dest)
        os.rename(p, dest)
        if old_meta.exists():
            try:
                os.rename(old_meta, new_meta)
            except OSError:
                os.rename(dest, p)
                raise StoreError("Could not move metadata file")
        old_dir = p.parent
        if old_dir != self.root:
            try:
                old_dir.rmdir()  # drop the old folder only if it is now empty
            except OSError:
                pass
        return dest

    # ---- machine makers ---------------------------------------------------
    def makers(self) -> list[str]:
        """Default makers plus any others already used in the records (case-insensitive unique)."""
        seen = {m.lower(): m for m in DEFAULT_MAKERS}
        for _, data in self._sidecars():
            m = data.get("maker")
            if isinstance(m, str) and m.strip():
                seen.setdefault(" ".join(m.split()).lower(), " ".join(m.split()))
        return sorted(seen.values(), key=str.lower)

    # ---- tag vocabulary ---------------------------------------------------
    def _vocab(self) -> list[str]:
        vp = self.root / VOCAB_FILE
        try:
            data = json.loads(vp.read_text(encoding="utf-8"))
            return norm_tags([t for t in data if isinstance(t, str) and t.strip()])
        except (OSError, ValueError, StoreError, TypeError):
            return []

    def _write_vocab(self, tags: list[str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_json(self.root / VOCAB_FILE, norm_tags(tags))

    def _sidecars(self):
        if not self.root.is_dir():
            return
        for mp in sorted(self.root.rglob("*" + META_SUFFIX)):
            if any(part.startswith(".") for part in mp.relative_to(self.root).parts):
                continue
            try:
                data = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get("tags"), list):
                yield mp, data

    def all_tags(self) -> list[dict]:
        names: dict[str, str] = {}
        counts: dict[str, int] = {}
        for t in self._vocab():
            names.setdefault(t.lower(), t)
            counts.setdefault(t.lower(), 0)
        for _, data in self._sidecars():
            for t in data["tags"]:
                if isinstance(t, str) and t.strip():
                    k = " ".join(t.split()).lower()
                    names.setdefault(k, " ".join(t.split()))
                    counts[k] = counts.get(k, 0) + 1
        return [{"name": names[k], "count": counts[k]} for k in sorted(names, key=lambda k: k)]

    def add_tag(self, tag: str) -> None:
        self._write_vocab(self._vocab() + [norm_tag(tag)])

    def _retag(self, old: str, new: str | None) -> int:
        """Rename (new given) or remove (new None) a tag everywhere. Returns files changed."""
        key = norm_tag(old).lower()
        changed = 0

        def apply(tags):
            out = []
            for t in tags:
                if isinstance(t, str) and " ".join(t.split()).lower() == key:
                    if new is not None:
                        out.append(new)
                else:
                    out.append(t)
            return norm_tags(out)

        found = False
        for mp, data in list(self._sidecars()):
            if any(isinstance(t, str) and " ".join(t.split()).lower() == key for t in data["tags"]):
                found = True
                data["tags"] = apply(data["tags"])
                self._write_json(mp, data)
                changed += 1
        vocab = self._vocab()
        if any(t.lower() == key for t in vocab):
            found = True
            self._write_vocab(apply(vocab))
        if not found:
            raise NotFound("Tag not found")
        return changed

    def rename_tag(self, old: str, new: str) -> int:
        return self._retag(old, norm_tag(new))

    def delete_tag(self, tag: str) -> int:
        return self._retag(tag, None)
