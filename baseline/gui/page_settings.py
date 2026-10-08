"""Settings: your name, the project, and the software BASELINE can launch."""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import settings
from .page_base import Page
from .theme import BG, BORDER, DIM, MUTED, PANEL, PANEL_HI, RED, TEXT, Fonts, px
from .widgets import CheckBox, FormDialog, Panel, SlantButton, eyebrow, label, separator


class SettingsPage(Page):
    nav_title = "Settings"

    def __init__(self, parent, app):
        super().__init__(parent, app, scroll=True)
        self.header("Preferences", "Settings")
        cols = tk.Frame(self.content, bg=BG)
        cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=4, uniform="s")
        cols.columnconfigure(1, weight=6, uniform="s")
        left = tk.Frame(cols, bg=BG)
        left.grid(row=0, column=0, sticky="new", padx=(0, px(18)))
        self.software_panel = Panel(cols, "Software", right=self._software_buttons)
        self.software_panel.grid(row=0, column=1, sticky="new")

        # You
        you = Panel(left, "You")
        you.pack(fill="x")
        f = tk.Frame(you.body, bg=PANEL)
        f.pack(fill="x", padx=px(18), pady=px(16))
        label(f, "Your name (goes in file names, e.g. sh010_anim_v003_ritvik.hip)", Fonts.small, MUTED,
              wraplength=px(330), justify="left").pack(anchor="w")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", pady=(px(6), 0))
        self.name_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.name_var).pack(side="left", fill="x", expand=True)
        SlantButton(row, "Save", self.save_name, kind="primary", height=32).pack(side="left", padx=(px(8), 0))

        # Project
        proj = Panel(left, "Project")
        proj.pack(fill="x", pady=(px(18), 0))
        f = tk.Frame(proj.body, bg=PANEL)
        f.pack(fill="x", padx=px(18), pady=px(16))
        eyebrow(f, "Folder", MUTED).pack(anchor="w")
        self.folder_label = label(f, "", Fonts.mono_small, TEXT, wraplength=px(330), justify="left")
        self.folder_label.pack(anchor="w", pady=(px(4), 0))
        self.real_label = label(f, "", Fonts.small, MUTED, wraplength=px(330), justify="left")
        self.real_label.pack(anchor="w")
        SlantButton(f, "Change project folder...", self.app.change_project, height=32).pack(anchor="w",
                                                                                           pady=(px(8), 0))
        separator(f).pack(fill="x", pady=px(14))
        eyebrow(f, "Final delivery date", MUTED).pack(anchor="w")
        label(f, "Shown on the dashboard for the whole team. Format: 2027-05-14", Fonts.small, MUTED).pack(
            anchor="w", pady=(px(2), px(6)))
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x")
        self.date_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.date_var, font=Fonts.mono).pack(side="left", fill="x", expand=True)
        SlantButton(row, "Save", self.save_date, height=32).pack(side="left", padx=(px(8), 0))
        separator(f).pack(fill="x", pady=px(14))
        eyebrow(f, "Drive letter", MUTED).pack(anchor="w")
        self.drive_var = tk.BooleanVar()
        self.drive_check = CheckBox(f, self.drive_var, "", command=self.save_drive)
        self.drive_check.pack(anchor="w", pady=(px(8), 0))
        label(f, "Shows the project as the same drive on every computer, so paths inside Houdini/Maya "
                 "scenes work for everyone. Applies next time BASELINE opens.", Fonts.small, MUTED,
              wraplength=px(330), justify="left").pack(anchor="w", pady=(px(4), 0))

    # --- Refresh ----------------------------------------------------------

    def refresh(self):
        project = self.app.project
        self.name_var.set(self.app.user)
        self.folder_label.configure(text=str(project.root))
        real = Path(self.app.real_root)
        self.real_label.configure(text=f"= {real}" if Path(project.root) != real else "")
        self.date_var.set(project.config.get("delivery_date", ""))
        letter = project.config.get("drive_letter")
        self.drive_check.set_text(f"Show the project as drive {letter}:" if letter else
                                  "(No drive letter set for this project)")
        self.drive_check.set_enabled(bool(letter))
        self.drive_var.set(bool(settings.get_user_setting("use_drive", True)))
        self._fill_software()

    def _software_buttons(self, parent):
        SlantButton(parent, "Add location...", self.add_location, kind="ghost", icon="+", height=30).pack(
            side="left")
        SlantButton(parent, "Search again", self.rescan, kind="ghost", icon="↻", height=30).pack(side="left")

    def _fill_software(self):
        body = self.software_panel.body
        for child in body.winfo_children():
            child.destroy()
        installs = self.app.all_installations()
        label(body, "Found on this computer through the Windows registry and the usual install folders. "
                    "Your default is used for new scenes and when a scene's own version isn't installed.",
              Fonts.small, MUTED, wraplength=px(560), justify="left").pack(anchor="w", padx=px(18),
                                                                          pady=(px(14), px(8)))
        if not installs:
            label(body, "No Houdini or Maya found. Click 'Add location...' and choose its .exe.",
                  Fonts.body, TEXT).pack(anchor="w", padx=px(18), pady=px(12))
        for inst in installs:
            separator(body).pack(fill="x")
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", padx=px(18), pady=px(10))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            top = tk.Frame(text, bg=PANEL)
            top.pack(anchor="w")
            label(top, inst.label, Fonts.body_bold).pack(side="left")
            label(top, f"  {inst.version}", Fonts.mono, TEXT).pack(side="left")
            if self.app.default_installation(inst.app) == inst:
                tk.Label(top, text="■ default", font=Fonts.small, fg=TEXT, bg="#3a1417", padx=px(6)).pack(
                    side="left", padx=(px(10), 0))
            label(text, str(inst.install_dir), Fonts.mono_small, DIM).pack(anchor="w", pady=(px(2), 0))
            SlantButton(row, "Launch", lambda i=inst: self.app.launch(i, self.app.selection["entity"],
                                                                      self.app.selection["task"]),
                        kind="primary", icon="↗", height=30).pack(side="right")
            if self.app.default_installation(inst.app) != inst:
                SlantButton(row, "Make default", lambda i=inst: self.make_default(i), kind="ghost",
                            height=30).pack(side="right")
        separator(body).pack(fill="x")
        label(body, "Need other software (e.g. Nuke)? It's added once in config/apps.json and then shows "
                    "up for everyone.", Fonts.small, DIM, wraplength=px(560), justify="left").pack(
            anchor="w", padx=px(18), pady=px(14))

    # --- Actions ----------------------------------------------------------

    def save_name(self):
        name = self.name_var.get().strip()
        if not name:
            return
        self.app.set_user(name)
        self.name_var.set(self.app.user)
        self.app.status(f"Saved. Your files will be named ..._{self.app.user}.")

    def save_date(self):
        text = self.date_var.get().strip()
        if text:
            try:
                datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Delivery date", "Use the format YYYY-MM-DD, e.g. 2027-05-14.", parent=self)
                return
        self.app.project.save_config(delivery_date=text)
        self.app.status("Delivery date saved for the whole team." if text else "Delivery date cleared.")

    def save_drive(self):
        settings.save_user_setting("use_drive", bool(self.drive_var.get()))
        self.app.status("Saved. The drive letter setting applies next time BASELINE opens.")

    def make_default(self, inst):
        defaults = settings.get_user_setting("default_versions", {})
        defaults[inst.app] = inst.version
        settings.save_user_setting("default_versions", defaults)
        self._fill_software()
        self.app.status(f"{inst.display} is now your default {inst.label}.")

    def add_location(self):
        labels = {cfg.get("label", name): name for name, cfg in self.app.app_config.items()}
        form = FormDialog(self.app, "Add software location", [
            ("software", "Software", next(iter(labels)), list(labels))],
            intro="Choose which software this is, then pick its program file (e.g. houdini.exe in "
                  "Houdini's bin folder, or maya.exe in Maya's bin folder).", ok_text="Choose file...")
        if not form.result:
            return
        app_name = labels[form.result["software"]]
        exe = filedialog.askopenfilename(parent=self, title=f"Choose {form.result['software']}'s program file",
                                         filetypes=[("Programs", "*.exe")])
        if not exe:
            return
        extra = settings.get_user_setting("extra_app_paths", {})
        extra.setdefault(app_name, [])
        if exe not in extra[app_name]:
            extra[app_name].append(exe)
        settings.save_user_setting("extra_app_paths", extra)
        self.rescan()

    def rescan(self):
        self.app.detect_software()
        self._fill_software()
        self.app.status("Searched for installed software again.")
