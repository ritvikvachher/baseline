"""Colours, fonts and ttk styling for the dark CORELINE look."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# Colours
BG = "#0e0e10"          # page background
SIDEBAR = "#09090a"
PANEL = "#151517"
PANEL_HI = "#1d1d20"    # hovered / selected rows
FIELD = "#101012"       # text boxes
BORDER = "#28282c"
TEXT = "#ececee"
MUTED = "#8c8c94"
DIM = "#5d5d64"
RED = "#d0141d"
RED_HI = "#e8262f"
RED_DARK = "#7a1015"
BLUE = "#6f9df2"
AMBER = "#eaa33c"
TRACK = "#2a2a2f"       # empty gauge ticks, not-started bars

# How each task status is drawn: (badge text colour, badge background, tracker colour)
STATUS_COLORS = {
    "Not started": ("#b5b5bd", "#252529", TRACK),
    "In progress": (AMBER, "#382910", AMBER),
    "In review": (BLUE, "#16213a", BLUE),
    "Approved": ("#ededf0", "#2c2c31", RED),
    "Blocked": ("#ff5d5d", "#3b1417", RED_DARK),
}

SCALE = 1.0  # screen scaling, set by init_theme


def px(value: float) -> int:
    """A size in pixels at normal scaling -> pixels on this screen."""
    return int(round(value * SCALE))


class Fonts:
    """Filled in by init_theme once Tk is running (fonts need a root window)."""
    page_title = eyebrow = panel_title = body = body_bold = small = mono = mono_bold = None
    mono_small = nav = gauge_big = gauge_small = gauge_label = icon = button = version_big = None


def _family(root, *choices):
    available = set(tkfont.families(root))
    return next((c for c in choices if c in available), "Segoe UI")


def init_theme(root: tk.Tk) -> float:
    global SCALE
    SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)

    condensed = _family(root, "Bahnschrift Condensed", "Bahnschrift", "Segoe UI")
    ui = _family(root, "Segoe UI")
    ui_semi = _family(root, "Segoe UI Semibold", "Segoe UI")
    mono = _family(root, "Cascadia Mono", "Consolas", "Courier New")
    symbols = _family(root, "Segoe UI Symbol", "Segoe UI")

    Fonts.page_title = (condensed, 30, "bold italic")
    Fonts.panel_title = (condensed, 17, "bold italic")
    Fonts.eyebrow = (condensed, 10, "bold")
    Fonts.version_big = (condensed, 26, "bold italic")
    Fonts.gauge_big = (condensed, 26, "bold italic")
    Fonts.gauge_small = (condensed, 15, "bold italic")
    Fonts.gauge_label = (condensed, 10, "bold")
    Fonts.body = (ui, 10)
    Fonts.small = (ui, 9)
    Fonts.body_bold = (ui_semi, 10)
    Fonts.nav = (ui_semi, 10)
    Fonts.button = (ui_semi, 10)
    Fonts.mono = (mono, 10)
    Fonts.mono_bold = (mono, 10, "bold")
    Fonts.mono_small = (mono, 8)
    Fonts.icon = (symbols, 11)

    root.configure(bg=BG)
    root.option_add("*Font", Fonts.body)
    root.option_add("*Background", BG)
    root.option_add("*Foreground", TEXT)
    root.option_add("*selectBackground", RED)
    root.option_add("*selectForeground", TEXT)
    # Dropdown lists of comboboxes
    root.option_add("*TCombobox*Listbox.background", PANEL_HI)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", RED)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", Fonts.body)
    root.option_add("*Menu.background", PANEL_HI)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", RED)
    root.option_add("*Menu.activeForeground", TEXT)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=PANEL, font=Fonts.body,
                    insertcolor=TEXT, selectbackground=RED, selectforeground=TEXT)
    style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, padding=px(6))
    style.map("TEntry", bordercolor=[("focus", RED)], lightcolor=[("focus", RED)])
    style.configure("TCombobox", fieldbackground=FIELD, background=PANEL_HI, foreground=TEXT,
                    arrowcolor=MUTED, padding=px(5))
    style.map("TCombobox",
              background=[("disabled", PANEL), ("active", BORDER), ("pressed", BORDER), ("!disabled", PANEL_HI)],
              fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
              foreground=[("disabled", DIM)],
              bordercolor=[("focus", RED)], arrowcolor=[("disabled", DIM)],
              selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])
    style.configure("TCheckbutton", background=PANEL, foreground=TEXT, indicatorbackground=FIELD,
                    indicatorforeground=TEXT, focuscolor=PANEL)
    style.map("TCheckbutton", indicatorbackground=[("selected", RED), ("disabled", PANEL)],
              background=[("active", PANEL)], foreground=[("disabled", DIM)])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=PANEL_HI, troughcolor=PANEL, bordercolor=PANEL,
                        arrowcolor=MUTED, lightcolor=PANEL_HI, darkcolor=PANEL_HI, gripcount=0,
                        arrowsize=px(12))
        style.map(f"{orient}.TScrollbar", background=[("active", BORDER)])
    return SCALE
