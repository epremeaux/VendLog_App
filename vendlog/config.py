"""Local configuration (vendlog.config.json, not tracked by Git)."""
from __future__ import annotations

import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = APP_DIR / "vendlog.config.json"

DEFAULTS = {
    "repo_path": str(APP_DIR),
    "logs_dir": "logs",
    "data_repo_url": "https://github.com/epremeaux/VendLog_Data.git",
    "host": "127.0.0.1",
    "port": 8765,
}


def load(path: Path = CONFIG_FILE) -> dict:
    cfg = dict(DEFAULTS)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def save_key(key: str, value, path: Path = CONFIG_FILE) -> None:
    """Update one key in the local config file, leaving every other key as it was."""
    data = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    data[key] = value
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
