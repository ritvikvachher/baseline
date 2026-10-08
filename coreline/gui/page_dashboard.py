"""Dashboard: film progress gauges, my tasks, recent publishes and the shot tracker."""
from __future__ import annotations

import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox

from .. import greeting, settings, thumbnails
from ..project import STATUSES, PipelineError
from .page_base import Page
from .theme import (BG, BORDER, DIM, MUTED, PANEL, PANEL_HI, RED, STATUS_COLORS, TEXT, Fonts, px)
from .widgets import (Badge, FormDialog, Gauge, Panel, SlantButton, Stripes, Tag, TimeGlyph, eyebrow, label,
                      relative_time, separator, set_bg, spaced)

TASK_ORDER = {"Blocked": 0, "In progress": 1, "In review": 2, "Not started": 3, "Approved": 4}
# Week 1 of production starts on this Monday. A project can override it with "start_date" in
# coreline_project.json.
DEFAULT_START_DATE = "2026-09-28"


def production_week(start: str, today: date = None) -> int:
    """Week number since the production start: 1 in the start's week, +1 every Monday."""
    today = today or date.today()
    try:
        first = datetime.strptime(start, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        first = datetime.strptime(DEFAULT_START_DATE, "%Y-%m-%d").date()
    first_monday = first.toordinal() - first.weekday()
    this_monday = today.toordinal() - today.weekday()
    return max(1, (this_monday - first_monday) // 7 + 1)


class DashboardPage(Page):
    nav_title = "Dashboard"

    def __init__(self, parent, app):
        super().__init__(parent, app, scroll=True)
        right = self._greeting_header()
        SlantButton(right, "Launch DCC", app.launch_dialog, kind="primary", icon="↗",
                    tip="Start Houdini or Maya (any installed version).").pack(side="right")
        SlantButton(right, "New shot", self.new_shot, icon="+").pack(side="right", padx=(0, px(8)))

        # Gauges
        gauges = Panel(self.content)
        gauges.pack(fill="x")
        row = tk.Frame(gauges.body, bg=PANEL)
        row.pack(fill="x", padx=px(24), pady=px(18))
        self.film_gauge = Gauge(row, size=118, ticks=18, big=True)
        self.film_gauge.pack(side="left")
        text = tk.Frame(row, bg=PANEL)
        text.pack(side="left", padx=(px(16), px(26)))
        label(text, "FILM COMPLETE", Fonts.panel_title).pack(anchor="w")
        self.dept_count = label(text, "", Fonts.body, MUTED)
        self.dept_count.pack(anchor="w", pady=(px(4), px(8)))
        delivery = tk.Frame(text, bg=PANEL)
        delivery.pack(anchor="w")
        self.delivery_date = label(delivery, "", (Fonts.panel_title[0], 16, "bold"))
        self.delivery_date.pack(side="left")
        self.delivery_hint = label(delivery, "", Fonts.small, MUTED)
        self.delivery_hint.pack(side="left", padx=(px(6), 0), pady=(px(4), 0))
        separator(row, orient="v").pack(side="left", fill="y", padx=(0, px(20)))
        self.dept_row = tk.Frame(row, bg=PANEL)
        self.dept_row.pack(side="left", fill="x", expand=True)
        self.dept_gauges = {}

        # My tasks + recent publishes
        middle = tk.Frame(self.content, bg=BG)
        middle.pack(fill="x", pady=(px(18), 0))
        middle.columnconfigure(0, weight=13, uniform="mid")
        middle.columnconfigure(1, weight=7, uniform="mid")
        self.show_all_tasks = False
        self.tasks_panel = Panel(middle, "My tasks", right=self._tasks_toggle)
        self.tasks_panel.grid(row=0, column=0, sticky="nsew", padx=(0, px(18)))
        self.publish_panel = Panel(middle, "Recent publishes")
        self.publish_panel.grid(row=0, column=1, sticky="nsew")

        # Shot tracker
        self.tracker_panel = Panel(self.content, "Shot tracker", right=self._legend)
        self.tracker_panel.pack(fill="x", pady=(px(18), 0))
        self.tracker = tk.Canvas(self.tracker_panel.body, bg=PANEL, highlightthickness=0, height=px(60))
        self.tracker.pack(fill="x", padx=px(18), pady=px(14))
        self.tracker.bind("<Configure>", lambda e: self._draw_tracker())
        self.tracker.bind("<Button-1>", self._tracker_click)
        self._tracker_data = ([], [], {})

    # --- Header pieces ----------------------------------------------------

    def _greeting_header(self):
        """'GOOD MORNING, RITVIK' with the info line under it. Returns the frame for the buttons."""
        head = tk.Frame(self.content, bg=BG)
        head.pack(fill="x", pady=(0, px(16)))
        left = tk.Frame(head, bg=BG)
        left.pack(side="left")
        title = tk.Frame(left, bg=BG)
        title.pack(anchor="w")
        self.greeting_icon = TimeGlyph(title)
        self.greeting_tag = Tag(title)
        self.greeting_text = label(title, "", Fonts.page_title, padx=0)
        self.greeting_name = label(title, "", Fonts.page_title, RED, padx=0)
        self.info_row = tk.Frame(left, bg=BG)
        self.info_row.pack(anchor="w", pady=(px(2), 0))
        right = tk.Frame(head, bg=BG)
        right.pack(side="right", anchor="s")
        return right

    def _refresh_greeting(self, rows, publishes):
        app, project = self.app, self.app.project
        now = datetime.now()
        start = project.config.get("start_date", DEFAULT_START_DATE)
        try:
            delivery = datetime.strptime(project.config.get("delivery_date", ""), "%Y-%m-%d").date()
        except ValueError:
            delivery = None
        greet = greeting.pick_greeting(now, settings.first_name(), app.last_seen, delivery)

        for widget in (self.greeting_icon, self.greeting_tag, self.greeting_text, self.greeting_name):
            widget.pack_forget()
        if greet.badge:
            self.greeting_tag.set(greet.badge)
            self.greeting_tag.pack(side="left", padx=(0, px(12)))
        else:
            self.greeting_icon.set(greet.slot)
            self.greeting_icon.pack(side="left", padx=(0, px(12)))
        # The trailing spaces keep the italic's last letter from being clipped.
        self.greeting_text.configure(text=f"{greet.text}, " if greet.name else f"{greet.text} ")
        self.greeting_text.pack(side="left")
        if greet.name:
            self.greeting_name.configure(text=f"{greet.name} ")
            self.greeting_name.pack(side="left")

        mine = sum(1 for _e, _t, info in rows
                   if info.get("assignee") == app.user and info["status"] != "Approved")
        new = []
        if app.last_seen:
            since = app.last_seen.isoformat(timespec="seconds")
            for entity, task, pub in publishes:
                if pub.info.get("time", "") <= since:
                    break  # newest first
                who = pub.info.get("user", "")
                if who and who != app.user:
                    new.append((settings.first_name(who), f"{entity.display_name} {task} v{pub.version:03d}"))
        total = production_week(start, delivery) if delivery else None
        parts = greeting.info_line(production_week(start), total, mine, new, first_launch=app.last_seen is None)
        self._draw_info_line(parts)

    def _draw_info_line(self, parts):
        for child in self.info_row.winfo_children():
            child.destroy()
        fonts = {"": Fonts.body, "bold": Fonts.body_bold, "mono": Fonts.mono_bold,
                 "link": (Fonts.body[0], Fonts.body[1], "underline")}
        for i, runs in enumerate(parts):
            if i:
                label(self.info_row, "  ·  ", Fonts.body, DIM, padx=0, pady=0, bd=0).pack(side="left")
            for text, style in runs:
                run = label(self.info_row, text, fonts[style], MUTED if style == "" else TEXT, padx=0, pady=0, bd=0)
                run.pack(side="left")
                if style == "link":
                    run.configure(cursor="hand2")
                    run.bind("<Button-1>", lambda e: self._show_my_tasks())

    def _show_my_tasks(self):
        self.show_all_tasks = False
        self.refresh()
        self.update_idletasks()
        inner = self.scroller.inner
        y = self.tasks_panel.master.winfo_y() + self.content.winfo_y()
        if inner.winfo_height() > 0:
            self.scroller.canvas.yview_moveto(y / inner.winfo_height())

    def _tasks_toggle(self, parent):
        self.toggle_link = label(parent, "View all", Fonts.body, MUTED, cursor="hand2")
        self.toggle_link.pack()
        self.toggle_link.bind("<Button-1>", lambda e: self._toggle_tasks())

    def _toggle_tasks(self):
        self.show_all_tasks = not self.show_all_tasks
        self.refresh()

    def _legend(self, parent):
        for status in ("Approved", "In review", "In progress", "Not started", "Blocked"):
            swatch = tk.Canvas(parent, width=px(18), height=px(9), bg=PANEL, highlightthickness=0)
            swatch.create_polygon(px(3), 0, px(18), 0, px(15), px(9), 0, px(9), fill=STATUS_COLORS[status][2])
            swatch.pack(side="left", padx=(px(12), px(5)))
            label(parent, status, Fonts.small, MUTED).pack(side="left")

    # --- Refresh ----------------------------------------------------------

    def refresh(self):
        project = self.app.project
        # Collect every task once.
        rows = []
        for kind in ("asset", "shot"):
            for entity in project.list_entities(kind):
                for task in project.entity_tasks(entity):
                    rows.append((entity, task, project.task_info(entity, task)))
        # Read once: the greeting wants everything since your last visit, the panel the newest 5.
        publishes = project.all_publishes(limit=200)
        self._refresh_greeting(rows, publishes)
        self._refresh_gauges(rows)
        self._refresh_tasks(rows)
        self._refresh_publishes(publishes[:5])
        shots = project.list_entities("shot")
        status = {(e.path, t): info["status"] for e, t, info in rows}
        self._tracker_data = (shots, project.all_tasks("shot"), status)
        self._draw_tracker()

    def _refresh_gauges(self, rows):
        project = self.app.project
        film, per_task = project.progress()
        departments = list(dict.fromkeys(project.all_tasks("asset") + project.all_tasks("shot")))
        self.film_gauge.set(film)
        self.dept_count.configure(text=f"Across {len(departments)} departments")
        delivery = project.config.get("delivery_date", "")
        try:
            when = datetime.strptime(delivery, "%Y-%m-%d")
            self.delivery_date.configure(text=when.strftime("%d %b %Y").upper())
            days = (when.date() - date.today()).days
            self.delivery_hint.configure(text=f"final delivery · {days} days" if days >= 0 else "final delivery")
        except ValueError:
            self.delivery_date.configure(text="[DATE]")
            self.delivery_hint.configure(text="set the delivery date in Settings")
        if list(self.dept_gauges) != departments:
            for child in self.dept_row.winfo_children():
                child.destroy()
            self.dept_gauges = {}
            for task in departments:
                gauge = Gauge(self.dept_row, size=74, ticks=12, caption=task)
                gauge.pack(side="left", expand=True)
                self.dept_gauges[task] = gauge
        for task, gauge in self.dept_gauges.items():
            gauge.set(per_task.get(task, 0.0))

    def _refresh_tasks(self, rows):
        body = self.tasks_panel.body
        for child in body.winfo_children():
            child.destroy()
        self.toggle_link.configure(text="Show mine" if self.show_all_tasks else "View all")
        if self.show_all_tasks:
            chosen = [r for r in rows if r[2]["status"] in ("In progress", "In review", "Blocked")]
        else:
            chosen = [r for r in rows if r[2].get("assignee") == self.app.user and r[2]["status"] != "Approved"]
        chosen.sort(key=lambda r: (TASK_ORDER[r[2]["status"]], r[2].get("updated", "")))

        table = tk.Frame(body, bg=PANEL)
        table.pack(fill="both", expand=True)
        widths = (0, 1, 2, 3, 4)
        for col, weight in zip(widths, (3, 2, 3, 2, 2)):
            table.columnconfigure(col, weight=weight, uniform="t")
        headings = ["ENTITY", "DEPT", "STATUS", "ARTIST" if self.show_all_tasks else "VERSION", "UPDATED"]
        for col, text in enumerate(headings):
            eyebrow(table, text, DIM).grid(row=0, column=col, sticky="w", padx=px(18), pady=(px(12), px(8)))
        separator(table).grid(row=1, column=0, columnspan=5, sticky="ew")
        if not chosen:
            text = ("Nothing in progress yet." if self.show_all_tasks else
                    "Nothing assigned to you. Assign yourself to a task in Assets & Shots.")
            label(table, text, Fonts.body, MUTED).grid(row=2, column=0, columnspan=5, sticky="w",
                                                      padx=px(18), pady=px(18))
            return
        project = self.app.project
        for i, (entity, task, info) in enumerate(chosen[:7]):
            r = 2 + i * 2
            cells = []
            name = entity.display_name
            cells.append(label(table, name, Fonts.mono))
            cells.append(label(table, task.title(), Fonts.body, MUTED))
            holder = tk.Frame(table, bg=PANEL)
            Badge(holder, info["status"]).pack(anchor="w")
            cells.append(holder)
            if self.show_all_tasks:
                cells.append(label(table, info.get("assignee") or "—", Fonts.body, MUTED))
            else:
                version = project.latest_version(entity, task)
                cells.append(label(table, f"v{version:03d}" if version else "—", Fonts.mono, MUTED))
            cells.append(label(table, relative_time(info.get("updated", "")) or "—", Fonts.body, MUTED))
            for col, cell in enumerate(cells):
                cell.grid(row=r, column=col, sticky="w", padx=px(18), pady=px(10))
                cell.configure(cursor="hand2")
                cell.bind("<Button-1>", lambda e, en=entity, t=task: self.app.open_in_assets(en, t))
                for sub in cell.winfo_children():
                    sub.bind("<Button-1>", lambda e, en=entity, t=task: self.app.open_in_assets(en, t))
            if i < len(chosen[:7]) - 1:
                separator(table).grid(row=r + 1, column=0, columnspan=5, sticky="ew")

    def _refresh_publishes(self, publishes):
        body = self.publish_panel.body
        for child in body.winfo_children():
            child.destroy()
        if not publishes:
            label(body, "Nothing published yet.", Fonts.body, MUTED).pack(anchor="w", padx=px(18), pady=px(18))
            return
        for i, (entity, task, pub) in enumerate(publishes):
            row = tk.Frame(body, bg=PANEL, cursor="hand2")
            row.pack(fill="x")
            Stripes(row, width=56, height=34, image=thumbnails.find(entity.path)).pack(side="left", padx=(px(18), px(12)), pady=px(10))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left")
            line = tk.Frame(text, bg=PANEL)
            line.pack(anchor="w")
            name = entity.display_name
            label(line, name, Fonts.mono).pack(side="left")
            label(line, f" v{pub.version:03d}", Fonts.mono, RED).pack(side="left")
            label(text, f"{task} · {pub.info.get('user', '')}", Fonts.small, MUTED).pack(anchor="w")
            label(row, relative_time(pub.info.get("time", "")), Fonts.small, MUTED).pack(side="right",
                                                                                      padx=px(18))
            for widget in [row] + row.winfo_children() + text.winfo_children() + line.winfo_children():
                widget.bind("<Button-1>", lambda e, en=entity, t=task: self.app.open_in_assets(en, t))
            row.bind("<Enter>", lambda e, r=row: set_bg(r, PANEL_HI))
            row.bind("<Leave>", lambda e, r=row: set_bg(r, PANEL))
            if i < len(publishes) - 1:
                separator(body).pack(fill="x")

    # --- Shot tracker -----------------------------------------------------

    def _tracker_geometry(self):
        shots, tasks, _ = self._tracker_data
        w = self.tracker.winfo_width()
        label_w = px(90)
        head_h = px(42)
        row_h = px(24)
        cell_w = (w - label_w) / max(1, len(shots))
        return label_w, head_h, row_h, cell_w

    def _draw_tracker(self):
        c = self.tracker
        c.delete("all")
        shots, tasks, status = self._tracker_data
        if not shots:
            c.configure(height=px(40))
            c.create_text(0, px(20), text="No shots yet. Click '+ New shot' to add the first one.",
                          fill=MUTED, font=Fonts.body, anchor="w")
            return
        label_w, head_h, row_h, cell_w = self._tracker_geometry()
        c.configure(height=head_h + row_h * len(tasks) + px(4))
        for col, shot in enumerate(shots):
            x = label_w + col * cell_w + cell_w / 2
            if shot.group:
                c.create_text(x, px(10), text=shot.group.upper(), fill=MUTED, font=Fonts.mono_small)
            c.create_text(x, px(24 if shot.group else 17), text=shot.name.upper(), fill=MUTED,
                          font=Fonts.mono_small)
        skew = px(5)
        for row, task in enumerate(tasks):
            y = head_h + row * row_h
            c.create_text(0, y + row_h / 2, text=spaced(task), fill=MUTED, font=Fonts.gauge_label, anchor="w")
            for col, shot in enumerate(shots):
                x0 = label_w + col * cell_w + px(4)
                x1 = label_w + (col + 1) * cell_w - px(4)
                color = STATUS_COLORS[status.get((shot.path, task), "Not started")][2]
                c.create_polygon(x0 + skew, y + px(4), x1, y + px(4), x1 - skew, y + row_h - px(4),
                                 x0, y + row_h - px(4), fill=color, outline="")

    def _tracker_click(self, event):
        shots, tasks, _ = self._tracker_data
        if not shots:
            return
        label_w, head_h, row_h, cell_w = self._tracker_geometry()
        col = int((event.x - label_w) // cell_w)
        row = int((event.y - head_h) // row_h)
        if 0 <= col < len(shots) and 0 <= row < len(tasks) and event.x >= label_w:
            self.app.open_in_assets(shots[col], tasks[row])

    # --- Actions ----------------------------------------------------------

    def new_shot(self):
        from .page_assets import create_shot_dialog
        entity = create_shot_dialog(self.app)
        if entity:
            self.refresh()
