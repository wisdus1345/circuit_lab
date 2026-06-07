import math
import random
import tkinter as tk

from .. import theme
from ..graphs import LoadLineGraph
from ..widgets import CanvasSlider, CollapsibleSection, CompactMetricBox, DualColumnWorkbench, NumberField, Panel, ResponsiveControlGrid, ResponsiveTopFrame, ScaledCanvas, ScrollableFrame, SectionHeader, font, make_button, set_button_variant


MODE_DEFS = {
    "bias": {
        "label": "바이어스 모드",
        "summary": "목표: active 영역에서 VCE ≈ VCC/2가 되도록 맞춰보세요.",
        "graph_help": "Q점과 양쪽 스윙 여유를 비교해 대칭 스윙 조건을 확인하세요.",
        "controls_intro": "바이어스 모드입니다. RB와 RC를 조절해서 Q점을 부하선 중앙 근처로 맞춰보세요.",
        "banner_title": "바이어스 목표 달성",
        "banner_detail": "양쪽 스윙 여유가 균형적이어서 대칭 스윙에 유리합니다.",
    },
    "switch_off": {
        "label": "스위치 OFF 모드",
        "summary": "목표: cutoff 상태를 만들어 IC를 거의 0으로 줄여보세요.",
        "graph_help": "Q점이 오른쪽 아래, 즉 IC≈0 · VCE≈VCC 목표 영역으로 이동해야 합니다.",
        "controls_intro": "진짜 cutoff는 Vin ≤ VBE일 때 성립합니다. RB를 키우면 전류는 줄지만 Vin이 VBE보다 크면 완전 cutoff가 아닐 수 있습니다.",
        "banner_title": "OFF 상태 달성",
        "banner_detail": "잔류 전류가 매우 작아 스위치 OFF 상태에 적합합니다.",
    },
    "switch_on": {
        "label": "스위치 ON 모드",
        "summary": "목표: saturation 상태를 만들고 충분한 베이스 구동으로 ON 상태를 확보하세요.",
        "graph_help": "Q점이 왼쪽 위, 즉 VCE≈VCE(sat) 포화 목표 영역 안으로 들어가는지 확인하세요.",
        "controls_intro": "스위치 ON 모드입니다. RB를 줄이거나 Vin을 올려 강한 베이스 구동을 만들어보세요.",
        "banner_title": "ON 상태 달성",
        "banner_detail": "VCE가 낮고 포화 여유가 충분하여 스위치 ON 용도로 적절합니다.",
    },
    "beta_observe": {
        "label": "β 관찰 모드",
        "summary": "목표: active 상태에서 β_est를 보고 숨겨진 β를 추정해보세요.",
        "graph_help": "Q점이 β 추정에 적합한 active 영역에 있는지 확인하세요.",
        "controls_intro": "β 관찰 모드입니다. 먼저 active 영역을 만든 뒤 측정값으로 숨겨진 β를 추정해보세요.",
        "banner_title": "β 관찰 준비 완료",
        "banner_detail": "현재 β_est를 비교적 신뢰성 있게 읽을 수 있는 상태입니다.",
    },
}


def random_beta():
    return math.floor(60 + random.random() * 161)


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def transistor_solve(vcc=12, vin=5, vbe_on=0.70, vce_sat=0.20, rb=100_000, rc=1000, beta=100):
    p = {
        "vcc": vcc,
        "vin": vin,
        "vbe": vbe_on,
        "vceSat": vce_sat,
        "rb": rb,
        "rc": rc,
        "beta": beta,
    }
    result = {
        "p": p,
        "ib": 0.0,
        "ic": 0.0,
        "vce": vcc,
        "vc": vcc,
        "forcedBeta": math.nan,
        "betaEst": math.nan,
        "power": 0.0,
        "region": "cutoff",
    }
    if vin <= vbe_on:
        return result

    result["ib"] = (vin - vbe_on) / rb
    ic_active = beta * result["ib"]
    ic_sat_max = (vcc - vce_sat) / rc
    if ic_active < ic_sat_max:
        result["region"] = "active"
        result["ic"] = ic_active
        result["vce"] = vcc - result["ic"] * rc
        result["betaEst"] = result["ic"] / result["ib"]
    else:
        result["region"] = "saturation"
        result["ic"] = ic_sat_max
        result["vce"] = vce_sat
        result["forcedBeta"] = result["ic"] / result["ib"]
    result["vc"] = result["vce"]
    result["power"] = result["vce"] * result["ic"]
    return result


def score_closeness(value, target, tolerance, falloff):
    diff = abs(value - target)
    if diff <= tolerance:
        return 1.0
    if diff >= falloff:
        return 0.0
    return 1 - (diff - tolerance) / (falloff - tolerance)


def region_ko(region):
    if region == "cutoff":
        return "cutoff · 차단"
    if region == "active":
        return "active · 활성"
    return "saturation · 포화"


def format_ohm(ohm):
    if not math.isfinite(ohm):
        return "-"
    if ohm >= 1000:
        return f"{ohm / 1000:.2f} kΩ"
    return f"{ohm:.0f} Ω"


def format_rb(kohm):
    return f"{kohm:.1f} kΩ"


def format_micro_amp(a):
    return f"{a * 1e6:.3f} µA"


def format_milli_amp(a):
    return f"{a * 1e3:.3f} mA"


def format_volt(v):
    return f"{v:.3f} V"


def format_power(w):
    if w >= 1:
        return f"{w:.4f} W"
    return f"{w * 1000:.2f} mW"


def build_goal(result, mode):
    p = result["p"]
    mid_vce = p["vcc"] / 2
    bias_tol = max(0.3, p["vcc"] * 0.05)
    bias_far = max(1.6, p["vcc"] * 0.28)
    off_ic_a = 50e-6
    checks = []
    score = 0
    status = "fail"
    status_text = "미달"

    if mode == "bias":
        q_center_ic = (p["vcc"] / p["rc"]) / 2
        q_dist = math.hypot(
            (result["vce"] - mid_vce) / max(0.5, p["vcc"] / 2),
            (result["ic"] - q_center_ic) / max(1e-6, q_center_ic),
        )
        active_pass = result["region"] == "active"
        vce_close = score_closeness(result["vce"], mid_vce, bias_tol, bias_far)
        q_close = clamp(1 - q_dist / 0.9, 0, 1)
        checks = [
            ("active 영역인가?", "활성 영역을 유지하고 있습니다." if active_pass else "먼저 cutoff나 saturation을 벗어나 active로 들어오세요.", "pass" if active_pass else "fail"),
            ("VCE가 VCC/2 근처인가?", f"목표 VCE = {mid_vce:.2f} V, 현재 = {result['vce']:.2f} V", "pass" if vce_close > 0.95 else "near" if vce_close > 0.65 else "fail"),
            ("Q점이 중앙 근처인가?", "Q점이 부하선 중앙에 가깝습니다." if q_close > 0.9 else "거의 맞았습니다." if q_close > 0.6 else "중앙에서 아직 벗어나 있습니다.", "pass" if q_close > 0.9 else "near" if q_close > 0.6 else "fail"),
        ]
        score = round((35 if active_pass else 0) + vce_close * 35 + q_close * 30)
        if active_pass and vce_close > 0.95 and q_close > 0.88:
            status, status_text = "success", "성공"
        elif active_pass and score >= 72:
            status, status_text = "almost", "거의 성공"
    elif mode == "switch_off":
        cutoff_pass = result["region"] == "cutoff"
        ic_score = clamp(1 - result["ic"] / off_ic_a, 0, 1)
        vce_score = score_closeness(result["vce"], p["vcc"], max(0.08, p["vcc"] * 0.02), max(0.7, p["vcc"] * 0.15))
        checks = [
            ("cutoff인가?", "차단 상태입니다." if cutoff_pass else f"완전 OFF 아님: Vin({p['vin']:.2f} V)이 VBE({p['vbe']:.2f} V)보다 커서 베이스 구동이 남아 있습니다.", "pass" if cutoff_pass else "fail"),
            ("IC가 매우 작은가?", "현재 IC = " + format_micro_amp(result["ic"]), "pass" if cutoff_pass and ic_score > 0.95 else "near" if cutoff_pass and ic_score > 0.65 else "fail"),
            ("VCE가 VCC에 가까운가?", f"현재 VCE = {result['vce']:.2f} V / VCC = {p['vcc']:.2f} V", "pass" if vce_score > 0.95 else "near" if vce_score > 0.7 else "fail"),
        ]
        score = round((45 if cutoff_pass else 0) + ic_score * 25 + vce_score * 30)
        if cutoff_pass and ic_score > 0.95 and vce_score > 0.95:
            status, status_text = "success", "성공"
        elif cutoff_pass and score >= 75:
            status, status_text = "almost", "거의 성공"
    elif mode == "switch_on":
        sat_pass = result["region"] == "saturation"
        vce_on_score = score_closeness(result["vce"], p["vceSat"], 0.05, 0.45)
        forced_score = clamp((20 - result["forcedBeta"]) / 10, 0, 1) if math.isfinite(result["forcedBeta"]) else 0
        checks = [
            ("saturation인가?", "포화 영역에 들어왔습니다." if sat_pass else "아직 완전한 ON 상태가 아닙니다.", "pass" if sat_pass else "fail"),
            ("VCE가 VCE(sat)에 가까운가?", f"현재 VCE = {result['vce']:.2f} V / 목표 ≈ {p['vceSat']:.2f} V", "pass" if vce_on_score > 0.95 else "near" if vce_on_score > 0.7 else "fail"),
            ("forced β가 충분히 작은가?", f"현재 forced β = {result['forcedBeta']:.1f}" if math.isfinite(result["forcedBeta"]) else "포화 상태에서 평가됩니다.", "pass" if forced_score > 0.9 else "near" if forced_score > 0.55 else "fail"),
        ]
        score = round((45 if sat_pass else 0) + vce_on_score * 25 + forced_score * 30)
        if sat_pass and vce_on_score > 0.92 and forced_score > 0.9:
            status, status_text = "success", "성공"
        elif sat_pass and score >= 72:
            status, status_text = "almost", "거의 성공"
    else:
        beta_active = result["region"] == "active"
        stable_vce = score_closeness(result["vce"], mid_vce, max(0.5, p["vcc"] * 0.08), max(2, p["vcc"] * 0.35))
        beta_readable = 1 if math.isfinite(result["betaEst"]) else 0
        checks = [
            ("active 영역인가?", "β 추정이 가능한 활성 영역입니다." if beta_active else "먼저 active 영역을 만들어야 β_est가 의미를 갖습니다.", "pass" if beta_active else "fail"),
            ("β_est를 읽을 수 있는가?", f"현재 β_est = {result['betaEst']:.1f}" if beta_readable else "활성 영역에서만 추정 가능합니다.", "pass" if beta_readable else "fail"),
            ("측정 조건이 안정적인가?", "Q점이 비교적 안정적입니다." if stable_vce > 0.85 else "VCE를 중앙 쪽으로 맞추면 추정이 쉬워집니다.", "pass" if stable_vce > 0.85 else "near" if stable_vce > 0.55 else "fail"),
        ]
        score = round((40 if beta_active else 0) + beta_readable * 30 + stable_vce * 30)
        if beta_active and beta_readable and stable_vce > 0.85:
            status, status_text = "success", "관찰 준비 완료"
        elif score >= 70:
            status, status_text = "almost", "거의 준비됨"

    return {
        "mode": mode,
        "label": MODE_DEFS[mode]["label"],
        "summary": MODE_DEFS[mode]["summary"],
        "graph_help": MODE_DEFS[mode]["graph_help"],
        "controls_intro": MODE_DEFS[mode]["controls_intro"],
        "banner_title": MODE_DEFS[mode]["banner_title"],
        "banner_detail": MODE_DEFS[mode]["banner_detail"],
        "checks": [{"title": c[0], "detail": c[1], "state": c[2]} for c in checks],
        "score": int(clamp(score, 0, 100)),
        "status": status,
        "status_text": status_text,
    }


def build_mode_insight(result, goal, mode, beta_visible):
    p = result["p"]
    if mode == "bias":
        delta_cutoff = max(0, p["vcc"] - result["vce"])
        delta_sat = max(0, result["vce"] - p["vceSat"])
        delta_peak = min(delta_cutoff, delta_sat)
        if abs(delta_cutoff - delta_sat) <= max(0.25, p["vcc"] * 0.04):
            balance = "양쪽 여유가 거의 균형적"
        elif delta_cutoff < delta_sat:
            balance = "cutoff 쪽 여유가 더 작음"
        else:
            balance = "saturation 쪽 여유가 더 작음"
        return {
            "summary": "바이어스 모드에서는 Q점에서 cutoff 쪽과 saturation 쪽으로 얼마나 움직일 수 있는지 비교하는 것이 핵심입니다.",
            "lamp": "neutral",
            "lamp_label": balance,
            "metrics": [
                ("cutoff 방향 전압 여유", f"{delta_cutoff:.3f} V"),
                ("saturation 방향 전압 여유", f"{delta_sat:.3f} V"),
                ("최대 대칭 출력 스윙(peak)", f"{delta_peak:.3f} V"),
                ("클리핑 위험 방향", balance),
            ],
        }
    if mode == "switch_off":
        off_level = clamp(goal["score"], 0, 100)
        base_removed = "베이스 구동이 사실상 제거됨" if result["ib"] <= 1e-9 else "베이스 전류가 매우 작음" if result["ib"] < 2e-6 else "아직 베이스 전류가 남아 있음"
        off_state = "완전 OFF에 가까움" if off_level >= 95 else "거의 OFF" if off_level >= 75 else "아직 ON 성분이 남음"
        return {
            "summary": "OFF 모드에서는 IC를 0에 가깝게 줄이고 VCE를 VCC 쪽으로 끌어올리는 것이 핵심입니다.",
            "lamp": "off" if off_level >= 75 else "neutral",
            "lamp_label": off_state,
            "metrics": [
                ("현재 OFF 수준", f"{off_level:.0f} %"),
                ("잔류 컬렉터 전류", format_milli_amp(result["ic"])),
                ("차단 여유", base_removed),
                ("현재 상태 판정", off_state),
            ],
        }
    if mode == "switch_on":
        forced = result["forcedBeta"] if math.isfinite(result["forcedBeta"]) else 999
        on_level = clamp(goal["score"], 0, 100)
        sat_label = "아직 포화 전" if result["region"] != "saturation" else "강한 포화" if forced < 10 else "적절한 포화" if forced <= 20 else "약한 포화"
        drive = "베이스 구동을 더 키워야 함" if result["region"] != "saturation" else "베이스 구동 여유 큼" if forced < 10 else "베이스 구동 여유 적절" if forced <= 20 else "포화는 되었지만 여유 부족"
        return {
            "summary": "ON 모드에서는 VCE를 낮게 만들고 forced β를 작게 유지해 충분한 포화 구동을 확보하는 것이 핵심입니다.",
            "lamp": "on" if on_level >= 75 else "neutral",
            "lamp_label": sat_label,
            "metrics": [
                ("현재 ON 수준", f"{on_level:.0f} %"),
                ("포화 정도", sat_label),
                ("VCE - VCE(sat)", f"{max(0, result['vce'] - p['vceSat']):.3f} V"),
                ("베이스 구동 여유", drive),
            ],
        }
    suitability = clamp(goal["score"], 0, 100)
    quality = "포화/차단 근처라 추정 부적절" if result["region"] != "active" else "추정하기 좋은 상태" if suitability >= 85 else "활성 영역이지만 Q점이 다소 치우침" if suitability >= 65 else "조건이 아직 불안정함"
    return {
        "summary": "β 관찰 모드에서는 active 영역을 유지한 채 Q점이 너무 극단적이지 않아야 β_est가 비교적 의미 있게 읽힙니다.",
        "lamp": "on" if suitability >= 75 else "neutral",
        "lamp_label": quality,
        "metrics": [
            ("현재 β_est", f"{result['betaEst']:.1f}" if math.isfinite(result["betaEst"]) else "읽기 어려움"),
            ("β 관찰 적합도", f"{suitability:.0f} %"),
            ("측정 조건 평가", quality),
            ("실제 β 공개 상태", "공개됨" if beta_visible else "숨김"),
        ],
    }


def make_hint(result, goal, insight, mode):
    p = result["p"]
    mid_vce = p["vcc"] / 2
    if mode == "bias":
        if result["region"] == "cutoff":
            return "차단 상태입니다. RB를 줄이거나 Vin을 올려 active로 진입하세요.", False
        if result["region"] == "saturation":
            return "포화 상태입니다. RB를 키우거나 RC를 줄여 active로 되돌리세요.", False
        delta_cutoff = p["vcc"] - result["vce"]
        delta_sat = result["vce"] - p["vceSat"]
        if abs(delta_cutoff - delta_sat) <= max(0.25, p["vcc"] * 0.04):
            return "현재는 cutoff와 saturation 여유가 비슷하여 대칭 스윙에 유리합니다.", True
        if delta_cutoff < delta_sat:
            return "현재는 cutoff 쪽 여유가 부족해서 큰 입력 신호에서 위쪽 스윙이 먼저 잘릴 수 있습니다.", True
        if result["vce"] < mid_vce:
            return "현재는 saturation 쪽 여유가 부족해서 큰 입력 신호에서 아래쪽 클리핑이 먼저 발생합니다. RB를 키우거나 RC를 줄여보세요.", True
        return "Q점이 위쪽에 있어 saturation 쪽보다 cutoff 쪽에 더 가깝습니다. RB를 줄이거나 RC를 키워 균형을 맞춰보세요.", True
    if mode == "switch_off":
        if goal["status"] == "success":
            return "현재는 거의 완전한 차단 상태이며 스위치 OFF에 적합합니다.", True
        if result["region"] == "cutoff":
            return "거의 OFF 상태입니다. IC가 더 작아지는지, VCE가 VCC에 더 가까워지는지 확인하세요.", True
        if p["vin"] > p["vbe"]:
            return "RB 문제가 아니라 Vin 조건 문제입니다. 완전 cutoff는 Vin ≤ VBE일 때만 성립하므로 Vin을 낮추거나 OFF 목표 자동 설정을 누르세요.", False
        return "IC가 아직 남아 있어 완전 OFF가 아닙니다. RB를 더 키우거나 Vin을 더 낮춰보세요.", False
    if mode == "switch_on":
        if goal["status"] == "success":
            return "현재는 강한 포화 상태여서 스위치 ON 용도로 적절합니다.", True
        if result["region"] == "active":
            return "아직 완전 ON이 아닙니다. RB를 더 줄여 베이스 구동을 강하게 하세요.", False
        if result["region"] == "saturation" and math.isfinite(result["forcedBeta"]) and result["forcedBeta"] > 20:
            return "포화에 들어왔지만 베이스 구동 여유가 충분하지 않습니다. RB를 더 줄여보세요.", False
        return "포화 상태에 가깝습니다. VCE가 VCE(sat)에 얼마나 가까운지와 forced β 여유를 함께 보세요.", True
    if result["region"] != "active":
        return "현재 Q점이 포화/차단에 가까워 β 추정에 부적절합니다. 먼저 active 영역을 유지하세요.", False
    if goal["status"] == "success":
        return "현재는 active 영역이며 β_est가 의미 있습니다. 마지막에 실제 β와 비교해보세요.", True
    return "활성 영역이지만 Q점이 다소 치우쳐 있습니다. VCE를 중앙 쪽으로 맞추면 β 추정 조건이 더 좋아집니다.", True


class TransistorDiagram(ScaledCanvas):
    def __init__(self, parent, page):
        self.page = page
        super().__init__(parent, 760, 470, min_height=285, max_height=340, padding=1)

    def draw(self):
        p = self.page
        result = p.result or p.solve()
        wire = theme.WIRE
        self.rect(10, 10, 740, 450, outline="#2b3847", fill=theme.PANEL_2, width=1.2)
        self.text(380, 40, "NPN 공통 이미터 트랜지스터 스위치", color=theme.TEXT_2, size=17, weight="bold")
        self.line(470, 74, 470, 112, fill=wire, width=3)
        self.text(430, 73, "+VCC", color=theme.DANGER, size=14, weight="bold")
        self.text(486, 75, f"{result['p']['vcc']:.2f} V", color=theme.TEXT_2, size=11, anchor="w")
        self.resistor_v(470, 112, 194, fill=wire, width=3)
        self.line(470, 194, 470, 232, fill=wire, width=3)
        self.oval(470, 232, 5.8, fill=theme.NODE, outline="#dff1ff", width=2)
        self.text(504, 142, "Rᶜ", color=theme.TEXT_2, size=14, weight="bold", anchor="w")
        self.text(504, 164, format_ohm(p.rc_slider.get()), color=theme.TEXT_2, size=11, anchor="w")
        self.text(486, 224, "c", color=theme.BLUE, size=15, weight="bold", anchor="w")
        self.oval(398, 286, 58, fill="#111821", outline=wire, width=3)
        self.line(354, 286, 398, 286, fill=wire, width=3)
        self.line(398, 286, 470, 232, fill=wire, width=3)
        self.line(398, 286, 470, 340, fill=wire, width=3)
        self.poly([(452, 333), (471, 340), (460, 322)], fill=theme.DANGER, width=3)
        self.text(361, 279, "b", color=theme.BLUE, size=15, weight="bold")
        self.text(475, 352, "e", color=theme.BLUE, size=15, weight="bold")
        self.text(362, 364, "NPN transistor", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(356, 386, "영역: " + region_ko(result["region"]), color=theme.MUTED_2, size=11, anchor="w")
        self.line(470, 340, 470, 392, fill=wire, width=3)
        self._ground(470, 392)
        self.text(500, 410, "GND", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.oval(150, 286, 38, fill="#111821", outline=wire, width=3)
        self.oval(150, 286, 29, outline="#334252", width=1)
        self.text(150, 279, "+", color=theme.DANGER, size=14, weight="bold")
        self.text(150, 307, "Vin", color=theme.MUTED_2, size=11)
        self.line(150, 324, 150, 392, fill=wire, width=3)
        self._ground(150, 392)
        self.line(188, 286, 226, 286, fill=wire, width=3)
        self.resistor_h(226, 296, 286, fill=wire, width=3)
        self.line(296, 286, 354, 286, fill=wire, width=3)
        self.oval(354, 286, 5.8, fill=theme.NODE, outline="#dff1ff", width=2)
        self.text(235, 252, "Rᴮ", color=theme.TEXT_2, size=14, weight="bold", anchor="w")
        self.text(235, 273, format_rb(p.rb_slider.get()), color=theme.TEXT_2, size=11, anchor="w")
        self.rect(36, 120, 178, 82, outline="#405064", fill="#0f1419", width=1)
        self.text(52, 144, "입력 전원", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(52, 169, f"Vin = {result['p']['vin']:.2f} V", color=theme.TEXT_2, size=11, anchor="w")
        self.text(52, 191, f"VBE(on) = {result['p']['vbe']:.2f} V", color=theme.MUTED_2, size=10, anchor="w")
        self.rect(542, 232, 172, 110, outline="#405064", fill="#0f1419", width=1)
        self.text(558, 255, "동작점", color=theme.TEXT_2, size=12, weight="bold", anchor="w")
        self.text(558, 280, "IC = " + format_milli_amp(result["ic"]), color=theme.TEXT_2, size=10, anchor="w")
        self.text(558, 303, f"VCE = {result['vce']:.2f} V", color=theme.TEXT_2, size=10, anchor="w")
        self.text(558, 326, "모드: " + MODE_DEFS[p.mode]["label"], color=theme.MUTED_2, size=9, anchor="w")

    def _ground(self, x, y):
        self.line(x - 32, y, x + 32, y, fill=theme.WIRE, width=3)
        self.line(x - 22, y + 12, x + 22, y + 12, fill=theme.WIRE, width=3)
        self.line(x - 12, y + 24, x + 12, y + 24, fill=theme.WIRE, width=3)


class TransistorPage(tk.Frame):
    title = "NPN 트랜지스터 공통 이미터 DC 바이어스/스위칭 실습"

    def __init__(self, parent, toast=None):
        super().__init__(parent, bg=theme.BG)
        self.toast = toast
        self.beta = random_beta()
        self.beta_visible = False
        self.mode = "bias"
        self.mode_order = ("bias", "switch_off", "switch_on", "beta_observe")
        self.result = None
        self._syncing = False
        self._after_id = None
        self._build()
        self.update_all()

    def _build(self):
        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)
        root = self.scroll.inner
        root.columnconfigure(0, weight=1)
        tk.Label(root, text="모드별 목표 영역에 Q점을 맞추며 cutoff/active/saturation을 비교합니다.", bg=theme.BG, fg=theme.MUTED_2, font=font(9)).grid(
            row=0, column=0, sticky="w", padx=theme.PAGE_PAD_X, pady=(6, 4)
        )
        top_frame = DualColumnWorkbench(root, breakpoint=1080, left_weight=3, right_weight=2)
        top_frame.grid(row=1, column=0, sticky="ew", padx=theme.PAGE_PAD_X, pady=(0, theme.CARD_GAP))
        left_stack = tk.Frame(top_frame, bg=theme.BG)
        right_stack = tk.Frame(top_frame, bg=theme.BG)
        left_stack.columnconfigure(0, weight=1)
        right_stack.columnconfigure(0, weight=1)

        diagram_panel = Panel(left_stack)
        diagram_panel.grid(row=0, column=0, sticky="ew")
        SectionHeader(diagram_panel, "NPN 공통 이미터 회로도", "RB: 베이스 전류 / RC: 컬렉터 부하 / emitter: GND").pack(fill="x")
        self.diagram = TransistorDiagram(diagram_panel, self)
        self.diagram.pack(fill="x", padx=6, pady=(0, 8))

        graph_panel = Panel(right_stack)
        graph_panel.grid(row=0, column=0, sticky="ew")
        SectionHeader(graph_panel, "부하선과 Q점 · 실시간 그래프").pack(fill="x")
        self.success_banner = tk.Label(
            graph_panel,
            text="",
            bg="#17382f",
            fg=theme.GOOD,
            font=font(9, "bold"),
            padx=8,
            pady=4,
            anchor="w",
        )
        self.graph_help = tk.Label(
            graph_panel,
            text="",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(8),
            anchor="w",
            justify="left",
            wraplength=520,
        )
        self.graph_help.pack(fill="x", padx=10, pady=(0, 5))
        self.graph = LoadLineGraph(graph_panel, height=theme.LOADLINE_HEIGHT)
        self.graph.pack(fill="both", expand=True, padx=8, pady=(0, 5))
        self.graph_meta = CompactMetricBox(graph_panel, "현재 그래프 상태", "-", wraplength="auto", value_anchor="nw")
        self.graph_meta.pack(fill="x", padx=8, pady=(0, 6))
        primary_adjust_panel = Panel(right_stack)
        primary_adjust_panel.grid(row=1, column=0, sticky="ew", pady=(theme.CARD_GAP, 0))
        SectionHeader(primary_adjust_panel, "핵심 조작", "Vin, RB, RC를 조절하며 Q점과 동작 영역 변화를 확인합니다.").pack(fill="x")
        left = tk.Frame(right_stack, bg=theme.BG)
        left.grid(row=2, column=0, sticky="ew", pady=(theme.CARD_GAP, 0))
        right = Panel(left_stack)
        right.grid(row=1, column=0, sticky="ew", pady=(theme.CARD_GAP, 0))

        lower = tk.Frame(left, bg=theme.BG)
        lower.pack(fill="x")
        lower.columnconfigure(0, weight=1)
        lower.columnconfigure(1, weight=1)
        readout_panel = Panel(lower)
        readout_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
        goal_panel = Panel(lower)
        goal_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))

        SectionHeader(readout_panel, "실시간 측정값").pack(fill="x")
        grid = tk.Frame(readout_panel, bg=theme.PANEL)
        grid.pack(fill="x", padx=8, pady=(0, 6))
        for i in range(2):
            grid.columnconfigure(i, weight=1)
        labels = [
            ("region", "동작 영역"),
            ("ib", "베이스 전류 IB"),
            ("ic", "컬렉터 전류 IC"),
            ("vce", "전압 VCE"),
            ("vc", "컬렉터 전압 VC"),
            ("power", "트랜지스터 전력 P"),
        ]
        self.metrics = {}
        for idx, (key, label) in enumerate(labels):
            box = CompactMetricBox(grid, label, "-", value_size=9)
            box.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=4, pady=3)
            self.metrics[key] = box
        tk.Label(readout_panel, text="추가 관찰값", bg=theme.PANEL, fg=theme.TEXT_2, font=font(10, "bold")).pack(
            anchor="w", padx=10, pady=(2, 2)
        )
        secondary_grid = tk.Frame(readout_panel, bg=theme.PANEL)
        secondary_grid.pack(fill="x", padx=8, pady=(0, 6))
        for i in range(2):
            secondary_grid.columnconfigure(i, weight=1)
        for idx, (key, label) in enumerate([
            ("beta_state", "숨겨진 β 상태"),
            ("beta_est", "추정 β_est"),
            ("forced", "forced β"),
        ]):
            box = CompactMetricBox(secondary_grid, label, "-", wraplength="auto", value_size=9, value_anchor="nw")
            box.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=4, pady=3)
            self.metrics[key] = box
        self.metrics["mission"] = CompactMetricBox(
            readout_panel,
            "현재 목표 달성 상태",
            "-",
            wraplength="auto",
            value_size=9,
            value_anchor="nw",
            value_pady=(1, 7),
        )
        self.metrics["mission"].pack(fill="x", padx=8, pady=(0, 5))
        self.metrics["hint"] = CompactMetricBox(
            readout_panel,
            "실습 힌트",
            "-",
            wraplength="auto",
            value_size=9,
            value_anchor="nw",
            value_pady=(1, 7),
        )
        self.metrics["hint"].pack(fill="x", padx=8, pady=(0, 8))

        SectionHeader(goal_panel, "목표 판정").pack(fill="x")
        self.goal_panel = goal_panel
        self.goal_summary = tk.Label(goal_panel, text="", bg=theme.PANEL, fg=theme.TEXT_2, font=font(9, "bold"), wraplength=360, justify="left")
        self.goal_summary.pack(fill="x", padx=10, pady=(0, 5))
        self.goal_score = CompactMetricBox(goal_panel, "현재 달성률", "0 / 100")
        self.goal_score.pack(fill="x", padx=10, pady=(0, 6))
        self.checklist = tk.Frame(goal_panel, bg=theme.PANEL)
        self.checklist.pack(fill="x", padx=10, pady=(0, 8))

        self.insight = CompactMetricBox(
            left,
            "현재 모드 결과 해석",
            "현재 조작 결과가 회로 동작에 어떤 의미를 갖는지 여기서 해석합니다.",
            wraplength="auto",
            value_size=9,
            value_anchor="nw",
            value_pady=(1, 8),
        )
        self.insight.pack(fill="x", pady=(0, 10))

        SectionHeader(right, "회로 값 조절").pack(fill="x")
        tk.Label(right, text="실습 모드", bg=theme.PANEL, fg=theme.MUTED_2, font=font(8, "bold"), anchor="w").pack(
            fill="x", padx=10, pady=(0, 3)
        )
        modes = tk.Frame(right, bg=theme.PANEL)
        modes.pack(fill="x", padx=10, pady=(0, 8))
        self.mode_buttons = {}
        self.modes_frame = modes
        for key in self.mode_order:
            btn = make_button(modes, MODE_DEFS[key]["label"], lambda k=key: self.set_mode(k), "secondary")
            self.mode_buttons[key] = btn
        modes.bind("<Configure>", self._layout_mode_buttons)
        self.controls_intro = tk.Label(right, text="", bg=theme.PANEL, fg=theme.MUTED_2, font=font(8), wraplength=360, justify="left")
        self.controls_intro.pack(fill="x", padx=10, pady=(0, 6))
        self.target_metric = CompactMetricBox(
            right,
            "목표 도달 가능성",
            "모드를 선택하면 현재 범위 판정이 표시됩니다.",
            wraplength="auto",
            value_size=9,
            value_anchor="nw",
            value_pady=(1, 7),
        )
        self.target_metric.pack(fill="x", padx=10, pady=(0, 8))
        quick = tk.Frame(right, bg=theme.PANEL)
        quick.pack(fill="x", padx=10, pady=(0, 8))
        self.quick_off_btn = make_button(quick, "OFF 목표 자동 설정", self.quick_off, "secondary")
        self.quick_bias_btn = make_button(quick, "중앙 Q점 자동 근접", self.quick_bias, "secondary")
        self.quick_on_btn = make_button(quick, "포화 ON 자동 근접", self.quick_on, "secondary")
        self.quick_off_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.quick_bias_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.quick_on_btn.pack(side="left", fill="x", expand=True)
        self.vin_slider = CanvasSlider(
            primary_adjust_panel,
            "Vin 입력 전압 · OFF 목표에서는 VBE 이하로 낮추세요",
            0,
            5,
            0.05,
            5,
            formatter=lambda v: f"{v:.2f} V",
            labels=["0 V", "2.5 V", "5 V"],
            on_change=self.on_vin_slider,
            compact=True,
        )
        self.vin_slider.pack(fill="x", padx=10, pady=(0, 8))
        slider_row = tk.Frame(primary_adjust_panel, bg=theme.PANEL)
        slider_row.pack(fill="x", padx=10, pady=(0, 8))
        slider_row.columnconfigure(0, weight=1, uniform="transistor_sliders")
        slider_row.columnconfigure(1, weight=1, uniform="transistor_sliders")
        self.rb_slider = CanvasSlider(
            slider_row,
            "Rᴮ 베이스 저항 (0.1 kΩ ~ 300 kΩ)",
            0.1,
            300,
            0.1,
            100,
            formatter=lambda v: f"{v:.1f} kΩ",
            labels=["0.1 kΩ", "150 kΩ", "300 kΩ"],
            on_change=self.update_all,
            compact=True,
        )
        self.rb_slider.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.rc_slider = CanvasSlider(
            slider_row,
            "Rᶜ 컬렉터 저항 (100 Ω ~ 5 kΩ)",
            100,
            5000,
            10,
            1000,
            formatter=format_ohm,
            labels=["100 Ω", "2.5 kΩ", "5 kΩ"],
            on_change=self.update_all,
            compact=True,
        )
        self.rc_slider.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        param_grid = ResponsiveControlGrid(right, columns=2, breakpoint=420)
        param_grid.pack(fill="x", padx=10, pady=(0, 8))
        self.vcc_field = NumberField(param_grid, "VCC 전원 (V)", 12, on_change=self.update_all, min_value=5, max_value=15, digits=1)
        self.vin_field = NumberField(param_grid, "Vin 직접 입력 (V)", 5, on_change=self.on_vin_field, min_value=0, max_value=5, digits=2)
        self.vbe_field = NumberField(param_grid, "VBE(on) (V)", 0.70, on_change=self.update_all, min_value=0.45, max_value=0.95, digits=2)
        self.vce_sat_field = NumberField(param_grid, "VCE(sat) (V)", 0.20, on_change=self.update_all, min_value=0.02, max_value=0.6, digits=2)
        for field in (self.vcc_field, self.vin_field, self.vbe_field, self.vce_sat_field):
            param_grid.add(field)

        advanced = CollapsibleSection(right, "β/모델 고급 설정", initially_open=False)
        advanced.pack(fill="x", padx=10, pady=(0, 8))
        beta_panel = tk.Frame(advanced.body, bg=theme.PANEL)
        beta_panel.pack(fill="x")
        tk.Label(beta_panel, text="미지 트랜지스터 파라미터", bg=theme.PANEL, fg=theme.MUTED_2, font=font(9, "bold")).pack(anchor="w")
        btns = tk.Frame(beta_panel, bg=theme.PANEL)
        btns.pack(fill="x", pady=(4, 4))
        make_button(btns, "β 값 확인 / 숨기기", self.toggle_beta).pack(side="left", fill="x", expand=True, padx=(0, 6))
        make_button(btns, "미지 β 값 갱신", self.refresh_beta, "secondary").pack(side="left", fill="x", expand=True)
        self.beta_output = tk.Label(beta_panel, text="β = ???", bg=theme.GRAPH_BG, fg=theme.TEXT_2, font=font(10, "bold"), padx=8, pady=4)
        self.beta_output.pack(fill="x")
        tk.Label(
            right,
            text="piecewise DC 모델 · cutoff/active/saturation 실시간 판정",
            bg=theme.PANEL,
            fg=theme.MUTED_2,
            font=font(8),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 10))
        top_frame.set_children(left_stack, right_stack)

    def value(self, field, fallback, min_value, max_value):
        v = field.get_float(default=fallback)
        if not math.isfinite(v):
            v = fallback
        return clamp(v, min_value, max_value)

    def solve(self):
        return transistor_solve(
            vcc=self.value(self.vcc_field, 12, 5, 15),
            vin=self.value(self.vin_field, 5, 0, 5),
            vbe_on=self.value(self.vbe_field, 0.7, 0.45, 0.95),
            vce_sat=self.value(self.vce_sat_field, 0.2, 0.02, 0.6),
            rb=self.rb_slider.get() * 1000,
            rc=self.rc_slider.get(),
            beta=self.beta,
        )

    def set_vin(self, value):
        value = clamp(value, self.vin_slider.min_value, self.vin_slider.max_value)
        self._syncing = True
        self.vin_slider.set(value, silent=True)
        self.vin_field.set_value(value, silent=True)
        self._syncing = False

    def on_vin_slider(self):
        if not self._syncing:
            self._syncing = True
            self.vin_field.set_value(self.vin_slider.get(), silent=True)
            self._syncing = False
        self.update_all()

    def on_vin_field(self):
        if self._syncing:
            return
        value = self.vin_field.get_float(default=None)
        if value is None or not math.isfinite(value):
            self.update_all()
            return
        self.set_vin(value)
        self.update_all()

    def set_mode(self, mode):
        self.mode = mode
        self.apply_mode_preset(mode)
        self.update_all()

    def apply_mode_preset(self, mode):
        if not hasattr(self, "vin_slider"):
            return
        if mode == "switch_off":
            self.set_vin(0.0)
            return
        if mode == "switch_on":
            self.set_vin(5.0)
            p = self.solve()["p"]
            ic_sat = max(0.0, (p["vcc"] - p["vceSat"]) / p["rc"])
            if ic_sat > 0 and p["vin"] > p["vbe"]:
                rb_limit_k = ((p["vin"] - p["vbe"]) / (ic_sat / 20)) / 1000
                self.rb_slider.set(clamp(min(3.0, rb_limit_k * 0.75), self.rb_slider.min_value, self.rb_slider.max_value), silent=True)
            return
        if mode == "bias":
            p = self.solve()["p"]
            if p["vin"] <= p["vbe"]:
                self.set_vin(max(p["vbe"] + 0.8, 1.5))
                p = self.solve()["p"]
            ic_target = p["vcc"] / (2 * p["rc"])
            ib_target = ic_target / self.beta if ic_target > 0 else math.nan
            if ib_target > 0 and p["vin"] > p["vbe"]:
                rb_target_k = ((p["vin"] - p["vbe"]) / ib_target) / 1000
                self.rb_slider.set(clamp(rb_target_k, self.rb_slider.min_value, self.rb_slider.max_value), silent=True)

    def _layout_mode_buttons(self, _event=None):
        if not hasattr(self, "modes_frame") or not hasattr(self, "mode_buttons"):
            return
        layout = [
            ("bias", 0, 0),
            ("switch_off", 0, 1),
            ("switch_on", 1, 0),
            ("beta_observe", 1, 1),
        ]
        for button in self.mode_buttons.values():
            button.grid_forget()
        self.modes_frame.columnconfigure(0, weight=1, uniform="mode_tabs")
        self.modes_frame.columnconfigure(1, weight=1, uniform="mode_tabs")
        self.modes_frame.columnconfigure(2, weight=0, uniform="")
        self.modes_frame.columnconfigure(3, weight=0, uniform="")
        for key, row, col in layout:
            self.mode_buttons[key].grid(row=row, column=col, sticky="ew", padx=3, pady=3)

    def target_range_message(self):
        result = self.result or self.solve()
        p = result["p"]
        rb_min = self.rb_slider.min_value * 1000
        rb_max = self.rb_slider.max_value * 1000
        beta_known = self.beta_visible or self.mode == "beta_observe"
        if self.mode == "switch_off":
            if p["vin"] <= p["vbe"]:
                return f"cutoff 가능: Vin({p['vin']:.2f} V) ≤ VBE({p['vbe']:.2f} V).", theme.GOOD
            target_ic = 50e-6
            required_rb = p["beta"] * (p["vin"] - p["vbe"]) / target_ic
            possible = required_rb <= rb_max
            detail = f" 거의 OFF(IC<50 µA) 필요 RB≈{format_ohm(required_rb)}." if beta_known else " 거의 OFF 가능성은 숨겨진 β 기준으로 판정합니다."
            return (
                f"완전 cutoff 불가: RB 문제가 아니라 Vin 조건 문제입니다. Vin({p['vin']:.2f} V) > VBE({p['vbe']:.2f} V)이므로 Vin을 낮추세요. "
                f"OFF 목표 자동 설정을 누르면 바로 cutoff 조건으로 이동합니다.{detail} 현재 RB 범위로 {'거의 OFF 가능' if possible else '거의 OFF도 어려움'}."
            ), theme.WARN if possible else theme.DANGER
        if self.mode == "bias":
            if p["vin"] <= p["vbe"]:
                return "active bias 불가: Vin이 VBE보다 낮아 베이스 전류가 없습니다.", theme.WARN
            ic_target = p["vcc"] / (2 * p["rc"])
            ib_target = ic_target / p["beta"]
            rb_target = (p["vin"] - p["vbe"]) / ib_target
            possible = rb_min <= rb_target <= rb_max
            exact = f" 목표 RB≈{format_ohm(rb_target)}." if beta_known else " 목표 RB 수치는 β 공개/관찰 모드에서 확인하세요."
            return f"VCE≈VCC/2 목표는 현재 RB 범위에서 {'가능' if possible else '범위 밖'}입니다.{exact}", theme.GOOD if possible else theme.WARN
        if self.mode == "switch_on":
            if p["vin"] <= p["vbe"]:
                return "ON 불가: Vin이 VBE보다 낮아 베이스 구동이 없습니다.", theme.DANGER
            ic_sat = max(0.0, (p["vcc"] - p["vceSat"]) / p["rc"])
            rb_limit = (p["vin"] - p["vbe"]) / (ic_sat / 20) if ic_sat > 0 else math.inf
            possible = rb_min <= rb_limit
            exact = f" forced β≤20 기준 RB≤{format_ohm(rb_limit)}." if beta_known else " forced β 기준 수치는 β 공개/관찰 모드에서 확인하세요."
            if not possible:
                return f"포화 ON 목표가 현재 RB 하한({format_ohm(rb_min)})보다 더 작은 RB를 요구합니다.{exact}", theme.WARN
            return f"포화 ON 목표는 현재 RB 범위에서 가능합니다.{exact}", theme.GOOD
        if result["region"] == "active" and math.isfinite(result["betaEst"]):
            return "β 관찰 가능: active 영역에서 β_est = IC/IB가 의미 있습니다.", theme.GOOD
        return "β 관찰 전 active 영역을 먼저 만드세요.", theme.WARN

    def quick_off(self):
        self.set_vin(0.0)
        self.update_all()
        if self.toast:
            self.toast.show("Transistor: OFF 목표 조건(Vin ≤ VBE)으로 설정했습니다.")

    def quick_bias(self):
        p = self.solve()["p"]
        if p["vin"] <= p["vbe"]:
            self.set_vin(max(p["vbe"] + 0.8, 1.5))
            p = self.solve()["p"]
        ic_target = p["vcc"] / (2 * p["rc"])
        ib_target = ic_target / self.beta if ic_target > 0 else math.nan
        rb_target_k = ((p["vin"] - p["vbe"]) / ib_target) / 1000 if ib_target > 0 else self.rb_slider.get()
        rb_target_k = clamp(rb_target_k, self.rb_slider.min_value, self.rb_slider.max_value)
        self.rb_slider.set(rb_target_k, silent=True)
        self.update_all()
        if self.toast:
            self.toast.show("Transistor: 중앙 Q점 근처로 RB를 보조 설정했습니다.")

    def quick_on(self):
        self.set_vin(5.0)
        p = self.solve()["p"]
        ic_sat = max(0.0, (p["vcc"] - p["vceSat"]) / p["rc"])
        rb_limit_k = ((p["vin"] - p["vbe"]) / (ic_sat / 20)) / 1000 if ic_sat > 0 and p["vin"] > p["vbe"] else 3.0
        target_rb = clamp(min(3.0, rb_limit_k * 0.75), self.rb_slider.min_value, self.rb_slider.max_value)
        self.rb_slider.set(target_rb, silent=True)
        self.update_all()
        if self.toast:
            self.toast.show("Transistor: 포화 ON 목표 근처로 Vin/RB를 보조 설정했습니다.")

    def toggle_beta(self):
        self.beta_visible = not self.beta_visible
        self.update_all()

    def refresh_beta(self):
        self.beta = random_beta()
        self.beta_visible = False
        self.update_all()
        if self.toast:
            self.toast.show("트랜지스터: β 값이 갱신되었습니다.")

    def update_all(self):
        self.result = self.solve()
        goal = build_goal(self.result, self.mode)
        insight = build_mode_insight(self.result, goal, self.mode, self.beta_visible)
        hint, good = make_hint(self.result, goal, insight, self.mode)
        if goal["status"] == "success":
            self.success_banner.configure(text=f"{goal['banner_title']} · {goal['banner_detail']}")
            if not self.success_banner.winfo_ismapped():
                self.success_banner.pack(fill="x", padx=10, pady=(0, 5), before=self.graph_help)
        else:
            self.success_banner.pack_forget()
        for key, button in self.mode_buttons.items():
            set_button_variant(button, key == self.mode)
        set_button_variant(self.quick_off_btn, self.mode == "switch_off")
        set_button_variant(self.quick_bias_btn, self.mode == "bias")
        set_button_variant(self.quick_on_btn, self.mode == "switch_on")
        self.graph_help.configure(text=goal["graph_help"])
        self.graph_meta.set(f"{region_ko(self.result['region'])} · {goal['label']} · 달성도 {goal['score']}/100")
        target_text, target_color = self.target_range_message()
        self.target_metric.set(target_text, target_color)
        self.graph.set_state(self.result, self.mode, region_ko(self.result["region"]))
        self.metrics["region"].set(region_ko(self.result["region"]))
        self.metrics["ib"].set(format_micro_amp(self.result["ib"]))
        self.metrics["ic"].set(format_milli_amp(self.result["ic"]))
        self.metrics["vce"].set(format_volt(self.result["vce"]))
        self.metrics["vc"].set(format_volt(self.result["vc"]))
        self.metrics["power"].set(format_power(self.result["power"]))
        self.metrics["mission"].set(f"{goal['status_text']} · {goal['score']}/100", theme.GOOD if goal["status"] == "success" else theme.WARN if goal["status"] == "almost" else theme.DANGER)
        self.metrics["hint"].set(hint, theme.GOOD if good else theme.WARN)
        self.metrics["beta_state"].set(f"공개됨: β = {self.beta}" if self.beta_visible else "숨김")
        if self.mode == "beta_observe" and self.result["region"] == "active" and math.isfinite(self.result["betaEst"]):
            self.metrics["beta_est"].set(f"{self.result['betaEst']:.1f} · active에서 IC/IB")
        elif self.result["region"] == "saturation":
            self.metrics["beta_est"].set("포화 영역에서는 실제 β 추정 불가", theme.WARN)
        elif self.result["region"] == "active":
            self.metrics["beta_est"].set("β 관찰 모드에서 자세히 확인")
        else:
            self.metrics["beta_est"].set("cutoff에서는 측정 불가", theme.WARN)
        if self.result["region"] == "saturation" and math.isfinite(self.result["forcedBeta"]):
            self.metrics["forced"].set(f"{self.result['forcedBeta']:.1f} · 포화 구동 여유")
        elif self.result["region"] == "active":
            self.metrics["forced"].set("active에서는 평가 대상 아님")
        else:
            self.metrics["forced"].set("포화 스위칭에서 평가")
        goal_wrap = 420
        self.goal_summary.configure(text=goal["summary"], wraplength=goal_wrap)
        self.goal_score.set(f"{goal['score']} / 100", theme.GOOD if goal["status"] == "success" else theme.TEXT_2)
        for child in self.checklist.winfo_children():
            child.destroy()
        for check in goal["checks"]:
            color = theme.GOOD if check["state"] == "pass" else theme.WARN if check["state"] == "near" else theme.DANGER
            label = tk.Label(
                self.checklist,
                text=f"● {check['title']} · {check['detail']}",
                bg=theme.PANEL,
                fg=color,
                font=font(8),
                justify="left",
                anchor="w",
                wraplength=goal_wrap,
            )
            label.pack(fill="x", pady=1)
        insight_text = insight["summary"] + "\n" + " · ".join(f"{k}: {v}" for k, v in insight["metrics"])
        if self.mode == "beta_observe":
            insight_text += "\n단순 모델에서는 active 영역에서 IC = βIB이므로 β_est가 실제 β와 거의 같게 유지됩니다."
        self.insight.set(insight_text, theme.TEXT_2)
        self.controls_intro.configure(text=goal["controls_intro"])
        self.beta_output.configure(text=f"β = {self.beta}" if self.beta_visible else "β = ???")
        self.diagram.redraw()

    def on_show(self):
        self.scroll.scroll_to_top()
        self._tick()

    def on_hide(self):
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None

    def _tick(self):
        if self.result:
            self.graph.draw()
        self._after_id = self.after(16, self._tick)
