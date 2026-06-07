import tkinter as tk

from . import theme
from .circuits.kelvin import KelvinPage
from .circuits.maxwell import MaxwellPage
from .circuits.rc import RCPage
from .circuits.transistor import TransistorPage
from .circuits.wheatstone import WheatstonePage
from .widgets import Panel, ScrollableFrame, Toast, font, make_button


CARD_DATA = [
    (
        "wheatstone",
        "Wheatstone 브리지",
        "미지 저항을 평형 조건으로 찾는 기본 브리지 실습입니다.",
        theme.ACCENT,
    ),
    (
        "maxwell",
        "Maxwell 브리지",
        "미지 코일의 저항과 인덕턴스를 복소 평형으로 맞춰봅니다.",
        theme.ACCENT_2,
    ),
    (
        "kelvin",
        "Kelvin 더블 브리지",
        "낮은 저항에서 연결 저항의 영향을 줄이며 평형점을 찾습니다.",
        theme.ACCENT_3,
    ),
    (
        "transistor",
        "트랜지스터",
        "NPN 공통 이미터 회로에서 바이어스, 스위치 ON/OFF, β 관찰을 실습합니다.",
        theme.BLUE_2,
    ),
    (
        "rc",
        "RC 회로",
        "저항과 커패시터의 충전·방전 응답을 그래프와 함께 관찰하는 실습입니다.",
        theme.RC_GREEN,
    ),
    (
        "pspice_lite",
        "PSpice Lite",
        "기본 소자를 배치하고 노드 전압/소자 전류를 간단히 확인합니다.",
        theme.BLUE,
    ),
]


class BridgeCard(Panel):
    def __init__(self, parent, key, title, desc, color, command):
        super().__init__(parent, bg=theme.SURFACE)
        self.key = key
        self.color = color
        self.command = command
        self.configure(cursor="hand2", highlightbackground=theme.LINE)
        body = tk.Frame(self, bg=theme.SURFACE)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(body, text=title, bg=theme.SURFACE, fg=theme.TEXT, font=font(14, "bold")).pack(pady=(4, 6))
        tk.Label(body, text=desc, bg=theme.SURFACE, fg=theme.MUTED, font=font(9), wraplength=260, justify="center").pack()
        self.icon = tk.Canvas(body, height=82, bg=theme.SURFACE_2, highlightthickness=1, highlightbackground="#35423c", bd=0)
        self.icon.pack(fill="x", pady=(16, 10))
        for widget in (self, body, self.icon):
            widget.bind("<Button-1>", lambda _e: self.command(self.key))
            widget.bind("<Enter>", self._hover_on)
            widget.bind("<Leave>", self._hover_off)
        for child in body.winfo_children():
            child.bind("<Button-1>", lambda _e: self.command(self.key))
            child.bind("<Enter>", self._hover_on)
            child.bind("<Leave>", self._hover_off)
        self.icon.bind("<Configure>", lambda _e: self.draw_icon())

    def _hover_on(self, _event=None):
        self.configure(highlightbackground=self.color)
        self.icon.configure(highlightbackground=self.color)

    def _hover_off(self, _event=None):
        self.configure(highlightbackground=theme.LINE)
        self.icon.configure(highlightbackground="#35423c")

    def draw_icon(self):
        c = self.icon
        c.delete("all")
        w = max(1, c.winfo_width())
        sx = w / 240
        sy = c.winfo_height() / 80

        def p(x, y):
            return x * sx, y * sy

        def line(*coords, fill=theme.TEXT, width=4, **kwargs):
            pts = []
            for i in range(0, len(coords), 2):
                pts.extend(p(coords[i], coords[i + 1]))
            c.create_line(*pts, fill=fill, width=width, capstyle="round", joinstyle="round", **kwargs)

        if self.key == "wheatstone":
            line(20, 40, 62, 40, fill=self.color, width=5)
            line(178, 40, 220, 40, fill=self.color, width=5)
            line(62, 40, 120, 12, 178, 40, 120, 68, 62, 40, fill=self.color, width=5)
            c.create_oval(*p(107, 27), *p(133, 53), outline=theme.ACCENT_2, width=4)
            line(92, 26, 82, 34, 92, 42, 82, 50, 92, 58, fill=theme.TEXT, width=4)
            line(148, 26, 158, 34, 148, 42, 158, 50, 148, 58, fill=theme.TEXT, width=4)
        elif self.key == "maxwell":
            line(26, 16, 214, 16, fill=self.color, width=5)
            line(26, 64, 214, 64, fill=self.color, width=5)
            line(66, 16, 66, 64, fill=self.color, width=5)
            line(174, 16, 174, 64, fill=self.color, width=5)
            line(66, 40, 174, 40, fill=self.color, width=5)
            line(98, 16, 86, 16, 86, 26, 98, 26, 110, 26, 110, 36, 98, 36, fill=theme.TEXT, width=4, smooth=True)
            line(174, 46, 154, 46, fill=theme.ACCENT_3, width=4)
            line(174, 56, 154, 56, fill=theme.ACCENT_3, width=4)
            c.create_oval(*p(108, 28), *p(132, 52), outline=theme.ACCENT, width=4)
        elif self.key == "kelvin":
            line(24, 16, 216, 16, 216, 64, 24, 64, 24, 16, fill=self.color, width=5)
            line(70, 16, 70, 64, fill=self.color, width=5)
            line(170, 16, 170, 64, fill=self.color, width=5)
            line(70, 40, 170, 40, fill=self.color, width=5)
            line(96, 16, 104, 24, 96, 32, 104, 40, 96, 48, 104, 56, 96, 64, fill=theme.TEXT, width=4)
            line(144, 16, 136, 24, 144, 32, 136, 40, 144, 48, 136, 56, 144, 64, fill=theme.TEXT, width=4)
            c.create_oval(*p(108, 28), *p(132, 52), outline=theme.ACCENT, width=4)
        elif self.key == "transistor":
            line(26, 16, 214, 16, fill=self.color, width=5)
            line(184, 16, 184, 64, fill=theme.TEXT, width=5)
            line(60, 40, 108, 40, fill=theme.TEXT, width=5)
            c.create_oval(*p(116, 24), *p(148, 56), outline=self.color, width=4)
            line(116, 40, 132, 40, 152, 24, fill=theme.TEXT, width=4)
            line(132, 40, 154, 58, fill=theme.TEXT, width=4)
            line(146, 52, 156, 58, 149, 44, fill=theme.ACCENT_2, width=4)
        elif self.key == "pspice_lite":
            line(18, 18, 222, 18, fill=self.color, width=4)
            line(18, 62, 222, 62, fill=self.color, width=4)
            line(58, 18, 58, 62, fill=theme.TEXT, width=4)
            line(58, 40, 96, 40, fill=theme.TEXT, width=4)
            line(96, 40, 106, 28, 118, 52, 130, 28, 142, 52, 154, 40, fill=theme.ACCENT_3, width=4)
            line(154, 40, 182, 40, fill=theme.TEXT, width=4)
            c.create_oval(*p(182, 24), *p(214, 56), outline=theme.BLUE_2, width=4)
            c.create_text(*p(198, 40), text="V", fill=theme.TEXT_2, font=font(18, "bold"))
        else:
            line(18, 40, 66, 40, fill=theme.TEXT, width=5)
            line(66, 40, 76, 28, 88, 52, 100, 28, 112, 52, 124, 28, 136, 40, fill=self.color, width=4)
            line(136, 40, 162, 40, fill=theme.TEXT, width=5)
            line(162, 22, 162, 58, fill=theme.BLUE_2, width=4)
            line(178, 22, 178, 58, fill=theme.BLUE_2, width=4)
            line(178, 40, 222, 40, fill=theme.TEXT, width=5)


class MenuFrame(tk.Frame):
    def __init__(self, parent, open_callback):
        super().__init__(parent, bg=theme.BG)
        self.open_callback = open_callback
        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)
        root = self.scroll.inner
        self.intro = tk.Frame(root, bg=theme.BG)
        self.intro.pack(fill="x", padx=28, pady=(28, 16))
        tk.Label(self.intro, text="실습할 회로를 선택하세요", bg=theme.BG, fg=theme.TEXT, font=font(22, "bold")).pack(anchor="w")
        self.grid_frame = tk.Frame(root, bg=theme.BG)
        self.grid_frame.pack(fill="both", expand=True, padx=28, pady=(0, 28))
        self.cards = []
        for data in CARD_DATA:
            self.cards.append(BridgeCard(self.grid_frame, *data, command=self.open_callback))
        self.bind("<Configure>", self._layout_cards)
        self.after_idle(self._layout_cards)

    def _layout_cards(self, _event=None):
        width = max(1, self.winfo_width())
        cols = 3 if width >= 860 else 1
        for child in self.grid_frame.winfo_children():
            child.grid_forget()
        for i in range(3):
            self.grid_frame.columnconfigure(i, weight=1 if i < cols else 0, uniform="cards" if i < cols else "")
        for idx, card in enumerate(self.cards):
            card.grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=8, pady=8)


class BridgeLabApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Circuit Lab - 회로 실습")
        self.geometry("1280x800")
        self.minsize(1100, 720)
        self.configure(bg=theme.BG)
        self.current_page = None
        self.pages = {}
        self._topbar_compact = False
        self._build_topbar()
        self.content = tk.Frame(self, bg=theme.BG)
        self.content.pack(fill="both", expand=True)
        self.toast = Toast(self)
        self.menu = MenuFrame(self.content, self.show_circuit)
        self.menu.pack(fill="both", expand=True)
        self.bind("<Escape>", self._on_escape)

    def _build_topbar(self):
        self.topbar = tk.Frame(self, bg=theme.TOPBAR, height=theme.TOPBAR_HEIGHT, highlightbackground=theme.LINE, highlightthickness=1)
        self.topbar.pack_propagate(False)
        self.topbar.pack(fill="x")
        self.brand = tk.Frame(self.topbar, bg=theme.TOPBAR)
        self.brand.pack(side="left", fill="x", expand=True, padx=22, pady=8)
        self.brand_label = tk.Label(self.brand, text="Circuit Lab", bg=theme.TOPBAR, fg=theme.ACCENT, font=font(9, "bold"))
        self.brand_label.pack(anchor="w")
        self.title_label = tk.Label(self.brand, text="회로 실습", bg=theme.TOPBAR, fg=theme.TEXT, font=font(18, "bold"))
        self.title_label.pack(anchor="w")
        self.back_btn = make_button(self.topbar, "뒤로가기", self.show_menu, "secondary")

    def set_topbar_compact(self, compact):
        if compact == self._topbar_compact:
            return
        self._topbar_compact = compact
        if compact:
            self.topbar.configure(height=theme.TOPBAR_COMPACT_HEIGHT, highlightbackground=theme.BORDER)
            self.brand.pack_configure(padx=18, pady=5)
            self.brand_label.pack_forget()
            self.title_label.configure(font=font(13, "bold"))
            if self.back_btn.winfo_ismapped():
                self.back_btn.pack_configure(padx=16, pady=7)
        else:
            self.topbar.configure(height=theme.TOPBAR_HEIGHT, highlightbackground=theme.LINE)
            self.brand.pack_configure(padx=22, pady=8)
            if not self.brand_label.winfo_ismapped():
                self.brand_label.pack(anchor="w", before=self.title_label)
            self.title_label.configure(font=font(18, "bold"))
            if self.back_btn.winfo_ismapped():
                self.back_btn.pack_configure(padx=22, pady=10)

    def _handle_page_scroll(self, fraction):
        if self.current_page:
            self.set_topbar_compact(fraction > 0.015)

    def _get_page(self, key):
        if key not in self.pages:
            if key == "pspice_lite":
                from .circuits.pspice_lite import PSpiceLitePage

                cls = PSpiceLitePage
            else:
                cls = {
                    "wheatstone": WheatstonePage,
                    "maxwell": MaxwellPage,
                    "kelvin": KelvinPage,
                    "transistor": TransistorPage,
                    "rc": RCPage,
                }[key]
            page = cls(self.content, toast=self.toast)
            if hasattr(page, "scroll"):
                page.scroll.set_on_scroll(self._handle_page_scroll)
            self.pages[key] = page
        return self.pages[key]

    def show_circuit(self, key):
        if self.current_page:
            self.current_page.on_hide()
            self.current_page.pack_forget()
        self.menu.pack_forget()
        page = self._get_page(key)
        page.pack(fill="both", expand=True)
        self.current_page = page
        self.title_label.configure(text=getattr(page, "title", "회로 실습"))
        self.back_btn.pack(side="right", padx=22, pady=10)
        self.set_topbar_compact(False)
        page.on_show()
        self.toast.show(f"{self.title_label.cget('text')} 화면을 열었습니다.")

    def _on_escape(self, _event=None):
        if not self.current_page:
            return None
        if hasattr(self.current_page, "handle_escape") and self.current_page.handle_escape():
            return "break"
        self.show_menu()
        return "break"

    def show_menu(self):
        if self.current_page:
            self.current_page.on_hide()
            self.current_page.pack_forget()
            self.current_page = None
        self.title_label.configure(text="회로 실습")
        self.back_btn.pack_forget()
        self.set_topbar_compact(False)
        self.menu.pack(fill="both", expand=True)
        self.toast.show("회로 선택 화면으로 돌아왔습니다.")
