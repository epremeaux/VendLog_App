"""Native 'choose a program' dialog. Browsers hide full file paths, so the server opens the dialog itself."""
from __future__ import annotations

import subprocess
import sys
import threading

from .store import StoreError

_lock = threading.Lock()

# Run in a separate process: Tk wants its own main thread, and this keeps the server unaffected.
_TK_SCRIPT = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
types = [("Programs", "*.exe;*.bat;*.cmd"), ("All files", "*.*")] if sys.platform == "win32" else [("All files", "*")]
path = filedialog.askopenfilename(title="Choose your text editor", filetypes=types)
sys.stdout.write(path or "")
"""

_MAC_SCRIPT = ('POSIX path of (choose file with prompt "Choose your text editor" '
               'of type {"app", "public.executable"})')


def pick_program() -> str:
    """Show the dialog and return the chosen path, or '' if cancelled."""
    if not _lock.acquire(blocking=False):
        raise StoreError("A file dialog is already open")
    try:
        if sys.platform == "darwin":
            cmd = ["osascript", "-e", _MAC_SCRIPT]
        else:
            cmd = [sys.executable, "-c", _TK_SCRIPT]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise StoreError(f"Could not show the file dialog: {e}. Type the path instead.")
        if r.returncode != 0:
            if "-128" in r.stderr:  # macOS: user cancelled
                return ""
            raise StoreError("The file dialog is not available on this computer. Type the path instead.")
        out = r.stdout.strip()
        if sys.platform == "darwin":
            return out.rstrip("/")
        return out.replace("/", "\\") if sys.platform == "win32" else out
    finally:
        _lock.release()
