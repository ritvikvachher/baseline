"""The project on disk: assets, shots, workfiles and publishes.

Folder names come from the "layout" section of baseline_project.json, e.g.

    assets/<type>/<name>/<task>/wip/<name>_<task>_v001_<user>.hip
    assets/<type>/<name>/<task>/published/v001/<name>_<task>_v001.hip
    shots/<shot>/<task>/...          ("sequences": true -> shots/<sequence>/<shot>/<task>/...)

Any folder inside an asset type (or shots) folder is an asset (or shot), and any
folder inside that with a work or publish folder is one of its tasks, so folders
made by hand in Explorer show up too.

Tracking data (status, assignee, frame ranges, publish notes) goes under
"tracking" (e.g. _ADMIN/baseline), mirroring the asset/shot paths, so the
artists' folders only hold their files. Projects without a "layout" use
BASELINE's original layout: work/, publish/, sequences, and tracking files
next to the work.

Workfiles are private and versioned; publishes are numbered, read-only copies
that other departments load from.
"""
from __future__ import annotations

import json
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PUBLISH_DIR_RE = re.compile(r"^v(\d{3,})$")
PROJECT_MARKER = "baseline_project.json"
ENTITY_FILE = "entity.json"
TASK_FILE = "task.json"

# Task statuses, in the order they're offered, with how far along each one counts as.
STATUSES = ["Not started", "In progress", "In review", "Approved", "Blocked"]
STATUS_PROGRESS = {"Not started": 0.0, "In progress": 0.4, "Blocked": 0.4, "In review": 0.8, "Approved": 1.0}
PUBLISH_FILE = "publish.json"
KIND_FOLDERS = {"asset": "assets", "shot": "shots"}  # kind -> its key in the layout

DEFAULT_LAYOUT = {           # BASELINE's original layout
    "assets": "assets",
    "shots": "shots",
    "work": "work",
    "publish": "publish",
    "sequences": True,       # shots/<sequence>/<shot> rather than shots/<shot>
    "tracking": "",          # "" = next to the work (<task>/task.json)
    "shot_render": "",       # e.g. "render/beauty": where BASELINE sends a shot's renders
    "shot_folders": [],      # extra folders made with each new shot, e.g. "render/depth"
}
# Files in a work folder that count as scenes (the project can override this list).
SCENE_EXTENSIONS = [".hip", ".hipnc", ".hiplc", ".ma", ".mb", ".blend", ".nk", ".spp", ".ztl", ".c4d"]
# Autosaves and backups that sit next to scenes, and temp files starting with ~ or .
IGNORED_FILE_RE = re.compile(r"(_autosave|_bak\d*)\.[^.]+$|^[~.]", re.IGNORECASE)
# The version in a freely named file, e.g. cheetah_rig_v12_final -> 12.
VERSION_RE = re.compile(r"(?:^|[_.-])v(\d{2,})(?=[_.-]|$)", re.IGNORECASE)
# Parts of a step (e.g. geo: skeleton, muscle, skin): lowercase letters and numbers only, since
# "_" separates the pieces of a file name (cheetah_geo_muscle_v004_liangyi.ma).
PART_RE = re.compile(r"^[a-z][a-z0-9]*$")


class PipelineError(Exception):
    """An error meant to be shown to the artist (bad name, missing file, ...)."""


@dataclass(frozen=True)
class Entity:
    kind: str   # "asset" or "shot"
    group: str  # asset type or sequence ("" for shots when the project has no sequences)
    name: str
    path: Path

    @property
    def label(self) -> str:
        return f"{self.group}/{self.name}" if self.group else self.name

    @property
    def display_name(self) -> str:
        return f"{self.group}_{self.name}" if self.kind == "shot" and self.group else self.name


@dataclass(frozen=True)
class Workfile:
    path: Path
    version: int  # 0 when the file name has no version
    user: str     # "" when the file isn't named <name>_<task>_vNNN_<user>
    part: str = ""  # e.g. "muscle" for <name>_<task>_muscle_vNNN_<user>; "" for the step as a whole

    @property
    def version_label(self) -> str:
        return f"v{self.version:03d}" if self.version else "no version"


@dataclass(frozen=True)
class Publish:
    path: Path  # the vNNN folder
    version: int
    info: dict


def validate_part(value: str) -> str:
    value = value.strip().lower()
    if not PART_RE.match(value) or PUBLISH_DIR_RE.match(value) or re.match(r"^v\d+$", value):
        raise PipelineError(f"Part '{value}' is not valid.\nUse lowercase letters and numbers, starting with a "
                            "letter (e.g. skeleton, muscle, skin).")
    return value


def validate_name(value: str, what: str) -> str:
    if not NAME_RE.match(value):
        raise PipelineError(
            f"{what} '{value}' is not valid.\n"
            "Use letters, numbers and underscores, starting with a letter."
        )
    return value


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _make_read_only(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _subdirs(path: Path) -> List[Path]:
    """Folders inside `path`, skipping ones starting with _ or . (e.g. _old, .git)."""
    if not path.is_dir():
        return []
    return sorted((p for p in path.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))),
                  key=lambda p: p.name.lower())


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class Project:
    def __init__(self, root, config: dict = None):
        """`root` is how this computer reaches the project (e.g. X:/), `config` defaults
        to the project's own baseline_project.json."""
        self.root = Path(root)
        self.config = config if config is not None else _read_json(self.root / PROJECT_MARKER)
        if not self.config:
            raise PipelineError(f"{self.root} is not a BASELINE project (no {PROJECT_MARKER}).")
        self.name = self.config.get("name", self.root.name)
        self.layout = dict(DEFAULT_LAYOUT, **self.config.get("layout", {}))
        tracking = self.layout["tracking"]
        self.tracking_root = self.root / tracking if tracking else None

    @classmethod
    def create(cls, root, template: dict, name: str = "") -> "Project":
        """Turn a folder into a BASELINE project. Existing files are left alone."""
        root = Path(root)
        config = dict(template, name=name or root.name)
        root.mkdir(parents=True, exist_ok=True)
        _write_json(root / PROJECT_MARKER, config)
        project = cls(root, config)
        project.ensure_structure()
        return project

    def save_config(self, **changes) -> None:
        """Update the shared baseline_project.json (e.g. the delivery date)."""
        self.config.update(changes)
        _write_json(self.root / PROJECT_MARKER, self.config)

    @property
    def logs_dir(self) -> Path:
        return (self.tracking_root or self.root) / "logs"

    # --- Visits -----------------------------------------------------------
    # When each person last had BASELINE open, for the dashboard greeting. Kept with the
    # project rather than in %APPDATA% so it follows people between lab PCs; one file each
    # so OneDrive never has two people editing the same file.

    def _visit_file(self, user: str) -> Path:
        return (self.tracking_root or self.root) / "users" / f"{user}.json"

    def start_visit(self, user: str) -> Optional[datetime]:
        """Record that `user` has BASELINE open now; returns when they last did (None = never)."""
        try:
            last = datetime.fromisoformat(_read_json(self._visit_file(user))["last_seen"])
        except (KeyError, TypeError, ValueError, OSError):
            last = None
        self.mark_seen(user)
        return last

    def mark_seen(self, user: str) -> None:
        try:
            _write_json(self._visit_file(user), {"last_seen": _now()})
        except OSError:
            pass  # a read-only or offline project shouldn't stop BASELINE opening

    @property
    def uses_sequences(self) -> bool:
        return bool(self.layout["sequences"])

    def kind_dir(self, kind: str) -> Path:
        """The assets or shots folder."""
        return self.root / self.layout[KIND_FOLDERS[kind]]

    def _depth(self, kind: str) -> int:
        """Folders from the assets/shots folder down to an asset or shot: type/name, sequence/shot or shot."""
        return 2 if kind == "asset" or self.uses_sequences else 1

    def context_for_path(self, path):
        """(entity, task) for a file inside the assets or shots folder, or (None, None)."""
        for kind in KIND_FOLDERS:
            try:
                parts = Path(path).relative_to(self.kind_dir(kind)).parts
            except ValueError:
                continue
            depth = self._depth(kind)
            if len(parts) <= depth or any(p.startswith(("_", ".")) for p in parts[:depth]):
                return None, None
            entity_path = self.kind_dir(kind).joinpath(*parts[:depth])
            if not entity_path.is_dir():
                return None, None
            entity = Entity(kind, parts[0] if depth == 2 else "", parts[depth - 1], entity_path)
            task = parts[depth] if parts[depth] in self.entity_tasks(entity) else None
            return entity, task
        return None, None

    @property
    def asset_types(self) -> List[str]:
        """The project's asset types, plus any other type folders made by hand."""
        types = list(self.config.get("asset_types", []))
        return types + [d.name for d in _subdirs(self.kind_dir("asset")) if d.name not in types]

    def tasks_for(self, kind: str) -> List[str]:
        """The project's steps for assets or shots, in pipeline order (new ones get these folders)."""
        return list(self.config["tasks"][kind])

    def entity_tasks(self, entity: Entity) -> List[str]:
        """The steps this asset/shot has folders for, in pipeline order; the project's list if none yet."""
        work, publish = self.layout["work"], self.layout["publish"]
        found = [d.name for d in _subdirs(entity.path) if (d / work).is_dir() or (d / publish).is_dir()]
        order = self.tasks_for(entity.kind)
        found.sort(key=lambda t: (order.index(t) if t in order else len(order), t))
        return found or order

    def all_tasks(self, kind: str) -> List[str]:
        """Every step used by any asset (or shot): the project's list, then extra ones found on disk."""
        tasks = self.tasks_for(kind)
        for entity in self.list_entities(kind):
            tasks += [t for t in self.entity_tasks(entity) if t not in tasks]
        return tasks

    def ensure_structure(self) -> None:
        for kind in KIND_FOLDERS:
            self.kind_dir(kind).mkdir(parents=True, exist_ok=True)

    # --- Tracking files ---------------------------------------------------

    def _meta_dir(self, entity: Entity) -> Path:
        if self.tracking_root is None:
            return entity.path
        return self.tracking_root / entity.path.relative_to(self.root)

    def _task_file(self, entity: Entity, task: str) -> Path:
        return self._meta_dir(entity) / task / TASK_FILE

    def _publish_info_file(self, entity: Entity, task: str, folder: Path) -> Path:
        if self.tracking_root is None:
            return folder / PUBLISH_FILE
        publishes = self._meta_dir(entity) / task / "publishes"
        part = folder.parent.name if folder.parent != self.publish_dir(entity, task) else ""
        return (publishes / part if part else publishes) / f"{folder.name}.json"

    def _part_file(self, entity: Entity, task: str, part: str) -> Path:
        return self._meta_dir(entity) / task / "parts" / f"{part}.json"

    # --- Assets and shots -------------------------------------------------

    def list_entities(self, kind: str) -> List[Entity]:
        base = self.kind_dir(kind)
        if self._depth(kind) == 1:
            return [Entity(kind, "", d.name, d) for d in _subdirs(base)]
        return [Entity(kind, group.name, d.name, d) for group in _subdirs(base) for d in _subdirs(group)]

    def _create_entity(self, kind: str, group: str, name: str, info: dict) -> Entity:
        path = (self.kind_dir(kind) / group if group else self.kind_dir(kind)) / name
        entity = Entity(kind, group, name, path)
        if path.exists():
            raise PipelineError(f"{kind.title()} {entity.label} already exists.")
        for task in self.tasks_for(kind):
            for folder in (self.layout["work"], self.layout["publish"]):
                (path / task / folder).mkdir(parents=True, exist_ok=True)
        if kind == "shot":
            for folder in self.layout["shot_folders"]:
                (path / folder).mkdir(parents=True, exist_ok=True)
        _write_json(self._meta_dir(entity) / ENTITY_FILE,
                    {"kind": kind, "group": group, "name": name, "created": _now(), **info})
        return entity

    def create_asset(self, asset_type: str, name: str, description: str = "", user: str = "") -> Entity:
        if asset_type not in self.asset_types:
            raise PipelineError(f"Unknown asset type '{asset_type}'.")
        validate_name(name, "Asset name")
        return self._create_entity("asset", asset_type, name, {"description": description, "created_by": user})

    def create_shot(self, sequence: str, shot: str, frame_start: int, frame_end: int,
                    description: str = "", user: str = "") -> Entity:
        """`sequence` is ignored when the project has no sequences."""
        if self.uses_sequences:
            validate_name(sequence, "Sequence name")
        else:
            sequence = ""
        validate_name(shot, "Shot name")
        if frame_end < frame_start:
            raise PipelineError("The end frame must come after the start frame.")
        return self._create_entity("shot", sequence, shot, {
            "frame_start": frame_start,
            "frame_end": frame_end,
            "description": description,
            "created_by": user,
        })

    def entity_info(self, entity: Entity) -> dict:
        return _read_json(self._meta_dir(entity) / ENTITY_FILE)

    def update_entity_info(self, entity: Entity, user: str, **changes) -> dict:
        info = self.entity_info(entity)
        info.update(changes)
        if info.get("frame_start") is not None and info.get("frame_end") is not None \
                and info["frame_end"] < info["frame_start"]:
            raise PipelineError("The end frame must come after the start frame.")
        info.update(kind=entity.kind, group=entity.group, name=entity.name, updated=_now(), updated_by=user)
        _write_json(self._meta_dir(entity) / ENTITY_FILE, info)
        return info

    def frame_range(self, entity: Entity) -> Optional[Tuple[int, int]]:
        """A shot's (first, last) frame, or None if it hasn't been set."""
        info = self.entity_info(entity)
        start, end = info.get("frame_start"), info.get("frame_end")
        return (start, end) if isinstance(start, int) and isinstance(end, int) else None

    def shot_render_dir(self, entity: Entity) -> Optional[Path]:
        folder = self.layout["shot_render"]
        return entity.path / folder if entity.kind == "shot" and folder else None

    def render_output(self, entity: Optional[Entity], scene: Path) -> Optional[str]:
        """Where BASELINE sends a shot's renders, one folder per scene version:
        <shot>/render/beauty/<scene name>/<scene name>.$F4.exr. None if the project doesn't set it."""
        folder = self.shot_render_dir(entity) if entity else None
        if folder is None:
            return None
        return (folder / scene.stem / f"{scene.stem}.$F4.exr").as_posix()

    # --- Workfiles --------------------------------------------------------

    def work_dir(self, entity: Entity, task: str) -> Path:
        return entity.path / task / self.layout["work"]

    def publish_dir(self, entity: Entity, task: str) -> Path:
        return entity.path / task / self.layout["publish"]

    def list_workfiles(self, entity: Entity, task: str, part: Optional[str] = None) -> List[Workfile]:
        """Every scene file in the work folder, oldest version first. Files named by BASELINE
        (hero_model_v003_ritvik.hip, or hero_model_muscle_v003_ritvik.ma for a part) carry their
        version, artist and part; others are listed too. `part` keeps only that part's files
        ("" = files that aren't in a part); None keeps everything."""
        folder = self.work_dir(entity, task)
        if not folder.is_dir():
            return []
        ours = re.compile(rf"^{re.escape(entity.name)}_{re.escape(task)}_(?:([a-z][a-z0-9]*)_)?v(\d{{3,}})_([a-z0-9]+)$",
                          re.IGNORECASE)
        extensions = {e.lower() for e in self.config.get("scene_extensions", SCENE_EXTENSIONS)}
        workfiles = []
        for path in folder.iterdir():
            if path.suffix.lower() not in extensions or IGNORED_FILE_RE.search(path.name) or not path.is_file():
                continue
            match = ours.match(path.stem)
            if match:
                found = Workfile(path, int(match.group(2)), match.group(3), (match.group(1) or "").lower())
            else:
                versions = VERSION_RE.findall(path.stem)
                found = Workfile(path, int(versions[-1]) if versions else 0, "")
            if part is None or found.part == part:
                workfiles.append(found)
        return sorted(workfiles, key=lambda w: (w.version, _mtime(w.path), w.path.name))

    def next_version(self, entity: Entity, task: str, part: str = "") -> int:
        """Versions are shared by the whole task (or, in a part, by that part), whoever saved them."""
        workfiles = self.list_workfiles(entity, task, part if part else None)
        return max(w.version for w in workfiles) + 1 if workfiles else 1

    def _file_stem(self, entity: Entity, task: str, part: str, version: int) -> str:
        return f"{entity.name}_{task}_{part + '_' if part else ''}v{version:03d}"

    def new_workfile_path(self, entity: Entity, task: str, extension: str, user: str, part: str = "") -> Path:
        if not extension.startswith("."):
            extension = "." + extension
        version = self.next_version(entity, task, part)
        return self.work_dir(entity, task) / f"{self._file_stem(entity, task, part, version)}_{user}{extension}"

    def _copy_to_new_version(self, source: Path, entity: Entity, task: str, user: str, part: str = "") -> Workfile:
        version = self.next_version(entity, task, part)
        target = self.work_dir(entity, task) / f"{self._file_stem(entity, task, part, version)}_{user}{source.suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return Workfile(target, version, user, part)

    def version_up(self, workfile: Workfile, entity: Entity, task: str, user: str) -> Workfile:
        """Copy a workfile to the next free version (of its part), saved under the current user."""
        if not workfile.path.is_file():
            raise PipelineError(f"File not found:\n{workfile.path}")
        return self._copy_to_new_version(workfile.path, entity, task, user, workfile.part)

    def add_existing_file(self, source, entity: Entity, task: str, user: str, part: str = "") -> Workfile:
        """Bring a scene made outside the pipeline in as the next version (of `part`, if given)."""
        source = Path(source)
        if not source.is_file():
            raise PipelineError(f"File not found:\n{source}")
        return self._copy_to_new_version(source, entity, task, user, part)

    # --- Parts --------------------------------------------------------------
    # A step can be split into parts worked on separately (geo: skeleton, muscle, skin), each
    # with its own versions, publishes, status and assignee. A step without parts works as before.

    def list_parts(self, entity: Entity, task: str) -> List[str]:
        """The step's parts: added ones, plus any found in work file names or publish folders."""
        found = set()
        folder = self._part_file(entity, task, "x").parent
        if folder.is_dir():
            found.update(p.stem for p in folder.glob("*.json") if PART_RE.match(p.stem))
        found.update(w.part for w in self.list_workfiles(entity, task) if w.part)
        found.update(d.name for d in _subdirs(self.publish_dir(entity, task))
                     if PART_RE.match(d.name) and not PUBLISH_DIR_RE.match(d.name))
        info = {p: self.part_info(entity, task, p) for p in found}
        # In the order they were added (then by name), so the chips don't jump around.
        return sorted(found, key=lambda p: (info[p].get("added", "~"), p))

    def add_part(self, entity: Entity, task: str, part: str, user: str) -> str:
        part = validate_part(part)
        if part in self.list_parts(entity, task):
            raise PipelineError(f"{task} already has a part called {part}.")
        self.update_part(entity, task, part, user, added=datetime.now().isoformat())  # exact, for the order
        return part

    def part_info(self, entity: Entity, task: str, part: str) -> dict:
        info = _read_json(self._part_file(entity, task, part))
        info.setdefault("status", "Not started")
        info.setdefault("assignee", "")
        return info

    def update_part(self, entity: Entity, task: str, part: str, user: str, **changes) -> dict:
        """Like update_task, for one part. Each part has its own small file (no OneDrive clashes)."""
        if "status" in changes and changes["status"] not in STATUSES:
            raise PipelineError(f"Unknown status '{changes['status']}'.")
        info = self.part_info(entity, task, part)
        info.update(changes)
        info["updated"] = _now()
        info["updated_by"] = user
        _write_json(self._part_file(entity, task, part), info)
        return info

    def part_of(self, entity: Entity, task: str, path) -> str:
        """The part a work file belongs to, from its name ("" if none)."""
        match = next((w for w in self.list_workfiles(entity, task) if w.path == Path(path)), None)
        return match.part if match else ""

    # --- Publishes --------------------------------------------------------

    def list_publishes(self, entity: Entity, task: str, part: str = "") -> List[Publish]:
        """The step's publishes, or a part's (published/<part>/vNNN)."""
        folder = self.publish_dir(entity, task) / part if part else self.publish_dir(entity, task)
        if not folder.is_dir():
            return []
        publishes = []
        for path in folder.iterdir():
            match = PUBLISH_DIR_RE.match(path.name)
            if match and path.is_dir():
                info = _read_json(self._publish_info_file(entity, task, path))
                publishes.append(Publish(path, int(match.group(1)), info))
        return sorted(publishes, key=lambda p: p.version)

    def publish(self, workfile: Workfile, entity: Entity, task: str, user: str, comment: str = "") -> Publish:
        if not workfile.path.is_file():
            raise PipelineError(f"File not found:\n{workfile.path}")
        part = workfile.part
        publishes = self.list_publishes(entity, task, part)
        version = publishes[-1].version + 1 if publishes else 1
        base = self.publish_dir(entity, task) / part if part else self.publish_dir(entity, task)
        folder = base / f"v{version:03d}"
        try:
            # exist_ok=False so two people publishing at once can't share a version.
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise PipelineError(f"Publish v{version:03d} was just created by someone else. Try again.")
        target = folder / f"{self._file_stem(entity, task, part, version)}{workfile.path.suffix}"
        shutil.copy2(workfile.path, target)
        info = {
            "version": version,
            "user": user,
            "time": _now(),
            "comment": comment,
            "source": workfile.path.relative_to(self.root).as_posix(),
            "files": [target.name],
        }
        if part:
            info["part"] = part
        _write_json(self._publish_info_file(entity, task, folder), info)
        _make_read_only(target)
        return Publish(folder, version, info)

    def all_publishes(self, limit: int = 20):
        """The most recent publishes across the whole project: [(entity, task, Publish)], newest first."""
        found = []
        for kind in KIND_FOLDERS:
            for entity in self.list_entities(kind):
                for task in self.entity_tasks(entity):
                    for part in [""] + self.list_parts(entity, task):
                        for pub in self.list_publishes(entity, task, part):
                            found.append((entity, task, pub))
        found.sort(key=lambda item: item[2].info.get("time", ""), reverse=True)
        return found[:limit]

    # --- Task status ------------------------------------------------------
    # Each task keeps its own small task.json, so two people updating different
    # tasks never edit the same file (which OneDrive would turn into a conflict).

    def task_info(self, entity: Entity, task: str) -> dict:
        info = _read_json(self._task_file(entity, task))
        info.setdefault("status", "Not started")
        info.setdefault("assignee", "")
        return info

    def update_task(self, entity: Entity, task: str, user: str, **changes) -> dict:
        if "status" in changes and changes["status"] not in STATUSES:
            raise PipelineError(f"Unknown status '{changes['status']}'.")
        info = self.task_info(entity, task)
        info.update(changes)
        info["updated"] = _now()
        info["updated_by"] = user
        _write_json(self._task_file(entity, task), info)
        return info

    def current_step(self, entity: Entity):
        """The task that's furthest along the pipeline and has been started, or the first task."""
        tasks = self.entity_tasks(entity)
        started = [t for t in tasks if self.task_info(entity, t)["status"] != "Not started"]
        return started[-1] if started else tasks[0]

    def latest_version(self, entity: Entity, task: str) -> int:
        workfiles = self.list_workfiles(entity, task)
        return workfiles[-1].version if workfiles else 0

    def progress(self):
        """(whole film 0-1, {task: 0-1}) from task statuses."""
        per_task: Dict[str, List[float]] = {}
        for kind in KIND_FOLDERS:
            for entity in self.list_entities(kind):
                for task in self.entity_tasks(entity):
                    status = self.task_info(entity, task)["status"]
                    per_task.setdefault(task, []).append(STATUS_PROGRESS[status])
        everything = [v for values in per_task.values() for v in values]
        film = sum(everything) / len(everything) if everything else 0.0
        return film, {task: sum(v) / len(v) for task, v in per_task.items()}

    # --- Launch context ---------------------------------------------------

    def context_env(self, entity: Optional[Entity], task: Optional[str], user: str) -> Dict[str, str]:
        """Environment variables handed to Houdini/Maya/... so tools inside know the context.
        Without an entity and task, only the project-wide ones are set."""
        env = {
            "BASELINE_ROOT": str(self.root),
            "BASELINE_PROJECT": self.name,
            "BASELINE_USER": user,
            "BASELINE_FPS": str(self.config.get("fps", 24)),
            "JOB": self.root.as_posix().rstrip("/"),  # Houdini's project variable: $JOB/assets -> X:/assets
        }
        if entity is None or task is None:
            return env
        env.update({
            "BASELINE_KIND": entity.kind,
            "BASELINE_GROUP": entity.group,
            "BASELINE_ENTITY": entity.name,
            "BASELINE_TASK": task,
            "BASELINE_WORKDIR": str(self.work_dir(entity, task)),
        })
        if entity.kind == "shot":
            frames = self.frame_range(entity)
            env["BASELINE_FRAME_START"] = str(frames[0]) if frames else ""
            env["BASELINE_FRAME_END"] = str(frames[1]) if frames else ""
        return env
