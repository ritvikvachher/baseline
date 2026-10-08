"""Assets & Shots: browse folders, filter, pick an asset/shot, then work with its steps and files."""
from __future__ import annotations

import json
import os
import re
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import apps, thumbnails, turntable
from ..project import STATUSES, PipelineError
from .page_base import Page
from .theme import BG, BORDER, DIM, MUTED, PANEL, PANEL_HI, RED, TEXT, Fonts, px
from .widgets import (Badge, Chip, FormDialog, ScrollFrame, SlantButton, Stripes, Tooltip, eyebrow, label,
                      separator, set_bg, spaced, status_menu)

# Where the Turntable dialog renders.
RENDER_HERE = "This computer"
RENDER_FARM_ONE = "Farm: one PC makes it all"
RENDER_FARM_SPLIT = "Farm: split between PCs"
TURNTABLE_CHUNK = 30

TYPE_NAMES = {"char": "Character", "prop": "Prop", "set": "Set", "env": "Environment", "veh": "Vehicle"}
# Plural folder names (characters/, environments/, ...) read as their singular.
TYPE_NAMES.update({name.lower() + "s": name for name in list(TYPE_NAMES.values())})
CARD_W = 206


def display_name(entity) -> str:
    return entity.display_name


def type_title(group: str, plural: bool = False) -> str:
    """'characters' -> 'Character' (or 'Characters'); unknown types -> 'Fx Elements'."""
    if group in TYPE_NAMES:
        return TYPE_NAMES[group] + ("s" if plural else "")
    return group.replace("_", " ").title()


def next_shot_name(names) -> str:
    """The shot after the highest one, e.g. SH070 -> SH080; sh010 if there are none."""
    best = None
    for name in names:
        match = re.fullmatch(r"([A-Za-z_]*?)(\d+)", name)
        if match and (best is None or int(match.group(2)) > int(best.group(2))):
            best = match
    if best is None:
        return "sh010"
    digits = best.group(2)
    return f"{best.group(1)}{int(digits) + 10:0{len(digits)}d}"


def create_asset_dialog(app, asset_type=None):
    types = app.project.asset_types
    form = FormDialog(app, "New asset", [
        ("type", "Type", asset_type if asset_type in types else types[0], types),
        ("name", "Name", "", None),
        ("description", "Description (optional)", "", None),
    ], intro="Names use lowercase letters, numbers and underscores, e.g. hero_robot.", ok_text="Create")
    if not form.result:
        return None
    try:
        entity = app.project.create_asset(form.result["type"], form.result["name"], form.result["description"],
                                          user=app.user)
    except PipelineError as e:
        messagebox.showerror("New asset", str(e), parent=app)
        return None
    app.status(f"Created asset {entity.label}.")
    return entity


def create_shot_dialog(app, sequence=None):
    project = app.project
    fields = [("sequence", "Sequence", sequence or "sq010", None)] if project.uses_sequences else []
    fields += [
        ("shot", "Shot", next_shot_name(e.name for e in project.list_entities("shot")), None),
        ("start", "First frame", "1001", None),
        ("end", "Last frame", "1100", None),
        ("description", "Description (optional)", "", None),
    ]
    example = "sq010 / sh020" if project.uses_sequences else "SH080"
    form = FormDialog(app, "New shot", fields, ok_text="Create",
                      intro=f"Names use letters, numbers and underscores, e.g. {example}.")
    if not form.result:
        return None
    try:
        start, end = int(form.result["start"]), int(form.result["end"])
        entity = project.create_shot(form.result.get("sequence", ""), form.result["shot"], start, end,
                                     form.result["description"], user=app.user)
    except ValueError:
        messagebox.showerror("New shot", "The first and last frames must be whole numbers.", parent=app)
        return None
    except PipelineError as e:
        messagebox.showerror("New shot", str(e), parent=app)
        return None
    app.status(f"Created shot {entity.label}.")
    return entity


class AssetsPage(Page):
    nav_title = "Assets & Shots"

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.kind = "asset"
        self.folder = None      # asset type or sequence, None = all
        self.filter = "all"     # "all", a task name, "review" or "mine"
        self.entity = None
        self.task = None
        self.workfile = None
        self.part = None          # the selected part of the step (None: all of it)
        self._part_of = None      # (entity path, step) the part belongs to
        self._cards = {}        # entity path -> card frame
        self._summary = {}      # entity path -> (step, status, version, caption)
        self._layout_job = None

        right = self.header("Browse", "Assets & Shots")
        self.new_btn = SlantButton(right, "New asset", self.new_entity, icon="+")
        self.new_btn.pack(side="right")

        tabs = tk.Frame(self.content, bg=BG)
        tabs.pack(fill="x")
        self.tab_labels = {}
        for kind, text in (("asset", "ASSETS"), ("shot", "SHOTS")):
            holder = tk.Frame(tabs, bg=BG)
            holder.pack(side="left", padx=(0, px(26)))
            lab = label(holder, text, (Fonts.panel_title[0], 15, "bold italic"), MUTED, bg=BG, cursor="hand2")
            lab.pack()
            bar = tk.Frame(holder, bg=BG, height=px(3))
            bar.pack(fill="x", pady=(px(6), 0))
            lab.bind("<Button-1>", lambda e, k=kind: self.set_kind(k))
            self.tab_labels[kind] = (lab, bar)
        separator(self.content).pack(fill="x", pady=(0, px(18)))

        body = tk.Frame(self.content, bg=BG)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        # Folders
        self.folders = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, width=px(210))
        self.folders.grid(row=0, column=0, sticky="ns", padx=(0, px(18)))
        self.folders.pack_propagate(False)

        # Chips + cards
        middle = tk.Frame(body, bg=BG)
        middle.grid(row=0, column=1, sticky="nsew")
        self.chip_row = tk.Frame(middle, bg=BG)
        self.chip_row.pack(fill="x", pady=(0, px(14)))
        self.grid_area = ScrollFrame(middle)
        self.grid_area.pack(fill="both", expand=True)
        self.grid_area.canvas.bind("<Configure>", lambda e: self._schedule_layout(), add="+")

        # Detail panel
        detail_box = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, width=px(350))
        detail_box.grid(row=0, column=2, sticky="ns", padx=(px(18), 0))
        detail_box.pack_propagate(False)
        # Open / Version up / Publish... stay pinned at the bottom; the rest scrolls.
        self.detail_actions = tk.Frame(detail_box, bg=PANEL)
        self.detail_actions.pack(side="bottom", fill="x")
        self.detail = ScrollFrame(detail_box, bg=PANEL)
        self.detail.pack(fill="both", expand=True)

    # --- Page API ---------------------------------------------------------

    def breadcrumb(self):
        parts = ["Assets" if self.kind == "asset" else "Shots"]
        if self.entity:
            if self.entity.kind == "asset":
                parts.append(type_title(self.entity.group))
            elif self.entity.group:
                parts.append(self.entity.group.upper())
            parts.append(display_name(self.entity))
        return parts

    def refresh(self):
        pending = self.app.pending_open  # set when another page asks to show an asset/shot
        if pending is not None:
            self.app.pending_open = None
            entity, task = pending
            self.kind, self.entity, self.folder, self.workfile = entity.kind, entity, None, None
            self.task = task or self.app.project.current_step(entity)
        # Drop a selection that no longer exists (e.g. project changed).
        if self.entity and not self.entity.path.is_dir():
            self.entity = self.task = self.workfile = None
        self._update_tabs()
        self._build_folders()
        self._build_chips()
        self.refresh_grid()
        self.refresh_detail()

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind, self.folder, self.filter = kind, None, "all"
            self.entity = self.task = self.workfile = None
            self.refresh()
            self.app.set_breadcrumb(self.breadcrumb())

    def _update_tabs(self):
        for kind, (lab, bar) in self.tab_labels.items():
            active = kind == self.kind
            lab.configure(fg=TEXT if active else MUTED)
            bar.configure(bg=RED if active else BG)
        self.new_btn.set_text("New asset" if self.kind == "asset" else "New shot")

    # --- Folders ----------------------------------------------------------

    def _build_folders(self):
        for child in self.folders.winfo_children():
            child.destroy()
        project = self.app.project
        for kind, title in (("asset", "Assets"), ("shot", "Shots")):
            eyebrow(self.folders, title, MUTED).pack(anchor="w", padx=px(16), pady=(px(16), px(6)))
            entities = project.list_entities(kind)
            groups = project.asset_types if kind == "asset" else sorted({e.group for e in entities})
            if kind == "asset":
                groups = groups + sorted({e.group for e in entities} - set(groups))
            if not groups:
                label(self.folders, "None yet", Fonts.small, DIM).pack(anchor="w", padx=px(16))
            for group in groups:
                count = sum(1 for e in entities if e.group == group)
                if kind == "asset":
                    name = type_title(group, plural=True)
                else:
                    name = group.upper() if group else "All shots"
                self._folder_row(kind, group, name, count)

    def _folder_row(self, kind, group, text, count):
        selected = self.kind == kind and self.folder == group
        row = tk.Frame(self.folders, bg=PANEL_HI if selected else PANEL, cursor="hand2")
        row.pack(fill="x")
        tk.Frame(row, bg=RED if selected else row["bg"], width=px(3)).pack(side="left", fill="y")
        label(row, "▭", Fonts.icon, MUTED).pack(side="left", padx=(px(12), px(8)), pady=px(7))
        label(row, text, Fonts.body, TEXT if selected else "#c9c9cf").pack(side="left")
        label(row, str(count), Fonts.small, DIM).pack(side="right", padx=px(14))

        def pick(_e=None):
            if self.kind == kind and self.folder == group:
                self.folder = None  # click again to show everything
            else:
                if self.kind != kind:
                    self.entity = self.task = self.workfile = None
                self.kind, self.folder = kind, group
            self.refresh()
            self.app.set_breadcrumb(self.breadcrumb())

        for widget in [row] + row.winfo_children():
            widget.bind("<Button-1>", pick)
        if not selected:
            row.bind("<Enter>", lambda e: set_bg(row, PANEL_HI))
            row.bind("<Leave>", lambda e: set_bg(row, PANEL))

    # --- Filters ----------------------------------------------------------

    def _build_chips(self):
        for child in self.chip_row.winfo_children():
            child.destroy()
        options = [("all", "All")] + [(t, t.title()) for t in self.app.project.all_tasks(self.kind)] + \
                  [("review", "In review"), ("mine", "Mine")]
        if self.filter not in dict(options):
            self.filter = "all"
        for key, text in options:
            chip = Chip(self.chip_row, text, lambda k=key: self._set_filter(k))
            chip.set_active(key == self.filter)
            chip.pack(side="left", padx=(0, px(8)))

    def _set_filter(self, key):
        self.filter = key
        self._build_chips()
        self.refresh_grid()

    def _department_filter(self):
        return None if self.filter in ("all", "review", "mine") else self.filter

    def _visible_entities(self):
        project = self.app.project
        query = self.app.search_text()
        result = []
        for entity in project.list_entities(self.kind):
            if self.folder and entity.group != self.folder:
                continue
            if query and query not in entity.label.lower() and query not in display_name(entity).lower():
                continue
            tasks = project.entity_tasks(entity)
            infos = {t: project.task_info(entity, t) for t in tasks}
            department = self._department_filter()
            if department and department not in tasks:
                continue
            # With a department chip on, cards show that department; otherwise the current step.
            step = department or project.current_step(entity)
            if self.filter == "review" and not any(i["status"] == "In review" for i in infos.values()):
                continue
            if self.filter == "mine" and not any(i.get("assignee") == self.app.user for i in infos.values()):
                continue
            workfiles = project.list_workfiles(entity, step)
            caption = workfiles[-1].path.name if workfiles else "no files yet"
            version = workfiles[-1].version if workfiles else 0
            self._summary[entity.path] = (step, infos[step]["status"], version, caption)
            result.append(entity)
        return result

    # --- Cards ------------------------------------------------------------

    def refresh_grid(self):
        inner = self.grid_area.inner
        for child in inner.winfo_children():
            child.destroy()
        self._cards = {}
        self._summary = {}
        entities = self._visible_entities()
        if not entities:
            what = "assets" if self.kind == "asset" else "shots"
            text = (f"No {what} match." if (self.filter != "all" or self.app.search_text()) else
                    f"No {what} yet. Click '+ New {'asset' if self.kind == 'asset' else 'shot'}' to add one.")
            label(inner, text, Fonts.body, MUTED, bg=BG).grid(row=0, column=0, sticky="w", pady=px(10))
            return
        for entity in entities:
            self._cards[entity.path] = self._make_card(inner, entity)
        self._layout_cards()

    def _make_card(self, parent, entity):
        step, status, version, caption = self._summary[entity.path]
        selected = self.entity is not None and self.entity.path == entity.path
        card = tk.Frame(parent, bg=PANEL, highlightthickness=1, cursor="hand2",
                        highlightbackground=RED if selected else BORDER)
        Stripes(card, width=CARD_W, height=104, caption=caption, image=thumbnails.find(entity.path)).pack(fill="x")
        body = tk.Frame(card, bg=PANEL)
        body.pack(fill="x", padx=px(12), pady=px(10))
        top = tk.Frame(body, bg=PANEL)
        top.pack(fill="x")
        label(top, display_name(entity), Fonts.mono_bold).pack(side="left")
        label(top, f"v{version:03d}" if version else "—", Fonts.mono, RED if version else DIM).pack(side="right")
        bottom = tk.Frame(body, bg=PANEL)
        bottom.pack(fill="x", pady=(px(8), 0))
        label(bottom, step.title(), Fonts.body, MUTED).pack(side="left")
        Badge(bottom, status).pack(side="right")

        def pick(_e=None):
            self.select_entity(entity)

        def walk(w):
            w.bind("<Button-1>", pick, add="+")
            for c in w.winfo_children():
                walk(c)
        walk(card)
        card.bind("<Enter>", lambda e: card.configure(highlightbackground=RED if self._is_selected(entity) else "#46464d"))
        card.bind("<Leave>", lambda e: card.configure(highlightbackground=RED if self._is_selected(entity) else BORDER))
        return card

    def _is_selected(self, entity):
        return self.entity is not None and self.entity.path == entity.path

    def _schedule_layout(self):
        if self._layout_job:
            self.after_cancel(self._layout_job)
        self._layout_job = self.after(60, self._layout_cards)

    def _layout_cards(self):
        self._layout_job = None
        if not self._cards:
            return
        width = self.grid_area.canvas.winfo_width()
        gap = px(14)
        columns = max(1, (width + gap) // (px(CARD_W) + gap))
        for i, card in enumerate(self._cards.values()):
            card.grid(row=i // columns, column=i % columns, sticky="nsew",
                      padx=(0, gap if i % columns < columns - 1 else 0), pady=(0, gap))
        for c in range(columns):
            self.grid_area.inner.columnconfigure(c, weight=1, uniform="card")
        for c in range(columns, 12):
            self.grid_area.inner.columnconfigure(c, weight=0, uniform="")

    def select_entity(self, entity):
        previous = self.entity
        self.entity = entity
        if previous is None or previous.path != entity.path:
            self.task = self._department_filter() or self.app.project.current_step(entity)
            self.workfile = None
        for path, card in self._cards.items():
            card.configure(highlightbackground=RED if path == entity.path else BORDER)
        self.refresh_detail()
        self.app.set_breadcrumb(self.breadcrumb())

    # --- Detail panel -----------------------------------------------------

    def refresh_detail(self):
        inner = self.detail.inner
        for child in inner.winfo_children() + self.detail_actions.winfo_children():
            child.destroy()
        project = self.app.project
        entity = self.entity
        if entity is None:
            self.app.select(None, None, None)
            label(inner, "Select an asset or shot to see its\npipeline steps and work files.",
                  Fonts.body, MUTED, justify="left").pack(anchor="w", padx=px(18), pady=px(20))
            return
        tasks = project.entity_tasks(entity)
        if self.task not in tasks:
            self.task = project.current_step(entity)
        parts = project.list_parts(entity, self.task)
        if self._part_of != (entity.path, self.task) or self.part not in parts:
            self.part = None
        self._part_of = (entity.path, self.task)
        workfiles = list(reversed(project.list_workfiles(entity, self.task, self.part)))
        if self.workfile is None or self.workfile.path not in [w.path for w in workfiles]:
            self.workfile = workfiles[0] if workfiles else None
        self.app.select(entity, self.task, self.workfile.path if self.workfile else None)

        caption = self.workfile.path.name if self.workfile else "no files yet"
        thumb = thumbnails.find(entity.path)
        # Taller with a picture: a thin strip crops a photo too much.
        Stripes(inner, width=348, height=150 if thumb else 80, caption=caption, image=thumb,
                link=("CHANGE THUMBNAIL" if thumb else "+ THUMBNAIL", self.thumbnail_menu)).pack(fill="x")
        pad = px(18)
        box = tk.Frame(inner, bg=PANEL)
        box.pack(fill="x", padx=pad, pady=(px(12), px(14)))
        kind_name = type_title(entity.group) if entity.kind == "asset" else "Shot"
        eyebrow(box, kind_name, MUTED).pack(anchor="w")
        label(box, display_name(entity).upper(), (Fonts.page_title[0], 22, "bold italic"),
              wraplength=px(310), justify="left").pack(anchor="w")
        rel = "/" + entity.path.relative_to(project.root).as_posix() + "/"
        label(box, rel, Fonts.mono_small, MUTED).pack(anchor="w", pady=(px(2), 0))
        info = project.entity_info(entity)
        if entity.kind == "shot":
            frames = project.frame_range(entity)
            text = f"Frames {frames[0]}–{frames[1]}" if frames else "Frames not set · click to set"
            frames_label = label(box, text, Fonts.small, MUTED if frames else RED, cursor="hand2")
            frames_label.pack(anchor="w", pady=(px(6), 0))
            frames_label.bind("<Button-1>", lambda e: self.set_frame_range())
            Tooltip(frames_label, "Click to change the shot's first and last frame.")
        if info.get("description"):
            label(box, info["description"], Fonts.small, MUTED, wraplength=px(310),
                  justify="left").pack(anchor="w", pady=(px(6), 0))

        # Pipeline steps
        eyebrow(box, "Pipeline steps", MUTED).pack(anchor="w", pady=(px(12), px(4)))
        for task in tasks:
            self._step_row(box, entity, task)

        # Work files
        heading = tk.Frame(box, bg=PANEL)
        heading.pack(fill="x", pady=(px(12), px(4)))
        eyebrow(heading, f"Work files · {self.task}" + (f" · {self.part}" if self.part else ""), MUTED).pack(
            side="left")
        add = label(heading, "+ part", Fonts.small, DIM, cursor="hand2")
        add.pack(side="right")
        add.bind("<Button-1>", lambda e: self.add_part())
        Tooltip(add, "Split this step into parts worked on separately (e.g. skeleton, muscle, skin), "
                     "each with its own versions, publishes, status and turntable.")
        if parts:
            self._part_chips(box, parts)
        if self.part:
            self._part_row(box, entity, self.task, self.part)
        if not workfiles:
            label(box, "No files yet. Start a new scene or bring one in.", Fonts.small, DIM).pack(
                anchor="w", pady=(0, px(6)))
        for wf in workfiles[:4]:
            self._file_row(box, entity, wf)
        if len(workfiles) > 4:
            label(box, f"+ {len(workfiles) - 4} older versions (Show folder)", Fonts.small, DIM).pack(anchor="w")

        shown = [self.part] if self.part else [""] + parts
        for part in shown:
            publishes = project.list_publishes(entity, self.task, part)
            if not publishes:
                continue
            last = publishes[-1]
            what = f"{part} v{last.version:03d}" if part and not self.part else f"v{last.version:03d}"
            usd = "  ·  USD for Houdini" if last.info.get("usd") else ""
            label(box, f"Latest publish: {what} by {last.info.get('user', '?')}"
                       f"  ·  {last.info.get('comment', '') or 'no notes'}{usd}",
                  Fonts.small, MUTED, wraplength=px(310), justify="left").pack(anchor="w", pady=(px(6), 0))
            movie = turntable.find_turntable(project, entity, self.task, last)
            if movie:
                play = label(box, f"▶  Play {what} turntable", Fonts.small, TEXT, cursor="hand2")
                play.pack(anchor="w", pady=(px(4), 0))
                play.bind("<Button-1>", lambda e, m=movie: os.startfile(m))
                Tooltip(play, str(movie))

        # Open with a chosen version
        separator(self.detail_actions).pack(fill="x")
        actions_box = tk.Frame(self.detail_actions, bg=PANEL)
        actions_box.pack(fill="x", padx=pad, pady=(px(12), px(14)))
        open_row = tk.Frame(actions_box, bg=PANEL)
        open_row.pack(fill="x")
        self.open_with = ttk.Combobox(open_row, state="readonly", width=17)
        self._open_choices = {}
        if self.workfile:
            inst, saved = self.app.installation_for_file(self.workfile.path)
            app_name = apps.app_for_extension(self.app.app_config, self.workfile.path.suffix)
            options = self.app.installations.get(app_name, []) if app_name else []
            self._open_choices = {i.display: i for i in options}
            self.open_with.configure(values=list(self._open_choices))
            if inst:
                self.open_with.set(inst.display)
            tip = f"Saved with {saved}." if saved else ""
        else:
            self.open_with.configure(state="disabled")
            tip = ""
        self.open_with.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        if tip:
            Tooltip(self.open_with, tip + " Pick another installed version if you need to.")
        open_btn = SlantButton(open_row, "Open", self.open_selected, kind="primary", icon="↗", width=100,
                               tip="Open the selected work file in the chosen version.")
        open_btn.pack(side="right")
        open_btn.set_enabled(self.workfile is not None)

        grid = tk.Frame(actions_box, bg=PANEL)
        grid.pack(fill="x", pady=(px(10), 0))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        actions = [
            ("Version up", "↑", self.version_up, True,
             "Copy the selected file to the next version under your name."),
            ("Publish...", "⤒", self.publish, True, "Share this version with the other departments."),
            ("Render...", "▷", self.render, "houdini",
             "Render the selected Houdini scene from the command line."),
            ("New scene...", "+", lambda: self.app.new_scene(self.entity, self.task, self.part or ""), False,
             "Start Houdini or Maya with an empty scene for this step (or the selected part)."),
            ("Bring in file...", "⇥", self.bring_in, False,
             "Copy a scene you already have into this step (or the selected part) as the next version."),
            ("Show folder", "▭", self.show_folder, False, "Open this step's work folder in Explorer."),
            ("Turntable...", "↻", self.make_turntable, "turntable",
             "Render a turntable (MP4) of a publish or work file, for dailies: clay for geo, "
             "shaded for lookdev."),
        ]
        is_houdini = bool(self.workfile) and \
            apps.app_for_extension(self.app.app_config, self.workfile.path.suffix) == "houdini"
        for i, (text, icon, command, needs, tip) in enumerate(actions):
            b = SlantButton(grid, text, command, icon=icon, height=32, width=150, tip=tip)
            b.grid(row=i // 2, column=i % 2, sticky="w", pady=px(3))
            if needs == "houdini":
                b.set_enabled(is_houdini)
            elif needs == "turntable":
                b.set_enabled(turntable.has_turntables(self.task))
            elif needs:
                b.set_enabled(self.workfile is not None)

    def _step_row(self, parent, entity, task):
        project = self.app.project
        info = project.task_info(entity, task)
        selected = task == self.task
        bg = PANEL_HI if selected else PANEL
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        row.pack(fill="x")
        tk.Frame(row, bg=RED if selected else bg, width=px(3)).pack(side="left", fill="y")
        label(row, spaced(task), Fonts.eyebrow, TEXT, width=11, anchor="w").pack(side="left", padx=(px(8), 0),
                                                                                pady=px(6))
        version = project.latest_version(entity, task)
        label(row, f"v{version:03d}" if version else "—", Fonts.mono, TEXT if version else DIM,
              width=5, anchor="w").pack(side="left")
        assignee = info.get("assignee") or ""
        who = label(row, assignee[:2].upper() if assignee else "+", Fonts.small, MUTED if assignee else DIM,
                    cursor="hand2", width=3)
        who.pack(side="right", padx=(0, px(6)))
        who.keep_bg = False
        Tooltip(who, f"Assigned to {assignee}. Click to change." if assignee else "Click to assign someone.")
        badge = Badge(row, info["status"])
        badge.pack(side="right", padx=(0, px(6)))
        badge.configure(cursor="hand2")
        Tooltip(badge, "Click to change the status.")

        def pick(_e=None):
            self.task, self.workfile = task, None
            self.refresh_detail()

        def change_status(event):
            menu = status_menu(self, STATUSES, lambda s: self._set_status(entity, task, s))
            menu.tk_popup(event.x_root, event.y_root)

        for widget in row.winfo_children() + [row]:
            if widget not in (badge, who):
                widget.bind("<Button-1>", pick)
        badge.bind("<Button-1>", change_status)
        who.bind("<Button-1>", lambda e: self._assign_menu(e, entity, task, assignee))

    def _part_chips(self, parent, parts):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(0, px(6)))
        for part in [None] + parts:
            chip = Chip(row, "All" if part is None else part, lambda p=part: self._pick_part(p))
            chip.set_active(part == self.part)
            chip.pack(side="left", padx=(0, px(4)), pady=px(2))

    def _pick_part(self, part):
        self.part, self.workfile = part, None
        self.refresh_detail()

    def _part_row(self, parent, entity, task, part):
        """The selected part's status and assignee, changed the same way as a step's."""
        info = self.app.project.part_info(entity, task, part)
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(0, px(6)))
        label(row, spaced(part), Fonts.eyebrow, TEXT).pack(side="left")
        assignee = info.get("assignee") or ""
        who = label(row, assignee or "+ assign", Fonts.small, MUTED if assignee else DIM, cursor="hand2")
        who.pack(side="right")
        Tooltip(who, f"{part} is assigned to {assignee}. Click to change." if assignee else
                f"Click to assign someone to {part}.")
        badge = Badge(row, info["status"])
        badge.pack(side="right", padx=(0, px(8)))
        badge.configure(cursor="hand2")
        Tooltip(badge, f"Click to change {part}'s status.")
        badge.bind("<Button-1>", lambda e: status_menu(
            self, STATUSES, lambda s: self._set_status(entity, task, s, part)).tk_popup(e.x_root, e.y_root))
        who.bind("<Button-1>", lambda e: self._assign_menu(e, entity, task, assignee, part))

    def add_part(self):
        project, entity, task = self.app.project, self.entity, self.task
        form = FormDialog(self.app, "Add part", [("name", "Part name", "", None)], ok_text="Add",
                          intro=f"A part of {display_name(entity)} {task} that's worked on and published on its "
                                "own, e.g. skeleton, muscle or skin. Lowercase letters and numbers.\n\n"
                                f"Its files are named {entity.name}_{task}_<part>_v001_<you>.")
        if not form.result:
            return
        try:
            part = project.add_part(entity, task, form.result["name"], self.app.user)
        except (PipelineError, OSError) as e:
            messagebox.showerror("Add part", str(e), parent=self)
            return
        self.part, self.workfile = part, None
        self.refresh_detail()
        self.app.status(f"Added the {part} part to {display_name(entity)} {task}.")

    def _file_row(self, parent, entity, wf):
        selected = self.workfile is not None and wf.path == self.workfile.path
        bg = PANEL_HI if selected else PANEL
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        row.pack(fill="x")
        label(row, wf.path.name, Fonts.mono_small, TEXT if selected else "#c9c9cf").pack(
            side="left", padx=(px(8), 0), pady=px(6))
        try:
            size = wf.path.stat().st_size / (1024 * 1024)
            size_text = f"{size:.0f} MB" if size >= 1 else f"{size * 1024:.0f} KB"
        except OSError:
            size_text = ""
        label(row, size_text, Fonts.small, DIM).pack(side="right", padx=px(8))

        def pick(_e=None):
            self.workfile = wf
            self.refresh_detail()

        for widget in [row] + row.winfo_children():
            widget.bind("<Button-1>", pick)
            widget.bind("<Double-1>", lambda e: (pick(), self.open_selected()))
        if not selected:
            row.bind("<Enter>", lambda e: set_bg(row, PANEL_HI))
            row.bind("<Leave>", lambda e: set_bg(row, PANEL))

    # --- Status & assignment ----------------------------------------------

    def _set_status(self, entity, task, status, part=""):
        if part:
            self.app.project.update_part(entity, task, part, self.app.user, status=status)
        else:
            self.app.project.update_task(entity, task, self.app.user, status=status)
        self.app.status(f"{display_name(entity)} {task}{' ' + part if part else ''}: {status}.")
        self.refresh_grid()
        self.refresh_detail()

    def _assign_menu(self, event, entity, task, current, part=""):
        menu = tk.Menu(self, tearoff=0, font=Fonts.body)
        menu.add_command(label=f"Assign to me ({self.app.user})",
                         command=lambda: self._assign(entity, task, self.app.user, part))
        menu.add_command(label="Assign to someone else...", command=lambda: self._assign_other(entity, task, part))
        if current:
            menu.add_command(label="Unassign", command=lambda: self._assign(entity, task, "", part))
        menu.tk_popup(event.x_root, event.y_root)

    def _assign_other(self, entity, task, part=""):
        form = FormDialog(self.app, "Assign", [("name", "Name", "", None)], ok_text="Assign",
                          intro="Use the short name they use in BASELINE, e.g. sam.")
        if form.result and form.result["name"]:
            from ..settings import sanitize_user
            self._assign(entity, task, sanitize_user(form.result["name"]), part)

    def _assign(self, entity, task, name, part=""):
        if part:
            self.app.project.update_part(entity, task, part, self.app.user, assignee=name)
        else:
            self.app.project.update_task(entity, task, self.app.user, assignee=name)
        what = f"{display_name(entity)} {task}{' ' + part if part else ''}"
        self.app.status(f"{what} assigned to {name}." if name else "Unassigned.")
        self.refresh_grid()
        self.refresh_detail()

    # --- Actions ----------------------------------------------------------

    def new_entity(self):
        if self.kind == "asset":
            entity = create_asset_dialog(self.app, self.folder)
        else:
            entity = create_shot_dialog(self.app, self.folder)
        if entity:
            self.entity, self.task, self.workfile = entity, None, None
            self.folder = entity.group if self.folder else None
            self.refresh()

    def set_frame_range(self):
        project, entity = self.app.project, self.entity
        frames = project.frame_range(entity) or (1001, 1100)
        form = FormDialog(self.app, "Frame range", [
            ("start", "First frame", str(frames[0]), None),
            ("end", "Last frame", str(frames[1]), None),
        ], intro=f"Frames for {display_name(entity)}. Render uses these for 'Shot's range'.", ok_text="Save")
        if not form.result:
            return
        try:
            start, end = int(form.result["start"]), int(form.result["end"])
            project.update_entity_info(entity, self.app.user, frame_start=start, frame_end=end)
        except ValueError:
            messagebox.showerror("Frame range", "The first and last frames must be whole numbers.", parent=self)
            return
        except PipelineError as e:
            messagebox.showerror("Frame range", str(e), parent=self)
            return
        self.app.status(f"{display_name(entity)} frames set to {start}-{end}.")
        self.refresh_detail()

    def thumbnail_menu(self):
        if not thumbnails.find(self.entity.path):
            self.choose_thumbnail()
            return
        menu = tk.Menu(self, tearoff=0, font=Fonts.body)
        menu.add_command(label="Choose another picture...", command=self.choose_thumbnail)
        menu.add_command(label="Remove thumbnail", command=self.remove_thumbnail)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def choose_thumbnail(self):
        entity = self.entity
        picture = filedialog.askopenfilename(parent=self, title=f"Choose a thumbnail for {display_name(entity)}",
                                             filetypes=thumbnails.IMAGE_FILETYPES)
        if not picture:
            return
        self.app.configure(cursor="watch")
        self.app.update_idletasks()
        try:
            thumbnails.set_thumbnail(entity.path, picture)
        except (PipelineError, OSError) as e:
            messagebox.showerror("Thumbnail", str(e), parent=self)
            return
        finally:
            self.app.configure(cursor="")
        self.refresh_grid()
        self.refresh_detail()
        self.app.status(f"{display_name(entity)} has a new thumbnail. Everyone sees it once OneDrive syncs.")

    def remove_thumbnail(self):
        entity = self.entity
        try:
            thumbnails.remove(entity.path)
        except OSError as e:
            messagebox.showerror("Thumbnail", f"Couldn't remove the thumbnail.\n\n{e}", parent=self)
            return
        self.refresh_grid()
        self.refresh_detail()
        self.app.status(f"Removed {display_name(entity)}'s thumbnail.")

    def make_turntable(self):
        project, entity, task = self.app.project, self.entity, self.task
        choices = {}
        for part in [self.part] if self.part else [""] + project.list_parts(entity, task):
            for pub in reversed(project.list_publishes(entity, task, part)):
                source = turntable.publish_source(pub)
                if source and turntable.kind_for(task, source):
                    who = pub.info.get("user", "")
                    name = f"{part} v{pub.version:03d}" if part else f"v{pub.version:03d}"
                    choices[f"Publish {name}" + (f" ({who})" if who else "")] = (source, pub)
        for wf in reversed(project.list_workfiles(entity, task, self.part)):
            if turntable.kind_for(task, wf.path):
                choices[f"Work file {wf.path.name} (test)"] = (wf.path, None)
        lookdev = task.lower() in turntable.LOOKDEV_TASKS
        if not choices:
            messagebox.showinfo("Turntable", f"There's no {'Houdini' if lookdev else 'Maya'} file in "
                                f"{display_name(entity)} {task} to make a turntable from yet.", parent=self)
            return
        config = turntable.load_settings()
        farm_labels = [text for text, _farm in self.app.pages["farm"].farm_choices()]
        if lookdev:
            spin = (" The asset spins once, then the HDRI spins once (the same number of frames again)."
                    if turntable.hdri_spin_frames("lookdev", config) else "")
            intro = ("A shaded turntable, rendered with Karma on this computer in the background. It's saved "
                     "as an MP4 next to the publish, and progress shows on the Render page." + spin + "\n\n"
                     f"The scene at the {turntable.LOOKDEV_NODE} node is used, without its cameras and lights.")
        else:
            intro = ("A clay turntable, rendered with Karma on this computer in the background. It's saved "
                     "as an MP4 next to the publish, and progress shows on the Render page.\n\n"
                     f"Only the {turntable.GEO_SET} set is used, if the Maya scene has one.")
        form = FormDialog(self.app, "Turntable", [
            ("version", "Make a turntable of", next(iter(choices)), list(choices)),
            ("frames", f"Frames for one spin ({config['fps']} a second)", str(config["frames"]), None),
            ("samples", "Samples (lower is faster but grainier)", str(config["samples"]), None),
            ("where", "Render on", RENDER_HERE, [RENDER_HERE, RENDER_FARM_ONE, RENDER_FARM_SPLIT]),
            ("chunk", "Frames per PC (when split)", str(TURNTABLE_CHUNK), None),
            ("farm", "Which farm", farm_labels[0], farm_labels),
        ], intro=intro + "\n\nOn the farm, lab PCs rendering farm jobs make it (see the Farm page), so "
                         "this PC stays free. Split shares one turntable's frames between several PCs.",
            ok_text="Render")
        if not form.result:
            return
        try:
            overrides = turntable.parse_overrides(form.result["frames"], form.result["samples"])
        except PipelineError as e:
            messagebox.showerror("Turntable", str(e), parent=self)
            return
        source, pub = choices[form.result["version"]]
        out = turntable.output_path(project, entity, task, source, pub)
        if out.exists() and not messagebox.askyesno(
                "Turntable", f"{out.name} already exists. Render it again and replace it?", parent=self):
            return
        if lookdev:
            maya = None
            houdini = self.app.installation_for_file(source)[0] or self.app.default_installation("houdini")
        else:
            maya, _saved = self.app.installation_for_file(source)
            houdini = self.app.default_installation("houdini")
        where = form.result["where"]
        if where != RENDER_HERE:
            try:
                chunk = int(form.result["chunk"])
                if chunk < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Turntable", "'Frames per PC' should be a whole number, e.g. 30.", parent=self)
                return
        hython = houdini.tool(self.app.app_config["houdini"], "hython") if houdini else None
        try:
            job = turntable.create_job(
                project, entity, task, source, pub, self.app.user,
                mayapy=maya.tool(self.app.app_config["maya"], "mayapy") if maya else None,
                maya_version=maya.version if maya else "",
                houdini_bin=hython.parent if hython else None, houdini_version=houdini.version if houdini else "",
                overrides=overrides)
        except (PipelineError, OSError) as e:
            messagebox.showerror("Turntable", str(e), parent=self)
            return
        if where != RENDER_HERE:
            self._send_turntable_to_farm(job, where == RENDER_FARM_SPLIT, chunk, houdini.version,
                                         form.result["farm"])
            return
        self.app.pages["render"].add_external_job(job)
        self.app.go("render")
        self.app.status(f"Turntable of {display_name(entity)} {task} started. It saves {out.name} when done.")

    def _send_turntable_to_farm(self, job, split, chunk, houdini_version, farm_label=""):
        """Hands a checked turntable job to the farm (`farm_label`: which) instead of rendering it here."""
        project, entity, task = self.app.project, self.entity, self.task
        spec = json.loads(job.spec_file.read_text(encoding="utf-8"))
        shutil.rmtree(spec["work_dir"], ignore_errors=True)  # this PC won't use it
        farm_page = self.app.pages["farm"]
        choices = dict(farm_page.farm_choices())
        target = choices.get(farm_label) or next(iter(choices.values()))
        try:
            job_id = target.submit_turntable(
                project_name=project.name, turntable=turntable.portable_spec(spec, project.root), frames=job.end,
                split=split, chunk_size=chunk, houdini_version=houdini_version, user=self.app.user)
        except OSError as e:
            messagebox.showerror("Turntable", f"Couldn't write to the farm folder {target.folder}:\n{e}\n\n"
                                              "You can change it on the Farm page.", parent=self)
            return
        pieces = -(-job.end // chunk) if split else 1
        self.app.status(f"Sent the {display_name(entity)} {task} turntable to {target.label}"
                        f"{f' in {pieces} pieces' if split else ''}. PCs rendering farm jobs pick it up "
                        f"{'within a minute or two' if target.synced else 'within a few seconds'}.")
        farm_page.select_job(target, job_id)
        self.app.go("farm")

    def open_selected(self):
        if not self.workfile:
            return
        inst = self._open_choices.get(self.open_with.get())
        if inst is None:
            inst, _ = self.app.installation_for_file(self.workfile.path)
        if inst is None:
            if messagebox.askyesno("Open", "No installed software is set up for this kind of file.\n"
                                   "Open it with Windows' default program instead?", parent=self):
                os.startfile(self.workfile.path)
            return
        self.app.launch(inst, self.entity, self.task, self.workfile.path)

    def version_up(self):
        if not self.workfile:
            return
        try:
            new = self.app.project.version_up(self.workfile, self.entity, self.task, self.app.user)
        except PipelineError as e:
            messagebox.showerror("Version up", str(e), parent=self)
            return
        self.workfile = new
        self.refresh_grid()
        self.refresh_detail()
        self.app.status(f"Created {new.path.name}. Open it to keep working.")

    def publish(self):
        if self.workfile:
            self.app.select(self.entity, self.task, self.workfile.path)
            self.app.go("publish")

    def render(self):
        if self.workfile:
            self.app.show_render_for(self.workfile.path)

    def bring_in(self):
        exts = sorted({e for cfg in self.app.app_config.values() for e in cfg.get("extensions", [])})
        source = filedialog.askopenfilename(
            parent=self, title="Choose a scene file to bring in",
            filetypes=[("Scene files", " ".join("*" + e for e in exts)), ("All files", "*.*")])
        if not source:
            return
        try:
            self.workfile = self.app.project.add_existing_file(source, self.entity, self.task, self.app.user,
                                                               self.part or "")
        except PipelineError as e:
            messagebox.showerror("Bring in file", str(e), parent=self)
            return
        self.refresh_grid()
        self.refresh_detail()
        self.app.status(f"Brought in {Path(source).name} as {self.workfile.path.name}.")

    def show_folder(self):
        folder = self.app.project.work_dir(self.entity, self.task)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)
