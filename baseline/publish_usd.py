"""A USD copy of a published Maya model, saved next to it for Houdini (Otis, Solaris) to load.

Publishing a Maya file in a geo step (or a part of one: skeleton, muscle, skin) adds a job to
the render queue that runs this file as its own process,

    python -m baseline.publish_usd <spec.json>

which exports the scene with mayapy (maya/export_geo.py: the OUT_BASELINE_geo set or group if
there is one, else every visible mesh; metres, Y-up, UVs) to

    published/muscle/v002/cheetah_geo_muscle_v002.usd

and notes it in the publish's info ("usd"). The Maya file itself is never changed.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import turntable
from .project import Entity, Project, Publish, _make_read_only
from .render import log_path_for, parse_message


@dataclass
class UsdExportJob(turntable.TurntableJob):
    """A render queue job that exports a publish to USD."""

    @property
    def frames_label(self) -> str:
        return f"USD for Houdini · {self.stage}"

    def command(self) -> List[str]:
        return [sys.executable, "-m", "baseline.publish_usd", str(self.spec_file)]


def wants_usd(task: str, scene: Optional[Path]) -> bool:
    return bool(scene) and task.lower() in turntable.GEO_TASKS and scene.suffix.lower() in turntable.MAYA_EXTENSIONS


def usd_path(scene: Path) -> Path:
    return scene.with_suffix(".usd")


def job_for(project: Project, entity: Entity, task: str, publish: Publish, app) -> Optional[UsdExportJob]:
    """The export job for a new publish, or None if it doesn't need one (not a Maya geo file) or
    Maya isn't on this PC."""
    scene = turntable.publish_source(publish)
    if not wants_usd(task, scene):
        return None
    maya, _saved = app.installation_for_file(scene)
    mayapy = maya.tool(app.app_config["maya"], "mayapy") if maya else None
    if not mayapy or not Path(mayapy).is_file():
        return None
    return create_job(project, entity, task, publish, scene, Path(mayapy), maya.version, app.user)


def create_job(project: Project, entity: Entity, task: str, publish: Publish, scene: Path, mayapy: Path,
               maya_version: str, user: str) -> UsdExportJob:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = turntable.WORK_ROOT / f"{stamp}_{entity.name}_{task}_usd"
    work.mkdir(parents=True, exist_ok=True)
    spec = {
        "scene": str(scene),
        "out_usd": str(usd_path(scene)),
        "info_file": str(project._publish_info_file(entity, task, publish.path)),
        "mayapy": str(mayapy),
        "work_dir": str(work),
    }
    spec_file = work / "spec.json"
    spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    env = project.context_env(entity, task, user)
    env["PYTHONPATH"] = str(turntable.PACKAGE_DIR.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")
    return UsdExportJob(
        hip=scene, node=f"{task} USD", start=1, end=1, step=1, hython=mayapy, houdini_label=f"Maya {maya_version}",
        env=env, log_path=log_path_for(project.logs_dir, user, scene, "usd"), output=spec["out_usd"],
        spec_file=spec_file)


# --- The job itself (runs in its own process) ----------------------------------

def run(spec: dict) -> int:
    work = Path(spec["work_dir"])
    usd, stats_file = work / "asset.usd", work / "stats.json"
    result = {}
    exported = threading.Event()
    for var in ("MAYA_DISABLE_CER", "MAYA_DISABLE_CIP", "MAYA_DISABLE_CLIC_IPM", "MAYA_DISABLE_ADP"):
        os.environ.setdefault(var, "1")

    def from_maya(line):
        kind, data = parse_message(line)
        if kind == "EXPORTED":
            result["export"] = data
            exported.set()
            return True
        return False

    turntable._stage("Exporting from Maya")
    code = turntable._run([spec["mayapy"], turntable.PACKAGE_DIR / "maya" / "export_geo.py", spec["scene"], usd,
                           stats_file], work, from_maya, work_done=exported)
    if not usd.is_file() or (code != 0 and "export" not in result):
        raise turntable.StageFailed("Maya couldn't export the model. See the log for Maya's messages.")

    turntable._stage("Saving next to the publish")
    out = Path(spec["out_usd"])
    if out.exists():
        os.chmod(out, 0o666)  # a re-export replaces the read-only copy
    turntable._copy_whole(usd, out)
    _make_read_only(out)
    info_file = Path(spec["info_file"])
    try:
        info = json.loads(info_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    stats = json.loads(stats_file.read_text(encoding="utf-8")) if stats_file.is_file() else {}
    info.update(usd=out.name, usd_stats=stats)
    info_file.parent.mkdir(parents=True, exist_ok=True)
    info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
    turntable.emit("DONE", {"message": f"Saved {out.name}", "path": str(out)})
    return 0


def main(argv) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    try:
        code = run(spec)
    except turntable.StageFailed as e:
        turntable.emit("ERROR", {"message": str(e)})
        print(f"The working files were kept for checking: {spec['work_dir']}", flush=True)
        return 1
    shutil.rmtree(spec["work_dir"], ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
