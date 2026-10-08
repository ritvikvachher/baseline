"""Command-line rendering of Houdini scenes with hython, and a simple render queue.

Jobs run one at a time on this computer. Each job runs hython with
hython/render_hip.py, which reports progress back as CORELINE:: lines.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .project import PipelineError

SCRIPT = Path(__file__).resolve().parent / "hython" / "render_hip.py"
MARK = "CORELINE::"
ALF_PROGRESS = re.compile(r"ALF_PROGRESS\s+(\d+)%")
RES_CHOICES = [(100, "100% - final"), (75, "75%"), (50, "50% - preview"), (25, "25% - quick check")]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class RenderNode:
    path: str
    type: str
    start: int
    end: int
    step: int
    output: str

    @property
    def display(self) -> str:
        return f"{self.path}   ({self.type})"


def parse_message(line: str):
    """'CORELINE::FRAME {"frame": 3}' -> ("FRAME", {"frame": 3}); other lines -> (None, None)."""
    if not line.startswith(MARK):
        return None, None
    kind, _, payload = line[len(MARK):].partition(" ")
    try:
        return kind, json.loads(payload) if payload.strip() else {}
    except ValueError:
        return None, None


def _env(extra: dict) -> dict:
    env = os.environ.copy()
    env.update(extra)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def scan_render_nodes(hython: Path, hip: Path, env: dict, timeout: int = 900) -> List[RenderNode]:
    """Opens the scene in the background and lists its render nodes. Can take a minute."""
    try:
        result = subprocess.run(
            [str(hython), str(SCRIPT), "list", str(hip)],
            capture_output=True, text=True, errors="replace", env=_env(env),
            cwd=str(hip.parent), timeout=timeout, creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise PipelineError("Houdini took too long to open the scene.")
    except OSError as e:
        raise PipelineError(f"Could not start hython:\n{e}")
    for line in result.stdout.splitlines():
        kind, data = parse_message(line)
        if kind == "NODES":
            return [RenderNode(**node) for node in data["nodes"]]
    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-12:])
    raise PipelineError(f"Could not read the scene's render nodes.\n\n{tail}")


@dataclass
class RenderJob:
    hip: Path
    node: str
    start: int
    end: int
    step: int
    hython: Path
    houdini_label: str
    env: dict
    log_path: Path
    output: str = ""         # where the node writes, as reported by the scan
    output_override: str = ""  # write here instead (e.g. the shot's render folder), with $F4
    res_percent: int = 100   # Karma resolution scale
    skip_existing: bool = False
    status: str = "Waiting"  # Waiting, Rendering, Done, Failed, Stopped
    frames_done: int = 0
    frames_skipped: int = 0
    total: int = 0
    current_frame: Optional[int] = None
    frame_percent: Optional[int] = None  # Karma's progress within the current frame
    message: str = ""
    lines: List[str] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None
    started: Optional[datetime] = None

    @property
    def frames_label(self) -> str:
        text = f"{self.start}-{self.end}" + (f" every {self.step}" if self.step != 1 else "")
        return text + (f" @{self.res_percent}%" if self.res_percent != 100 else "")

    @property
    def info_label(self) -> str:
        """The line under the job's progress bar in the queue."""
        return f"Frames {self.frames_label.replace('-', chr(8211))}"

    @property
    def progress_label(self) -> str:
        if not self.total:
            return ""
        text = f"{self.frames_done}/{self.total}"
        if self.status == "Rendering" and self.current_frame is not None:
            text += f", frame {self.current_frame}"
            if self.frame_percent is not None:
                text += f" {self.frame_percent}%"
        if self.frames_skipped:
            text += f" ({self.frames_skipped} skipped)"
        return text

    def command(self) -> List[str]:
        cmd = [str(self.hython), str(SCRIPT), "render", str(self.hip), self.node,
               str(self.start), str(self.end), str(self.step)]
        if self.res_percent != 100:
            cmd += ["--res", str(self.res_percent)]
        if self.skip_existing:
            cmd.append("--skip-existing")
        if self.output_override:
            cmd += ["--output", self.output_override]
        return cmd

    @property
    def finished(self) -> bool:
        return self.status in ("Done", "Failed", "Stopped")


class RenderQueue:
    """Runs jobs one after another in background threads. The UI polls `jobs`."""

    MAX_LOG_LINES = 5000

    def __init__(self):
        self.jobs: List[RenderJob] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> Optional[RenderJob]:
        return next((j for j in self.jobs if j.status == "Rendering"), None)

    def add(self, job: RenderJob) -> None:
        with self._lock:
            self.jobs.append(job)
        self._start_next()

    def stop(self, job: RenderJob) -> None:
        if job.status == "Waiting":
            job.status = "Stopped"
        elif job.status == "Rendering" and job.process:
            job.status = "Stopped"
            # /T also stops the renderer processes hython started (e.g. husk for Karma).
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(job.process.pid)],
                           capture_output=True, creationflags=_NO_WINDOW)

    def stop_all(self) -> None:
        for job in list(self.jobs):
            self.stop(job)

    def remove_finished(self) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if not j.finished]

    def _start_next(self) -> None:
        with self._lock:
            if self.running:
                return
            job = next((j for j in self.jobs if j.status == "Waiting"), None)
            if job is None:
                return
            job.status = "Rendering"
        threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def _run(self, job: RenderJob) -> None:
        job.started = datetime.now()
        cmd = job.command()
        job.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(job.log_path, "w", encoding="utf-8") as log:
            log.write(f"# {' '.join(cmd)}\n")
            try:
                job.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                    bufsize=1, env=_env(job.env), cwd=str(job.hip.parent), creationflags=_NO_WINDOW,
                )
            except OSError as e:
                job.status, job.message = "Failed", f"Could not start hython: {e}"
                log.write(job.message + "\n")
                self._start_next()
                return
            for raw in job.process.stdout:
                line = raw.rstrip("\n")
                log.write(line + "\n")
                log.flush()
                self._handle_line(job, line)
            code = job.process.wait()
        if job.status == "Rendering":
            if code == 0:
                job.status = "Done"
                job.message = job.message or "Finished."
            else:
                job.status = "Failed"
                job.message = job.message or f"Houdini stopped with error code {code}. See the log."
        job.current_frame = None
        self._start_next()

    def _handle_line(self, job: RenderJob, line: str) -> None:
        kind, data = parse_message(line)
        if kind is None:
            match = ALF_PROGRESS.search(line)
            if match:
                # Karma prints these every percent: show them in the queue, not the log.
                job.frame_percent = int(match.group(1))
                return
            job.lines.append(line)
            if len(job.lines) > self.MAX_LOG_LINES:
                del job.lines[: len(job.lines) - self.MAX_LOG_LINES]
        elif kind == "START":
            job.total = data["total"]
        elif kind == "FRAME":
            job.current_frame = data["frame"]
            job.frame_percent = None
            job.lines.append(f"--- Frame {data['frame']} ({data['index']}/{data['total']}) ---")
        elif kind == "FRAME_DONE":
            job.frames_done = data["index"]
            job.lines.append(f"--- Frame {data['frame']} done in {data['seconds']}s ---")
        elif kind == "FRAME_SKIPPED":
            job.frames_done = data["index"]
            job.frames_skipped += 1
            job.lines.append(f"--- Frame {data['frame']} already rendered, skipped ---")
        elif kind == "WARNING":
            job.lines.append("WARNING: " + data["message"])
        elif kind == "STAGE":  # turntables: which step of the job is running
            job.stage = data["name"]
            job.lines.append(f"=== {data['name']} ===")
        elif kind == "NOTE":
            job.lines.append(data["message"])
        elif kind == "DONE":
            job.message = data["message"]
            job.lines.append(data["message"])
        elif kind == "ERROR":
            job.message = data["message"]
            job.lines.append("ERROR: " + data["message"])


def log_path_for(logs_dir: Path, user: str, hip: Path, node: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    node_name = node.strip("/").replace("/", "_")
    return logs_dir / "render" / f"{stamp}_{user}_{hip.stem}_{node_name}.log"
