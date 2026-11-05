"""Search over log metadata (filename, tags, machine, description) and optionally file contents."""
from __future__ import annotations

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


def search(store: Store, q: str = "", tags: list[str] | None = None, mode: str = "and",
           content: bool = False) -> dict:
    """Each comma-separated term and each selected tag is one condition.

    mode "and": a file must satisfy every condition; "or": at least one.
    A term is satisfied by the filename/path, a tag, the machine or description (substring,
    case-insensitive), or - when `content` is on - by the file text. Selected tags match exactly.
    """
    terms = parse_terms(q)
    want_tags = [t.lower() for t in (tags or []) if t and t.strip()]
    mode = "or" if mode == "or" else "and"
    combine = any if mode == "or" else all

    results, truncated = [], False
    for f in store.list_logs():
        file_tags = {t.lower() for t in f["tags"] if isinstance(t, str)}
        tag_ok = [t in file_tags for t in want_tags]
        if mode == "and" and not all(tag_ok):
            continue

        haystack = [f["path"].lower(), f.get("machine", "").lower(), f.get("maker", "").lower(),
                    f.get("description", "").lower(),
                    *(t.lower() for t in file_tags)]
        term_ok = [any(t in h for h in haystack) for t in terms]

        hits = []
        if content and terms and f.get("kind") != "image":
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
