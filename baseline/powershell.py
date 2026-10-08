"""Running small Windows PowerShell scripts, for things Python can't do on the lab PCs
(e.g. resizing pictures and drawing slates with Windows' own System.Drawing)."""
from __future__ import annotations

import base64
import subprocess

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Runs `script` in Windows PowerShell without a window. Passed encoded, so quotes and
    the execution policy (which only covers .ps1 files) don't get in the way. Raises OSError
    or subprocess.TimeoutExpired if PowerShell can't run it."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW)


def quote(text: str) -> str:
    """`text` as a single-quoted PowerShell string."""
    return "'" + str(text).replace("'", "''") + "'"
