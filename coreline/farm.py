"""The lab render farm: several PCs share one Houdini render by splitting its frames into chunks.

There's no server. Everything goes through farm folders that the PCs can see. Only small text
files go there; scenes and frames stay in the project. There are two farms, and workers watch both:

  Mine (MINE): H:/CORELINE_farm, your own uni network drive, the same on every PC you log into.
               Fast and exact, but only your logins see it.
  Team (TEAM): _ADMIN/coreline/farm in the project, which OneDrive shares with the whole team.
               Any teammate's PC can help, but OneDrive syncs with a delay (see "synced" below).

    <farm>/jobs/<job id>/job.json                 what to render (written once, by the submitter)
    <farm>/jobs/<job id>/cancelled                the job was cancelled
    <farm>/jobs/<job id>/chunks/<a>_<b>.claim     a PC is rendering frames a-b (its heartbeat)
    <farm>/jobs/<job id>/chunks/<a>_<b>.done.<PC> frames a-b are rendered (by <PC>)
    <farm>/jobs/<job id>/chunks/<a>_<b>.fail.<PC> one line per failed attempt on <PC>
    <farm>/jobs/<job id>/logs/<a>_<b>_<pc>.log
    <farm>/workers/<pc>.json                      each worker's heartbeat and what it's doing
    <farm>/workers/<pc>.stop                      asks that PC's worker to stop

On a network drive a PC takes a chunk by creating its .claim file, which only one PC can do (the
file system refuses to create a file that already exists). While rendering, the worker touches
the claim every 30 seconds. If a PC restarts (Windows updates, someone unplugging it), its claim
stops being touched, and after STALE_SECONDS another PC takes the chunk over. Chunks always
render with "skip frames that are already rendered", so a taken-over chunk carries on.

A synced farm (OneDrive) gives every PC its own copy of the folder, so "only one PC can create
it" doesn't hold. There, each PC writes its own claim (<a>_<b>.claim.<PC>, with the time it
claimed), waits SETTLE_SECONDS for OneDrive to bring in anyone else's, and the earliest claim
wins; the others give the chunk up. Heartbeats are slower (SYNCED_HEARTBEAT_SECONDS) and carry the
PC's own clock, since OneDrive changes file times. No file is ever written by two PCs.

Turntables (job "type": "turntable") go to the farm in one of two ways:
  "whole": one chunk; the PC that takes it makes the whole turntable, as it would on its own;
  "split": chunks of frames, each made into a short MP4 piece in <farm>/jobs/<job id>/parts/
           by the PC that takes it, then the FINISH chunk, which waits until every other chunk
           is done and joins the slate and the pieces into the turntable.

Run a worker without the window:  python -m coreline.farm worker
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import apps, settings
from .render import RenderJob, RenderQueue

DEFAULT_FOLDER = "H:/CORELINE_farm"
DEFAULT_CHUNK = 10              # frames per chunk
HEARTBEAT_SECONDS = 30          # how often a busy worker touches its claim
STALE_SECONDS = 10 * 60         # a claim not touched for this long belongs to a PC that went away
WORKER_OFFLINE_SECONDS = 2 * 60  # a worker that hasn't checked in for this long is shown as offline
POLL_SECONDS = 15               # how often an idle worker looks for work
MAX_ATTEMPTS = 3                # a chunk that fails this many times is marked failed
MAX_ATTEMPTS_PER_PC = 2         # ...and one PC gives up on a chunk after failing it this many times
MAX_FAILS_IN_A_ROW = 3          # a worker skips a job after failing this many of its chunks in a row
FINISH = (0, 0)                 # a split turntable's last chunk: joining the pieces into the MP4
# A synced (OneDrive) farm:
SETTLE_SECONDS = 60             # how long a claim waits for other PCs' claims to arrive
SYNCED_HEARTBEAT_SECONDS = 120
SYNCED_STALE_SECONDS = 15 * 60
SYNCED_OFFLINE_SECONDS = 5 * 60
MINE, TEAM = "mine", "team"
FARM_LABELS = {MINE: "My PCs (H:)", TEAM: "Team (OneDrive)"}


def pc_name() -> str:
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "pc").upper()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None  # missing, or caught half-written by another PC


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _create_exclusive(path: Path, data: dict) -> bool:
    """Creates `path` only if it doesn't exist yet. Exactly one PC wins when several try at once."""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return True


def _age(path: Path) -> Optional[float]:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def is_on_onedrive(path) -> bool:
    """OneDrive gives every PC its own copy of a folder, so two PCs can both 'create' the same claim.
    The farm still works there, but chunks are sometimes rendered twice."""
    text = str(path).lower()
    return "onedrive" in text or any(
        os.environ.get(var) and text.startswith(os.environ[var].lower())
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"))


def my_folder() -> Optional[Path]:
    """Your own farm: the saved folder, else H:/CORELINE_farm if this PC has an H: drive, else None."""
    saved = settings.get_user_setting("farm_folder")
    if saved:
        return settings.expand_path(saved)
    if Path(DEFAULT_FOLDER).drive and Path(Path(DEFAULT_FOLDER).drive + "/").is_dir():
        return Path(DEFAULT_FOLDER)
    return None


def team_folder(project) -> Path:
    """The team's farm, in the project's tracking folder (shared by OneDrive)."""
    return (project.tracking_root or project.root) / "farm"


def farms_for(project) -> List["Farm"]:
    """The farms this PC uses, yours first: [Mine (if there's an H: drive), Team]."""
    team = Farm(team_folder(project), TEAM)
    mine = my_folder()
    if mine is None or str(mine).lower().rstrip("/\\") == str(team.folder).lower().rstrip("/\\"):
        return [team]
    return [Farm(mine, MINE), team]


# --- Chunks -------------------------------------------------------------------

def make_chunks(start: int, end: int, step: int, size: int) -> List[Tuple[int, int]]:
    """Frames start..end every `step`, cut into (first, last) pieces of `size` frames."""
    frames = list(range(start, end + 1, max(1, step)))
    size = max(1, size)
    return [(piece[0], piece[-1]) for piece in (frames[i:i + size] for i in range(0, len(frames), size))]


def chunk_name(chunk: Tuple[int, int]) -> str:
    return f"{chunk[0]}_{chunk[1]}"


def chunk_frames(job: dict, chunk: Tuple[int, int]) -> int:
    if tuple(chunk) == FINISH:
        return 0
    return len(range(chunk[0], chunk[1] + 1, max(1, job["step"])))


def part_name(chunk: Tuple[int, int]) -> str:
    return f"part_{chunk[0]:04d}_{chunk[1]:04d}.mp4"


def project_relative(root, path) -> str:
    """X:/shots/SH010/a.hip -> 'shots/SH010/a.hip', so every PC finds it in its own project folder.
    Paths outside the project stay as they are."""
    if not path:
        return ""
    text = str(path).replace("\\", "/")
    base = str(root).replace("\\", "/").rstrip("/") + "/"
    if text.lower().startswith(base.lower()):
        return text[len(base):]
    return text


def resolve(root, text: str) -> str:
    if not text or Path(text).is_absolute() or re.match(r"^[A-Za-z]:", text) or text.startswith("$"):
        return text
    return (Path(root) / text).as_posix()


# --- The farm folder ------------------------------------------------------------

@dataclass
class ChunkState:
    chunk: Tuple[int, int]
    state: str                 # waiting, rendering, done, failed, retry (failed before, will try again)
    pc: str = ""               # who is rendering it / rendered it
    frame: Optional[int] = None
    seconds: Optional[float] = None
    attempts: int = 0
    message: str = ""

    @property
    def label(self) -> str:
        if tuple(self.chunk) == FINISH:
            return "MP4"
        a, b = self.chunk
        return f"{a}" if a == b else f"{a}-{b}"


@dataclass
class JobStatus:
    id: str
    job: dict
    chunks: List[ChunkState] = field(default_factory=list)
    cancelled: bool = False

    def count(self, state: str) -> int:
        return sum(1 for c in self.chunks if c.state == state)

    @property
    def frames_total(self) -> int:
        return sum(chunk_frames(self.job, c.chunk) for c in self.chunks)

    @property
    def frames_done(self) -> int:
        return sum(chunk_frames(self.job, c.chunk) for c in self.chunks if c.state == "done")

    @property
    def pcs(self) -> List[str]:
        return sorted({c.pc for c in self.chunks if c.state == "rendering" and c.pc})

    @property
    def status(self) -> str:
        if self.cancelled:
            return "Cancelled"
        if self.chunks and self.count("done") == len(self.chunks):
            return "Done"
        if self.count("rendering"):
            return "Rendering"
        if self.count("failed") and not (self.count("waiting") or self.count("retry")):
            return "Failed"
        return "Waiting"

    @property
    def finished(self) -> bool:
        return self.status in ("Done", "Failed", "Cancelled")


class Farm:
    def __init__(self, folder, kind: str = MINE, synced: Optional[bool] = None):
        """`synced`: the folder is synced between PCs with a delay (OneDrive) rather than shared
        directly; worked out from the folder if not given."""
        self.folder = Path(folder)
        self.kind = kind
        self.synced = is_on_onedrive(self.folder) if synced is None else synced
        self.jobs_dir = self.folder / "jobs"
        self.workers_dir = self.folder / "workers"

    @property
    def label(self) -> str:
        return FARM_LABELS.get(self.kind, self.kind)

    @property
    def heartbeat_seconds(self) -> int:
        return SYNCED_HEARTBEAT_SECONDS if self.synced else HEARTBEAT_SECONDS

    @property
    def stale_seconds(self) -> int:
        return SYNCED_STALE_SECONDS if self.synced else STALE_SECONDS

    @property
    def offline_seconds(self) -> int:
        return SYNCED_OFFLINE_SECONDS if self.synced else WORKER_OFFLINE_SECONDS

    def ensure(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.workers_dir.mkdir(parents=True, exist_ok=True)

    # --- Jobs ---------------------------------------------------------------

    def submit(self, *, project_name: str, scene: str, node: str, start: int, end: int, step: int,
               chunk_size: int, houdini_version: str, user: str, output: str = "", output_override: str = "",
               res_percent: int = 100, engine: str = "", extra: Optional[dict] = None) -> str:
        """Adds a job. `scene`, `output` and `output_override` should be project-relative (see
        project_relative) so every PC finds them. `extra` adds to job.json. Returns the job id."""
        self.ensure()
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(scene).stem)
        base = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user}_{stem}"
        job_id, n = base, 1
        while (self.jobs_dir / job_id).exists():
            n += 1
            job_id = f"{base}_{n}"
        job = {
            "id": job_id, "project": project_name, "scene": scene, "node": node,
            "start": start, "end": end, "step": step, "chunk_size": chunk_size,
            "houdini_version": houdini_version, "engine": engine, "res_percent": res_percent,
            "output": output, "output_override": output_override,
            "user": user, "submitted_from": pc_name(), "submitted": _now(),
            **(extra or {}),
        }
        (self.jobs_dir / job_id / "chunks").mkdir(parents=True)
        (self.jobs_dir / job_id / "logs").mkdir()
        (self.jobs_dir / job_id / "parts").mkdir()
        _write_json(self.jobs_dir / job_id / "job.json", job)
        return job_id

    def submit_turntable(self, *, project_name: str, turntable: dict, frames: int, split: bool,
                         chunk_size: int, houdini_version: str, user: str) -> str:
        """Adds a turntable job. `turntable` is its portable spec (turntable.portable_spec)."""
        size = chunk_size if split else frames
        chunks = [list(c) for c in make_chunks(1, frames, 1, size)] + ([list(FINISH)] if split else [])
        return self.submit(project_name=project_name, scene=turntable["source"],
                           node=f"{turntable['context']['task']} turntable", start=1, end=frames, step=1,
                           chunk_size=size, houdini_version=houdini_version, user=user,
                           output=turntable["out_mp4"], engine="karma",
                           extra={"type": "turntable", "mode": "split" if split else "whole",
                                  "turntable": turntable, "chunks": chunks})

    def parts_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id / "parts"

    def job_ids(self) -> List[str]:
        """Oldest first, so workers finish one job before starting the next."""
        try:
            return sorted(p.name for p in self.jobs_dir.iterdir() if (p / "job.json").is_file())
        except OSError:
            return []

    def read_job(self, job_id: str) -> Optional[dict]:
        return _read_json(self.jobs_dir / job_id / "job.json")

    def chunks_of(self, job: dict) -> List[Tuple[int, int]]:
        if job.get("chunks"):
            return [tuple(c) for c in job["chunks"]]
        return make_chunks(job["start"], job["end"], job["step"], job["chunk_size"])

    def _chunk_file(self, job_id: str, chunk, ext: str) -> Path:
        return self.jobs_dir / job_id / "chunks" / f"{chunk_name(chunk)}.{ext}"

    def log_file(self, job_id: str, chunk, pc: str) -> Path:
        return self.jobs_dir / job_id / "logs" / f"{chunk_name(chunk)}_{pc}.log"

    def is_cancelled(self, job_id: str) -> bool:
        return (self.jobs_dir / job_id / "cancelled").exists()

    def cancel(self, job_id: str) -> None:
        _write_json(self.jobs_dir / job_id / "cancelled", {"when": _now(), "pc": pc_name()})

    def remove(self, job_id: str) -> None:
        """Deletes the job's farm files (not its rendered frames)."""
        shutil.rmtree(self.jobs_dir / job_id, ignore_errors=True)

    # Done and fail files are one per PC (<a>_<b>.done.<PC>), so OneDrive never has two PCs
    # writing the same file. Older jobs' shared <a>_<b>.done / .fail are read too.

    @staticmethod
    def _entries(listing: set, chunk, ext: str) -> List[str]:
        name = f"{chunk_name(chunk)}.{ext}"
        return sorted(n for n in listing if n == name or (n.startswith(name + ".") and ".stale_" not in n))

    def _is_done(self, chunk, listing: set) -> bool:
        return bool(self._entries(listing, chunk, "done"))

    def retry_failed(self, job_id: str) -> int:
        """Gives failed chunks another go. Returns how many."""
        job = self.read_job(job_id)
        listing = self._listing(job_id)
        count = 0
        for chunk in self.chunks_of(job) if job else []:
            fails = self._entries(listing, chunk, "fail")
            if fails and not self._is_done(chunk, listing):
                for name in fails:
                    try:
                        (self.jobs_dir / job_id / "chunks" / name).unlink()
                    except OSError:
                        pass
                count += 1
        return count

    def _listing(self, job_id: str) -> set:
        """The names in a job's chunks folder, read once (each check is a trip over the network)."""
        try:
            return set(os.listdir(self.jobs_dir / job_id / "chunks"))
        except OSError:
            return set()

    def _fails(self, job_id: str, chunk, listing: set = None) -> List[dict]:
        listing = self._listing(job_id) if listing is None else listing
        fails = []
        for name in self._entries(listing, chunk, "fail"):
            try:
                lines = (self.jobs_dir / job_id / "chunks" / name).read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    fails.append(json.loads(line))
                except ValueError:
                    pass
        return sorted(fails, key=lambda f: f.get("when", ""))

    def _claims(self, job_id: str, chunk, listing: set = None) -> List[dict]:
        """The live claims on a chunk, the one that counts first. On a synced (OneDrive) farm each
        PC has its own claim file and the earliest claim wins; on a network drive there's only one."""
        listing = self._listing(job_id) if listing is None else listing
        claims = []
        for name in self._entries(listing, chunk, "claim"):
            path = self.jobs_dir / job_id / "chunks" / name
            info = _read_json(path)
            if self.synced and info and info.get("beat"):
                age = time.time() - info["beat"]  # the PC's own clock: OneDrive changes file times
            else:
                age = _age(path)
            if age is not None and age < self.stale_seconds:
                claims.append(dict(info or {"pc": name.rsplit(".", 1)[-1] if self.synced else ""}, _file=name))
        return sorted(claims, key=lambda c: (c.get("at", 0), c.get("pc", "")))

    def status(self, job_id: str) -> Optional[JobStatus]:
        job = self.read_job(job_id)
        if job is None:
            return None
        result = JobStatus(job_id, job, cancelled=self.is_cancelled(job_id))
        listing = self._listing(job_id)
        for chunk in self.chunks_of(job):
            done = self._entries(listing, chunk, "done")
            if done:
                info = _read_json(self.jobs_dir / job_id / "chunks" / done[0]) or {}
                result.chunks.append(ChunkState(chunk, "done", info.get("pc", ""), seconds=info.get("seconds")))
                continue
            fails = self._fails(job_id, chunk, listing)
            claims = self._claims(job_id, chunk, listing) if self._entries(listing, chunk, "claim") else []
            if claims:
                result.chunks.append(ChunkState(chunk, "rendering", claims[0].get("pc", ""), claims[0].get("frame"),
                                                attempts=len(fails)))
            elif len(fails) >= MAX_ATTEMPTS:
                result.chunks.append(ChunkState(chunk, "failed", fails[-1].get("pc", ""), attempts=len(fails),
                                                message=fails[-1].get("message", "")))
            elif fails:
                result.chunks.append(ChunkState(chunk, "retry", fails[-1].get("pc", ""), attempts=len(fails),
                                                message=fails[-1].get("message", "")))
            else:
                result.chunks.append(ChunkState(chunk, "waiting"))
        return result

    def all_status(self) -> List[JobStatus]:
        return [s for s in (self.status(job_id) for job_id in self.job_ids()) if s is not None]

    # --- Claiming chunks (workers) -------------------------------------------

    def claim_next(self, pc: str, project_name: str, skip_jobs=(),
                   wait: Callable[[float], bool] = None) -> Optional[Tuple[dict, Tuple[int, int]]]:
        """Takes the next chunk this PC should render, or None if there's nothing to do.
        On a synced farm this takes about SETTLE_SECONDS: `wait(seconds)` waits, returning True
        if the worker is being stopped meanwhile."""
        for job_id in self.job_ids():
            if job_id in skip_jobs or self.is_cancelled(job_id):
                continue
            job = self.read_job(job_id)
            if not job or job.get("project") != project_name:
                continue
            listing = self._listing(job_id)
            chunks = self.chunks_of(job)
            for chunk in chunks:
                if self._is_done(chunk, listing):
                    continue
                if chunk == FINISH and any(not self._is_done(c, listing) for c in chunks if c != FINISH):
                    continue  # the pieces aren't all rendered yet
                fails = self._fails(job_id, chunk, listing)
                if len(fails) >= MAX_ATTEMPTS or sum(f.get("pc") == pc for f in fails) >= MAX_ATTEMPTS_PER_PC:
                    continue
                if not self.synced:
                    if self._claim(job_id, chunk, pc):
                        return job, chunk
                    continue
                if self._claims(job_id, chunk, listing):
                    continue  # someone has it
                outcome = self._claim_synced(job_id, chunk, pc, wait)
                if outcome is None:
                    return None  # stopping
                if outcome:
                    return job, chunk
        return None

    def _claim(self, job_id: str, chunk, pc: str) -> bool:
        claim = self._chunk_file(job_id, chunk, "claim")
        data = {"pc": pc, "claimed": _now(), "frame": None}
        if _create_exclusive(claim, data):
            return True
        age = _age(claim)
        if age is None or age < STALE_SECONDS:
            return False
        # The PC holding it went away. Renaming is all-or-nothing too, so only one PC takes it over.
        try:
            os.replace(claim, claim.with_name(f"{claim.name}.stale_{pc}_{int(time.time())}"))
        except OSError:
            return False
        return _create_exclusive(claim, data)

    def _own_claim(self, job_id: str, chunk, pc: str) -> Path:
        return self.jobs_dir / job_id / "chunks" / f"{chunk_name(chunk)}.claim.{pc}"

    def _claim_synced(self, job_id: str, chunk, pc: str, wait) -> Optional[bool]:
        """Writes this PC's claim, waits for OneDrive to bring in any other PC's, and keeps the
        chunk only if this claim is the earliest. None if the worker is stopped while waiting."""
        folder = self.jobs_dir / job_id / "chunks"
        # Claims of PCs that went away: moved aside, so they can't win again if the PC comes back.
        for name in self._entries(self._listing(job_id), chunk, "claim"):
            if not name.endswith("." + pc):
                try:
                    os.replace(folder / name, folder / f"{name}.stale_{pc}_{int(time.time())}")
                except OSError:
                    pass
        now = time.time()
        own = self._own_claim(job_id, chunk, pc)
        _write_json(own, {"pc": pc, "claimed": _now(), "at": now, "beat": now, "frame": None})
        stopped = (wait or (lambda s: time.sleep(s) or False))(SETTLE_SECONDS)
        if stopped:
            self.release(job_id, chunk, pc)
            return None
        if self.owns(job_id, chunk, pc):
            return True
        self.release(job_id, chunk, pc)
        return False

    def owns(self, job_id: str, chunk, pc: str) -> bool:
        if not self.synced:
            info = _read_json(self._chunk_file(job_id, chunk, "claim"))
            return info is not None and info.get("pc") == pc
        listing = self._listing(job_id)
        if self._is_done(chunk, listing):
            return False
        claims = self._claims(job_id, chunk, listing)
        return bool(claims) and claims[0].get("pc") == pc

    def heartbeat(self, job_id: str, chunk, pc: str, frame: Optional[int] = None) -> bool:
        """Touches this PC's claim. False if the chunk was given to another PC in the meantime
        (e.g. this PC lost the network for longer than the stale time, or on OneDrive, an
        earlier claim from another PC arrived late)."""
        if self.synced:
            own = self._own_claim(job_id, chunk, pc)
            info = _read_json(own)
            if info is None:
                return own.exists()  # being written right now: assume it's still ours
            if not self.owns(job_id, chunk, pc):
                return False
            info.update(beat=time.time(), frame=frame)
            try:
                _write_json(own, info)
            except OSError:
                pass
            return True
        claim = self._chunk_file(job_id, chunk, "claim")
        info = _read_json(claim)
        if info is None:
            return claim.exists()  # being written right now: assume it's still ours
        if info.get("pc") != pc:
            return False
        info["frame"] = frame
        try:
            _write_json(claim, info)
        except OSError:
            pass  # a network blip; the next heartbeat tries again
        return True

    def finish(self, job_id: str, chunk, pc: str, seconds: float) -> None:
        _write_json(self.jobs_dir / job_id / "chunks" / f"{chunk_name(chunk)}.done.{pc}",
                    {"pc": pc, "seconds": round(seconds), "when": _now()})
        self.release(job_id, chunk, pc)

    def fail(self, job_id: str, chunk, pc: str, message: str) -> None:
        line = json.dumps({"pc": pc, "when": _now(), "message": message[:500]})
        try:
            with open(self.jobs_dir / job_id / "chunks" / f"{chunk_name(chunk)}.fail.{pc}", "a",
                      encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        self.release(job_id, chunk, pc)

    def release(self, job_id: str, chunk, pc: str) -> None:
        """Gives a chunk back (only if this PC holds it)."""
        if self.synced:
            path = self._own_claim(job_id, chunk, pc)
        elif self.owns(job_id, chunk, pc):
            path = self._chunk_file(job_id, chunk, "claim")
        else:
            return
        try:
            path.unlink()
        except OSError:
            pass

    # --- Workers --------------------------------------------------------------

    def worker_checkin(self, pc: str, data: dict) -> None:
        try:
            _write_json(self.workers_dir / f"{pc}.json", dict(data, pc=pc, updated=_now(), beat=time.time()))
        except OSError:
            pass

    def worker_gone(self, pc: str) -> None:
        try:
            _write_json(self.workers_dir / f"{pc}.json", {"pc": pc, "state": "Stopped", "updated": _now(),
                                                          "beat": time.time()})
        except OSError:
            pass

    def workers(self) -> List[dict]:
        """Every PC that has run a worker, with 'online' set from how recently it checked in."""
        result = []
        try:
            files = sorted(self.workers_dir.glob("*.json"))
        except OSError:
            return result
        for path in files:
            info = _read_json(path)
            if not info:
                continue
            age = time.time() - info["beat"] if self.synced and info.get("beat") else _age(path)
            info["online"] = info.get("state") != "Stopped" and age is not None and age < self.offline_seconds
            result.append(info)
        return result

    def ask_to_stop(self, pc: str) -> None:
        _write_json(self.workers_dir / f"{pc}.stop", {"when": _now(), "from": pc_name()})

    def stop_requested(self, pc: str) -> bool:
        stop = self.workers_dir / f"{pc}.stop"
        if not stop.exists():
            return False
        try:
            stop.unlink()
        except OSError:
            pass
        return True


# --- Keeping the PC awake ------------------------------------------------------

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake(on: bool) -> None:
    """Stops Windows sleeping while this thread runs (the screen can still turn off)."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if on else 0))
    except (AttributeError, OSError):
        pass  # not Windows


# --- The worker ------------------------------------------------------------------

class FarmWorker:
    """Renders farm chunks on this PC, one at a time, until stopped. `farm` is one Farm or a list
    (yours first: the worker looks there first, then in the next).

    `project` is this PC's Project (scenes are found in it). `on_event(text)` gets a line for each
    thing that happens, for the console or the Farm page."""

    def __init__(self, farm: Farm, project, user: str, installs: List[apps.Installation], app_cfg: dict,
                 on_event: Callable[[str], None] = None, maya_installs: List[apps.Installation] = (),
                 maya_cfg: Optional[dict] = None):
        self.farms = list(farm) if isinstance(farm, (list, tuple)) else [farm]
        self.farm = self.farms[0]
        self.project = project
        self.user = user
        self.app_cfg = app_cfg
        self.hythons = [i for i in installs if i.tool(app_cfg, "hython")]
        self.maya_cfg = maya_cfg or {}
        self.mayas = [i for i in maya_installs if maya_cfg and i.tool(maya_cfg, "mayapy")]
        self.on_event = on_event or (lambda text: None)
        self.pc = pc_name()
        self.state = "Stopped"
        self.current: Optional[RenderJob] = None   # the chunk being rendered, for showing progress
        self.current_job: Optional[dict] = None
        self.chunks_done = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._queue = RenderQueue()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops now. A chunk being rendered goes back to the farm (its finished frames are kept)."""
        self._stop.set()
        if self.current is not None and not self.current.finished:
            self._queue.stop(self.current)

    def _event(self, text: str) -> None:
        self.on_event(f"{datetime.now().strftime('%H:%M:%S')}  {text}")

    def _checkin(self) -> None:
        data = {"state": self.state, "user": self.user, "chunks_done": self.chunks_done,
                "houdini": [i.version for i in self.hythons], "farms": [f.kind for f in self.farms]}
        if self.current_job and self.current:
            data.update(job=self.current_job["id"], scene=Path(self.current_job["scene"]).name,
                        chunk=f"{self.current.start}-{self.current.end}", frame=self.current.current_frame)
        for farm in self.farms:
            farm.worker_checkin(self.pc, data)

    def _stop_asked(self) -> bool:
        return any([farm.stop_requested(self.pc) for farm in self.farms])  # clears every farm's request

    def run(self) -> None:
        """The worker loop (start() runs it in the background; the command line runs it directly)."""
        keep_awake(True)
        skip_jobs: Dict[str, set] = {}
        fails_in_a_row: Dict[tuple, int] = {}
        try:
            for farm in list(self.farms):
                try:
                    farm.ensure()
                except OSError as e:
                    self._event(f"Can't use the {farm.label} farm ({farm.folder}): {e}")
                    self.farms.remove(farm)
            if not self.farms:
                self.state = "No farm folder"
                return
            self._stop_asked()  # an old request from before this worker started
            if not self.hythons:
                self.state = "No Houdini found"
                self._event("No Houdini with hython was found on this PC, so it can't render.")
                self._checkin()
                return
            self.state = "Idle"
            self._event(f"Worker started on {self.pc}. Farms: "
                        + ", ".join(f"{f.label} {f.folder}" for f in self.farms))
            while not self._stop.is_set():
                self._checkin()
                if self._stop_asked():
                    self._event("Asked to stop from another PC.")
                    break
                picked = None
                for farm in self.farms:
                    if farm.synced:
                        self.state = "Checking the team farm"
                    picked = farm.claim_next(self.pc, self.project.name, skip_jobs.get(farm.kind, set()),
                                             wait=self._stop.wait)
                    if picked or self._stop.is_set():
                        break
                if picked is None:
                    self.state = "Idle"
                    self._stop.wait(POLL_SECONDS)
                    continue
                job, chunk = picked
                ok = self._render(job, chunk, farm)
                if ok is None:
                    continue
                key = (farm.kind, job["id"])
                fails_in_a_row[key] = 0 if ok else fails_in_a_row.get(key, 0) + 1
                if fails_in_a_row[key] >= MAX_FAILS_IN_A_ROW:
                    skip_jobs.setdefault(farm.kind, set()).add(job["id"])
                    self._event(f"{job['id']}: {MAX_FAILS_IN_A_ROW} chunks failed in a row on this PC, "
                                "so it's leaving this job to the others.")
        except Exception as e:  # keep the PC's state honest on the farm whatever happens
            self._event(f"The worker stopped because of an error: {e}")
            raise
        finally:
            self.state = "Stopped"
            self.current = None
            for farm in self.farms:
                farm.worker_gone(self.pc)
            keep_awake(False)
            self._event("Worker stopped.")

    def _hython_for(self, version: str) -> apps.Installation:
        return apps.pick_installation(self.hythons, version)

    def _turntable_job(self, job: dict, chunk, inst: apps.Installation, farm: "Farm") -> RenderJob:
        """This PC's part of a farm turntable (raises PipelineError if it can't do it)."""
        from . import turntable
        portable = job["turntable"]
        maya = None
        if portable["kind"] == "geo" and chunk != FINISH:
            maya = apps.pick_installation(self.mayas, portable.get("versions", {}).get("maya", "")) \
                if self.mayas else None
        parts = farm.parts_dir(job["id"])
        split = job.get("mode") == "split"
        render_chunks = [c for c in farm.chunks_of(job) if c != FINISH]
        return turntable.farm_job(
            portable, self.project, job.get("user") or self.user,
            houdini_bin=inst.tool(self.app_cfg, "hython").parent, houdini_version=inst.version,
            mayapy=maya.tool(self.maya_cfg, "mayapy") if maya else None, maya_version=maya.version if maya else "",
            log_path=farm.log_file(job["id"], chunk, self.pc),
            work_name=f"farm_{job['id']}_{chunk_name(chunk)}",
            frame_range=chunk if split and chunk != FINISH else None,
            part_out=parts / part_name(chunk) if split and chunk != FINISH else None,
            finish_parts=[parts / part_name(c) for c in render_chunks] if chunk == FINISH else None)

    def _render(self, job: dict, chunk, farm: Optional["Farm"] = None) -> Optional[bool]:
        """Renders one chunk of `farm`'s job. True = done, False = failed, None = stopped or taken away."""
        farm = farm or self.farm
        job_id, pc = job["id"], self.pc
        is_turntable = job.get("type") == "turntable"
        if is_turntable:
            what = "joining the MP4" if chunk == FINISH else \
                "whole turntable" if job.get("mode") != "split" else f"turntable frames {chunk[0]}-{chunk[1]}"
            label = f"{Path(job['scene']).name} {what}"
        else:
            label = f"{Path(job['scene']).name} frames {chunk[0]}-{chunk[1]}"
        hip = Path(resolve(self.project.root, job["scene"]))
        if not hip.is_file() and chunk != FINISH:
            message = f"The scene isn't on this PC: {hip}"
            self._event(f"{label}: {message}")
            farm.fail(job_id, chunk, pc, message)
            return False
        inst = self._hython_for(job.get("houdini_version", ""))
        if is_turntable:
            from .project import PipelineError
            try:
                render_job = self._turntable_job(job, chunk, inst, farm)
            except (PipelineError, OSError) as e:
                self._event(f"{label}: {e}")
                farm.fail(job_id, chunk, pc, str(e))
                return False
        else:
            entity, task = self.project.context_for_path(hip)
            override = resolve(self.project.root, job.get("output_override", ""))
            render_job = RenderJob(
                hip=hip, node=job["node"], start=chunk[0], end=chunk[1], step=job["step"],
                hython=inst.tool(self.app_cfg, "hython"), houdini_label=inst.version,
                env=self.project.context_env(entity, task, job.get("user") or self.user),
                log_path=farm.log_file(job_id, chunk, pc),
                output=resolve(self.project.root, job.get("output", "")), output_override=override,
                res_percent=job.get("res_percent", 100), skip_existing=True)
        self.current, self.current_job = render_job, job
        self.state = "Rendering"
        version_note = "" if inst.version == job.get("houdini_version") else \
            f" (Houdini {inst.version}; the job asked for {job.get('houdini_version') or 'any'})"
        self._event(f"Rendering {label}{version_note}")
        began = time.time()
        self._queue.add(render_job)
        last_beat = 0.0
        outcome = None
        while not render_job.finished:
            if self._stop.wait(1):
                self._queue.stop(render_job)
                outcome = "stopped"
            elif time.time() - last_beat >= farm.heartbeat_seconds:
                last_beat = time.time()
                self._checkin()
                if self._stop_asked():
                    self._event("Asked to stop from another PC.")
                    self._stop.set()
                    self._queue.stop(render_job)
                    outcome = "stopped"
                elif farm.is_cancelled(job_id):
                    self._event(f"{label}: the job was cancelled.")
                    self._queue.stop(render_job)
                    outcome = "cancelled"
                elif not farm.heartbeat(job_id, chunk, pc, render_job.current_frame):
                    self._event(f"{label}: another PC took this chunk over, so this PC lets it go.")
                    self._queue.stop(render_job)
                    outcome = "lost"
        if outcome is None and (self._stop.is_set() or render_job.status == "Stopped"):
            outcome = "stopped"  # stop() was called from the window
        while render_job.process is not None and render_job.process.poll() is None:
            time.sleep(0.2)
        self._queue.remove_finished()
        self.current, self.current_job = None, None
        self.state = "Idle"
        seconds = time.time() - began
        if outcome in ("stopped", "cancelled", "lost"):
            farm.release(job_id, chunk, pc)
            return None
        if render_job.status == "Done":
            farm.finish(job_id, chunk, pc, seconds)
            self.chunks_done += 1
            if is_turntable and chunk == FINISH:
                shutil.rmtree(farm.parts_dir(job_id), ignore_errors=True)  # the pieces are in the MP4 now
            skipped = f", {render_job.frames_skipped} already there" if render_job.frames_skipped else ""
            self._event(f"Done {label} in {_duration(seconds)}{skipped}.")
            return True
        message = render_job.message or "Houdini stopped with an error."
        farm.fail(job_id, chunk, pc, message)
        self._event(f"FAILED {label}: {message}")
        return False


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


# --- Command line ------------------------------------------------------------------

def main(argv: List[str]) -> int:
    if not argv or argv[0] != "worker":
        print("Usage: python -m coreline.farm worker [--folder H:/CORELINE_farm]")
        return 2
    from .gui.main_window import open_project  # also shows the project as its drive letter
    root = settings.find_project_root()
    if root is None:
        print("CORELINE can't find the project folder. Open CORELINE once on this PC first.")
        return 1
    project, message = open_project(root)
    if message:
        print(message)
    farms = [Farm(Path(argv[argv.index("--folder") + 1]))] if "--folder" in argv else farms_for(project)
    app_cfg = settings.load_app_config()
    found = apps.detect_all(app_cfg, settings.get_user_setting("extra_app_paths", {}))
    worker = FarmWorker(farms, project, settings.current_user(), found.get("houdini", []), app_cfg["houdini"],
                        on_event=lambda text: print(text, flush=True),
                        maya_installs=found.get("maya", []), maya_cfg=app_cfg.get("maya"))
    print(f"CORELINE farm worker on {worker.pc} for {project.name}. Close this window (or press Ctrl+C) "
          "to stop; a chunk being rendered goes back to the farm.", flush=True)
    _stop_on_close(worker)
    worker.start()
    try:
        while worker.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        worker.stop()
        while worker.running:
            time.sleep(0.2)
    return 0


def _stop_on_close(worker: FarmWorker) -> None:
    """Closing the console window would leave Houdini rendering on its own; stop it properly instead."""
    try:
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    except AttributeError:
        return  # not Windows

    def handler(_event):
        worker.stop()
        deadline = time.time() + 4  # Windows allows about 5 seconds after the window is closed
        while worker.running and time.time() < deadline:
            time.sleep(0.1)
        return False

    worker._console_handler = handler_type(handler)  # kept alive for as long as the worker
    ctypes.windll.kernel32.SetConsoleCtrlHandler(worker._console_handler, True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
