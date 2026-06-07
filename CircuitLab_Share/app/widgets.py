import math
import re
import tkinter as tk
from tkinter import ttk

from . import theme


def font(size=10, weight="normal"):
    return (theme.FONT, size, weight)


def mono(size=10, weight="normal"):
    return (theme.MONO, size, weight)


_ENGINEERING_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\s]*)\s*$"
)


def parse_engineering_value(text, default=None):
    raw = "" if text is None else str(text).strip()
    if not raw:
        return default
    match = _ENGINEERING_RE.match(raw.replace("Ω", "ohm").replace("µ", "u"))
    if not match:
        return default
    try:
        value = float(match.group(1))
    except ValueError:
        return default
    suffix = match.group(2).strip()
    lower = suffix.lower()
    if lower.startswith("ohm"):
        multiplier = 1.0
    elif lower.startswith("meg"):
        multiplier = 1e6
    elif suffix.startswith("M"):
        multiplier = 1e6
    elif suffix.startswith(("G", "g")):
        multiplier = 1e9
    elif suffix.startswith(("K", "k")):
        multiplier = 1e3
    elif suffix.startswith("m"):
        multiplier = 1e-3
    elif suffix.startswith(("u", "U")):
        multiplier = 1e-6
    elif suffix.startswith(("n", "N")):
        multiplier = 1e-9
    elif suffix.startswith(("p", "P")):
        multiplier = 1e-12
    else:
        multiplier = 1.0
    parsed = value * multiplier
    return parsed if math.isfinite(parsed) else default


def format_engineering_value(value, digits=None):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return str(value)
    if value == 0:
        return "0"
    abs_value = abs(value)
    if digits is not None:
        fixed = f"{value:.{digits}f}"
        reparsed = parse_engineering_value(fixed, None)
        if reparsed is not None and abs(reparsed - value) <= max(abs_value * 0.01, 1e-18):
            return fixed
    return f"{value:.8g}"


class Panel(tk.Frame):
    def __init__(self, parent, bg=theme.PANEL, **kwargs):
        super().__init__(
            parent,
            bg=bg,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.BORDER,
            highlightthickness=1,
            bd=0,
            **kwargs,
        )


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, on_scroll=None, **kwargs):
        super().__init__(parent, bg=theme.BG, **kwargs)
        self.on_scroll = on_scroll
        self.canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self._on_scrollbar)
        self.inner = tk.Frame(self.canvas, bg=theme.BG)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._set_yview)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._notify_scroll()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self._notify_scroll()

    def _set_yview(self, first, last):
        self.vbar.set(first, last)
        self._notify_scroll(first)

    def _on_scrollbar(self, *args):
        self.canvas.yview(*args)
        self._notify_scroll()

    def _notify_scroll(self, first=None):
        if not self.on_scroll:
            return
        if first is None:
            first = self.canvas.yview()[0]
        try:
            self.on_scroll(float(first))
        except (TypeError, ValueError, tk.TclError):
            pass

    def set_on_scroll(self, callback):
        self.on_scroll = callback
        self._notify_scroll()

    def _bind_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_wheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._notify_scroll()

    def scroll_to_top(self):
        self.canvas.yview_moveto(0)
        self._notify_scroll(0)


class NumberField(tk.Frame):
    def __init__(
        self,
        parent,
        label,
        value,
        on_change=None,
        width=12,
        min_value=None,
        max_value=None,
        digits=None,
        **kwargs,
    ):
        super().__init__(parent, bg=kwargs.pop("bg", theme.PANEL))
        self.on_change = on_change
        self.min_value = min_value
        self.max_value = max_value
        self.digits = digits
        self._suspend_trace = False
        self._normal_bg = theme.GRAPH_BG
        self._last_valid_value = parse_engineering_value(value, value)
        self.var = tk.StringVar(value=str(value))
        tk.Label(self, text=label, bg=self["bg"], fg=theme.MUTED_2, font=font(8, "bold"), anchor="w").pack(
            fill="x", pady=(0, 3)
        )
        self.entry = tk.Entry(
            self,
            textvariable=self.var,
            bg=self._normal_bg,
            fg=theme.TEXT_2,
            insertbackground=theme.TEXT_2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.BLUE,
            font=font(10),
            width=width,
        )
        self.entry.pack(fill="x", ipady=4)
        self.entry.bind("<Return>", self._commit_from_key)
        self.entry.bind("<KP_Enter>", self._commit_from_key)
        self.entry.bind("<Escape>", self._revert_from_key)
        self.entry.bind("<FocusOut>", self._clamp_and_notify)
        self.var.trace_add("write", self._trace)

    def _trace(self, *_args):
        if self._suspend_trace:
            return
        if self.on_change:
            self.after_idle(self.on_change)

    def _notify(self, _event=None):
        if self.on_change:
            self.on_change()

    def _flash(self, color):
        try:
            self.entry.configure(bg=color)
            self.after(180, lambda: self.entry.configure(bg=self._normal_bg))
        except tk.TclError:
            pass

    def _mark_invalid(self):
        try:
            self.entry.configure(bg=theme.DANGER)
            self.entry.selection_range(0, "end")
            self.entry.bell()
        except tk.TclError:
            pass

    def _commit_value(self, notify=True):
        value = self.get_float(default=None)
        if value is None or not math.isfinite(value):
            self._mark_invalid()
            return False
        if self.min_value is not None:
            value = max(self.min_value, value)
        if self.max_value is not None:
            value = min(self.max_value, value)
        self.set_value(value, silent=not notify)
        self._last_valid_value = value
        self._flash(theme.GOOD)
        if notify:
            self._notify()
        return True

    def _commit_from_key(self, _event=None):
        if self._commit_value(notify=True):
            self.entry.selection_clear()
            parent = self.master
            while parent is not None:
                if isinstance(parent, tk.Canvas):
                    parent.focus_set()
                    break
                parent = getattr(parent, "master", None)
            else:
                try:
                    self.master.focus_set()
                except tk.TclError:
                    pass
        return "break"

    def _revert_from_key(self, _event=None):
        self.set_value(self._last_valid_value, silent=True)
        self.entry.selection_clear()
        self.entry.configure(bg=self._normal_bg)
        return "break"

    def _clamp_and_notify(self, _event=None):
        self._commit_value(notify=True)

    def get_float(self, default=math.nan, positive=False):
        value = parse_engineering_value(self.var.get(), None)
        if value is None:
            return default
        if positive and not (value > 0):
            return default
        return value if math.isfinite(value) else default

    def set_value(self, value, silent=False):
        self._suspend_trace = silent
        try:
            self.var.set(format_engineering_value(value, self.digits))
            parsed = parse_engineering_value(self.var.get(), None)
            if isinstance(value, (int, float)) and math.isfinite(value) and parsed is not None:
                if abs(parsed - value) > max(abs(value) * 0.01, 1e-18):
                    self.var.set(f"{value:.8g}")
            if isinstance(value, (int, float)) and math.isfinite(value):
                self._last_valid_value = float(value)
        finally:
            self._suspend_trace = False


class MetricBox(tk.Frame):
    def __init__(self, parent, label, value="-", bg=theme.PANEL_2):
        super().__init__(
            parent,
            bg=bg,
            highlightbackground=theme.BORDER,
            highlightthickness=1,
            bd=0,
        )
        self.small = tk.Label(self, text=label, bg=bg, fg=theme.MUTED_2, font=font(8), anchor="w")
        self.small.pack(fill="x", padx=10, pady=(8, 1))
        self.value = tk.Label(self, text=value, bg=bg, fg=theme.TEXT_2, font=font(11, "bold"), anchor="w")
        self.value.configure(justify="left", wraplength=420)
        self.value.pack(fill="x", padx=10, pady=(0, 9))

    def set(self, value, color=None):
        self.value.configure(text=value, fg=color or theme.TEXT_2)


class CompactMetricBox(tk.Frame):
    def __init__(
        self,
        parent,
        label,
        value="-",
        bg=theme.PANEL_2,
        wraplength=None,
        value_size=9,
        min_height=None,
        value_anchor="w",
        value_justify="left",
        value_pady=(0, 4),
    ):
        super().__init__(
            parent,
            bg=bg,
            highlightbackground=theme.BORDER,
            highlightthickness=1,
            bd=0,
        )
        if min_height:
            self.configure(height=min_height)
        self.small = tk.Label(self, text=label, bg=bg, fg=theme.MUTED_2, font=font(7), anchor="w")
        self.small.pack(fill="x", padx=7, pady=(4, 0))
        self._auto_wrap = wraplength == "auto"
        self.value = tk.Label(
            self,
            text=value,
            bg=bg,
            fg=theme.TEXT_2,
            font=font(value_size, "bold"),
            anchor=value_anchor,
        )
        self.value.configure(justify=value_justify)
        if wraplength and not self._auto_wrap:
            self.value.configure(wraplength=wraplength)
        self.value.pack(fill="x", expand=True, padx=7, pady=value_pady)
        if self._auto_wrap:
            self.bind("<Configure>", self._update_wraplength)

    def _update_wraplength(self, event=None):
        width = (event.width if event else self.winfo_width()) - 14
        if width > 40:
            self.value.configure(wraplength=max(120, width))

    def set(self, value, color=None):
        self.value.configure(text=value, fg=color or theme.TEXT_2)


class SectionHeader(tk.Frame):
    def __init__(self, parent, title, desc=None, bg=theme.PANEL, title_size=11, desc_size=8):
        super().__init__(parent, bg=bg)
        tk.Label(self, text=title, bg=bg, fg=theme.TEXT_2, font=font(title_size, "bold"), anchor="w").pack(
            fill="x", padx=10, pady=(8, 2)
        )
        if desc:
            tk.Label(self, text=desc, bg=bg, fg=theme.MUTED_2, font=font(desc_size), anchor="w", justify="left").pack(
                fill="x", padx=10, pady=(0, 6)
            )


class CompactHintBar(tk.Frame):
    def __init__(self, parent, label, value="-", bg=theme.PANEL_2, wraplength=520):
        super().__init__(parent, bg=bg, highlightbackground=theme.BORDER, highlightthickness=1, bd=0)
        self.label = tk.Label(self, text=label, bg=bg, fg=theme.MUTED_2, font=font(7, "bold"), anchor="w")
        self.label.pack(side="left", padx=(8, 5), pady=5)
        self.value = tk.Label(self, text=value, bg=bg, fg=theme.TEXT_2, font=font(9, "bold"), anchor="w", justify="left", wraplength=wraplength)
        self.value.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=5)

    def set(self, value, color=None):
        self.value.configure(text=value, fg=color or theme.TEXT_2)


class CollapsibleSection(tk.Frame):
    def __init__(self, parent, title, initially_open=False, bg=theme.PANEL):
        super().__init__(parent, bg=bg)
        self.open = initially_open
        self.header = make_button(self, "", self.toggle, "secondary")
        self.header.pack(fill="x")
        self.body = tk.Frame(self, bg=bg)
        self.title = title
        self._sync()

    def toggle(self):
        self.open = not self.open
        self._sync()

    def _sync(self):
        self.header.configure(text=("▾ " if self.open else "▸ ") + self.title)
        if self.open:
            self.body.pack(fill="x", pady=(6, 0))
        else:
            self.body.pack_forget()


def make_button(parent, text, command=None, variant="primary"):
    colors = {
        "primary": (theme.SURFACE, theme.TEXT, theme.ACCENT),
        "accent": (theme.ACCENT, theme.BG, theme.ACCENT),
        "secondary": (theme.PANEL_2, theme.TEXT_2, theme.BORDER),
        "danger": (theme.DANGER, theme.BG, theme.DANGER),
    }
    bg, fg, active = colors.get(variant, colors["primary"])
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=theme.BG if variant in ("accent", "danger") else theme.TEXT,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=theme.LINE,
        padx=10,
        pady=5,
        font=font(9, "bold"),
        cursor="hand2",
    )
    return btn


def set_button_variant(button, active):
    if active:
        button.configure(
            bg=theme.ACCENT,
            fg=theme.BG,
            activebackground=theme.ACCENT,
            activeforeground=theme.BG,
            highlightbackground=theme.ACCENT,
            highlightcolor=theme.ACCENT,
        )
    else:
        button.configure(
            bg=theme.PANEL_2,
            fg=theme.TEXT_2,
            activebackground=theme.BORDER,
            activeforeground=theme.TEXT,
            highlightbackground=theme.LINE,
            highlightcolor=theme.LINE,
        )


class ResponsiveTopFrame(tk.Frame):
    """Places diagram and graph side-by-side, then stacks them on narrow widths."""

    def __init__(
        self,
        parent,
        breakpoint=980,
        left_weight=3,
        right_weight=2,
        stretch_y=False,
        left_sticky=None,
        right_sticky=None,
        stacked_sticky=None,
        **kwargs,
    ):
        super().__init__(parent, bg=kwargs.pop("bg", theme.BG), **kwargs)
        self.breakpoint = breakpoint
        self.left_weight = left_weight
        self.right_weight = right_weight
        self.stretch_y = stretch_y
        default_sticky = "nsew" if stretch_y else "new"
        self.left_sticky = left_sticky or default_sticky
        self.right_sticky = right_sticky or default_sticky
        self.stacked_sticky = stacked_sticky or default_sticky
        self.left = None
        self.right = None
        self._stacked = None
        self.bind("<Configure>", self._layout)

    def set_children(self, left, right):
        self.left = left
        self.right = right
        self._stacked = None
        self._layout()

    def _layout(self, _event=None):
        if not self.left or not self.right:
            return
        stacked = self.winfo_width() < self.breakpoint
        if stacked == self._stacked:
            return
        self._stacked = stacked
        self.left.grid_forget()
        self.right.grid_forget()
        for row in range(2):
            self.rowconfigure(row, weight=0)
        for col in range(2):
            self.columnconfigure(col, weight=0)
        if stacked:
            self.columnconfigure(0, weight=1)
            self.rowconfigure(0, weight=0)
            self.rowconfigure(1, weight=0)
            self.left.grid(row=0, column=0, sticky=self.stacked_sticky, padx=0, pady=(0, 12))
            self.right.grid(row=1, column=0, sticky=self.stacked_sticky, padx=0, pady=(0, 0))
        else:
            self.rowconfigure(0, weight=1 if self.stretch_y else 0)
            self.columnconfigure(0, weight=self.left_weight, uniform="top")
            self.columnconfigure(1, weight=self.right_weight, uniform="top")
            self.left.grid(row=0, column=0, sticky=self.left_sticky, padx=(0, 8), pady=0)
            self.right.grid(row=0, column=1, sticky=self.right_sticky, padx=(8, 0), pady=0)


class DualColumnWorkbench(ResponsiveTopFrame):
    """Two-column circuit workbench: left context, right live graph and controls."""

    def __init__(self, parent, breakpoint=1040, left_weight=3, right_weight=2, **kwargs):
        super().__init__(
            parent,
            breakpoint=breakpoint,
            left_weight=left_weight,
            right_weight=right_weight,
            stretch_y=False,
            left_sticky="new",
            right_sticky="new",
            stacked_sticky="ew",
            **kwargs,
        )


class ResponsiveControlGrid(tk.Frame):
    """Responsive form grid used by the bottom control panels."""

    def __init__(self, parent, columns=3, breakpoint=760, **kwargs):
        super().__init__(parent, bg=kwargs.pop("bg", theme.PANEL), **kwargs)
        self.columns = columns
        self.breakpoint = breakpoint
        self.items = []
        self._last_cols = None
        self.bind("<Configure>", self._layout)

    def add(self, widget, span=1):
        self.items.append((widget, max(1, span)))
        self._last_cols = None
        self._layout()
        return widget

    def _layout(self, _event=None):
        cols = 1 if self.winfo_width() and self.winfo_width() < self.breakpoint else self.columns
        if cols == self._last_cols and all(item[0].grid_info() for item in self.items):
            return
        self._last_cols = cols
        for child, _span in self.items:
            child.grid_forget()
        for col in range(self.columns):
            self.columnconfigure(col, weight=1 if col < cols else 0, uniform="controls" if col < cols else "")
        row = 0
        col = 0
        for child, span in self.items:
            use_span = 1 if cols == 1 else min(span, cols)
            if col + use_span > cols:
                row += 1
                col = 0
            child.grid(row=row, column=col, columnspan=use_span, sticky="nsew", padx=5, pady=4)
            col += use_span
            if col >= cols:
                row += 1
                col = 0


class CanvasSlider(tk.Frame):
    def __init__(
        self,
        parent,
        label,
        min_value,
        max_value,
        step,
        value,
        formatter=None,
        on_change=None,
        bg=theme.PANEL,
        labels=None,
        compact=False,
    ):
        super().__init__(parent, bg=bg)
        self.min_value = float(min_value)
        self.max_value = float(max_value)
        self.step = float(step)
        self.value = self.snap(value)
        self.formatter = formatter or (lambda v: f"{v:g}")
        self.on_change = on_change
        self.labels = labels or [str(min_value), str((min_value + max_value) / 2), str(max_value)]
        self.compact = compact
        self.slider_height = 36 if compact else 58
        self.track_y = 14 if compact else 21
        self.tick_y = 29 if compact else 45
        self.thumb_radius = 7 if compact else 9
        label_gap = 2 if compact else 5
        host_pad = 8 if compact else 12
        canvas_top_pad = 3 if compact else 7
        readout_pad_y = 2 if compact else 6
        readout_bottom_gap = 3 if compact else 7
        tk.Label(self, text=label, bg=bg, fg=theme.MUTED_2, font=font(9, "bold"), anchor="w").pack(
            fill="x", pady=(0, label_gap)
        )
        self.host = tk.Frame(
            self,
            bg=theme.GRAPH_BG,
            highlightbackground=theme.BORDER,
            highlightthickness=1,
            bd=0,
        )
        self.host.pack(fill="x")
        self.canvas = tk.Canvas(self.host, height=self.slider_height, bg=theme.GRAPH_BG, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="x", expand=True, padx=host_pad, pady=(canvas_top_pad, 0))
        self.readout = tk.Label(
            self.host,
            text=self.formatter(self.value),
            bg=theme.GRAPH_BG,
            fg=theme.TEXT_2,
            font=mono(8 if compact else 10, "bold"),
            padx=8,
            pady=readout_pad_y,
            anchor="center",
        )
        self.readout.pack(fill="x", padx=host_pad, pady=(0, readout_bottom_gap))
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<Button-1>", self._mouse_set)
        self.canvas.bind("<B1-Motion>", self._mouse_set)
        self.canvas.bind("<Key>", self._key)
        self.canvas.configure(takefocus=1)

    def snap(self, value):
        value = max(self.min_value, min(self.max_value, float(value)))
        snapped = self.min_value + round((value - self.min_value) / self.step) * self.step
        text = f"{self.step:.10f}".rstrip("0").rstrip(".")
        decimals = len(text.split(".", 1)[1]) if "." in text else 0
        snapped = round(snapped, max(0, min(6, decimals)))
        return max(self.min_value, min(self.max_value, snapped))

    def pct(self):
        width = self.max_value - self.min_value
        if abs(width) < 1e-12:
            return 0.0
        return (self.value - self.min_value) / width

    def set(self, value, silent=False):
        new_value = self.snap(value)
        if abs(new_value - self.value) < 1e-12 and not silent:
            return
        self.value = new_value
        self.readout.configure(text=self.formatter(self.value))
        self.redraw()
        if self.on_change and not silent:
            self.on_change()

    def get(self):
        return self.value

    def configure_range(self, min_value=None, max_value=None, step=None, labels=None, value=None, formatter=None, silent=False):
        if min_value is not None:
            self.min_value = float(min_value)
        if max_value is not None:
            self.max_value = float(max_value)
        if self.max_value <= self.min_value:
            self.max_value = self.min_value + max(abs(self.step), 1.0)
        if step is not None:
            self.step = float(step)
        if formatter is not None:
            self.formatter = formatter
        if labels is not None:
            self.labels = labels
        else:
            mid = (self.min_value + self.max_value) / 2
            self.labels = [f"{self.min_value:g}", f"{mid:g}", f"{self.max_value:g}"]
        self.set(self.value if value is None else value, silent=silent)
        self.redraw()

    def _mouse_set(self, event):
        self.canvas.focus_set()
        width = max(1, self.canvas.winfo_width())
        pad = 18
        track_w = max(1, width - pad * 2)
        ratio = max(0.0, min(1.0, (event.x - pad) / track_w))
        self.set(self.min_value + ratio * (self.max_value - self.min_value))

    def _key(self, event):
        value = self.value
        if event.keysym in ("Right", "Up"):
            value += self.step
        elif event.keysym in ("Left", "Down"):
            value -= self.step
        elif event.keysym == "Prior":
            value += self.step * 10
        elif event.keysym == "Next":
            value -= self.step * 10
        elif event.keysym == "Home":
            value = self.min_value
        elif event.keysym == "End":
            value = self.max_value
        else:
            return
        self.set(value)
        return "break"

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w = max(80, c.winfo_width())
        pad = 18
        y = self.track_y
        x1 = pad
        x2 = w - pad
        fill_x = x1 + self.pct() * (x2 - x1)
        c.create_line(x1, y, x2, y, fill=theme.BORDER, width=9, capstyle="round")
        c.create_line(x1, y, fill_x, y, fill=theme.BLUE, width=9, capstyle="round")
        r = self.thumb_radius
        c.create_oval(fill_x - r, y - r, fill_x + r, y + r, fill=theme.BLUE, outline=theme.TEXT_2, width=2)
        for i, text in enumerate(self.labels):
            x = x1 + (x2 - x1) * (i / max(1, len(self.labels) - 1))
            c.create_text(x, self.tick_y, text=text, fill=theme.MUTED_2, font=font(8))


class ScaledCanvas(tk.Canvas):
    def __init__(
        self,
        parent,
        view_width,
        view_height,
        min_height=260,
        auto_height=True,
        max_height=None,
        padding=0,
        **kwargs,
    ):
        super().__init__(
            parent,
            bg=theme.PANEL_2,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            bd=0,
            **kwargs,
        )
        self.view_width = view_width
        self.view_height = view_height
        self.min_height = min_height
        self.auto_height = auto_height
        self.max_height = max_height
        self.padding = padding
        self.scale = 1
        self.ox = 0
        self.oy = 0
        self._last_auto_width = None
        self.configure(height=min_height)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        if self.auto_height and event.width > 1 and event.width != self._last_auto_width:
            self._last_auto_width = event.width
            desired = int(event.width * self.view_height / self.view_width)
            desired = max(self.min_height, desired)
            if self.max_height is not None:
                desired = min(desired, self.max_height)
            if abs(desired - self.winfo_height()) > 2:
                self.configure(height=desired)
                return
        self.redraw()

    def setup_transform(self):
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        pad = max(0, self.padding)
        avail_w = max(1, w - pad * 2)
        avail_h = max(1, h - pad * 2)
        self.scale = min(avail_w / self.view_width, avail_h / self.view_height)
        self.ox = pad + (avail_w - self.view_width * self.scale) / 2
        self.oy = pad + (avail_h - self.view_height * self.scale) / 2

    def p(self, x, y):
        return self.ox + x * self.scale, self.oy + y * self.scale

    def sw(self, width):
        return max(1, width * self.scale)

    def sf(self, size):
        return max(7, int(size * self.scale))

    def line(self, *coords, **kwargs):
        pts = []
        for i in range(0, len(coords), 2):
            pts.extend(self.p(coords[i], coords[i + 1]))
        if "width" in kwargs:
            kwargs["width"] = self.sw(kwargs["width"])
        return self.create_line(*pts, **kwargs)

    def poly(self, points, **kwargs):
        coords = []
        for x, y in points:
            coords.extend(self.p(x, y))
        if "width" in kwargs:
            kwargs["width"] = self.sw(kwargs["width"])
        return self.create_line(*coords, **kwargs)

    def oval(self, cx, cy, r, **kwargs):
        x, y = self.p(cx, cy)
        rr = r * self.scale
        if "width" in kwargs:
            kwargs["width"] = self.sw(kwargs["width"])
        return self.create_oval(x - rr, y - rr, x + rr, y + rr, **kwargs)

    def rect(self, x, y, w, h, **kwargs):
        x1, y1 = self.p(x, y)
        x2, y2 = self.p(x + w, y + h)
        if "width" in kwargs:
            kwargs["width"] = self.sw(kwargs["width"])
        return self.create_rectangle(x1, y1, x2, y2, **kwargs)

    def text(self, x, y, text, size=12, color=theme.TEXT_2, weight="normal", anchor="center", **kwargs):
        return self.create_text(
            *self.p(x, y),
            text=text,
            fill=color,
            font=font(self.sf(size), weight),
            anchor=anchor,
            **kwargs,
        )

    def resistor_v(self, x, y1, y2, amp=9, segments=6, **kwargs):
        top = min(y1, y2)
        bottom = max(y1, y2)
        step = (bottom - top) / segments
        pts = [(x, top)]
        left = True
        for i in range(1, segments):
            pts.append((x - amp if left else x + amp, top + i * step))
            left = not left
        pts.append((x, bottom))
        return self.poly(pts, **kwargs)

    def resistor_h(self, x1, x2, y, amp=9, segments=6, **kwargs):
        left = min(x1, x2)
        right = max(x1, x2)
        step = (right - left) / segments
        pts = [(left, y)]
        up = True
        for i in range(1, segments):
            pts.append((left + i * step, y - amp if up else y + amp))
            up = not up
        pts.append((right, y))
        return self.poly(pts, **kwargs)

    def redraw(self):
        self.delete("all")
        self.setup_transform()
        self.draw()

    def draw(self):
        pass


class Toast(tk.Label):
    def __init__(self, parent):
        super().__init__(
            parent,
            bg=theme.SURFACE,
            fg=theme.TEXT,
            text="",
            font=font(10),
            padx=12,
            pady=9,
            highlightbackground=theme.LINE,
            highlightthickness=1,
        )
        self._after_id = None

    def show(self, text, duration=1900):
        self.configure(text=text)
        self.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(duration, self.hide)

    def hide(self):
        self.place_forget()
        self._after_id = None
