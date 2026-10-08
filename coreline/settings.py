"""Where settings live:

- config/ (next to this code): the tool's own config, e.g. which software it knows about
  and team.json (university login -> short name, and the shared project folder's name).
- <project folder>/coreline_project.json: the film's shared settings (tasks, fps...).
  It lives in OneDrive, so everyone gets the same one.
- %APPDATA%/CORELINE/settings.json: this user's own settings on this computer
  (their name, where the project folder is, preferred software versions).
"""
from __future__ import annotations

import getpass
import glob
import json
import os
import re
from pathlib import Path
from typing import List, Optional

TOOL_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = TOOL_ROOT / "config"
PROJECT_MARKER = "coreline_project.json"
USER_FILE = Path(os.environ.get("APPDATA") or Path.home()) / "CORELINE" / "settings.json"

# OneDrive lives at a different path for every user, e.g.
#   C:/Users/s5707305/OneDrive - Bournemouth University   (%OneDriveCommercial%)
# Saving paths relative to these variables lets the same setting work for everyone.
# USERPROFILE (C:/Users/s5707305) is the fallback for Teams/SharePoint folders, which
# sync to C:/Users/<id>/Bournemouth University/... instead.
ONEDRIVE_VARS = ("OneDriveCommercial", "OneDriveConsumer", "OneDrive", "USERPROFILE")


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_project_template() -> dict:
    return read_json(CONFIG_DIR / "project_template.json")


def load_app_config() -> dict:
    return read_json(CONFIG_DIR / "apps.json", {})


# --- Per-user settings ------------------------------------------------------

def load_user() -> dict:
    return read_json(USER_FILE, {})


def get_user_setting(key: str, default=None):
    return load_user().get(key, default)


def save_user_setting(key: str, value) -> None:
    data = load_user()
    data[key] = value
    write_json(USER_FILE, data)


def sanitize_user(name: str) -> str:
    """User names go into file names, so keep them to lowercase letters and digits."""
    return re.sub(r"[^a-z0-9]", "", name.lower()) or "user"


def load_team() -> dict:
    return read_json(CONFIG_DIR / "team.json", {})


def team_member(login: str = None) -> Optional[dict]:
    """{"name": ..., "short": ...} for a university login listed in config/team.json."""
    login = (login or getpass.getuser()).lower()
    return load_team().get("members", {}).get(login)


def first_name(user: str = None) -> Optional[str]:
    """The first name to greet someone by, from team.json ("ritvik" -> "Ritvik").
    With no `user`, it's whoever is logged in, or None if CORELINE only knows their login."""
    if user is None:
        member = team_member()
        if member:
            return member["name"].split()[0]
        chosen = os.environ.get("CORELINE_USER") or get_user_setting("user")
        if not chosen:
            return None
        user = sanitize_user(chosen)
    for member in load_team().get("members", {}).values():
        # "ritvik" or "ritvikvachher" (a home PC login) both mean Ritvik Vachher.
        if user in (member.get("short"), sanitize_user(member["name"])):
            return member["name"].split()[0]
    return user.title()


def current_user() -> str:
    member = team_member()
    name = (os.environ.get("CORELINE_USER") or get_user_setting("user")
            or (member and member["short"]) or getpass.getuser())
    return sanitize_user(name)


# --- Portable paths ---------------------------------------------------------

def portable_path(path) -> str:
    """C:/Users/sam/OneDrive - Uni/Film -> %OneDriveCommercial%/Film, when possible."""
    path = Path(path)
    best_var, best_base = None, None
    for var in ONEDRIVE_VARS:
        value = os.environ.get(var)
        if not value:
            continue
        base = Path(value)
        try:
            path.relative_to(base)
        except ValueError:
            continue
        if best_base is None or len(str(base)) > len(str(best_base)):
            best_var, best_base = var, base
    if best_var is None:
        return path.as_posix()
    rest = path.relative_to(best_base).as_posix()
    return f"%{best_var}%" + ("" if rest == "." else "/" + rest)


def expand_path(text: str) -> Path:
    return Path(os.path.expandvars(text))


# --- Finding the project ----------------------------------------------------

def is_project(path) -> bool:
    return (Path(path) / PROJECT_MARKER).is_file()


def project_root_candidates() -> List[Path]:
    candidates = []
    if os.environ.get("CORELINE_ROOT"):
        candidates.append(expand_path(os.environ["CORELINE_ROOT"]))
    saved = get_user_setting("root")
    if saved:
        candidates.append(expand_path(saved))
    # If this tool is stored inside the project folder (e.g. <project>/_pipeline/CORELINE),
    # every computer that syncs the project finds it without being told.
    candidates.extend(TOOL_ROOT.parents)
    # The team's shared Teams folder has the same name for everyone, so look for it in
    # this user's OneDrive (e.g. C:/Users/s5707305/OneDrive - Bournemouth University/...).
    folder = load_team().get("project_folder")
    if folder:
        for var in ONEDRIVE_VARS:
            if os.environ.get(var):
                candidates.append(Path(os.environ[var]) / folder)
        profile = Path(os.environ.get("USERPROFILE") or Path.home())
        candidates.extend(profile.glob(f"*/{glob.escape(folder)}"))
    return candidates


def find_project_root() -> Optional[Path]:
    for candidate in project_root_candidates():
        if is_project(candidate):
            return candidate
    return None


def remember_project_root(root) -> None:
    save_user_setting("root", portable_path(root))
