"""Checks run before publishing. They look at the file from outside Houdini/Maya:
naming, whether it's saved, which version saved it, and whether it's the right version."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from . import apps
from .project import Entity, Project, Workfile

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class Check:
    name: str
    state: str  # pass / warn / fail
    detail: str


def _size(num_bytes: int) -> str:
    mb = num_bytes / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 1 else f"{num_bytes / 1024:.0f} KB"


def run_checks(project: Project, app_config: dict, installations: Dict[str, list],
               entity: Entity, task: str, workfile: Workfile) -> List[Check]:
    checks = []
    path = workfile.path

    # 1. The file is there and has something in it.
    if not path.is_file():
        return [Check("Scene file saved", FAIL, "The file can't be found. Was it moved or renamed?")]
    stat = path.stat()
    saved = datetime.fromtimestamp(stat.st_mtime)
    if stat.st_size == 0:
        checks.append(Check("Scene file saved", FAIL, "The file is empty. Save your scene first."))
    else:
        checks.append(Check("Scene file saved", PASS, f"{_size(stat.st_size)} · saved {saved:%d %b %H:%M}"))

    # 2. Naming convention (files from the tool always pass; this catches hand-named files).
    if workfile.user:
        checks.append(Check("Naming convention", PASS, f"Matches {entity.name}_{task}_vNNN_<artist>"))
    else:
        checks.append(Check("Naming convention", WARN,
                            f"Not named {entity.name}_{task}_vNNN_<artist>. Version up saves a correctly "
                            "named copy to keep working in."))

    # 3. Which software version saved it, and is that installed here?
    app_name = apps.app_for_extension(app_config, path.suffix)
    if not app_name:
        checks.append(Check("Software version", WARN, f"BASELINE doesn't know {path.suffix} files."))
    else:
        label = app_config[app_name].get("label", app_name)
        version = apps.saved_version(app_config[app_name], path)
        installed = [i.version for i in installations.get(app_name, [])]
        if version and version in installed:
            checks.append(Check("Software version", PASS, f"Saved with {label} {version}"))
        elif version:
            checks.append(Check("Software version", WARN,
                                f"Saved with {label} {version}, which isn't installed on this computer."))
        else:
            checks.append(Check("Software version", WARN, f"Couldn't tell which {label} version saved it."))

    # 4. Is it the newest work file?
    newest = project.list_workfiles(entity, task, workfile.part)[-1]
    if newest.path != workfile.path and newest.version >= workfile.version:
        checks.append(Check("Latest version", WARN,
                            f"{newest.path.name} is newer. Publishing an older one?"))
    else:
        checks.append(Check("Latest version", PASS, f"{workfile.version_label} is the newest work file"))

    # 5. Not a repeat of an earlier publish.
    publishes = project.list_publishes(entity, task, workfile.part)
    source = path.relative_to(project.root).as_posix()
    repeat = next((p for p in reversed(publishes) if p.info.get("source") == source), None)
    if repeat and repeat.info.get("time", "") >= saved.isoformat(timespec="seconds"):
        checks.append(Check("Changed since last publish", WARN,
                            f"This file was already published as v{repeat.version:03d} and hasn't changed."))
    else:
        checks.append(Check("Changed since last publish", PASS,
                            "New changes to publish" if publishes else "First publish of this step"))
    return checks


def summary(checks: List[Check]) -> str:
    counts = {state: sum(1 for c in checks if c.state == state) for state in (PASS, WARN, FAIL)}
    parts = [f"{counts[PASS]} passed"]
    if counts[WARN]:
        parts.append(f"{counts[WARN]} warning" + ("s" if counts[WARN] > 1 else ""))
    if counts[FAIL]:
        parts.append(f"{counts[FAIL]} failed")
    return " · ".join(parts)
