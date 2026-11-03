"""Compare the running app version with the version recorded in the data repository."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import DATA_FORMAT, __version__
from .store import MANIFEST_FILE, StoreError


def parse(v) -> tuple | None:
    m = re.fullmatch(r"\s*v?(\d+(?:\.\d+)*)\s*", v if isinstance(v, str) else "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def newer(a, b) -> bool:
    """True if version a is strictly newer than b (unparseable counts as not newer)."""
    pa, pb = parse(a), parse(b)
    return bool(pa and pb and pa > pb)


def read_manifest(logs_dir: Path) -> dict | None:
    try:
        data = json.loads((Path(logs_dir) / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def data_info(logs_dir: Path) -> dict:
    m = read_manifest(logs_dir)
    info = {"app_version": __version__, "supported_format": DATA_FORMAT, "has_manifest": m is not None,
            "data_app_version": "", "data_format": None, "app_too_old": False, "format_unsupported": False}
    if m:
        info["data_app_version"] = str(m.get("app_version", ""))
        fmt = m.get("data_format")
        info["data_format"] = fmt if isinstance(fmt, int) else None
        info["app_too_old"] = newer(info["data_app_version"], __version__)
        info["format_unsupported"] = isinstance(fmt, int) and fmt > DATA_FORMAT
    return info


def stamp(logs_dir: Path) -> bool:
    """Record this app's version in the data repository (never lowers it). Returns True if the file changed.

    Refuses when the data was written in a format this app cannot understand."""
    logs_dir = Path(logs_dir)
    m = read_manifest(logs_dir) or {}
    fmt = m.get("data_format")
    if isinstance(fmt, int) and fmt > DATA_FORMAT:
        raise StoreError(f"The data uses format {fmt} but this app only understands format {DATA_FORMAT}. "
                         "Update the app before pushing.")
    prev = str(m.get("app_version", ""))
    new = {"data_format": max(DATA_FORMAT, fmt if isinstance(fmt, int) else 0),
           "app_version": __version__ if not newer(prev, __version__) else prev}
    if m.get("data_format") == new["data_format"] and m.get("app_version") == new["app_version"]:
        return False
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / MANIFEST_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True
