import math
import random
import tkinter as tk

from .. import theme
from ..graphs import RollingGraph, format_auto_current
from ..widgets import CanvasSlider, CollapsibleSection, CompactHintBar, CompactMetricBox, DualColumnWorkbench, NumberField, Panel, ResponsiveControlGrid, ResponsiveTopFrame, ScaledCanvas, ScrollableFrame, SectionHeader, font, make_button


R3_MIN = 100
R3_MAX = 400
R3_STEP = 0.1
RV_MIN = 500
RV_MAX = 10000
RV_STEP = 0.1
LX_MIN_MH = 100
LX_MAX_MH = 400
RX_MIN = 40
RX_MAX = 200


def random_unknown():
    lx_mh = round((LX_MIN_MH + random.random() * (LX_MAX_MH - LX_MIN_MH)) * 2) / 2
    rx = random.randint(RX_MIN, RX_MAX)
    return {"lx_mh": lx_mh, "lx_h": lx_mh / 1000, "rx": rx}


def maxwell_detector_current(vs, freq, r2, cap_uf, rd, r3, rv, lx_h, rx):
    """원본 solveDetectorCurrent()의 Maxwell bridge 복소 노드 해석."""
    c_f = cap_uf * 1e-6
    if not (
        math.isfinite(vs)
        and freq > 0
        and r2 > 0
        and c_f > 0
        and rd > 0
        and r3 > 0
        and rv > 0
        and lx_h > 0
        and rx > 0
    ):
        return {"ok": False, "message": "전압, 주파수, 저항, 커패시터 값을 0보다 큰 값으로 입력하세요."}

    omega = 2 * math.pi * freq
    vt = complex(vs, 0)
    zx = complex(rx, omega * lx_h)
    zs = 1 / (complex(1 / rv, 0) + complex(0, omega * c_f))
    yx = 1 / zx
    y2 = complex(1 / r2, 0)
    y3 = complex(1 / r3, 0)
    ys = 1 / zs
    yd = complex(1 / rd, 0)

    A = yx + y3 + yd
    B = -yd
    C = -yd
    D = y2 + ys + yd
    E = yx * vt
    F = y2 * vt
    det = A * D - B * C
    if abs(det) < 1e-30:
        return {"ok": False, "message": "계산 행렬이 불안정합니다. 회로 값을 확인하세요."}

    va = (E * D - B * F) / det
    vb = (A * F - E * C) / det
    detector = (va - vb) / complex(rd, 0)
    return {
        "ok": True,
        "Id": detector,
        "mag": abs(detector),
        "phase": math.degrees(math.atan2(detector.imag, detector.real)),
        "l_est_h": c_f * r2 * r3,
        "r_est": (r2 * r3) / rv,
        "params": {"vs": vs, "freq": freq, "r2": r2, "cap_f": c_f, "rd": rd, "r3": r3, "rv": rv},
    }


def maxwell_ideal_values(lx_h, rx, r2, cap_uf):
    c_f = cap_uf * 1e-6
    if not (lx_h > 0 and rx > 0 and r2 > 0 and c_f > 0):
        return {"ok": False, "message": "Lx, Rx, R2, C 값은 모두 0보다 커야 합니다."}
    r3 = lx_h / (c_f * r2)
    rv = (r2 * r3) / rx
    return {"ok": True, "r3": r3, "rv": rv}


def format_ohm(value):
    if not math.isfinite(value):
        return "-"
    if abs(value) >= 1000:
        return f"{round(value):d} Ω"
    return f"{value:.1f} Ω"


def format_mh(h):
    return "-" if not math.isfinite(h) else f"{h * 1000:.2f} mH"


def format_uf(f):
    return "-" if not math.isfinite(f) else f"{f * 1e6:.3f} µF"


def balance_hint(result):
    mag = result["mag"]
    re = abs(result["Id"].real)
    im = abs(result["Id"].imag)
    if mag < 1e-6:
        return "거의 완전 평형입니다. a-b 전위차가 매우 작고 검출기 전류가 1 µA 미만입니다.", True
    if mag < 10e-6:
        return "매우 좋은 평형입니다. 더 줄이고 싶다면 R3와 Rv를 아주 조금씩 번갈아 조절하세요.", True
    if im > re * 1.35:
        return "리액턴스성 불평형이 큽니다. 먼저 R3를 조절해 Im(Id)를 줄인 뒤 Rv로 마무리하세요.", False
    if re > im * 1.35:
        return "저항성 불평형이 큽니다. 먼저 Rv를 조절해 Re(Id)를 줄인 뒤 R3로 다시 확인하세요.", False
    return "저항성/리액턴스성 불평형이 함께 남아 있습니다. R3와 Rv를 번갈아 조절하며 |Id|의 최솟점을 찾으세요.", False


class MaxwellDiagram(ScaledCanvas):
    def __init__(self, parent, page):
        self.page = page
        super().__init__(parent, 840, 470, min_height=300, max_height=360, padding=1)

    def draw(self):
        p = self.page
        wire = theme.WIRE
        self.rect(10, 10, 820, 450, outline="#2b3847", fill=theme.PANEL_2, width=1.2)
        self.text(420, 38, "Maxwell 브리지", color=theme.TEXT_2, size=18, weight="bold")

        self.line(310, 82, 610, 82, fill=wire, width=3)
        self.line(310, 372, 610, 372, fill=wire, width=3)
        for x, y in [(460, 82), (460, 372), (310, 225), (610, 225)]:
            self.oval(x, y, 6, fill=theme.NODE, outline="#dff1ff", width=2)
        self.text(474, 78, "상단", color=theme.DANGER, size=12, anchor="w", weight="bold")
        self.text(474, 390, "하단", color="#7aa2ff", size=12, anchor="w", weight="bold")
        self.text(288, 214, "a", color=theme.BLUE, size=15, weight="bold")
        self.text(626, 214, "b", color=theme.BLUE, size=15, weight="bold")

        self.line(460, 82, 218, 82, fill=wire, width=3)
        self.line(218, 82, 218, 187, fill=wire, width=3)
        self.line(218, 263, 218, 372, fill=wire, width=3)
        self.line(218, 372, 460, 372, fill=wire, width=3)
        self.oval(218, 225, 38, fill="#111821", outline=wire, width=3)
        self.oval(218, 225, 29, outline="#334252", width=1)
        self.poly([(194, 225), (201, 208), (218, 225), (226, 242), (243, 225)], fill=wire, width=2.8, smooth=True)
        self.rect(36, 179, 130, 84, outline="#405064", fill="#0f1419", width=1)
        self.text(51, 200, "교류 전원", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(51, 226, f"Vs = {p.vs():.2f} Vrms", color=theme.TEXT_2, size=11, anchor="w")
        self.text(51, 249, f"f = {p.freq():.0f} Hz", color=theme.MUTED_2, size=10, anchor="w")

        self.line(310, 82, 310, 104, fill=wire, width=3)
        self.resistor_v(310, 104, 155, fill=wire, width=2.8)
        self.line(310, 155, 310, 168, fill=wire, width=3)
        self._coil(310, 168, 224)
        self.line(310, 224, 310, 225, fill=wire, width=3)
        self.rect(78, 102, 176, 72, outline="#405064", fill="#0f1419", width=1)
        zx = f"Lx = {p.unknown['lx_mh']:.1f} mH / Rx = {p.unknown['rx']} Ω" if p.unknown_visible else "Lx = ??? / Rx = ???"
        self.text(93, 124, "Zₓ 미지 코일", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(93, 150, zx, color=theme.TEXT_2, size=10, anchor="w")

        self.line(310, 225, 310, 260, fill=wire, width=3)
        self.resistor_v(310, 260, 318, fill=wire, width=2.8)
        self.line(310, 318, 310, 372, fill=wire, width=3)
        self.rect(118, 274, 132, 62, outline="#405064", fill="#0f1419", width=1)
        self.text(133, 296, "R₃", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(133, 319, f"{p.r3_slider.get():.1f} Ω", color=theme.TEXT_2, size=10, anchor="w")

        self.line(610, 82, 610, 122, fill=wire, width=3)
        self.resistor_v(610, 122, 180, fill=wire, width=2.8)
        self.line(610, 180, 610, 225, fill=wire, width=3)
        self.rect(650, 118, 134, 62, outline="#405064", fill="#0f1419", width=1)
        self.text(665, 140, "R₂", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(665, 163, format_ohm(p.r2()), color=theme.TEXT_2, size=10, anchor="w")

        self.line(610, 225, 610, 250, fill=wire, width=3)
        self.line(578, 250, 642, 250, fill=wire, width=2.4)
        self.line(578, 334, 642, 334, fill=wire, width=2.4)
        self.line(610, 334, 610, 372, fill=wire, width=3)
        self.line(578, 250, 578, 268, fill=wire, width=2.4)
        self.resistor_v(578, 268, 320, fill=wire, width=2.6)
        self.line(578, 320, 578, 334, fill=wire, width=2.4)
        self.line(642, 250, 642, 278, fill=wire, width=2.4)
        self.line(626, 278, 658, 278, fill=wire, width=2.4)
        self.line(626, 293, 658, 293, fill=wire, width=2.4)
        self.line(642, 293, 642, 334, fill=wire, width=2.4)
        self.rect(660, 266, 154, 76, outline="#405064", fill="#0f1419", width=1)
        self.text(675, 288, "Zₛ 표준 arm", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(675, 315, f"Rv = {format_ohm(p.rv_slider.get())} / C = {p.cap():.3g} µF", color=theme.TEXT_2, size=9, anchor="w")

        self.line(316, 225, 404, 225, fill=wire, width=3)
        self.line(516, 225, 604, 225, fill=wire, width=3)
        self.oval(460, 225, 52, fill="#111821", outline=wire, width=3)
        self.oval(460, 225, 39, outline="#334252", width=1)
        self.text(460, 235, "A", color=theme.TEXT_2, size=30, weight="bold")
        self.text(460, 292, "검출기", color=theme.TEXT_2, size=13, weight="bold")
        self.text(460, 315, f"Rd = {format_ohm(p.rd())}", color=theme.MUTED_2, size=11)

    def _coil(self, x, y1, y2):
        pts = []
        turns = 4
        step = (y2 - y1) / turns
        for i in range(turns):
            cy = y1 + i * step + step / 2
            pts.extend([x, y1 + i * step, x - 24, cy, x, y1 + (i + 1) * step, x + 24, cy, x, y1 + (i + 1) * step])
        # Tk smooth line approximates the coil loops well enough for the original symbol.
        coords = []
        for i in range(0, len(pts), 2):
            coords.extend(self.p(pts[i], pts[i + 1]))
        self.create_line(*coords, fill=theme.WIRE, width=self.sw(2.6), smooth=True)


class MaxwellPage(tk.Frame):
    title = "Maxwell 브리지"

    def __init__(self, parent, toast=None):
        super().__init__(parent, bg=theme.BG)
        self.toast = toast
        self.unknown = random_unknown()
        self.unknown_visible = False
        self.diagnostic_visible = False
        self._after_id = None
        self._build()
        self.set_initial_dial_near_unknown()

    def _build(self):
        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)
        root = self.scroll.inner
        root.columnconfigure(0, weight=1)
        tk.Label(root, text="R₃와 Rᵥ를 번갈아 조절해 검출기 전류 |Id|를 0에 가깝게 맞춥니다.", bg=theme.BG, fg=theme.MUTED_2, font=font(9)).grid(
            row=0, column=0, sticky="w", padx=theme.PAGE_PAD_X, pady=(6, 4)
        )
        top_frame = DualColumnWorkbench(root, breakpoint=1080, left_weight=3, right_weight=2)
        top_frame.grid(row=1, column=0, sticky="ew", padx=theme.PAGE_PAD_X, pady=(0, 14))

        left_stack = tk.Frame(top_frame, bg=theme.BG)
        right_stack = tk.Frame(top_frame, bg=theme.BG)
        left_stack.columnconfigure(0, weight=1)
        right_stack.columnconfigure(0, weight=1)

        diagram_panel = Panel(left_stack)
        diagram_panel.grid(row=0, column=0, sticky="ew", pady=(0, theme.CARD_GAP))
        SectionHeader(diagram_panel, "회로도", "R₃는 주로 Lx, Rᵥ는 주로 Rx 평형에 영향").pack(fill="x")
        self.diagram = MaxwellDiagram(diagram_panel, self)
        self.diagram.pack(fill="x", padx=6, pady=(0, 8))

        graph_panel = Panel(right_stack)
        graph_panel.grid(row=0, column=0, sticky="ew", pady=(0, theme.CARD_GAP))
        SectionHeader(graph_panel, "검출기 전류 · 실시간 그래프").pack(fill="x")
        self.graph = RollingGraph(graph_panel, mode="magnitude", height=theme.GRAPH_HEIGHT_MAXWELL)
        self.graph.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        tk.Label(
            graph_panel,
            text="가로축: 최근 12 s · 세로축: |Id|",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(9),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 6))

        basic_controls_panel = Panel(left_stack)
        basic_controls_panel.grid(row=1, column=0, sticky="ew")
        SectionHeader(basic_controls_panel, "기본 회로 값 조절", "Vs, f, R₂, C, Rd와 미지 코일을 확인합니다.").pack(fill="x")

        basic_grid = ResponsiveControlGrid(basic_controls_panel, columns=3, breakpoint=520)
        basic_grid.pack(fill="x", padx=8, pady=(0, 10))

        self.vs_field = NumberField(basic_grid, "전원 전압 Vs (Vrms)", 5, on_change=self.on_value_change)
        self.freq_field = NumberField(basic_grid, "주파수 f (Hz)", 1000, on_change=self.on_value_change)
        self.r2_field = NumberField(basic_grid, "R₂ 고정 저항 (Ω)", 1000, on_change=self.on_value_change)
        self.cap_field = NumberField(basic_grid, "C 표준 커패시터 (µF)", 1.0, on_change=self.on_value_change)
        self.rd_field = NumberField(basic_grid, "검출기 저항 Rᵈ (Ω)", 500, on_change=self.on_value_change)
        for field in (self.vs_field, self.freq_field, self.r2_field, self.cap_field, self.rd_field):
            basic_grid.add(field)

        unknown_panel = tk.Frame(basic_grid, bg=theme.PANEL)
        tk.Label(unknown_panel, text="숨겨진 미지 코일", bg=theme.PANEL, fg=theme.MUTED_2, font=font(9, "bold")).pack(anchor="w")
        btns = tk.Frame(unknown_panel, bg=theme.PANEL)
        btns.pack(fill="x", pady=(4, 4))
        make_button(btns, "Lₓ, Rₓ 값 확인", self.toggle_unknown).pack(side="left", fill="x", expand=True, padx=(0, 6))
        make_button(btns, "미지 코일 값 갱신", self.refresh_unknown, "secondary").pack(side="left", fill="x", expand=True)
        self.unknown_output = tk.Label(
            unknown_panel,
            text="Lx = ??? mH / Rx = ??? Ω",
            bg=theme.GRAPH_BG,
            fg=theme.TEXT_2,
            font=font(10, "bold"),
            padx=8,
            pady=4,
        )
        self.unknown_output.pack(fill="x")
        basic_grid.add(unknown_panel, span=3)

        slider_controls_panel = Panel(right_stack)
        slider_controls_panel.grid(row=1, column=0, sticky="ew")
        SectionHeader(slider_controls_panel, "평형 슬라이더 조절", "두 값은 완전히 독립은 아니므로 번갈아 조절합니다.").pack(fill="x")
        slider_box = tk.Frame(slider_controls_panel, bg=theme.PANEL)
        slider_box.pack(fill="x", padx=12, pady=(0, 10))
        slider_box.columnconfigure(0, weight=1, uniform="maxwell_sliders")
        slider_box.columnconfigure(1, weight=1, uniform="maxwell_sliders")
        self.r3_slider = CanvasSlider(
            slider_box,
            "R₃ 인덕턴스 평형용 (Ω)",
            R3_MIN,
            R3_MAX,
            R3_STEP,
            250,
            formatter=lambda v: f"{v:.1f} Ω",
            labels=["100", "250", "400"],
            on_change=self.on_r3_slider,
            compact=True,
        )
        self.r3_slider.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.r3_field = NumberField(slider_box, "R₃ 직접 입력 (Ω)", 250, on_change=self.on_r3_field, digits=1)
        self.r3_field.grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=(4, 0))
        self.rv_slider = CanvasSlider(
            slider_box,
            "Rᵥ 저항 평형용 (Ω)",
            RV_MIN,
            RV_MAX,
            RV_STEP,
            2500,
            formatter=lambda v: f"{v:.1f} Ω",
            labels=["500", "5250", "10000"],
            on_change=self.on_rv_slider,
            compact=True,
        )
        self.rv_slider.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self.rv_field = NumberField(slider_box, "Rᵥ 직접 입력 (Ω)", 2500, on_change=self.on_rv_field, digits=1)
        self.rv_field.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=(4, 0))
        make_button(slider_controls_panel, "평형점 기준 슬라이더 범위 자동 조정", self.auto_range, "accent").pack(
            fill="x", padx=12, pady=(0, 10)
        )

        readout_panel = Panel(right_stack)
        readout_panel.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        SectionHeader(readout_panel, "검출기 전류와 평형 판정").pack(fill="x")
        self.diagnostic_btn = make_button(readout_panel, "진단 정보 보기", self.toggle_diagnostic, "secondary")
        self.diagnostic_btn.pack(fill="x", padx=10, pady=(0, 6))
        read_grid = tk.Frame(readout_panel, bg=theme.PANEL)
        read_grid.pack(fill="x", padx=8, pady=(0, 8))
        for i in range(3):
            read_grid.columnconfigure(i, weight=1)
        self.metrics = {
            "mag": CompactMetricBox(read_grid, "|Id|", "-"),
            "phase": CompactMetricBox(read_grid, "angle(Id)", "-"),
            "range": CompactMetricBox(read_grid, "평형 가능", "-"),
        }
        for idx, box in enumerate(self.metrics.values()):
            box.grid(row=idx // 3, column=idx % 3, sticky="ew", padx=4, pady=3)
        diag = CollapsibleSection(readout_panel, "진단 정보와 현재 슬라이더 범위", initially_open=False)
        diag.pack(fill="x", padx=10, pady=(0, 8))
        diag_grid = tk.Frame(diag.body, bg=theme.PANEL)
        diag_grid.pack(fill="x")
        for i in range(3):
            diag_grid.columnconfigure(i, weight=1)
        self.metrics.update({
            "re": CompactMetricBox(diag_grid, "Re(Id)", "-"),
            "im": CompactMetricBox(diag_grid, "Im(Id)", "-"),
            "l": CompactMetricBox(diag_grid, "Lx 추정", "-"),
            "r": CompactMetricBox(diag_grid, "Rx 추정", "-"),
            "ideal_r3": CompactMetricBox(diag_grid, "이론 R3*", "-"),
            "ideal_rv": CompactMetricBox(diag_grid, "이론 Rv*", "-"),
            "r3_range": CompactMetricBox(diag_grid, "R3 범위", "-"),
            "rv_range": CompactMetricBox(diag_grid, "Rv 범위", "-"),
        })
        for idx, key in enumerate(("re", "im", "l", "r", "ideal_r3", "ideal_rv", "r3_range", "rv_range")):
            self.metrics[key].grid(row=idx // 3, column=idx % 3, sticky="ew", padx=4, pady=3)
        self.balance = CompactHintBar(readout_panel, "조절 안내", "회로 값을 조절하세요.", wraplength=420)
        self.balance.pack(fill="x", padx=10, pady=(0, 10))
        top_frame.set_children(left_stack, right_stack)
        self._syncing = False

    def vs(self):
        return self.vs_field.get_float(math.nan)

    def freq(self):
        return self.freq_field.get_float(math.nan, positive=True)

    def r2(self):
        return self.r2_field.get_float(math.nan, positive=True)

    def cap(self):
        return self.cap_field.get_float(math.nan, positive=True)

    def rd(self):
        return self.rd_field.get_float(math.nan, positive=True)

    def get_result(self):
        return maxwell_detector_current(
            self.vs(),
            self.freq(),
            self.r2(),
            self.cap(),
            self.rd(),
            self.r3_slider.get(),
            self.rv_slider.get(),
            self.unknown["lx_h"],
            self.unknown["rx"],
        )

    def ideal_values(self):
        return maxwell_ideal_values(self.unknown["lx_h"], self.unknown["rx"], self.r2(), self.cap())

    def range_status(self):
        ideal = self.ideal_values()
        if not ideal["ok"]:
            return {**ideal, "in_r3": False, "in_rv": False, "possible": False}
        in_r3 = self.r3_slider.min_value <= ideal["r3"] <= self.r3_slider.max_value
        in_rv = self.rv_slider.min_value <= ideal["rv"] <= self.rv_slider.max_value
        return {**ideal, "in_r3": in_r3, "in_rv": in_rv, "possible": in_r3 and in_rv}

    def set_initial_dial_near_unknown(self):
        ideal = self.ideal_values()
        if not ideal["ok"]:
            return
        ideal_r3 = ideal["r3"]
        ideal_rv = ideal["rv"]
        r3_offset = 1 + (-1 if random.random() < 0.5 else 1) * (0.08 + random.random() * 0.12)
        rv_offset = 1 + (-1 if random.random() < 0.5 else 1) * (0.10 + random.random() * 0.16)
        self.r3_slider.set(max(self.r3_slider.min_value, min(self.r3_slider.max_value, ideal_r3 * r3_offset)), silent=True)
        self.rv_slider.set(max(self.rv_slider.min_value, min(self.rv_slider.max_value, ideal_rv * rv_offset)), silent=True)
        self.sync_dial_fields()
        self.on_value_change()

    def on_value_change(self):
        self.diagram.redraw()
        self.update_readouts(self.get_result())

    def sync_dial_fields(self):
        self._syncing = True
        self.r3_field.set_value(round(self.r3_slider.get(), 3), silent=True)
        self.rv_field.set_value(round(self.rv_slider.get(), 3), silent=True)
        self._syncing = False

    def _range_labels(self, min_value, max_value, integer=False):
        mid = (min_value + max_value) / 2
        if integer:
            return [f"{round(min_value):d}", f"{round(mid):d}", f"{round(max_value):d}"]
        return [f"{min_value:.1f}", f"{mid:.1f}", f"{max_value:.1f}"]

    def _expand_slider_for_value(self, slider, value, integer=False):
        if slider.min_value <= value <= slider.max_value:
            return False
        low = max(slider.step, min(slider.min_value, value * 0.4))
        high = max(slider.max_value, value * 1.6, low + slider.step * 20)
        slider.configure_range(low, high, labels=self._range_labels(low, high, integer), value=value, silent=True)
        return True

    def on_r3_slider(self):
        if not self._syncing:
            self.sync_dial_fields()
        self.on_value_change()

    def on_rv_slider(self):
        if not self._syncing:
            self.sync_dial_fields()
        self.on_value_change()

    def on_r3_field(self):
        if self._syncing:
            return
        value = self.r3_field.get_float(default=None, positive=True)
        if value is None or not math.isfinite(value):
            self.on_value_change()
            return
        expanded = self._expand_slider_for_value(self.r3_slider, value)
        self._syncing = True
        self.r3_slider.set(value, silent=True)
        self.r3_field.set_value(round(self.r3_slider.get(), 3), silent=True)
        self._syncing = False
        if expanded and self.toast:
            self.toast.show("Maxwell: R3 입력값에 맞춰 슬라이더 범위를 확장했습니다.")
        self.on_value_change()

    def on_rv_field(self):
        if self._syncing:
            return
        value = self.rv_field.get_float(default=None, positive=True)
        if value is None or not math.isfinite(value):
            self.on_value_change()
            return
        expanded = self._expand_slider_for_value(self.rv_slider, value)
        self._syncing = True
        self.rv_slider.set(value, silent=True)
        self.rv_field.set_value(round(self.rv_slider.get(), 3), silent=True)
        self._syncing = False
        if expanded and self.toast:
            self.toast.show("Maxwell: Rv 입력값에 맞춰 슬라이더 범위를 확장했습니다.")
        self.on_value_change()

    def auto_range(self):
        ideal = self.ideal_values()
        if not ideal["ok"]:
            self.balance.set(ideal["message"], theme.WARN)
            return
        r3_min, r3_max = self._auto_bounds(ideal["r3"], R3_STEP, 20)
        rv_min, rv_max = self._auto_bounds(ideal["rv"], RV_STEP, 40)
        if self.unknown_visible or self.diagnostic_visible:
            r3_value = ideal["r3"]
            rv_value = ideal["rv"]
        else:
            r3_current = self.r3_slider.get()
            rv_current = self.rv_slider.get()
            r3_value = r3_current if r3_min <= r3_current <= r3_max else ideal["r3"] * 0.92
            rv_value = rv_current if rv_min <= rv_current <= rv_max else ideal["rv"] * 1.08
        self.r3_slider.configure_range(r3_min, r3_max, step=R3_STEP, labels=self._range_labels(r3_min, r3_max), value=r3_value, silent=True)
        self.rv_slider.configure_range(rv_min, rv_max, step=RV_STEP, labels=self._range_labels(rv_min, rv_max), value=rv_value, silent=True)
        self.sync_dial_fields()
        self.graph.clear()
        self.on_value_change()
        if self.toast:
            self.toast.show("Maxwell: 이론 평형점 주변으로 슬라이더 범위를 조정했습니다.")

    def _auto_bounds(self, ideal, step, min_steps):
        low = max(step, ideal * 0.4)
        high = max(ideal * 1.6, low + step * min_steps)
        return low, high

    def toggle_unknown(self):
        self.unknown_visible = not self.unknown_visible
        self.update_unknown_box()
        self.update_readouts(self.get_result())

    def toggle_diagnostic(self):
        self.diagnostic_visible = not self.diagnostic_visible
        self.diagnostic_btn.configure(text="진단 정보 숨기기" if self.diagnostic_visible else "진단 정보 보기")
        self.update_readouts(self.get_result())

    def refresh_unknown(self):
        self.unknown = random_unknown()
        self.unknown_visible = False
        self.set_initial_dial_near_unknown()
        self.update_unknown_box()
        self.graph.clear()
        if self.toast:
            self.toast.show("Maxwell: 미지 코일 값이 갱신되었습니다.")

    def update_unknown_box(self):
        if self.unknown_visible:
            text = f"Lx = {self.unknown['lx_mh']:.1f} mH / Rx = {self.unknown['rx']} Ω"
        else:
            text = "Lx = ??? mH / Rx = ??? Ω"
        self.unknown_output.configure(text=text)
        self.diagram.redraw()

    def theory_visible(self):
        return self.unknown_visible or self.diagnostic_visible

    def update_readouts(self, result):
        if not result["ok"]:
            for metric in self.metrics.values():
                metric.set("-")
            self.balance.set(result["message"], theme.WARN)
            return
        detector = result["Id"]
        self.metrics["mag"].set(format_auto_current(result["mag"]))
        self.metrics["phase"].set(f"{result['phase']:.2f}°")
        self.metrics["re"].set(format_auto_current(detector.real, signed=True))
        self.metrics["im"].set(format_auto_current(detector.imag, signed=True))
        self.metrics["l"].set(format_mh(result["l_est_h"]))
        self.metrics["r"].set(format_ohm(result["r_est"]))
        status = self.range_status()
        if status["ok"]:
            range_parts = [
                "R3 가능" if status["in_r3"] else "R3 범위 밖",
                "Rv 가능" if status["in_rv"] else "Rv 범위 밖",
            ]
            if self.theory_visible():
                self.metrics["ideal_r3"].set(format_ohm(status["r3"]))
                self.metrics["ideal_rv"].set(format_ohm(status["rv"]))
                self.metrics["r3_range"].set(f"{format_ohm(self.r3_slider.min_value)} ~ {format_ohm(self.r3_slider.max_value)}")
                self.metrics["rv_range"].set(f"{format_ohm(self.rv_slider.min_value)} ~ {format_ohm(self.rv_slider.max_value)}")
            else:
                self.metrics["ideal_r3"].set("진단 모드에서 표시")
                self.metrics["ideal_rv"].set("진단 모드에서 표시")
                self.metrics["r3_range"].set("연습 모드")
                self.metrics["rv_range"].set("연습 모드")
            self.metrics["range"].set("둘 다 가능" if status["possible"] else " / ".join(range_parts), theme.GOOD if status["possible"] else theme.WARN)
        else:
            self.metrics["ideal_r3"].set("-")
            self.metrics["ideal_rv"].set("-")
            self.metrics["r3_range"].set("-")
            self.metrics["rv_range"].set("-")
            self.metrics["range"].set("입력 확인", theme.WARN)
        text, good = balance_hint(result)
        if status["ok"] and not status["possible"]:
            parts = []
            if not status["in_r3"]:
                parts.append("R3")
            if not status["in_rv"]:
                parts.append("Rv")
            which = ", ".join(parts)
            if self.theory_visible():
                text = (
                    f"현재 R2/C 설정에서는 {which} 평형점이 슬라이더 범위 밖입니다. "
                    f"R3*={format_ohm(status['r3'])}, Rv*={format_ohm(status['rv'])}. Auto Range를 누르거나 R2/C를 조정하세요."
                )
            else:
                text = f"현재 범위에서는 완전 평형이 어렵습니다. 범위 밖: {which}. Auto Range를 누르거나 R2/C 값을 조정하세요. 이론값은 진단 모드에서 확인할 수 있습니다."
            self.balance.set(text, theme.WARN)
        else:
            self.balance.set(text, theme.GOOD if good else theme.TEXT_2)

    def on_show(self):
        self.scroll.scroll_to_top()
        self._tick()

    def on_hide(self):
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None

    def _tick(self):
        result = self.get_result()
        if result["ok"]:
            self.graph.add_sample(result["mag"])
        self.update_readouts(result)
        self.graph.draw()
        self._after_id = self.after(16, self._tick)
