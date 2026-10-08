"""Shows the project folder as a drive letter (e.g. X:) using Windows' `subst`.

Everyone's OneDrive is at a different path, so a Houdini or Maya scene that
references C:/Users/sam/OneDrive/... breaks on anyone else's computer. With the
project mapped to X: on every computer, all paths look the same: X:/assets/...
The mapping needs no admin rights and lasts until you log off.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

_SUBST_LINE = re.compile(r"^([A-Za-z]):\\: => (.+)$")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_subst_output(text: str) -> Dict[str, str]:
    mappings = {}
    for line in text.splitlines():
        match = _SUBST_LINE.match(line.strip())
        if match:
            mappings[match.group(1).upper()] = match.group(2).strip()
    return mappings


def current_mappings() -> Dict[str, str]:
    try:
        result = subprocess.run(["subst"], capture_output=True, text=True, creationflags=_NO_WINDOW)
    except OSError:
        return {}
    return parse_subst_output(result.stdout)


def same_path(a, b) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def real_path(path) -> Path:
    """X:/film -> the folder X: points to, so we never save a path that only exists while mapped."""
    path = Path(path)
    mapped = current_mappings().get(path.drive.rstrip(":").upper()) if path.drive else None
    if not mapped:
        return path
    return Path(mapped).joinpath(*path.parts[1:])


def unmap_drive(letter: str) -> None:
    subprocess.run(["subst", f"{letter.strip().rstrip(':').upper()}:", "/D"],
                   capture_output=True, creationflags=_NO_WINDOW)


def map_drive(letter: str, target) -> Tuple[Optional[Path], str]:
    """Returns (drive root or None, message for the status bar)."""
    letter = letter.strip().rstrip(":").upper()
    drive_root = Path(f"{letter}:\\")
    mapped = current_mappings().get(letter)
    if mapped:
        if same_path(mapped, target):
            return drive_root, f"Project is on drive {letter}:"
        return None, f"Drive {letter}: is already used for {mapped}, so the full folder path is used instead."
    if drive_root.exists():
        return None, f"Drive {letter}: is already in use on this computer, so the full folder path is used instead."
    try:
        subprocess.run(["subst", f"{letter}:", str(Path(target))], capture_output=True, creationflags=_NO_WINDOW)
    except OSError as e:
        return None, f"Could not map drive {letter}: ({e})."
    if same_path(current_mappings().get(letter, ""), target):
        return drive_root, f"Project mapped to drive {letter}:"
    return None, f"Could not map drive {letter}:, so the full folder path is used instead."
