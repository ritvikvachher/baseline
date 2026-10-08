"""Dark dialogs that need more than a simple form."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from .theme import BORDER, MUTED, PANEL, PANEL_HI, RED, TEXT, Fonts, px
from .widgets import SlantButton, label, set_bg


class SoftwareDialog(tk.Toplevel):
    """Pick one installed version of Houdini/Maya/... `result` is the Installation or None."""

    def __init__(self, app, title, intro, ok_text="Launch"):
        super().__init__(app, bg=PANEL)
        self.app = app
        self.title(title)
        self.transient(app)
        self.resizable(False, False)
        self.result = None
        installs = app.all_installations()
        if not installs:
            self.destroy()
            messagebox.showerror(title, "No supported software (Houdini, Maya, Nuke...) was found on this computer.\n"
                                 "Add its location in Settings > Software.", parent=app)
            return

        body = tk.Frame(self, bg=PANEL, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        label(body, title.upper(), Fonts.panel_title).pack(anchor="w")
        label(body, intro, Fonts.body, MUTED).pack(anchor="w", pady=(px(4), px(12)))

        self._rows = []
        defaults = {inst.app: app.default_installation(inst.app) for inst in installs}
        chosen = defaults.get("houdini") or installs[0]
        for inst in installs:
            row = tk.Frame(body, bg=PANEL, cursor="hand2", highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill="x", pady=px(3))
            label(row, inst.label, Fonts.body_bold, width=17, anchor="w").pack(side="left", padx=(px(12), 0),
                                                                              pady=px(9))
            label(row, inst.version, Fonts.mono, width=12, anchor="w").pack(side="left")
            if defaults.get(inst.app) == inst:
                label(row, "your default", Fonts.small, MUTED).pack(side="left")
            label(row, str(inst.install_dir), Fonts.small, MUTED).pack(side="right", padx=px(12))
            for widget in [row] + row.winfo_children():
                widget.bind("<Button-1>", lambda e, i=inst: self._pick(i))
                widget.bind("<Double-1>", lambda e, i=inst: (self._pick(i), self._ok()))
            self._rows.append((inst, row))
        self._pick(chosen)

        buttons = tk.Frame(body, bg=PANEL)
        buttons.pack(fill="x", pady=(px(16), 0))
        SlantButton(buttons, ok_text, self._ok, kind="primary", icon="↗").pack(side="right")
        SlantButton(buttons, "Cancel", self.destroy, kind="ghost").pack(side="right", padx=(0, px(6)))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.wait_window(self)

    def _pick(self, inst):
        self._chosen = inst
        for other, row in self._rows:
            selected = other == inst
            set_bg(row, PANEL_HI if selected else PANEL)
            row.configure(highlightbackground=RED if selected else BORDER)

    def _ok(self):
        self.result = self._chosen
        self.destroy()
