import math
import random
import tkinter as tk

from .. import theme
from ..graphs import RollingGraph, format_current
from ..widgets import CanvasSlider, CompactMetricBox, DualColumnWorkbench, NumberField, Panel, ResponsiveControlGrid, ResponsiveTopFrame, ScaledCanvas, ScrollableFrame, SectionHeader, font, make_button


RX_MIN = 0.01
RX_MAX = 1.0


def random_rx():
    return round(RX_MIN + random.random() * (RX_MAX - RX_MIN), 4)


def format_resistance(value):
    if not math.isfinite(value):
        return "—"
    if abs(value) < 1:
        return f"{value * 1000:.2f} mΩ"
    if abs(value) < 1000:
        return f"{value:.4g} Ω"
    return f"{value / 1000:.3g} kΩ"


def kelvin_bridge_current(vs, P, Q, p, q, r, Rx, R, Ra):
    """원본 JS kelvinBridgeCurrent()의 4노드 행렬 풀이."""
    if not (P > 0 and Q > 0 and p > 0 and q > 0 and r > 0 and Rx > 0 and R > 0 and Ra > 0):
        return math.nan
    va = vs
    vc = 0.0
    g_rx = 1 / Rx
    g_r = 1 / R
    g_p_big = 1 / P
    g_q_big = 1 / Q
    g_p = 1 / p
    g_q = 1 / q
    g_conn = 1 / r
    g_a = 1 / Ra

    a00 = -(g_rx + g_conn + g_p)
    a01 = g_conn
    a02 = 0
    a03 = g_p
    a10 = g_conn
    a11 = -(g_conn + g_r + g_q)
    a12 = 0
    a13 = g_q
    a20 = 0
    a21 = 0
    a22 = -(g_p_big + g_q_big + g_a)
    a23 = g_a
    a30 = g_p
    a31 = g_q
    a32 = g_a
    a33 = -(g_p + g_q + g_a)

    b0 = -va * g_rx
    b1 = -vc * g_r
    b2 = -va * g_p_big
    b3 = 0.0

    matrix = [
        [a00, a01, a02, a03, b0],
        [a10, a11, a12, a13, b1],
        [a20, a21, a22, a23, b2],
        [a30, a31, a32, a33, b3],
    ]

    for col in range(4):
        max_row = col
        for row in range(col + 1, 4):
            if abs(matrix[row][col]) > abs(matrix[max_row][col]):
                max_row = row
        matrix[col], matrix[max_row] = matrix[max_row], matrix[col]
        if abs(matrix[col][col]) < 1e-30:
            return math.nan
        for row in range(col + 1, 4):
            factor = matrix[row][col] / matrix[col][col]
            for j in range(col, 5):
                matrix[row][j] -= factor * matrix[col][j]

    x = [0.0, 0.0, 0.0, 0.0]
    for i in range(3, -1, -1):
        s = matrix[i][4]
        for j in range(i + 1, 4):
            s -= matrix[i][j] * x[j]
        x[i] = s / matrix[i][i]

    vm = x[2]
    vn = x[3]
    return (vm - vn) / Ra


def kelvin_balance_rx(P, Q, p, q, r, R):
    if not (P > 0 and Q > 0 and p > 0 and q > 0 and r > 0 and R > 0):
        return math.nan
    return R * (P / Q) + r * ((P / Q) - (p / q)) * (q / (p + q + r))


def kelvin_required_R(P, Q, p, q, r, Rx):
    if not (P > 0 and Q > 0 and p > 0 and q > 0 and r > 0 and Rx > 0):
        return math.nan
    ratio = P / Q
    correction = r * (ratio - (p / q)) * (q / (p + q + r))
    if ratio <= 0:
        return math.nan
    required = (Rx - correction) / ratio
    return required if required > 0 else math.nan


class KelvinDiagram(ScaledCanvas):
    def __init__(self, parent, page):
        self.page = page
        super().__init__(parent, 700, 460, min_height=285, max_height=350, padding=1)

    def draw(self):
        p = self.page
        wire = theme.WIRE
        self.rect(10, 10, 680, 440, outline="#2b3847", fill=theme.PANEL_2, width=1.2)
        self.line(160, 110, 250, 110, fill=wire, width=3)
        self.resistor_h(250, 300, 110, fill=wire, width=3)
        self.line(300, 110, 340, 110, fill=wire, width=3)
        self.text(275, 90, "Rₓ" if not p.rx_visible else f"Rₓ={format_resistance(p.rx)}", color=theme.TEXT_2, size=13, weight="bold")

        self.line(340, 110, 352, 110, fill=wire, width=3)
        self.resistor_h(352, 408, 110, amp=7, fill=theme.WARN, width=3)
        self.line(408, 110, 420, 110, fill=wire, width=3)
        self.text(380, 84, f"r = {format_resistance(p.values()['r'])}", color=theme.WARN, size=12, weight="bold")
        self.line(420, 110, 540, 110, fill=wire, width=3)
        self.line(540, 110, 540, 390, fill=wire, width=3)

        self.line(540, 390, 460, 390, fill=wire, width=3)
        self.resistor_h(410, 460, 390, fill=wire, width=3)
        self.line(410, 390, 160, 390, fill=wire, width=3)
        self.text(455, 415, f"R={format_resistance(p.rv_slider.get())}", color=theme.TEXT_2, size=12, weight="bold")

        self.oval(160, 235, 35, outline=wire, width=3)
        self.line(160, 110, 160, 200, fill=wire, width=3)
        self.line(160, 270, 160, 390, fill=wire, width=3)
        self.text(160, 224, "+", color=theme.DANGER, size=15, weight="bold")
        self.text(160, 255, "−", color="#7aa2ff", size=15, weight="bold")
        self.text(160, 295, "전압원 (Vs)", color=theme.TEXT_2, size=11)

        for x, y in [(260, 110), (420, 110), (420, 390)]:
            self.oval(x, y, 5, fill=theme.NODE, outline="#dff1ff", width=1.5)

        self.line(260, 110, 260, 190, fill=wire, width=3)
        self.resistor_v(260, 190, 230, fill=wire, width=3)
        self.line(260, 230, 260, 250, fill=wire, width=3)
        self.text(240, 185, "P", color=theme.TEXT_2, size=13, weight="bold")
        self.line(260, 250, 260, 310, fill=wire, width=3)
        self.resistor_v(260, 310, 350, fill=wire, width=3)
        self.line(260, 350, 260, 390, fill=wire, width=3)
        self.text(240, 305, "Q", color=theme.TEXT_2, size=13, weight="bold")

        self.line(420, 110, 420, 190, fill=wire, width=3)
        self.resistor_v(420, 190, 230, fill=wire, width=3)
        self.line(420, 230, 420, 250, fill=wire, width=3)
        self.text(430, 185, "p", color=theme.TEXT_2, size=13, weight="bold")
        self.line(420, 250, 420, 310, fill=wire, width=3)
        self.resistor_v(420, 310, 350, fill=wire, width=3)
        self.line(420, 350, 420, 390, fill=wire, width=3)
        self.text(430, 305, "q", color=theme.TEXT_2, size=13, weight="bold")

        self.line(260, 250, 330, 250, fill=wire, width=3)
        self.oval(350, 250, 30, outline=wire, width=3)
        self.text(350, 255, "G", color=theme.TEXT_2, size=18, weight="bold")
        self.line(370, 250, 420, 250, fill=wire, width=3)
        self.text(350, 295, "검류계", color=theme.TEXT_2, size=12)
        self.text(350, 38, "Kelvin Double Bridge", color=theme.TEXT_2, size=16, weight="bold")


class KelvinPage(tk.Frame):
    title = "Kelvin 더블 브리지"

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
        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)
        root = self.scroll.inner
        root.columnconfigure(0, weight=1)
        tk.Label(root, text="낮은 저항에서 연결저항 r의 영향을 줄이며 표준저항 R로 평형을 찾습니다.", bg=theme.BG, fg=theme.MUTED_2, font=font(9)).grid(
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
        SectionHeader(diagram_panel, "회로도", "P/Q, p/q, r, R, Rx 구조").pack(fill="x")
        self.diagram = KelvinDiagram(diagram_panel, self)
        self.diagram.pack(fill="x", padx=6, pady=(0, 8))

        graph_panel = Panel(right_stack)
        graph_panel.pack(fill="x")
        SectionHeader(graph_panel, "검류계 전류 · 실시간 그래프").pack(fill="x")
        self.graph = RollingGraph(graph_panel, mode="signed", height=theme.GRAPH_HEIGHT)
        self.graph.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.current_metric = CompactMetricBox(graph_panel, "검류계 전류 I (시간값)", "슬라이더로 R을 조절하면 표시됩니다.")
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
        SectionHeader(primary_controls_panel, "핵심 조작", "표준저항 R을 조절해 낮은 저항의 평형점을 찾습니다.").pack(fill="x")
        focus_grid = ResponsiveControlGrid(primary_controls_panel, columns=2, breakpoint=420)
        focus_grid.pack(fill="x", padx=8, pady=(0, 8))
        self.rv_slider = CanvasSlider(
            focus_grid,
            "R 표준저항 조절 · 저저항 평형",
            0.01,
            1.0,
            0.001,
            0.25,
            formatter=format_resistance,
            labels=["10 mΩ", "505 mΩ", "1 Ω"],
            on_change=self.on_r_slider,
            compact=True,
        )
        focus_grid.add(self.rv_slider, span=2)
        self.R_field = NumberField(focus_grid, "R 직접 입력 (Ω)", 0.25, on_change=self.on_r_field)
        self.range_metric = CompactMetricBox(focus_grid, "평형 가능성", "현재 R 범위 판정 대기")
        self.calc_metric = CompactMetricBox(focus_grid, "Rx 추정값(Kelvin 보정)", "—")
        self.hint = CompactMetricBox(focus_grid, "핵심 힌트", "이 값이 Kelvin 브리지로 추정한 미지저항입니다.")
        self.two_wire_metric = CompactMetricBox(focus_grid, "2선식 단순 추정", "리드선 r 포함 비교값")
        self.error_metric = CompactMetricBox(focus_grid, "오차 비교", "실제 Rx 공개 후 표시")
        focus_grid.add(self.R_field)
        focus_grid.add(self.range_metric)
        focus_grid.add(self.calc_metric)
        focus_grid.add(self.hint)
        focus_grid.add(self.two_wire_metric)
        focus_grid.add(self.error_metric)
        range_buttons = tk.Frame(focus_grid, bg=theme.PANEL)
        make_button(range_buttons, "R 범위 자동 조정", self.auto_range, "accent").pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.diagnostic_btn = make_button(range_buttons, "진단 정보 보기", self.toggle_diagnostic, "secondary")
        self.diagnostic_btn.pack(side="left", fill="x", expand=True)
        focus_grid.add(range_buttons, span=2)
        goal_panel = Panel(left_stack)
        goal_panel.pack(fill="x", pady=(0, theme.CARD_GAP))

        SectionHeader(goal_panel, "실습 목표", "낮은 저항 측정에서 리드선/접촉저항 r의 영향을 Kelvin 비율팔로 줄입니다.").pack(fill="x")
        tk.Label(
            goal_panel,
            text="1. 표준저항 R을 조절해 검류계 전류가 0에 가까운 지점을 찾습니다.\n2. P/Q와 p/q를 같게 맞추면 연결저항 r의 영향이 줄어듭니다.\n3. Kelvin 보정 추정값과 2선식 단순 추정값을 비교합니다.",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(8),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 8))

        basic_controls_panel = Panel(left_stack)
        basic_controls_panel.pack(fill="x")
        SectionHeader(basic_controls_panel, "기본 비율값 조절", "P, Q, p, q와 연결저항 r을 같은 영역에서 조정합니다.").pack(fill="x")

        control_grid = ResponsiveControlGrid(basic_controls_panel, columns=3, breakpoint=560)
        control_grid.pack(fill="x", padx=8, pady=(0, 10))

        self.vs_field = NumberField(control_grid, "전원 전압 (V)", 0.2, on_change=self.on_value_change)
        self.P_field = NumberField(control_grid, "외부 비율팔 P (Ω)", 1000, on_change=self.on_value_change)
        self.Q_field = NumberField(control_grid, "외부 비율팔 Q (Ω)", 1000, on_change=self.on_value_change)
        self.p_field = NumberField(control_grid, "내부 비율팔 p (Ω)", 1000, on_change=self.on_value_change)
        self.q_field = NumberField(control_grid, "내부 비율팔 q (Ω)", 1000, on_change=self.on_value_change)
        self.r_field = NumberField(control_grid, "리드선/연결저항 r (Ω)", 0.02, on_change=self.on_value_change)
        for field in (self.vs_field, self.P_field, self.Q_field, self.p_field, self.q_field, self.r_field):
            control_grid.add(field)

        rx_panel = tk.Frame(control_grid, bg=theme.PANEL)
        tk.Label(rx_panel, text="Rₓ (Ω)", bg=theme.PANEL, fg=theme.MUTED_2, font=font(9, "bold")).pack(anchor="w")
        btns = tk.Frame(rx_panel, bg=theme.PANEL)
        btns.pack(fill="x", pady=(4, 4))
        make_button(btns, "Rₓ 저항값 확인", self.toggle_rx).pack(side="left", fill="x", expand=True, padx=(0, 6))
        make_button(btns, "Rₓ 값 갱신", self.refresh_rx, "secondary").pack(side="left", fill="x", expand=True)
        self.rx_output = tk.Label(rx_panel, text="", bg=theme.GRAPH_BG, fg=theme.TEXT_2, font=font(10, "bold"), padx=8, pady=4)
        self.rx_output.pack(fill="x")
        control_grid.add(rx_panel)

        self.ra_field = NumberField(control_grid, "검류계 내부저항 (Ω)", 100, on_change=self.on_value_change)
        control_grid.add(self.ra_field)
        tk.Label(
            basic_controls_panel,
            text="R: 10 mΩ~1 Ω · P/Q = p/q이면 연결저항 r의 영향이 줄어듭니다.",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(8),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))
        self.update_calc()
        top_frame.set_children(left_stack, right_stack)

    def values(self):
        return {
            "vs": self.vs_field.get_float(math.nan),
            "P": self.P_field.get_float(math.nan, positive=True),
            "Q": self.Q_field.get_float(math.nan, positive=True),
            "p": self.p_field.get_float(math.nan, positive=True),
            "q": self.q_field.get_float(math.nan, positive=True),
            "r": self.r_field.get_float(math.nan, positive=True),
            "R": self.rv_slider.get(),
            "Ra": self.ra_field.get_float(math.nan, positive=True),
        }

    def on_r_slider(self):
        if not self._syncing:
            self._syncing = True
            self.R_field.set_value(round(self.rv_slider.get(), 6), silent=True)
            self._syncing = False
        self.on_value_change()

    def on_r_field(self):
        if self._syncing:
            return
        value = self.R_field.get_float(default=None, positive=True)
        if value is None or not math.isfinite(value):
            self.on_value_change()
            return
        expanded = self._expand_r_for_value(value)
        self._syncing = True
        self.rv_slider.set(value, silent=True)
        self.R_field.set_value(round(self.rv_slider.get(), 6), silent=True)
        self._syncing = False
        if expanded and self.toast:
            self.toast.show("Kelvin: 입력한 R 값을 포함하도록 슬라이더 범위를 확장했습니다.")
        self.on_value_change()

    def _range_labels(self, low, high):
        return [format_resistance(low), format_resistance((low + high) / 2), format_resistance(high)]

    def _expand_r_for_value(self, value):
        if self.rv_slider.min_value <= value <= self.rv_slider.max_value:
            return False
        low = max(0.001, min(self.rv_slider.min_value, value * 0.4))
        high = max(self.rv_slider.max_value, value * 1.6, low + 0.001 * 60)
        self.rv_slider.configure_range(low, high, step=0.001, labels=self._range_labels(low, high), value=value, silent=True)
        return True

    def _auto_bounds(self, ideal):
        low = max(0.001, ideal * 0.4)
        high = max(ideal * 1.6, low + 0.001 * 60)
        return low, high

    def ideal_R(self):
        v = self.values()
        return kelvin_required_R(v["P"], v["Q"], v["p"], v["q"], v["r"], self.rx)

    def range_status(self):
        v = self.values()
        ideal = kelvin_required_R(v["P"], v["Q"], v["p"], v["q"], v["r"], self.rx)
        ok = math.isfinite(ideal) and ideal > 0
        possible = ok and self.rv_slider.min_value <= ideal <= self.rv_slider.max_value
        reason = ""
        if not ok:
            if all(math.isfinite(v[k]) and v[k] > 0 for k in ("P", "Q", "p", "q", "r")):
                reason = "현재 P/Q, p/q, r 조건에서는 양의 R 평형점이 나오지 않습니다. P/Q와 p/q를 가깝게 맞추세요."
            else:
                reason = "P, Q, p, q, r 값은 모두 0보다 커야 합니다."
        return {"ok": ok, "ideal": ideal, "possible": possible, "reason": reason}

    def theory_visible(self):
        return self.rx_visible or self.diagnostic_visible

    def toggle_diagnostic(self):
        self.diagnostic_visible = not self.diagnostic_visible
        self.diagnostic_btn.configure(text="진단 정보 숨기기" if self.diagnostic_visible else "진단 정보 보기")
        self.update_calc()

    def auto_range(self):
        status = self.range_status()
        if not status["ok"]:
            self.range_metric.set(status.get("reason") or "입력값을 먼저 확인하세요.", theme.WARN)
            return
        low, high = self._auto_bounds(status["ideal"])
        current = self.rv_slider.get()
        if self.theory_visible():
            next_value = status["ideal"]
        elif low <= current <= high:
            next_value = current
        else:
            next_value = max(low, min(high, status["ideal"] * 0.92))
        self._syncing = True
        self.rv_slider.configure_range(low, high, step=0.001, labels=self._range_labels(low, high), value=next_value, silent=True)
        self.R_field.set_value(round(self.rv_slider.get(), 6), silent=True)
        self._syncing = False
        self.graph.clear()
        self.on_value_change()
        if self.toast:
            self.toast.show("Kelvin: 평형점 주변으로 R 슬라이더 범위를 조정했습니다.")

    def on_value_change(self):
        self.update_calc()
        self.diagram.redraw()

    def update_calc(self):
        v = self.values()
        rx_calc = kelvin_balance_rx(v["P"], v["Q"], v["p"], v["q"], v["r"], v["R"])
        self.calc_metric.set(format_resistance(rx_calc) if math.isfinite(rx_calc) else "—")
        two_wire = self.rx + v["r"] if math.isfinite(v["r"]) else math.nan
        self.two_wire_metric.set(format_resistance(two_wire) if math.isfinite(two_wire) else "—")
        status = self.range_status()
        if status["ok"]:
            if self.theory_visible():
                ratio_big = v["P"] / v["Q"] if v["Q"] > 0 else math.nan
                ratio_small = v["p"] / v["q"] if v["q"] > 0 else math.nan
                self.range_metric.set(
                    f"R* = {format_resistance(status['ideal'])} · 범위 {format_resistance(self.rv_slider.min_value)}~{format_resistance(self.rv_slider.max_value)} · {'가능' if status['possible'] else '범위 밖'} · P/Q={ratio_big:.3g}, p/q={ratio_small:.3g}",
                    theme.GOOD if status["possible"] else theme.WARN,
                )
            else:
                if status["possible"]:
                    msg = "현재 R 범위 안에 평형점이 있습니다."
                elif status["ideal"] < self.rv_slider.min_value:
                    msg = "현재 비율 조건에서는 평형 R이 범위보다 낮습니다. P/Q, p/q를 맞추거나 R Auto Range를 누르세요."
                else:
                    msg = "현재 비율 조건에서는 평형 R이 범위보다 높습니다. P/Q, p/q를 맞추거나 R Auto Range를 누르세요."
                self.range_metric.set(
                    msg,
                    theme.GOOD if status["possible"] else theme.WARN,
                )
        else:
            self.range_metric.set(status.get("reason") or "P, Q, p, q, r 값을 확인하세요.", theme.WARN)
        if self.rx_visible and math.isfinite(rx_calc):
            two_err = abs(two_wire - self.rx) / self.rx * 100 if self.rx > 0 else math.nan
            kelvin_err = abs(rx_calc - self.rx) / self.rx * 100 if self.rx > 0 else math.nan
            self.error_metric.set(f"2선식 {two_err:.2f}% / Kelvin {kelvin_err:.2f}%")
        else:
            self.error_metric.set("실제 Rx 공개 후 오차율 표시")
        if math.isfinite(rx_calc):
            ratio_delta = abs((v["P"] / v["Q"]) - (v["p"] / v["q"])) if v["Q"] > 0 and v["q"] > 0 else math.inf
            correction = v["r"] * ((v["P"] / v["Q"]) - (v["p"] / v["q"])) * (v["q"] / (v["p"] + v["q"] + v["r"])) if all(v[k] > 0 for k in ("P", "Q", "p", "q", "r")) else math.nan
            if ratio_delta < 1e-6:
                self.hint.set("P/Q = p/q: 연결저항 r 영향이 거의 소거됩니다.", theme.GOOD)
            elif self.theory_visible() and math.isfinite(correction):
                self.hint.set(f"P/Q와 p/q 차이 {ratio_delta:.4g} · 보정항 {format_resistance(correction)}", theme.WARN)
            else:
                self.hint.set("P/Q와 p/q가 다르면 r 보정항이 커집니다.", theme.WARN)

    def toggle_rx(self):
        self.rx_visible = not self.rx_visible
        self.rx_output.configure(text=format_resistance(self.rx) if self.rx_visible else "")
        self.update_calc()
        self.diagram.redraw()

    def refresh_rx(self):
        self.rx = random_rx()
        self.rx_visible = False
        self.rx_output.configure(text="")
        self.on_value_change()
        self.graph.clear()
        if self.toast:
            self.toast.show("Kelvin: Rₓ 값이 갱신되었습니다.")

    def current(self):
        v = self.values()
        return kelvin_bridge_current(v["vs"], v["P"], v["Q"], v["p"], v["q"], v["r"], self.rx, v["R"], v["Ra"])

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
            self.current_metric.set(format_current(value))
        else:
            self.current_metric.set("전압·저항 값을 확인하세요.", theme.WARN)
        self.graph.draw()
        self._after_id = self.after(16, self._tick)
