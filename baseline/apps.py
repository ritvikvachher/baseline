"""Finds every installed version of the software in config/apps.json, and launches it.

Each program is looked for in three places, so it's found on any computer:
1. The Windows registry (where installers record the install folder).
2. The usual install folders ("paths" in apps.json).
3. Extra locations the user added themselves (Launch tab > Add Location...).
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None


@dataclass(frozen=True)
class Installation:
    app: str          # key in apps.json, e.g. "houdini"
    label: str        # "Houdini"
    version: str      # "21.0.729"
    exe: Path
    install_dir: Path

    @property
    def display(self) -> str:
        return f"{self.label} {self.version}".strip()

    def tool(self, app_cfg: dict, name: str) -> Optional[Path]:
        """Another program from the same install, e.g. hython next to houdini."""
        relative = app_cfg.get("tools", {}).get(name)
        if not relative:
            return None
        path = self.install_dir / relative
        return path if path.is_file() else None


def _natural_key(text: str):
    # So "21.0.729" sorts after "20.5.613", and 2026 after 2025.
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def _registry_installs(reg: dict) -> Iterator[Tuple[str, Path]]:
    """Yields (version name, install folder) from one registry rule in apps.json."""
    if winreg is None:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, reg["key"], 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            continue
        with key:
            if reg.get("mode") == "values":
                # e.g. Houdini: value "21.0.0.729" = "C:\Program Files\...\Houdini 21.0.729\"
                i = 0
                while True:
                    try:
                        name, data, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if isinstance(data, str) and name[:1].isdigit():
                        yield name, Path(data)
            else:
                # e.g. Maya: subkey "2025" > Setup\InstallPath > MAYA_INSTALL_LOCATION
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(key, f"{name}\\{reg['subpath']}") as sub:
                            data, _ = winreg.QueryValueEx(sub, reg["value"])
                    except OSError:
                        continue
                    yield name, Path(data)


def _install_dir_for(exe: Path, app_cfg: dict) -> Path:
    # exe "bin/houdini.exe" is two levels below the install folder.
    depth = len(Path(app_cfg.get("exe", exe.name)).parts)
    return exe.parents[depth - 1]


def detect_installations(app: str, app_cfg: dict, extra_exes=()) -> List[Installation]:
    """All installed versions of one program, newest first."""
    found: Dict[str, Installation] = {}
    version_re = re.compile(app_cfg["version_regex"]) if app_cfg.get("version_regex") else None

    def add(exe: Path, fallback_version: str):
        if not exe.is_file():
            return
        key = os.path.normcase(os.path.normpath(str(exe)))
        if key in found:
            return
        match = version_re.search(str(exe)) if version_re else None
        version = match.group(1) if match else fallback_version
        found[key] = Installation(app, app_cfg.get("label", app), version, exe, _install_dir_for(exe, app_cfg))

    exe_relative = app_cfg.get("exe", "")
    for reg in app_cfg.get("registry", []):
        for name, folder in _registry_installs(reg):
            add(folder / exe_relative, name)
    for pattern in app_cfg.get("paths", []):
        for match in glob.glob(os.path.expandvars(pattern)):
            add(Path(match), "")
    for exe in extra_exes:
        add(Path(exe), "custom")

    return sorted(found.values(), key=lambda inst: _natural_key(inst.version), reverse=True)


def detect_all(app_config: dict, extra: Dict[str, List[str]] = None) -> Dict[str, List[Installation]]:
    extra = extra or {}
    return {app: detect_installations(app, cfg, extra.get(app, [])) for app, cfg in app_config.items()}


def app_for_extension(app_config: dict, extension: str) -> Optional[str]:
    extension = extension.lower()
    for name, cfg in app_config.items():
        if extension in (e.lower() for e in cfg.get("extensions", [])):
            return name
    return None


def saved_version(app_cfg: dict, file: Path) -> Optional[str]:
    """The version a scene was last saved with, read from the file's header."""
    pattern = app_cfg.get("file_version_regex")
    if not pattern:
        return None
    try:
        with open(file, "rb") as f:
            head = f.read(65536).decode("latin-1")
    except OSError:
        return None
    match = re.search(pattern, head)
    return match.group(1) if match else None


def pick_installation(installs: List[Installation], wanted_version: Optional[str] = None,
                      default_version: Optional[str] = None) -> Optional[Installation]:
    """Exact match for the scene's version, then the user's default, then the newest."""
    for version in (wanted_version, default_version):
        if version:
            for inst in installs:
                if inst.version == version:
                    return inst
    return installs[0] if installs else None


def launch(inst: Installation, app_cfg: dict, file: Optional[Path], env: dict, cwd: Optional[Path] = None):
    cmd = [str(inst.exe), *app_cfg.get("args", [])]
    if file:
        cmd += [arg.replace("{file}", str(file)) for arg in app_cfg.get("open_args", ["{file}"])]
    full_env = os.environ.copy()
    full_env.update(env)
    return subprocess.Popen(cmd, env=full_env, cwd=str(cwd) if cwd and Path(cwd).is_dir() else None)
