"""The CORELINE window: sidebar, top bar, pages, and opening the project."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Tuple

from .. import __version__, apps, drives, settings
from ..project import PipelineError, Project
from . import theme
from .theme import BG, BORDER, DIM, MUTED, PANEL_HI, RED, SIDEBAR, TEXT, Fonts, px
from .widgets import Avatar, FormDialog, SlantButton, label, separator

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

NAV = [
    ("dashboard", "Dashboard", "\u25a6"),
    ("assets", "Assets & Shots", "\u2b21"),
    ("publish", "Publish", "\u2912"),
    ("render", "Render", "\u25b7"),
    ("farm", "Farm", "\u25a4"),
]


def open_project(real_root: Path) -> Tuple[Project, str]:
    """Load the project and, if the project asks for it, show it as a drive letter.
    Returns the project (rooted at X:/ when mapped) and a status message."""
    project = Project(real_root)
    letter = project.config.get("drive_letter")
    if not letter or not settings.get_user_setting("use_drive", True):
        return project, ""
    mapped, message = drives.map_drive(letter, real_root)
    current = drives.current_mappings().get(letter.strip().rstrip(":").upper())
    ours = current and (settings.is_project(current) or drives.same_path(current, settings.get_user_setting("mapped")))
    if not mapped and ours:
        # The letter still points where CORELINE put it (another project, or a folder that was one):
        # move it to this project. Other uses of the letter are left alone.
        drives.unmap_drive(letter)
        mapped, message = drives.map_drive(letter, real_root)
    if mapped:
        settings.save_user_setting("mapped", str(real_root))
        return Project(mapped, project.config), message
    return project, message


def choose_project_folder(parent) -> Optional[Path]:
    """Ask for the project folder, offering to set one up if it isn't a project yet."""
    messagebox.showinfo(
        "CORELINE - Project Folder",
        "Choose the film's project folder.\n\n"
        "This is the shared OneDrive folder that holds (or will hold) the assets and shots. "
        "You only need to do this once on each computer.",
        parent=parent)
    chosen = filedialog.askdirectory(parent=parent, title="Choose the film's project folder")
    if not chosen:
        return None
    root = drives.real_path(chosen)
    if not settings.is_project(root):
        if not messagebox.askyesno(
                "CORELINE - Project Folder",
                f"There's no CORELINE project in:\n{root}\n\n"
                "Set one up here? This adds a coreline_project.json file (and 'assets' and 'shots' "
                "folders if they're missing). Existing folders and files aren't moved or renamed.\n\n"
                "(If your team already has a project, choose No and pick that folder instead.)",
                parent=parent):
            return None
        Project.create(root, settings.load_project_template())
    settings.remember_project_root(root)
    return root


class NavItem(tk.Canvas):
    """A sidebar entry; the active one gets the red slanted block from the design."""

    def __init__(self, parent, text, icon, command):
        super().__init__(parent, height=px(44), bg=SIDEBAR, highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.icon, self.command = text, icon, command
        self.active = self.hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: command())

    def _set_hover(self, value):
        self.hover = value
        self._draw()

    def set_active(self, value):
        self.active = value
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self["height"])
        left = px(12)
        if self.active:
            self.create_polygon(left, 0, w, 0, w - px(12), h, left, h, fill=RED, outline="")
        elif self.hover:
            self.create_rectangle(left, 0, w - px(12), h, fill=PANEL_HI, outline="")
        color = TEXT if (self.active or self.hover) else MUTED
        self.create_text(left + px(18), h / 2, text=self.icon, fill=color, font=Fonts.icon)
        self.create_text(left + px(44), h / 2, text=self.text, fill=TEXT if self.active else color,
                         font=Fonts.nav, anchor="w")


class MainWindow(tk.Tk):
    def __init__(self, project: Project, real_root: Path, start_message: str = ""):
        super().__init__()
        theme.init_theme(self)
        self.project = project
        self.real_root = real_root
        self.user = settings.current_user()
        self.last_seen = project.start_visit(self.user)  # for the dashboard greeting
        self.app_config = settings.load_app_config()
        self.installations = {}
        self.detect_software()
        self.selection = {"entity": None, "task": None, "file": None}
        self.pending_open = None
        self.current_page = None

        self.title(f"CORELINE {__version__}")
        try:
            self.iconbitmap(default=str(ASSETS_DIR / "coreline.ico"))  # window, taskbar and dialogs
        except tk.TclError:
            pass
        width = min(px(1400), self.winfo_screenwidth() - px(40))
        height = min(px(880), self.winfo_screenheight() - px(80))
        self.geometry(f"{width}x{height}+{px(20)}+{px(10)}")
        self.minsize(min(px(1180), width), min(px(700), height))
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main()
        self.bind("<F5>", lambda e: self.refresh_current())
        self.bind("<Control-k>", lambda e: self.focus_search())
        self.bind("<Control-K>", lambda e: self.focus_search())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.status(start_message or "Ready.")
        self.go("dashboard")
        if not settings.get_user_setting("user") and not settings.team_member():
            self.after(300, self.ask_name)

    # --- Layout -----------------------------------------------------------

    def _build_sidebar(self):
        side = tk.Frame(self, bg=SIDEBAR, width=px(230))
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=px(18), pady=(px(22), px(18)))
        logo_file = "logo_2x.png" if theme.SCALE >= 1.75 else "logo_15x.png" if theme.SCALE >= 1.25 else "logo_1x.png"
        try:
            self._logo = tk.PhotoImage(file=str(ASSETS_DIR / logo_file))
            tk.Label(brand, image=self._logo, bg=SIDEBAR).pack(anchor="w")
        except tk.TclError:
            label(brand, "CORE", Fonts.page_title, bg=SIDEBAR).pack(anchor="w")
        word = tk.Frame(brand, bg=SIDEBAR)
        word.pack(anchor="w", pady=(px(8), 0))
        tk.Frame(word, bg=RED, width=px(18), height=px(3)).pack(side="left", padx=(0, px(8)))
        label(word, "CORELINE", (Fonts.panel_title[0], 15, "bold italic"), bg=SIDEBAR).pack(side="left")
        separator(side).pack(fill="x")

        self.nav_items = {}
        nav = tk.Frame(side, bg=SIDEBAR)
        nav.pack(fill="x", pady=(px(14), 0))
        for key, text, icon in NAV:
            item = NavItem(nav, text, icon, lambda k=key: self.go(k))
            item.pack(fill="x", pady=px(1))
            self.nav_items[key] = item

        bottom = tk.Frame(side, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x")
        item = NavItem(bottom, "Settings", "\u2699", lambda: self.go("settings"))
        item.pack(fill="x", pady=(0, px(10)))
        self.nav_items["settings"] = item
        separator(bottom).pack(fill="x")
        self.user_row = tk.Frame(bottom, bg=SIDEBAR)
        self.user_row.pack(fill="x", padx=px(18), pady=px(14))
        self._draw_user_row()

    def _draw_user_row(self):
        for child in self.user_row.winfo_children():
            child.destroy()
        Avatar(self.user_row, self.user, size=34).pack(side="left")
        text = tk.Frame(self.user_row, bg=SIDEBAR)
        text.pack(side="left", padx=(px(10), 0))
        label(text, self.user.title(), Fonts.body_bold, bg=SIDEBAR).pack(anchor="w")
        label(text, self.project.name, Fonts.small, MUTED, bg=SIDEBAR).pack(anchor="w")

    def _build_main(self):
        main = tk.Frame(self, bg=BG)
        main.grid(row=0, column=1, sticky="nsew")
        tk.Frame(self, bg=BORDER, width=1).grid(row=0, column=0, sticky="nse")

        top = tk.Frame(main, bg=BG, height=px(58))
        top.pack(fill="x")
        top.pack_propagate(False)
        inner = tk.Frame(top, bg=BG)
        inner.pack(fill="both", expand=True, padx=px(26))
        self.project_pill = tk.Label(inner, text=f"{self.project.name.upper()}  \u25be", font=Fonts.button,
                                     bg="#18181b", fg=TEXT, padx=px(12), pady=px(5), cursor="hand2",
                                     highlightthickness=1, highlightbackground=BORDER)
        self.project_pill.pack(side="left", pady=px(12))
        self.project_pill.bind("<Button-1>", self._project_menu)
        self.crumbs = tk.Frame(inner, bg=BG)
        self.crumbs.pack(side="left", padx=(px(18), 0))

        search = tk.Frame(inner, bg="#141416", highlightthickness=1, highlightbackground=BORDER)
        search.pack(side="right", pady=px(11))
        label(search, "\u2315", Fonts.icon, MUTED, bg="#141416").pack(side="left", padx=(px(10), px(4)))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(search, textvariable=self.search_var, bg="#141416", fg=TEXT, bd=0,
                                     insertbackground=TEXT, font=Fonts.body, width=30,
                                     highlightthickness=0)
        self.search_entry.pack(side="left", ipady=px(4))
        self._placeholder(True)
        self.search_entry.bind("<FocusIn>", lambda e: self._placeholder(False))
        self.search_entry.bind("<FocusOut>", lambda e: self._placeholder(not self.search_var.get()))
        self.search_entry.bind("<Return>", lambda e: self.run_search())
        self.search_entry.bind("<KeyRelease>", lambda e: self._live_search())
        tk.Label(search, text="Ctrl K", font=Fonts.mono_small, bg="#141416", fg=MUTED, padx=px(5),
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=px(8))
        separator(main).pack(fill="x")

        self.page_area = tk.Frame(main, bg=BG)
        self.page_area.pack(fill="both", expand=True)
        self.page_area.rowconfigure(0, weight=1)
        self.page_area.columnconfigure(0, weight=1)

        from .page_assets import AssetsPage
        from .page_dashboard import DashboardPage
        from .page_farm import FarmPage
        from .page_publish import PublishPage
        from .page_render import RenderPage
        from .page_settings import SettingsPage
        self.pages = {
            "dashboard": DashboardPage(self.page_area, self),
            "assets": AssetsPage(self.page_area, self),
            "publish": PublishPage(self.page_area, self),
            "render": RenderPage(self.page_area, self),
            "farm": FarmPage(self.page_area, self),
            "settings": SettingsPage(self.page_area, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self.status_var = tk.StringVar()
        bar = tk.Frame(main, bg=BG)
        bar.pack(fill="x", side="bottom")
        separator(bar).pack(fill="x")
        tk.Label(bar, textvariable=self.status_var, font=Fonts.small, fg=MUTED, bg=BG, anchor="w",
                 padx=px(26), pady=px(5)).pack(fill="x")

    def _placeholder(self, show):
        entry = self.search_entry
        if show and not self.search_var.get():
            entry.configure(fg=DIM)
            entry.insert(0, "Search assets, shots, versions...")
            self._placeholder_on = True
        elif getattr(self, "_placeholder_on", False) and not show:
            entry.delete(0, "end")
            entry.configure(fg=TEXT)
            self._placeholder_on = False

    def search_text(self) -> str:
        return "" if getattr(self, "_placeholder_on", False) else self.search_var.get().strip().lower()

    def focus_search(self):
        self.search_entry.focus_set()
        return "break"

    def _live_search(self):
        if self.current_page == "assets":
            self.pages["assets"].refresh_grid()

    def run_search(self):
        self.go("assets")
        self.pages["assets"].refresh_grid()

    def set_breadcrumb(self, parts):
        for child in self.crumbs.winfo_children():
            child.destroy()
        for i, part in enumerate(parts):
            if i:
                label(self.crumbs, "\u203a", Fonts.body, DIM, bg=BG).pack(side="left", padx=px(8))
            last = i == len(parts) - 1
            label(self.crumbs, part, Fonts.body_bold if last else Fonts.body, TEXT if last else MUTED,
                  bg=BG).pack(side="left")

    def _project_menu(self, event):
        menu = tk.Menu(self, tearoff=0, font=Fonts.body)
        menu.add_command(label="Refresh   (F5)", command=self.refresh_current)
        menu.add_command(label="Change project folder...", command=self.change_project)
        menu.add_separator()
        menu.add_command(label=f"Folder: {self.project.root}", state="disabled")
        menu.tk_popup(event.x_root, event.y_root + px(10))

    # --- Navigation & shared state ----------------------------------------

    def go(self, key):
        self.current_page = key
        for name, item in self.nav_items.items():
            item.set_active(name == key)
        page = self.pages[key]
        page.tkraise()
        page.refresh()
        self.set_breadcrumb(page.breadcrumb())

    def refresh_current(self):
        if self.current_page:
            self.go(self.current_page)
        self.status("Refreshed.")

    def select(self, entity=None, task=None, file: Path = None):
        self.selection = {"entity": entity, "task": task, "file": file}
        if self.current_page:
            self.set_breadcrumb(self.pages[self.current_page].breadcrumb())

    def open_in_assets(self, entity, task=None):
        self.pending_open = (entity, task)
        self.go("assets")

    def status(self, message: str):
        self.status_var.set(message)

    # --- Software ---------------------------------------------------------

    def detect_software(self):
        self.installations = apps.detect_all(self.app_config, settings.get_user_setting("extra_app_paths", {}))

    def all_installations(self):
        return [inst for installs in self.installations.values() for inst in installs]

    def default_installation(self, app_name: str):
        wanted = settings.get_user_setting("default_versions", {}).get(app_name)
        return apps.pick_installation(self.installations.get(app_name, []), default_version=wanted)

    def installation_for_file(self, path: Path):
        """(best installation to open this file with, version the file was saved with)."""
        app_name = apps.app_for_extension(self.app_config, path.suffix)
        if not app_name:
            return None, None
        saved = apps.saved_version(self.app_config[app_name], path)
        wanted_default = settings.get_user_setting("default_versions", {}).get(app_name)
        inst = apps.pick_installation(self.installations.get(app_name, []), saved, wanted_default)
        return inst, saved

    def launch(self, inst, entity=None, task=None, file: Path = None) -> bool:
        cwd = self.project.work_dir(entity, task) if entity and task else self.project.root
        try:
            apps.launch(inst, self.app_config[inst.app], file,
                        self.project.context_env(entity, task, self.user), cwd=cwd)
        except OSError as e:
            messagebox.showerror("CORELINE", f"Could not start {inst.display}:\n{e}", parent=self)
            return False
        where = f" for {entity.label} > {task}" if entity and task else ""
        self.status(f"Starting {inst.display}" + (f" with {file.name}" if file else "") + where + "...")
        return True

    def launch_dialog(self):
        """'Launch DCC': pick any installed version, started in the selected task if there is one."""
        from .dialogs import SoftwareDialog
        entity, task = self.selection["entity"], self.selection["task"]
        where = f"{entity.label} > {task}" if entity and task else "the project (no task selected)"
        inst = SoftwareDialog(self, "Launch DCC", f"Starts in {where}.", "Launch").result
        if inst:
            self.launch(inst, entity, task)

    def new_scene(self, entity, task, part=""):
        from .dialogs import SoftwareDialog
        where = f"{entity.label} > {task}" + (f" > {part}" if part else "")
        inst = SoftwareDialog(self, "New Scene", f"Starts an empty scene for {where}.", "Start").result
        if not inst:
            return
        extension = self.app_config[inst.app]["extensions"][0]
        target = self.project.new_workfile_path(entity, task, extension, self.user, part)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self.launch(inst, entity, task):
            return
        self.clipboard_clear()
        self.clipboard_append(str(target))
        messagebox.showinfo(
            "New Scene",
            f"{inst.display} is starting.\n\nWhen it's open, use File > Save As and save to:\n\n{target}\n\n"
            "(This is already copied: just paste it into the file name box.)\n\n"
            "Then press F5 in CORELINE to see the new file.", parent=self)

    def to_project_path(self, path: Path) -> Path:
        """A file picked via its full OneDrive path -> the same file on the project drive (X:/...)."""
        path = drives.real_path(path)
        try:
            return Path(self.project.root) / path.relative_to(self.real_root)
        except ValueError:
            return path

    def show_render_for(self, path: Path):
        self.go("render")
        self.pages["render"].set_scene(path)

    # --- Settings & project -----------------------------------------------

    def set_user(self, name: str):
        self.user = settings.sanitize_user(name)
        settings.save_user_setting("user", self.user)
        self.last_seen = self.project.start_visit(self.user)
        self._draw_user_row()
        if self.current_page == "dashboard":
            self.pages["dashboard"].refresh()  # greet them by their new name

    def ask_name(self):
        """University logins are student IDs (s5707305); ask for a friendlier name for file names."""
        form = FormDialog(self, "Welcome to CORELINE", [("user", "Your name", "", None)], ok_text="Save", intro=(
            "What should CORELINE call you? It goes into your scene file names, e.g. "
            "sh010_anim_v003_ritvik.hip. Use the same short name on every computer."))
        self.set_user((form.result or {}).get("user") or self.user)
        self.status(f"Hi {self.user}! You can change your name any time in Settings.")

    def change_project(self):
        if self.pages["render"].has_active_jobs():
            messagebox.showwarning("Change Project", "Wait for the renders to finish (or stop them) first.",
                                   parent=self)
            return
        root = choose_project_folder(self)
        if not root:
            return
        if Path(self.project.root) != Path(self.real_root):
            drives.unmap_drive(str(self.project.root)[0])  # free our letter for the new project
        try:
            self.project, message = open_project(root)
        except PipelineError as e:
            messagebox.showerror("Change Project", str(e), parent=self)
            return
        self.real_root = root
        self.last_seen = self.project.start_visit(self.user)
        self.selection = {"entity": None, "task": None, "file": None}
        self.project_pill.configure(text=f"{self.project.name.upper()}  \u25be")
        self._draw_user_row()
        self.refresh_current()
        self.status(message or f"Opened {root}.")

    def on_close(self):
        if self.pages["farm"].has_active_worker():
            if not messagebox.askyesno("CORELINE", "This PC is rendering farm jobs.\n\n"
                                       "Closing CORELINE stops that (its current chunk goes back to the farm "
                                       "for another PC). Close anyway?", parent=self):
                return
            self.pages["farm"].stop_worker()
        if self.pages["render"].has_active_jobs():
            if not messagebox.askyesno("CORELINE", "Renders are still running or waiting.\n\n"
                                       "Closing CORELINE stops them. Close anyway?", parent=self):
                return
            self.pages["render"].queue.stop_all()
        self.project.mark_seen(self.user)
        self.destroy()


def enable_sharp_text():
    """Without this, Windows stretches the window on high-resolution screens and text looks blurry."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main():
    enable_sharp_text()
    root = settings.find_project_root()
    if root is None:
        picker = tk.Tk()
        picker.withdraw()
        root = choose_project_folder(picker)
        picker.destroy()
        if root is None:
            return
    try:
        project, message = open_project(root)
    except PipelineError as e:
        picker = tk.Tk()
        picker.withdraw()
        messagebox.showerror("CORELINE", str(e), parent=picker)
        picker.destroy()
        return
    MainWindow(project, root, message).mainloop()
