import math
import random
import tkinter as tk

from .. import theme
from ..graphs import RollingGraph, format_current
from ..widgets import CanvasSlider, CompactHintBar, CompactMetricBox, DualColumnWorkbench, NumberField, Panel, ResponsiveControlGrid, ResponsiveTopFrame, ScaledCanvas, ScrollableFrame, SectionHeader, font, make_button


RX_MIN = 100
RX_MAX = 1000
RV_STEP = 0.01


def random_rx():
    return random.randint(RX_MIN, RX_MAX)


def wheatstone_current(vs, r1, r2, rv, rx, ra):
    """원본 JS bridgeAmmeterCurrent()의 노드 해석식."""
    if not (r1 > 0 and r2 > 0 and rv > 0 and rx > 0 and ra > 0):
        return math.nan
    g11 = 1 / r1 + 1 / rv + 1 / ra
    g12 = -1 / ra
    g21 = -1 / ra
    g22 = 1 / r2 + 1 / rx + 1 / ra
    i1 = vs / r1
    i2 = vs / r2
    det = g11 * g22 - g12 * g21
    if abs(det) < 1e-30:
        return math.nan
    va = (i1 * g22 - i2 * g12) / det
    vb = (g11 * i2 - g21 * i1) / det
    return (va - vb) / ra


class WheatstoneDiagram(ScaledCanvas):
    def __init__(self, parent, page):
        self.page = page
        super().__init__(parent, 620, 400, min_height=270, max_height=330, padding=1)

    def draw(self):
        p = self.page
        wire = theme.WIRE
        self.rect(10, 10, 600, 380, outline="#2b3847", fill=theme.PANEL_2, width=1.2)
        self.line(88, 88, 428, 88, fill=wire, width=3.2)
        self.line(88, 312, 428, 312, fill=wire, width=3.2)
        self.line(88, 88, 88, 158, fill=wire, width=3.2)
        self.line(88, 242, 88, 312, fill=wire, width=3.2)
        self.oval(88, 200, 42, fill="#111821", outline=wire, width=3)
        self.oval(88, 200, 34, outline="#334252", width=1)
        self.text(88, 187, "+", color=theme.DANGER, size=16, weight="bold")
        self.text(88, 219, "−", color="#7aa2ff", size=16, weight="bold")
        self.text(26, 194, "Vₛ", color=theme.TEXT_2, size=16, weight="bold", anchor="w")
        self.text(20, 230, f"{p.vs():g} V", color=theme.TEXT_2, size=12, anchor="w")

        self._arm(178, 88, 208, "R₁", f"{p.r1():g} Ω", "left")
        self._arm(428, 88, 208, "R₂", f"{p.r2():g} Ω", "right")
        self._arm(178, 208, 312, "Rᵥ", f"{p.rv():.2f} Ω", "left", sub="가변저항")
        rx_text = f"{p.rx} Ω" if p.rx_visible else "??? Ω"
        self._arm(428, 208, 312, "Rₓ", rx_text, "right", sub="미지저항")

        self.oval(178, 208, 5.8, fill=theme.NODE, outline="#dff1ff", width=2)
        self.oval(428, 208, 5.8, fill=theme.NODE, outline="#dff1ff", width=2)
        self.line(184, 208, 262, 208, fill=wire, width=3.2)
        self.line(344, 208, 422, 208, fill=wire, width=3.2)
        self.oval(303, 208, 41, fill="#111821", outline=wire, width=3)
        self.oval(303, 208, 33, outline="#334252", width=1)
        self.text(303, 216, "A", color=theme.TEXT_2, size=28, weight="bold")
        self.text(303, 263, "검류계", color=theme.TEXT_2, size=15, weight="bold")
        self.text(303, 284, f"내부저항 {p.ra():g} Ω", color=theme.MUTED_2, size=12)
        self.text(155, 195, "a", color=theme.BLUE, size=15, weight="bold")
        self.text(443, 195, "b", color=theme.BLUE, size=15, weight="bold")
        self.text(151, 237, "+", color=theme.DANGER, size=16, weight="bold")
        self.text(310, 52, "Wheatstone Bridge", color=theme.TEXT_2, size=16, weight="bold")
        self.text(310, 72, "균형점에서 a, b 전위차가 0에 가까워집니다", color=theme.MUTED_2, size=12)

    def _arm(self, x, y1, y2, label, value, side, sub=None):
        wire = theme.WIRE
        if y2 - y1 > 120:
            lead1, lead2 = y1 + 12, y2 - 12
        else:
            lead1, lead2 = y1 + 28, y2 - 12
        self.line(x, y1, x, lead1, fill=wire, width=3.2)
        self.resistor_v(x, lead1, lead2, fill=wire, width=2.8)
        self.line(x, lead2, x, y2, fill=wire, width=3.2)
        tx = x - 66 if side == "left" else x + 36
        anchor = "w"
        self.text(tx, (lead1 + lead2) / 2 - 16, label, color=theme.TEXT_2, size=16, weight="bold", anchor=anchor)
        self.text(tx, (lead1 + lead2) / 2 + 6, value, color=theme.TEXT_2, size=12, anchor=anchor)
        if sub:
            self.text(tx, (lead1 + lead2) / 2 + 25, sub, color=theme.MUTED_2, size=12, anchor=anchor)


class WheatstonePage(tk.Frame):
    title = "Wheatstone 브리지"

    def __init__(self, parent, toast=None):
        super().__init__(parent, bg=theme.BG)
        self.toast = toast
        self.rx = random_rx()
        self.rx_visible = False
        self.diagnostic_visible = False
        self._after_id = None
        self._syncing = False
        self._build()

    def _build(self):
        scroll = ScrollableFrame(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        root = scroll.inner
        root.columnconfigure(0, weight=1)

        tk.Label(root, text="Rv를 조절해 검류계 전류 0선에 맞추고 Rx를 추정합니다.", bg=theme.BG, fg=theme.MUTED_2, font=font(9)).grid(
            row=0, column=0, sticky="w", padx=theme.PAGE_PAD_X, pady=(6, 4)
        )

        top_frame = DualColumnWorkbench(root, breakpoint=980, left_weight=3, right_weight=2)
        top_frame.grid(row=1, column=0, sticky="ew", padx=theme.PAGE_PAD_X, pady=(0, theme.CARD_GAP))
        left_stack = tk.Frame(top_frame, bg=theme.BG)
        right_stack = tk.Frame(top_frame, bg=theme.BG)
        left_stack.columnconfigure(0, weight=1)
        right_stack.columnconfigure(0, weight=1)

        diagram_panel = Panel(left_stack)
        diagram_panel.pack(fill="x", pady=(0, theme.CARD_GAP))
        SectionHeader(diagram_panel, "회로도", "상단: 고정저항 / 하단: Rv, Rx / 중앙: 검류계").pack(fill="x")
        self.diagram = WheatstoneDiagram(diagram_panel, self)
        self.diagram.pack(fill="x", padx=6, pady=(0, 8))

        graph_panel = Panel(right_stack)
        graph_panel.pack(fill="x")
        SectionHeader(graph_panel, "검류계 전류 · 실시간 그래프").pack(fill="x")
        self.graph = RollingGraph(graph_panel, mode="signed", height=theme.GRAPH_HEIGHT)
        self.graph.fixed_abs_min = 2e-6
        self.graph.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.current_metric = CompactMetricBox(graph_panel, "검류계 전류 / (시간값)", "연결 대기 중...")
        self.current_metric.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(
            graph_panel,
            text="가로축: 최근 12 s · 세로축: 전류",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(9),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))
        primary_controls_panel = Panel(right_stack)
        primary_controls_panel.pack(fill="x", pady=(theme.CARD_GAP, 0))
        SectionHeader(primary_controls_panel, "핵심 조작", "Rv를 조절하면서 검류계 전류와 평형 판정을 바로 확인합니다.").pack(fill="x")
        focus_grid = ResponsiveControlGrid(primary_controls_panel, columns=2, breakpoint=420)
        focus_grid.pack(fill="x", padx=8, pady=(0, 8))
        self.rv_slider = CanvasSlider(
            focus_grid,
            "Rᵥ 조절 · 전류 0선 맞추기",
            100,
            1000,
            RV_STEP,
            550,
            formatter=lambda v: f"{v:.2f} Ω",
            labels=["100", "550", "1000"],
            on_change=self.on_rv_slider,
            compact=True,
        )
        focus_grid.add(self.rv_slider, span=2)
        self.rv_field = NumberField(focus_grid, "Rᵥ 직접 입력 (Ω)", 550, on_change=self.on_rv_field, digits=2)
        self.range_metric = CompactMetricBox(focus_grid, "평형 가능성", "현재 범위 판정 대기")
        self.balance_state = CompactMetricBox(focus_grid, "평형 상태", "전류를 관찰하세요.")
        self.estimate_metric = CompactMetricBox(focus_grid, "미지저항 Rx 추정값", "Rx_est = Rv × R₂ / R₁")
        focus_grid.add(self.rv_field)
        focus_grid.add(self.range_metric)
        focus_grid.add(self.balance_state)
        focus_grid.add(self.estimate_metric)
        range_buttons = tk.Frame(focus_grid, bg=theme.PANEL)
        make_button(range_buttons, "평형점 주변으로 확대", self.auto_range, "accent").pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.diagnostic_btn = make_button(range_buttons, "진단 정보 보기", self.toggle_diagnostic, "secondary")
        self.diagnostic_btn.pack(side="left", fill="x", expand=True)
        focus_grid.add(range_buttons, span=2)
        fine = tk.Frame(focus_grid, bg=theme.PANEL)
        for label, delta in (("−1Ω", -1.0), ("−0.1Ω", -0.1), ("−0.01Ω", -0.01), ("+0.01Ω", 0.01), ("+0.1Ω", 0.1), ("+1Ω", 1.0)):
            make_button(fine, label, lambda d=delta: self.adjust_rv(d), "secondary").pack(side="left", fill="x", expand=True, padx=2)
        focus_grid.add(fine, span=2)
        self.hint = CompactHintBar(primary_controls_panel, "현재 조작 힌트", "Rv를 움직여 전류 0선에 맞춥니다. R1/R2를 바꾸면 평형 Rv 위치도 이동합니다.", wraplength=440)
        self.hint.pack(fill="x", padx=8, pady=(0, 8))
        goal_panel = Panel(left_stack)
        goal_panel.pack(fill="x", pady=(0, theme.CARD_GAP))

        SectionHeader(goal_panel, "실습 목표", "검류계 전류가 0이 되는 비율 평형점에서 미지저항 Rx를 추정합니다.").pack(fill="x")
        tk.Label(
            goal_panel,
            text="1. Rv를 조절해 검류계 전류가 0에 가까워지는 지점을 찾습니다.\n2. 평형점에서 Rx ≈ Rv × R2 / R1 관계를 확인합니다.\n3. Rx 공개 후 추정값과 실제값의 오차를 비교합니다.",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(8),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 8))

        basic_controls_panel = Panel(left_stack)
        basic_controls_panel.pack(fill="x")
        SectionHeader(basic_controls_panel, "기본 회로 값 조절", "Vs, R₁, R₂, 검류계와 미지저항 설정을 한곳에서 정리합니다.").pack(fill="x")

        control_grid = ResponsiveControlGrid(basic_controls_panel, columns=2, breakpoint=520)
        control_grid.pack(fill="x", padx=8, pady=(0, 10))

        self.vs_field = NumberField(control_grid, "전원 전압 (V)", 220, on_change=self.on_value_change)
        self.r1_field = NumberField(control_grid, "상단 좌측 R₁ (Ω)", 1000, on_change=self.on_value_change)
        self.r2_field = NumberField(control_grid, "상단 우측 R₂ (Ω)", 1000, on_change=self.on_value_change)
        self.ra_field = NumberField(control_grid, "검류계 내부저항 (Ω)", 100, on_change=self.on_value_change)
        control_grid.add(self.vs_field)
        control_grid.add(self.r1_field)
        control_grid.add(self.r2_field)
        control_grid.add(self.ra_field)

        rx_panel = tk.Frame(control_grid, bg=theme.PANEL)
        tk.Label(rx_panel, text="Rₓ (Ω)", bg=theme.PANEL, fg=theme.MUTED_2, font=font(9, "bold")).pack(anchor="w")
        btns = tk.Frame(rx_panel, bg=theme.PANEL)
        btns.pack(fill="x", pady=(4, 4))
        make_button(btns, "Rₓ 저항값 확인", self.toggle_rx).pack(side="left", fill="x", expand=True, padx=(0, 6))
        make_button(btns, "Rₓ 값 갱신", self.refresh_rx, "secondary").pack(side="left", fill="x", expand=True)
        self.rx_output = tk.Label(rx_panel, text="", bg=theme.GRAPH_BG, fg=theme.TEXT_2, font=font(10, "bold"), padx=8, pady=4)
        self.rx_output.pack(fill="x")
        control_grid.add(rx_panel)

        top_frame.set_children(left_stack, right_stack)


    def vs(self):
        return self.vs_field.get_float(default=math.nan)

    def r1(self):
        return self.r1_field.get_float(default=math.nan, positive=True)

    def r2(self):
        return self.r2_field.get_float(default=math.nan, positive=True)

    def rv(self):
        return self.rv_slider.get()

    def ra(self):
        return self.ra_field.get_float(default=math.nan, positive=True)

    def on_rv_slider(self):
        if not self._syncing:
            self._syncing = True
            self.rv_field.set_value(self.rv_slider.get(), silent=True)
            self._syncing = False
        self.on_value_change()

    def on_rv_field(self):
        if self._syncing:
            return
        value = self.rv_field.get_float(default=None, positive=True)
        if value is None or not math.isfinite(value):
            self.on_value_change()
            return
        expanded = self._expand_rv_for_value(value)
        self._syncing = True
        self.rv_slider.set(value, silent=True)
        self.rv_field.set_value(self.rv_slider.get(), silent=True)
        self._syncing = False
        if expanded and self.toast:
            self.toast.show("Wheatstone: 입력한 Rv 값을 포함하도록 슬라이더 범위를 확장했습니다.")
        self.on_value_change()

    def rx_estimate(self):
        r1 = self.r1()
        r2 = self.r2()
        rv = self.rv()
        if r1 > 0 and r2 > 0 and rv > 0:
            return rv * r2 / r1
        return math.nan

    def ideal_rv(self):
        r1 = self.r1()
        r2 = self.r2()
        if r1 > 0 and r2 > 0 and self.rx > 0:
            return self.rx * r1 / r2
        return math.nan

    def range_status(self):
        ideal = self.ideal_rv()
        ok = math.isfinite(ideal) and ideal > 0
        possible = ok and self.rv_slider.min_value <= ideal <= self.rv_slider.max_value
        return {"ok": ok, "ideal": ideal, "possible": possible}

    def _range_labels(self, low, high):
        mid = (low + high) / 2
        return [f"{low:.2f}", f"{mid:.2f}", f"{high:.2f}"]

    def _auto_bounds(self, ideal):
        span = max(20.0, abs(ideal) * 0.08)
        low = max(RV_STEP, ideal - span)
        high = max(ideal + span, low + RV_STEP * 200)
        return low, high

    def _expand_rv_for_value(self, value):
        if self.rv_slider.min_value <= value <= self.rv_slider.max_value:
            return False
        low = max(RV_STEP, min(self.rv_slider.min_value, value * 0.4))
        high = max(self.rv_slider.max_value, value * 1.6, low + RV_STEP * 200)
        self.rv_slider.configure_range(low, high, step=RV_STEP, labels=self._range_labels(low, high), value=value, silent=True)
        return True

    def adjust_rv(self, delta):
        new_value = self.rv_slider.get() + delta
        new_value = max(self.rv_slider.min_value, min(self.rv_slider.max_value, new_value))
        self._syncing = True
        self.rv_slider.set(new_value, silent=True)
        self.rv_field.set_value(self.rv_slider.get(), silent=True)
        self._syncing = False
        self.on_value_change()

    def toggle_diagnostic(self):
        self.diagnostic_visible = not self.diagnostic_visible
        self.diagnostic_btn.configure(text="진단 정보 숨기기" if self.diagnostic_visible else "진단 정보 보기")
        self.update_range_info()

    def auto_range(self):
        status = self.range_status()
        if not status["ok"]:
            self.range_metric.set("입력값을 먼저 확인하세요.", theme.WARN)
            return
        low, high = self._auto_bounds(status["ideal"])
        current = self.rv_slider.get()
        if self.rx_visible or self.diagnostic_visible:
            next_value = status["ideal"]
        elif low <= current <= high:
            next_value = current
        else:
            next_value = max(low, min(high, status["ideal"] * 0.92))
        self._syncing = True
        self.rv_slider.configure_range(low, high, step=RV_STEP, labels=self._range_labels(low, high), value=next_value, silent=True)
        self.rv_field.set_value(self.rv_slider.get(), silent=True)
        self._syncing = False
        self.graph.clear()
        self.on_value_change()
        if self.toast:
            self.toast.show("Wheatstone: 평형점 주변으로 Rv 슬라이더 범위를 조정했습니다.")

    def update_range_info(self):
        status = self.range_status()
        if not status["ok"]:
            self.range_metric.set("R1, R2, Rx 값을 확인하세요.", theme.WARN)
            return
        if self.rx_visible or self.diagnostic_visible:
            ratio = self.r1() / self.r2() if self.r2() > 0 else math.nan
            text = (
                f"Rv* = {status['ideal']:.2f} Ω · "
                f"범위 {self.rv_slider.min_value:.2f}~{self.rv_slider.max_value:.2f} Ω · "
                f"{'가능' if status['possible'] else '범위 밖'} · R1/R2={ratio:.3g}"
            )
            self.range_metric.set(text, theme.GOOD if status["possible"] else theme.WARN)
        else:
            if status["possible"]:
                msg = "현재 Rv 범위 안에 평형점이 있습니다."
            elif status["ideal"] < self.rv_slider.min_value:
                msg = "현재 R1/R2 비율에서는 평형 Rv가 범위보다 낮습니다. R1/R2를 키우거나 Rv Auto Range를 누르세요."
            else:
                msg = "현재 R1/R2 비율에서는 평형 Rv가 범위보다 높습니다. R1/R2를 낮추거나 Rv Auto Range를 누르세요."
            self.range_metric.set(
                msg,
                theme.GOOD if status["possible"] else theme.WARN,
            )

    def on_value_change(self):
        self.update_range_info()
        self.diagram.redraw()

    def toggle_rx(self):
        self.rx_visible = not self.rx_visible
        self.rx_output.configure(text=f"{self.rx} Ω" if self.rx_visible else "")
        self.update_range_info()
        self.diagram.redraw()

    def refresh_rx(self):
        self.rx = random_rx()
        self.rx_visible = False
        self.rx_output.configure(text="")
        self.on_value_change()
        self.graph.clear()
        if self.toast:
            self.toast.show("Wheatstone: Rₓ 값이 갱신되었습니다.")

    def current(self):
        return wheatstone_current(self.vs(), self.r1(), self.r2(), self.rv(), self.rx, self.ra())

    def on_show(self):
        self.scroll.scroll_to_top()
        self._tick()

    def on_hide(self):
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None

    def _tick(self):
        value = self.current()
        if math.isfinite(value):
            self.graph.add_sample(value)
            self.current_metric.set(format_current(value), theme.TEXT_2)
            rx_est = self.rx_estimate()
            if math.isfinite(rx_est):
                if self.rx_visible:
                    err = abs(rx_est - self.rx) / self.rx * 100 if self.rx > 0 else math.nan
                    self.estimate_metric.set(f"Rx_est = {rx_est:.2f} Ω\n실제 Rx = {self.rx:.2f} Ω · 오차 {err:.3f}%")
                else:
                    self.estimate_metric.set(f"Rx_est = {rx_est:.2f} Ω\n식: Rx = Rv × R2 / R1")
            else:
                self.estimate_metric.set("R1, R2, Rv 값을 확인하세요.")
            status = self.range_status()
            self.update_range_info()
            abs_i = abs(value)
            rv_error = abs(self.rv() - status["ideal"]) if status["ok"] else math.inf
            success_by_rv = status["ok"] and status["possible"] and rv_error <= max(RV_STEP * 0.6, abs(status["ideal"]) * 1e-5)
            near_by_rv = status["ok"] and status["possible"] and rv_error <= max(RV_STEP * 5, abs(status["ideal"]) * 5e-5)
            if abs_i < 1e-6 or success_by_rv:
                self.balance_state.set("성공: 검류계 전류가 거의 0입니다.", theme.GOOD)
                self.hint.set("평형점입니다. 이때 Rx ≈ Rv × R2 / R1 관계를 확인하세요.", theme.GOOD)
            elif status["ok"] and not status["possible"]:
                self.balance_state.set("범위 밖: 현재 Rv 범위에서 완전 평형이 어렵습니다.", theme.WARN)
                self.hint.set("평형점 주변으로 확대를 누르거나 R1/R2 비율을 바꿔 평형점이 범위 안에 오게 하세요.", theme.WARN)
            elif abs_i < 50e-6 or near_by_rv:
                self.balance_state.set("거의 성공: 조금만 더 조절하세요.", theme.WARN)
                self.hint.set("Rv를 0.01 Ω 단위로 미세 조절해 전류 0선을 더 가까이 맞추세요.", theme.WARN)
            else:
                self.balance_state.set("미달: 아직 불평형입니다.", theme.TEXT_2)
                self.hint.set("직접 입력이나 ±0.01Ω 버튼으로 전류 0선에 더 가깝게 맞추세요.", theme.TEXT_2)
        else:
            self.current_metric.set("전압·저항 값을 확인하세요.", theme.WARN)
            self.estimate_metric.set("모든 저항과 전압 값을 확인하세요.", theme.WARN)
            self.balance_state.set("입력 오류", theme.WARN)
            self.hint.set("모든 저항은 0보다 커야 합니다.", theme.WARN)
        self.graph.draw()
        self._after_id = self.after(16, self._tick)
