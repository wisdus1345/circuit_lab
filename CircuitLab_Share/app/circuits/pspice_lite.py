import json
import math
import queue
import threading
import tkinter as tk
from bisect import bisect_left
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, simpledialog
from typing import Optional

from .. import theme
from ..widgets import (
    CompactMetricBox,
    NumberField,
    Panel,
    SectionHeader,
    font,
    make_button,
    parse_engineering_value,
    set_button_variant,
)


GRID = 20
MAX_TRANSIENT_STEPS = 200000
WORKER_TRANSIENT_STEPS = 5000
MAX_PLOT_POINTS = 10000
UNSET = object()
TRACE_COLORS = [
    "#22d3ee", "#facc15", "#fb7185", "#a78bfa", "#34d399",
    "#f97316", "#60a5fa", "#f472b6", "#bef264", "#c084fc",
]


def nice_step(span, target=6):
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / max(target, 1)
    power = 10 ** math.floor(math.log10(raw))
    fraction = raw / power
    if fraction <= 1:
        nice = 1
    elif fraction <= 2:
        nice = 2
    elif fraction <= 5:
        nice = 5
    else:
        nice = 10
    return nice * power


def nice_linear_ticks(vmin, vmax, target=6):
    if not math.isfinite(vmin) or not math.isfinite(vmax):
        return [0.0]
    if abs(vmax - vmin) < 1e-15:
        return [vmin]
    step = nice_step(vmax - vmin, target)
    start = math.ceil(vmin / step) * step
    ticks = []
    value = start
    limit = vmax + step * 0.5
    while value <= limit and len(ticks) < 20:
        if value >= vmin - step * 0.5:
            ticks.append(0.0 if abs(value) < abs(step) * 1e-10 else value)
        value += step
    return ticks or [vmin, vmax]


SUFFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
}


def parse_value(text, default=None):
    raw = str(text).strip().lower().replace("ohm", "").replace("ω", "")
    if not raw:
        return default
    for suffix in ("meg", "µ", "p", "n", "u", "m", "k", "g"):
        if raw.endswith(suffix):
            try:
                return float(raw[: -len(suffix)]) * SUFFIXES[suffix]
            except ValueError:
                return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_value(text, default=None):
    # [1번] widgets.py의 공용 engineering parser를 사용해 M/m, u/µ, Hz/ms 같은 표기를 일관 처리한다.
    return parse_engineering_value(text, default)


def parse_finite_value(text, default=None):
    value = parse_value(text, None)
    if value is None:
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def format_eng(value, unit=""):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(value) < 1e-15:
        return f"0 {unit}".strip()
    prefixes = [
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "µ"),
        (1e-9, "n"),
        (1e-12, "p"),
    ]
    av = abs(value)
    scale, prefix = prefixes[-1]
    for s, p in prefixes:
        if av >= s:
            scale, prefix = s, p
            break
    return f"{value / scale:.4g} {prefix}{unit}".strip()


def minmax_downsample(samples, max_points=MAX_PLOT_POINTS):
    """Display-only peak preserving downsample. Raw samples stay available for CSV."""
    if len(samples) <= max_points or max_points < 4:
        return list(samples)
    bucket_count = max_points // 2
    bucket_size = max(1, math.ceil(len(samples) / bucket_count))
    reduced = []
    for start in range(0, len(samples), bucket_size):
        bucket = samples[start : start + bucket_size]
        if not bucket:
            continue
        lo = min(bucket, key=lambda item: item[1])
        hi = max(bucket, key=lambda item: item[1])
        ordered = sorted([lo, hi], key=lambda item: item[0])
        for point in ordered:
            if not reduced or reduced[-1] != point:
                reduced.append(point)
    if samples[-1] not in reduced:
        reduced.append(samples[-1])
    return reduced[:max_points]


def safe_exp(x):
    # [6번] Diode NR: prevent Shockley exponential overflow while keeping a very large forward slope.
    return math.exp(min(x, 500.0))


def gaussian_solve(a, b):
    n = len(b)
    if n == 0:
        return []
    a = [list(map(float, row)) for row in a]
    b = list(map(float, b))
    # [5번] AC sweep uses gaussian_solve_complex(); keep this real solver separate for DC/transient.
    # FIX 4-2: Use a matrix-norm relative pivot threshold instead of a fixed one.
    max_element = max((abs(value) for row in a for value in row), default=1.0)
    threshold = max(max_element * 1e-12, 1e-18)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < threshold:
            raise ValueError("singular")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]
        div = a[col][col]
        for j in range(col, n):
            a[col][j] /= div
        b[col] /= div
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if abs(factor) < 1e-18:
                continue
            for j in range(col, n):
                a[r][j] -= factor * a[col][j]
            b[r] -= factor * b[col]
    return b


def gaussian_solve_complex(a, b):
    n = len(b)
    if n == 0:
        return []
    a = [list(map(complex, row)) for row in a]
    b = list(map(complex, b))
    max_element = max((abs(value) for row in a for value in row), default=1.0)
    threshold = max(max_element * 1e-12, 1e-18)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < threshold:
            raise ValueError("singular")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]
        div = a[col][col]
        for j in range(col, n):
            a[col][j] /= div
        b[col] /= div
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if abs(factor) < 1e-18:
                continue
            for j in range(col, n):
                a[r][j] -= factor * a[col][j]
            b[r] -= factor * b[col]
    return b


def point_on_segment(p, a, b):
    x, y = p
    ax, ay = a
    bx, by = b
    if ax == bx and x == ax and min(ay, by) <= y <= max(ay, by):
        return True
    if ay == by and y == ay and min(ax, bx) <= x <= max(ax, bx):
        return True
    return False


def segment_intersection(a, b, c, d):
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    if ay == by and cx == dx:
        if min(ax, bx) <= cx <= max(ax, bx) and min(cy, dy) <= ay <= max(cy, dy):
            return (cx, ay)
    if ax == bx and cy == dy:
        if min(ay, by) <= cy <= max(ay, by) and min(cx, dx) <= ax <= max(cx, dx):
            return (ax, cy)
    return None


def collinear_overlap_points(a, b, c, d):
    points = []
    if a[1] == b[1] == c[1] == d[1]:
        for p in (a, b):
            if point_on_segment(p, c, d):
                points.append(p)
        for p in (c, d):
            if point_on_segment(p, a, b):
                points.append(p)
    elif a[0] == b[0] == c[0] == d[0]:
        for p in (a, b):
            if point_on_segment(p, c, d):
                points.append(p)
        for p in (c, d):
            if point_on_segment(p, a, b):
                points.append(p)
    return points


class UnionFind:
    def __init__(self):
        self.parent = {}

    def add(self, x):
        self.parent.setdefault(x, x)

    def find(self, x):
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class CircuitElement:
    id: int
    kind: str
    name: str
    value: str
    x: int
    y: int
    rotation: int = 0
    params: dict = field(default_factory=dict)

    def numeric_value(self):
        return parse_value(self.value, 0.0)

    def diode_params(self):
        return {
            "is": max(parse_value(self.params.get("is", "1e-14"), 1e-14), 1e-30),
            "n": max(parse_value(self.params.get("n", "1"), 1.0), 1e-9),
            "vt": max(parse_value(self.params.get("vt", "0.02585"), 0.02585), 1e-9),
        }

    def initial_cap_voltage(self):
        raw = self.params.get("ic_v", self.params.get("ic", "0"))
        return parse_finite_value(raw, 0.0)

    def initial_ind_current(self):
        raw = self.params.get("ic_i", self.params.get("ic", "0"))
        return parse_finite_value(raw, 0.0)

    def terminals(self):
        if self.kind == "gnd":
            return [(self.x, self.y)]
        horizontal = self.rotation % 180 == 0
        if horizontal:
            return [(self.x - 40, self.y), (self.x + 40, self.y)]
        return [(self.x, self.y - 40), (self.x, self.y + 40)]

    def source_value_at(self, t=0.0):
        waveform = str(self.params.get("waveform", "sin" if self.kind == "vac" else "dc")).strip().lower()
        if waveform == "pulse":
            v1 = parse_value(self.params.get("v1", "0"), 0.0)
            v2 = parse_value(self.params.get("v2", self.value), self.numeric_value())
            td = max(parse_value(self.params.get("td", "0"), 0.0), 0.0)
            tr = max(parse_value(self.params.get("tr", "1n"), 1e-9), 1e-15)
            tf = max(parse_value(self.params.get("tf", "1n"), 1e-9), 1e-15)
            pw = max(parse_value(self.params.get("pw", "1m"), 1e-3), 0.0)
            per = max(parse_value(self.params.get("per", "2m"), 2e-3), tr + tf + pw + 1e-15)
            if t < td:
                return v1
            phase = (t - td) % per
            if phase < tr:
                return v1 + (v2 - v1) * phase / tr
            if phase < tr + pw:
                return v2
            if phase < tr + pw + tf:
                return v2 + (v1 - v2) * (phase - tr - pw) / tf
            return v1
        if waveform == "pwl":
            raw = str(self.params.get("pwl", "")).replace(",", " ").split()
            pairs = []
            for i in range(0, len(raw) - 1, 2):
                tt = parse_value(raw[i], None)
                vv = parse_value(raw[i + 1], None)
                if tt is not None and vv is not None:
                    pairs.append((tt, vv))
            pairs.sort()
            if not pairs:
                return self.numeric_value()
            if t <= pairs[0][0]:
                return pairs[0][1]
            for (t0, v0), (t1, v1) in zip(pairs, pairs[1:]):
                if t0 <= t <= t1:
                    if abs(t1 - t0) < 1e-18:
                        return v1
                    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
            return pairs[-1][1]
        if waveform == "sin" or self.kind == "vac":
            # FIX 1-5: VAC amplitude is the element value. params["amplitude"] is legacy only.
            offset = parse_value(self.params.get("offset", "0"), 0.0)
            amp = self.numeric_value()
            freq = parse_value(self.params.get("frequency", "1k"), 1000.0)
            phase = math.radians(parse_value(self.params.get("phase", "0"), 0.0))
            return offset + amp * math.sin(2 * math.pi * freq * t + phase)
        return self.numeric_value()

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "value": self.value,
            "x": self.x,
            "y": self.y,
            "rotation": self.rotation,
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            int(data["id"]),
            data["kind"],
            data["name"],
            data.get("value", ""),
            int(data["x"]),
            int(data["y"]),
            int(data.get("rotation", 0)),
            dict(data.get("params", {})),
        )


@dataclass
class Wire:
    id: int
    points: list

    def to_dict(self):
        return {"id": self.id, "points": [list(p) for p in self.points]}

    @classmethod
    def from_dict(cls, data):
        return cls(int(data["id"]), [tuple(map(int, p)) for p in data["points"]])


@dataclass
class MeasurementLabel:
    id: int
    kind: str
    name: str
    point: Optional[tuple] = None
    element_id: Optional[int] = None

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "point": list(self.point) if self.point is not None else None,
            "element_id": self.element_id,
        }

    @classmethod
    def from_dict(cls, data):
        point = data.get("point")
        return cls(
            int(data["id"]),
            data["kind"],
            data["name"],
            tuple(point) if point is not None else None,
            int(data["element_id"]) if data.get("element_id") is not None else None,
        )


class CircuitModel:
    def __init__(self):
        self.elements = []
        self.wires = []
        self.next_id = 1
        self.counters = {"r": 0, "c": 0, "l": 0, "d": 0, "vdc": 0, "vac": 0, "isrc": 0, "gnd": 0}

    def clear(self):
        self.elements.clear()
        self.wires.clear()
        self.next_id = 1
        self.counters = {k: 0 for k in self.counters}

    def new_id(self):
        value = self.next_id
        self.next_id += 1
        return value

    def add_element(self, kind, x, y):
        defaults = {
            "r": ("R", "1k"),
            "c": ("C", "1u"),
            "l": ("L", "1m"),
            "d": ("D", "Is=1e-14"),
            "vdc": ("V", "5"),
            "vac": ("VAC", "1"),
            "isrc": ("I", "1m"),
            "gnd": ("GND", "0"),
        }
        prefix, value = defaults[kind]
        self.counters[kind] += 1
        name = "GND" if kind == "gnd" else f"{prefix}{self.counters[kind]}"
        params = {}
        if kind == "vac":
            params = {"waveform": "sin", "offset": "0", "frequency": "1k", "phase": "0", "ac_mag": value, "ac_phase": "0"}
        elif kind == "isrc":
            params = {"waveform": "dc", "ac_mag": "0", "ac_phase": "0"}
        elif kind == "d":
            params = {"is": "1e-14", "n": "1", "vt": "0.02585"}
        el = CircuitElement(self.new_id(), kind, name, value, x, y, 0, params)
        self.elements.append(el)
        return el

    def add_wire(self, points):
        if len(points) < 2:
            return None
        wire = Wire(self.new_id(), points)
        self.wires.append(wire)
        return wire

    def remove(self, obj):
        if isinstance(obj, CircuitElement):
            # FIX 1-1: model stays UI-state free; page handles property target cleanup.
            self.elements = [e for e in self.elements if e.id != obj.id]
        elif isinstance(obj, Wire):
            self.wires = [w for w in self.wires if w.id != obj.id]

    def to_dict(self):
        return {
            "elements": [e.to_dict() for e in self.elements],
            "wires": [w.to_dict() for w in self.wires],
            "next_id": self.next_id,
            "counters": self.counters,
        }

    def from_dict(self, data):
        self.elements = [CircuitElement.from_dict(e) for e in data.get("elements", [])]
        self.wires = [Wire.from_dict(w) for w in data.get("wires", [])]
        self.next_id = int(data.get("next_id", 1))
        self.counters.update(data.get("counters", {}))


class CircuitSolver:
    def __init__(self, model):
        self.model = model

    def build_nodes(self):
        uf = UnionFind()
        terminal_points = []
        for el in self.model.elements:
            for p in el.terminals():
                uf.add(p)
                terminal_points.append(p)
        wire_segments = []
        for wire in self.model.wires:
            for p in wire.points:
                uf.add(p)
            for a, b in zip(wire.points, wire.points[1:]):
                wire_segments.append((a, b))
                uf.union(a, b)
                for tp in terminal_points:
                    if point_on_segment(tp, a, b):
                        uf.union(tp, a)
                for other in self.model.wires:
                    for p in other.points:
                        if point_on_segment(p, a, b):
                            uf.union(p, a)
        for i, (a, b) in enumerate(wire_segments):
            for c, d in wire_segments[i + 1 :]:
                p = segment_intersection(a, b, c, d)
                if p is not None:
                    # PSpice-style rule: wires that merely cross are not connected.
                    # Only endpoints/T-junctions are joined here; plain X/+ crossings stay separate.
                    if p in (a, b, c, d):
                        uf.add(p)
                        uf.union(p, a)
                        uf.union(p, c)
                # FIX 1-4: also connect overlapping collinear wire segments.
                overlap = collinear_overlap_points(a, b, c, d)
                if overlap:
                    anchor = overlap[0]
                    uf.add(anchor)
                    uf.union(anchor, a)
                    uf.union(anchor, c)
                    for op in overlap[1:]:
                        uf.add(op)
                        uf.union(anchor, op)
        ground_roots = set()
        for el in self.model.elements:
            if el.kind == "gnd":
                ground_roots.add(uf.find(el.terminals()[0]))
        if not ground_roots:
            raise ValueError("GND가 없습니다. 접지를 하나 배치하세요.")
        terminal_roots = {uf.find(p) for p in terminal_points}
        root_to_points = {}
        for p in list(uf.parent):
            root = uf.find(p)
            if root in terminal_roots:
                root_to_points.setdefault(root, []).append(p)
        roots = {root: "0" for root in ground_roots if root in terminal_roots}
        ordered_roots = sorted(
            (root for root in root_to_points if root not in ground_roots),
            key=lambda root: min(root_to_points[root]),
        )
        for idx, root in enumerate(ordered_roots, start=1):
            roots[root] = f"n{idx}"
        point_node = {p: roots[uf.find(p)] for p in uf.parent if uf.find(p) in roots}
        return uf, point_node

    def node_for_point(self, point, point_node):
        if point in point_node:
            return point_node[point]
        for wire in self.model.wires:
            for a, b in zip(wire.points, wire.points[1:]):
                if point_on_segment(point, a, b):
                    return point_node.get(a) or point_node.get(b)
        for el in self.model.elements:
            for terminal in el.terminals():
                if terminal == point:
                    return point_node.get(terminal)
        return None

    def solve_dc(self, cap_prev=None, ind_prev=None, dt=None, t=0.0, integration="be"):
        _uf, point_node = self.build_nodes()
        nodes = sorted({node for node in point_node.values() if node != "0"})
        node_index = {node: i for i, node in enumerate(nodes)}
        vsrcs = [e for e in self.model.elements if e.kind in ("vdc", "vac", "l")]
        node_count = len(nodes)
        size = node_count + len(vsrcs)
        if size == 0:
            return {"nodes": {"0": 0.0}, "currents": {}, "point_node": point_node}

        def idx(node):
            return None if node == "0" else node_index[node]

        def cap_state(el):
            raw = (cap_prev or {}).get(el.id, 0.0)
            if isinstance(raw, tuple):
                return raw[0], raw[1] if len(raw) > 1 else 0.0
            return raw, 0.0

        def ind_state(el):
            raw = (ind_prev or {}).get(el.id, 0.0)
            if isinstance(raw, tuple):
                return raw[0], raw[1] if len(raw) > 1 else 0.0
            return raw, 0.0

        def make_matrix(guess_nodes):
            a = [[0.0 for _ in range(size)] for _ in range(size)]
            b = [0.0 for _ in range(size)]
            vsrc_index = {}

            def stamp_g(np, nn, g):
                ip, inn = idx(np), idx(nn)
                if ip is not None:
                    a[ip][ip] += g
                if inn is not None:
                    a[inn][inn] += g
                if ip is not None and inn is not None:
                    a[ip][inn] -= g
                    a[inn][ip] -= g

            def stamp_i(np, nn, current):
                ip, inn = idx(np), idx(nn)
                if ip is not None:
                    b[ip] -= current
                if inn is not None:
                    b[inn] += current

            for el in self.model.elements:
                terms = el.terminals()
                if el.kind == "gnd":
                    continue
                np, nn = point_node.get(terms[0], "0"), point_node.get(terms[1], "0")
                if el.kind in ("vdc", "vac") and np == nn:
                    raise ValueError(
                        f"{el.name}의 양단이 모두 {np} 노드입니다. 전압원이 단락되었습니다. "
                        "도선 교차 연결 또는 junction 설정을 확인하세요."
                    )
                if el.kind == "r":
                    resistance = max(abs(el.numeric_value()), 1e-12)
                    stamp_g(np, nn, 1.0 / resistance)
                elif el.kind == "d":
                    # FIX 2-1: Newton-Raphson diode companion model.
                    params = el.diode_params()
                    vd = guess_nodes.get(np, 0.0) - guess_nodes.get(nn, 0.0)
                    arg = max(-40.0, min(40.0, vd / (params["n"] * params["vt"])))
                    exp_v = safe_exp(arg)
                    conductance = max(params["is"] / (params["n"] * params["vt"]) * exp_v, 1e-9)
                    current = params["is"] * (exp_v - 1.0)
                    i_eq = current - conductance * vd
                    stamp_g(np, nn, conductance)
                    stamp_i(np, nn, i_eq)
                elif el.kind == "isrc":
                    stamp_i(np, nn, el.source_value_at(t))
                elif el.kind == "c":
                    if cap_prev is not None and dt and dt > 0:
                        capacitance = max(abs(el.numeric_value()), 1e-18)
                        vprev, iprev = cap_state(el)
                        if integration == "trap":
                            conductance = 2.0 * capacitance / dt
                            i_eq = -conductance * vprev - iprev
                        else:
                            conductance = capacitance / dt
                            i_eq = -conductance * vprev
                        stamp_g(np, nn, conductance)
                        stamp_i(np, nn, i_eq)
                elif el.kind in ("vdc", "vac", "l"):
                    row = node_count + len(vsrc_index)
                    vsrc_index[el.id] = row
                    ip, inn = idx(np), idx(nn)
                    if ip is not None:
                        a[ip][row] += 1
                        a[row][ip] += 1
                    if inn is not None:
                        a[inn][row] -= 1
                        a[row][inn] -= 1
                    if el.kind == "l":
                        if ind_prev is not None and dt and dt > 0:
                            inductance = max(abs(el.numeric_value()), 1e-18)
                            iprev, vprev = ind_state(el)
                            r_eq = (2.0 * inductance / dt) if integration == "trap" else (inductance / dt)
                            a[row][row] -= r_eq
                            b[row] = -r_eq * iprev - (vprev if integration == "trap" else 0.0)
                        else:
                            b[row] = 0.0
                    elif el.kind == "vac" and cap_prev is None and dt is None:
                        # [5번][15번] DC OP에서는 transient sine이 아니라 VAC의 VOFF/DC offset만 사용한다.
                        b[row] = parse_value(el.params.get("offset", "0"), 0.0)
                    else:
                        b[row] = el.source_value_at(t)
            return a, b, vsrc_index

        diode_present = any(el.kind == "d" for el in self.model.elements)
        guess = {"0": 0.0}
        guess.update({node: 0.0 for node in nodes})
        solution = None
        vsrc_index = {}
        iterations = 50 if diode_present else 1
        for _iteration in range(iterations):
            a, b, vsrc_index = make_matrix(guess)
            try:
                solution = gaussian_solve(a, b)
            except ValueError:
                raise ValueError("떠 있는 노드 또는 특이 회로입니다. GND와 배선을 확인하세요.")
            next_guess = {"0": 0.0}
            for node, i in node_index.items():
                next_guess[node] = solution[i]
            if diode_present:
                # [6번] Clamp each diode voltage correction to roughly 2*n*Vt for stable NR steps.
                for el in self.model.elements:
                    if el.kind != "d":
                        continue
                    p, n = el.terminals()
                    np, nn = point_node.get(p, "0"), point_node.get(n, "0")
                    old_vd = guess.get(np, 0.0) - guess.get(nn, 0.0)
                    new_vd = next_guess.get(np, 0.0) - next_guess.get(nn, 0.0)
                    params = el.diode_params()
                    limit = max(2.0 * params["n"] * params["vt"], 1e-6)
                    delta = new_vd - old_vd
                    if abs(delta) > limit:
                        clamped_vd = old_vd + math.copysign(limit, delta)
                        adjust = clamped_vd - new_vd
                        if np != "0" and nn != "0":
                            next_guess[np] += adjust / 2.0
                            next_guess[nn] -= adjust / 2.0
                        elif np != "0":
                            next_guess[np] += adjust
                        elif nn != "0":
                            next_guess[nn] -= adjust
            delta = max((abs(next_guess.get(node, 0.0) - guess.get(node, 0.0)) for node in next_guess), default=0.0)
            guess = next_guess
            if not diode_present or delta < 1e-9:
                break
        else:
            raise ValueError(
                "다이오드 회로가 반복 계산 안에 수렴하지 못했습니다.\n"
                "원인: 강한 비선형 조건이거나 직렬 저항이 너무 작을 수 있습니다.\n"
                "해결: R 값을 키우거나 DC 입력 전압을 줄여보세요."
            )

        node_voltages = guess
        currents = {}
        for el in self.model.elements:
            if el.kind == "gnd":
                continue
            terms = el.terminals()
            np, nn = point_node.get(terms[0], "0"), point_node.get(terms[1], "0")
            vp, vn = node_voltages.get(np, 0.0), node_voltages.get(nn, 0.0)
            vd = vp - vn
            if el.kind == "r":
                current = vd / max(abs(el.numeric_value()), 1e-12)
            elif el.kind == "d":
                params = el.diode_params()
                arg = max(-40.0, min(40.0, vd / (params["n"] * params["vt"])))
                current = params["is"] * (safe_exp(arg) - 1.0)
            elif el.kind == "c":
                if cap_prev is None or not dt:
                    current = 0.0
                else:
                    capacitance = max(abs(el.numeric_value()), 1e-18)
                    vprev, iprev = cap_state(el)
                    if integration == "trap":
                        conductance = 2.0 * capacitance / dt
                        current = conductance * vd - conductance * vprev - iprev
                    else:
                        current = capacitance * (vd - vprev) / dt
            elif el.kind == "isrc":
                current = el.source_value_at(t)
            elif el.kind in ("vdc", "vac", "l"):
                current = solution[vsrc_index[el.id]]
            else:
                current = 0.0
            currents[el.name] = current
        return {"nodes": node_voltages, "currents": currents, "point_node": point_node}

    def solve_transient_initial_state(self, t=0.0):
        """Solve the exact t=0 state for transient ICs before any companion step."""
        _uf, point_node = self.build_nodes()
        nodes = sorted({node for node in point_node.values() if node != "0"})
        node_index = {node: i for i, node in enumerate(nodes)}
        ideal_voltage_elements = [e for e in self.model.elements if e.kind in ("vdc", "vac", "c")]
        node_count = len(nodes)
        size = node_count + len(ideal_voltage_elements)
        if size == 0:
            return {"nodes": {"0": 0.0}, "currents": {}, "point_node": point_node}

        def idx(node):
            return None if node == "0" else node_index[node]

        a = [[0.0 for _ in range(size)] for _ in range(size)]
        b = [0.0 for _ in range(size)]
        vsrc_index = {}

        def stamp_g(np, nn, g):
            ip, inn = idx(np), idx(nn)
            if ip is not None:
                a[ip][ip] += g
            if inn is not None:
                a[inn][inn] += g
            if ip is not None and inn is not None:
                a[ip][inn] -= g
                a[inn][ip] -= g

        def stamp_i(np, nn, current):
            ip, inn = idx(np), idx(nn)
            if ip is not None:
                b[ip] -= current
            if inn is not None:
                b[inn] += current

        def stamp_v(el, np, nn, voltage):
            row = node_count + len(vsrc_index)
            vsrc_index[el.id] = row
            if np == nn:
                if abs(voltage) > 1e-12:
                    raise ValueError(
                        "초기조건 해석 실패\n"
                        f"원인: {el.name}이 같은 노드 {np}에 {format_eng(voltage, 'V')}를 강제합니다.\n"
                        "해결: 초기조건 또는 전압원 연결을 확인하세요."
                    )
                a[row][row] = 1.0
                return
            ip, inn = idx(np), idx(nn)
            if ip is not None:
                a[ip][row] += 1
                a[row][ip] += 1
            if inn is not None:
                a[inn][row] -= 1
                a[row][inn] -= 1
            b[row] = voltage

        for el in self.model.elements:
            if el.kind == "gnd":
                continue
            terms = el.terminals()
            np, nn = point_node.get(terms[0], "0"), point_node.get(terms[1], "0")
            if el.kind == "r":
                resistance = max(abs(el.numeric_value()), 1e-12)
                stamp_g(np, nn, 1.0 / resistance)
            elif el.kind == "d":
                params = el.diode_params()
                conductance = max(params["is"] / (params["n"] * params["vt"]), 1e-9)
                stamp_g(np, nn, conductance)
            elif el.kind == "isrc":
                stamp_i(np, nn, el.source_value_at(t))
            elif el.kind == "l":
                stamp_i(np, nn, el.initial_ind_current())
            elif el.kind == "c":
                stamp_v(el, np, nn, el.initial_cap_voltage())
            elif el.kind in ("vdc", "vac"):
                stamp_v(el, np, nn, el.source_value_at(t))

        try:
            solution = gaussian_solve(a, b)
        except ValueError as exc:
            raise ValueError(
                "초기조건 해석 실패\n"
                "원인: C/L 초기조건이 이상 전압원/전류원 조건과 충돌하거나 회로가 떠 있을 수 있습니다.\n"
                "해결: 초기조건을 0으로 바꾸거나 회로 연결과 GND를 확인하세요."
            ) from exc

        node_voltages = {"0": 0.0}
        for node, i in node_index.items():
            node_voltages[node] = solution[i]

        currents = {}
        for el in self.model.elements:
            if el.kind == "gnd":
                continue
            terms = el.terminals()
            np, nn = point_node.get(terms[0], "0"), point_node.get(terms[1], "0")
            vp, vn = node_voltages.get(np, 0.0), node_voltages.get(nn, 0.0)
            vd = vp - vn
            if el.kind == "r":
                current = vd / max(abs(el.numeric_value()), 1e-12)
            elif el.kind == "d":
                params = el.diode_params()
                arg = max(-40.0, min(40.0, vd / (params["n"] * params["vt"])))
                current = params["is"] * (safe_exp(arg) - 1.0)
            elif el.kind == "isrc":
                current = el.source_value_at(t)
            elif el.kind == "l":
                current = el.initial_ind_current()
            elif el.kind in ("vdc", "vac", "c"):
                current = solution[vsrc_index[el.id]] if el.id in vsrc_index else 0.0
            else:
                current = 0.0
            currents[el.name] = current
        return {"nodes": node_voltages, "currents": currents, "point_node": point_node}

    # FIX 1-2: removed the old single-trace solve_transient(); solve_transient_traces()
    # is the only transient entry point used by the page.

    def solve_ac(self, frequency):
        if frequency <= 0:
            raise ValueError("AC sweep frequency는 0보다 커야 합니다.")
        _uf, point_node = self.build_nodes()
        nodes = sorted({node for node in point_node.values() if node != "0"})
        node_index = {node: i for i, node in enumerate(nodes)}
        vsrcs = [e for e in self.model.elements if e.kind in ("vdc", "vac")]
        node_count = len(nodes)
        size = node_count + len(vsrcs)
        if size == 0:
            return {"nodes": {"0": 0j}, "currents": {}, "point_node": point_node}
        a = [[0j for _ in range(size)] for _ in range(size)]
        b = [0j for _ in range(size)]
        omega = 2 * math.pi * frequency

        def idx(node):
            return None if node == "0" else node_index[node]

        def stamp_y(np, nn, admittance):
            ip, inn = idx(np), idx(nn)
            if ip is not None:
                a[ip][ip] += admittance
            if inn is not None:
                a[inn][inn] += admittance
            if ip is not None and inn is not None:
                a[ip][inn] -= admittance
                a[inn][ip] -= admittance

        def stamp_i(np, nn, current):
            ip, inn = idx(np), idx(nn)
            if ip is not None:
                b[ip] -= current
            if inn is not None:
                b[inn] += current

        vsrc_index = {}
        for el in self.model.elements:
            if el.kind == "gnd":
                continue
            t1, t2 = el.terminals()
            np, nn = point_node.get(t1, "0"), point_node.get(t2, "0")
            if el.kind in ("vdc", "vac") and np == nn:
                raise ValueError(
                    f"{el.name}의 양단이 모두 {np} 노드입니다. 전압원이 단락되었습니다. "
                    "도선 교차 연결 또는 junction 설정을 확인하세요."
                )
            if el.kind == "r":
                stamp_y(np, nn, 1.0 / max(abs(el.numeric_value()), 1e-12))
            elif el.kind == "c":
                stamp_y(np, nn, 1j * omega * max(abs(el.numeric_value()), 1e-18))
            elif el.kind == "l":
                stamp_y(np, nn, 1.0 / (1j * omega * max(abs(el.numeric_value()), 1e-18)))
            elif el.kind == "d":
                params = el.diode_params()
                stamp_y(np, nn, max(params["is"] / (params["n"] * params["vt"]), 1e-9))
            elif el.kind in ("vdc", "vac"):
                row = node_count + len(vsrc_index)
                vsrc_index[el.id] = row
                ip, inn = idx(np), idx(nn)
                if ip is not None:
                    a[ip][row] += 1
                    a[row][ip] += 1
                if inn is not None:
                    a[inn][row] -= 1
                    a[row][inn] -= 1
                if el.kind == "vac":
                    amplitude = parse_value(el.params.get("ac_mag", el.value), parse_value(el.value, 1.0))
                    phase = math.radians(parse_value(el.params.get("ac_phase", el.params.get("phase", "0")), 0.0))
                    b[row] = amplitude * complex(math.cos(phase), math.sin(phase))
                else:
                    b[row] = 0j
            elif el.kind == "isrc":
                amplitude = parse_value(el.params.get("ac_mag", "0"), 0.0)
                phase = math.radians(parse_value(el.params.get("ac_phase", "0"), 0.0))
                stamp_i(np, nn, amplitude * complex(math.cos(phase), math.sin(phase)))
        try:
            solution = gaussian_solve_complex(a, b)
        except ValueError:
            raise ValueError("AC 해석 중 특이 행렬이 발생했습니다. GND와 배선을 확인하세요.")
        node_voltages = {"0": 0j}
        for node, i in node_index.items():
            node_voltages[node] = solution[i]
        currents = {}
        for el in self.model.elements:
            if el.kind == "gnd":
                continue
            t1, t2 = el.terminals()
            np, nn = point_node.get(t1, "0"), point_node.get(t2, "0")
            vp, vn = node_voltages.get(np, 0j), node_voltages.get(nn, 0j)
            vd = vp - vn
            if el.kind == "r":
                current = vd / max(abs(el.numeric_value()), 1e-12)
            elif el.kind == "c":
                current = vd * 1j * omega * max(abs(el.numeric_value()), 1e-18)
            elif el.kind == "l":
                current = vd / (1j * omega * max(abs(el.numeric_value()), 1e-18))
            elif el.kind == "d":
                params = el.diode_params()
                current = vd * params["is"] / (params["n"] * params["vt"])
            elif el.kind in ("vdc", "vac"):
                current = solution[vsrc_index[el.id]]
            elif el.kind == "isrc":
                amplitude = parse_value(el.params.get("ac_mag", "0"), 0.0)
                phase = math.radians(parse_value(el.params.get("ac_phase", "0"), 0.0))
                current = amplitude * complex(math.cos(phase), math.sin(phase))
            else:
                current = 0j
            currents[el.name] = current
        return {"nodes": node_voltages, "currents": currents, "point_node": point_node}

    def solve_ac_sweep(self, f_start, f_stop, points_per_decade=50):
        if f_start <= 0 or f_stop <= 0 or f_stop <= f_start:
            raise ValueError("AC sweep 범위는 0보다 크고 f_stop > f_start 이어야 합니다.")
        points_per_decade = max(1, int(points_per_decade))
        decades = math.log10(f_stop / f_start)
        count = max(2, int(decades * points_per_decade) + 1)
        freqs = [f_start * (10 ** (i / points_per_decade)) for i in range(count)]
        if freqs[-1] < f_stop:
            freqs.append(f_stop)
        rows = []
        for freq in freqs:
            rows.append((freq, self.solve_ac(freq)))
        return rows


class TracePlotWindow(tk.Toplevel):
    def __init__(self, parent, title, traces, x_label="t", x_unit="s", x_scale="linear", preferred_unit=None, lock_log_x=False):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=theme.BG)
        self.geometry("800x520")
        self.traces = traces or {}
        self.x_label = x_label
        self.x_unit = x_unit
        self.x_scale = x_scale
        self.lock_log_x = lock_log_x
        self.trace_names = list(self.traces)
        self.units = []
        for trace in self.traces.values():
            unit = trace.get("unit", "")
            if unit not in self.units:
                self.units.append(unit)
        if not self.units:
            self.units = ["V"]
        initial_unit = preferred_unit if preferred_unit in self.units else self.units[0]
        initial_trace = next((name for name, trace in self.traces.items() if trace.get("unit", "") == initial_unit), self.trace_names[0] if self.trace_names else "")
        self.trace_var = tk.StringVar(value=initial_trace)
        self.unit_var = tk.StringVar(value=initial_unit)
        self.log_x_var = tk.BooleanVar(value=(x_scale == "log"))
        self.log_y_var = tk.BooleanVar(value=False)
        self.selected_only_var = tk.BooleanVar(value=False)
        self.trace_enabled_vars = {
            name: tk.BooleanVar(value=not self.is_gain_trace_name(name))
            for name in self.trace_names
        }
        self.samples = []
        self.xs = []
        self.y_unit = "V"
        self.trace_name = ""
        self.hover_index = None
        self.pinned_index = None
        self.plot_bounds = (64, 26, 620, 320)
        self.xmin = 0.0
        self.xmax = 1.0
        self.ymin = -1.0
        self.ymax = 1.0

        top = tk.Frame(self, bg=theme.BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="Trace", bg=theme.BG, fg=theme.MUTED_2, font=font(8, "bold")).pack(side="left")
        self.trace_menu = tk.OptionMenu(top, self.trace_var, *self.trace_names, command=lambda _v: self.set_trace(self.trace_var.get()))
        self.trace_menu.configure(bg=theme.PANEL_2, fg=theme.TEXT_2, activebackground=theme.BORDER, activeforeground=theme.TEXT, highlightthickness=0, relief="flat")
        self.trace_menu.pack(side="left", padx=(8, 12))
        tk.Label(top, text="Display", bg=theme.BG, fg=theme.MUTED_2, font=font(8, "bold")).pack(side="left")
        self.unit_menu = tk.OptionMenu(top, self.unit_var, *self.units, command=lambda _v: self.set_unit(self.unit_var.get()))
        self.unit_menu.configure(bg=theme.PANEL_2, fg=theme.TEXT_2, activebackground=theme.BORDER, activeforeground=theme.TEXT, highlightthickness=0, relief="flat")
        self.unit_menu.pack(side="left", padx=(8, 12))
        # [3번] Graph popup log-scale controls; Bode windows lock X to log.
        self.log_x_check = tk.Checkbutton(top, text="X Log", variable=self.log_x_var, command=self.draw, bg=theme.BG, fg=theme.TEXT_2, selectcolor=theme.PANEL_2, activebackground=theme.BG, activeforeground=theme.TEXT)
        self.log_x_check.pack(side="left", padx=(0, 6))
        if self.lock_log_x:
            self.log_x_check.configure(state="disabled")
        tk.Checkbutton(top, text="Y Log", variable=self.log_y_var, command=self.draw, bg=theme.BG, fg=theme.TEXT_2, selectcolor=theme.PANEL_2, activebackground=theme.BG, activeforeground=theme.TEXT).pack(side="left", padx=(0, 12))
        tk.Checkbutton(
            top,
            text="Selected only",
            variable=self.selected_only_var,
            command=self.draw,
            bg=theme.BG,
            fg=theme.TEXT_2,
            selectcolor=theme.PANEL_2,
            activebackground=theme.BG,
            activeforeground=theme.TEXT,
        ).pack(side="left", padx=(0, 12))
        self.readout = tk.Label(top, text="Cursor: -", bg=theme.TOPBAR, fg=theme.TEXT_2, anchor="w", font=font(9, "bold"), padx=10, pady=6)
        self.readout.pack(side="left", fill="x", expand=True)

        trace_bar = tk.Frame(self, bg=theme.BG)
        trace_bar.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(trace_bar, text="Visible traces", bg=theme.BG, fg=theme.MUTED_2, font=font(8, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 8))
        for idx, name in enumerate(self.trace_names[:12], start=1):
            cb = tk.Checkbutton(
                trace_bar,
                text=name,
                variable=self.trace_enabled_vars[name],
                command=self.draw,
                bg=theme.BG,
                fg=theme.TEXT_2,
                selectcolor=theme.PANEL_2,
                activebackground=theme.BG,
                activeforeground=theme.TEXT,
                font=font(8),
            )
            cb.grid(row=(idx - 1) // 4, column=(idx - 1) % 4 + 1, sticky="w", padx=(0, 10))

        self.canvas = tk.Canvas(self, bg=theme.GRAPH_BG, highlightthickness=1, highlightbackground=theme.BORDER)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=(6, 8))
        buttons = tk.Frame(self, bg=theme.BG)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        make_button(buttons, "Clear Cursor", self.clear_cursor, "secondary").pack(side="left", padx=(0, 6))
        make_button(buttons, "Copy Cursor Value", self.copy_cursor, "secondary").pack(side="left", padx=6)
        make_button(buttons, "Copy Data CSV", self.copy_csv, "secondary").pack(side="left", padx=6)
        make_button(buttons, "닫기", self.destroy, "secondary").pack(side="right")

        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", self.on_leave)
        self.canvas.bind("<Button-1>", self.on_click)
        self.set_trace(self.trace_var.get())

    def is_gain_trace_name(self, name):
        lowered = name.lower()
        return "gain" in lowered or "-vdb(" in lowered or "-vp(" in lowered

    def set_trace(self, name):
        trace = self.traces.get(name, {})
        self.trace_name = name
        self.samples = list(trace.get("samples", []))
        self.xs = [x for x, _ in self.samples]
        self.y_unit = trace.get("unit", "V")
        if self.y_unit in self.units and self.unit_var.get() != self.y_unit:
            self.unit_var.set(self.y_unit)
        self.hover_index = None
        self.pinned_index = None
        self.draw()

    def set_unit(self, unit):
        if unit not in self.units:
            return
        if unit in ("dB", "deg") and self.log_y_var.get():
            self.log_y_var.set(False)
            self.readout.configure(text="Y Log는 magnitude V/A에서만 사용합니다. dB/phase는 linear Y로 표시합니다.")
        names = [name for name, trace in self.traces.items() if trace.get("unit", "") == unit]
        if names and self.trace_var.get() not in names:
            self.trace_var.set(names[0])
            self.set_trace(names[0])
            return
        self.y_unit = unit
        self.hover_index = None
        self.pinned_index = None
        self.draw()

    def visible_traces(self):
        unit = self.unit_var.get() or self.y_unit
        traces = []
        for idx, (name, trace) in enumerate(self.traces.items()):
            if trace.get("unit", "") != unit:
                continue
            if name in self.trace_enabled_vars and not self.trace_enabled_vars[name].get():
                continue
            if self.selected_only_var.get() and name != self.trace_var.get():
                continue
            samples = list(trace.get("samples", []))
            if samples:
                traces.append((name, samples, trace.get("unit", unit), TRACE_COLORS[idx % len(TRACE_COLORS)]))
        return traces

    def focus_samples(self):
        visible = self.visible_traces()
        for name, samples, _unit, _color in visible:
            if name == self.trace_var.get():
                return samples
        return visible[0][1] if visible else []

    def x_to_plot(self, x):
        if self.log_x_var.get():
            return math.log10(max(x, 1e-30))
        return x

    def plot_to_x(self, value):
        if self.log_x_var.get():
            return 10 ** value
        return value

    def y_to_plot(self, y):
        if self.log_y_var.get():
            return math.log10(max(y, 1e-30))
        return y

    def plot_to_y(self, value):
        if self.log_y_var.get():
            return 10 ** value
        return value

    def data_to_screen(self, x, y):
        left, top, plot_w, plot_h = self.plot_bounds
        px = self.x_to_plot(x)
        py = self.y_to_plot(y)
        sx = left + (px - self.xmin) / max(self.xmax - self.xmin, 1e-18) * plot_w
        sy = top + plot_h - (py - self.ymin) / max(self.ymax - self.ymin, 1e-18) * plot_h
        return sx, sy

    def axis_text(self, value, unit):
        if unit == "dB":
            return f"{value:g} dB"
        if unit == "deg":
            return f"{value:g}°"
        return format_eng(value, unit)

    def draw_log_grid(self, axis, vmin, vmax, major_color="#2a3440", minor_color="#151d26"):
        c = self.canvas
        left, top, plot_w, plot_h = self.plot_bounds
        start = math.floor(vmin)
        stop = math.ceil(vmax)
        for decade in range(start, stop + 1):
            for mult in range(1, 10):
                value = math.log10(mult * (10 ** decade))
                if value < vmin - 1e-12 or value > vmax + 1e-12:
                    continue
                is_major = mult == 1
                color = major_color if is_major else minor_color
                dash = None if is_major else (2, 5)
                if axis == "x":
                    sx = left + (value - vmin) / max(vmax - vmin, 1e-18) * plot_w
                    c.create_line(sx, top, sx, top + plot_h, fill=color, dash=dash)
                    if is_major:
                        c.create_text(sx, top + plot_h + 17, text=format_eng(10 ** value, self.x_unit), fill=theme.MUTED_2, font=font(8))
                else:
                    sy = top + plot_h - (value - vmin) / max(vmax - vmin, 1e-18) * plot_h
                    c.create_line(left, sy, left + plot_w, sy, fill=color, dash=dash)
                    if is_major:
                        c.create_text(left - 7, sy, text=self.axis_text(10 ** value, self.y_unit), fill=theme.MUTED_2, font=font(8), anchor="e")

    def draw_linear_grid(self, axis, vmin, vmax):
        c = self.canvas
        left, top, plot_w, plot_h = self.plot_bounds
        major_color = "#2a3440"
        minor_color = "#151d26"
        ticks = nice_linear_ticks(vmin, vmax, 6)
        step = ticks[1] - ticks[0] if len(ticks) > 1 else max(vmax - vmin, 1.0)
        minor_step = step / 5.0
        minor_start = math.ceil(vmin / minor_step) * minor_step
        value = minor_start
        while value <= vmax + minor_step * 0.5 and minor_step > 0:
            if all(abs(value - tick) > abs(minor_step) * 0.2 for tick in ticks):
                if axis == "x":
                    sx = left + (value - vmin) / max(vmax - vmin, 1e-18) * plot_w
                    c.create_line(sx, top, sx, top + plot_h, fill=minor_color, dash=(2, 5))
                else:
                    sy = top + plot_h - (value - vmin) / max(vmax - vmin, 1e-18) * plot_h
                    c.create_line(left, sy, left + plot_w, sy, fill=minor_color, dash=(2, 5))
            value += minor_step
        for value in ticks:
            if axis == "x":
                sx = left + (value - vmin) / max(vmax - vmin, 1e-18) * plot_w
                c.create_line(sx, top, sx, top + plot_h, fill=major_color)
                c.create_text(sx, top + plot_h + 17, text=format_eng(value, self.x_unit), fill=theme.MUTED_2, font=font(8))
            else:
                sy = top + plot_h - (value - vmin) / max(vmax - vmin, 1e-18) * plot_h
                c.create_line(left, sy, left + plot_w, sy, fill=major_color)
                c.create_text(left - 7, sy, text=self.axis_text(value, self.y_unit), fill=theme.MUTED_2, font=font(8), anchor="e")

    def log_y_allowed(self, visible):
        if self.unit_var.get() in ("dB", "deg"):
            return False, "Y Log는 dB/phase에 적용하지 않습니다."
        values = [y for _name, samples, _unit, _color in visible for _x, y in samples]
        if not values or any(y <= 0 for y in values):
            return False, "Y Log는 0보다 큰 magnitude 데이터에서만 사용할 수 있습니다."
        return True, ""

    def log_x_allowed(self, visible):
        values = [x for _name, samples, _unit, _color in visible for x, _y in samples]
        if not values or any(x <= 0 for x in values):
            return False, "X Log는 0보다 큰 X 값에서만 사용할 수 있습니다."
        return True, ""

    def nearest_index(self, sx):
        samples = self.focus_samples()
        if not samples:
            return None
        xs = [x for x, _ in samples]
        left, _top, plot_w, _plot_h = self.plot_bounds
        plot_x = self.xmin + (sx - left) / max(plot_w, 1) * (self.xmax - self.xmin)
        x = self.plot_to_x(plot_x)
        idx = bisect_left(xs, x)
        candidates = [i for i in (idx - 1, idx, idx + 1) if 0 <= i < len(samples)]
        if not candidates:
            return None
        return min(candidates, key=lambda i: abs(samples[i][0] - x))

    def nearest_sample_at(self, samples, target_x):
        if not samples:
            return None
        xs = [x for x, _ in samples]
        idx = bisect_left(xs, target_x)
        candidates = [i for i in (idx - 1, idx, idx + 1) if 0 <= i < len(samples)]
        if not candidates:
            return None
        return samples[min(candidates, key=lambda i: abs(samples[i][0] - target_x))]

    def cursor_text(self, idx):
        focus = self.focus_samples()
        if idx is None or not (0 <= idx < len(focus)):
            return "Cursor: -"
        x_value, _y = focus[idx]
        parts = [f"{self.x_label} = {format_eng(x_value, self.x_unit)}"]
        for name, samples, unit, _color in self.visible_traces():
            sample = self.nearest_sample_at(samples, x_value)
            if sample is not None:
                parts.append(f"{name} = {format_eng(sample[1], unit)}")
        return "Cursor: " + "  |  ".join(parts)

    def draw_cursor(self, idx, pinned=False):
        focus = self.focus_samples()
        if idx is None or not (0 <= idx < len(focus)):
            return
        left, top, _plot_w, plot_h = self.plot_bounds
        x, _y = focus[idx]
        px = self.x_to_plot(x)
        sx = left + (px - self.xmin) / max(self.xmax - self.xmin, 1e-18) * self.plot_bounds[2]
        color = theme.ACCENT_2 if pinned else theme.BLUE_2
        self.canvas.create_line(sx, top, sx, top + plot_h, fill=color, dash=(4, 3), width=1)
        for _name, samples, _unit, trace_color in self.visible_traces():
            sample = self.nearest_sample_at(samples, x)
            if sample is None:
                continue
            _tx, y = sample
            _sx, sy = self.data_to_screen(x, y)
            self.canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill=trace_color, outline="")
        if pinned:
            self.canvas.create_text(sx + 8, top + 12, text=format_eng(x, self.x_unit), fill=color, font=font(8, "bold"), anchor="nw")

    def draw(self):
        c = self.canvas
        c.delete("all")
        w, h = max(1, c.winfo_width()), max(1, c.winfo_height())
        visible = self.visible_traces()
        if not visible:
            c.create_text(w / 2, h / 2, text="표시할 데이터가 없습니다.", fill=theme.MUTED_2, font=font(11))
            self.readout.configure(text="Cursor: -")
            return
        self.y_unit = visible[0][2]
        axis_notices = []
        if self.log_x_var.get():
            allowed, reason = self.log_x_allowed(visible)
            if not allowed:
                self.log_x_var.set(False)
                axis_notices.append(reason)
        if self.log_y_var.get():
            allowed, reason = self.log_y_allowed(visible)
            if not allowed:
                self.log_y_var.set(False)
                axis_notices.append(reason)
        xs = [self.x_to_plot(x) for _name, samples, _unit, _color in visible for x, _ in samples]
        ys = [self.y_to_plot(y) for _name, samples, _unit, _color in visible for _, y in samples]
        self.xmin, self.xmax = min(xs), max(xs) or 1.0
        self.ymin, self.ymax = min(ys), max(ys)
        if abs(self.xmax - self.xmin) < 1e-12:
            self.xmax += 1
            self.xmin -= 1
        if abs(self.ymax - self.ymin) < 1e-12:
            self.ymax += 1
            self.ymin -= 1
        margin = max((self.ymax - self.ymin) * 0.08, 1e-12)
        self.ymin -= margin
        self.ymax += margin
        pad_l, pad_t, pad_r, pad_b = 72, 26, 22, 46
        plot_w = max(10, w - pad_l - pad_r)
        plot_h = max(10, h - pad_t - pad_b)
        self.plot_bounds = (pad_l, pad_t, plot_w, plot_h)
        c.create_rectangle(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, outline=theme.BORDER)
        if self.log_x_var.get():
            self.draw_log_grid("x", self.xmin, self.xmax)
        else:
            self.draw_linear_grid("x", self.xmin, self.xmax)
        if self.log_y_var.get():
            self.draw_log_grid("y", self.ymin, self.ymax)
        else:
            self.draw_linear_grid("y", self.ymin, self.ymax)
        y_title = {"dB": "Gain (dB)", "deg": "Phase (deg)", "V": "Voltage (V)", "A": "Current (A)"}.get(self.y_unit, self.y_unit)
        x_title = "Frequency (Hz)" if self.x_unit == "Hz" else "Time (s)"
        c.create_text(pad_l + plot_w / 2, h - 13, text=x_title, fill=theme.TEXT_2, font=font(9, "bold"))
        c.create_text(16, pad_t + plot_h / 2, text=y_title, fill=theme.TEXT_2, font=font(9, "bold"), angle=90)
        if not self.log_y_var.get() and self.ymin <= 0 <= self.ymax:
            _zx, zy = self.data_to_screen(self.plot_to_x(self.xmin), 0)
            c.create_line(pad_l, zy, pad_l + plot_w, zy, fill=theme.LINE, width=2)
        for idx, (name, samples, _unit, color) in enumerate(visible):
            pts = []
            for x, y in minmax_downsample(samples, max(80, int(plot_w * 1.4))):
                pts.extend(self.data_to_screen(x, y))
            if len(pts) >= 4:
                c.create_line(*pts, fill=color, width=2)
            lx = pad_l + 8 + (idx % 3) * 150
            ly = 8 + (idx // 3) * 14
            c.create_line(lx, ly + 6, lx + 18, ly + 6, fill=color, width=2)
            c.create_text(lx + 24, ly, text=name, fill=theme.TEXT_2, font=font(8, "bold"), anchor="nw")
        self.draw_cursor(self.hover_index, pinned=False)
        self.draw_cursor(self.pinned_index, pinned=True)
        cursor_text = self.cursor_text(self.pinned_index if self.pinned_index is not None else self.hover_index)
        self.readout.configure(text=" / ".join(axis_notices) if axis_notices else cursor_text)

    def on_motion(self, event):
        left, top, plot_w, plot_h = self.plot_bounds
        self.hover_index = self.nearest_index(event.x) if left <= event.x <= left + plot_w and top <= event.y <= top + plot_h else None
        self.draw()

    def on_leave(self, _event=None):
        self.hover_index = None
        self.draw()

    def on_click(self, event):
        left, top, plot_w, plot_h = self.plot_bounds
        if not (left <= event.x <= left + plot_w and top <= event.y <= top + plot_h):
            return
        idx = self.nearest_index(event.x)
        self.pinned_index = None if self.pinned_index == idx else idx
        self.draw()

    def clear_cursor(self):
        self.pinned_index = None
        self.hover_index = None
        self.draw()

    def cursor_payload(self):
        idx = self.pinned_index if self.pinned_index is not None else self.hover_index
        if idx is None:
            return ""
        focus = self.focus_samples()
        if not (0 <= idx < len(focus)):
            return ""
        x_value, _ = focus[idx]
        lines = [f"trace,{self.x_label},value,unit"]
        for name, samples, unit, _color in self.visible_traces():
            sample = self.nearest_sample_at(samples, x_value)
            if sample is not None:
                lines.append(f"{name},{x_value},{sample[1]},{unit}")
        return "\n".join(lines)

    def copy_cursor(self):
        payload = self.cursor_payload()
        if payload:
            self.clipboard_clear()
            self.clipboard_append(payload)

    def copy_csv(self):
        self.clipboard_clear()
        lines = [f"trace,{self.x_label},value,unit,sample_source"]
        for name, trace in self.traces.items():
            unit = trace.get("unit", "")
            source_name = "raw" if trace.get("raw_samples") else "display"
            for t, y in trace.get("raw_samples", trace.get("samples", [])):
                lines.append(f"{name},{t},{y},{unit},{source_name}")
        self.clipboard_append("\n".join(lines))


class BodePlotWindow(TracePlotWindow):
    """DEPRECATED: kept for a future optional two-panel Bode view.

    The default AC Sweep path intentionally uses TracePlotWindow so users can
    switch Linear/dB/Phase and X/Y scale in one PSpice-like plot window.
    """

    def __init__(self, parent, title, traces):
        # [5번] Bode plot uses log-frequency X and draws magnitude/phase as two synchronized panels.
        super().__init__(parent, title, traces, x_label="f", x_unit="Hz", x_scale="log", preferred_unit="dB", lock_log_x=True)
        self.geometry("860x560")

    def bode_groups(self):
        groups = {"dB": [], "deg": []}
        for idx, (name, trace) in enumerate(self.traces.items()):
            unit = trace.get("unit", "")
            if unit not in groups:
                continue
            samples = list(trace.get("samples", []))
            if samples:
                groups[unit].append((name, samples, unit, TRACE_COLORS[idx % len(TRACE_COLORS)]))
        return groups

    def focus_samples(self):
        groups = self.bode_groups()
        preferred = groups.get("dB") or groups.get("deg") or []
        return preferred[0][1] if preferred else []

    def nearest_index(self, sx):
        samples = self.focus_samples()
        if not samples:
            return None
        left, _top, plot_w, _plot_h = self.plot_bounds
        plot_x = self.xmin + (sx - left) / max(plot_w, 1) * (self.xmax - self.xmin)
        x = self.plot_to_x(plot_x)
        xs = [item[0] for item in samples]
        idx = bisect_left(xs, x)
        candidates = [i for i in (idx - 1, idx, idx + 1) if 0 <= i < len(samples)]
        return min(candidates, key=lambda i: abs(samples[i][0] - x)) if candidates else None

    def panel_y_range(self, group):
        values = [y for _name, samples, _unit, _color in group for _x, y in samples]
        if not values:
            return -1.0, 1.0
        ymin, ymax = min(values), max(values)
        if abs(ymax - ymin) < 1e-12:
            ymin -= 1.0
            ymax += 1.0
        margin = max((ymax - ymin) * 0.08, 1e-9)
        return ymin - margin, ymax + margin

    def panel_to_screen(self, x, y, bounds, ymin, ymax):
        left, top, plot_w, plot_h = bounds
        px = self.x_to_plot(x)
        sx = left + (px - self.xmin) / max(self.xmax - self.xmin, 1e-18) * plot_w
        sy = top + plot_h - (y - ymin) / max(ymax - ymin, 1e-18) * plot_h
        return sx, sy

    def cursor_text(self, idx):
        focus = self.focus_samples()
        if idx is None or not (0 <= idx < len(focus)):
            return "Cursor: -"
        x_value, _ = focus[idx]
        parts = [f"f = {format_eng(x_value, 'Hz')}"]
        for unit, suffix in (("dB", "dB"), ("deg", "°")):
            for name, samples, _unit, _color in self.bode_groups().get(unit, []):
                sample = self.nearest_sample_at(samples, x_value)
                if sample is not None:
                    parts.append(f"{name} = {sample[1]:.3g} {suffix}")
        return "Cursor: " + "  |  ".join(parts)

    def draw_cursor(self, idx, pinned=False):
        focus = self.focus_samples()
        if idx is None or not (0 <= idx < len(focus)):
            return
        x, _ = focus[idx]
        color = theme.ACCENT_2 if pinned else theme.BLUE_2
        for unit, panels in getattr(self, "bode_panels", {}).items():
            bounds, ymin, ymax = panels
            left, top, _plot_w, plot_h = bounds
            sx, _ = self.panel_to_screen(x, ymin, bounds, ymin, ymax)
            self.canvas.create_line(sx, top, sx, top + plot_h, fill=color, dash=(4, 3), width=1)
            for _name, samples, _unit, trace_color in self.bode_groups().get(unit, []):
                sample = self.nearest_sample_at(samples, x)
                if sample is None:
                    continue
                _tx, y = sample
                _sx, sy = self.panel_to_screen(x, y, bounds, ymin, ymax)
                self.canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill=trace_color, outline="")
        if pinned and getattr(self, "bode_panels", None):
            first_bounds = next(iter(self.bode_panels.values()))[0]
            sx, _ = self.panel_to_screen(x, 0, first_bounds, -1, 1)
            self.canvas.create_text(sx + 8, first_bounds[1] + 10, text=format_eng(x, "Hz"), fill=color, font=font(8, "bold"), anchor="nw")

    def draw(self):
        c = self.canvas
        c.delete("all")
        w, h = max(1, c.winfo_width()), max(1, c.winfo_height())
        groups = self.bode_groups()
        all_samples = [sample for group in groups.values() for _name, samples, _unit, _color in group for sample in samples]
        if not all_samples:
            c.create_text(w / 2, h / 2, text="표시할 AC Sweep 데이터가 없습니다.", fill=theme.MUTED_2, font=font(11))
            self.readout.configure(text="Cursor: -")
            return
        x_values = [self.x_to_plot(x) for x, _y in all_samples if x > 0]
        self.xmin, self.xmax = min(x_values), max(x_values)
        pad_l, pad_r, pad_t, gap, pad_b = 76, 24, 26, 36, 44
        plot_w = max(10, w - pad_l - pad_r)
        plot_h = max(40, (h - pad_t - pad_b - gap) / 2)
        mag_bounds = (pad_l, pad_t, plot_w, plot_h)
        phase_bounds = (pad_l, pad_t + plot_h + gap, plot_w, plot_h)
        mag_y = self.panel_y_range(groups.get("dB", []))
        phase_y = self.panel_y_range(groups.get("deg", []))
        self.bode_panels = {"dB": (mag_bounds, *mag_y), "deg": (phase_bounds, *phase_y)}
        self.plot_bounds = (pad_l, pad_t, plot_w, plot_h * 2 + gap)

        for unit, title, bounds, y_range in (
            ("dB", "Gain (dB)", mag_bounds, mag_y),
            ("deg", "Phase (°)", phase_bounds, phase_y),
        ):
            if unit == "deg":
                title = "Phase (deg)"
            left, top, pw, ph = bounds
            ymin, ymax = y_range
            c.create_text(left, top - 16, text=title, fill=theme.TEXT_2, font=font(9, "bold"), anchor="w")
            c.create_rectangle(left, top, left + pw, top + ph, outline=theme.BORDER)
            for i in range(6):
                x = left + pw * i / 5
                c.create_line(x, top, x, top + ph, fill="#18212a")
                plot_value = self.xmin + (self.xmax - self.xmin) * i / 5
                if unit == "deg":
                    c.create_text(x, top + ph + 16, text=format_eng(10 ** plot_value, "Hz"), fill=theme.MUTED_2, font=font(8))
            for i in range(5):
                y = top + ph * i / 4
                c.create_line(left, y, left + pw, y, fill="#18212a")
                value = ymax - (ymax - ymin) * i / 4
                c.create_text(left - 7, y, text=f"{value:.3g}", fill=theme.MUTED_2, font=font(8), anchor="e")
            if ymin <= 0 <= ymax:
                _sx, zy = self.panel_to_screen(10 ** self.xmin, 0, bounds, ymin, ymax)
                c.create_line(left, zy, left + pw, zy, fill=theme.LINE, width=2)
            for idx, (name, samples, _unit, color) in enumerate(groups.get(unit, [])):
                pts = []
                for x, y in minmax_downsample(samples, max(80, int(pw * 1.2))):
                    pts.extend(self.panel_to_screen(x, y, bounds, ymin, ymax))
                if len(pts) >= 4:
                    c.create_line(*pts, fill=color, width=2)
                lx = left + 8 + (idx % 3) * 150
                ly = top + 6 + (idx // 3) * 13
                c.create_line(lx, ly + 6, lx + 18, ly + 6, fill=color, width=2)
                c.create_text(lx + 24, ly, text=name, fill=theme.TEXT_2, font=font(8, "bold"), anchor="nw")
        c.create_text(pad_l + plot_w / 2, h - 15, text="Frequency (Hz)", fill=theme.TEXT_2, font=font(9, "bold"))
        self.draw_cursor(self.hover_index, pinned=False)
        self.draw_cursor(self.pinned_index, pinned=True)
        self.readout.configure(text=self.cursor_text(self.pinned_index if self.pinned_index is not None else self.hover_index))

    def on_motion(self, event):
        in_panel = False
        for bounds, _ymin, _ymax in getattr(self, "bode_panels", {}).values():
            left, top, pw, ph = bounds
            if left <= event.x <= left + pw and top <= event.y <= top + ph:
                in_panel = True
                break
        self.hover_index = self.nearest_index(event.x) if in_panel else None
        self.draw()

    def on_click(self, event):
        for bounds, _ymin, _ymax in getattr(self, "bode_panels", {}).values():
            left, top, pw, ph = bounds
            if left <= event.x <= left + pw and top <= event.y <= top + ph:
                idx = self.nearest_index(event.x)
                self.pinned_index = None if self.pinned_index == idx else idx
                self.draw()
                return


class PSpiceLitePage(tk.Frame):
    title = "PSpice Lite"

    TOOLS = [
        ("select", "선택/이동"),
        ("wire", "배선"),
        ("r", "저항 R"),
        ("c", "커패시터 C"),
        ("l", "인덕터 L"),
        ("d", "다이오드 D"),
        ("gnd", "GND"),
        ("vdc", "DC 전압원"),
        ("vac", "AC 전압원"),
        ("isrc", "전류원"),
        ("vprobe", "Probe V"),
        ("iprobe", "Probe I"),
        ("label_v", "Label V"),
        ("label_i", "Label I"),
    ]

    def __init__(self, parent, toast=None):
        super().__init__(parent, bg=theme.BG)
        self.toast = toast
        self.model = CircuitModel()
        self.tool = "select"
        self.selected = None
        self.wire_start = None
        self.probe_point = None
        self.probe_element = None
        self.measurement_labels = []
        self.next_label_id = 1
        self.last_result = None
        self.last_transient = None
        self.last_ac_result = None
        self.show_node_labels = True
        self.node_label_mode = "compact"
        self.show_measurement_labels = True
        self.zoom = 1.0
        self.pan_x = 90
        self.pan_y = 80
        self.drag = None
        self.hover_point = None
        self.hover_target = None
        self.preview_rotation = 0
        self._property_target_id = None
        self.undo_stack = []
        self.redo_stack = []
        self._suppress_undo = False
        self.sim_thread = None
        self.sim_queue = None
        self.sim_cancel_event = None
        self.integration_var = tk.StringVar(value="BE")
        self._build()

    def _build(self):
        self.columnconfigure(0, weight=0, minsize=190)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0, minsize=270)
        self.rowconfigure(1, weight=1)
        tk.Label(
            self,
            text="소자를 배치하고 배선한 뒤 해석을 실행하세요. 삭제는 선택 후 Delete 키 또는 속성 패널의 삭제 버튼을 사용합니다.",
            bg=theme.BG,
            fg=theme.MUTED_2,
            font=font(9),
        ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=theme.PAGE_PAD_X, pady=(6, 4))
        tools = Panel(self)
        tools.grid(row=1, column=0, sticky="nsw", padx=(theme.PAGE_PAD_X, theme.CARD_GAP), pady=(0, theme.CARD_GAP))
        tools.configure(width=190)
        tools.grid_propagate(False)
        SectionHeader(tools, "도구", "배치·배선·Probe").pack(fill="x")
        self.tool_buttons = {}
        tool_grid = tk.Frame(tools, bg=theme.PANEL)
        tool_grid.pack(fill="x", padx=7, pady=(0, 6))
        tool_grid.columnconfigure(0, weight=1)
        tool_grid.columnconfigure(1, weight=1)
        for idx, (key, label) in enumerate(self.TOOLS):
            btn = make_button(tool_grid, label, lambda k=key: self.set_tool(k), "secondary")
            btn.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=2, pady=2)
            self.tool_buttons[key] = btn
        # [1번] Keep example load feedback visible by retaining the buttons.
        self.voltage_example_button = make_button(tools, "전압분배 예제", self.load_voltage_divider, "accent")
        self.voltage_example_button.pack(fill="x", padx=8, pady=(8, 2))
        self.rc_example_button = make_button(tools, "RC 예제", self.load_rc_example, "secondary")
        self.rc_example_button.pack(fill="x", padx=8, pady=2)
        make_button(tools, "새 회로", self.new_circuit, "secondary").pack(fill="x", padx=8, pady=(8, 2))
        make_button(tools, "저장", self.save_json, "secondary").pack(fill="x", padx=8, pady=2)
        make_button(tools, "불러오기", self.load_json, "secondary").pack(fill="x", padx=8, pady=(2, 8))

        center = Panel(self)
        center.grid(row=1, column=1, sticky="nsew", pady=(0, theme.CARD_GAP))
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)
        SectionHeader(center, "회로 편집 캔버스", "Ctrl+Wheel 확대/축소 · 우클릭 드래그 Pan · Ctrl+R 회전 · Delete 삭제").grid(row=0, column=0, sticky="ew")
        self.canvas = tk.Canvas(center, bg=theme.GRAPH_BG, highlightthickness=1, highlightbackground=theme.BORDER, takefocus=1)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<Button-1>", self.on_left_down)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_up)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        self.canvas.bind("<Button-3>", self.on_pan_start)
        self.canvas.bind("<B3-Motion>", self.on_pan_drag)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Control-r>", self._key_rotate)
        self.canvas.bind("<Control-d>", self._key_duplicate)
        self.canvas.bind("<Control-z>", self._key_undo)
        self.canvas.bind("<Control-Z>", self._key_redo)
        self.canvas.bind("<Delete>", self._key_delete)
        for key, tool in [("r", "r"), ("c", "c"), ("l", "l"), ("d", "d"), ("w", "wire"), ("g", "gnd"), ("v", "vdc"), ("p", "vprobe"), ("i", "iprobe")]:
            self.canvas.bind(f"<Key-{key}>", lambda event, t=tool: self._key_set_tool(t, event))

        side = Panel(self)
        side.grid(row=1, column=2, sticky="nse", padx=(theme.CARD_GAP, theme.PAGE_PAD_X), pady=(0, theme.CARD_GAP))
        side.configure(width=270)
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)
        SectionHeader(side, "속성 / 해석", "소자값과 결과").grid(row=0, column=0, sticky="ew")
        # [2번] Keep the right side compact by scrolling only the side panel.
        self.side_canvas = tk.Canvas(side, bg=theme.PANEL, highlightthickness=0, bd=0)
        self.side_canvas.grid(row=1, column=0, sticky="nsew")
        side_scroll = tk.Scrollbar(side, orient="vertical", command=self.side_canvas.yview)
        side_scroll.grid(row=1, column=1, sticky="ns")
        self.side_canvas.configure(yscrollcommand=side_scroll.set)
        self.side_body = tk.Frame(self.side_canvas, bg=theme.PANEL)
        self.side_window = self.side_canvas.create_window((0, 0), window=self.side_body, anchor="nw")
        self.side_body.bind("<Configure>", lambda _e: self.side_canvas.configure(scrollregion=self.side_canvas.bbox("all")))
        self.side_canvas.bind("<Configure>", lambda e: self.side_canvas.itemconfigure(self.side_window, width=e.width))
        self.side_canvas.bind("<Enter>", lambda _e: self.side_canvas.bind_all("<MouseWheel>", self._side_mousewheel))
        self.side_canvas.bind("<Leave>", lambda _e: self.side_canvas.unbind_all("<MouseWheel>"))
        self.side_body.bind("<Enter>", lambda _e: self.side_canvas.bind_all("<MouseWheel>", self._side_mousewheel))
        self.side_body.bind("<Leave>", lambda _e: self.side_canvas.unbind_all("<MouseWheel>"))
        self.prop_frame = tk.Frame(self.side_body, bg=theme.PANEL)
        self.prop_frame.pack(fill="x", padx=10, pady=(0, 8))
        self.name_var = tk.StringVar()
        self.value_var = tk.StringVar()
        self.param_var = tk.StringVar()
        self.prop_title = CompactMetricBox(self.prop_frame, "선택", "선택된 항목 없음", wraplength=225)
        self.prop_title.pack(fill="x", pady=(0, 4))
        self.name_entry = self._entry(self.prop_frame, "이름", self.name_var)
        self.value_entry = self._entry(self.prop_frame, "값", self.value_var)
        self.param_entry = self._entry(self.prop_frame, "AC: freq, phase", self.param_var)
        btn_row = tk.Frame(self.prop_frame, bg=theme.PANEL)
        btn_row.pack(fill="x", pady=(4, 0))
        make_button(btn_row, "적용", self.apply_properties, "accent").pack(side="left", fill="x", expand=True, padx=(0, 4))
        make_button(btn_row, "회전", self.rotate_selected, "secondary").pack(side="left", fill="x", expand=True, padx=4)
        make_button(btn_row, "삭제", self.delete_selected, "secondary").pack(side="left", fill="x", expand=True, padx=(4, 0))

        analysis = tk.Frame(self.side_body, bg=theme.PANEL)
        analysis.pack(fill="x", padx=10, pady=(0, 6))
        action_row = tk.Frame(analysis, bg=theme.PANEL)
        action_row.pack(fill="x", pady=(0, 5))
        self.dc_button = make_button(action_row, "DC", self.run_dc, "accent")
        self.dc_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.tran_button = make_button(action_row, "Tran", self.run_transient, "accent")
        self.tran_button.pack(side="left", fill="x", expand=True, padx=3)
        self.ac_button = make_button(action_row, "AC", self.run_ac_sweep, "secondary")
        self.ac_button.pack(side="left", fill="x", expand=True, padx=(3, 0))
        self.cancel_button = make_button(analysis, "해석 취소", self.cancel_simulation, "secondary")
        self.cancel_button.pack(fill="x", pady=(0, 5))
        self.cancel_button.configure(state="disabled")
        tr_row = tk.Frame(analysis, bg=theme.PANEL)
        tr_row.pack(fill="x", pady=(0, 5))
        self.tstop_field = NumberField(tr_row, "t_stop (s)", 0.01, min_value=1e-9, digits=5)
        self.tstop_field.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.dt_field = NumberField(tr_row, "dt (s)", 0.00005, min_value=1e-9, digits=6)
        self.dt_field.pack(side="left", fill="x", expand=True, padx=(4, 0))
        make_button(analysis, "Auto dt", self.auto_dt, "secondary").pack(fill="x", pady=(0, 5))
        ac_row = tk.Frame(analysis, bg=theme.PANEL)
        ac_row.pack(fill="x", pady=(0, 5))
        self.fstart_field = NumberField(ac_row, "f start", 10.0, min_value=1e-9, digits=4)
        self.fstart_field.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.fstop_field = NumberField(ac_row, "f stop", 1000000.0, min_value=1e-9, digits=4)
        self.fstop_field.pack(side="left", fill="x", expand=True, padx=3)
        self.ppd_field = NumberField(ac_row, "pt/dec", 50, min_value=1, digits=0)
        self.ppd_field.pack(side="left", fill="x", expand=True, padx=(3, 0))
        integ_row = tk.Frame(analysis, bg=theme.PANEL)
        integ_row.pack(fill="x", pady=(0, 5))
        tk.Label(integ_row, text="Integration", bg=theme.PANEL, fg=theme.MUTED_2, font=font(8, "bold")).pack(side="left")
        self.integration_menu = tk.OptionMenu(integ_row, self.integration_var, "BE", "TRAP")
        self.integration_menu.configure(bg=theme.PANEL_2, fg=theme.TEXT_2, activebackground=theme.BORDER, activeforeground=theme.TEXT, highlightthickness=0, relief="flat")
        self.integration_menu.pack(side="left", fill="x", expand=True, padx=(6, 0))
        toggle_row = tk.Frame(analysis, bg=theme.PANEL)
        toggle_row.pack(fill="x", pady=(4, 0))
        make_button(toggle_row, "Node Labels", self.toggle_node_labels, "secondary").pack(side="left", fill="x", expand=True, padx=(0, 3))
        make_button(toggle_row, "Meas. Labels", self.toggle_measurement_labels, "secondary").pack(side="left", fill="x", expand=True, padx=(3, 0))
        fit_row = tk.Frame(analysis, bg=theme.PANEL)
        fit_row.pack(fill="x", pady=(4, 0))
        make_button(fit_row, "화면 맞춤", self.fit_view, "secondary").pack(side="left", fill="x", expand=True, padx=(0, 3))
        make_button(fit_row, "결과 지우기", self.clear_results, "secondary").pack(side="left", fill="x", expand=True, padx=(3, 0))
        labels_frame = tk.Frame(self.side_body, bg=theme.PANEL)
        labels_frame.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(labels_frame, text="Measurement Labels", bg=theme.PANEL, fg=theme.MUTED_2, font=font(8, "bold")).pack(anchor="w")
        self.label_list = tk.Listbox(labels_frame, height=3, bg=theme.GRAPH_BG, fg=theme.TEXT_2, selectbackground=theme.ACCENT, relief="flat", font=font(8))
        self.label_list.pack(fill="x", pady=(2, 4))
        label_btns = tk.Frame(labels_frame, bg=theme.PANEL)
        label_btns.pack(fill="x")
        make_button(label_btns, "Tran 그래프", self.plot_selected_label, "secondary").pack(side="left", fill="x", expand=True, padx=(0, 2))
        make_button(label_btns, "AC 그래프", self.plot_selected_label_ac, "secondary").pack(side="left", fill="x", expand=True, padx=2)
        make_button(label_btns, "삭제", self.delete_selected_label, "secondary").pack(side="left", fill="x", expand=True, padx=(2, 0))
        make_button(analysis, "Netlist 보기", self.show_netlist, "secondary").pack(fill="x")
        self.status_metric = CompactMetricBox(self.side_body, "상태", "GND를 포함한 회로를 만들고 해석을 실행하세요.", wraplength=230, value_anchor="nw")
        self.status_metric.pack(fill="x", padx=10, pady=(0, 8))
        result_frame = tk.Frame(self.side_body, bg=theme.PANEL)
        result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        self.result_text = tk.Text(result_frame, height=5, bg=theme.GRAPH_BG, fg=theme.TEXT_2, insertbackground=theme.TEXT_2, relief="flat", font=font(9), wrap="word")
        self.result_text.grid(row=0, column=0, sticky="nsew")
        result_scroll = tk.Scrollbar(result_frame, orient="vertical", command=self.result_text.yview)
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=result_scroll.set)

        self.status = tk.Label(self, text="", bg=theme.TOPBAR, fg=theme.MUTED_2, anchor="w", font=font(8))
        self.status.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.set_tool("select")
        self.load_voltage_divider()

    def _entry(self, parent, label, var):
        frame = tk.Frame(parent, bg=theme.PANEL)
        frame.pack(fill="x", pady=2)
        label_widget = tk.Label(frame, text=label, bg=theme.PANEL, fg=theme.MUTED_2, font=font(8, "bold"))
        label_widget.pack(anchor="w")
        if var is self.value_var:
            self.value_label = label_widget
        elif var is self.param_var:
            self.param_label = label_widget
        entry = tk.Entry(frame, textvariable=var, bg=theme.GRAPH_BG, fg=theme.TEXT_2, insertbackground=theme.TEXT_2, relief="flat", font=font(9))
        entry.pack(fill="x", ipady=3)
        def commit(_event=None):
            self.apply_properties()
            self.canvas.focus_set()
            return "break"

        def cancel(_event=None):
            self.update_properties()
            self.canvas.focus_set()
            return "break"

        entry.bind("<Return>", commit)
        entry.bind("<KP_Enter>", commit)
        entry.bind("<Escape>", cancel)
        return entry

    def _side_mousewheel(self, event):
        # [2번] MouseWheel is captured only while the cursor is over the side panel.
        self.side_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def on_show(self):
        self.canvas.focus_set()

    def on_hide(self):
        if self.sim_thread is not None and self.sim_thread.is_alive():
            if self.sim_cancel_event is not None:
                self.sim_cancel_event.set()
            try:
                self.status_metric.set("페이지 이동으로 계산을 취소했습니다.", theme.WARN)
            except tk.TclError:
                pass

    def snapshot(self):
        selected_ref = None
        if isinstance(self.selected, CircuitElement):
            selected_ref = ("element", self.selected.id)
        elif isinstance(self.selected, Wire):
            selected_ref = ("wire", self.selected.id)
        return {
            "model": self.model.to_dict(),
            "measurement_labels": [label.to_dict() for label in self.measurement_labels],
            "next_label_id": self.next_label_id,
            "probe_point": tuple(self.probe_point) if self.probe_point else None,
            "probe_element_id": self.probe_element.id if self.probe_element else None,
            "node_label_mode": self.node_label_mode,
            "show_node_labels": self.show_node_labels,
            "show_measurement_labels": self.show_measurement_labels,
            "selected": selected_ref,
        }

    def model_signature(self):
        # Long-running analyses use a frozen circuit snapshot. This signature
        # lets the UI warn when the user edited the circuit before results return.
        return json.dumps(self.model.to_dict(), sort_keys=True)

    def transient_worker_snapshot(self):
        return {
            "model": self.model.to_dict(),
            "measurement_labels": [label.to_dict() for label in self.measurement_labels],
            "probe_point": tuple(self.probe_point) if self.probe_point else None,
            "probe_element_id": self.probe_element.id if self.probe_element else None,
            "signature": self.model_signature(),
        }

    def restore_snapshot(self, snap):
        self._suppress_undo = True
        try:
            self.model.from_dict(snap.get("model", {}))
            self.measurement_labels = [MeasurementLabel.from_dict(item) for item in snap.get("measurement_labels", [])]
            self.next_label_id = int(snap.get("next_label_id", max([label.id for label in self.measurement_labels], default=0) + 1))
            self.wire_start = None
            self.drag = None
            self.probe_point = tuple(snap.get("probe_point")) if snap.get("probe_point") else None
            probe_id = snap.get("probe_element_id")
            self.probe_element = self.element_by_id(probe_id) if probe_id is not None else None
            self.node_label_mode = snap.get("node_label_mode", self.node_label_mode)
            self.show_node_labels = bool(snap.get("show_node_labels", self.node_label_mode != "off"))
            if not self.show_node_labels:
                self.node_label_mode = "off"
            self.show_measurement_labels = bool(snap.get("show_measurement_labels", True))
            self.selected = None
            selected_ref = snap.get("selected")
            if selected_ref and len(selected_ref) == 2:
                ref_kind, ref_id = selected_ref
                if ref_kind == "element":
                    self.selected = self.element_by_id(ref_id)
                elif ref_kind == "wire":
                    self.selected = next((wire for wire in self.model.wires if wire.id == ref_id), None)
            self.last_result = None
            self.last_transient = None
            self.last_ac_result = None
            self.refresh_label_list()
            self.redraw()
            self.update_properties()
            self.update_status()
        finally:
            self._suppress_undo = False

    def push_undo(self):
        if self._suppress_undo:
            return
        self.undo_stack.append(self.snapshot())
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            self.status_metric.set("되돌릴 작업이 없습니다.", theme.WARN)
            return
        self.redo_stack.append(self.snapshot())
        snap = self.undo_stack.pop()
        self.restore_snapshot(snap)
        self.status_metric.set("Undo 완료", theme.ACCENT)

    def redo(self):
        if not self.redo_stack:
            self.status_metric.set("다시 실행할 작업이 없습니다.", theme.WARN)
            return
        self.undo_stack.append(self.snapshot())
        snap = self.redo_stack.pop()
        self.restore_snapshot(snap)
        self.status_metric.set("Redo 완료", theme.ACCENT)

    def handle_escape(self):
        if self._focus_is_text_input():
            return True
        if self.wire_start:
            self.wire_start = None
            self.redraw()
            self.status_metric.set("ESC: 배선을 취소했습니다.", theme.TEXT_2)
            return True
        if self.tool != "select":
            self.preview_rotation = 0
            self.set_tool("select")
            self.status_metric.set("ESC: 도구 취소 + 선택 모드", theme.TEXT_2)
            return True
        if self.selected or self.drag:
            self.selected = None
            self.drag = None
            self.redraw()
            self.update_properties()
            return True
        return False

    def _focus_is_text_input(self):
        widget = self.focus_get()
        if widget is None:
            return False
        return widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox")

    def _key_event_is_canvas(self, event=None):
        widget = getattr(event, "widget", None)
        if widget is not None:
            return widget is self.canvas
        return self.focus_get() is self.canvas

    def set_tool(self, tool):
        previous = self.tool
        self.tool = tool
        if tool != previous:
            self.wire_start = None
        if tool == "select":
            self.preview_rotation = 0
        self.drag = None
        for key, btn in self.tool_buttons.items():
            btn.configure(bg=theme.ACCENT if key == tool else theme.PANEL_2, fg=theme.BG if key == tool else theme.TEXT_2)
        self.update_status()
        self.redraw()

    def set_example_button_state(self, active=None):
        # [1번] Example buttons act like lightweight tabs: active example is accent.
        if hasattr(self, "voltage_example_button"):
            set_button_variant(self.voltage_example_button, active == "voltage")
        if hasattr(self, "rc_example_button"):
            set_button_variant(self.rc_example_button, active == "rc")

    def world_to_screen(self, x, y):
        return x * self.zoom + self.pan_x, y * self.zoom + self.pan_y

    def screen_to_world(self, x, y):
        return (x - self.pan_x) / self.zoom, (y - self.pan_y) / self.zoom

    def snap(self, x, y):
        return round(x / GRID) * GRID, round(y / GRID) * GRID

    def clear_interaction_state(self, clear_labels=True, clear_results=True):
        self.selected = None
        self.wire_start = None
        self.probe_point = None
        self.probe_element = None
        self.drag = None
        self.hover_point = None
        self.hover_target = None
        if clear_labels:
            self.measurement_labels.clear()
            self.next_label_id = 1
            if hasattr(self, "label_list"):
                self.refresh_label_list()
        if clear_results:
            self.last_result = None
            self.last_transient = None
            self.last_ac_result = None

    def invalidate_analysis_cache(self, reason=None, redraw=True, notify=True):
        had_results = self.last_result is not None or self.last_transient is not None or self.last_ac_result is not None
        self.last_result = None
        self.last_transient = None
        self.last_ac_result = None
        if had_results and notify:
            suffix = f" ({reason})" if reason else ""
            self.status_metric.set(f"회로가 변경되어 이전 해석 결과를 지웠습니다.{suffix}", theme.WARN)
            self.write_results("회로가 변경되었습니다. DC/Transient/AC 해석을 다시 실행하세요.")
        if redraw:
            self.redraw()

    def terminal_connected(self, point, owner=None):
        for wire in self.model.wires:
            for a, b in zip(wire.points, wire.points[1:]):
                if point_on_segment(point, a, b):
                    return True
        for el in self.model.elements:
            if owner is not None and el.id == owner.id:
                continue
            if point in el.terminals():
                return True
        return False

    def unconnected_terminals(self):
        floating = []
        for el in self.model.elements:
            if el.kind == "gnd":
                continue
            for idx, point in enumerate(el.terminals(), start=1):
                if not self.terminal_connected(point, el):
                    floating.append((el, idx, point))
        return floating

    def preflight_warnings(self):
        warnings = []
        if not any(el.kind == "gnd" for el in self.model.elements):
            warnings.append("GND가 없습니다. 접지를 하나 배치하세요.")
        floating = self.unconnected_terminals()
        if floating:
            items = ", ".join(f"{el.name}.T{idx}" for el, idx, _p in floating[:6])
            suffix = " ..." if len(floating) > 6 else ""
            warnings.append(f"연결 안 된 단자: {items}{suffix}")
        try:
            solver = self.solver()
            _uf, point_node = solver.build_nodes()
            floating_wires = [wire for wire in self.model.wires if not any(point in point_node for point in wire.points)]
            for el in self.model.elements:
                if el.kind == "gnd":
                    continue
                t1, t2 = el.terminals()
                n1 = solver.node_for_point(t1, point_node) or "?"
                n2 = solver.node_for_point(t2, point_node) or "?"
                if n1 == n2:
                    if el.kind in ("vdc", "vac"):
                        warnings.append(f"{el.name}의 양단이 모두 {n1} 노드입니다. 전압원이 단락되었습니다.")
                    elif el.kind == "isrc":
                        warnings.append(f"{el.name}의 양단이 모두 {n1} 노드입니다. 전류원이 같은 노드에 연결되었습니다.")
                    elif el.kind in ("r", "c", "l", "d"):
                        warnings.append(f"{el.name}의 양단이 모두 {n1} 노드입니다. 소자가 단락된 상태일 수 있습니다.")
            if floating_wires:
                warnings.append(f"회로 소자와 연결되지 않은 floating wire {len(floating_wires)}개는 해석에서 제외됩니다.")
        except ValueError:
            pass
        return warnings

    def preflight_errors(self, analysis="dc"):
        errors = []
        if not any(el.kind == "gnd" for el in self.model.elements):
            errors.append("GND가 없습니다. 기준 접지를 하나 배치하세요.")
            return errors
        floating = self.unconnected_terminals()
        if floating:
            items = ", ".join(f"{el.name}.T{idx}" for el, idx, _p in floating[:8])
            suffix = " ..." if len(floating) > 8 else ""
            errors.append(f"연결되지 않은 단자가 있습니다: {items}{suffix}. 소자 핀이 도선이나 다른 단자에 닿아야 합니다.")
        for el in self.model.elements:
            if el.kind in ("r", "c", "l"):
                value = el.numeric_value()
                if not math.isfinite(value) or value <= 0:
                    errors.append(f"{el.name} 값이 유효하지 않습니다: {el.value}. 0보다 큰 값을 입력하세요.")
        try:
            solver = self.solver()
            _uf, point_node = solver.build_nodes()
            for el in self.model.elements:
                if el.kind == "gnd":
                    continue
                t1, t2 = el.terminals()
                n1 = solver.node_for_point(t1, point_node)
                n2 = solver.node_for_point(t2, point_node)
                if not n1 or not n2:
                    errors.append(f"{el.name}의 단자가 유효한 노드에 연결되지 않았습니다.")
                    continue
                if n1 == n2 and el.kind in ("vdc", "vac"):
                    errors.append(f"전압원 {el.name}의 양단이 모두 {n1} 노드에 연결되어 있습니다. 이상 전압원 단락이므로 해석할 수 없습니다.")
        except ValueError as exc:
            errors.append(str(exc))
        return errors

    def abort_on_preflight_errors(self, analysis="dc"):
        errors = self.preflight_errors(analysis)
        if not errors:
            return False
        message = "해석 전 오류:\n- " + "\n- ".join(errors)
        self.status_metric.set(errors[0], theme.DANGER)
        self.write_results(message)
        return True

    def floating_terminal_warning(self):
        floating = self.unconnected_terminals()
        if not floating:
            return ""
        items = ", ".join(f"{el.name}.T{idx}" for el, idx, _p in floating[:8])
        suffix = " ..." if len(floating) > 8 else ""
        return f"⚠ 떠 있는 단자가 있습니다 ({items}{suffix}). 결과 신뢰도가 낮습니다."

    def recommended_dt(self, t_stop=None):
        candidates = []
        for el in self.model.elements:
            if el.kind == "vac":
                freq = parse_value(el.params.get("frequency", "1k"), 1000.0)
                if freq and freq > 0:
                    candidates.append(1.0 / (freq * 80.0))
        rs = [abs(el.numeric_value()) for el in self.model.elements if el.kind == "r" and abs(el.numeric_value()) > 0]
        cs = [abs(el.numeric_value()) for el in self.model.elements if el.kind == "c" and abs(el.numeric_value()) > 0]
        ls = [abs(el.numeric_value()) for el in self.model.elements if el.kind == "l" and abs(el.numeric_value()) > 0]
        if rs and cs:
            candidates.append(min(r * c for r in rs for c in cs) / 40.0)
        if rs and ls:
            candidates.append(min(l / r for l in ls for r in rs) / 40.0)
        if t_stop and t_stop > 0:
            candidates.append(t_stop / 1000.0)
        dt = min(candidates) if candidates else 1e-5
        if t_stop and t_stop > 0:
            dt = max(dt, t_stop / max(MAX_TRANSIENT_STEPS - 1, 1))
        return max(dt, 1e-9)

    def transient_quality_warnings(self, t_stop, dt):
        warnings = []
        recommended = self.recommended_dt(t_stop)
        if dt > recommended * 1.8:
            warnings.append(f"dt가 커서 파형이 거칠게 보일 수 있습니다. Auto dt 권장값: {format_eng(recommended, 's')}")
        return warnings

    def transient_sample_count(self, t_stop, dt):
        if t_stop <= 0 or dt <= 0:
            return 0
        tolerance = max(abs(t_stop), abs(dt), 1.0) * 1e-12
        full_steps = int(math.floor((t_stop + tolerance) / dt))
        count = full_steps + 1
        last_nominal = full_steps * dt
        if last_nominal < t_stop - tolerance:
            count += 1
        return count

    def transient_time_points(self, t_stop, dt):
        count = self.transient_sample_count(t_stop, dt)
        if count <= 0:
            return []
        times = []
        tolerance = max(abs(t_stop), abs(dt), 1.0) * 1e-12
        for index in range(count):
            value = index * dt
            if value >= t_stop - tolerance or index == count - 1:
                value = t_stop
            if not times or abs(value - times[-1]) > tolerance:
                times.append(value)
        if abs(times[-1] - t_stop) > tolerance:
            times.append(t_stop)
        return times

    def read_number_field_strict(self, field, label, positive=False):
        # Commit the current Entry draft before an analysis button reads it.
        # This keeps t_stop/dt/frequency edits from silently using an old value.
        try:
            field._commit_value(notify=False)
        except Exception:
            pass
        value = field.get_float(default=None, positive=positive)
        if value is None or not math.isfinite(value):
            try:
                field.entry.configure(bg=theme.DANGER)
                field.entry.selection_range(0, "end")
            except tk.TclError:
                pass
            raise ValueError(f"{label} 입력값을 확인하세요. 빈 값이나 잘못된 단위는 사용할 수 없습니다.")
        field.set_value(value, silent=True)
        return value

    def transient_inputs_or_raise(self):
        t_stop = self.read_number_field_strict(self.tstop_field, "t_stop", positive=True)
        dt = self.read_number_field_strict(self.dt_field, "dt", positive=True)
        if dt > t_stop:
            raise ValueError("dt가 t_stop보다 큽니다. dt를 줄이거나 t_stop을 늘리세요.")
        requested_steps = self.transient_sample_count(t_stop, dt)
        if requested_steps > MAX_TRANSIENT_STEPS:
            raise ValueError(
                "Transient step too large\n"
                f"원인: 계산 스텝 수가 {requested_steps}개로 최대 {MAX_TRANSIENT_STEPS}개를 초과합니다.\n"
                "해결: dt를 키우거나 t_stop을 줄이세요."
            )
        return t_stop, dt, requested_steps

    def ac_inputs_or_raise(self):
        f_start = self.read_number_field_strict(self.fstart_field, "f start", positive=True)
        f_stop = self.read_number_field_strict(self.fstop_field, "f stop", positive=True)
        ppd = int(self.read_number_field_strict(self.ppd_field, "points/decade", positive=True))
        if f_stop <= f_start:
            raise ValueError("f stop은 f start보다 커야 합니다.")
        if ppd <= 0:
            raise ValueError("points/decade는 0보다 커야 합니다.")
        return f_start, f_stop, ppd

    def auto_dt(self):
        t_stop = self.tstop_field.get_float(default=None, positive=True)
        if t_stop is None:
            t_stop = 0.01
            self.tstop_field.set_value(t_stop, silent=True)
        dt = self.recommended_dt(t_stop)
        self.dt_field.set_value(dt, silent=True)
        self.status_metric.set(f"Auto dt = {format_eng(dt, 's')} 로 설정했습니다.", theme.ACCENT)

    def nearest_connect_point(self, x, y):
        best = None
        best_dist = GRID * 0.7
        for el in self.model.elements:
            for p in el.terminals():
                dist = math.hypot(p[0] - x, p[1] - y)
                if dist < best_dist:
                    best = p
                    best_dist = dist
        if best:
            return best
        snapped = self.snap(x, y)
        for wire in self.model.wires:
            for a, b in zip(wire.points, wire.points[1:]):
                if point_on_segment(snapped, a, b):
                    return snapped
        return None

    def current_snap_point(self, event):
        wx, wy = self.screen_to_world(event.x, event.y)
        sx, sy = self.snap(wx, wy)
        target = self.nearest_connect_point(sx, sy)
        self.hover_target = target
        self.hover_point = target or (sx, sy)
        return self.hover_point

    def preview_path(self, start, end):
        sx, sy = start
        ex, ey = end
        return [start, end] if sx == ex or sy == ey else [start, (ex, sy), end]

    def unique_label_name(self, base):
        names = {label.name for label in self.measurement_labels}
        if base not in names:
            return base
        idx = 2
        while f"{base}_{idx}" in names:
            idx += 1
        return f"{base}_{idx}"

    def add_measurement_label(self, kind, point=None, element=None):
        base = "Vout" if kind == "voltage" else f"I_{element.name}" if element else "Ilabel"
        name = simpledialog.askstring("Measurement Label", "라벨 이름", initialvalue=self.unique_label_name(base), parent=self)
        if not name:
            return
        name = self.unique_label_name(name.strip() or base)
        label = MeasurementLabel(
            self.next_label_id,
            kind,
            name,
            point=point,
            element_id=element.id if element else None,
        )
        self.push_undo()
        self.next_label_id += 1
        self.measurement_labels.append(label)
        self.refresh_label_list()
        self.invalidate_analysis_cache("측정 라벨 추가", redraw=False, notify=False)
        self.status_metric.set(f"라벨 {name}을 추가했습니다.", theme.ACCENT)
        self.redraw()

    def refresh_label_list(self):
        if not hasattr(self, "label_list"):
            return
        self.label_list.delete(0, "end")
        for label in self.measurement_labels:
            prefix = "V" if label.kind == "voltage" else "I"
            self.label_list.insert("end", f"{prefix}  {label.name}")

    def selected_measurement_label(self):
        if not hasattr(self, "label_list"):
            return None
        selection = self.label_list.curselection()
        if not selection:
            return None
        idx = selection[0]
        return self.measurement_labels[idx] if 0 <= idx < len(self.measurement_labels) else None

    def delete_selected_label(self):
        label = self.selected_measurement_label()
        if not label:
            return
        self.push_undo()
        self.measurement_labels = [item for item in self.measurement_labels if item.id != label.id]
        self.refresh_label_list()
        self.invalidate_analysis_cache("측정 라벨 삭제", redraw=False, notify=False)
        self.status_metric.set(f"라벨 {label.name}을 삭제했습니다.", theme.WARN)
        self.redraw()

    def plot_selected_label(self):
        label = self.selected_measurement_label()
        if not label:
            self.status_metric.set("그래프로 볼 측정 라벨을 선택하세요.", theme.WARN)
            return
        if not self.last_transient or label.name not in self.last_transient.get("traces", {}):
            self.status_metric.set("먼저 Transient 해석을 실행하세요.", theme.WARN)
            return
        unit = self.last_transient["traces"][label.name].get("unit", "")
        traces = {
            name: trace
            for name, trace in self.last_transient.get("traces", {}).items()
            if trace.get("unit", "") == unit
        }
        TracePlotWindow(self, f"Transient · {label.name}", traces)

    def plot_selected_label_ac(self):
        label = self.selected_measurement_label()
        if not label:
            self.status_metric.set("AC 그래프로 볼 측정 라벨을 선택하세요.", theme.WARN)
            return
        if not self.last_ac_result:
            self.status_metric.set("먼저 AC Sweep을 실행하세요.", theme.WARN)
            return
        token = label.name
        if label.kind == "current":
            element = self.element_by_id(label.element_id)
            token = element.name if element is not None else label.name
            wanted = {f"I({token})", f"IDB({token})", f"IP({token})"}
        else:
            wanted = {f"V({token})", f"VDB({token})", f"VP({token})"}
        traces = {
            name: trace for name, trace in self.last_ac_result.get("traces", {}).items()
            if name in wanted
        }
        if not traces:
            self.status_metric.set("선택한 라벨의 AC trace가 없습니다. AC Sweep을 다시 실행하세요.", theme.WARN)
            return
        TracePlotWindow(
            self,
            f"AC Sweep · {label.name}",
            traces,
            x_label="f",
            x_unit="Hz",
            x_scale="log",
            preferred_unit="dB",
            lock_log_x=False,
        )

    def toggle_node_labels(self):
        order = ["off", "compact", "values"]
        self.node_label_mode = order[(order.index(self.node_label_mode) + 1) % len(order)]
        self.show_node_labels = self.node_label_mode != "off"
        self.status_metric.set(f"Node Labels: {self.node_label_mode}", theme.TEXT_2)
        self.redraw()

    def toggle_measurement_labels(self):
        self.show_measurement_labels = not self.show_measurement_labels
        state = "On" if self.show_measurement_labels else "Off"
        self.status_metric.set(f"Measurement Labels: {state}", theme.TEXT_2)
        self.redraw()

    def probe_node_name(self, point):
        try:
            solver = self.solver()
            _uf, point_node = solver.build_nodes()
            return solver.node_for_point(point, point_node) or "unknown"
        except ValueError:
            return "GND 없음"

    def on_left_down(self, event):
        self.canvas.focus_set()
        wx, wy = self.current_snap_point(event)
        if self.tool in ("r", "c", "l", "d", "gnd", "vdc", "vac", "isrc"):
            self.push_undo()
            self.selected = self.model.add_element(self.tool, wx, wy)
            self.selected.rotation = self.preview_rotation
            self.invalidate_analysis_cache("소자 추가", redraw=False, notify=False)
            self.update_properties()
            self.redraw()
            return
        if self.tool == "wire":
            if self.wire_start is None:
                self.wire_start = (wx, wy)
                self.status_metric.set("배선 시작점을 선택했습니다. 끝점을 클릭하세요.", theme.ACCENT)
            else:
                self.push_undo()
                self.model.add_wire(self.preview_path(self.wire_start, (wx, wy)))
                self.wire_start = None
                self.invalidate_analysis_cache("배선 추가", redraw=False, notify=False)
                self.redraw()
            return
        if self.tool == "vprobe":
            self.probe_point = self.hit_point(wx, wy) or (wx, wy)
            self.probe_element = None
            self.invalidate_analysis_cache("Voltage Probe 변경", redraw=False, notify=False)
            self.status_metric.set(f"Voltage Probe: node {self.probe_node_name(self.probe_point)}", theme.ACCENT)
            self.redraw()
            return
        if self.tool == "label_v":
            point = self.hit_point(wx, wy) or (wx, wy)
            self.add_measurement_label("voltage", point=point)
            return
        if self.tool == "label_i":
            obj = self.hit_test(event.x, event.y)
            if isinstance(obj, CircuitElement) and obj.kind != "gnd":
                self.add_measurement_label("current", element=obj)
            else:
                self.status_metric.set("Label I는 소자를 클릭해 추가합니다.", theme.WARN)
            return
        if self.tool == "iprobe":
            obj = self.hit_test(event.x, event.y)
            if isinstance(obj, CircuitElement) and obj.kind != "gnd":
                self.probe_element = obj
                self.probe_point = None
                self.invalidate_analysis_cache("Current Probe 변경", redraw=False, notify=False)
                self.status_metric.set(f"Current Probe: {obj.name} · 방향 terminal 1 -> terminal 2", theme.ACCENT)
            self.redraw()
            return
        obj = self.hit_test(event.x, event.y)
        self.selected = obj
        self.update_properties()
        if isinstance(obj, CircuitElement):
            self.push_undo()
            self.drag = ("move", obj, wx - obj.x, wy - obj.y, obj.x, obj.y)
        elif obj is None:
            self.drag = ("pan", event.x, event.y, self.pan_x, self.pan_y)
        self.redraw()

    def on_double_click(self, event):
        # [8번] Double-click edits the selected element value without opening a separate property workflow.
        self.canvas.focus_set()
        obj = self.hit_test(event.x, event.y)
        if not isinstance(obj, CircuitElement):
            return
        self.selected = obj
        self.update_properties()
        if obj.kind == "gnd":
            self.status_metric.set("GND는 값을 수정할 수 없습니다.", theme.TEXT_2)
            return
        if obj.kind == "d":
            initial = f"{obj.params.get('is', '1e-14')}, {obj.params.get('n', '1')}, {obj.params.get('vt', '0.02585')}"
            draft = simpledialog.askstring("다이오드 파라미터", "Is, n, Vt", initialvalue=initial, parent=self)
            if draft is None:
                return
            parts = [part.strip() for part in draft.split(",")]
            if len(parts) < 3 or any(parse_value(part, None) is None for part in parts[:3]):
                self.status_metric.set("다이오드는 Is, n, Vt 형식으로 입력하세요. 기존값은 유지됩니다.", theme.DANGER)
                return
            self.push_undo()
            obj.params.update({"is": parts[0], "n": parts[1], "vt": parts[2]})
        else:
            title = "Transient amplitude (V)" if obj.kind == "vac" else "값"
            draft = simpledialog.askstring("소자 값 수정", f"{obj.name} {title}", initialvalue=obj.value, parent=self)
            if draft is None:
                return
            draft = draft.strip()
            if parse_value(draft, None) is None:
                self.status_metric.set("값 형식을 확인하세요. 기존값은 유지됩니다.", theme.DANGER)
                return
            self.push_undo()
            obj.value = draft
        self.invalidate_analysis_cache("소자 값 수정", redraw=False, notify=False)
        self.status_metric.set("소자 값을 수정했습니다.", theme.GOOD)
        self.redraw()
        self.update_properties()

    def on_left_drag(self, event):
        if not self.drag:
            return
        if self.drag[0] == "move":
            _, obj, dx, dy, _old_x, _old_y = self.drag
            wx, wy = self.snap(*self.screen_to_world(event.x, event.y))
            obj.x, obj.y = wx - dx, wy - dy
            obj.x, obj.y = self.snap(obj.x, obj.y)
            self.redraw()
        elif self.drag[0] == "pan":
            _, sx, sy, px, py = self.drag
            self.pan_x = px + event.x - sx
            self.pan_y = py + event.y - sy
            self.redraw()

    def on_left_up(self, _event):
        if self.drag and self.drag[0] == "move":
            _, obj, _dx, _dy, old_x, old_y = self.drag
            if (obj.x, obj.y) != (old_x, old_y):
                self.invalidate_analysis_cache("소자 이동", redraw=False, notify=False)
        self.drag = None
        self.update_status()

    def on_pan_start(self, event):
        self.drag = ("pan", event.x, event.y, self.pan_x, self.pan_y)

    def on_pan_drag(self, event):
        self.on_left_drag(event)

    def on_motion(self, event):
        wx, wy = self.current_snap_point(event)
        self.update_status()
        if self.tool in ("wire", "r", "c", "l", "d", "gnd", "vdc", "vac", "isrc", "vprobe", "iprobe", "label_v", "label_i"):
            self.redraw()

    def on_wheel(self, event):
        if not (event.state & 0x0004):
            return
        old_zoom = self.zoom
        factor = 1.12 if event.delta > 0 else 1 / 1.12
        self.zoom = max(0.35, min(2.8, self.zoom * factor))
        wx, wy = self.screen_to_world(event.x, event.y)
        self.pan_x = event.x - wx * self.zoom
        self.pan_y = event.y - wy * self.zoom
        if abs(old_zoom - self.zoom) > 1e-9:
            self.redraw()

    def _key_rotate(self, _event=None):
        if self._focus_is_text_input() or not self._key_event_is_canvas(_event):
            return None
        self.rotate_selected()
        return "break"

    def _key_duplicate(self, _event=None):
        if self._focus_is_text_input() or not self._key_event_is_canvas(_event):
            return None
        self.duplicate_selected()
        return "break"

    def _key_delete(self, _event=None):
        if self._focus_is_text_input() or not self._key_event_is_canvas(_event):
            return None
        self.delete_selected()
        return "break"

    def _key_undo(self, _event=None):
        if self._focus_is_text_input() or not self._key_event_is_canvas(_event):
            return None
        self.undo()
        return "break"

    def _key_redo(self, _event=None):
        if self._focus_is_text_input() or not self._key_event_is_canvas(_event):
            return None
        self.redo()
        return "break"

    def _key_set_tool(self, tool, event=None):
        if self._focus_is_text_input() or not self._key_event_is_canvas(event):
            return None
        self.set_tool(tool)
        return "break"

    def hit_point(self, x, y):
        for el in self.model.elements:
            for p in el.terminals():
                if abs(p[0] - x) <= GRID / 2 and abs(p[1] - y) <= GRID / 2:
                    return p
        for wire in self.model.wires:
            for a, b in zip(wire.points, wire.points[1:]):
                if point_on_segment((x, y), a, b):
                    return (x, y)
        return None

    def hit_test(self, sx, sy):
        wx, wy = self.screen_to_world(sx, sy)
        for el in reversed(self.model.elements):
            if abs(wx - el.x) <= 48 and abs(wy - el.y) <= 48:
                return el
        for wire in reversed(self.model.wires):
            for a, b in zip(wire.points, wire.points[1:]):
                if a[0] == b[0] and abs(wx - a[0]) < 8 and min(a[1], b[1]) - 6 <= wy <= max(a[1], b[1]) + 6:
                    return wire
                if a[1] == b[1] and abs(wy - a[1]) < 8 and min(a[0], b[0]) - 6 <= wx <= max(a[0], b[0]) + 6:
                    return wire
        return None

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = max(1, c.winfo_width()), max(1, c.winfo_height())
        step = GRID * self.zoom
        if step >= 6:
            start_x = self.pan_x % step
            start_y = self.pan_y % step
            x = start_x
            while x < w:
                c.create_line(x, 0, x, h, fill="#18212a")
                x += step
            y = start_y
            while y < h:
                c.create_line(0, y, w, y, fill="#18212a")
                y += step
        for wire in self.model.wires:
            self.draw_wire(wire)
        for el in self.model.elements:
            self.draw_element(el)
        if self.wire_start:
            sx, sy = self.world_to_screen(*self.wire_start)
            c.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, outline=theme.ACCENT, width=2)
            if self.hover_point and self.hover_point != self.wire_start:
                self.draw_preview_wire(self.preview_path(self.wire_start, self.hover_point))
        if self.tool in ("r", "c", "l", "d", "gnd", "vdc", "vac", "isrc") and self.hover_point:
            preview = CircuitElement(-1, self.tool, self.preview_name(self.tool), self.preview_value(self.tool), self.hover_point[0], self.hover_point[1], self.preview_rotation)
            self.draw_element(preview, ghost=True)
        if self.probe_point:
            sx, sy = self.world_to_screen(*self.probe_point)
            c.create_oval(sx - 8, sy - 8, sx + 8, sy + 8, outline=theme.BLUE_2, width=2)
            c.create_text(sx, sy, text="V", fill=theme.BLUE_2, font=font(8, "bold"))
        if self.probe_element:
            sx, sy = self.world_to_screen(self.probe_element.x, self.probe_element.y)
            c.create_rectangle(sx - 9, sy - 9, sx + 9, sy + 9, outline=theme.ACCENT_2, width=2)
            c.create_text(sx, sy, text="I", fill=theme.ACCENT_2, font=font(8, "bold"))
        self.draw_junction_dots()
        self._badge_boxes = self.element_label_avoid_boxes()
        self.draw_measurement_labels()
        self.draw_node_voltage_labels()
        if self.hover_target:
            sx, sy = self.world_to_screen(*self.hover_target)
            c.create_oval(sx - 7, sy - 7, sx + 7, sy + 7, outline=theme.WARN, width=2)

    def preview_name(self, kind):
        return {"r": "R?", "c": "C?", "l": "L?", "d": "D?", "gnd": "GND", "vdc": "V?", "vac": "VAC?", "isrc": "I?"}.get(kind, "?")

    def element_by_id(self, element_id):
        for element in self.model.elements:
            if element.id == element_id:
                return element
        return None

    def element_label_avoid_boxes(self):
        boxes = []
        margin = max(28, int(48 * self.zoom))
        for el in self.model.elements:
            sx, sy = self.world_to_screen(el.x, el.y)
            boxes.append((sx - margin, sy - margin, sx + margin, sy + margin))
        return boxes

    def boxes_overlap(self, a, b):
        return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

    def draw_badge(self, anchor, text, color=theme.BLUE_2):
        c = self.canvas
        sx, sy = self.world_to_screen(*anchor)
        lines = str(text).splitlines() or [str(text)]
        width = max(46, min(110, max(len(line) for line in lines) * 6 + 12))
        height = max(20, len(lines) * 13 + 7)
        candidates = [(12, -height - 12), (12, 12), (-width - 12, -height - 12), (-width - 12, 12), (-width / 2, -height - 18), (-width / 2, 18)]
        canvas_w, canvas_h = max(1, c.winfo_width()), max(1, c.winfo_height())
        chosen = None
        for dx, dy in candidates:
            x1, y1 = sx + dx, sy + dy
            x1 = max(4, min(canvas_w - width - 4, x1))
            y1 = max(4, min(canvas_h - height - 4, y1))
            box = (x1, y1, x1 + width, y1 + height)
            if not any(self.boxes_overlap(box, other) for other in getattr(self, "_badge_boxes", [])):
                chosen = box
                break
        if chosen is None:
            dx, dy = candidates[0]
            x1, y1 = sx + dx, sy + dy
            x1 = max(4, min(canvas_w - width - 4, x1))
            y1 = max(4, min(canvas_h - height - 4, y1))
            chosen = (x1, y1, x1 + width, y1 + height)
        x1, y1, x2, y2 = chosen
        c.create_line(sx, sy, x1 if x1 > sx else x2, y1 if y1 > sy else y2, fill=color, dash=(2, 3))
        c.create_rectangle(x1, y1, x2, y2, fill=theme.TOPBAR, outline=color)
        c.create_text(x1 + 6, y1 + 4, text="\n".join(lines), fill=color, font=font(8, "bold"), anchor="nw")
        self._badge_boxes.append(chosen)

    def draw_junction_dots(self):
        try:
            _uf, point_node = self.solver().build_nodes()
        except ValueError:
            return
        candidates = set()
        terminals = []
        wire_segments = []
        for wire in self.model.wires:
            for p in wire.points:
                candidates.add(p)
            for a, b in zip(wire.points, wire.points[1:]):
                wire_segments.append((wire.id, a, b))
        for el in self.model.elements:
            for p in el.terminals():
                candidates.add(p)
                terminals.append((el.id, p))
        for p in sorted(candidates):
            if p not in point_node:
                continue
            touching_segments = [(wire_id, a, b) for wire_id, a, b in wire_segments if point_on_segment(p, a, b)]
            touching_segment_count = len(touching_segments)
            distinct_wire_count = len({wire_id for wire_id, _a, _b in touching_segments})
            touching_terms = {element_id for element_id, terminal in terminals if terminal == p}
            # Show a dot exactly where the connectivity model says multiple
            # distinct electrical objects meet: endpoint-endpoint, T-junction,
            # terminal-on-wire, or terminal-terminal. A plain X/+ crossing is
            # intentionally absent from candidates, so it remains visually and
            # electrically unconnected. A simple bend in a single polyline has
            # two segments but one wire and no terminal, so it does not get a dot.
            is_terminal_junction = bool(touching_terms) and touching_segment_count + len(touching_terms) >= 2
            is_wire_junction = distinct_wire_count >= 2 and touching_segment_count >= 2
            is_branch_node = touching_segment_count >= 3
            if is_terminal_junction or is_wire_junction or is_branch_node:
                sx, sy = self.world_to_screen(*p)
                r = 4
                self.canvas.create_oval(sx - r, sy - r, sx + r, sy + r, fill=theme.NODE, outline=theme.BG, width=1)

    def draw_measurement_labels(self):
        if not self.show_measurement_labels:
            return
        for label in self.measurement_labels:
            if label.kind == "voltage" and label.point:
                self.draw_badge(label.point, f"V {label.name}", theme.BLUE_2)
            elif label.kind == "current":
                element = self.element_by_id(label.element_id)
                if not element:
                    continue
                self.draw_badge((element.x, element.y), f"I {label.name}", theme.ACCENT_2)

    def draw_node_voltage_labels(self):
        if self.node_label_mode == "off" or not self.last_result:
            return
        point_node = self.last_result.get("point_node", {})
        node_values = self.last_result.get("nodes", {})
        seen = set()
        for point, node in point_node.items():
            if node in seen:
                continue
            seen.add(node)
            value = node_values.get(node, 0.0)
            name = "GND" if node == "0" else node
            text = name if self.node_label_mode == "compact" else f"{name}\n{format_eng(value, 'V')}"
            self.draw_badge(point, text, theme.TEXT_2)

    def preview_value(self, kind):
        return {"r": "1k", "c": "1u", "l": "1m", "d": "Is=1e-14", "vdc": "5", "vac": "1", "isrc": "1m", "gnd": "0"}.get(kind, "")

    def draw_preview_wire(self, points):
        pts = []
        for p in points:
            pts.extend(self.world_to_screen(*p))
        if len(pts) >= 4:
            self.canvas.create_line(*pts, fill=theme.ACCENT_2, width=max(1, int(2 * self.zoom)), dash=(5, 4), capstyle="round", joinstyle="round")
            sx, sy = self.world_to_screen(*points[-1])
            self.canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, outline=theme.ACCENT_2, width=2)

    def draw_wire(self, wire):
        pts = []
        for p in wire.points:
            pts.extend(self.world_to_screen(*p))
        color = theme.ACCENT if wire is self.selected else theme.WIRE
        self.canvas.create_line(*pts, fill=color, width=max(2, int(3 * self.zoom)), capstyle="round", joinstyle="round")

    def transform(self, el, x, y):
        rot = el.rotation % 360
        if rot == 0:
            tx, ty = x, y
        elif rot == 90:
            tx, ty = -y, x
        elif rot == 180:
            tx, ty = -x, -y
        else:
            tx, ty = y, -x
        return self.world_to_screen(el.x + tx, el.y + ty)

    def draw_element(self, el, ghost=False):
        c = self.canvas
        color = theme.MUTED_2 if ghost else theme.ACCENT if el is self.selected else theme.TEXT_2
        wire = "#536270" if ghost else theme.WIRE
        dash = (5, 4) if ghost else None

        def line(points, fill=wire, width=3):
            pts = []
            for x, y in points:
                pts.extend(self.transform(el, x, y))
            opts = {"dash": dash} if dash else {}
            c.create_line(*pts, fill=fill, width=max(1, int(width * self.zoom)), capstyle="round", joinstyle="round", **opts)

        def text(x, y, value, fill=theme.TEXT_2, size=9):
            sx, sy = self.transform(el, x, y)
            c.create_text(sx, sy, text=value, fill=fill, font=font(size), anchor="center")

        terminal_offsets = [(-40, 0), (40, 0)] if el.kind != "gnd" else [(0, 0)]
        terminals = el.terminals()
        for idx, (tx, ty) in enumerate(terminal_offsets):
            sx, sy = self.transform(el, tx, ty)
            if ghost:
                fill, outline, radius = "", theme.NODE, 3
            else:
                connected = self.terminal_connected(terminals[idx], el) or el.kind == "gnd"
                fill = theme.NODE if connected else theme.WARN
                outline = "" if connected else theme.DANGER
                radius = 3 if connected else 5
            c.create_oval(sx - radius, sy - radius, sx + radius, sy + radius, fill=fill, outline=outline)
        if el.kind == "r":
            line([(-40, 0), (-26, 0)])
            line([(-26, 0), (-18, -10), (-8, 10), (2, -10), (12, 10), (22, -10), (30, 0), (40, 0)], fill=color)
        elif el.kind == "c":
            line([(-40, 0), (-10, 0)])
            line([(10, 0), (40, 0)])
            line([(-10, -18), (-10, 18)], fill=color, width=4)
            line([(10, -18), (10, 18)], fill=color, width=4)
        elif el.kind == "l":
            line([(-40, 0), (-24, 0)])
            for x in (-18, -6, 6, 18):
                sx, sy = self.transform(el, x - 7, -7)
                ex, ey = self.transform(el, x + 7, 7)
                opts = {"dash": dash} if dash else {}
                c.create_arc(sx, sy, ex, ey, start=0, extent=180, outline=color, width=max(1, int(3 * self.zoom)), style="arc", **opts)
            line([(24, 0), (40, 0)])
        elif el.kind == "d":
            line([(-40, 0), (-18, 0)])
            line([(18, 0), (40, 0)])
            tri = []
            for point in [(-18, -16), (-18, 16), (14, 0)]:
                tri.extend(self.transform(el, *point))
            opts = {"dash": dash} if dash else {}
            c.create_polygon(*tri, outline=color, fill="" if ghost else theme.GRAPH_BG, width=max(1, int(3 * self.zoom)), **opts)
            line([(18, -18), (18, 18)], fill=color, width=4)
        elif el.kind in ("vdc", "vac", "isrc"):
            line([(-40, 0), (-22, 0)])
            line([(22, 0), (40, 0)])
            sx1, sy1 = self.transform(el, -22, -22)
            sx2, sy2 = self.transform(el, 22, 22)
            opts = {"dash": dash} if dash else {}
            c.create_oval(sx1, sy1, sx2, sy2, outline=color, width=max(1, int(3 * self.zoom)), **opts)
            if el.kind == "isrc":
                line([(-6, 8), (8, 0), (-6, -8)], fill=color, width=2)
            else:
                text(-10, -2, "+", fill=color, size=12)
                text(10, 1, "−", fill=color, size=12)
                if el.kind == "vac":
                    text(0, 12, "~", fill=color, size=11)
        elif el.kind == "gnd":
            line([(0, 0), (0, 12)], fill=color)
            line([(-20, 12), (20, 12)], fill=color)
            line([(-13, 22), (13, 22)], fill=color)
            line([(-6, 31), (6, 31)], fill=color)
        text(0, -34, el.name, fill=color, size=9)
        if el.kind not in ("gnd",):
            text(0, 34, el.value, fill=theme.MUTED_2, size=8)

    def update_properties(self):
        obj = self.selected
        if isinstance(obj, CircuitElement):
            self.prop_title.set(f"{obj.name} · {obj.kind.upper()} · 회전 {obj.rotation}°")
            self.name_entry.configure(state="normal")
            self.value_entry.configure(state="normal" if obj.kind not in ("gnd", "d") else "disabled")
            self.param_entry.configure(state="normal" if obj.kind in ("vac", "d", "vdc", "isrc", "c", "l") else "disabled")
            if hasattr(self, "value_label"):
                self.value_label.configure(text="Transient amplitude (V)" if obj.kind == "vac" else "값")
            if hasattr(self, "param_label"):
                if obj.kind == "vac":
                    self.param_label.configure(text="Transient: offset/freq/phase · AC: ac/ac_phase")
                elif obj.kind == "d":
                    self.param_label.configure(text="Diode: Is, n, Vt")
                elif obj.kind == "c":
                    self.param_label.configure(text="Initial Vc(0) [V] · terminal 1 - terminal 2")
                elif obj.kind == "l":
                    self.param_label.configure(text="Initial IL(0) [A] · terminal 1 -> terminal 2")
                else:
                    self.param_label.configure(text="Waveform / AC params")
            self.name_var.set(obj.name)
            self.value_var.set("0" if obj.kind == "gnd" else obj.value)
            if obj.kind == "vac":
                self.param_var.set(
                    f"offset={obj.params.get('offset', '0')}, freq={obj.params.get('frequency', '1k')}, "
                    f"phase={obj.params.get('phase', '0')}, ac={obj.params.get('ac_mag', obj.value)}, "
                    f"ac_phase={obj.params.get('ac_phase', '0')}"
                )
            elif obj.kind == "d":
                self.param_var.set(f"{obj.params.get('is', '1e-14')}, {obj.params.get('n', '1')}, {obj.params.get('vt', '0.02585')}")
            elif obj.kind in ("vdc", "isrc"):
                waveform = obj.params.get("waveform", "dc")
                if waveform == "pulse":
                    self.param_var.set("pulse: " + ", ".join(obj.params.get(k, "") for k in ("v1", "v2", "td", "tr", "tf", "pw", "per")))
                elif waveform == "pwl":
                    self.param_var.set("pwl: " + obj.params.get("pwl", ""))
                else:
                    self.param_var.set("")
            elif obj.kind == "c":
                self.param_var.set(obj.params.get("ic_v", obj.params.get("ic", "0")))
            elif obj.kind == "l":
                self.param_var.set(obj.params.get("ic_i", obj.params.get("ic", "0")))
            else:
                self.param_var.set("")
        elif isinstance(obj, Wire):
            self._property_target_id = None
            self.prop_title.set(f"Wire {obj.id} · {len(obj.points)} points")
            self.name_entry.configure(state="disabled")
            self.value_entry.configure(state="disabled")
            self.param_entry.configure(state="disabled")
            self.name_var.set("")
            self.value_var.set("")
            self.param_var.set("")
        else:
            self._property_target_id = None
            self.prop_title.set("선택된 항목 없음")
            self.name_entry.configure(state="disabled")
            self.value_entry.configure(state="disabled")
            self.param_entry.configure(state="disabled")
            self.name_var.set("")
            self.value_var.set("")
            self.param_var.set("")

    def apply_properties(self):
        if not isinstance(self.selected, CircuitElement):
            return
        old_name = self.selected.name
        old_value = self.selected.value
        old_params = dict(self.selected.params)
        name = self.name_var.get().strip()
        value = self.value_var.get().strip()
        if self.selected.kind == "vac":
            old_param = self.param_var.get()
        elif self.selected.kind == "d":
            old_param = f"{self.selected.params.get('is', '1e-14')}, {self.selected.params.get('n', '1')}, {self.selected.params.get('vt', '0.02585')}"
        elif self.selected.kind in ("vdc", "isrc", "c", "l"):
            old_param = self.param_var.get()
        else:
            old_param = ""
        if not name:
            self.status_metric.set("이름을 입력하세요. 기존 이름은 유지됩니다.", theme.DANGER)
            self.name_var.set(self.selected.name)
            return
        if any(el is not self.selected and el.name == name for el in self.model.elements):
            self.status_metric.set(f"이름 {name}은 이미 사용 중입니다. 다른 이름을 쓰세요.", theme.DANGER)
            self.name_var.set(self.selected.name)
            return
        if self.selected.kind not in ("gnd", "d") and parse_value(value, None) is None:
            self.status_metric.set("값 형식을 확인하세요. 기존값은 유지됩니다. 예: 1k, 4.7k, 10u, 100n, 1meg", theme.DANGER)
            self.value_var.set(self.selected.value)
            return
        new_params = None
        if self.selected.kind == "vac":
            raw_items = [p.strip() for p in self.param_var.get().replace(";", ",").split(",") if p.strip()]
            parsed_items = {}
            positional = []
            for item in raw_items:
                if "=" in item:
                    key, item_value = [p.strip() for p in item.split("=", 1)]
                    parsed_items[key.lower().replace(" ", "")] = item_value
                else:
                    positional.append(item)
            if positional:
                parsed_items.setdefault("frequency", positional[0])
            if len(positional) > 1:
                parsed_items.setdefault("phase", positional[1])
            key_map = {
                "offset": "offset", "voff": "offset",
                "freq": "frequency", "frequency": "frequency",
                "phase": "phase",
                "ac": "ac_mag", "ac_mag": "ac_mag",
                "acphase": "ac_phase", "ac_phase": "ac_phase",
            }
            new_params = dict(self.selected.params)
            for key, item_value in parsed_items.items():
                if parse_value(item_value, None) is None:
                    self.status_metric.set("VAC 파라미터 형식을 확인하세요. 예: offset=0, freq=5k, phase=0, ac=1, ac_phase=0", theme.DANGER)
                    self.param_var.set(old_param)
                    return
                mapped = key_map.get(key)
                if mapped:
                    new_params[mapped] = item_value
            new_params.setdefault("waveform", "sin")
            new_params.setdefault("offset", "0")
            new_params.setdefault("frequency", "1k")
            new_params.setdefault("phase", "0")
            new_params.setdefault("ac_mag", value)
            new_params.setdefault("ac_phase", "0")
        elif self.selected.kind == "d":
            parts = [p.strip() for p in self.param_var.get().split(",")]
            if len(parts) < 3:
                self.status_metric.set("다이오드는 Is, n, Vt 형식으로 입력하세요. 기존값은 유지됩니다.", theme.DANGER)
                self.param_var.set(old_param)
                return
            keys = ("is", "n", "vt")
            parsed = {}
            for key, text in zip(keys, parts[:3]):
                if parse_value(text, None) is None:
                    self.status_metric.set("다이오드 파라미터 형식을 확인하세요. 기존값은 유지됩니다.", theme.DANGER)
                    self.param_var.set(old_param)
                    return
                parsed[key] = text
            new_params = parsed
        elif self.selected.kind in ("c", "l"):
            raw_ic = self.param_var.get().strip()
            if not raw_ic:
                self.status_metric.set("초기조건 값을 입력하세요. 기존값은 유지됩니다.", theme.DANGER)
                self.param_var.set(old_param)
                return
            if parse_finite_value(raw_ic, None) is None:
                self.status_metric.set("초기조건 값 형식을 확인하세요. 예: 2, -1, 500m, 1u", theme.DANGER)
                self.param_var.set(old_param)
                return
            new_params = dict(self.selected.params)
            if self.selected.kind == "c":
                new_params["ic_v"] = raw_ic
                new_params.pop("ic", None)
            else:
                new_params["ic_i"] = raw_ic
                new_params.pop("ic", None)
        elif self.selected.kind in ("vdc", "isrc"):
            raw_param = self.param_var.get().strip()
            if raw_param:
                low = raw_param.lower()
                if self.selected.kind == "isrc" and low.startswith("ac"):
                    values = raw_param.replace("=", " ").replace(",", " ").split()
                    if len(values) < 2 or parse_value(values[1], None) is None:
                        self.status_metric.set("ISRC AC는 ac=크기 또는 ac 크기 형식으로 입력하세요.", theme.DANGER)
                        self.param_var.set(old_param)
                        return
                    new_params = {"waveform": "dc", "ac_mag": values[1], "ac_phase": values[2] if len(values) > 2 else "0"}
                elif low.startswith("pulse:"):
                    values = [part.strip() for part in raw_param.split(":", 1)[1].replace(",", " ").split()]
                    if len(values) < 7 or any(parse_value(item, None) is None for item in values[:7]):
                        self.status_metric.set("PULSE는 pulse: V1 V2 TD TR TF PW PER 형식입니다.", theme.DANGER)
                        self.param_var.set(old_param)
                        return
                    new_params = {"waveform": "pulse"}
                    for key, item in zip(("v1", "v2", "td", "tr", "tf", "pw", "per"), values[:7]):
                        new_params[key] = item
                elif low.startswith("pwl:"):
                    raw_pwl = raw_param.split(":", 1)[1].strip()
                    values = raw_pwl.replace(",", " ").split()
                    if len(values) < 4 or len(values) % 2 or any(parse_value(item, None) is None for item in values):
                        self.status_metric.set("PWL은 pwl: t1 v1 t2 v2 ... 형식입니다.", theme.DANGER)
                        self.param_var.set(old_param)
                        return
                    new_params = {"waveform": "pwl", "pwl": raw_pwl}
                else:
                    self.status_metric.set("파형 파라미터는 pulse: ... 또는 pwl: ... 형식으로 입력하세요.", theme.DANGER)
                    self.param_var.set(old_param)
                    return
            else:
                new_params = {"waveform": "dc"}
        future_value = self.selected.value if self.selected.kind in ("gnd", "d") else value
        future_params = dict(self.selected.params)
        if new_params is not None:
            future_params.update(new_params)
        if old_name == name and old_value == future_value and old_params == future_params:
            self.status_metric.set("변경된 속성이 없습니다.", theme.TEXT_2)
            self.update_properties()
            return
        # [4번] Property changes become one undo step only after validation passes.
        self.push_undo()
        self.selected.name = name
        if self.selected.kind not in ("gnd", "d"):
            self.selected.value = value
        if new_params is not None:
            self.selected.params.update(new_params)
        self.invalidate_analysis_cache("속성 변경", redraw=False, notify=False)
        self.status_metric.set("속성을 적용했습니다.", theme.GOOD)
        self.redraw()
        self.update_properties()

    def rotate_selected(self):
        if self.tool in ("r", "c", "l", "d", "vdc", "vac", "isrc", "gnd"):
            self.preview_rotation = (self.preview_rotation + 90) % 360
            self.status_metric.set(f"다음 배치 회전: {self.preview_rotation}°", theme.ACCENT)
            self.redraw()
            return
        if isinstance(self.selected, CircuitElement):
            self.push_undo()
            self.selected.rotation = (self.selected.rotation + 90) % 360
            self.invalidate_analysis_cache("소자 회전", redraw=False, notify=False)
            if self.selected.kind == "gnd":
                self.status_metric.set(f"GND 회전: {self.selected.rotation}°", theme.ACCENT)
            self.redraw()
            self.update_properties()

    def unique_copy_name(self, base):
        existing = {el.name for el in self.model.elements}
        root = f"{base}_copy"
        if root not in existing:
            return root
        idx = 2
        while f"{root}_{idx}" in existing:
            idx += 1
        return f"{root}_{idx}"

    def duplicate_selected(self):
        # [8번] Ctrl+D duplicates the selected element by one grid step; wires are intentionally excluded.
        if not isinstance(self.selected, CircuitElement):
            self.status_metric.set("복제할 소자를 선택하세요.", theme.WARN)
            return
        src = self.selected
        self.push_undo()
        dup = self.model.add_element(src.kind, src.x + GRID, src.y + GRID)
        dup.name = self.unique_copy_name(src.name)
        dup.value = src.value
        dup.rotation = src.rotation
        dup.params = dict(src.params)
        self.selected = dup
        self.invalidate_analysis_cache("소자 복제", redraw=False, notify=False)
        self.status_metric.set(f"{src.name}을(를) 복제했습니다.", theme.ACCENT)
        self.redraw()
        self.update_properties()

    def delete_selected(self):
        if self.selected:
            self.remove_object(self.selected)
    
    def remove_object(self, obj):
        self.push_undo()
        if isinstance(obj, CircuitElement):
            if self.probe_element and self.probe_element.id == obj.id:
                self.probe_element = None
            self.measurement_labels = [label for label in self.measurement_labels if label.element_id != obj.id]
            self.refresh_label_list()
        elif isinstance(obj, Wire):
            # FIX 1-3: deleting a wire can stale voltage probes/labels.
            if self.probe_point and any(point_on_segment(self.probe_point, a, b) for a, b in zip(obj.points, obj.points[1:])):
                self.probe_point = None
            self.measurement_labels = [
                label for label in self.measurement_labels
                if not (label.kind == "voltage" and label.point and any(point_on_segment(label.point, a, b) for a, b in zip(obj.points, obj.points[1:])))
            ]
            self.refresh_label_list()
        self.model.remove(obj)
        # [7번] Re-run stale probe cleanup after wire/element deletion because node ownership can change.
        self.validate_probe_state()
        self._property_target_id = None
        if self.selected is obj:
            self.selected = None
        self.invalidate_analysis_cache("객체 삭제", redraw=False, notify=False)
        self.redraw()
        self.update_properties()

    def solver(self):
        return CircuitSolver(self.model)

    def validate_probe_state(self, solver=None):
        warnings = []
        if self.probe_element and self.probe_element not in self.model.elements:
            self.probe_element = None
            warnings.append("Probe I 대상 소자가 삭제되어 Probe를 해제했습니다.")
        if self.probe_point:
            try:
                solver = solver or self.solver()
                _uf, point_node = solver.build_nodes()
                if solver.node_for_point(self.probe_point, point_node) is None:
                    self.probe_point = None
                    warnings.append("Probe V 위치가 회로 노드에 연결되어 있지 않아 Probe를 해제했습니다.")
            except ValueError:
                pass
        valid_ids = {el.id for el in self.model.elements}
        before = len(self.measurement_labels)
        self.measurement_labels = [
            label for label in self.measurement_labels
            if label.kind != "current" or label.element_id in valid_ids
        ]
        if len(self.measurement_labels) != before:
            warnings.append("삭제된 소자를 참조하던 Label I를 제거했습니다.")
            self.refresh_label_list()
        return warnings

    def label_dc_lines(self, result, solver):
        lines = []
        for label in self.measurement_labels:
            if label.kind == "voltage" and label.point:
                node = solver.node_for_point(label.point, result["point_node"])
                if node is None:
                    lines.append(f"{label.name}: invalid node")
                else:
                    lines.append(f"{label.name} = {format_eng(result['nodes'].get(node, 0.0), 'V')}")
            elif label.kind == "current":
                element = self.element_by_id(label.element_id)
                if element is None:
                    lines.append(f"{label.name}: missing element")
                else:
                    lines.append(f"{label.name} = {format_eng(result['currents'].get(element.name, 0.0), 'A')}  terminal 1 -> terminal 2")
        return lines

    def transient_trace_targets(self, initial_result, solver, labels=None, probe_point=UNSET, probe_element=UNSET):
        labels = self.measurement_labels if labels is None else labels
        probe_point = self.probe_point if probe_point is UNSET else probe_point
        probe_element = self.probe_element if probe_element is UNSET else probe_element

        def element_by_id_local(element_id):
            for element in solver.model.elements:
                if element.id == element_id:
                    return element
            return None

        targets = []
        for label in labels:
            if label.kind == "voltage" and label.point:
                node = solver.node_for_point(label.point, initial_result["point_node"])
                if node:
                    targets.append((label.name, "V", "node", node))
            elif label.kind == "current":
                element = element_by_id_local(label.element_id)
                if element is not None:
                    targets.append((label.name, "A", "current", element.name))
        if probe_point:
            node = solver.node_for_point(probe_point, initial_result["point_node"])
            if node:
                if not any(kind == "node" and key == node for _name, _unit, kind, key in targets):
                    targets.append((f"V({node})", "V", "node", node))
            else:
                raise ValueError(f"Probe 좌표 {probe_point}가 어느 노드와도 일치하지 않습니다. 노드/단자 위에 다시 찍어주세요.")
        if probe_element:
            if probe_element in solver.model.elements:
                if not any(kind == "current" and key == probe_element.name for _name, _unit, kind, key in targets):
                    targets.append((f"I({probe_element.name})", "A", "current", probe_element.name))
            else:
                raise ValueError("Probe I 대상 소자가 삭제되었습니다. Probe를 다시 지정하세요.")
        if not targets:
            nodes = [n for n in sorted(initial_result["nodes"]) if n != "0"]
            node = nodes[0] if nodes else "0"
            targets.append((f"V({node})", "V", "node", node))
        return targets

    def solve_transient_traces(
        self,
        t_stop,
        dt,
        cancel_event=None,
        progress_callback=None,
        integration=None,
        model=None,
        measurement_labels=None,
        probe_point=UNSET,
        probe_element=UNSET,
        return_result=False,
    ):
        if t_stop <= 0 or dt <= 0:
            raise ValueError("t_stop과 dt는 0보다 커야 합니다.")
        analysis_model = model or self.model
        solver = CircuitSolver(analysis_model)
        times = self.transient_time_points(t_stop, dt)
        requested_steps = len(times)
        warnings = []
        if requested_steps > MAX_TRANSIENT_STEPS:
            raise ValueError(
                "Transient step too large\n"
                f"원인: 계산 스텝 수가 {requested_steps}개로 최대 {MAX_TRANSIENT_STEPS}개를 초과합니다.\n"
                "해결: dt를 키우거나 t_stop을 줄이세요."
            )
        steps = requested_steps
        if model is None:
            warnings.extend(self.transient_quality_warnings(t_stop, dt))
        cap_prev = {e.id: (e.initial_cap_voltage(), 0.0) for e in analysis_model.elements if e.kind == "c"}
        ind_prev = {e.id: (e.initial_ind_current(), 0.0) for e in analysis_model.elements if e.kind == "l"}
        integration = (integration or self.integration_var.get()).lower()
        initial_result = solver.solve_transient_initial_state(t=times[0])
        targets = self.transient_trace_targets(
            initial_result,
            solver,
            labels=measurement_labels,
            probe_point=probe_point,
            probe_element=probe_element,
        )
        traces = {name: {"unit": unit, "raw_samples": [], "samples": []} for name, unit, _kind, _key in targets}
        progress_stride = max(1, steps // 100)
        result = initial_result

        def record_sample(sample_t, sample_result):
            for name, _unit, kind, key in targets:
                value = sample_result["nodes"].get(key, 0.0) if kind == "node" else sample_result["currents"].get(key, 0.0)
                traces[name]["raw_samples"].append((sample_t, value))

        def update_dynamic_history(sample_result):
            for el in analysis_model.elements:
                if el.kind == "c":
                    p, n = el.terminals()
                    np, nn = sample_result["point_node"].get(p, "0"), sample_result["point_node"].get(n, "0")
                    voltage = sample_result["nodes"].get(np, 0.0) - sample_result["nodes"].get(nn, 0.0)
                    cap_prev[el.id] = (voltage, sample_result["currents"].get(el.name, 0.0))
                elif el.kind == "l":
                    p, n = el.terminals()
                    np, nn = sample_result["point_node"].get(p, "0"), sample_result["point_node"].get(n, "0")
                    voltage = sample_result["nodes"].get(np, 0.0) - sample_result["nodes"].get(nn, 0.0)
                    ind_prev[el.id] = (sample_result["currents"].get(el.name, 0.0), voltage)

        # t=0 must be the user-defined physical IC, not the first companion step.
        record_sample(times[0], initial_result)
        update_dynamic_history(initial_result)
        previous_t = times[0] if times else 0.0
        for step, t in enumerate(times[1:], start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("cancelled")
            if progress_callback is not None and (step % progress_stride == 0):
                progress_callback(step, steps)
            dt_step = max(t - previous_t, 1e-18)
            result = solver.solve_dc(cap_prev=cap_prev, ind_prev=ind_prev, dt=dt_step, t=t, integration=integration)
            record_sample(t, result)
            update_dynamic_history(result)
            previous_t = t
        if progress_callback is not None:
            progress_callback(steps, steps)
        if model is None:
            self.last_result = result
        for trace in traces.values():
            raw = trace.get("raw_samples", [])
            trace["samples"] = minmax_downsample(raw, MAX_PLOT_POINTS)
        if any(len(trace.get("raw_samples", [])) > len(trace.get("samples", [])) for trace in traces.values()):
            warnings.append("그래프 표시는 peak 보존 다운샘플링을 적용했습니다. CSV는 raw samples를 내보냅니다.")
        if return_result:
            return traces, warnings, result
        return traces, warnings

    def ac_trace_targets(self, initial_result, solver):
        targets = []
        for label in self.measurement_labels:
            if label.kind == "voltage" and label.point:
                node = solver.node_for_point(label.point, initial_result["point_node"])
                if node:
                    targets.append((label.name, "node", node))
            elif label.kind == "current":
                element = self.element_by_id(label.element_id)
                if element is not None:
                    targets.append((label.name, "current", element.name))
        if self.probe_point:
            node = solver.node_for_point(self.probe_point, initial_result["point_node"])
            if node:
                if not any(kind == "node" and key == node for _name, kind, key in targets):
                    targets.append((f"V({node})", "node", node))
            else:
                raise ValueError(f"Probe 좌표 {self.probe_point}가 어느 노드와도 일치하지 않습니다. 노드/단자 위에 다시 찍어주세요.")
        if self.probe_element:
            if self.probe_element in self.model.elements:
                if not any(kind == "current" and key == self.probe_element.name for _name, kind, key in targets):
                    targets.append((f"I({self.probe_element.name})", "current", self.probe_element.name))
            else:
                raise ValueError("Probe I 대상 소자가 삭제되었습니다. Probe를 다시 지정하세요.")
        if not targets:
            nodes = [n for n in sorted(initial_result["nodes"]) if n != "0"]
            node = nodes[0] if nodes else "0"
            targets.append((f"V({node})", "node", node))
        return targets

    def solve_ac_sweep_traces(self, f_start, f_stop, points_per_decade):
        solver = self.solver()
        rows = solver.solve_ac_sweep(f_start, f_stop, points_per_decade)
        if not rows:
            return {}
        targets = self.ac_trace_targets(rows[0][1], solver)
        traces = {}
        trace_names = {}
        for name, kind, key in targets:
            prefix = "I" if kind == "current" else "V"
            token = key
            if kind == "node" and not name.startswith(("Probe ", "V(", "I(")):
                token = name
            linear_name = f"{prefix}({token})"
            db_name = f"{prefix}DB({token})"
            phase_name = f"{prefix}P({token})"
            trace_names[(kind, key, name)] = (linear_name, db_name, phase_name)
            traces[linear_name] = {"unit": "A" if kind == "current" else "V", "samples": [], "psp_kind": "lin"}
            traces[db_name] = {"unit": "dB", "samples": [], "psp_kind": "db"}
            traces[phase_name] = {"unit": "deg", "samples": [], "psp_kind": "phase"}
        voltage_targets = [(name, key, trace_names[("node", key, name)][0][2:-1]) for name, kind, key in targets if kind == "node"]
        transfer_pairs = []
        if len(voltage_targets) >= 2:
            ref_name, ref_key, ref_token = next((item for item in voltage_targets if item[2].lower() in ("vin", "v(input)", "input")), voltage_targets[0])
            for name, key, token in voltage_targets:
                if key != ref_key:
                    gain_name = f"VDB({token})-VDB({ref_token})"
                    phase_name = f"VP({token})-VP({ref_token})"
                    traces[gain_name] = {"unit": "dB", "samples": [], "psp_kind": "gain_db"}
                    traces[phase_name] = {"unit": "deg", "samples": [], "psp_kind": "gain_phase"}
                    transfer_pairs.append((key, ref_key, gain_name, phase_name))
        for freq, result in rows:
            values = {}
            for name, kind, key in targets:
                value = result["nodes"].get(key, 0j) if kind == "node" else result["currents"].get(key, 0j)
                values[(kind, key)] = value
                mag = 20.0 * math.log10(max(abs(value), 1e-30))
                phase = math.degrees(math.atan2(value.imag, value.real))
                linear_name, db_name, phase_name = trace_names[(kind, key, name)]
                traces[linear_name]["samples"].append((freq, abs(value)))
                traces[db_name]["samples"].append((freq, mag))
                traces[phase_name]["samples"].append((freq, phase))
            for key, ref_key, gain_name, phase_name in transfer_pairs:
                value = values.get(("node", key), 0j)
                ref = values.get(("node", ref_key), 0j)
                ratio = value / ref if abs(ref) > 1e-30 else 0j
                traces[gain_name]["samples"].append((freq, 20.0 * math.log10(max(abs(ratio), 1e-30))))
                traces[phase_name]["samples"].append((freq, math.degrees(math.atan2(ratio.imag, ratio.real))))
        return traces

    def run_dc(self):
        if self.abort_on_preflight_errors("dc"):
            return
        warnings = self.preflight_warnings()
        try:
            solver = self.solver()
            warnings.extend(self.validate_probe_state(solver))
            result = solver.solve_dc()
        except ValueError as exc:
            self.status_metric.set(str(exc), theme.DANGER)
            self.write_results(str(exc))
            return
        self.last_result = result
        self.last_transient = None
        lines = ["DC Operating Point"]
        floating_header = self.floating_terminal_warning()
        if floating_header:
            lines.insert(0, floating_header)
        if self.probe_point:
            node = solver.node_for_point(self.probe_point, result["point_node"]) or "0"
            lines.append(f"Probe V({node}) = {result['nodes'].get(node, 0.0):.5g} V")
        if self.probe_element:
            lines.append(f"Probe I({self.probe_element.name}) = {format_eng(result['currents'].get(self.probe_element.name, 0), 'A')}  방향: terminal 1 -> terminal 2")
        if warnings:
            lines.append("주의: " + " / ".join(warnings))
        label_lines = self.label_dc_lines(result, solver)
        if label_lines:
            lines.append("")
            lines.append("Labels:")
            lines.extend(label_lines)
        lines.append("")
        for node, value in sorted(result["nodes"].items()):
            lines.append(f"{node:>4} = {value: .5g} V")
        lines.append("")
        for name, current in sorted(result["currents"].items()):
            lines.append(f"I({name}) = {format_eng(current, 'A')}")
        self.status_metric.set("DC 해석 완료" if not warnings else "DC 해석 완료 · 연결 경고가 있습니다.", theme.GOOD if not warnings else theme.WARN)
        self.write_results("\n".join(lines))
        self.redraw()

    def run_transient(self):
        if self.abort_on_preflight_errors("tran"):
            return
        warnings = self.preflight_warnings()
        warnings.extend(self.validate_probe_state())
        if not self.probe_point and not self.probe_element and not self.measurement_labels:
            self.status_metric.set("Probe 또는 Label이 없어 첫 번째 노드를 기본 trace로 표시합니다.", theme.WARN)
        try:
            t_stop, dt, requested_steps = self.transient_inputs_or_raise()
            transient_warnings = self.transient_quality_warnings(t_stop, dt)
            if requested_steps >= WORKER_TRANSIENT_STEPS:
                self.start_transient_worker(t_stop, dt, requested_steps, warnings + transient_warnings)
                return
            traces, solver_warnings = self.solve_transient_traces(t_stop, dt, integration=self.integration_var.get().lower())
            transient_warnings.extend(solver_warnings)
            warnings.extend(transient_warnings)
        except ValueError as exc:
            self.status_metric.set(str(exc), theme.DANGER)
            self.write_results(str(exc))
            return
        self.finish_transient(traces, warnings, t_stop, dt)

    def set_analysis_running(self, running):
        state = "disabled" if running else "normal"
        for button in (getattr(self, "dc_button", None), getattr(self, "tran_button", None), getattr(self, "ac_button", None)):
            if button is not None:
                button.configure(state=state)
        if getattr(self, "cancel_button", None) is not None:
            self.cancel_button.configure(state="normal" if running else "disabled")

    def cancel_simulation(self):
        if self.sim_cancel_event is not None:
            self.sim_cancel_event.set()
            self.status_metric.set("계산 취소 요청을 보냈습니다.", theme.WARN)

    def start_transient_worker(self, t_stop, dt, requested_steps, warnings):
        self.sim_queue = queue.Queue()
        self.sim_cancel_event = threading.Event()
        integration = self.integration_var.get().lower()
        snapshot = self.transient_worker_snapshot()
        self.set_analysis_running(True)
        self.status_metric.set(f"Transient 계산 중: 0/{requested_steps} steps", theme.WARN)
        self.write_results(f"Transient 계산 중...\nt_stop={format_eng(t_stop, 's')}, dt={format_eng(dt, 's')}, steps={requested_steps}\nCancel로 중단할 수 있습니다.")

        def worker():
            try:
                analysis_model = CircuitModel()
                analysis_model.from_dict(snapshot["model"])
                labels = [MeasurementLabel.from_dict(item) for item in snapshot["measurement_labels"]]
                probe_element = None
                if snapshot["probe_element_id"] is not None:
                    probe_element = next((el for el in analysis_model.elements if el.id == snapshot["probe_element_id"]), None)
                traces, worker_warnings, last_result = self.solve_transient_traces(
                    t_stop,
                    dt,
                    cancel_event=self.sim_cancel_event,
                    progress_callback=lambda step, total: self.sim_queue.put(("progress", step, total)),
                    integration=integration,
                    model=analysis_model,
                    measurement_labels=labels,
                    probe_point=snapshot["probe_point"],
                    probe_element=probe_element,
                    return_result=True,
                )
                self.sim_queue.put(("done", traces, worker_warnings, last_result, snapshot["signature"]))
            except RuntimeError as exc:
                self.sim_queue.put(("cancelled", str(exc)))
            except Exception as exc:
                self.sim_queue.put(("error", str(exc)))

        self.sim_thread = threading.Thread(target=worker, daemon=True)
        self.sim_thread.start()
        self.after(100, lambda: self.poll_transient_worker(t_stop, dt, warnings))

    def poll_transient_worker(self, t_stop, dt, warnings):
        if self.sim_queue is None:
            return
        try:
            while True:
                item = self.sim_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _kind, step, total = item
                    self.status_metric.set(f"Transient 계산 중: {step}/{total} steps", theme.WARN)
                elif kind == "done":
                    _kind, traces, worker_warnings, last_result, snapshot_signature = item
                    self.set_analysis_running(False)
                    self.sim_thread = None
                    self.sim_cancel_event = None
                    self.sim_queue = None
                    self.finish_transient(
                        traces,
                        warnings + worker_warnings,
                        t_stop,
                        dt,
                        last_result=last_result,
                        snapshot_signature=snapshot_signature,
                    )
                    return
                elif kind == "cancelled":
                    self.set_analysis_running(False)
                    self.sim_thread = None
                    self.sim_cancel_event = None
                    self.sim_queue = None
                    self.status_metric.set("Transient 계산을 취소했습니다.", theme.WARN)
                    self.write_results("Transient 계산을 취소했습니다.")
                    return
                elif kind == "error":
                    _kind, message = item
                    self.set_analysis_running(False)
                    self.sim_thread = None
                    self.sim_cancel_event = None
                    self.sim_queue = None
                    self.status_metric.set(message, theme.DANGER)
                    self.write_results(message)
                    return
        except queue.Empty:
            pass
        if self.sim_thread is not None and self.sim_thread.is_alive():
            self.after(100, lambda: self.poll_transient_worker(t_stop, dt, warnings))
        else:
            self.set_analysis_running(False)

    def finish_transient(self, traces, warnings, t_stop, dt, last_result=None, snapshot_signature=None):
        warnings = list(warnings or [])
        if last_result is not None:
            self.last_result = last_result
        if snapshot_signature is not None and snapshot_signature != self.model_signature():
            warnings.append("계산 중 회로가 변경되어 이 결과는 계산 시작 당시 회로 기준입니다.")
        first_name = next(iter(traces))
        first_trace = traces[first_name]
        samples = first_trace.get("samples", [])
        raw_reference = first_trace.get("raw_samples", samples)
        raw_count = max(
            (len(trace.get("raw_samples", trace.get("samples", []))) for trace in traces.values()),
            default=len(samples),
        )
        display_count = max((len(trace.get("samples", [])) for trace in traces.values()), default=len(samples))
        final_step = 0.0
        if len(raw_reference) >= 2:
            final_step = raw_reference[-1][0] - raw_reference[-2][0]
        elif raw_reference:
            final_step = raw_reference[-1][0]
        metadata = {
            "t_stop": t_stop,
            "nominal_dt": dt,
            "final_step_dt": final_step,
            "raw_samples": raw_count,
            "display_samples": display_count,
            "integration": self.integration_var.get(),
        }
        self.last_transient = {"traces": traces, "metadata": metadata}
        popup_title = (
            f"Transient - PSpice Lite | samples={raw_count}, "
            f"dt={format_eng(dt, 's')}, final step={format_eng(final_step, 's')}"
        )
        TracePlotWindow(self, popup_title, traces)
        self.redraw()
        unit = first_trace.get("unit", "V")
        final = samples[-1][1] if samples else 0.0
        summary = (
            f"Transient: requested t_stop={format_eng(t_stop, 's')}, nominal dt={format_eng(dt, 's')}, "
            f"final step dt={format_eng(final_step, 's')}, samples={raw_count}, integration={self.integration_var.get()}"
        )
        self.status_metric.set(f"{summary} · {first_name} last {format_eng(final, unit)}", theme.GOOD if not warnings else theme.WARN)
        trace_lines = []
        for name, trace in traces.items():
            source = trace.get("raw_samples") or trace.get("samples", [])
            if not source:
                continue
            values = [value for _time, value in source]
            trace_unit = trace.get("unit", "")
            rms = math.sqrt(sum(value * value for value in values) / len(values))
            display_len = len(trace.get("samples", []))
            raw_note = "raw" if trace.get("raw_samples") else "display"
            trace_lines.append(
                f"{name}: final={format_eng(values[-1], trace_unit)}, min={format_eng(min(values), trace_unit)}, "
                f"max={format_eng(max(values), trace_unit)}, rms={format_eng(rms, trace_unit)}, "
                f"raw={len(source)}, display={display_len} ({raw_note})"
            )
        extra = "\n주의: " + " / ".join(warnings) if warnings else ""
        floating_header = self.floating_terminal_warning()
        prefix = floating_header + "\n" if floating_header else ""
        ic_lines = self.initial_condition_lines()
        ic_text = "\nInitial Conditions:\n" + "\n".join(ic_lines) if ic_lines else ""
        self.write_results(prefix + summary + f"\nDisplay samples: {display_count}{ic_text}\nTraces:\n" + "\n".join(trace_lines) + extra)

    def run_ac_sweep(self):
        if self.abort_on_preflight_errors("ac"):
            return
        warnings = self.preflight_warnings()
        warnings.extend(self.validate_probe_state())
        try:
            f_start, f_stop, ppd = self.ac_inputs_or_raise()
            has_ac_source = any(
                el.kind in ("vac", "isrc") and abs(parse_value(el.params.get("ac_mag", el.value if el.kind == "vac" else "0"), 0.0)) > 0
                for el in self.model.elements
            )
            if not has_ac_source:
                raise ValueError("AC Sweep에는 AC magnitude가 0이 아닌 VAC 또는 ISRC가 필요합니다.")
            traces = self.solve_ac_sweep_traces(f_start, f_stop, ppd)
        except ValueError as exc:
            self.status_metric.set(str(exc), theme.DANGER)
            self.write_results(str(exc))
            return
        self.last_ac_result = {"traces": traces, "f_start": f_start, "f_stop": f_stop, "points_per_decade": ppd}
        TracePlotWindow(
            self,
            "AC Sweep - PSpice Lite",
            traces,
            x_label="f",
            x_unit="Hz",
            x_scale="log",
            preferred_unit="dB",
            lock_log_x=False,
        )
        first_name = next(iter(traces), "")
        samples = traces[first_name].get("samples", []) if first_name else []
        lines = [
            f"AC Sweep: {format_eng(f_start, 'Hz')} ~ {format_eng(f_stop, 'Hz')} · {ppd} pt/dec",
            "Unit 메뉴에서 linear V/A, dB, phase를 전환하세요. 전압 label이 2개 이상이면 Vout/Vin gain trace도 함께 생성됩니다.",
            "Traces:",
        ]
        for name, trace in traces.items():
            if trace.get("samples"):
                lines.append(f"{name}: {format_eng(trace['samples'][-1][1], trace.get('unit', ''))}")
        if warnings:
            lines.append("")
            lines.append("주의: " + " / ".join(warnings))
        self.status_metric.set("AC Sweep 완료" if not warnings else "AC Sweep 완료 · 연결 경고가 있습니다.", theme.GOOD if not warnings else theme.WARN)
        self.write_results("\n".join(lines))

    def show_text_popup(self, title, text):
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=theme.BG)
        win.geometry("620x420")
        box = tk.Text(win, bg=theme.GRAPH_BG, fg=theme.TEXT_2, insertbackground=theme.TEXT_2, relief="flat", font=font(9), wrap="none")
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", text)
        box.configure(state="disabled")
        make_button(win, "닫기", win.destroy, "secondary").pack(anchor="e", padx=12, pady=(0, 12))

    def initial_condition_lines(self, model=None):
        model = model or self.model
        lines = []
        for el in model.elements:
            if el.kind == "c":
                raw = el.params.get("ic_v", el.params.get("ic", "0"))
                lines.append(f"{el.name}: Vc(0)={format_eng(parse_finite_value(raw, 0.0), 'V')} (terminal 1 - terminal 2)")
            elif el.kind == "l":
                raw = el.params.get("ic_i", el.params.get("ic", "0"))
                lines.append(f"{el.name}: IL(0)={format_eng(parse_finite_value(raw, 0.0), 'A')} (terminal 1 -> terminal 2)")
        return lines

    def show_netlist(self):
        try:
            solver = self.solver()
            _uf, point_node = solver.build_nodes()
        except ValueError as exc:
            self.write_results(f"Netlist를 만들려면 먼저 GND를 배치하세요.\n{exc}")
            return
        lines = ["* PSpice Lite netlist"]
        for el in self.model.elements:
            if el.kind == "gnd":
                continue
            terms = el.terminals()
            n1 = solver.node_for_point(terms[0], point_node) or "?"
            n2 = solver.node_for_point(terms[1], point_node) or "?"
            if el.kind == "vdc":
                lines.append(f"{el.name} {n1} {n2} DC {el.value}")
            elif el.kind == "vac":
                voff = el.params.get("offset", "0")
                freq = el.params.get("frequency", "1k")
                phase = el.params.get("phase", "0")
                ac_mag = el.params.get("ac_mag", el.value)
                ac_phase = el.params.get("ac_phase", "0")
                lines.append(f"{el.name} {n1} {n2} SIN({voff} {el.value} {freq} 0 0 {phase}) AC {ac_mag} {ac_phase}")
            elif el.kind == "isrc":
                ac_mag = el.params.get("ac_mag", "0")
                ac_phase = el.params.get("ac_phase", "0")
                lines.append(f"{el.name} {n1} {n2} DC {el.value} AC {ac_mag} {ac_phase}")
            elif el.kind == "c":
                ic = el.params.get("ic_v", el.params.get("ic", "0"))
                lines.append(f"{el.name} {n1} {n2} {el.value} IC={ic}")
            elif el.kind == "l":
                ic = el.params.get("ic_i", el.params.get("ic", "0"))
                lines.append(f"{el.name} {n1} {n2} {el.value} IC={ic}")
            else:
                lines.append(f"{el.name} {n1} {n2} {el.value}")
        for label in self.measurement_labels:
            if label.kind == "voltage" and label.point:
                node = solver.node_for_point(label.point, point_node) or "?"
                lines.append(f".label {label.name} {node}")
            elif label.kind == "current":
                element = self.element_by_id(label.element_id)
                if element is not None:
                    lines.append(f".probe I({element.name}) ; {label.name}")
        t_stop = self.tstop_field.get_float(default=0.01, positive=True)
        dt = self.dt_field.get_float(default=0.00005, positive=True)
        f_start = self.fstart_field.get_float(default=10.0, positive=True)
        f_stop = self.fstop_field.get_float(default=1000000.0, positive=True)
        ppd = int(self.ppd_field.get_float(default=50, positive=True))
        lines.append("")
        lines.append(".OP")
        lines.append(f".TRAN {format_eng(dt, 's')} {format_eng(t_stop, 's')}")
        lines.append(f".AC DEC {ppd} {format_eng(f_start, 'Hz')} {format_eng(f_stop, 'Hz')}")
        probe_terms = []
        for label in self.measurement_labels:
            if label.kind == "voltage" and label.point:
                node = solver.node_for_point(label.point, point_node) or "?"
                probe_terms.append(f"V({label.name or node})")
            elif label.kind == "current":
                element = self.element_by_id(label.element_id)
                if element is not None:
                    probe_terms.append(f"I({element.name})")
        if probe_terms:
            lines.append(".PROBE " + " ".join(probe_terms))
        ic_lines = self.initial_condition_lines()
        if ic_lines:
            lines.append("")
            lines.append("* Initial Conditions for transient")
            lines.extend("* " + line for line in ic_lines)
        lines.append(".END")
        self.write_results("\n".join(lines))
        self.show_text_popup("PSpice Lite Netlist", "\n".join(lines))
        self.status_metric.set("Netlist를 결과창에 표시했습니다.", theme.ACCENT)

    def write_results(self, text):
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        self.result_text.configure(state="disabled")

    def update_status(self):
        selected = getattr(self.selected, "name", "none") if self.selected else "none"
        if self.probe_element:
            probe = f"I({self.probe_element.name})"
        elif self.probe_point:
            probe = f"V@{self.probe_point}"
        else:
            probe = "none"
        cursor = self.hover_point if self.hover_point else "-"
        self.status.configure(
            text=f"Tool: {self.tool}    Zoom: {self.zoom * 100:.0f}%    Cursor: {cursor}    Elements: {len(self.model.elements)}    Wires: {len(self.model.wires)}    Selected: {selected}    Probe: {probe}"
        )

    def fit_view(self):
        # [8번] Fit View keeps every element and wire inside the canvas with an 80 px safety margin.
        points = []
        for el in self.model.elements:
            points.extend(el.terminals())
            points.append((el.x, el.y))
        for wire in self.model.wires:
            points.extend(wire.points)
        if not points:
            return
        self.canvas.update_idletasks()
        w, h = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        min_x, max_x = min(x for x, _ in points), max(x for x, _ in points)
        min_y, max_y = min(y for _, y in points), max(y for _, y in points)
        span_x = max(max_x - min_x, GRID * 4)
        span_y = max(max_y - min_y, GRID * 4)
        margin = 80
        self.zoom = max(0.35, min(2.8, min((w - margin * 2) / span_x, (h - margin * 2) / span_y)))
        self.pan_x = w / 2 - ((min_x + max_x) / 2) * self.zoom
        self.pan_y = h / 2 - ((min_y + max_y) / 2) * self.zoom
        self.redraw()
        self.update_status()

    def clear_results(self):
        self.last_result = None
        self.last_transient = None
        self.last_ac_result = None
        self.write_results("")
        self.status_metric.set("해석 결과를 지웠습니다.", theme.TEXT_2)
        self.redraw()

    def new_circuit(self):
        # [4번] New circuit is undoable; reset example visual state.
        if self.model.elements or self.model.wires:
            self.push_undo()
        self.model.clear()
        self.clear_interaction_state()
        self.redo_stack.clear()
        self.set_example_button_state(None)
        self.set_tool("select")
        self.redraw()
        self.update_properties()
        self.write_results("")
        self.status_metric.set("새 회로입니다. 소자를 배치하고 GND를 연결하세요.", theme.TEXT_2)
        self.update_status()

    def load_voltage_divider(self):
        # [4번] Loading examples pushes the previous circuit onto undo.
        if self.model.elements or self.model.wires:
            self.push_undo()
        self.model.clear()
        self.clear_interaction_state()
        self.redo_stack.clear()
        v1 = self.model.add_element("vdc", 120, 220)
        v1.name, v1.value, v1.rotation = "V1", "10", 90
        r1 = self.model.add_element("r", 160, 180)
        r1.name, r1.value = "R1", "1k"
        r2 = self.model.add_element("r", 200, 220)
        r2.name, r2.value, r2.rotation = "R2", "1k", 90
        g = self.model.add_element("gnd", 120, 260)
        self.model.add_wire([(200, 260), (120, 260)])
        self.probe_point = (200, 180)
        self.probe_element = None
        self.redraw()
        self.update_properties()
        self.set_tool("select")
        self.set_example_button_state("voltage")
        self.status_metric.set("전압분배 예제: DC 해석을 누르면 중간 노드가 약 5 V가 됩니다.", theme.ACCENT)
        self.write_results("예제: V1=10 V, R1=R2=1 kΩ\nDC 해석 예상: 중간 노드 ≈ 5 V")

    def load_rc_example(self):
        # [4번] Loading examples pushes the previous circuit onto undo.
        if self.model.elements or self.model.wires:
            self.push_undo()
        self.model.clear()
        self.clear_interaction_state()
        self.redo_stack.clear()
        v1 = self.model.add_element("vac", 120, 240)
        v1.name, v1.value, v1.rotation = "VAC1", "1", 0
        v1.params.update({"waveform": "sin", "frequency": "5k", "phase": "0"})
        r1 = self.model.add_element("r", 220, 240)
        r1.name, r1.value = "R1", "10k"
        c1 = self.model.add_element("c", 300, 280)
        c1.name, c1.value, c1.rotation = "C1", "10n", 90
        self.model.add_element("gnd", 80, 320)
        self.model.add_wire([(160, 240), (180, 240)])
        self.model.add_wire([(260, 240), (300, 240)])
        self.model.add_wire([(300, 320), (80, 320), (80, 240)])
        self.measurement_labels = [
            MeasurementLabel(1, "voltage", "Vin", (160, 240), None),
            MeasurementLabel(2, "voltage", "Vout", (300, 240), None),
        ]
        self.next_label_id = 3
        self.refresh_label_list()
        self.tstop_field.set_value(0.001, silent=True)
        self.dt_field.set_value(0.000001, silent=True)
        self.fstart_field.set_value(10, silent=True)
        self.fstop_field.set_value(1000000, silent=True)
        self.ppd_field.set_value(50, silent=True)
        self.probe_point = (300, 240)
        self.probe_element = None
        self.redraw()
        self.update_properties()
        self.set_tool("select")
        self.set_example_button_state("rc")
        self.status_metric.set("RC low-pass 예제: 5 kHz sine 입력, Vin/Vout label 포함.", theme.ACCENT)
        self.write_results("예제: VAC1=1 V, 5 kHz / R1=10 kΩ / C1=10 nF\nTransient: Vin과 Vout sine overlay\nAC Sweep: fc ≈ 1.59 kHz")

    def save_json(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Circuit JSON", "*.json")])
        if not path:
            return
        payload = {
            "model": self.model.to_dict(),
            "measurement_labels": [label.to_dict() for label in self.measurement_labels],
            "next_label_id": self.next_label_id,
            "ac_settings": {
                "f_start": self.fstart_field.get_float(10.0, positive=True),
                "f_stop": self.fstop_field.get_float(1000000.0, positive=True),
                "points_per_decade": self.ppd_field.get_float(50, positive=True),
            },
            "transient_settings": {
                "t_stop": self.tstop_field.get_float(0.01, positive=True),
                "dt": self.dt_field.get_float(0.00005, positive=True),
            },
            "integration": self.integration_var.get(),
            "node_label_mode": self.node_label_mode,
            "show_node_labels": self.show_node_labels,
            "show_measurement_labels": self.show_measurement_labels,
            "probe_point": list(self.probe_point) if self.probe_point else None,
            "probe_element_id": self.probe_element.id if self.probe_element else None,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self.status_metric.set("회로를 저장했습니다.", theme.GOOD)

    def load_json(self):
        path = filedialog.askopenfilename(filetypes=[("Circuit JSON", "*.json")])
        if not path:
            return
        previous_snap = self.snapshot()
        applied = False
        try:
            # [4번] Loading JSON is undoable and keeps JSON backward compatibility.
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self.model.elements or self.model.wires:
                self.push_undo()
            applied = True
            if "model" in data:
                self.model.from_dict(data.get("model", {}))
                self.measurement_labels = [MeasurementLabel.from_dict(item) for item in data.get("measurement_labels", [])]
                self.next_label_id = int(data.get("next_label_id", max([label.id for label in self.measurement_labels], default=0) + 1))
                ac = data.get("ac_settings", {})
                if ac:
                    self.fstart_field.set_value(ac.get("f_start", 10.0), silent=True)
                    self.fstop_field.set_value(ac.get("f_stop", 1000000.0), silent=True)
                    self.ppd_field.set_value(ac.get("points_per_decade", 50), silent=True)
                tran = data.get("transient_settings", {})
                if tran:
                    self.tstop_field.set_value(tran.get("t_stop", 0.01), silent=True)
                    self.dt_field.set_value(tran.get("dt", 0.00005), silent=True)
                self.integration_var.set(data.get("integration", "BE"))
                self.node_label_mode = data.get("node_label_mode", "compact")
                self.show_node_labels = bool(data.get("show_node_labels", self.node_label_mode != "off"))
                if not self.show_node_labels:
                    self.node_label_mode = "off"
                self.show_measurement_labels = bool(data.get("show_measurement_labels", True))
                probe_point = data.get("probe_point")
                probe_element_id = data.get("probe_element_id")
            else:
                self.model.from_dict(data)
                self.measurement_labels = []
                self.next_label_id = 1
                probe_point = None
                probe_element_id = None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if applied:
                self.restore_snapshot(previous_snap)
            self.status_metric.set(f"불러오기 실패: {exc}", theme.DANGER)
            return
        self.clear_interaction_state(clear_labels=False)
        self.probe_point = tuple(probe_point) if probe_point else None
        self.probe_element = self.element_by_id(probe_element_id) if probe_element_id is not None else None
        self.redo_stack.clear()
        self.set_example_button_state(None)
        self.set_tool("select")
        self.refresh_label_list()
        self.redraw()
        self.update_properties()
        self.status_metric.set("회로를 불러왔습니다.", theme.GOOD)
