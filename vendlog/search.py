"""Search over log metadata (filename, tags, machine, description) and optionally file contents."""
from __future__ import annotations

from datetime import datetime

from . import preview
from .store import Store

MAX_RESULTS = 500


def parse_terms(q: str) -> list[str]:
    seen, out = set(), []
    for part in (q or "").split(","):
        t = " ".join(part.split()).lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _day(f: dict, kind: str) -> str:
    """The file's upload day (local time; falls back to the file's modified time) or recorded day."""
    if kind == "recorded":
        return (f.get("datetime") or "")[:10]
    try:
        return datetime.fromisoformat(f.get("uploaded") or f["modified"]).astimezone().strftime("%Y-%m-%d")
    except (ValueError, KeyError):
        return ""


def search(store: Store, q: str = "", tags: list[str] | None = None, mode: str = "and",
           content: bool = False, session: bool = False, date_kind: str = "upload",
           date_from: str = "", date_to: str = "", folder: str = "") -> dict:
    """Each comma-separated term and each selected tag is one condition.

    mode "and": a file must satisfy every condition; "or": at least one.
    A term is satisfied by the filename/path, a tag, the machine or description (substring,
    case-insensitive), or - when `content` is on - by the file text. Selected tags match exactly.
    `session` and the date range always narrow the result, whatever the mode.
    """
    terms = parse_terms(q)
    want_tags = [t.lower() for t in (tags or []) if t and t.strip()]
    mode = "or" if mode == "or" else "and"
    combine = any if mode == "or" else all
    date_kind = "recorded" if date_kind == "recorded" else "upload"

    results, truncated = [], False
    for f in store.list_logs():
        if folder:  # "." means files directly in the logs folder
            top = f["path"].split("/")[0] if "/" in f["path"] else "."
            if top.lower() != folder.lower():
                continue
        if session and not (f.get("uploaded") and f["uploaded"] >= store.session_start):
            continue
        if date_from or date_to:
            day = _day(f, date_kind)
            if not day or (date_from and day < date_from) or (date_to and day > date_to):
                continue
        file_tags = {t.lower() for t in f["tags"] if isinstance(t, str)}
        tag_ok = [t in file_tags for t in want_tags]
        if mode == "and" and not all(tag_ok):
            continue

        haystack = [f["path"].lower(), f.get("machine", "").lower(), f.get("maker", "").lower(),
                    f.get("description", "").lower(),
                    *(t.lower() for t in file_tags)]
        term_ok = [any(t in h for h in haystack) for t in terms]

        hits = []
        if content and terms and f.get("kind") == "text":
            found, hits = preview.scan_terms(store.resolve(f["path"]), terms)
            term_ok = [ok or t in found for ok, t in zip(term_ok, terms)]

        conditions = term_ok + tag_ok
        if conditions and not combine(conditions):
            continue
        if len(results) >= MAX_RESULTS:
            truncated = True
            break
        results.append({**f, "hits": hits})
    return {"files": results, "truncated": truncated, "terms": terms}
