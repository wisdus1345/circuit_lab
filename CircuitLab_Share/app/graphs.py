import math
import time
import tkinter as tk
from collections import deque

from . import theme
from .widgets import font


def _nice_max(value):
    if not (value > 0):
        return 1.0
    power = 10 ** math.floor(math.log10(value))
    n = value / power
    if n <= 1:
        return power
    if n <= 2:
        return 2 * power
    if n <= 5:
        return 5 * power
    return 10 * power


def format_current(a):
    if not math.isfinite(a):
        return "—"
    if abs(a) < 1e-15:
        a = 0.0
    return format_auto_current(a, signed=True)


def format_auto_current(a, signed=False):
    if not math.isfinite(a):
        return "-"
    ref = abs(a)
    if ref >= 1:
        factor, unit, digits = 1, "A", 4
    elif ref >= 1e-3:
        factor, unit, digits = 1e3, "mA", 4
    elif ref >= 1e-6:
        factor, unit, digits = 1e6, "µA", 3
    else:
        factor, unit, digits = 1e9, "nA", 3
    value = a * factor if signed else abs(a) * factor
    return f"{value:.{digits}f} {unit}"


class RollingGraph(tk.Canvas):
    def __init__(self, parent, mode="signed", window_seconds=12, height=290, **kwargs):
        super().__init__(
            parent,
            bg=theme.GRAPH_BG,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            bd=0,
            height=height,
            **kwargs,
        )
        self.mode = mode
        self.window_seconds = window_seconds
        self.samples = deque()
        self.fixed_abs_min = None
        self.target_value = None
        self.voltage_max = 5
        self.unit_label = ""
        self.tau_markers = None
        self.bind("<Configure>", lambda _e: self.draw())

    def clear(self):
        self.samples.clear()
        self.draw()

    def add_sample(self, value):
        self.samples.append((time.perf_counter(), value))

    def set_target(self, value):
        self.target_value = value

    def set_voltage_max(self, value):
        self.voltage_max = value

    def set_window_seconds(self, seconds):
        if math.isfinite(seconds) and seconds > 0:
            self.window_seconds = seconds

    def set_tau_markers(self, tau=None, start_time=None, initial=0.0, target=0.0, active=True):
        if not active or not tau or tau <= 0 or start_time is None:
            self.tau_markers = None
        else:
            self.tau_markers = {"tau": tau, "start": start_time, "initial": initial, "target": target}

    def _slice(self, now):
        cutoff = now - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff - 0.1:
            self.samples.popleft()
        return [(t, v) for t, v in self.samples if t >= cutoff and math.isfinite(v)]

    def draw(self):
        self.delete("all")
        w = max(80, self.winfo_width())
        h = max(80, self.winfo_height())
        pad_l, pad_r, pad_t, pad_b = 42, 12, 10, 24
        plot_w = max(10, w - pad_l - pad_r)
        plot_h = max(10, h - pad_t - pad_b)
        self.create_rectangle(0, 0, w, h, fill=theme.GRAPH_BG, outline="")
        now = time.perf_counter()
        data = self._slice(now)
        t_min, t_max = now - self.window_seconds, now

        if self.mode == "magnitude":
            y_min, y_max, unit_factor, unit = self._magnitude_scale(data)
        elif self.mode == "voltage":
            y_min, y_max, unit_factor, unit = 0.0, max(1.0, self.voltage_max * 1.1), 1.0, "V"
        else:
            y_min, y_max, unit_factor, unit = self._signed_scale(data)

        def x_at(t):
            return pad_l + ((t - t_min) / (t_max - t_min)) * plot_w

        def y_at(v):
            return pad_t + (1 - ((v * unit_factor) - y_min) / (y_max - y_min)) * plot_h

        self.create_rectangle(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, outline=theme.BORDER, width=1)
        for i in range(1, 4):
            x = pad_l + plot_w * i / 4
            y = pad_t + plot_h * i / 4
            self.create_line(x, pad_t, x, pad_t + plot_h, fill="#263345")
            self.create_line(pad_l, y, pad_l + plot_w, y, fill="#263345")

        if self.mode == "voltage":
            zero_y = y_at(0)
            self.create_line(pad_l, zero_y, pad_l + plot_w, zero_y, fill=theme.DANGER, width=2, dash=(4, 4))
            self.create_text(pad_l + 5, zero_y - 6, text="0 V", fill=theme.DANGER, font=font(8, "bold"), anchor="w")
            if self.target_value is not None:
                ty = y_at(self.target_value)
                self.create_line(pad_l, ty, pad_l + plot_w, ty, fill=theme.MUTED_2, width=1, dash=(5, 4))
                label = "방전 목표 0 V" if abs(self.target_value) < 1e-12 else f"충전 목표 Vs = {self.target_value:g} V"
                self.create_text(pad_l + plot_w - 5, ty - 8, text=label, fill=theme.DANGER, font=font(8), anchor="e")
            self._draw_tau_markers(now, t_min, t_max, x_at, y_at, pad_l, pad_t, plot_w, plot_h)
        else:
            zero_y = y_at(0)
            self.create_line(pad_l, zero_y, pad_l + plot_w, zero_y, fill=theme.DANGER if self.mode == "signed" else theme.GOOD, width=3)

        if len(data) >= 2:
            pts = []
            for t, value in data:
                pts.extend((x_at(t), y_at(value)))
            self.create_line(*pts, fill=theme.BLUE, width=2)
        elif len(data) == 1:
            t, value = data[0]
            x, y = x_at(t), y_at(value)
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=theme.BLUE, outline="")

        self._draw_labels(pad_l, pad_t, plot_w, plot_h, y_min, y_max, unit, self.mode)

    def _draw_tau_markers(self, now, t_min, t_max, x_at, y_at, pad_l, pad_t, plot_w, plot_h):
        if self.mode != "voltage" or not self.tau_markers:
            return
        tau = self.tau_markers["tau"]
        start = self.tau_markers["start"]
        initial = self.tau_markers["initial"]
        target = self.tau_markers["target"]
        charging = target >= initial
        percentages = {1: 0.632, 2: 0.865, 3: 0.950, 5: 0.993} if charging else {1: 0.368, 2: 0.135, 3: 0.050, 5: 0.007}
        for n, pct in percentages.items():
            t = start + tau * n
            if not (t_min <= t <= t_max):
                continue
            x = x_at(t)
            level = initial + (target - initial) * (1 - math.exp(-n))
            y = y_at(level)
            self.create_line(x, pad_t, x, pad_t + plot_h, fill=theme.WARN, width=1, dash=(3, 4))
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=theme.WARN, outline="")
            self.create_text(x + 4, pad_t + 12, text=f"{n}τ", fill=theme.WARN, font=font(8, "bold"), anchor="w")
            self.create_text(x + 4, y - 7, text=f"{pct * 100:.1f}%", fill=theme.MUTED_2, font=font(7), anchor="w")

    def _signed_scale(self, data):
        if not data:
            abs_max = self.fixed_abs_min or 1e-9
        else:
            abs_max = max(abs(v) for _t, v in data)
            abs_max = max(abs_max * 1.5, self.fixed_abs_min or 1e-6)
        if abs_max >= 1:
            unit_factor, unit = 1, "A"
        elif abs_max >= 1e-3:
            unit_factor, unit = 1e3, "mA"
        elif abs_max >= 1e-6:
            unit_factor, unit = 1e6, "µA"
        else:
            unit_factor, unit = 1e9, "nA"
        y = max(abs_max * unit_factor, 1e-9 * unit_factor)
        return -y, y, unit_factor, unit

    def _magnitude_scale(self, data):
        max_amp = max((abs(v) for _t, v in data), default=0)
        if max_amp >= 1e-3:
            factor, unit = 1e3, "mA"
        else:
            factor, unit = 1e6, "µA"
        y_max = _nice_max((max_amp * factor) * 1.35 or 1)
        self.unit_label = f"|Id| ({unit})"
        return 0.0, y_max, factor, unit

    def _draw_labels(self, pad_l, pad_t, plot_w, plot_h, y_min, y_max, unit, mode):
        self.create_text(pad_l - 7, pad_t, text=f"{y_max:.3g} {unit}", fill=theme.MUTED_2, font=font(8), anchor="ne")
        self.create_text(pad_l - 7, pad_t + plot_h, text=f"{y_min:.3g} {unit}", fill=theme.MUTED_2, font=font(8), anchor="se")
        if mode == "signed":
            y0 = pad_t + plot_h * (1 - (0 - y_min) / (y_max - y_min))
            self.create_text(pad_l - 7, y0, text="0 A", fill=theme.DANGER, font=font(8, "bold"), anchor="e")
        self.create_text(pad_l + plot_w * 0.15, pad_t + plot_h + 13, text="과거", fill=theme.MUTED_2, font=font(8))
        self.create_text(pad_l + plot_w * 0.85, pad_t + plot_h + 13, text="최근", fill=theme.MUTED_2, font=font(8))


class LoadLineGraph(tk.Canvas):
    def __init__(self, parent, height=330, **kwargs):
        super().__init__(
            parent,
            bg=theme.GRAPH_BG,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            bd=0,
            height=height,
            **kwargs,
        )
        self.result = None
        self.mode = "bias"
        self.region_label = "-"
        self.bind("<Configure>", lambda _e: self.draw())

    def set_state(self, result, mode, region_label):
        self.result = result
        self.mode = mode
        self.region_label = region_label
        self.draw()

    def draw(self):
        self.delete("all")
        w = max(120, self.winfo_width())
        h = max(100, self.winfo_height())
        self.create_rectangle(0, 0, w, h, fill=theme.GRAPH_BG, outline="")
        if not self.result:
            return
        p = self.result["p"]
        pad_l, pad_r, pad_t, pad_b = 50, 14, 16, 34
        plot_w = max(20, w - pad_l - pad_r)
        plot_h = max(20, h - pad_t - pad_b)
        x_max = max(1, p["vcc"])
        y_max_a = max(p["vcc"] / p["rc"], self.result["ic"], 1e-6) * 1.18
        y_max_ma = y_max_a * 1000

        def x_at(vce):
            return pad_l + (vce / x_max) * plot_w

        def y_at(ic_a):
            return pad_t + (1 - (ic_a * 1000) / y_max_ma) * plot_h

        self.create_rectangle(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, outline=theme.BORDER)
        self._draw_overlay(p, x_at, y_at, pad_l, pad_t, plot_w, plot_h)
        for i in range(1, 5):
            x = pad_l + plot_w * i / 5
            y = pad_t + plot_h * i / 5
            self.create_line(x, pad_t, x, pad_t + plot_h, fill="#263345")
            self.create_line(pad_l, y, pad_l + plot_w, y, fill="#263345")

        self.create_line(x_at(p["vcc"]), y_at(0), x_at(0), y_at(p["vcc"] / p["rc"]), fill=theme.BLUE, width=3)
        qx, qy = x_at(self.result["vce"]), y_at(self.result["ic"])
        color = theme.GOOD if self.result["region"] == "active" else theme.WARN if self.result["region"] == "saturation" else theme.MUTED_2
        self.create_oval(qx - 7, qy - 7, qx + 7, qy + 7, fill=color, outline=theme.TEXT_2, width=2)
        self.create_text(qx + 12, qy - 8, text="Q", fill=theme.TEXT_2, font=font(10, "bold"), anchor="sw")
        self.create_text(pad_l + 12, pad_t + 12, text=self.region_label, fill=color, font=font(11, "bold"), anchor="nw")
        self.create_text(pad_l + 12, pad_t + 31, text="IC = (VCC - VCE) / RC", fill=theme.MUTED_2, font=font(8), anchor="nw")
        self.create_text(pad_l + plot_w / 2, h - 18, text="VCE (V)", fill=theme.MUTED_2, font=font(8))
        self.create_text(24, pad_t - 7, text="IC (mA)", fill=theme.MUTED_2, font=font(8), anchor="w")
        self.create_text(pad_l + plot_w, pad_t + plot_h + 7, text=f"{p['vcc']:.1f} V", fill=theme.MUTED_2, font=font(8), anchor="n")
        self.create_text(pad_l - 8, pad_t, text=f"{y_max_ma:.2f}", fill=theme.MUTED_2, font=font(8), anchor="ne")

    def _draw_overlay(self, p, x_at, y_at, pad_l, pad_t, plot_w, plot_h):
        r = self.result
        if self.mode == "bias":
            tol = max(0.3, p["vcc"] * 0.05)
            x1 = x_at(max(0, p["vcc"] / 2 - tol))
            x2 = x_at(min(p["vcc"], p["vcc"] / 2 + tol))
            self.create_rectangle(x1, pad_t, x2, pad_t + plot_h, fill="#16342f", outline="")
            mid_x = x_at(p["vcc"] / 2)
            self.create_line(mid_x, pad_t, mid_x, pad_t + plot_h, fill=theme.GOOD, width=1)
            self.create_text(mid_x + 5, pad_t + 13, text="VCC/2", fill=theme.GOOD, font=font(8, "bold"), anchor="w")
            left_margin = max(0.0, r["vce"] - p["vceSat"])
            right_margin = max(0.0, p["vcc"] - r["vce"])
            swing = min(left_margin, right_margin)
            self.create_text(pad_l + plot_w - 8, pad_t + 12, text=f"대칭 스윙 여유 ±{swing:.2f} V", fill=theme.TEXT_2, font=font(8), anchor="ne")
            self._arrow(x_at(r["vce"]), y_at(r["ic"]), x_at(p["vcc"]), y_at(0), "#7aa2ff", dash=(5, 4))
            self._arrow(x_at(r["vce"]), y_at(r["ic"]), x_at(p["vceSat"]), y_at((p["vcc"] - p["vceSat"]) / p["rc"]), theme.WARN, dash=(5, 4))
        elif self.mode == "switch_off":
            x1 = x_at(p["vcc"] * 0.82)
            y1 = y_at((p["vcc"] / p["rc"]) * 0.12)
            self.create_rectangle(x1, y1, x_at(p["vcc"]), y_at(0), fill="#172542", outline="")
            self.create_text(x_at(p["vcc"]) - 8, y_at(0) - 10, text="OFF 목표: IC≈0, VCE≈VCC", fill=theme.GOOD, font=font(8, "bold"), anchor="e")
            self._arrow(x_at(r["vce"]), y_at(r["ic"]), x_at(p["vcc"]), y_at(0), "#7aa2ff", dash=(4, 4))
        elif self.mode == "switch_on":
            x2 = x_at(min(p["vcc"], p["vceSat"] + 0.45))
            y1 = y_at((p["vcc"] / p["rc"]) * 0.72)
            self.create_rectangle(x_at(0), y1, x2, y_at(0), fill="#3b3216", outline="")
            sat_x = x_at(p["vceSat"])
            self.create_line(sat_x, pad_t, sat_x, pad_t + plot_h, fill=theme.WARN, width=1, dash=(4, 4))
            self.create_text(sat_x + 5, pad_t + 13, text="VCE(sat)", fill=theme.WARN, font=font(8, "bold"), anchor="w")
            self._arrow(x_at(r["vce"]), y_at(r["ic"]), x_at(p["vceSat"]), y_at((p["vcc"] - p["vceSat"]) / p["rc"]), theme.WARN, dash=(4, 4))
        else:
            x1 = x_at(p["vcc"] * 0.28)
            x2 = x_at(p["vcc"] * 0.72)
            y1 = y_at((p["vcc"] / p["rc"]) * 0.72)
            y2 = y_at((p["vcc"] / p["rc"]) * 0.22)
            self.create_rectangle(x1, y1, x2, y2, fill="#102944", outline=theme.BLUE, dash=(5, 5))

    def _arrow(self, x1, y1, x2, y2, color, dash=None):
        self.create_line(x1, y1, x2, y2, fill=color, width=2, dash=dash or ())
        angle = math.atan2(y2 - y1, x2 - x1)
        head = 8
        p1 = (x2 - head * math.cos(angle - math.pi / 6), y2 - head * math.sin(angle - math.pi / 6))
        p2 = (x2 - head * math.cos(angle + math.pi / 6), y2 - head * math.sin(angle + math.pi / 6))
        self.create_polygon(x2, y2, *p1, *p2, fill=color, outline=color)
