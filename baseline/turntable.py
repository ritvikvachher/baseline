"""Turntables: a standard spinning render of an asset, made on request for dailies.

Nothing here starts by itself: someone clicks Turntable... on an asset, and a job joins the
render queue. The job runs this file as its own process,

    python -m baseline.turntable <spec.json>

which takes it through the stages, each in the program that can do it:

    geo (.ma/.mb):  mayapy exports the OUT_BASELINE_geo set/group to USD (maya/export_geo.py)
                    hython puts it in the team's turntable template (hython/turntable_from_template.py),
                    or, with no template set, a plain studio        (hython/turntable_scene.py)
                    husk renders it with Karma (EXR frames)
                    hoiiotool turns the frames' colour into display colour (OCIO), dropping alpha
                    Windows draws the slate, Houdini's ffmpeg makes the MP4
    lookdev (.hip): hython exports the stage at the OUT_BASELINE_lookdev node to USD, shaded
                    (hython/export_lookdev.py), then the same as geo without the clay; in the
                    template the asset spins once, then the HDRI does

and reports progress with BASELINE:: lines, like a normal render. The working files go in
the computer's temp folder (not OneDrive) and are deleted when the MP4 is saved.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from . import settings, slate
from .project import Entity, Project, Publish, PipelineError
from .render import MARK, RenderJob, log_path_for, parse_message

PACKAGE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = settings.CONFIG_DIR / "turntable.json"
# Not %LOCALAPPDATA%: the Microsoft Store Python hides folders it makes there from other
# programs (Maya, Houdini), and those need to read and write the job's files.
WORK_ROOT = Path(tempfile.gettempdir()) / "baseline_turntables"
LOGO = PACKAGE_DIR / "gui" / "assets" / "logo_2x.png"
GEO_SET = "OUT_BASELINE_geo"
# Steps whose files are models: retopo and UVs are part of geo in this project.
GEO_TASKS = {"geo", "model", "modelling", "modeling", "retopo", "uv", "uvs"}
MAYA_EXTENSIONS = {".ma", ".mb"}
LOOKDEV_NODE = "OUT_BASELINE_lookdev"
# Steps whose Houdini files hold the shaded asset: texturing is done in Houdini in this project.
LOOKDEV_TASKS = {"lookdev", "look", "lookdevelopment", "shading", "surfacing", "texturing", "texture", "textures"}
HOUDINI_EXTENSIONS = {".hip", ".hipnc", ".hiplc"}
DEFAULTS = {
    "frames": 120,
    "fps": 24,
    "resolution": [1280, 720],
    "samples": 64,
    "engine": "xpu",
    "focal_mm": 50,
    "elevation_deg": 12,
    "start_angle": 30,
    "margin": 1.1,
    # The team's Houdini turntable scene (see hython/make_turntable_template.py). Empty: use
    # BASELINE's own plain studio instead, lit by "hdri".
    "template": "_ADMIN/pipeline/turntable/turntable_template.hipnc",
    "clay_material": "clay",
    "hdri": "_ADMIN/pipeline/turntable/studio_small_08_4k.exr",
    "hdri_intensity": 1.0,
    "scale_figure": True,
    "clay_color": [0.5, 0.5, 0.5],
    "backdrop_color": [0.18, 0.18, 0.19],
    "figure_color": [0.08, 0.08, 0.09],
    "slate_seconds": 1,
    "bitrate": "8M",
    # How the linear EXR frames become MP4 colour. Empty config: $OCIO if it's set, else the
    # config that comes with Houdini. Empty display/view: that config's defaults, which is what
    # Houdini's viewer shows unless someone changes it.
    "ocio_config": "",
    "display": "",
    "view": "",
    # Lookdev in the template: after the asset's spin, the HDRI spins once (same number of frames).
    "lookdev_hdri_spin": True,
}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_settings() -> dict:
    """config/turntable.json on top of the defaults, so the file only needs what's changed."""
    return dict(DEFAULTS, **(settings.read_json(SETTINGS_FILE, {}) or {}))


def parse_overrides(frames: str, samples: str) -> dict:
    """The Frames and Samples boxes from the Turntable dialog, checked."""
    try:
        values = {"frames": int(str(frames).strip()), "samples": int(str(samples).strip())}
    except ValueError:
        raise PipelineError("Frames and samples must be whole numbers.")
    if not 1 <= values["frames"] <= 1000:
        raise PipelineError("Frames must be between 1 and 1000.")
    if not 1 <= values["samples"] <= 4096:
        raise PipelineError("Samples must be between 1 and 4096.")
    return values


def kind_for(task: str, source: Path) -> Optional[str]:
    """Which kind of turntable this step's file gets, or None if it can't have one (yet)."""
    if task.lower() in GEO_TASKS and source.suffix.lower() in MAYA_EXTENSIONS:
        return "geo"
    if task.lower() in LOOKDEV_TASKS and source.suffix.lower() in HOUDINI_EXTENSIONS:
        return "lookdev"
    return None


def has_turntables(task: str) -> bool:
    return task.lower() in GEO_TASKS | LOOKDEV_TASKS


def hdri_spin_frames(kind: str, config: dict) -> int:
    """Extra frames after the asset's spin while the HDRI turns (lookdev in the template only)."""
    if kind == "lookdev" and config.get("template") and config.get("lookdev_hdri_spin", True):
        return int(config["frames"])
    return 0


def publish_source(publish: Publish) -> Optional[Path]:
    """The scene file inside a publish folder."""
    for name in publish.info.get("files", []):
        if (publish.path / name).is_file():
            return publish.path / name
    return next((p for p in sorted(publish.path.iterdir()) if p.suffix.lower() in MAYA_EXTENSIONS |
                 HOUDINI_EXTENSIONS), None) if publish.path.is_dir() else None


def output_path(project: Project, entity: Entity, task: str, source: Path,
                publish: Optional[Publish] = None) -> Path:
    """published/v004/turntable/cheetah_geo_v004_turntable.mp4 for a publish (for a part:
    published/muscle/v002/turntable/cheetah_geo_muscle_v002_turntable.mp4), or
    wip/turntables/<work file>_turntable.mp4 for a test from a work file."""
    if publish is not None:
        part = publish.info.get("part", "")
        return publish.path / "turntable" /             f"{entity.name}_{task}_{part + '_' if part else ''}v{publish.version:03d}_turntable.mp4"
    return project.work_dir(entity, task) / "turntables" / f"{source.stem}_turntable.mp4"


def find_turntable(project: Project, entity: Entity, task: str, publish: Publish) -> Optional[Path]:
    path = output_path(project, entity, task, Path(""), publish)
    return path if path.is_file() else None


@dataclass
class TurntableJob(RenderJob):
    """A render queue job that runs the turntable stages instead of a hip file's node."""
    spec_file: Optional[Path] = None
    stage: str = "Waiting to start"

    @property
    def frames_label(self) -> str:
        return f"Turntable · {self.stage}"

    @property
    def info_label(self) -> str:
        return self.frames_label

    def command(self) -> List[str]:
        return [sys.executable, "-m", "baseline.turntable", str(self.spec_file)]


def _project_path(project: Project, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project.root / path


def houdini_ocio_config(houdini_bin: Optional[Path]) -> Optional[Path]:
    """The colour config Houdini uses by itself: $OCIO, else the one in its install."""
    if os.environ.get("OCIO"):
        return Path(os.environ["OCIO"])
    if houdini_bin:
        found = sorted((Path(houdini_bin).parent / "packages" / "ocio").glob("houdini-config-*.ocio"))
        if found:
            return found[-1]
    return None


def pick_ocio_config(configured: str, houdini_bin: Optional[Path]):
    """(colour config to use, note). The team's config (e.g. the lab's S:/tools/OCIO/BU_config.ocio)
    when this PC can reach it; otherwise Houdini's own, with a note saying so, so turntables
    still work off the lab network."""
    if configured and Path(configured).is_file():
        return configured, ""
    fallback = houdini_ocio_config(houdini_bin)
    fallback = str(fallback) if fallback else ""
    if not configured:
        return fallback, ""
    return fallback, (f"The colour config {configured} isn't reachable on this PC, so Houdini's own was used "
                      "(colours may differ a little from the lab's).")


def _clean_old_work(days: int = 3) -> None:
    """Leftovers from failed or stopped turntables (frames can be hundreds of MB)."""
    if not WORK_ROOT.is_dir():
        return
    cutoff = time.time() - timedelta(days=days).total_seconds()
    for folder in WORK_ROOT.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def create_job(project: Project, entity: Entity, task: str, source: Path, publish: Optional[Publish],
               user: str, mayapy: Optional[Path], maya_version: str, houdini_bin: Optional[Path],
               houdini_version: str, overrides: Optional[dict] = None) -> TurntableJob:
    """Checks everything needed is there and writes the job's spec. Raises PipelineError with
    a message for the artist if not. `overrides` changes settings for this job only, e.g.
    {"frames": 96, "samples": 32} from the Turntable dialog."""
    kind = kind_for(task, source)
    if kind is None:
        raise PipelineError("Turntables are made from Maya files in the geo step, or Houdini files "
                            "in the lookdev step.")
    if not source.is_file():
        raise PipelineError(f"Can't find the file:\n{source}")
    if kind == "geo" and (not mayapy or not Path(mayapy).is_file()):
        raise PipelineError("Maya isn't installed on this computer (needed to export the model).")
    tools = {name: houdini_bin / f"{name}.exe" if houdini_bin else None for name in ("hython", "husk", "hffmpeg", "hoiiotool")}
    missing = [name for name, path in tools.items() if not path or not path.is_file()]
    if missing:
        raise PipelineError("Houdini isn't installed on this computer, or is missing "
                            + ", ".join(missing) + " (needed to render the turntable).")
    config = load_settings()
    config.update(overrides or {})
    template = hdri = None
    if config.get("template"):
        template = _project_path(project, config["template"])
        if not template.is_file():
            raise PipelineError(f"The turntable template is missing:\n{template}\n\nPut the team's "
                                f"turntable_template.hipnc there, or set \"template\" to \"\" in "
                                f"{SETTINGS_FILE.name} to use BASELINE's plain studio.")
    else:
        hdri = _project_path(project, config["hdri"])
        if not hdri.is_file():
            raise PipelineError(f"The turntable HDRI is missing:\n{hdri}\n\n"
                                f"Put it there, or change \"hdri\" in {SETTINGS_FILE.name}.")

    ocio, ocio_note = pick_ocio_config(
        str(_project_path(project, config["ocio_config"])) if config.get("ocio_config") else "", houdini_bin)

    _clean_old_work()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = WORK_ROOT / f"{stamp}_{entity.name}_{task}"
    work.mkdir(parents=True, exist_ok=True)
    out = output_path(project, entity, task, source, publish)
    info = publish.info if publish else {}
    version_text = f"v{publish.version:03d}" if publish else f"work file {source.stem}"
    spec = {
        "kind": kind,
        "source": str(source),
        "work_dir": str(work),
        "out_mp4": str(out),
        "template": str(template) if template else "",
        "hdri": str(hdri) if hdri else "",
        "ocio_config": ocio,
        "ocio_note": ocio_note,
        "logo": str(LOGO),
        "settings": config,
        "tools": {"mayapy": str(mayapy), **{k: str(v) for k, v in tools.items()}},
        "versions": {"maya": maya_version, "houdini": houdini_version},
        "context": {
            "project": project.name,
            "entity": entity.name,
            "group": entity.group,
            "task": task,
            "part": publish.info.get("part", "") if publish else project.part_of(entity, task, source),
            "version": publish.version if publish else None,
            "version_text": version_text,
            "artist": info.get("user") or user,
            "made_by": user,
            "computer": platform.node(),
            "published": info.get("time", ""),
            "notes": info.get("comment", ""),
        },
    }
    spec_file = work / "spec.json"
    spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    env = project.context_env(entity, task, user)
    env["PYTHONPATH"] = str(PACKAGE_DIR.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")
    return TurntableJob(
        hip=source, node=f"{task} turntable", start=1, end=config["frames"] + hdri_spin_frames(kind, config),
        step=1,
        hython=tools["hython"], houdini_label=houdini_version, env=env,
        log_path=log_path_for(project.logs_dir, user, source, "turntable"),
        output=str(out), spec_file=spec_file)


# --- On the render farm --------------------------------------------------------
# A turntable sent to the farm carries a "portable" copy of its spec: the same, but with paths
# relative to the project and nothing about the PC that sent it. The PC that renders it puts
# its own project folder, programs and temp folder back in (farm_job).

_PROJECT_PATHS = ("source", "out_mp4", "template", "hdri")


def portable_spec(spec: dict, root) -> dict:
    from . import farm
    portable = json.loads(json.dumps(spec))
    for key in _PROJECT_PATHS:
        portable[key] = farm.project_relative(root, portable.get(key, ""))
    # The team's config travels (project-relative if it's in the project, e.g. S:/... as it is);
    # Houdini's own (or $OCIO) is found again on each PC.
    configured = portable["settings"].get("ocio_config", "")
    portable["ocio_config"] = farm.project_relative(root, configured) if configured else ""
    portable.pop("ocio_note", None)
    for key in ("work_dir", "tools", "logo"):
        portable.pop(key, None)
    portable["context"].setdefault("requested", datetime.now().isoformat(timespec="seconds"))
    return portable


def farm_job(portable: dict, project: Project, user: str, houdini_bin: Path, houdini_version: str,
             mayapy: Optional[Path], maya_version: str, log_path: Path, work_name: str,
             frame_range=None, part_out: Optional[Path] = None, finish_parts=None) -> TurntableJob:
    """This PC's job for (a piece of) a farm turntable. Raises PipelineError if something it
    needs isn't on this PC."""
    from . import farm
    root = project.root
    spec = json.loads(json.dumps(portable))
    for key in _PROJECT_PATHS:
        spec[key] = farm.resolve(root, spec.get(key, ""))
    source = Path(spec["source"])
    if not finish_parts and not source.is_file():
        raise PipelineError(f"The scene isn't on this PC: {source}")
    if spec.get("template") and not finish_parts and not Path(spec["template"]).is_file():
        raise PipelineError(f"The turntable template isn't on this PC: {spec['template']}")
    tools = {name: Path(houdini_bin) / f"{name}.exe" for name in ("hython", "husk", "hffmpeg", "hoiiotool")}
    missing = [name for name, path in tools.items() if not path.is_file()]
    if missing:
        raise PipelineError("This PC's Houdini is missing " + ", ".join(missing) + ".")
    if spec["kind"] == "geo" and not finish_parts and (not mayapy or not Path(mayapy).is_file()):
        raise PipelineError("Maya isn't installed on this PC (needed to export the model).")
    spec["ocio_config"], spec["ocio_note"] = pick_ocio_config(
        farm.resolve(root, spec["ocio_config"]) if spec.get("ocio_config") else "", houdini_bin)

    _clean_old_work()
    work = WORK_ROOT / work_name
    shutil.rmtree(work, ignore_errors=True)  # a retry starts clean
    work.mkdir(parents=True, exist_ok=True)
    spec.update(work_dir=str(work), logo=str(LOGO),
                tools={"mayapy": str(mayapy or ""), **{k: str(v) for k, v in tools.items()}},
                versions={"maya": maya_version or spec.get("versions", {}).get("maya", ""),
                          "houdini": houdini_version})
    spec["context"]["computer"] = platform.node()
    spin = hdri_spin_frames(spec["kind"], dict(spec["settings"], template=spec.get("template")))
    total = spec["settings"]["frames"] + spin
    first, last = 1, total
    if frame_range:
        first, last = frame_range
        spec["frame_range"] = [first, last]
        spec["part_out"] = str(part_out)
    if finish_parts:
        spec["finish_parts"] = [str(p) for p in finish_parts]
    spec_file = work / "spec.json"
    spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    entity, task = project.context_for_path(source)
    env = project.context_env(entity, task, user)
    env["PYTHONPATH"] = str(PACKAGE_DIR.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")
    return TurntableJob(
        hip=source, node=f"{spec['context']['task']} turntable", start=first, end=last, step=1,
        hython=tools["hython"], houdini_label=houdini_version, env=env, log_path=log_path,
        output=spec["out_mp4"], spec_file=spec_file)


# --- The job itself (runs in its own process) ----------------------------------

def emit(kind, data=None):
    print(f"{MARK}{kind} {json.dumps(data or {})}", flush=True)


class StageFailed(Exception):
    pass


EXIT_GRACE_SECONDS = 30  # after a program says its work is done, how long it gets to close
LET_GO_SECONDS = 10      # ...and after closing it (or it closing), how long to wait for its output to end


def _run(cmd, cwd, on_line=None, work_done: threading.Event = None) -> int:
    """Runs a program, passing its output on to the log. `on_line(line)` can take a line
    (return True) so it isn't passed on.

    If `work_done` gets set (e.g. by on_line), BASELINE stops depending on the program: Maya with
    V-Ray/Arnold loaded can hang while shutting down after the work is finished, can't always be
    killed, and helper programs it started can keep its output open after it's gone. So it's
    closed after EXIT_GRACE_SECONDS, and LET_GO_SECONDS later BASELINE carries on regardless."""
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    try:
        process = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", bufsize=1, cwd=str(cwd),
                                   creationflags=_NO_WINDOW)
    except OSError as e:
        raise StageFailed(f"Couldn't start {Path(cmd[0]).name}: {e}")

    lines = queue.Queue()

    def read():
        for raw in process.stdout:
            lines.put(raw.rstrip("\n"))
        lines.put(None)  # the output ended

    threading.Thread(target=read, daemon=True).start()
    name = Path(cmd[0]).name
    done_at = closed_at = None
    while True:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            line = ""
        else:
            if line is None:
                break
            if on_line is None or not on_line(line):
                print(line, flush=True)
            continue
        if work_done is None or not work_done.is_set():
            continue
        now = time.time()
        done_at = done_at or now
        if closed_at is None and process.poll() is not None:
            closed_at = now  # it closed, but something still holds its output open
        if closed_at is None and now - done_at >= EXIT_GRACE_SECONDS:
            print(f"{name} finished its work but didn't close, so BASELINE closed it.", flush=True)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, creationflags=_NO_WINDOW)
            closed_at = now
        if closed_at is not None and now - closed_at >= LET_GO_SECONDS:
            print(f"{name}'s work is saved, so BASELINE carries on without waiting for it any longer.",
                  flush=True)
            code = process.poll()
            return -1 if code is None else code
    return process.wait()


def _stage(name):
    emit("STAGE", {"name": name})


def _size_text(size):
    x, y, z = size
    return f"{x:.2f} × {y:.2f} × {z:.2f} m  (width × height × length)"


def _model_text(stats):
    uvs = stats.get("meshes_with_uvs", 0)
    meshes = stats.get("meshes", 0)
    uv_text = "no UVs yet" if uvs == 0 else "UVs on every mesh" if uvs == meshes else f"UVs on {uvs} of {meshes} meshes"
    return f"{stats.get('triangles', 0):,} triangles · {meshes} meshes · {uv_text}"


def _when(context):
    """The date on the slate: the publish's, else when the turntable was asked for."""
    stamp = context.get("published") or context.get("requested")
    return datetime.fromisoformat(stamp) if stamp else datetime.now()


def _draw_slate(spec, work, stats, size):
    """slate.png (the card at the start) and overlay.png (the strip along the bottom)."""
    config, context = spec["settings"], spec["context"]
    lookdev = spec.get("kind") == "lookdev"
    when = _when(context)
    rows = [("Artist", context["artist"]), ("Date", f"{when:%a} {when.day} {when:%b %Y, %H:%M}"),
            ("Size", _size_text(size)), ("Model", _model_text(stats))]
    if context.get("notes"):
        rows.append(("Notes", context["notes"]))
    rows.append(("Source", Path(spec["source"]).name))
    version_text = context["version_text"]
    step = context["task"] + (f" · {context['part']}" if context.get("part") else "")
    slate_png, overlay_png = work / "slate.png", work / "overlay.png"
    try:
        slate.draw(
            slate_png, overlay_png, tuple(config["resolution"]), title=context["entity"].replace("_", " ").upper(),
            subtitle=f"{step.upper()} TURNTABLE  ·  {version_text}", rows=rows,
            corner_left=f"{context['entity']}  ·  {step}  ·  {version_text}  ·  {context['artist']}",
            corner_right=f"{when:%d %b %Y}",
            footer=f"BASELINE turntable  ·  {'' if lookdev else 'Maya ' + spec['versions']['maya'] + ' → '}"
                   f"Karma {config['engine'].upper()} (Houdini {spec['versions']['houdini']})  ·  "
                   f"made by {context['made_by']} on {context['computer']}",
            warning="" if stats.get("used_set") else f"No {GEO_SET} set or group in the scene, so every visible mesh was used.",
            logo=Path(spec["logo"]))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        raise StageFailed(str(e))
    return slate_png, overlay_png


def _copy_whole(src: Path, dest: Path) -> None:
    """Copies so the destination is either the old file or the whole new one, never half."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    shutil.copyfile(src, partial)
    os.replace(partial, dest)


def _save(spec, mp4, frames, spin_frames, size, stats, versions):
    """Saves the MP4 next to the publish (or work file), with a .json of its numbers."""
    config, context = spec["settings"], spec["context"]
    out = Path(spec["out_mp4"])
    _copy_whole(mp4, out)
    record = dict(context, kind=spec["kind"], source=spec["source"], made=datetime.now().isoformat(timespec="seconds"),
                  frames=frames, hdri_spin_frames=spin_frames, fps=config["fps"], size_m=size, stats=stats,
                  versions=versions,
                  colour={"config": spec.get("ocio_config", ""), "display": config["display"],
                          "view": config["view"], "note": spec.get("ocio_note", "")},
                  file=out.name)
    if spec.get("farm_job"):
        record["farm_job"] = spec["farm_job"]
    out.with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    emit("DONE", {"message": f"Saved {out.name}", "path": str(out)})


def run(spec: dict) -> int:
    """The whole turntable; or, on the farm, one piece of it:
    spec["frame_range"] + spec["part_out"]: render only those frames into a short MP4 piece;
    spec["finish_parts"]: put the slate and the pieces together into the turntable."""
    if spec.get("finish_parts"):
        return _finish(spec)
    work = Path(spec["work_dir"])
    tools, config = spec["tools"], spec["settings"]
    lookdev = spec.get("kind") == "lookdev"
    spin_frames = hdri_spin_frames(spec.get("kind", "geo"), dict(config, template=spec.get("template")))
    total = int(config["frames"]) + spin_frames
    first, last = spec.get("frame_range") or (1, total)
    frames = last - first + 1
    result = {}

    # 1. Export the asset: the model from Maya, or the shaded asset from Houdini.
    _stage("Exporting from Houdini" if lookdev else "Exporting from Maya")
    usd, stats_file = work / "asset.usd", work / "stats.json"

    exported = threading.Event()
    # No crash reporter / analytics helpers in the background Maya: they can outlive it.
    for var in ("MAYA_DISABLE_CER", "MAYA_DISABLE_CIP", "MAYA_DISABLE_CLIC_IPM", "MAYA_DISABLE_ADP"):
        os.environ.setdefault(var, "1")

    def from_export(line):
        kind, data = parse_message(line)
        if kind == "EXPORTED":
            result["export"] = data
            exported.set()
            return True
        return False

    if lookdev:
        code = _run([tools["hython"], PACKAGE_DIR / "hython" / "export_lookdev.py", spec["source"], usd,
                     stats_file], work, from_export, work_done=exported)
        if not usd.is_file() or (code != 0 and "export" not in result):
            raise StageFailed(f"Houdini couldn't export the asset at {LOOKDEV_NODE}. See the log.")
    else:
        code = _run([tools["mayapy"], PACKAGE_DIR / "maya" / "export_geo.py", spec["source"], usd, stats_file],
                    work, from_export, work_done=exported)
        if not usd.is_file() or (code != 0 and "export" not in result):
            raise StageFailed("Maya couldn't export the model. See the log for Maya's messages.")
    stats = json.loads(stats_file.read_text(encoding="utf-8"))

    # 2. Put it in the template (or BASELINE's plain studio).
    _stage("Building the scene")
    if spec.get("template"):
        script = "turntable_from_template.py"
        scene = work / "turntable.usdc"
        scene_spec = dict(config, template=spec["template"], asset_usd=str(usd), scene_usd=str(scene),
                          wrapper_usd=str(work / "asset_turntable.usda"), clay=not lookdev,
                          hdri_spin_frames=spin_frames)
    else:
        script = "turntable_scene.py"
        scene = work / "turntable.usda"
        scene_spec = dict(config, asset_usd=str(usd), scene_usd=str(scene), hdri=spec["hdri"], clay=not lookdev)
    (work / "scene_spec.json").write_text(json.dumps(scene_spec), encoding="utf-8")

    def from_scene(line):
        kind, data = parse_message(line)
        if kind == "SCENE":
            result["scene"] = data
            return True
        return False

    code = _run([tools["hython"], PACKAGE_DIR / "hython" / script, work / "scene_spec.json"], work, from_scene)
    if code != 0 or not scene.is_file():
        raise StageFailed("Houdini couldn't build the turntable scene. See the log.")

    # 3. Render it with Karma.
    _stage(f"Rendering {frames} frames" if frames == total else f"Rendering frames {first}-{last}")
    frames_dir = work / "frames"
    frames_dir.mkdir(exist_ok=True)
    emit("START", {"total": frames})
    emit("FRAME", {"frame": first, "index": 1, "total": frames})
    done = {"count": 0}
    began = {"time": time.time()}

    def from_husk(line):
        if "Saved Image:" in line:
            done["count"] += 1
            index = done["count"]
            emit("FRAME_DONE", {"frame": first + index - 1, "index": index, "total": frames,
                                "seconds": round(time.time() - began["time"], 1)})
            began["time"] = time.time()
            if index < frames:
                emit("FRAME", {"frame": first + index, "index": index + 1, "total": frames})
        return False

    width, height = config["resolution"]
    picks = []  # the template's own render settings and camera, when it reported them
    if result.get("scene", {}).get("settings"):
        picks += ["-s", result["scene"]["settings"]]
    if result.get("scene", {}).get("camera"):
        picks += ["-c", result["scene"]["camera"]]
    code = _run([tools["husk"], "--engine", config["engine"], "-p", config["samples"], *picks,
                 "-r", width, height, "-f", first, "-n", frames, "-o", frames_dir / "tt.$F4.exr", "-Va2", scene],
                work, from_husk)
    missing = [f for f in range(first, last + 1) if not (frames_dir / f"tt.{f:04d}.exr").is_file()]
    if code != 0 or missing:
        raise StageFailed(f"Karma stopped before finishing ({len(missing)} of {frames} frames missing). "
                          "See the log.")

    # 4. Display colour. Only R, G and B are kept, so transparency can't leave black areas or
    # fringes in the MP4 (anything see-through ends up over black, as it renders).
    _stage("Converting the colour")
    if spec.get("ocio_note"):
        emit("WARNING", {"message": spec["ocio_note"]})
    display_dir = work / "display"
    display_dir.mkdir(exist_ok=True)
    colour = ["--colorconfig", spec["ocio_config"]] if spec.get("ocio_config") else []
    code = _run([tools["hoiiotool"], *colour, "--frames", f"{first}-{last}", "--framepadding", "4",
                 frames_dir / "tt.#.exr", "--ch", "R,G,B",
                 "--ociodisplay:from=scene_linear", config["display"], config["view"],
                 "-d", "uint8", "-o", display_dir / "tt.#.png"], work)
    missing = [f for f in range(first, last + 1) if not (display_dir / f"tt.{f:04d}.png").is_file()]
    if code != 0 or missing:
        raise StageFailed(f"Couldn't convert the frames' colour ({len(missing)} of {frames} missing). "
                          f"Check \"display\" and \"view\" in {SETTINGS_FILE.name}, and see the log.")

    # 5. Slate, burn-in and MP4 (or, for a farm piece, just these frames with the burn-in).
    size = result.get("scene", {}).get("size_m") or stats.get("size_m")
    slate_png, overlay_png = _draw_slate(spec, work, stats, size)
    fps = str(config["fps"])
    if spec.get("part_out"):
        _stage("Making the MP4 piece")
        piece = work / "piece.mp4"
        code = _run([tools["hffmpeg"], "-y", "-hide_banner", "-loglevel", "error",
                     "-framerate", fps, "-start_number", str(first), "-i", display_dir / "tt.%04d.png",
                     "-i", overlay_png,
                     "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p,setsar=1[v]",
                     "-map", "[v]", "-frames:v", str(frames), "-r", fps, "-c:v", "libopenh264",
                     "-b:v", PIECE_BITRATE, piece], work)
        if code != 0 or not piece.is_file():
            raise StageFailed("Couldn't make the MP4 piece. See the log.")
        part = Path(spec["part_out"])
        info = {"frames": [first, last], "size_m": size, "stats": stats, "versions": spec["versions"],
                "computer": platform.node()}
        _copy_whole(piece, part)
        part.with_suffix(".json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        emit("DONE", {"message": f"Rendered frames {first}-{last}", "path": str(part)})
        return 0

    _stage("Making the MP4")
    mp4 = work / "turntable.mp4"
    code = _run([tools["hffmpeg"], "-y", "-hide_banner", "-loglevel", "error",
                 "-loop", "1", "-framerate", fps, "-t", str(config["slate_seconds"]), "-i", slate_png,
                 "-framerate", fps, "-start_number", "1", "-i", display_dir / "tt.%04d.png",
                 "-i", overlay_png,
                 "-filter_complex",
                 "[1:v][2:v]overlay=0:0,format=yuv420p,setsar=1[spin];"
                 "[0:v]format=yuv420p,setsar=1[card];[card][spin]concat=n=2:v=1:a=0[v]",
                 "-map", "[v]", "-r", fps, "-c:v", "libopenh264", "-b:v", config["bitrate"],
                 "-movflags", "+faststart", mp4], work)
    if code != 0 or not mp4.is_file():
        raise StageFailed("Couldn't make the MP4. See the log.")

    # 6. Save it next to the publish (or work file), whole or not at all.
    _save(spec, mp4, total, spin_frames, size, stats, spec["versions"])
    return 0


PIECE_BITRATE = "20M"        # farm pieces are encoded again when they're joined, so keep them sharp
PARTS_WAIT_SECONDS = 15 * 60  # how long the last step waits for the pieces to arrive (OneDrive can lag)


def _finish(spec: dict) -> int:
    """Farm, last step: the slate, then every piece in order, as one MP4."""
    work = Path(spec["work_dir"])
    tools, config = spec["tools"], spec["settings"]
    parts = [Path(p) for p in spec["finish_parts"]]
    _stage("Waiting for the pieces")
    deadline = time.time() + PARTS_WAIT_SECONDS
    while True:
        missing = [p for p in parts if not (p.is_file() and p.with_suffix(".json").is_file())]
        if not missing:
            break
        if time.time() > deadline:
            raise StageFailed(f"{len(missing)} of {len(parts)} pieces never arrived, e.g. {missing[0]}")
        print(f"Waiting for {len(missing)} of {len(parts)} pieces to arrive...", flush=True)
        time.sleep(10)
    infos = [json.loads(p.with_suffix(".json").read_text(encoding="utf-8")) for p in parts]
    first = infos[0]
    frames = sum(i["frames"][1] - i["frames"][0] + 1 for i in infos)
    spin_frames = hdri_spin_frames(spec.get("kind", "geo"), dict(config, template=spec.get("template")))
    versions = dict(spec["versions"], **(first.get("versions") or {}))
    spec = dict(spec, versions=versions)

    _stage("Making the MP4")
    slate_png, _overlay = _draw_slate(spec, work, first["stats"], first["size_m"])
    listing = work / "pieces.txt"
    listing.write_text("".join("file '{}'\n".format(str(p).replace("\\", "/").replace("'", "'\\''"))
                               for p in parts), encoding="utf-8")
    fps = str(config["fps"])
    mp4 = work / "turntable.mp4"
    code = _run([tools["hffmpeg"], "-y", "-hide_banner", "-loglevel", "error",
                 "-loop", "1", "-framerate", fps, "-t", str(config["slate_seconds"]), "-i", slate_png,
                 "-f", "concat", "-safe", "0", "-i", listing,
                 "-filter_complex",
                 "[1:v]format=yuv420p,setsar=1[spin];"
                 "[0:v]format=yuv420p,setsar=1[card];[card][spin]concat=n=2:v=1:a=0[v]",
                 "-map", "[v]", "-r", fps, "-c:v", "libopenh264", "-b:v", config["bitrate"],
                 "-movflags", "+faststart", mp4], work)
    if code != 0 or not mp4.is_file():
        raise StageFailed("Couldn't join the pieces into the MP4. See the log.")
    _save(spec, mp4, frames, spin_frames, first["size_m"], first["stats"], versions)
    return 0


def main(argv) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    try:
        code = run(spec)
    except StageFailed as e:
        emit("ERROR", {"message": str(e)})
        print(f"The working files were kept for checking: {spec['work_dir']}", flush=True)
        return 1
    shutil.rmtree(spec["work_dir"], ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
