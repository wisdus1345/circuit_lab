import math
import time
import tkinter as tk

from .. import theme
from ..graphs import RollingGraph, format_current
from ..widgets import CanvasSlider, CompactMetricBox, DualColumnWorkbench, NumberField, Panel, ResponsiveControlGrid, ResponsiveTopFrame, ScaledCanvas, ScrollableFrame, SectionHeader, font, make_button, set_button_variant


def rc_step(vc, target_v, dt, tau):
    if not (tau > 0) or not math.isfinite(dt):
        return vc
    return target_v + (vc - target_v) * math.exp(-dt / tau)


class RCDiagram(ScaledCanvas):
    def __init__(self, parent, page):
        self.page = page
        super().__init__(parent, 620, 400, min_height=270, max_height=330, padding=1)

    def draw(self):
        p = self.page
        wire = theme.WIRE
        self.rect(10, 10, 600, 380, outline="#2b3847", fill=theme.PANEL_2, width=1.2)
        self.line(120, 300, 120, 220, fill=wire, width=3.2)
        self.line(120, 180, 120, 100, fill=wire, width=3.2)
        self.line(95, 220, 145, 220, fill=wire, width=3)
        self.line(105, 180, 135, 180, fill=wire, width=6)
        self.text(80, 225, "+", color=theme.DANGER, size=16, weight="bold")
        self.text(80, 185, "−", color="#7aa2ff", size=16, weight="bold")
        self.text(120, 260, f"Vs {p.vs():g} V", color=theme.TEXT_2, size=14, weight="bold")
        self.line(120, 300, 460, 300, fill=wire, width=3.2)
        self.line(120, 100, 180, 100, fill=wire, width=3.2)
        self.oval(180, 100, 5, fill=theme.NODE, outline="#dff1ff", width=2)
        self.text(180, 85, "충전 (A)", color=theme.MUTED_2, size=11)
        self.line(180, 300, 180, 180, fill=wire, width=3.2)
        self.oval(180, 180, 5, fill=theme.NODE, outline="#dff1ff", width=2)
        self.text(180, 200, "방전 (B)", color=theme.MUTED_2, size=11)
        self.oval(260, 140, 5, fill=theme.NODE, outline="#dff1ff", width=2)
        end_y = 100 if p.switch_state == "charge" else 180
        self.line(260, 140, 180, end_y, fill=theme.BLUE, width=4)
        self.text(240, 125, "SW", color=theme.TEXT_2, size=14, weight="bold")
        self.line(260, 140, 320, 140, fill=wire, width=3.2)
        self.resistor_h(320, 390, 140, fill=theme.RC_GREEN, width=3)
        self.line(390, 140, 460, 140, fill=wire, width=3.2)
        self.text(355, 115, f"R {p.r_slider.get():.0f} Ω", color=theme.TEXT_2, size=12, weight="bold")
        self.line(460, 140, 460, 210, fill=wire, width=3.2)
        self.line(430, 210, 490, 210, fill=wire, width=4)
        self.line(430, 230, 490, 230, fill=wire, width=4)
        self.line(460, 230, 460, 300, fill=wire, width=3.2)
        self.text(510, 225, f"C {p.c_slider.get():.0f} µF", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(460, 180, f"Vc {p.vc:.2f} V", color=theme.DANGER, size=13, weight="bold")


class RCPage(tk.Frame):
    title = "RC 회로"

    def __init__(self, parent, toast=None):
        super().__init__(parent, bg=theme.BG)
        self.toast = toast
        self.vc = 0.0
        self.switch_state = "charge"
        self.last_time = None
        self.switch_start_time = time.perf_counter()
        self.switch_start_vc = 0.0
        self._after_id = None
        self._build()

    def _build(self):
        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)
        root = self.scroll.inner
        root.columnconfigure(0, weight=1)
        tk.Label(root, text="스위치를 바꿔 충전/방전 곡선과 시간상수 τ의 의미를 관찰합니다.", bg=theme.BG, fg=theme.MUTED_2, font=font(9)).grid(
            row=0, column=0, sticky="w", padx=theme.PAGE_PAD_X, pady=(6, 4)
        )
        top_frame = DualColumnWorkbench(root, breakpoint=980, left_weight=3, right_weight=2)
        top_frame.grid(row=1, column=0, sticky="ew", padx=theme.PAGE_PAD_X, pady=(0, theme.CARD_GAP))
        left_stack = tk.Frame(top_frame, bg=theme.BG)
        right_stack = tk.Frame(top_frame, bg=theme.BG)
        left_stack.columnconfigure(0, weight=1)
        right_stack.columnconfigure(0, weight=1)

        diagram_panel = Panel(left_stack)
        diagram_panel.grid(row=0, column=0, sticky="ew", pady=(0, theme.CARD_GAP))
        SectionHeader(diagram_panel, "회로도", "좌측: 전압원 / 중앙: 스위치 / 우측: R, C").pack(fill="x")
        self.diagram = RCDiagram(diagram_panel, self)
        self.diagram.pack(fill="x", padx=6, pady=(0, 8))

        switch_card = Panel(left_stack)
        switch_card.grid(row=1, column=0, sticky="ew")
        SectionHeader(switch_card, "충전/방전 조작", "스위치를 바꾸면 목표 전압선과 τ 마커가 갱신됩니다.").pack(fill="x")
        switch_grid = ResponsiveControlGrid(switch_card, columns=2, breakpoint=420)
        switch_grid.pack(fill="x", padx=8, pady=(0, 8))
        self.tau_metric = CompactMetricBox(switch_grid, "시상수 τ", "—")
        switch_grid.add(self.tau_metric)
        self.rc_hint = CompactMetricBox(switch_grid, "관찰 포인트", "1τ≈63.2%, 5τ≈99.3% 접근", value_size=9)
        switch_grid.add(self.rc_hint)
        switch_panel = tk.Frame(switch_grid, bg=theme.PANEL)
        tk.Label(switch_panel, text="스위치 조작", bg=theme.PANEL, fg=theme.MUTED_2, font=font(9, "bold")).pack(
            anchor="w", padx=6, pady=(0, 3)
        )
        btns = tk.Frame(switch_panel, bg=theme.PANEL)
        btns.pack(fill="x", padx=6, pady=(0, 5))
        self.switch_buttons_frame = btns
        self.charge_btn = make_button(btns, "충전 회로 (A) 연결", lambda: self.set_switch("charge"), "accent")
        self.discharge_btn = make_button(btns, "방전 회로 (B) 연결", lambda: self.set_switch("discharge"), "secondary")
        self.charge_btn.configure(wraplength=150, justify="center")
        self.discharge_btn.configure(wraplength=150, justify="center")
        btns.bind("<Configure>", self._layout_switch_buttons)
        self._layout_switch_buttons()
        make_button(switch_panel, "커패시터 완전 방전", self.reset_capacitor, "secondary").pack(
            fill="x", padx=6, pady=(0, 5)
        )
        switch_grid.add(switch_panel, span=2)

        graph_panel = Panel(right_stack)
        graph_panel.grid(row=0, column=0, sticky="ew")
        SectionHeader(graph_panel, "커패시터 전압 (Vc) · 실시간 그래프").pack(fill="x")
        self.graph = RollingGraph(graph_panel, mode="voltage", height=theme.GRAPH_HEIGHT)
        self.graph.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.readout = CompactMetricBox(graph_panel, "현재 Vc / I", "전압 대기 중...")
        self.readout.pack(fill="x", padx=8, pady=(0, 4))
        self.graph_meta = tk.Label(
            graph_panel,
            text="가로축: 최근 12 s · 세로축: 전압",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(9),
            anchor="w",
        )
        self.graph_meta.pack(fill="x", padx=8, pady=(0, 4))
        primary_controls_panel = Panel(right_stack)
        primary_controls_panel.grid(row=1, column=0, sticky="ew", pady=(theme.CARD_GAP, 0))
        SectionHeader(primary_controls_panel, "핵심 조작", "R과 C로 시간상수 τ를 먼저 정하고, Vs로 목표 전압을 조절합니다.").pack(fill="x")
        focus_grid = ResponsiveControlGrid(primary_controls_panel, columns=2, breakpoint=420)
        focus_grid.pack(fill="x", padx=8, pady=(0, 8))
        self.r_slider = CanvasSlider(
            focus_grid,
            "저항 R (Ω)",
            100,
            100000,
            100,
            10000,
            formatter=lambda v: f"{v:.0f} Ω",
            labels=["100", "50k", "100k"],
            on_change=self.on_value_change,
            compact=True,
        )
        focus_grid.add(self.r_slider)
        self.c_slider = CanvasSlider(
            focus_grid,
            "커패시턴스 C (µF)",
            1,
            1000,
            1,
            100,
            formatter=lambda v: f"{v:.0f} µF",
            labels=["1", "500", "1000"],
            on_change=self.on_value_change,
            compact=True,
        )
        focus_grid.add(self.c_slider)
        self.vs_field = NumberField(focus_grid, "전원 전압 Vs (V)", 5, on_change=self.on_value_change)
        focus_grid.add(self.vs_field)
        top_frame.set_children(left_stack, right_stack)
        self.update_tau()
        set_button_variant(self.charge_btn, True)
        set_button_variant(self.discharge_btn, False)

    def vs(self):
        value = self.vs_field.get_float(default=5)
        return value if math.isfinite(value) and value >= 0 else 5

    def _layout_switch_buttons(self, _event=None):
        if not hasattr(self, "charge_btn"):
            return
        width = self.switch_buttons_frame.winfo_width()
        for button in (self.charge_btn, self.discharge_btn):
            button.grid_forget()
        if width and width < 360:
            self.switch_buttons_frame.columnconfigure(0, weight=1)
            self.charge_btn.grid(row=0, column=0, sticky="ew", pady=(0, 5))
            self.discharge_btn.grid(row=1, column=0, sticky="ew")
        else:
            self.switch_buttons_frame.columnconfigure(0, weight=1, uniform="rc_switch")
            self.switch_buttons_frame.columnconfigure(1, weight=1, uniform="rc_switch")
            self.charge_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
            self.discharge_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))

    def on_value_change(self):
        self._advance_state()
        self.switch_start_time = time.perf_counter()
        self.switch_start_vc = self.vc
        self.update_tau()
        self.graph.set_voltage_max(self.vs())
        self.update_tau_markers()
        self.diagram.redraw()

    def update_tau(self):
        tau = self.r_slider.get() * self.c_slider.get() * 1e-6
        self.tau_metric.set(f"τ = R × C = {tau:.3f} s")
        window = max(2.0, min(120.0, 5 * tau))
        self.graph.set_window_seconds(window)
        self.graph_meta.configure(text=f"가로축: 최근 5τ ≈ {window:.2f} s · 세로축: 전압")

    def update_tau_markers(self):
        tau = self.r_slider.get() * self.c_slider.get() * 1e-6
        target = self.vs() if self.switch_state == "charge" else 0.0
        self.graph.set_target(target)
        self.graph.set_tau_markers(tau, self.switch_start_time, self.switch_start_vc, target)

    def set_switch(self, state):
        self._advance_state()
        self.switch_state = state
        self.switch_start_time = time.perf_counter()
        self.switch_start_vc = self.vc
        set_button_variant(self.charge_btn, state == "charge")
        set_button_variant(self.discharge_btn, state == "discharge")
        self.update_tau_markers()
        self.diagram.redraw()
        if self.toast:
            self.toast.show("RC: 충전 회로가 연결되었습니다." if state == "charge" else "RC: 방전 회로가 연결되었습니다.")

    def reset_capacitor(self):
        self.vc = 0.0
        self.switch_start_time = time.perf_counter()
        self.switch_start_vc = 0.0
        self.last_time = self.switch_start_time
        self.graph.clear()
        self.update_tau_markers()
        self.readout.set("Vc = 0.000 V  |  I = 0.0000 mA  |  초기화됨")
        self.diagram.redraw()
        if self.toast:
            self.toast.show("커패시터를 완전 방전했습니다.")

    def on_show(self):
        self.scroll.scroll_to_top()
        self.last_time = time.perf_counter()
        self._tick()

    def on_hide(self):
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None

    def _tick(self):
        now = time.perf_counter()
        updated = self._advance_state(now)
        if updated:
            self.diagram.redraw()
        self.graph.draw()
        self._after_id = self.after(16, self._tick)

    def _advance_state(self, now=None):
        now = now or time.perf_counter()
        dt = now - (self.last_time or now)
        if dt * 1000 >= 16:
            vs = self.vs()
            r = self.r_slider.get()
            c = self.c_slider.get() * 1e-6
            tau = r * c
            target = vs if self.switch_state == "charge" else 0.0
            self.vc = rc_step(self.vc, target, dt, tau)
            current = (target - self.vc) / r
            self.graph.set_voltage_max(max(vs, self.switch_start_vc))
            self.update_tau_markers()
            self.graph.add_sample(self.vc)
            if self.switch_state == "charge":
                pct = (self.vc / vs * 100) if vs > 0 else 0.0
                pct_text = f"목표 전압의 {pct:.1f}%"
            else:
                base = self.switch_start_vc if self.switch_start_vc > 1e-9 else max(self.vc, 1e-9)
                pct = (1 - self.vc / base) * 100
                pct_text = f"방전 진행 {max(0, min(100, pct)):.1f}%"
            self.readout.set(f"Vc = {self.vc:.3f} V  |  I = {format_current(current)}  |  {pct_text}")
            self.last_time = now
            return True
        return False
