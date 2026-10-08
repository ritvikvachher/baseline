"""Common layout for the pages: the eyebrow + big italic title header."""
from __future__ import annotations

import tkinter as tk

from .theme import BG, MUTED, Fonts, px
from .widgets import ScrollFrame, eyebrow, label

PAD = 26


class Page(tk.Frame):
    nav_title = ""

    def __init__(self, parent, app, scroll=False):
        super().__init__(parent, bg=BG)
        self.app = app
        if scroll:
            self.scroller = ScrollFrame(self)
            self.scroller.pack(fill="both", expand=True)
            outer = self.scroller.inner
        else:
            self.scroller = None
            outer = self
        self.content = tk.Frame(outer, bg=BG)
        self.content.pack(fill="both", expand=True, padx=px(PAD), pady=(px(22), px(18)))

    def header(self, small, title):
        """Returns the frame on the right of the header, for buttons."""
        head = tk.Frame(self.content, bg=BG)
        head.pack(fill="x", pady=(0, px(16)))
        left = tk.Frame(head, bg=BG)
        left.pack(side="left")
        eyebrow(left, small, MUTED, BG).pack(anchor="w")
        self.title_label = label(left, title.upper(), Fonts.page_title, bg=BG)
        self.title_label.pack(anchor="w")
        right = tk.Frame(head, bg=BG)
        right.pack(side="right", anchor="s")
        return right

    def refresh(self):
        pass

    def breadcrumb(self):
        return [self.nav_title]
