"""Reusable pieces for the dark BASELINE look: slanted buttons, gauges, badges, panels..."""
from __future__ import annotations

import math
import threading
import tkinter as tk
import tkinter.font as tkfont
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from .. import thumbnails
from . import theme
from .theme import (AMBER, BG, BORDER, DIM, FIELD, MUTED, PANEL, PANEL_HI, RED, RED_HI, STATUS_COLORS,
                    TEXT, TRACK, Fonts, px)


# --- Small helpers ----------------------------------------------------------

def label(parent, text="", font=None, fg=TEXT, bg=None, **kw):
    font = font or Fonts.body
    if "italic" in str(font[-1]) and "padx" not in kw:
        kw["padx"] = px(5)  # italic letters lean past their box; without padding the last one is clipped
    return tk.Label(parent, text=text, font=font, fg=fg, bg=bg if bg is not None else parent["bg"], **kw)


def spaced(text: str) -> str:
    """Letter-spaced capitals for small labels ('PIPELINE STEPS' -> 'P I P E ...' is too much;
    a thin space between letters reads like tracking)."""
    return " ".join(text.upper())


def eyebrow(parent, text, fg=MUTED, bg=None):
    return label(parent, spaced(text), Fonts.eyebrow, fg, bg)


def separator(parent, color=BORDER, orient="h"):
    if orient == "h":
        return tk.Frame(parent, bg=color, height=1)
    return tk.Frame(parent, bg=color, width=1)


def relative_time(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    seconds = (datetime.now() - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    if seconds < 172800:
        return "yesterday"
    if seconds < 7 * 86400:
        return then.strftime("%a")
    return then.strftime("%d %b")


def initials(name: str) -> str:
    return (name[:2] or "?").upper()


def bind_all_children(widget, event, handler):
    widget.bind(event, handler, add="+")
    for child in widget.winfo_children():
        bind_all_children(child, event, handler)


def set_bg(widget, color):
    """Recolour a row and its plain children (for hover/selection)."""
    try:
        widget.configure(bg=color)
    except tk.TclError:
        return
    for child in widget.winfo_children():
        if isinstance(child, (tk.Frame, tk.Label)) and not getattr(child, "keep_bg", False):
            set_bg(child, color)
        elif isinstance(child, tk.Canvas) and not getattr(child, "keep_bg", False):
            child.configure(bg=color)


# --- Buttons ------------------------------------------------------------------

class SlantButton(tk.Canvas):
    """A parallelogram button like the design's red 'Launch DCC'.
    kind: 'primary' (red), 'secondary' (dark), 'ghost' (text only)."""

    COLORS = {
        "primary": (RED, RED_HI, TEXT),
        "secondary": ("#202024", "#2b2b30", TEXT),
        "ghost": (None, PANEL_HI, MUTED),
    }

    def __init__(self, parent, text, command=None, kind="secondary", icon="", width=None,
                 height=36, font=None, tip=""):
        self.bg_parent = parent["bg"]
        self.font = font or Fonts.button
        self.icon = icon
        self.text = text
        self.kind = kind
        self.command = command
        self.enabled = True
        self.hover = False
        f = tkfont.Font(font=self.font)
        self._h = px(height)
        self._skew = self._h * 0.28
        label_w = f.measure((icon + "  " if icon else "") + text)
        self._fixed_w = px(width) if width else None
        w = self._fixed_w or int(label_w + px(34) + self._skew)
        super().__init__(parent, width=w, height=self._h, bg=self.bg_parent, highlightthickness=0,
                         bd=0, cursor="hand2")
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", lambda e: self._draw())
        if tip:
            Tooltip(self, tip)
        self._draw()

    def _set_hover(self, value):
        self.hover = value
        self._draw()

    def _click(self, event):
        if self.enabled and self.command and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_text(self, text):
        self.text = text
        if not self._fixed_w:
            f = tkfont.Font(font=self.font)
            self.configure(width=int(f.measure((self.icon + "  " if self.icon else "") + text)
                                     + px(34) + self._skew))
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h, s = int(self["width"]), self._h, self._skew
        if self.winfo_width() > 1 and self._fixed_w is None and self.winfo_width() > w:
            w = self.winfo_width()
        base, hover, fg = self.COLORS[self.kind]
        fill = hover if (self.hover and self.enabled) else base
        if not self.enabled:
            fill = "#1a1a1d" if base else None
            fg = DIM
        if fill:
            self.create_polygon(s, 0, w, 0, w - s, h, 0, h, fill=fill, outline="")
        text = (self.icon + "  " if self.icon else "") + self.text
        self.create_text(w / 2, h / 2, text=text, fill=fg, font=self.font)


class Chip(tk.Label):
    """A small toggle filter, e.g. 'All' / 'Model' / 'Mine'."""

    def __init__(self, parent, text, command):
        super().__init__(parent, text=text, font=Fonts.body, padx=px(12), pady=px(5), cursor="hand2",
                         bd=0, highlightthickness=1)
        self.command = command
        self.bind("<Button-1>", lambda e: command())
        self.set_active(False)

    def set_active(self, active):
        self.configure(bg=RED if active else PANEL, fg=TEXT if active else MUTED,
                       highlightbackground=RED if active else BORDER, highlightcolor=RED)


class CheckBox(tk.Frame):
    """The design's checkbox: a red square with a white tick when on."""

    def __init__(self, parent, variable, text="", detail="", enabled=True, command=None):
        super().__init__(parent, bg=parent["bg"], cursor="hand2" if enabled else "arrow")
        self.variable, self.enabled, self.command = variable, enabled, command
        size = px(18)
        self.box = tk.Canvas(self, width=size, height=size, bg=self["bg"], highlightthickness=0, bd=0)
        self.box.keep_bg = True
        self.box.pack(side="left", anchor="n", pady=(px(2), 0))
        text_frame = tk.Frame(self, bg=self["bg"])
        text_frame.pack(side="left", padx=(px(10), 0))
        self.text_label = label(text_frame, text, Fonts.body, TEXT if enabled else MUTED)
        self.text_label.pack(anchor="w")
        self.detail_label = label(text_frame, detail, Fonts.mono_small, DIM)
        if detail:
            self.detail_label.pack(anchor="w")
        for widget in (self, self.box, text_frame, self.text_label, self.detail_label):
            widget.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def set_detail(self, text):
        self.detail_label.configure(text=text)
        if text and not self.detail_label.winfo_ismapped():
            self.detail_label.pack(anchor="w")

    def set_text(self, text):
        self.text_label.configure(text=text)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.text_label.configure(fg=TEXT if enabled else MUTED)
        self._draw()

    def _toggle(self, _event=None):
        if self.enabled:
            self.variable.set(not self.variable.get())
            if self.command:
                self.command()

    def _draw(self):
        c, s = self.box, px(18)
        c.delete("all")
        on = bool(self.variable.get())
        fill = (RED if self.enabled else "#5a1418") if on else FIELD
        c.create_rectangle(1, 1, s - 1, s - 1, fill=fill, outline=RED if on else "#4a4a50")
        if on:
            c.create_line(s * 0.25, s * 0.52, s * 0.43, s * 0.7, s * 0.76, s * 0.3, fill=TEXT,
                          width=max(2, px(2)), capstyle="round", joinstyle="round")


class Badge(tk.Label):
    """Status pill: '■ In progress' in the status colour."""

    def __init__(self, parent, status="Not started", command=None):
        super().__init__(parent, font=Fonts.small, padx=px(7), pady=px(1), bd=0)
        self.keep_bg = True
        self.set_status(status)
        if command:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda e: command(e))

    def set_status(self, status):
        fg, bg, _ = STATUS_COLORS.get(status, STATUS_COLORS["Not started"])
        self.configure(text=f"■ {status}", fg=fg, bg=bg)


class Avatar(tk.Canvas):
    def __init__(self, parent, name, size=32, fill="#26262b"):
        s = px(size)
        super().__init__(parent, width=s, height=s, bg=parent["bg"], highlightthickness=0)
        self.keep_bg = False
        self.create_oval(1, 1, s - 1, s - 1, fill=fill, outline=BORDER)
        self.create_text(s / 2, s / 2, text=initials(name), fill=TEXT,
                         font=(Fonts.body_bold[0], max(7, int(size / 3.6)), "bold"))


# --- Panels -------------------------------------------------------------------

class Panel(tk.Frame):
    """A dark bordered box with an italic title row, like 'MY TASKS'."""

    def __init__(self, parent, title="", right=None, pad=18):
        super().__init__(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        self.pad = pad
        if title:
            head = tk.Frame(self, bg=PANEL)
            head.pack(fill="x", padx=px(pad), pady=(px(14), px(12)))
            label(head, title.upper(), Fonts.panel_title).pack(side="left")
            self.head_right = tk.Frame(head, bg=PANEL)
            self.head_right.pack(side="right")
            if right:
                right(self.head_right)
            separator(self).pack(fill="x")
        self.body = tk.Frame(self, bg=PANEL)
        self.body.pack(fill="both", expand=True)


class ScrollFrame(tk.Frame):
    """A vertically scrolling area. Put content in `.inner`."""

    def __init__(self, parent, bg=BG, horizontal=False):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.bind("<Enter>", lambda e: self._wheel(True))
        self.bind("<Leave>", lambda e: self._wheel(False))

    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._toggle_bar()

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        self._toggle_bar()

    def _toggle_bar(self):
        needed = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1
        if needed and not self.vbar.winfo_ismapped():
            self.vbar.pack(side="right", fill="y")
        elif not needed and self.vbar.winfo_ismapped():
            self.vbar.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, on):
        if on:
            self.bind_all("<MouseWheel>", self._scroll)
        else:
            self.unbind_all("<MouseWheel>")

    def _scroll(self, event):
        if self.vbar.winfo_ismapped():
            self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")

    def scroll_top(self):
        self.canvas.yview_moveto(0)


class _ThumbnailLoader:
    """Hands thumbnail pictures to widgets. Resizing happens in the background, a batch at a
    time (one PowerShell call per batch), so a page full of cards doesn't freeze the window."""

    MAX_PHOTOS = 150

    def __init__(self):
        self.pending = {}            # (source, w, h) -> [callback]
        self.photos = OrderedDict()  # resized file -> PhotoImage, so a picture is only read once
        self.busy = False
        self.result = None
        self.root = None

    def request(self, widget, source, width, height, callback):
        cached = thumbnails.cache_path(source, width, height)
        photo = self._photo(cached) if cached.is_file() else None
        if photo:
            callback(photo)
            return
        self.pending.setdefault((source, width, height), []).append(callback)
        self.root = widget._root()
        if not self.busy:
            self.busy = True
            self.root.after(50, self._start)

    def _photo(self, path):
        if path in self.photos:
            self.photos.move_to_end(path)
            return self.photos[path]
        try:
            photo = tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None
        self.photos[path] = photo
        while len(self.photos) > self.MAX_PHOTOS:
            self.photos.popitem(last=False)
        return photo

    def _start(self):
        batch, self.pending = self.pending, {}
        self.result = None

        def work():
            try:
                self.result = (batch, thumbnails.make_cached(list(batch)))
            except Exception:  # never leave the loader stuck
                self.result = (batch, {})

        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, self._poll)

    def _poll(self):
        if self.result is None:
            self.root.after(100, self._poll)
            return
        batch, made = self.result
        for (source, w, h), callbacks in batch.items():
            photo = self._photo(made[(source, w, h)]) if made.get((source, w, h)) else self._fallback(source, w, h)
            if photo:
                for callback in callbacks:
                    callback(photo)
        if self.pending:
            self.root.after(50, self._start)
        else:
            self.busy = False

    def _fallback(self, source, w, h):
        """If Windows couldn't resize it, Tk can still show a PNG/GIF, shrunk by a whole number."""
        if source.suffix.lower() not in (".png", ".gif"):
            return None
        try:
            photo = tk.PhotoImage(file=str(source))
        except tk.TclError:
            return None
        factor = max(1, min(photo.width() // max(w, 1), photo.height() // max(h, 1)))
        return photo.subsample(factor) if factor > 1 else photo


_THUMBNAILS = _ThumbnailLoader()


class Stripes(tk.Canvas):
    """The striped placeholder where a thumbnail would go, with a small caption. Given an
    `image` (an asset's thumbnail picture) it shows that instead, cropped to fill.
    `link` is an optional (text, command) drawn as a small clickable word in the corner."""

    def __init__(self, parent, width=200, height=110, caption="", bg="#19191c", image=None, link=None):
        super().__init__(parent, width=px(width), height=px(height), bg=bg, highlightthickness=0, bd=0)
        self.keep_bg = True
        self.caption = caption
        self.link = link
        self.image_path = None
        self._photo = None
        self._asked = None
        self._size_job = None
        self.bind("<Configure>", lambda e: self._resized())
        self.set_image(image)

    def set_caption(self, caption):
        self.caption = caption
        self._draw()

    def set_image(self, path):
        self.image_path = Path(path) if path else None
        self._photo = self._asked = None
        self._draw()
        self._resized()

    def _size(self):
        return max(self.winfo_width(), int(self["width"])), max(self.winfo_height(), int(self["height"]))

    def _resized(self):
        self._draw()
        if self.image_path:
            # Wait for the size to settle (cards resize a few times while the grid lays out).
            if self._size_job:
                self.after_cancel(self._size_job)
            self._size_job = self.after(150, self._ask_for_photo)

    def _ask_for_photo(self):
        self._size_job = None
        try:
            size = self._size()
        except tk.TclError:  # destroyed meanwhile
            return
        if not self.image_path or self._asked == (self.image_path, size):
            return
        self._asked = (self.image_path, size)
        source = self.image_path

        def got(photo):
            try:
                alive = self.winfo_exists()
            except tk.TclError:
                alive = False
            if alive and self.image_path == source:
                self._photo = photo
                self._draw()

        _THUMBNAILS.request(self, source, size[0], size[1], got)

    def _draw(self):
        self.delete("all")
        w, h = self._size()
        if self._photo:
            self.create_image(w // 2, h // 2, image=self._photo)
        else:
            step = px(18)
            for x in range(-h, w + h, step):
                self.create_line(x, h, x + h, 0, fill="#202024", width=px(7))
        if self.caption:
            self._backed(self.create_text(px(8), h - px(8), text=self.caption, anchor="sw",
                                          fill=MUTED if self._photo else DIM, font=Fonts.mono_small))
        if self.link:
            text, command = self.link
            item = self.create_text(w - px(8), px(7), text=text, anchor="ne", fill=MUTED,
                                    font=Fonts.mono_small, tags=("link",))
            self._backed(item, tags=("link",))
            self.tag_bind("link", "<Button-1>", lambda e: command())
            self.tag_bind("link", "<Enter>", lambda e: (self.itemconfigure(item, fill=TEXT),
                                                        self.configure(cursor="hand2")))
            self.tag_bind("link", "<Leave>", lambda e: (self.itemconfigure(item, fill=MUTED),
                                                        self.configure(cursor="")))

    def _backed(self, item, tags=()):
        """Over a picture, put a dark box behind the text so it stays readable."""
        if not self._photo:
            return
        x1, y1, x2, y2 = self.bbox(item)
        box = self.create_rectangle(x1 - px(4), y1 - px(2), x2 + px(4), y2 + px(2), fill=BG, outline="",
                                    tags=tags)
        self.tag_lower(box, item)


class Gauge(tk.Canvas):
    """The speedometer ring from the design: red ticks up to the percentage."""

    def __init__(self, parent, size=86, ticks=16, big=False, caption=""):
        self.size = px(size)
        self.ticks = ticks
        self.big = big
        self.caption = caption
        extra = 0 if big else px(22)
        super().__init__(parent, width=self.size, height=self.size + extra, bg=parent["bg"],
                         highlightthickness=0)
        self.value = 0.0

    def set(self, value: float):
        self.value = max(0.0, min(1.0, value))
        self._draw()

    def _draw(self):
        self.delete("all")
        s = self.size
        cx, cy = s / 2, s / 2
        r_out, r_in = s * 0.47, s * (0.33 if self.big else 0.35)
        start, sweep = 215.0, 250.0  # degrees, clockwise from the lower left
        lit = round(self.value * self.ticks)
        gap = 3.2
        seg = sweep / self.ticks
        for i in range(self.ticks):
            a0 = math.radians(start - i * seg - gap / 2)
            a1 = math.radians(start - (i + 1) * seg + gap / 2)
            pts = [cx + r_out * math.cos(a0), cy - r_out * math.sin(a0),
                   cx + r_out * math.cos(a1), cy - r_out * math.sin(a1),
                   cx + r_in * math.cos(a1), cy - r_in * math.sin(a1),
                   cx + r_in * math.cos(a0), cy - r_in * math.sin(a0)]
            self.create_polygon(pts, fill=RED if i < lit else TRACK, outline="")
        pct = int(round(self.value * 100))
        font = Fonts.gauge_big if self.big else Fonts.gauge_small
        self.create_text(cx - px(3), cy, text=str(pct), fill=TEXT, font=font, anchor="center")
        small = (font[0], max(7, int(font[1] * 0.5)), "bold italic")
        num_w = tkfont.Font(font=font).measure(str(pct))
        self.create_text(cx - px(3) + num_w / 2 + px(1), cy + px(4 if self.big else 3), text="%", fill=MUTED,
                         font=small, anchor="w")
        if self.caption:
            self.create_text(cx, s + px(10), text=spaced(self.caption), fill=MUTED, font=Fonts.gauge_label)


class TimeGlyph(tk.Canvas):
    """The little arch in front of the dashboard greeting: four strokes, and the red one moves
    left to right through the day (morning, afternoon, evening, night)."""

    # (x0, y0, x1, y1) of each stroke, in a 26 x 22 box
    STROKES = ((3, 21, 3, 10), (5, 7, 11.5, 2.5), (14.5, 2.5, 21, 7), (23, 10, 23, 21))
    OFF = "#3a3a40"

    def __init__(self, parent):
        super().__init__(parent, width=px(26), height=px(22), bg=parent["bg"], highlightthickness=0, bd=0)
        self.set(0)

    def set(self, slot: int):
        self.delete("all")
        for i, (x0, y0, x1, y1) in enumerate(self.STROKES):
            self.create_line(px(x0), px(y0), px(x1), px(y1), width=px(3.5), capstyle="butt",
                             fill=RED if i == slot else self.OFF)


class Tag(tk.Canvas):
    """A red slanted tag with bold italic text, e.g. the '12 DAYS' countdown on the dashboard."""

    def __init__(self, parent, height=34):
        self._h = px(height)
        self.font = (Fonts.panel_title[0], 16, "bold italic")
        super().__init__(parent, width=1, height=self._h, bg=parent["bg"], highlightthickness=0, bd=0)

    def set(self, text: str):
        self.delete("all")
        h, skew = self._h, self._h * 0.28
        w = int(tkfont.Font(font=self.font).measure(text) + px(22) + skew)
        self.configure(width=w)
        self.create_polygon(skew, 0, w, 0, w - skew, h, 0, h, fill=RED, outline="")
        self.create_text(w / 2, h / 2, text=text, fill=TEXT, font=self.font)


class ProgressBar(tk.Canvas):
    def __init__(self, parent, height=5):
        super().__init__(parent, height=px(height), bg=parent["bg"], highlightthickness=0, bd=0)
        self.keep_bg = True
        self.value, self.done = 0.0, False
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, value, done=False, failed=False):
        self.value, self.done, self.failed = max(0.0, min(1.0, value)), done, failed
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self["height"])
        self.create_rectangle(0, 0, w, h, fill=TRACK, outline="")
        color = TEXT if self.done else (DIM if getattr(self, "failed", False) else RED)
        self.create_rectangle(0, 0, w * self.value, h, fill=color, outline="")


class Tooltip:
    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._after = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + px(8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(4)
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self.text, bg="#26262b", fg=TEXT, font=Fonts.small, justify="left",
                 wraplength=px(300), padx=px(8), pady=px(5), highlightthickness=1,
                 highlightbackground=BORDER).pack()

    def _hide(self, _e=None):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip:
            self._tip.destroy()
            self._tip = None


def status_menu(parent, statuses, on_pick):
    menu = tk.Menu(parent, tearoff=0, font=Fonts.body, bd=0)
    for status in statuses:
        fg = STATUS_COLORS[status][0]
        menu.add_command(label=f"■  {status}", foreground=fg, command=lambda s=status: on_pick(s))
    return menu


# --- Dialogs ------------------------------------------------------------------

class FormDialog(tk.Toplevel):
    """A small dark modal form. `fields` is [(key, label, default, choices or None)].
    After it closes, `result` is a dict of the values, or None if cancelled."""

    def __init__(self, parent, title, fields, intro="", ok_text="OK"):
        super().__init__(parent, bg=PANEL)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.result = None
        self._vars = {}
        body = tk.Frame(self, bg=PANEL, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        label(body, title.upper(), Fonts.panel_title).grid(row=0, column=0, columnspan=2, sticky="w")
        row = 1
        if intro:
            label(body, intro, Fonts.body, MUTED, wraplength=px(400), justify="left").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(px(6), px(4)))
            row += 1
        first = None
        for key, text, default, choices in fields:
            label(body, text, Fonts.body, MUTED).grid(row=row, column=0, sticky="w", padx=(0, px(14)),
                                                       pady=px(5))
            var = tk.StringVar(value=default)
            if choices:
                widget = ttk.Combobox(body, textvariable=var, values=choices, state="readonly", width=34)
            else:
                widget = ttk.Entry(body, textvariable=var, width=36)
            widget.grid(row=row, column=1, sticky="ew", pady=px(5))
            first = first or widget
            self._vars[key] = var
            row += 1
        buttons = tk.Frame(body, bg=PANEL)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(px(16), 0))
        SlantButton(buttons, ok_text, self._ok, kind="primary").pack(side="right")
        SlantButton(buttons, "Cancel", self.destroy, kind="ghost").pack(side="right", padx=(0, px(6)))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_width()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        if first is not None:
            first.focus_set()
        self.grab_set()
        self.wait_window(self)

    def _ok(self):
        self.result = {key: var.get().strip() for key, var in self._vars.items()}
        self.destroy()


class LogView(tk.Frame):
    """Read-only scrolling text for render logs."""

    def __init__(self, parent, height=12):
        super().__init__(parent, bg=FIELD)
        self.text = tk.Text(self, height=height, wrap="none", font=Fonts.mono_small, bg=FIELD, fg="#c9c9cf",
                            insertbackground=TEXT, state="disabled", bd=0, highlightthickness=0,
                            padx=px(10), pady=px(8))
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=yscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.text.tag_configure("marker", foreground=MUTED)
        self.text.tag_configure("error", foreground="#ff6b6b")
        self.text.tag_configure("warn", foreground=AMBER)

    def _insert(self, lines):
        for i, line in enumerate(lines):
            tag = ("marker" if line.startswith("---") else "error" if line.startswith("ERROR")
                   else "warn" if line.startswith("WARNING") else "")
            self.text.insert("end", ("\n" if (i or self.text.index("end-1c") != "1.0") else "") + line, tag)

    def set_lines(self, lines):
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._insert(lines)
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def append_lines(self, lines):
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self._insert(lines)
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")
