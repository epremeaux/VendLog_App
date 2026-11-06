"""Memory-friendly text access to log files: encoding detection, line windows, in-file search.

Lines are split on "\\n" only (a trailing "\\r" is stripped for display), and every function here
uses that same rule so line numbers agree between preview, in-file search and content search.
The file on disk is never modified.
"""
from __future__ import annotations

import codecs
from itertools import islice
from pathlib import Path

INDEX_STEP = 1000
MAX_LINE_CHARS = 10000
MAX_WINDOW = 2000
_index_cache: dict = {}
_INDEX_CACHE_MAX = 16


def detect_encoding(path: Path) -> str:
    """Return 'utf-8', 'utf-16' or 'cp1252' based on the start of the file."""
    with open(path, "rb") as f:
        sample = f.read(65536)
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def _open_text(path: Path, enc: str):
    py_enc = "utf-8-sig" if enc == "utf-8" else enc
    return open(path, "r", encoding=py_enc, errors="replace", newline="\n")


def _clean(line: str) -> str:
    line = line.rstrip("\n")
    if line.endswith("\r"):
        line = line[:-1]
    if len(line) > MAX_LINE_CHARS:
        line = line[:MAX_LINE_CHARS] + " \u2026[line truncated]"
    return line


def _index(path: Path, enc: str) -> dict:
    """Total line count plus byte offsets of every INDEX_STEP-th line (byte encodings only)."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _index_cache.get(key)
    if hit:
        return hit
    offsets = [0]
    total = 0
    if enc == "utf-16":
        offsets = None
        with _open_text(path, enc) as f:
            total = sum(1 for _ in f)
    else:
        pos = 0
        newlines = 0
        last = b""
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                last = chunk[-1:]
                i = chunk.find(b"\n")
                while i != -1:
                    newlines += 1
                    if newlines % INDEX_STEP == 0:
                        offsets.append(pos + i + 1)
                    i = chunk.find(b"\n", i + 1)
                pos += len(chunk)
        total = newlines + (1 if st.st_size and last != b"\n" else 0)
    result = {"total": total, "offsets": offsets}
    if len(_index_cache) >= _INDEX_CACHE_MAX:
        _index_cache.pop(next(iter(_index_cache)))
    _index_cache[key] = result
    return result


def window(path: Path, offset: int, limit: int) -> dict:
    """Return `limit` lines starting at 0-based line `offset`."""
    limit = max(1, min(int(limit), MAX_WINDOW))
    offset = max(0, int(offset))
    enc = detect_encoding(path)
    idx = _index(path, enc)
    total = idx["total"]
    lines: list[str] = []
    if offset < total:
        if idx["offsets"] is None:
            with _open_text(path, enc) as f:
                lines = [_clean(x) for x in islice(f, offset, offset + limit)]
        else:
            block = min(offset // INDEX_STEP, len(idx["offsets"]) - 1)
            skip = offset - block * INDEX_STEP
            py_enc = "utf-8" if enc == "utf-8" else enc
            with open(path, "rb") as f:
                f.seek(idx["offsets"][block])
                for i, raw in enumerate(islice(f, skip, skip + limit)):  # binary iteration splits on \n only
                    text = raw.decode(py_enc, errors="replace")
                    if offset == 0 and i == 0:
                        text = text.lstrip("\ufeff")
                    lines.append(_clean(text))
    return {"encoding": enc, "total": total, "offset": offset, "lines": lines, 
            "truncated": len(lines) >= 500 and offset + len(lines) < total}


def find(path: Path, query: str, max_matches: int = 1000) -> dict:
    """Case-insensitive in-file search. Returns matching line numbers (1-based) and text."""
    q = query.lower()
    enc = detect_encoding(path)
    matches, truncated = [], False
    if q:
        with _open_text(path, enc) as f:
            for n, line in enumerate(f, 1):
                if q in line.lower():
                    if len(matches) >= max_matches:
                        truncated = True
                        break
                    matches.append({"line": n, "text": _clean(line)[:300]})
    return {"encoding": enc, "matches": matches, "truncated": truncated}


def scan_terms(path: Path, terms: list[str], max_hits: int = 3) -> tuple[set, list]:
    """Stream the file once. Returns (terms found anywhere, up to max_hits sample matching lines)."""
    found: set = set()
    hits: list = []
    if not terms:
        return found, hits
    enc = detect_encoding(path)
    with _open_text(path, enc) as f:
        for n, line in enumerate(f, 1):
            low = line.lower()
            line_terms = [t for t in terms if t in low]
            if line_terms:
                found.update(line_terms)
                if len(hits) < max_hits:
                    hits.append({"line": n, "text": _clean(line)[:300]})
                if len(found) == len(terms) and len(hits) >= max_hits:
                    break
    return found, hits
