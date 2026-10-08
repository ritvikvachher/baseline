"""Publish ('Version up'): check a work file, add notes, publish it and set the task status."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import publish_usd, thumbnails
from ..checks import FAIL, PASS, WARN, run_checks, summary
from ..project import STATUSES, PipelineError
from .page_assets import display_name
from .page_base import Page
from .theme import AMBER, BG, BORDER, DIM, FIELD, MUTED, PANEL, RED, TEXT, Fonts, px
from .widgets import CheckBox, Panel, SlantButton, Stripes, eyebrow, label, relative_time, separator

ICONS = {PASS: ("✓", TEXT), WARN: ("!", AMBER), FAIL: ("✕", "#ff5d5d")}


class PublishPage(Page):
    nav_title = "Publish"

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Publish", "Version up")
        self._entities = {}
        self._workfiles = {}
        self.entity = self.task = self.workfile = None
        self.checks = []

        # Pickers: what to publish
        pick = tk.Frame(self.content, bg=BG)
        pick.pack(fill="x", pady=(0, px(12)))
        self.entity_box = self._picker(pick, "Asset or shot", 26)
        self.task_box = self._picker(pick, "Step", 12)
        self.file_box = self._picker(pick, "Work file", 34)
        self.entity_box.bind("<<ComboboxSelected>>", lambda e: self._pick_entity())
        self.task_box.bind("<<ComboboxSelected>>", lambda e: self._pick_task())
        self.file_box.bind("<<ComboboxSelected>>", lambda e: self._pick_file())

        # Source bar
        source = tk.Frame(self.content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        source.pack(fill="x")
        self.source_thumb = Stripes(source, width=116, height=62)
        self.source_thumb.pack(side="left", padx=px(20), pady=px(18))
        # Pack the version numbers before the (long) path so the path gets squeezed, not them.
        versions = tk.Frame(source, bg=PANEL)
        versions.pack(side="right", padx=px(24))
        text = tk.Frame(source, bg=PANEL)
        text.pack(side="left", fill="x", expand=True)
        eyebrow(text, "Publishing from", MUTED).pack(anchor="w")
        self.source_name = label(text, "", (Fonts.mono[0], 12))
        self.source_name.pack(anchor="w", pady=(px(4), px(2)))
        self.source_path = label(text, "", Fonts.mono_small, MUTED, anchor="w")
        self.source_path.pack(anchor="w", fill="x")
        self.current_label = self._version_block(versions, "Current", MUTED)
        slash = tk.Canvas(versions, width=px(40), height=px(26), bg=PANEL, highlightthickness=0)
        for i, color in enumerate(("#7a1015", "#a8141b", RED)):
            x = px(6 + i * 10)
            slash.create_polygon(x + px(6), px(4), x + px(12), px(4), x + px(6), px(18), x, px(18), fill=color)
        slash.pack(side="left", padx=px(10), pady=(px(18), 0))
        self.publish_label = self._version_block(versions, "Publish as", TEXT)

        # Three panels
        row = tk.Frame(self.content, bg=BG)
        row.pack(fill="both", expand=True, pady=(px(18), 0))
        row.columnconfigure(0, weight=5, uniform="p")
        row.columnconfigure(1, weight=5, uniform="p")
        row.columnconfigure(2, weight=4, uniform="p")
        row.rowconfigure(0, weight=1)
        self.valid_panel = Panel(row, "Validation", right=self._valid_summary)
        self.valid_panel.grid(row=0, column=0, sticky="nsew", padx=(0, px(18)))
        self.out_panel = Panel(row, "Outputs & notes")
        self.out_panel.grid(row=0, column=1, sticky="nsew", padx=(0, px(18)))
        self.history_panel = Panel(row, "History")
        self.history_panel.grid(row=0, column=2, sticky="nsew")
        self._build_outputs()

        # Bottom bar
        bar = tk.Frame(self.content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        # Packed before the panels so the publish button always stays on screen.
        bar.pack(side="bottom", fill="x", pady=(px(18), 0), before=row)
        self.bar_icon = label(bar, "", (Fonts.body[0], 13, "bold"), AMBER)
        self.bar_icon.pack(side="left", padx=(px(20), px(8)), pady=px(14))
        self.bar_text = label(bar, "", Fonts.body, TEXT)
        self.bar_text.pack(side="left")
        self.publish_btn = SlantButton(bar, "Version up & publish", self.do_publish, kind="primary",
                                       icon="⤒", height=40)
        self.publish_btn.pack(side="right", padx=(px(6), px(20)), pady=px(12))
        self.version_only_btn = SlantButton(bar, "Version up only", self.do_version_up, icon="↑", height=40,
                                            tip="Copy to a new version under your name without publishing.")
        self.version_only_btn.pack(side="right")
        # The status sits here, not under the notes, so the notes box keeps its height on small screens.
        self.status_box = ttk.Combobox(bar, state="readonly", values=STATUSES, width=14)
        self.status_box.set("In review")
        self.status_box.pack(side="right", padx=(px(6), px(14)))
        label(bar, "Set status to", Fonts.small, MUTED).pack(side="right")

    # --- Building blocks --------------------------------------------------

    def _picker(self, parent, text, width):
        holder = tk.Frame(parent, bg=BG)
        holder.pack(side="left", padx=(0, px(14)))
        label(holder, text, Fonts.small, MUTED, bg=BG).pack(anchor="w")
        box = ttk.Combobox(holder, state="readonly", width=width)
        box.pack(anchor="w", pady=(px(3), 0))
        return box

    def _version_block(self, parent, title, color):
        block = tk.Frame(parent, bg=PANEL)
        block.pack(side="left")
        eyebrow(block, title, MUTED).pack(anchor="e")
        value = label(block, "", Fonts.version_big, color)
        value.pack(anchor="e")
        return value

    def _valid_summary(self, parent):
        self.summary_label = label(parent, "", Fonts.small, MUTED)
        self.summary_label.pack()

    def _build_outputs(self):
        body = self.out_panel.body
        self.outputs = tk.Frame(body, bg=PANEL)
        self.outputs.pack(fill="x", padx=px(18), pady=(px(10), 0))
        self.scene_var = tk.BooleanVar(value=True)
        self.version_up_var = tk.BooleanVar(value=True)
        self.scene_row = self._output_row(self.outputs, self.scene_var, "Scene file (always published)",
                                          enabled=False)
        self.vup_row = self._output_row(self.outputs, self.version_up_var, "Version up after publishing")
        self.version_up_var.trace_add("write", lambda *a: self._update_buttons())

        notes = tk.Frame(body, bg=PANEL)
        notes.pack(fill="both", expand=True, padx=px(18), pady=(px(14), px(16)))
        label(notes, "Publish notes", Fonts.small, MUTED).pack(anchor="w")
        self.notes = tk.Text(notes, height=4, bg=FIELD, fg=TEXT, insertbackground=TEXT, bd=0, wrap="word",
                             font=Fonts.body, highlightthickness=1, highlightbackground=BORDER,
                             highlightcolor=RED, padx=px(10), pady=px(8))
        self.notes.pack(fill="both", expand=True, pady=(px(4), 0))

    def _output_row(self, parent, var, title, enabled=True):
        box = CheckBox(parent, var, title, detail=" ", enabled=enabled)
        box.pack(fill="x", pady=px(6))
        separator(parent).pack(fill="x")
        return box

    # --- Selection --------------------------------------------------------

    def refresh(self):
        project = self.app.project
        entities = project.list_entities("asset") + project.list_entities("shot")
        self._entities = {f"{'Asset' if e.kind == 'asset' else 'Shot'}: {e.label}": e for e in entities}
        self.entity_box.configure(values=list(self._entities))
        sel = self.app.selection
        entity = sel["entity"]
        if entity is None or entity.path not in [e.path for e in entities]:
            self.entity_box.set("")
            self._fill_tasks(None)
            return
        self.entity_box.set(next(k for k, e in self._entities.items() if e.path == entity.path))
        self._fill_tasks(entity, sel["task"], sel["file"])

    def _fill_tasks(self, entity, task=None, file=None):
        if entity is None:
            self.task_box.configure(values=[])
            self.task_box.set("")
            self._fill_files(None, None)
            return
        tasks = self.app.project.entity_tasks(entity)
        self.task_box.configure(values=tasks)
        task = task if task in tasks else self.app.project.current_step(entity)
        self.task_box.set(task)
        self._fill_files(entity, task, file)

    def _fill_files(self, entity, task, file=None):
        self._workfiles = {}
        if entity and task:
            for wf in reversed(self.app.project.list_workfiles(entity, task)):
                self._workfiles[wf.path.name] = wf
        self.file_box.configure(values=list(self._workfiles))
        chosen = next((wf for wf in self._workfiles.values() if file and wf.path == file), None)
        chosen = chosen or next(iter(self._workfiles.values()), None)
        self.file_box.set(chosen.path.name if chosen else "")
        self._show(entity, task, chosen)

    def _pick_entity(self):
        entity = self._entities.get(self.entity_box.get())
        self._fill_tasks(entity)

    def _pick_task(self):
        entity = self._entities.get(self.entity_box.get())
        self._fill_files(entity, self.task_box.get())

    def _pick_file(self):
        entity = self._entities.get(self.entity_box.get())
        self._show(entity, self.task_box.get(), self._workfiles.get(self.file_box.get()))

    # --- Showing a work file ----------------------------------------------

    def _show(self, entity, task, workfile):
        self.entity, self.task, self.workfile = entity, task, workfile
        self.app.select(entity, task, workfile.path if workfile else None)
        project = self.app.project
        self.source_thumb.set_image(thumbnails.find(entity.path) if entity else None)
        for panel in (self.valid_panel, self.history_panel):
            for child in panel.body.winfo_children():
                child.destroy()
        self.checks = []

        if not workfile:
            self.source_name.configure(text="Nothing selected" if not entity else f"{display_name(entity)} / {task}")
            self.source_path.configure(text="Pick an asset or shot, a step and a work file above."
                                       if not entity else "This step has no work files yet.")
            self.current_label.configure(text="—")
            self.publish_label.configure(text="—")
            self.summary_label.configure(text="")
            self.scene_row.set_detail(" ")
            self.vup_row.set_detail(" ")
            label(self.valid_panel.body, "Nothing to check yet.", Fonts.body, MUTED).pack(
                anchor="w", padx=px(18), pady=px(16))
            if entity and task:
                self._fill_history(entity, task)
            self._update_bar()
            return

        part = workfile.part
        publishes = project.list_publishes(entity, task, part)
        self.next_publish = (publishes[-1].version + 1) if publishes else 1
        self.next_work = project.next_version(entity, task, part)
        who = f" ({workfile.user})" if workfile.user else ""
        step = f"{task}  /  {part}" if part else task
        self.source_name.configure(text=f"{display_name(entity)}  /  {step}  /  {workfile.version_label}{who}")
        self.source_path.configure(text=str(workfile.path))
        self.current_label.configure(text=f"v{workfile.version:03d}" if workfile.version else "—")
        self.publish_label.configure(text=f"v{self.next_publish:03d}")
        part_dir, part_name = (f"{part}/", f"{part}_") if part else ("", "")
        self.scene_row.set_detail(f"{project.layout['publish']}/{part_dir}v{self.next_publish:03d}/{entity.name}_"
                                  f"{task}_{part_name}v{self.next_publish:03d}{workfile.path.suffix}")
        self.vup_row.set_detail(f"{entity.name}_{task}_{part_name}v{self.next_work:03d}_{self.app.user}"
                                f"{workfile.path.suffix}")

        self.checks = run_checks(project, self.app.app_config, self.app.installations, entity, task, workfile)
        self.summary_label.configure(text=summary(self.checks))
        for i, check in enumerate(self.checks):
            self._check_row(check)
            if i < len(self.checks) - 1:
                separator(self.valid_panel.body).pack(fill="x")
        self._fill_history(entity, task, part)
        self._update_bar()

    def _check_row(self, check):
        row = tk.Frame(self.valid_panel.body, bg=PANEL)
        row.pack(fill="x", padx=px(18), pady=px(7))
        symbol, color = ICONS[check.state]
        icon = tk.Canvas(row, width=px(26), height=px(26), bg=PANEL, highlightthickness=0)
        icon.create_oval(px(1), px(1), px(25), px(25), outline=color, width=max(1, px(1.4)))
        icon.create_text(px(13), px(13), text=symbol, fill=color, font=(Fonts.body[0], 10, "bold"))
        icon.pack(side="left", anchor="n")
        text = tk.Frame(row, bg=PANEL)
        text.pack(side="left", padx=(px(12), 0), fill="x", expand=True)
        label(text, check.name, Fonts.body_bold).pack(anchor="w")
        label(text, check.detail, Fonts.small, MUTED, wraplength=px(300), justify="left").pack(anchor="w")

    def _fill_history(self, entity, task, part=""):
        body = self.history_panel.body
        publishes = list(reversed(self.app.project.list_publishes(entity, task, part)))
        if not publishes:
            label(body, f"Nothing published for {'the ' + part + ' part' if part else 'this step'} yet.",
                  Fonts.body, MUTED).pack(
                anchor="w", padx=px(18), pady=px(16))
            return
        for i, pub in enumerate(publishes[:8]):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", padx=px(18), pady=(px(12), 0))
            top = tk.Frame(row, bg=PANEL)
            top.pack(fill="x")
            mark = tk.Canvas(top, width=px(12), height=px(10), bg=PANEL, highlightthickness=0)
            mark.create_polygon(px(3), 0, px(12), 0, px(9), px(10), 0, px(10), fill=RED if i == 0 else "#3a3a40")
            mark.pack(side="left", padx=(0, px(8)))
            label(top, f"v{pub.version:03d}", Fonts.mono, RED if i == 0 else TEXT).pack(side="left")
            label(top, relative_time(pub.info.get("time", "")), Fonts.small, MUTED).pack(side="right")
            note = pub.info.get("comment") or "no notes"
            label(row, f"{note} · {pub.info.get('user', '')}", Fonts.small, MUTED, wraplength=px(240),
                  justify="left").pack(anchor="w", padx=(px(20), 0))

    # --- Bottom bar -------------------------------------------------------

    def _update_bar(self):
        failed = [c for c in self.checks if c.state == FAIL]
        warned = [c for c in self.checks if c.state == WARN]
        if not self.workfile:
            self.bar_icon.configure(text="")
            self.bar_text.configure(text="Choose a work file to publish.", fg=MUTED)
        elif failed:
            self.bar_icon.configure(text="✕", fg="#ff5d5d")
            self.bar_text.configure(text=f"Fix {len(failed)} failed check{'s' if len(failed) > 1 else ''} "
                                         "before publishing.", fg=TEXT)
        elif warned:
            self.bar_icon.configure(text="⚠", fg=AMBER)
            self.bar_text.configure(text=f"{len(warned)} warning{'s' if len(warned) > 1 else ''}: "
                                         "check them, then publish if it's right.", fg=TEXT)
        else:
            self.bar_icon.configure(text="✓", fg=TEXT)
            self.bar_text.configure(text="All checks passed. Ready to publish.", fg=TEXT)
        self._update_buttons()

    def _update_buttons(self):
        ok = bool(self.workfile) and not any(c.state == FAIL for c in getattr(self, "checks", []))
        if self.workfile:
            text = (f"Version up & publish v{self.next_publish:03d}" if self.version_up_var.get()
                    else f"Publish v{self.next_publish:03d}")
        else:
            text = "Version up & publish"
        self.publish_btn.set_text(text)
        self.publish_btn.set_enabled(ok)
        self.version_only_btn.set_enabled(bool(self.workfile))

    # --- Actions ----------------------------------------------------------

    def do_publish(self):
        if not self.workfile:
            return
        project, user = self.app.project, self.app.user
        comment = self.notes.get("1.0", "end").strip()
        try:
            pub = project.publish(self.workfile, self.entity, self.task, user, comment)
            if self.workfile.part:
                project.update_part(self.entity, self.task, self.workfile.part, user, status=self.status_box.get())
            else:
                project.update_task(self.entity, self.task, user, status=self.status_box.get())
            new = project.version_up(self.workfile, self.entity, self.task, user) \
                if self.version_up_var.get() else None
        except PipelineError as e:
            messagebox.showerror("Publish", str(e), parent=self)
            return
        self.notes.delete("1.0", "end")
        step = f"{self.task} {self.workfile.part}" if self.workfile.part else self.task
        message = f"Published {display_name(self.entity)} {step} v{pub.version:03d}"
        usd_job = publish_usd.job_for(project, self.entity, self.task, pub, self.app)
        if usd_job:
            self.app.pages["render"].add_external_job(usd_job)
            message += " · saving a USD copy for Houdini in the background"
        if new:
            message += f" · keep working in {new.path.name}"
        self.app.status(message + ".")
        self._fill_files(self.entity, self.task, new.path if new else self.workfile.path)

    def do_version_up(self):
        if not self.workfile:
            return
        try:
            new = self.app.project.version_up(self.workfile, self.entity, self.task, self.app.user)
        except PipelineError as e:
            messagebox.showerror("Version up", str(e), parent=self)
            return
        self.app.status(f"Created {new.path.name}. Open it to keep working.")
        self._fill_files(self.entity, self.task, new.path)

    def breadcrumb(self):
        sel = self.app.selection
        parts = ["Publish"]
        if sel["entity"] is not None:
            parts.append(display_name(sel["entity"]))
            if sel["task"]:
                parts.append(sel["task"].title())
        return parts
