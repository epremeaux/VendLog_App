"""Git integration for the logs folder, using the local git CLI.

The logs folder is its own Git repository (logs + metadata). Authentication is left to git
(for example Git Credential Manager); no credentials are stored or accepted here.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .store import Conflict, StoreError

TOKEN_TTL = 300
CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
_URL_CREDS = re.compile(r"(://)[^/@\s]*:[^/@\s]*@")


class GitError(StoreError):
    status = 502


def scrub(text: str) -> str:
    return _URL_CREDS.sub(r"\1", text or "")


def validate_url(url: str, allow_local: bool = False) -> str:
    url = (url or "").strip()
    if not url or url.startswith("-") or re.search(r"[\s\x00]", url):
        raise StoreError("Enter a repository URL such as https://github.com/owner/repo.git")
    if re.match(r"^(https|ssh)://", url, re.I):
        parts = urlsplit(url)
        if not parts.hostname or parts.password is not None:
            raise StoreError("Do not put a password or token in the URL; Git Credential Manager handles sign-in")
        return url
    if re.match(r"^[\w.-]+@[\w.-]+:[^\s]+$", url):
        return url
    if allow_local and os.path.isdir(url):
        return url
    raise StoreError("Only https://, ssh:// or git@host:path repository URLs are accepted")


def repo_name(url: str) -> str:
    last = re.split(r"[/:\\]", url.rstrip("/\\"))[-1]
    return re.sub(r"\.git$", "", last, flags=re.I)


def _retry(fn, tries: int = 8, delay: float = 0.5):
    """Retry briefly on Windows sharing/permission errors (sync clients, virus scanners)."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(delay)


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class Git:
    def __init__(self, logs_dir: Path, allow_local: bool = False, before_push=None):
        self.dir = Path(logs_dir)
        self.allow_local = allow_local
        self.before_push = before_push
        self._lock = threading.Lock()
        self._pending: dict | None = None

    # ---- plumbing ---------------------------------------------------------
    def _run(self, args, timeout=120, check=True, cwd=None) -> subprocess.CompletedProcess:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        cmd = ["git", "-c", "protocol.ext.allow=never", "-c", "core.quotepath=off", *args]
        try:
            r = subprocess.run(cmd, cwd=str(cwd or self.dir), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, env=env,
                               stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise GitError("git is not installed or not on PATH")
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} timed out after {timeout}s (waiting for sign-in or network?)")
        if check and r.returncode != 0:
            msg = scrub((r.stderr or r.stdout or "").strip()) or f"git {args[0]} failed"
            if "tell me who you are" in msg or "empty ident" in msg:
                msg += "\n\nSet your identity once: git config --global user.name \"Your Name\" and git config --global user.email you@example.com"
            raise GitError(msg)
        return r

    def _out(self, args, **kw) -> str:
        return self._run(args, **kw).stdout.strip()

    def _guard(self):
        if not self._lock.acquire(blocking=False):
            raise Conflict("Another Git operation is running; try again in a moment")

    def is_repo(self) -> bool:
        if not self.dir.is_dir():
            return False
        r = self._run(["rev-parse", "--show-toplevel"], check=False)
        return r.returncode == 0 and _same(r.stdout.strip(), str(self.dir))

    def _require_repo(self):
        if not self.is_repo():
            raise StoreError("No repository is connected. Connect one first.")

    # ---- status -----------------------------------------------------------
    def status(self) -> dict:
        if not self.is_repo():
            return {"connected": False, "pending": self._pending is not None}
        st = {"connected": True}
        st["branch"] = self._out(["symbolic-ref", "--short", "-q", "HEAD"], check=False) or "(detached)"
        r = self._run(["remote", "get-url", "origin"], check=False)
        st["remote"] = scrub(r.stdout.strip()) if r.returncode == 0 else ""
        up = self._run(["rev-parse", "--abbrev-ref", "@{u}"], check=False)
        st["upstream"] = up.stdout.strip() if up.returncode == 0 else ""
        st["ahead"] = st["behind"] = None
        if st["upstream"]:
            c = self._run(["rev-list", "--left-right", "--count", "HEAD...@{u}"], check=False)
            if c.returncode == 0:
                a, b = c.stdout.split()
                st["ahead"], st["behind"] = int(a), int(b)
        st["has_commits"] = self._run(["rev-parse", "--verify", "-q", "HEAD"], check=False).returncode == 0
        raw = self._run(["status", "--porcelain", "-z", "-uall", "--", "."]).stdout.split("\0")
        changes, conflicts, i = [], [], 0
        while i < len(raw):
            e = raw[i]
            i += 1
            if len(e) < 4:
                continue
            code, path = e[:2], e[3:]
            if code[0] in "RC":
                i += 1  # original path of a rename/copy
            changes.append({"code": code.strip() or "?", "path": path})
            if code in CONFLICT_CODES:
                conflicts.append(path)
        st["changes"], st["conflicts"] = changes, conflicts
        gitdir = self._out(["rev-parse", "--git-dir"])
        gd = Path(gitdir) if os.path.isabs(gitdir) else self.dir / gitdir
        st["merging"] = (gd / "MERGE_HEAD").exists()
        st["diverged"] = bool(st["ahead"] and st["behind"])
        return st

    # ---- sync operations --------------------------------------------------
    def fetch(self) -> dict:
        self._guard()
        try:
            self._require_repo()
            self._run(["fetch", "--prune", "origin"], timeout=300)
            return self.status()
        finally:
            self._lock.release()

    def pull(self) -> dict:
        self._guard()
        try:
            self._require_repo()
            st = self.status()
            if st["merging"] or st["conflicts"]:
                raise Conflict("Unresolved merge conflicts exist: " + ", ".join(st["conflicts"] or ["(merge in progress)"]))
            self._run(["fetch", "--prune", "origin"], timeout=300)
            st = self.status()
            if not st["upstream"] or not st["behind"]:
                return {**st, "message": "Already up to date."}
            if not st["ahead"]:
                self._run(["merge", "--ff-only", "@{u}"])
                return {**self.status(), "message": f"Pulled {st['behind']} new commit(s)."}
            # Both sides have new commits: merge, but never leave a half-done merge behind.
            r = self._run(["merge", "--no-edit", "@{u}"], check=False)
            if r.returncode != 0:
                bad = self.status()["conflicts"]
                self._run(["merge", "--abort"], check=False)
                if bad:
                    raise Conflict("Merge conflict between your changes and the remote in: " + ", ".join(bad)
                                   + ". Nothing was changed. Resolve with git in the logs folder, or ask your colleague to coordinate.")
                raise GitError(scrub((r.stderr or r.stdout).strip()))
            return {**self.status(), "message": f"Merged {st['behind']} remote commit(s) with your {st['ahead']} local commit(s)."}
        finally:
            self._lock.release()

    def _ensure_excludes(self):
        ex = self.dir / ".git" / "info" / "exclude"
        try:
            text = ex.read_text(encoding="utf-8") if ex.exists() else ""
            if "*.tmp" not in text.split():
                ex.parent.mkdir(parents=True, exist_ok=True)
                ex.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "*.tmp\n", encoding="utf-8")
        except OSError:
            pass

    def push(self, message: str = "") -> dict:
        """Commit local changes (if any) and push. Refuses when the remote has newer commits."""
        self._guard()
        try:
            self._require_repo()
            st = self.status()
            if st["merging"] or st["conflicts"]:
                raise Conflict("Unresolved merge conflicts exist; resolve them first")
            self._run(["fetch", "--prune", "origin"], timeout=300)
            st = self.status()
            if st["behind"]:
                raise Conflict(f"The remote has {st['behind']} newer commit(s). Pull first so nothing is overwritten.")
            done = []
            if self.before_push:
                self.before_push()
                st = self.status()
            if st["changes"]:
                self._ensure_excludes()
                msg = (message or "").strip() or f"Update logs ({len(st['changes'])} change(s))"
                self._run(["add", "-A", "--", "."])
                self._run(["commit", "-m", msg, "--", "."])
                done.append(f"Committed {len(st['changes'])} change(s)")
            st = self.status()
            if not st["has_commits"]:
                raise StoreError("Nothing to push yet.")
            if st["upstream"] and not st["ahead"]:
                return {**st, "message": "Nothing to push; already in sync."}
            self._run(["push", "-u", "origin", "HEAD"], timeout=600)  # never --force
            done.append("pushed to GitHub")
            return {**self.status(), "message": "; ".join(done) + "."}
        finally:
            self._lock.release()

    # ---- connecting a different repository --------------------------------
    def prepare_connect(self, url: str, local_files: int) -> dict:
        """Step 1: check the URL is reachable and issue a one-time token plus the text to type."""
        url = validate_url(url, self.allow_local)
        self._guard()
        try:
            self._ls_remote(url)
            local = self.status() if self.is_repo() else {"connected": False}
            unsaved = bool(local.get("changes")) or bool(local.get("ahead")) or (
                local.get("connected") and not local.get("upstream") and local.get("has_commits"))
            token = secrets.token_urlsafe(16)
            self._pending = {"token": token, "url": url, "expires": time.time() + TOKEN_TTL}
            return {"token": token, "url": url, "confirm_text": repo_name(url) or "REPLACE",
                    "local_files": local_files, "currently": local.get("remote", ""),
                    "unsaved": unsaved, "expires_in": TOKEN_TTL}
        finally:
            self._lock.release()

    def _ls_remote(self, url: str):
        parent = self.dir.parent
        r = self._run(["ls-remote", "--", url], timeout=120, check=False, cwd=parent)
        if r.returncode != 0:
            raise GitError("Cannot reach that repository: " + (scrub(r.stderr).strip() or "unknown error"))

    def connect(self, url: str, token: str, confirm: str) -> dict:
        """Step 2: clone the new repository and swap it in. The old folder is kept as a backup."""
        url = validate_url(url, self.allow_local)
        self._guard()
        try:
            p = self._pending
            if not p or p["expires"] < time.time() or not secrets.compare_digest(p["token"], token or "") or p["url"] != url:
                self._pending = None
                raise Conflict("The confirmation expired or does not match. Start again from step 1.")
            if (confirm or "").strip() != (repo_name(url) or "REPLACE"):
                raise StoreError("The confirmation text does not match.")
            self._pending = None
            parent = self.dir.parent
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            # Move the old folder aside, then clone straight into the final path: renaming a
            # freshly cloned folder is what Dropbox/antivirus tend to block on Windows.
            backup = None
            if self.dir.exists():
                backup = parent / f"{self.dir.name}.old-{stamp}"
                try:
                    _retry(lambda: os.replace(self.dir, backup))
                except OSError as e:
                    raise GitError(f"Could not move the current folder aside (is a file open or syncing?): {e}")
            try:
                self._run(["clone", "--", url, str(self.dir)], timeout=1800, cwd=parent)
            except Exception:
                shutil.rmtree(self.dir, ignore_errors=True)
                if backup is not None and not self.dir.exists():
                    try:
                        _retry(lambda: os.replace(backup, self.dir))
                    except OSError:
                        pass
                raise
            self._ensure_excludes()
            msg = "Repository connected."
            if backup:
                msg += f" The previous folder was kept as '{backup.name}'; delete it when you no longer need it."
            return {**self.status(), "message": msg}
        finally:
            self._lock.release()


class AppRepo(Git):
    """The application's own checkout (VendLog_App): check for and fetch newer versions."""

    VERSION_FILE = "vendlog/__init__.py"

    def _remote_version(self) -> str:
        r = self._run(["show", f"@{{u}}:{self.VERSION_FILE}"], check=False)
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', r.stdout) if r.returncode == 0 else None
        return m.group(1) if m else ""

    def _tracked_dirty(self) -> bool:
        return bool(self._run(["status", "--porcelain", "-uno"]).stdout.strip())

    def check(self) -> dict:
        from . import __version__
        from .version import newer
        out = {"connected": False, "app_version": __version__, "latest_version": "", "behind": 0,
               "ahead": 0, "dirty": False, "can_update": False, "message": ""}
        if not self.is_repo():
            out["message"] = "The app folder is not a Git checkout, so it cannot update itself. Download it again from GitHub."
            return out
        out["connected"] = True
        self._guard()
        try:
            self._run(["fetch", "--prune", "origin"], timeout=300)
            st = self.status()
            out["behind"], out["ahead"] = st["behind"] or 0, st["ahead"] or 0
            out["dirty"] = self._tracked_dirty()
            if st["upstream"]:
                out["latest_version"] = self._remote_version()
        finally:
            self._lock.release()
        out["update_available"] = bool(out["behind"]) or newer(out["latest_version"], __version__)
        out["can_update"] = out["behind"] > 0 and not out["ahead"] and not out["dirty"]
        if not out["update_available"]:
            out["message"] = "The app is up to date."
        elif not out["behind"]:
            out["restart_needed"] = True
            out["message"] = "The newer version is already downloaded. Close VendLog and start it again to use it."
        elif out["ahead"] or out["dirty"]:
            out["message"] = "An update exists, but this copy of the app has local changes, so it was not applied automatically."
        return out

    def update(self) -> dict:
        self._guard()
        try:
            self._require_repo()
            if self._tracked_dirty():
                raise Conflict("The app folder has local changes; not updating over them.")
            self._run(["fetch", "--prune", "origin"], timeout=300)
            st = self.status()
            if st["ahead"]:
                raise Conflict("The app folder has local commits that are not on GitHub; not updating.")
            if not st["behind"]:
                return {"message": "The app is already up to date.", "restart": False}
            old = self._out(["rev-parse", "HEAD"])
            self._run(["merge", "--ff-only", "@{u}"])
            changed = self._out(["diff", "--name-only", old, "HEAD"]).split()
            msg = "App updated. Close VendLog and start it again to use the new version."
            if "requirements.txt" in changed:
                msg += " requirements.txt changed: also run  pip install -r requirements.txt"
            return {"message": msg, "restart": True, "new_version": self._remote_version() or ""}
        finally:
            self._lock.release()
