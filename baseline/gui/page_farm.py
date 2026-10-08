"""Farm: lab PCs share Houdini renders. Jobs are sent from the Render page (or as turntables);
here you watch them, and turn this PC into a worker that renders chunks. There are two farms,
yours (H:) and the team's (OneDrive), and a worker on this PC watches both."""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from .. import farm, settings
from .page_base import Page
from .theme import AMBER, BG, DIM, MUTED, PANEL, PANEL_HI, RED, TEXT, Fonts, px
from .widgets import LogView, Panel, ProgressBar, ScrollFrame, SlantButton, Tooltip, label, separator, set_bg

POLL_MS = 500
READ_EVERY_S = 5   # how often the farm folder is read while this page is open
MAX_EVENTS = 500


def _ago(iso: str) -> str:
    try:
        seconds = (datetime.now() - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


class FarmPage(Page):
    nav_title = "Farm"

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.farms = farm.farms_for(app.project)
        self.worker = None
        self.events = []
        self._shown_events = 0
        self._jobs = []           # JobStatus list from the last read
        self._workers = []
        self._snapshot = None     # filled by the reading thread
        self._reading = False
        self._last_read = 0.0
        self._selected = None     # "<farm kind>:<job id>"
        self._job_rows = {}

        self.header("Lab render farm", "Farm")
        body = tk.Frame(self.content, bg=BG)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        left = Panel(body, "This PC")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(18)))
        left.configure(width=px(400))
        left.grid_propagate(False)
        left.pack_propagate(False)
        self._build_this_pc(left.body)

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=3)
        right.columnconfigure(1, weight=2)
        jobs = Panel(right, "Farm jobs", right=self._jobs_header)
        jobs.grid(row=0, column=0, columnspan=2, sticky="ew")
        jobs_scroll = ScrollFrame(jobs.body, bg=PANEL)
        jobs_scroll.configure(height=px(230))
        jobs_scroll.pack_propagate(False)
        jobs_scroll.pack(fill="x")
        self.jobs_list = jobs_scroll.inner
        self.jobs_empty = label(self.jobs_list, "No farm jobs. Send one from the Render page with "
                                                "'Send to the render farm' ticked, or a turntable.", Fonts.body, DIM)
        self.jobs_empty.pack(anchor="w", padx=px(18), pady=px(14))

        chunks = Panel(right, "Chunks", right=self._chunks_header)
        chunks.grid(row=1, column=0, sticky="nsew", pady=(px(18), 0), padx=(0, px(18)))
        self.chunk_title = label(chunks.body, "Select a job to see its chunks.", Fonts.body, MUTED,
                                 wraplength=px(520), justify="left")
        self.chunk_title.pack(anchor="w", padx=px(18), pady=(px(12), px(8)))
        self.chunk_view = LogView(chunks.body)
        self.chunk_view.pack(fill="both", expand=True, padx=px(18), pady=(0, px(18)))

        pcs = Panel(right, "PCs")
        pcs.grid(row=1, column=1, sticky="nsew", pady=(px(18), 0))
        pcs_scroll = ScrollFrame(pcs.body, bg=PANEL)
        pcs_scroll.pack(fill="both", expand=True)
        self.pcs_list = pcs_scroll.inner
        self.after(POLL_MS, self._poll)

    # --- This PC ------------------------------------------------------------

    def _build_this_pc(self, parent):
        bar = tk.Frame(parent, bg=PANEL)
        bar.pack(side="bottom", fill="x")
        separator(bar).pack(fill="x")
        self.worker_btn = SlantButton(bar, "Render farm jobs on this PC", self.toggle_worker, kind="primary",
                                      icon="▷", height=42, width=360)
        self.worker_btn.pack(fill="x", padx=px(18), pady=px(14))

        f = tk.Frame(parent, bg=PANEL)
        f.pack(fill="both", expand=True, padx=px(18), pady=px(14))
        self.folders_box = tk.Frame(f, bg=PANEL)
        self.folders_box.pack(fill="x")
        SlantButton(f, "Change my folder...", self.change_folder, kind="ghost", height=30,
                    tip="Your own farm's folder (H:/BASELINE_farm by default). Every PC you use must use the "
                        "same one. The team farm is always in the project.").pack(anchor="w", pady=(px(6), 0))
        separator(f).pack(fill="x", pady=px(12))
        top = tk.Frame(f, bg=PANEL)
        top.pack(fill="x")
        label(top, farm.pc_name(), Fonts.mono).pack(side="left")
        self.worker_state = label(top, "Not rendering farm jobs", Fonts.small, MUTED)
        self.worker_state.pack(side="right")
        self.worker_bar = ProgressBar(f)
        self.worker_bar.pack(fill="x", pady=(px(8), px(4)))
        self.worker_info = label(f, "", Fonts.small, DIM, wraplength=px(350), justify="left")
        self.worker_info.pack(anchor="w")
        self.event_view = LogView(f, height=8)
        self.event_view.pack(fill="both", expand=True, pady=(px(10), 0))

    def _show_folder(self):
        for child in self.folders_box.winfo_children():
            child.destroy()
        kinds = [f.kind for f in self.farms]
        if farm.MINE not in kinds:
            label(self.folders_box, farm.FARM_LABELS[farm.MINE], Fonts.small, MUTED).pack(anchor="w")
            label(self.folders_box, "This PC has no H: drive (off the uni network?), so only the team farm is used.",
                  Fonts.small, AMBER, wraplength=px(350), justify="left").pack(anchor="w", pady=(px(3), px(10)))
        for f in self.farms:
            label(self.folders_box, f.label, Fonts.small, MUTED).pack(anchor="w")
            label(self.folders_box, str(f.folder), Fonts.mono_small, TEXT, wraplength=px(350), justify="left").pack(
                anchor="w", pady=(px(3), 0))
            if not Path(f.folder.anchor or f.folder).exists():
                text, color = f"{f.folder.anchor or f.folder} isn't available on this PC.", "#ff6b6b"
            elif f.kind == farm.TEAM:
                text, color = "Shared with the whole team: any teammate's PC can help.", MUTED
                if f.synced:
                    text += " Chunks start about a minute after a PC picks them, while OneDrive catches up."
            else:
                text, color = "Only your logins see this. Fast, and no chunk is ever rendered twice.", MUTED
            label(self.folders_box, text, Fonts.small, color, wraplength=px(350), justify="left").pack(
                anchor="w", pady=(px(3), px(10)))

    def farm_choices(self):
        """[(label, Farm)] for 'Send to farm', yours first."""
        return [(f.label, f) for f in self.farms]

    def select_job(self, farm_obj, job_id):
        self._selected = f"{farm_obj.kind}:{job_id}"

    def change_folder(self):
        if self.worker and self.worker.running:
            messagebox.showinfo("Farm folder", "Stop rendering farm jobs on this PC first.", parent=self)
            return
        current = farm.my_folder() or Path(farm.DEFAULT_FOLDER)
        chosen = filedialog.askdirectory(parent=self, title="Choose your farm folder (the same on every PC you use)",
                                         initialdir=str(current if current.exists() else Path.home()))
        if not chosen:
            return
        settings.save_user_setting("farm_folder", settings.portable_path(chosen))
        self.farms = farm.farms_for(self.app.project)
        self._jobs, self._workers, self._selected = [], [], None
        self._show_folder()
        self._read_now()
        self.app.status(f"Farm folder: {chosen}")

    def toggle_worker(self):
        if self.worker and self.worker.running:
            self.worker.stop()
            self.app.status("Stopping. A chunk being rendered goes back to the farm for another PC.")
            return
        houdini_cfg = self.app.app_config["houdini"]
        self.worker = farm.FarmWorker(self.farms, self.app.project, self.app.user,
                                      self.app.installations.get("houdini", []), houdini_cfg,
                                      on_event=self._add_event,
                                      maya_installs=self.app.installations.get("maya", []),
                                      maya_cfg=self.app.app_config.get("maya"))
        self.worker.start()
        self.app.status("This PC now renders farm jobs (" + " and ".join(f.label for f in self.farms)
                        + "). It won't go to sleep while it's on.")

    def _add_event(self, text):
        self.events.append(text)  # from the worker's thread; shown by _poll
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]
            self._shown_events = 0

    def has_active_worker(self) -> bool:
        return bool(self.worker and self.worker.running)

    def stop_worker(self):
        if self.worker:
            self.worker.stop()

    # --- Jobs -----------------------------------------------------------------

    def _jobs_header(self, parent):
        self.cancel_btn = SlantButton(parent, "Cancel", self.cancel_job, icon="■", height=30,
                                      tip="Stop the selected job on every PC.")
        self.cancel_btn.pack(side="left")
        self.retry_btn = SlantButton(parent, "Retry failed", self.retry_job, kind="ghost", height=30,
                                     tip="Give the selected job's failed chunks another go.")
        self.retry_btn.pack(side="left")
        self.remove_btn = SlantButton(parent, "Remove", self.remove_job, kind="ghost", height=30,
                                      tip="Take a finished job off the list. Its rendered frames are kept.")
        self.remove_btn.pack(side="left")

    def _chunks_header(self, parent):
        SlantButton(parent, "Output folder", self.open_output, kind="ghost", icon="▭", height=30).pack(
            side="left")
        SlantButton(parent, "Logs", self.open_logs, kind="ghost", icon="↗", height=30).pack(side="left")

    @staticmethod
    def _key(farm_obj, status):
        return f"{farm_obj.kind}:{status.id}"

    def _selected_pair(self):
        return next(((f, s) for f, s in self._jobs if self._key(f, s) == self._selected), (None, None))

    def _selected_status(self):
        return self._selected_pair()[1]

    def cancel_job(self):
        farm_obj, status = self._selected_pair()
        if status and not status.finished and messagebox.askyesno(
                "Cancel farm job", f"Stop {Path(status.job['scene']).name} on every PC?\n\n"
                                   "Frames already rendered are kept.", parent=self):
            farm_obj.cancel(status.id)
            self._read_now()

    def retry_job(self):
        farm_obj, status = self._selected_pair()
        if status:
            count = farm_obj.retry_failed(status.id)
            self.app.status(f"{count} chunk(s) will be tried again." if count else "No failed chunks to retry.")
            self._read_now()

    def remove_job(self):
        farm_obj, status = self._selected_pair()
        if not status:
            return
        if not status.finished:
            messagebox.showinfo("Remove", "Cancel the job first (or wait for it to finish).", parent=self)
            return
        farm_obj.remove(status.id)
        self._selected = None
        self._read_now()

    def _output_folder(self, status):
        job = status.job
        text = farm.resolve(self.app.project.root, job.get("output_override") or job.get("output") or "")
        return Path(text).parent if text and not text.startswith("$") else None

    def open_output(self):
        status = self._selected_status()
        folder = self._output_folder(status) if status else None
        if folder and folder.is_dir():
            os.startfile(folder)
        elif status:
            messagebox.showinfo("Farm", "The output folder doesn't exist yet (nothing written so far).",
                                parent=self)

    def open_logs(self):
        farm_obj, status = self._selected_pair()
        if status:
            folder = farm_obj.jobs_dir / status.id / "logs"
            if folder.is_dir():
                os.startfile(folder)

    # --- Reading the farm folder ------------------------------------------------

    def refresh(self):
        self._show_folder()
        self._read_now()

    def _read_now(self):
        self._last_read = 0.0

    def _start_read(self):
        if self._reading:
            return
        self._reading = True
        farms_now = self.farms

        def work():
            jobs, workers = [], {}
            try:
                for f in farms_now:
                    try:
                        jobs += [(f, status) for status in f.all_status()]
                        for info in f.workers():
                            seen = workers.get(info["pc"])
                            # A PC checks in on every farm it watches; show it once, online if any says so.
                            if seen is None or (info["online"] and not seen["online"]):
                                workers[info["pc"]] = info
                    except OSError:
                        pass
                jobs.sort(key=lambda pair: pair[1].job.get("submitted", ""))
                self._snapshot = (farms_now, jobs, list(workers.values()))
            finally:
                self._reading = False

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        visible = self.app.current_page == "farm"
        if visible and time.time() - self._last_read >= READ_EVERY_S and not self._reading:
            self._last_read = time.time()
            self._start_read()
        if self._snapshot is not None:
            farms_read, jobs, workers = self._snapshot
            self._snapshot = None
            if farms_read is self.farms:
                self._jobs, self._workers = jobs, workers
                self._sync_jobs()
                self._sync_pcs()
                self._show_chunks()
        self._sync_worker()
        self.after(POLL_MS, self._poll)

    def _sync_worker(self):
        worker = self.worker
        running = bool(worker and worker.running)
        self.worker_btn.set_text("Stop rendering farm jobs" if running else "Render farm jobs on this PC")
        if not running:
            self.worker_state.configure(text=worker.state if worker and worker.state != "Stopped" else
                                        "Not rendering farm jobs", fg=MUTED)
            self.worker_bar.set(0)
            self.worker_info.configure(text="Turn this on in the lab PCs you're logged into. Each one takes a "
                                            "chunk of frames at a time, and doesn't sleep while it's on.")
        else:
            job = worker.current
            self.worker_state.configure(text=worker.state, fg=RED if job else TEXT)
            if job and job.total:
                fraction = (job.frames_done + (job.frame_percent or 0) / 100.0 *
                            (job.current_frame is not None)) / job.total
                self.worker_bar.set(fraction)
                self.worker_info.configure(text=f"{job.hip.name}  ·  frames {job.start}-{job.end}  ·  "
                                                f"{job.progress_label}")
            else:
                self.worker_bar.set(0)
                self.worker_info.configure(text=f"Waiting for work  ·  {worker.chunks_done} chunk(s) done so far")
        if len(self.events) != self._shown_events:
            if len(self.events) > self._shown_events:
                self.event_view.append_lines(self.events[self._shown_events:])
            else:
                self.event_view.set_lines(self.events)
            self._shown_events = len(self.events)

    def _job_row(self, status):
        row = tk.Frame(self.jobs_list, bg=PANEL, cursor="hand2")
        row.pack(fill="x")
        inner = tk.Frame(row, bg=PANEL)
        inner.pack(fill="x", padx=px(18), pady=px(10))
        top = tk.Frame(inner, bg=PANEL)
        top.pack(fill="x")
        farm_obj = next(f for f, s in self._jobs if s is status)
        label(top, "TEAM " if farm_obj.kind == farm.TEAM else "MINE ", Fonts.small,
              AMBER if farm_obj.kind == farm.TEAM else MUTED).pack(side="left")
        label(top, Path(status.job["scene"]).stem, Fonts.mono).pack(side="left")
        label(top, f" · {status.job['node'].rsplit('/', 1)[-1]}", Fonts.mono, DIM).pack(side="left")
        label(top, f"  by {status.job.get('user', '?')}", Fonts.small, DIM).pack(side="left")
        state = label(top, "", Fonts.small, MUTED)
        state.pack(side="right")
        mid = tk.Frame(inner, bg=PANEL)
        mid.pack(fill="x", pady=(px(6), px(4)))
        bar = ProgressBar(mid)
        bar.pack(side="left", fill="x", expand=True)
        pct = label(mid, "", Fonts.small, MUTED, width=5, anchor="e")
        pct.pack(side="right")
        info = label(inner, "", Fonts.small, DIM)
        info.pack(anchor="w")
        sep = separator(self.jobs_list)
        sep.pack(fill="x")

        def pick(_e=None, key=self._key(farm_obj, status)):
            self._selected = key
            self._sync_jobs()
            self._show_chunks()

        for widget in (row, inner, top, mid, info, state, pct) + tuple(top.winfo_children()):
            widget.bind("<Button-1>", pick)
        return row, sep, state, bar, pct, info

    def _sync_jobs(self):
        live = {self._key(f, s) for f, s in self._jobs}
        for key in [k for k in self._job_rows if k not in live]:
            row, sep = self._job_rows.pop(key)[:2]
            row.destroy()
            sep.destroy()
        for farm_obj, status in self._jobs:
            key = self._key(farm_obj, status)
            if key not in self._job_rows:
                self._job_rows[key] = self._job_row(status)
            row, _, state, bar, pct, info = self._job_rows[key]
            fraction = status.frames_done / status.frames_total if status.frames_total else 0.0
            bar.set(fraction, done=status.status == "Done", failed=status.status in ("Failed", "Cancelled"))
            pct.configure(text=f"{int(fraction * 100)}%")
            state.configure(text=status.status, fg=RED if status.status == "Failed" else MUTED)
            parts = [f"Frames {status.job['start']}–{status.job['end']}",
                     f"{status.count('done')}/{len(status.chunks)} chunks"]
            if status.pcs:
                parts.append("on " + ", ".join(status.pcs))
            if status.count("failed"):
                parts.append(f"{status.count('failed')} failed")
            parts.append(_ago(status.job.get("submitted")))
            info.configure(text="  ·  ".join(p for p in parts if p))
            set_bg(row, PANEL_HI if key == self._selected else PANEL)
            bar.configure(bg=row["bg"])
        if self._jobs:
            self.jobs_empty.pack_forget()
        elif not self.jobs_empty.winfo_ismapped():
            self.jobs_empty.pack(anchor="w", padx=px(18), pady=px(14))
        status = self._selected_status()
        self.cancel_btn.set_enabled(bool(status) and not status.finished)
        self.retry_btn.set_enabled(bool(status) and status.count("failed") + status.count("retry") > 0)
        self.remove_btn.set_enabled(bool(status) and status.finished)

    def _show_chunks(self):
        status = self._selected_status()
        if status is None:
            self.chunk_title.configure(text="Select a job to see its chunks.", fg=MUTED)
            self.chunk_view.set_lines([])
            return
        job = status.job
        res = f"  ·  {job['res_percent']}%" if job.get("res_percent", 100) != 100 else ""
        self.chunk_title.configure(
            text=f"{Path(job['scene']).name}  ·  {job['node']}  ·  Houdini {job.get('houdini_version') or '?'}"
                 f"{res}  ·  {job['chunk_size']} frames per chunk", fg=TEXT)
        lines = []
        for c in status.chunks:
            detail = c.pc
            if c.state == "rendering" and c.frame is not None:
                detail += f"  frame {c.frame}"
            elif c.state == "done" and c.seconds is not None:
                detail += f"  {farm._duration(c.seconds)}"
            elif c.state in ("failed", "retry"):
                detail += f"  try {c.attempts}/{farm.MAX_ATTEMPTS}: {c.message}"
            prefix = "ERROR " if c.state == "failed" else "WARNING " if c.state == "retry" else ""
            lines.append(f"{prefix}{c.label:<12} {c.state:<10} {detail}".rstrip())
        self.chunk_view.set_lines(lines)

    def _sync_pcs(self):
        for child in self.pcs_list.winfo_children():
            child.destroy()
        if not self._workers:
            label(self.pcs_list, "No PCs have rendered farm jobs yet.", Fonts.body, DIM).pack(
                anchor="w", padx=px(18), pady=px(14))
            return
        for info in sorted(self._workers, key=lambda w: (not w["online"], w["pc"])):
            row = tk.Frame(self.pcs_list, bg=PANEL)
            row.pack(fill="x", padx=px(18), pady=px(8))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            top = tk.Frame(text, bg=PANEL)
            top.pack(anchor="w")
            label(top, "●", Fonts.small, RED if info["online"] and info.get("state") == "Rendering" else
                  TEXT if info["online"] else DIM).pack(side="left")
            label(top, f" {info['pc']}", Fonts.mono, TEXT if info["online"] else DIM).pack(side="left")
            if info["online"]:
                detail = info.get("state", "")
                if info.get("state") == "Rendering":
                    detail = f"{info.get('scene', '')} {info.get('chunk', '')}"
                    if info.get("frame") is not None:
                        detail += f", frame {info['frame']}"
                watching = [{farm.MINE: "own H:", farm.TEAM: "team"}.get(k, k) for k in info.get("farms", [])]
                if watching and info.get("state") != "Rendering":
                    detail += "  ·  " + " + ".join(watching)
            else:
                detail = f"Off  ·  last seen {_ago(info.get('updated'))}"
            label(text, detail, Fonts.small, MUTED if info["online"] else DIM).pack(anchor="w")
            if info["online"]:
                button = SlantButton(row, "Stop", lambda pc=info["pc"]: self.stop_pc(pc), kind="ghost", height=26)
                button.pack(side="right")
                Tooltip(button, "Ask this PC to stop rendering farm jobs. Its current chunk goes back to the farm.")
            separator(self.pcs_list).pack(fill="x")

    def stop_pc(self, pc):
        if pc == farm.pc_name() and self.worker and self.worker.running:
            self.worker.stop()
        else:
            for f in self.farms:
                try:
                    f.ask_to_stop(pc)
                except OSError:
                    pass
            self.app.status(f"Asked {pc} to stop. It notices within half a minute.")
        self._read_now()
