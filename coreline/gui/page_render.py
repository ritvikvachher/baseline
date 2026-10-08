"""Render: send a Houdini scene to the local render queue (hython + Karma) and watch it."""
from __future__ import annotations

import os
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import apps, farm
from ..project import PipelineError
from ..render import RES_CHOICES, RenderJob, RenderQueue, log_path_for, scan_render_nodes
from .page_assets import display_name
from .page_base import Page
from .theme import BG, BORDER, DIM, FIELD, MUTED, PANEL, PANEL_HI, RED, TEXT, Fonts, px
from .widgets import (CheckBox, LogView, Panel, ProgressBar, ScrollFrame, SlantButton, Tooltip, label,
                      separator, set_bg)

POLL_MS = 500


def is_karma_node(node) -> bool:
    return node is not None and ("karma" in node.type.lower() or "usd render" in node.type.lower())


def parse_frames(text: str):
    """'1001-1096' -> (1001, 1096); '1001' -> (1001, 1001)."""
    match = re.fullmatch(r"\s*(-?\d+)\s*(?:[-–to]+\s*(-?\d+))?\s*", text)
    if not match:
        raise ValueError(text)
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    return start, end


class RenderPage(Page):
    nav_title = "Render"

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.queue = RenderQueue()
        self._nodes = []
        self._houdinis = {}
        self._job_rows = {}      # id(job) -> (row, widgets)
        self._selected = None    # the job whose log is shown
        self._shown_lines = 0
        self._scan_result = None
        self._scanning = False
        self._scene_context = (None, None)
        self._shot_output = None  # the shot's render folder path for this scene, if the project has one
        self._shot_frames = None  # the shot's (first, last) frame, if set

        self.header("Houdini · Karma", "Render")
        body = tk.Frame(self.content, bg=BG)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        left = Panel(body, "Render")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(18)))
        left.configure(width=px(400))
        left.grid_propagate(False)
        left.pack_propagate(False)
        submit_bar = tk.Frame(left.body, bg=PANEL)
        submit_bar.pack(side="bottom", fill="x")
        separator(submit_bar).pack(fill="x")
        self.submit_btn = SlantButton(submit_bar, "Submit render", self.add_job, kind="primary", icon="⚙",
                                      height=42, width=360)
        self.submit_btn.pack(fill="x", padx=px(18), pady=px(14))
        scroll = ScrollFrame(left.body, bg=PANEL)
        scroll.pack(fill="both", expand=True)
        self.form = tk.Frame(scroll.inner, bg=PANEL)
        self.form.pack(fill="x", padx=px(18), pady=px(14))
        self._build_form(self.form)

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        queue = Panel(right, "Queue", right=self._queue_header)
        queue.pack(fill="x")
        queue_scroll = ScrollFrame(queue.body, bg=PANEL)
        queue_scroll.configure(height=px(230))
        queue_scroll.pack_propagate(False)
        queue_scroll.pack(fill="x")
        self.queue_list = queue_scroll.inner
        self.queue_empty = label(self.queue_list, "No renders yet. Set one up on the left and click "
                                                  "'Submit render'.", Fonts.body, DIM)
        self.queue_empty.pack(anchor="w", padx=px(18), pady=px(14))

        log = Panel(right, "Render log", right=self._log_header)
        log.pack(fill="both", expand=True, pady=(px(18), 0))
        self.job_message = label(log.body, "Select a render in the queue to see its log.", Fonts.body, MUTED,
                                 wraplength=px(700), justify="left")
        self.job_message.pack(anchor="w", padx=px(18), pady=(px(12), px(8)))
        self.log = LogView(log.body)
        self.log.pack(fill="both", expand=True, padx=px(18), pady=(0, px(18)))
        self.after(POLL_MS, self._poll)

    # --- Form -------------------------------------------------------------

    def _field(self, parent, text):
        label(parent, text, Fonts.small, MUTED).pack(anchor="w", pady=(px(10), px(3)))

    def _build_form(self, f):
        self._field(f, "Scene")
        self.scene_var = tk.StringVar()
        self.scene_entry = ttk.Entry(f, textvariable=self.scene_var, state="readonly")
        self.scene_entry.pack(fill="x")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", pady=(px(6), 0))
        SlantButton(row, "Use selected", self.use_selected_scene, height=30,
                    tip="Use the work file selected in Assets & Shots.").pack(side="left")
        SlantButton(row, "Browse...", self.browse, kind="ghost", height=30).pack(side="left")
        self.scene_hint = label(f, "", Fonts.small, MUTED, wraplength=px(350), justify="left")
        self.scene_hint.pack(anchor="w", pady=(px(4), 0))

        self._field(f, "Houdini version")
        self.houdini_box = ttk.Combobox(f, state="readonly")
        self.houdini_box.pack(fill="x")
        self.houdini_hint = label(f, "", Fonts.small, MUTED, wraplength=px(350), justify="left")
        self.houdini_hint.pack(anchor="w", pady=(px(3), 0))

        self._field(f, "Render node")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x")
        self.node_box = ttk.Combobox(row, state="readonly")
        self.node_box.pack(side="left", fill="x", expand=True)
        self.node_box.bind("<<ComboboxSelected>>", lambda e: self._on_node_selected())
        self.scan_btn = SlantButton(row, "Find", self.scan, height=30, width=72,
                                    tip="Open the scene in the background and list its render nodes.")
        self.scan_btn.pack(side="left", padx=(px(6), 0))
        self.scan_status = label(f, "", Fonts.small, MUTED, wraplength=px(350), justify="left")
        self.scan_status.pack(anchor="w", pady=(px(3), 0))

        grid = tk.Frame(f, bg=PANEL)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=3)
        grid.columnconfigure(1, weight=1)
        label(grid, "Frames", Fonts.small, MUTED).grid(row=0, column=0, sticky="w", pady=(px(10), px(3)))
        label(grid, "Every", Fonts.small, MUTED).grid(row=0, column=1, sticky="w", pady=(px(10), px(3)),
                                                      padx=(px(10), 0))
        self.frames_var = tk.StringVar()
        self.step_var = tk.StringVar(value="1")
        ttk.Entry(grid, textvariable=self.frames_var, font=Fonts.mono).grid(row=1, column=0, sticky="ew")
        ttk.Entry(grid, textvariable=self.step_var, width=5, font=Fonts.mono).grid(row=1, column=1, sticky="ew",
                                                                                   padx=(px(10), 0))
        links = tk.Frame(f, bg=PANEL)
        links.pack(fill="x", pady=(px(4), 0))
        self.node_range_link = self._link(links, "Node's range", self.use_node_range)
        label(links, "·", Fonts.small, DIM).pack(side="left", padx=px(6))
        self.shot_range_link = self._link(links, "Shot's range", self.use_shot_range)

        self._field(f, "Resolution")
        self.res_box = ttk.Combobox(f, state="readonly", values=[text for _, text in RES_CHOICES])
        self.res_box.set(RES_CHOICES[0][1])
        self.res_box.pack(fill="x")
        Tooltip(self.res_box, "For Karma / USD Render nodes. Lower = faster previews. "
                              "The scene file isn't changed.")
        self.skip_var = tk.BooleanVar(value=True)
        skip = CheckBox(f, self.skip_var, "Skip frames that are already rendered")
        skip.pack(anchor="w", pady=(px(12), 0))
        Tooltip(skip, "If a render was stopped or the PC restarted, submit it again with this ticked and "
                      "it carries on from where it stopped.")
        self.output_var = tk.BooleanVar(value=True)
        self.output_check = CheckBox(f, self.output_var, "Save to the shot's render folder",
                                     command=self._show_output)
        self.output_check.pack(anchor="w", pady=(px(8), 0))
        Tooltip(self.output_check, "Karma writes to the shot's render/beauty folder, in a folder named after "
                                   "the scene, so each version keeps its own frames. Untick to use the "
                                   "node's own output path. The scene file isn't changed.")

        self.farm_var = tk.BooleanVar(value=False)
        farm_check = CheckBox(f, self.farm_var, "Send to the render farm", command=self._update_buttons)
        farm_check.pack(anchor="w", pady=(px(8), 0))
        Tooltip(farm_check, "Split the frames into chunks that the lab PCs rendering farm jobs share "
                            "(see the Farm page). The scene must be saved in the project.")
        chunk_row = tk.Frame(f, bg=PANEL)
        chunk_row.pack(fill="x", pady=(px(6), 0))
        label(chunk_row, "Frames per chunk", Fonts.small, MUTED).pack(side="left", padx=(px(28), px(8)))
        self.chunk_var = tk.StringVar(value=str(farm.DEFAULT_CHUNK))
        self.chunk_entry = ttk.Entry(chunk_row, textvariable=self.chunk_var, width=5, font=Fonts.mono)
        self.chunk_entry.pack(side="left")
        Tooltip(self.chunk_entry, "Smaller chunks share the work out better and lose less when a PC "
                                  "restarts; bigger ones open the scene less often.")
        farm_row = tk.Frame(f, bg=PANEL)
        farm_row.pack(fill="x", pady=(px(6), 0))
        label(farm_row, "Farm", Fonts.small, MUTED).pack(side="left", padx=(px(28), px(8)))
        labels = [f.label for f in farm.farms_for(self.app.project)]
        self.farm_box = ttk.Combobox(farm_row, values=labels, state="readonly", width=18)
        self.farm_box.set(labels[0])
        self.farm_box.pack(side="left")
        Tooltip(self.farm_box, "My PCs (H:): only PCs logged in as you, fast. Team (OneDrive): any teammate's "
                               "PC rendering farm jobs can help; chunks start about a minute later.")

    def _link(self, parent, text, command):
        link = label(parent, text, Fonts.small, MUTED, cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: command() if link.cget("fg") != DIM else None)
        return link

    def _queue_header(self, parent):
        self.stop_btn = SlantButton(parent, "Stop", self.stop_job, icon="■", height=30,
                                    tip="Stop the selected render (or take it out of the line).")
        self.stop_btn.pack(side="left")
        SlantButton(parent, "Clear finished", self.clear_finished, kind="ghost", height=30).pack(side="left")

    def _log_header(self, parent):
        SlantButton(parent, "Output folder", self.open_output, kind="ghost", icon="▭", height=30).pack(
            side="left")
        SlantButton(parent, "Log file", self.open_log, kind="ghost", icon="↗", height=30).pack(side="left")

    # --- Scene & version --------------------------------------------------

    def refresh(self):
        self._fill_houdinis(self.houdini_box.get())
        self._read_shot_frames()
        self._update_buttons()

    def breadcrumb(self):
        return ["Render"] + ([Path(self.scene_var.get()).name] if self.scene_var.get() else [])

    def _fill_houdinis(self, keep=None):
        installs = self.app.installations.get("houdini", [])
        self._houdinis = {i.display: i for i in installs if i.tool(self.app.app_config["houdini"], "hython")}
        self.houdini_box.configure(values=list(self._houdinis))
        if keep in self._houdinis:
            self.houdini_box.set(keep)
        elif self._houdinis:
            self.houdini_box.set(next(iter(self._houdinis)))
        else:
            self.houdini_box.set("")
            self.houdini_hint.configure(text="No Houdini with hython found. Add it in Settings > Software.")

    def use_selected_scene(self):
        path = self.app.selection["file"]
        if not path:
            messagebox.showinfo("Render", "Select a Houdini work file in Assets & Shots first.", parent=self)
            return
        self.set_scene(path)

    def browse(self):
        start = self.scene_var.get() or str(self.app.project.root)
        path = filedialog.askopenfilename(parent=self, title="Choose a Houdini scene",
                                          initialdir=str(Path(start).parent if Path(start).is_file() else start),
                                          filetypes=[("Houdini scenes", "*.hip *.hipnc *.hiplc")])
        if path:
            self.set_scene(Path(path))

    def set_scene(self, path: Path):
        path = self.app.to_project_path(path)
        if apps.app_for_extension(self.app.app_config, path.suffix) != "houdini":
            messagebox.showinfo("Render", f"{path.name} isn't a Houdini scene.", parent=self)
            return
        self.scene_var.set(str(path))
        self.scene_entry.xview_moveto(1.0)
        self._nodes = []
        self.node_box.configure(values=[])
        self.node_box.set("")
        self.scan_status.configure(text="")
        entity, task = self.app.project.context_for_path(path)
        self._scene_context = (entity, task)
        self._shot_output = self.app.project.render_output(entity, path)
        self._read_shot_frames()
        if entity:
            text = f"{display_name(entity)}" + (f"  ·  {task}" if task else "")
            if entity.kind == "shot":
                frames = self._shot_frames
                text += f"  ·  shot frames {frames[0]}–{frames[1]}" if frames else "  ·  shot frames not set"
            self.scene_hint.configure(text=text)
        else:
            self.scene_hint.configure(text="This scene is outside the project's assets and shots.")
        inst, saved = self.app.installation_for_file(path)
        self._fill_houdinis(inst.display if inst else None)
        if saved and inst and inst.version == saved:
            self.houdini_hint.configure(text=f"Matches the version the scene was saved with ({saved}).")
        elif saved:
            self.houdini_hint.configure(text=f"Saved with Houdini {saved}, which isn't installed here. "
                                             "Another version will usually work.")
        else:
            self.houdini_hint.configure(text="")
        self.app.set_breadcrumb(self.breadcrumb())
        self._update_buttons()
        self.scan()

    # --- Render nodes -----------------------------------------------------

    def _hython(self):
        inst = self._houdinis.get(self.houdini_box.get())
        return (inst, inst.tool(self.app.app_config["houdini"], "hython")) if inst else (None, None)

    def scan(self):
        scene = self.scene_var.get()
        inst, hython = self._hython()
        if not scene or not hython or self._scanning:
            return
        self._scanning = True
        self.scan_status.configure(text="Opening the scene in Houdini to find render nodes...")
        self._update_buttons()
        entity, task = self._scene_context
        env = self.app.project.context_env(entity, task, self.app.user)

        def work():
            try:
                self._scan_result = (scene, scan_render_nodes(hython, Path(scene), env))
            except PipelineError as e:
                self._scan_result = (scene, e)

        threading.Thread(target=work, daemon=True).start()

    def _finish_scan(self, scene, result):
        self._scanning = False
        if scene != self.scene_var.get():
            self._update_buttons()
            self.scan()
            return
        if isinstance(result, Exception):
            self.scan_status.configure(text="Couldn't read the scene.")
            messagebox.showerror("Find render nodes", str(result), parent=self)
        elif not result:
            self.scan_status.configure(text="No render nodes found in this scene.")
        else:
            self._nodes = result
            self.node_box.configure(values=[n.display for n in result])
            first = next((n for n in result if is_karma_node(n)), result[0])
            self.node_box.set(first.display)
            self._on_node_selected()
        self._update_buttons()

    def _selected_node(self):
        return next((n for n in self._nodes if n.display == self.node_box.get()), None)

    def _on_node_selected(self):
        if self._selected_node():
            self.use_node_range()
        self._show_output()

    def _show_output(self):
        node = self._selected_node()
        if node:
            output = self._output_override(node) or node.output
            self.scan_status.configure(text=f"Writes to {output}" if output else
                                       f"Found {len(self._nodes)} render node(s).")
        self._update_buttons()

    def _output_override(self, node):
        """The shot's render folder path, when it applies to this node and the box is ticked."""
        if self._shot_output and self.output_var.get() and is_karma_node(node):
            return self._shot_output
        return ""

    def use_node_range(self):
        node = self._selected_node()
        if node:
            self.frames_var.set(f"{node.start}-{node.end}")
            self.step_var.set(str(node.step))

    def _read_shot_frames(self):
        entity, _ = self._scene_context
        self._shot_frames = self.app.project.frame_range(entity) if entity and entity.kind == "shot" else None

    def use_shot_range(self):
        if self._shot_frames:
            self.frames_var.set(f"{self._shot_frames[0]}-{self._shot_frames[1]}")

    # --- Queue ------------------------------------------------------------

    def add_job(self):
        scene, node = self.scene_var.get(), self._selected_node()
        inst, hython = self._hython()
        if not (scene and node and hython):
            return
        try:
            start, end = parse_frames(self.frames_var.get())
            step = int(self.step_var.get())
        except ValueError:
            messagebox.showerror("Render", "Frames should look like 1001-1096, and 'Every' a whole number.",
                                 parent=self)
            return
        if end < start or step < 1:
            messagebox.showerror("Render", "The last frame must be after the first, and 'Every' at least 1.",
                                 parent=self)
            return
        entity, task = self._scene_context
        hip = Path(scene)
        pct = next((p for p, text in RES_CHOICES if text == self.res_box.get()), 100)
        override = self._output_override(node)
        if self.farm_var.get():
            self._send_to_farm(hip, node, inst, start, end, step, override, pct if is_karma_node(node) else 100)
            return
        job = RenderJob(
            hip=hip, node=node.path, start=start, end=end, step=step, hython=hython,
            houdini_label=inst.version, env=self.app.project.context_env(entity, task, self.app.user),
            log_path=log_path_for(self.app.project.logs_dir, self.app.user, hip, node.path),
            output=override.replace("$F4", f"{start:04d}") if override else node.output,
            output_override=override, res_percent=pct if is_karma_node(node) else 100,
            skip_existing=self.skip_var.get(),
        )
        self.queue.add(job)
        self._selected = job
        self._sync_jobs()
        self._show_log(force=True)
        self.app.status(f"Submitted {hip.name} {node.path} (frames {job.frames_label}).")

    def _send_to_farm(self, hip, node, inst, start, end, step, override, res_percent):
        try:
            chunk = int(self.chunk_var.get())
            if chunk < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Render", "'Frames per chunk' should be a whole number, e.g. 10.", parent=self)
            return
        root = self.app.project.root
        if farm.project_relative(root, hip) == str(hip).replace("\\", "/"):
            if not messagebox.askyesno(
                    "Send to the render farm", f"{hip.name} isn't in the project, so other PCs may not be able "
                                               "to open it. Send it anyway?", parent=self):
                return
        farm_page = self.app.pages["farm"]
        choices = dict(farm_page.farm_choices())
        target = choices.get(self.farm_box.get()) or next(iter(choices.values()))
        try:
            job_id = target.submit(
                project_name=self.app.project.name, scene=farm.project_relative(root, hip), node=node.path,
                start=start, end=end, step=step, chunk_size=chunk, houdini_version=inst.version,
                user=self.app.user, output=farm.project_relative(root, node.output),
                output_override=farm.project_relative(root, override), res_percent=res_percent,
                engine=node.type)
        except OSError as e:
            messagebox.showerror("Send to the render farm",
                                 f"Couldn't write to the farm folder {target.folder}:\n{e}\n\n"
                                 "You can change it on the Farm page.", parent=self)
            return
        chunks = len(farm.make_chunks(start, end, step, chunk))
        self.app.status(f"Sent {hip.name} to {target.label} as {chunks} chunk(s). PCs rendering farm jobs pick it "
                        f"up {'within a minute or two' if target.synced else 'within a few seconds'}.")
        farm_page.select_job(target, job_id)
        self.app.go("farm")

    def add_external_job(self, job):
        """A job made elsewhere (e.g. a turntable from Assets & Shots)."""
        self.queue.add(job)
        self._selected = job
        self._sync_jobs()
        self._show_log(force=True)

    def stop_job(self):
        job = self._selected
        if job and not job.finished:
            self.queue.stop(job)
            self.app.status(f"Stopped {job.hip.name} {job.node}.")

    def clear_finished(self):
        self.queue.remove_finished()
        if self._selected and self._selected not in self.queue.jobs:
            self._selected = None
        self._sync_jobs()
        self._show_log(force=True)

    def open_output(self):
        job = self._selected
        folder = Path(job.output).parent if job and job.output else None
        if folder and folder.is_dir():
            os.startfile(folder)
        elif job:
            messagebox.showinfo("Render", "The output folder doesn't exist yet (nothing written so far).",
                                parent=self)

    def open_log(self):
        job = self._selected
        if job and job.log_path.is_file():
            os.startfile(job.log_path)

    def has_active_jobs(self) -> bool:
        return any(not j.finished for j in self.queue.jobs)

    # --- Polling ----------------------------------------------------------

    def _poll(self):
        if self._scan_result is not None:
            scene, result = self._scan_result
            self._scan_result = None
            self._finish_scan(scene, result)
        self._sync_jobs()
        self._show_log()
        self.after(POLL_MS, self._poll)

    def _job_row(self, job):
        row = tk.Frame(self.queue_list, bg=PANEL, cursor="hand2")
        row.pack(fill="x")
        inner = tk.Frame(row, bg=PANEL)
        inner.pack(fill="x", padx=px(18), pady=px(10))
        top = tk.Frame(inner, bg=PANEL)
        top.pack(fill="x")
        label(top, job.hip.stem, Fonts.mono).pack(side="left")
        label(top, f" · {job.node.rsplit('/', 1)[-1]}", Fonts.mono, DIM).pack(side="left")
        status = label(top, "", Fonts.small, MUTED)
        status.pack(side="right")
        mid = tk.Frame(inner, bg=PANEL)
        mid.pack(fill="x", pady=(px(6), px(4)))
        bar = ProgressBar(mid)
        bar.pack(side="left", fill="x", expand=True)
        pct = label(mid, "", Fonts.small, MUTED, width=5, anchor="e")
        pct.pack(side="right")
        info = label(inner, "", Fonts.small, DIM)
        info.pack(anchor="w")
        sep = separator(self.queue_list)
        sep.pack(fill="x")

        def pick(_e=None):
            self._selected = job
            self._show_log(force=True)
            self._sync_jobs()

        for widget in (row, inner, top, mid, info, status, pct) + tuple(top.winfo_children()):
            widget.bind("<Button-1>", pick)
        return row, sep, status, bar, pct, info

    def _sync_jobs(self):
        live = {id(j) for j in self.queue.jobs}
        for key in [k for k in self._job_rows if k not in live]:
            row, sep = self._job_rows.pop(key)[:2]
            row.destroy()
            sep.destroy()
        for job in self.queue.jobs:
            if id(job) not in self._job_rows:
                self._job_rows[id(job)] = self._job_row(job)
            row, _, status, bar, pct, info = self._job_rows[id(job)]
            fraction = 0.0
            if job.total:
                fraction = (job.frames_done + (job.frame_percent or 0) / 100.0 *
                            (job.status == "Rendering" and job.current_frame is not None)) / job.total
            if job.status == "Done":
                fraction = 1.0
            bar.set(fraction, done=job.status == "Done", failed=job.status in ("Failed", "Stopped"))
            pct.configure(text=f"{int(fraction * 100)}%")
            status.configure(text=job.status, fg=RED if job.status == "Failed" else MUTED)
            extra = f" · frame {job.current_frame}" if job.status == "Rendering" and job.current_frame else ""
            skipped = f" · {job.frames_skipped} skipped" if job.frames_skipped else ""
            info.configure(text=f"{job.info_label}{extra}{skipped}")
            set_bg(row, PANEL_HI if job is self._selected else PANEL)
            bar.configure(bg=row["bg"])
        if self.queue.jobs:
            self.queue_empty.pack_forget()
        elif not self.queue_empty.winfo_ismapped():
            self.queue_empty.pack(anchor="w", padx=px(18), pady=(0, px(10)))
        self._update_buttons()

    def _show_log(self, force=False):
        job = self._selected or self.queue.running
        if job is None:
            if force:
                self.log.set_lines([])
                self.job_message.configure(text="Select a render in the queue to see its log.", fg=MUTED)
            return
        if force or job is not getattr(self, "_log_job", None):
            self.log.set_lines(job.lines)
            self._log_job, self._shown_lines = job, len(job.lines)
        elif len(job.lines) != self._shown_lines:
            if len(job.lines) > self._shown_lines:
                self.log.append_lines(job.lines[self._shown_lines:])
            else:
                self.log.set_lines(job.lines)
            self._shown_lines = len(job.lines)
        text = f"{job.hip.name}  ·  {job.node}  ·  {job.status}"
        if job.message:
            text += f"  —  {job.message}"
        self.job_message.configure(text=text, fg="#ff6b6b" if job.status == "Failed" else TEXT)

    def _update_buttons(self):
        _, hython = self._hython()
        self.scan_btn.set_enabled(bool(self.scene_var.get()) and bool(hython) and not self._scanning)
        self.node_box.configure(state="readonly" if self._nodes else "disabled")
        self.res_box.configure(state="readonly" if is_karma_node(self._selected_node()) else "disabled")
        self.node_range_link.configure(fg=MUTED if self._nodes else DIM)
        self.shot_range_link.configure(fg=MUTED if self._shot_frames else DIM)
        self.output_check.set_enabled(bool(self._shot_output) and is_karma_node(self._selected_node()))
        self.submit_btn.set_enabled(bool(self._selected_node()) and bool(hython) and not self._scanning)
        self.submit_btn.set_text("Send to farm" if self.farm_var.get() else "Submit render")
        self.chunk_entry.configure(state="normal" if self.farm_var.get() else "disabled")
        self.farm_box.configure(state="readonly" if self.farm_var.get() else "disabled")
        job = self._selected
        self.stop_btn.set_enabled(bool(job) and not job.finished)
