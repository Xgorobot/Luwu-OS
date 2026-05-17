#!/usr/bin/env python3
"""
Luwu OS Settings App (PySide6)
Settings list with 5 items: SN, Volume, Language, Contact Us, App Download
Launched by the Luwu launcher via FIFO/preload mechanism.
"""
import os
import sys
import json
import uuid
import time
import datetime
import signal
import io
from pathlib import Path

import barcode
from barcode.writer import ImageWriter
from PIL import Image

from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QFont, QKeyEvent, QPixmap, QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QStackedWidget,
)

# ---- Paths ----
APP_DIR = Path(__file__).resolve().parent
PICS_DIR = APP_DIR / "pics"
# 全局唯一语言配置（统一接入 libs/i18n）
LUWU_ROOT = Path("/home/pi/luwu-os")
LANGUAGE_INI = LUWU_ROOT / "configs" / "language.ini"
VOLUME_INI = APP_DIR / "volume.ini"
CN_LA = APP_DIR / "cn.la"
EN_LA = APP_DIR / "en.la"

# 接入全局 i18n 库
if str(LUWU_ROOT) not in sys.path:
    sys.path.insert(0, str(LUWU_ROOT))
try:
    from libs.i18n import get_lang as _i18n_get_lang, set_lang as _i18n_set_lang
except Exception:
    _i18n_get_lang = None
    _i18n_set_lang = None

# ---- Language helpers ----
def get_lang_code():
    """读取全局语言代码：'cn' 或 'en'。"""
    if _i18n_get_lang:
        try:
            return _i18n_get_lang()
        except Exception:
            pass
    try:
        with open(LANGUAGE_INI, "r") as f:
            return f.read().strip() or "cn"
    except Exception:
        return "cn"

def set_lang_code(code: str) -> bool:
    """写入全局语言代码（重启后生效）。"""
    if _i18n_set_lang:
        try:
            return _i18n_set_lang(code)
        except Exception:
            pass
    try:
        LANGUAGE_INI.parent.mkdir(parents=True, exist_ok=True)
        with open(LANGUAGE_INI, "w") as f:
            f.write(code)
        return True
    except Exception:
        return False

def load_language():
    """按当前语言加载 settings 自带的翻译 JSON（cn.la / en.la）。"""
    lang = get_lang_code()
    la_path = CN_LA if lang == "cn" else EN_LA
    try:
        with open(la_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

# ---- SN helpers ----
def get_sn_short():
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":")[1].strip().upper()[-8:]
    except Exception:
        return ""
    return ""

def get_mac_address():
    mac = uuid.getnode()
    return ''.join(['{:02x}'.format((mac >> i) & 0xff) for i in reversed(range(0, 48, 8))]).upper()

# ---- Volume helpers ----
def read_volume():
    try:
        with open(VOLUME_INI, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 50

def write_volume(vol):
    with open(VOLUME_INI, "w") as f:
        f.write(str(vol))
    # Try pactl first, fallback to amixer
    if os.system("which pactl > /dev/null 2>&1") == 0:
        os.system("pactl set-sink-volume @DEFAULT_SINK@ " + str(vol) + "%")
    else:
        os.system("amixer set Playback " + str(vol) + "% > /dev/null 2>&1")

# ---- System info helpers ----
def get_cpu_model():
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Model"):
                    return line.split(":")[1].strip()
    except Exception:
        return "Unknown"
    return "Unknown"

def get_cpu_cores():
    return os.cpu_count() or 0

def get_ram_total():
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split(":")[1].strip().split()[0])
                    gb = kb / (1024 * 1024)
                    if gb >= 1:
                        return f"{gb:.1f} GB"
                    return f"{kb // 1024} MB"
    except Exception:
        return "Unknown"
    return "Unknown"

def get_ram_available():
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable"):
                    kb = int(line.split(":")[1].strip().split()[0])
                    gb = kb / (1024 * 1024)
                    if gb >= 1:
                        return f"{gb:.1f} GB"
                    return f"{kb // 1024} MB"
    except Exception:
        return "Unknown"
    return "Unknown"

def get_disk_info():
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        def fmt(size):
            gb = size / (1024**3)
            if gb >= 1:
                return f"{gb:.1f} GB"
            return f"{size // (1024**2)} MB"
        return f"{fmt(total)} | 用{fmt(used)} | 剩{fmt(free)}"
    except Exception:
        return "Unknown"

def _get_pkg_version(pkg_name: str) -> str:
    """使用 importlib.metadata 直接读取已安装包的版本号（毫秒级，无子进程）。
    避免在主线程调用 `pip show` 造成 1~2 秒阻塞，导致打开 settings 时屏幕黑一半。
    """
    try:
        try:
            from importlib.metadata import version, PackageNotFoundError  # py3.8+
        except Exception:
            from importlib_metadata import version, PackageNotFoundError  # type: ignore
        try:
            return version(pkg_name)
        except PackageNotFoundError:
            return "未安装"
    except Exception:
        return "未知"

def get_xgolib_version():
    return _get_pkg_version("xgolib")

def get_xgoedu_version():
    return _get_pkg_version("xgoedu-luwuos")

# ---- Color constants ----
COLOR_BG = QColor(15, 21, 48)
COLOR_CARD = QColor(25, 32, 65)
COLOR_SELECT = QColor(100, 80, 220)
COLOR_WHITE = QColor(255, 255, 255)
COLOR_GRAY = QColor(140, 145, 180)
COLOR_PURPLE = QColor(120, 100, 240)
COLOR_UNSELECT = QColor(50, 55, 80)
COLOR_GREEN = QColor(0, 229, 255)

# ============================================================================
# Setting Item Data
# ============================================================================
SETTING_ITEMS = [
    {"id": "about",       "icon": "icon_sn.png",       "label_key": "ABOUT"},
    {"id": "sn",          "icon": "icon_sn.png",       "label_key": "SN"},
    {"id": "volume",      "icon": "volume.png",        "label_key": "VOLUME"},
    {"id": "language",    "icon": "language.png",      "label_key": "LANGUAGE"},
    {"id": "contact_us",  "icon": "qrcode.png",        "label_key": "CONTACT"},
    {"id": "app_download","icon": "app_download.png",  "label_key": "APPDOWN"},
    {"id": "time",        "icon": "icon_sn.png",       "label_key": "TIME"},
    {"id": "shutdown",    "icon": "power.png",         "label_key": "SHUTDOWN"},
    {"id": "reboot",      "icon": "power.png",         "label_key": "REBOOT"},
]

# ============================================================================
# SettingsListPage
# ============================================================================
class SettingsListPage(QWidget):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.selected_idx = 0
        self.scroll_offset = 0
        self.la = load_language()

        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_tl = QLabel("A:上移", self)
        self.corner_tl.setStyleSheet(hint_style)
        self.corner_tr = QLabel("B:下移", self)
        self.corner_tr.setStyleSheet(hint_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel("C:返回", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("D:进入", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        # Item widgets
        self.item_widgets = []
        for i, item in enumerate(SETTING_ITEMS):
            w = self._make_item_widget(item, i)
            self.item_widgets.append(w)

        self.update_selection()

    def _make_item_widget(self, item, index):
        """Create a container widget for a settings list item."""
        container = QWidget(self)
        container.setFixedSize(250, 26)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 1, 6, 1)
        layout.setSpacing(6)

        # Icon
        icon_label = QLabel()
        icon_path = str(PICS_DIR / item["icon"])
        pix = QPixmap(icon_path)
        if not pix.isNull():
            pix = pix.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            icon_label.setPixmap(pix)
        icon_label.setFixedSize(20, 20)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("background: transparent;")
        icon_label.setObjectName(f"icon_{index}")
        layout.addWidget(icon_label)

        # Label
        label_key = item["label_key"]
        text = self.la.get("DEMOEN", {}).get(label_key, label_key)
        text_label = QLabel(text)
        text_font = QFont()
        text_font.setPointSize(11)
        text_label.setFont(text_font)
        text_label.setStyleSheet("color: #cccccc; background: transparent;")
        text_label.setObjectName(f"text_{index}")
        layout.addWidget(text_label)

        layout.addStretch()

        # Arrow indicator
        arrow = QLabel(">")
        arrow_font = QFont()
        arrow_font.setPointSize(11)
        arrow.setFont(arrow_font)
        arrow.setStyleSheet("color: #555; background: transparent;")
        arrow.setObjectName(f"arrow_{index}")
        layout.addWidget(arrow)

        container.setObjectName(f"item_{index}")
        container.setStyleSheet(f"#item_{index} {{ background-color: {COLOR_CARD.name()}; border-radius: 6px; }}")
        return container

    def update_selection(self):
        """Update visual styles based on selected index."""
        for i, w in enumerate(self.item_widgets):
            sel = (i == self.selected_idx)
            self._update_item_style(w, i, sel)

    def _update_item_style(self, container, idx, selected):
        if selected:
            container.setStyleSheet(
                f"#item_{idx} {{ background-color: {COLOR_SELECT.name()}; border-radius: 6px; }}"
            )
            text_label = container.findChild(QLabel, f"text_{idx}")
            if text_label:
                text_label.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: bold; background: transparent;")
            arrow = container.findChild(QLabel, f"arrow_{idx}")
            if arrow:
                arrow.setStyleSheet("color: #ffffff; font-size: 11px; background: transparent;")
        else:
            container.setStyleSheet(
                f"#item_{idx} {{ background-color: {COLOR_CARD.name()}; border-radius: 6px; }}"
            )
            text_label = container.findChild(QLabel, f"text_{idx}")
            if text_label:
                text_label.setStyleSheet("color: #cccccc; font-size: 11px; background: transparent;")
            arrow = container.findChild(QLabel, f"arrow_{idx}")
            if arrow:
                arrow.setStyleSheet("color: #555555; font-size: 11px; background: transparent;")

    def move_selection(self, delta):
        new_idx = self.selected_idx + delta
        total = len(SETTING_ITEMS)
        if new_idx < 0:
            new_idx = 0
        if new_idx >= total:
            new_idx = total - 1
        if new_idx == self.selected_idx:
            return
        self.selected_idx = new_idx

        # Adjust scroll offset to keep selected item visible
        visible_count = self._visible_count()
        if self.selected_idx < self.scroll_offset:
            self.scroll_offset = self.selected_idx
        elif self.selected_idx >= self.scroll_offset + visible_count:
            self.scroll_offset = self.selected_idx - visible_count + 1

        self._relayout_items()
        self.update_selection()

    def _visible_count(self):
        """How many items fit in the list area."""
        h = self.height()
        if h == 0:
            return len(SETTING_ITEMS)
        top_margin = 28   # reserve for top corner hints
        bottom_margin = 28
        avail = h - top_margin - bottom_margin
        item_h = 26
        gap = 4
        count = (avail + gap) // (item_h + gap)
        return max(1, min(count, len(SETTING_ITEMS)))

    def _relayout_items(self):
        """Position visible items, hide others."""
        w = self.width()
        if w == 0:
            return
        h = self.height()
        top_margin = 28
        item_h = 26
        gap = 4
        visible_count = self._visible_count()
        x = (w - 250) // 2

        for i, item_w in enumerate(self.item_widgets):
            if self.scroll_offset <= i < self.scroll_offset + visible_count:
                rel = i - self.scroll_offset
                y = top_margin + rel * (item_h + gap)
                item_w.setGeometry(x, y, 250, item_h)
                item_w.show()
            else:
                item_w.hide()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()

        # Keep selection in visible range after resize
        visible_count = self._visible_count()
        if self.selected_idx < self.scroll_offset:
            self.scroll_offset = self.selected_idx
        elif self.selected_idx >= self.scroll_offset + visible_count:
            self.scroll_offset = max(0, self.selected_idx - visible_count + 1)

        self._relayout_items()

        # Corner hints
        pad = 12
        self.corner_tl.move(pad, pad)
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Up or ev.key() == Qt.Key.Key_Left:
            self.move_selection(-1)
        elif ev.key() == Qt.Key.Key_Down or ev.key() == Qt.Key.Key_Right:
            self.move_selection(1)
        elif ev.key() == Qt.Key.Key_Return:
            item = SETTING_ITEMS[self.selected_idx]
            self.stack.navigate_to(item["id"])
        elif ev.key() == Qt.Key.Key_Back:
            QApplication.instance().quit()

    def refresh_language(self):
        self.la = load_language()
        self.update_selection()


# ============================================================================
# About Page (关于本机)
# ============================================================================
class AboutPage(QWidget):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.la = load_language()
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.scroll_offset = 0
        self.visible_count = 1

        # 懒加载：__init__ 时不采集信息，避免 settings 启动时被 AboutPage 拖慢。
        # info_items / info_labels 在 on_enter() 首次进入页面时才生成。
        self.info_items = []
        self.info_labels = []
        self._info_loaded = False

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_tl = QLabel("A:上翻", self)
        self.corner_tl.setStyleSheet(hint_style)
        self.corner_tr = QLabel("B:下翻", self)
        self.corner_tr.setStyleSheet(hint_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel("C:返回", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("D:退出", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        # 加载中占位
        self.loading_label = QLabel("加载中…", self)
        loading_font = QFont()
        loading_font.setPointSize(11)
        self.loading_label.setFont(loading_font)
        self.loading_label.setStyleSheet("color: #8892c9; background: transparent;")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _gather_info(self):
        items = []
        items.append(("LuwuOS 版本", "2.0.0"))
        items.append(("xgolib 版本", get_xgolib_version()))
        items.append(("xgoedu 版本", get_xgoedu_version()))
        items.append(("CPU", get_cpu_model()))
        items.append(("CPU 核心数", str(get_cpu_cores())))
        items.append(("内存总量", get_ram_total()))
        items.append(("可用内存", get_ram_available()))
        items.append(("硬盘", get_disk_info()))
        items.append(("SN", f"{get_sn_short()}{get_mac_address()}"))
        return items

    def _build_info_labels(self):
        """首次进入 about 页时才采集信息并创建 labels。"""
        # 清理旧 label（多次重入也能刷新）
        for lbl in self.info_labels:
            lbl.setParent(None)
            lbl.deleteLater()
        self.info_labels = []

        self.info_items = self._gather_info()
        for key, value in self.info_items:
            lbl = QLabel(f"{key}:  {value}", self)
            lbl_font = QFont()
            lbl_font.setPointSize(9)
            lbl.setFont(lbl_font)
            lbl.setStyleSheet("color: #c0c8e0; background: transparent; padding: 0px 6px;")
            lbl.setWordWrap(False)
            self.info_labels.append(lbl)
        self._info_loaded = True

    def on_enter(self):
        """被 SettingsStack.navigate_to('about') 调用，首次进入时采集信息。"""
        if self._info_loaded:
            return
        # 先显示“加载中”占位，下一个事件循环再采集信息，避免首帧被阻塞
        self.loading_label.show()
        self.loading_label.raise_()
        QTimer.singleShot(0, self._do_load_info)

    def _do_load_info(self):
        self._build_info_labels()
        self.loading_label.hide()
        self._relayout_items()
        self.update()

    def refresh_language(self):
        self.la = load_language()

    def _visible_count(self):
        h = self.height()
        if h == 0 or not self.info_items:
            return 1
        top_margin = 28
        bottom_margin = 32
        avail = h - top_margin - bottom_margin
        item_h = 22
        gap = 2
        count = (avail + gap) // (item_h + gap)
        return max(1, min(count, len(self.info_items)))

    def _relayout_items(self):
        w = self.width()
        if w == 0:
            return
        h = self.height()
        top_margin = 28
        item_h = 22
        gap = 2
        self.visible_count = self._visible_count()
        x = 20

        for i, lbl in enumerate(self.info_labels):
            if self.scroll_offset <= i < self.scroll_offset + self.visible_count:
                rel = i - self.scroll_offset
                y = top_margin + rel * (item_h + gap)
                lbl.setGeometry(x, y, w - 40, item_h)
                lbl.show()
            else:
                lbl.hide()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()

        # Keep selection in visible range after resize
        self.visible_count = self._visible_count()
        if self.info_items and self.scroll_offset > len(self.info_items) - self.visible_count:
            self.scroll_offset = max(0, len(self.info_items) - self.visible_count)

        self._relayout_items()

        # Loading label 居中
        if hasattr(self, "loading_label"):
            self.loading_label.setGeometry(0, h // 2 - 15, w, 30)

        pad = 12
        self.corner_tl.move(pad, pad)
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Up or ev.key() == Qt.Key.Key_Left:
            if self.scroll_offset > 0:
                self.scroll_offset -= 1
                self._relayout_items()
        elif ev.key() == Qt.Key.Key_Down or ev.key() == Qt.Key.Key_Right:
            if self.scroll_offset < len(self.info_items) - self.visible_count:
                self.scroll_offset += 1
                self._relayout_items()
        elif ev.key() == Qt.Key.Key_Back:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            QApplication.instance().quit()


# ============================================================================
# SN Page
# ============================================================================
class SNPage(QWidget):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.la = load_language()

        # SN display
        self.sn_id = get_sn_short() + get_mac_address()
        full_sn = f"SN: {self.sn_id}"
        self.sn_label = QLabel(full_sn, self)
        sn_font = QFont()
        sn_font.setPointSize(14)
        self.sn_label.setFont(sn_font)
        self.sn_label.setStyleSheet("color: #00E5FF; background: transparent;")
        self.sn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Barcode display
        self.barcode_label = QLabel(self)
        self.barcode_label.setStyleSheet("background: transparent;")
        self.barcode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._generate_barcode()

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_bl = QLabel("Back", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("Exit", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

    def _generate_barcode(self):
        try:
            code128 = barcode.get("code128", self.sn_id, writer=ImageWriter())
            buf = io.BytesIO()
            # Disable text below bars since we already show SN text
            code128.write(buf, options={"write_text": False})
            buf.seek(0)
            img = Image.open(buf)
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            pixmap = QPixmap(img.width, img.height)
            # Convert image to QPixmap via QImage
            from PySide6.QtGui import QImage
            qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
            pixmap = QPixmap.fromImage(qimg)
            self.barcode_label.setPixmap(pixmap)
            self.barcode_label.setFixedSize(img.width, img.height)
        except Exception as e:
            self.barcode_label.setText(f"[Barcode Error: {e}]")
            self.barcode_label.setStyleSheet("color: #ff5555; background: transparent;")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        # SN text at top area
        self.sn_label.setGeometry(0, h // 4 - 20, w, 30)
        # Barcode centered
        bw = self.barcode_label.width()
        bh = self.barcode_label.height()
        if bw > w - 40:
            # Scale down if too wide
            scaled_w = w - 40
            scaled_h = int(bh * scaled_w / bw)
            self.barcode_label.setFixedSize(scaled_w, scaled_h)
            self.barcode_label.setScaledContents(True)
        self.barcode_label.move((w - self.barcode_label.width()) // 2, h // 2 - self.barcode_label.height() // 2)
        pad = 12
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back or ev.key() == Qt.Key.Key_Left:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            QApplication.instance().quit()


# ============================================================================
# Volume Page
# ============================================================================
class VolumePage(QWidget):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.volume = read_volume()
        self.la = load_language()
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Title
        self.title_label = QLabel("Volume", self)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #ffffff; background: transparent;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Volume percent label
        self.percent_label = QLabel(f"{self.volume}%", self)
        pct_font = QFont()
        pct_font.setPointSize(20)
        pct_font.setBold(True)
        self.percent_label.setFont(pct_font)
        self.percent_label.setStyleSheet("color: #00E5FF; background: transparent;")
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Saved hint (hidden by default)
        self.saved_label = QLabel("", self)
        saved_font = QFont()
        saved_font.setPointSize(12)
        self.saved_label.setFont(saved_font)
        self.saved_label.setStyleSheet("color: #00E5FF; background: transparent;")
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.saved_label.hide()

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_tl = QLabel("-5%", self)
        self.corner_tl.setStyleSheet(hint_style)
        self.corner_tr = QLabel("+5%", self)
        self.corner_tr.setStyleSheet(hint_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel("Save", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("Exit", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Volume bar background
        bar_x, bar_y = 40, h // 2 + 10
        bar_w, bar_h = w - 80, 20
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_UNSELECT)
        painter.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 6, 6)

        # Volume bar fill
        fill_w = int(bar_w * self.volume / 100)
        if fill_w > 0:
            painter.setBrush(COLOR_PURPLE)
            painter.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, 6, 6)

        # Tick marks
        painter.setPen(QPen(QColor(80, 85, 120), 1))
        for i in range(0, 101, 25):
            tx = bar_x + int(bar_w * i / 100)
            painter.drawLine(tx, bar_y, tx, bar_y + bar_h)

        painter.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.title_label.setGeometry(0, 30, w, 30)
        self.percent_label.setGeometry(0, h // 2 - 40, w, 30)
        self.saved_label.setGeometry(0, h - 60, w, 25)
        pad = 12
        self.corner_tl.move(pad, pad)
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def update_volume_display(self):
        self.percent_label.setText(f"{self.volume}%")
        self.update()

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Left:
            if self.volume > 0:
                self.volume = max(0, self.volume - 5)
                self.update_volume_display()
        elif ev.key() == Qt.Key.Key_Right:
            if self.volume < 100:
                self.volume = min(100, self.volume + 5)
                self.update_volume_display()
        elif ev.key() == Qt.Key.Key_Back:
            # Save and go back
            write_volume(self.volume)
            saved_text = self.la.get("VOLUME", {}).get("SAVED", "Saved!")
            self.saved_label.setText(saved_text)
            self.saved_label.show()
            QTimer.singleShot(800, lambda: self.stack.navigate_to("list"))
        elif ev.key() == Qt.Key.Key_Return:
            QApplication.instance().quit()


# ============================================================================
# Language Page
# ============================================================================
class LanguagePage(QWidget):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.content = get_lang_code()
        self.la = load_language()
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Title
        self.title_label = QLabel("Language", self)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #ffffff; background: transparent;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Option buttons (drawn manually)
        self.cn_selected = (self.content == "cn")
        self.en_selected = (self.content == "en")

        # Saved hint
        self.saved_label = QLabel("", self)
        saved_font = QFont()
        saved_font.setPointSize(12)
        self.saved_label.setFont(saved_font)
        self.saved_label.setStyleSheet("color: #00E5FF; background: transparent;")
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.saved_label.hide()

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_tl = QLabel("CN", self)
        self.corner_tl.setStyleSheet(hint_style)
        self.corner_tr = QLabel("EN", self)
        self.corner_tr.setStyleSheet(hint_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel("Save", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("Exit", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        btn_w, btn_h = 100, 50
        total_w = btn_w * 2 + 20
        start_x = (w - total_w) // 2
        btn_y = h // 2 - btn_h // 2

        cn_font = QFont()
        cn_font.setPointSize(16)
        cn_font.setBold(True)
        en_font = QFont()
        en_font.setPointSize(16)
        en_font.setBold(True)

        # CN button
        if self.cn_selected:
            painter.setBrush(COLOR_PURPLE)
        else:
            painter.setBrush(COLOR_UNSELECT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(start_x, btn_y, btn_w, btn_h, 10, 10)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(cn_font)
        painter.drawText(QRect(start_x, btn_y, btn_w, btn_h), Qt.AlignmentFlag.AlignCenter, "CN")

        # EN button
        en_x = start_x + btn_w + 20
        if self.en_selected:
            painter.setBrush(COLOR_PURPLE)
        else:
            painter.setBrush(COLOR_UNSELECT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(en_x, btn_y, btn_w, btn_h, 10, 10)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(en_font)
        painter.drawText(QRect(en_x, btn_y, btn_w, btn_h), Qt.AlignmentFlag.AlignCenter, "EN")

        painter.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.title_label.setGeometry(0, 30, w, 30)
        self.saved_label.setGeometry(0, h - 60, w, 25)
        pad = 12
        self.corner_tl.move(pad, pad)
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Left:
            self.content = "cn"
            self.cn_selected = True
            self.en_selected = False
            self.update()
        elif ev.key() == Qt.Key.Key_Right:
            self.content = "en"
            self.cn_selected = False
            self.en_selected = True
            self.update()
        elif ev.key() == Qt.Key.Key_Back:
            # Save language (写入全局 configs/language.ini) and restart
            set_lang_code(self.content)
            saved_text = self.la.get("LANGUAGE", {}).get("SAVED", "Saved!")
            self.saved_label.setText(saved_text)
            self.saved_label.show()
            QTimer.singleShot(1500, lambda: self._do_restart())
        elif ev.key() == Qt.Key.Key_Return:
            QApplication.instance().quit()

    def _do_restart(self):
        # Quit app; launcher will restart preload process automatically
        QApplication.instance().quit()


# ============================================================================
# QR Code Page (Contact Us / App Download)
# ============================================================================
class QRPage(QWidget):
    def __init__(self, stack: QStackedWidget, qr_image: str, email: str = None):
        super().__init__()
        self.stack = stack
        self.qr_image = qr_image
        self.email = email
        self.la = load_language()
        self.qr_pixmap = None
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Load QR image
        qr_path = str(PICS_DIR / qr_image)
        pix = QPixmap(qr_path)
        if not pix.isNull():
            self.qr_pixmap = pix

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_bl = QLabel("Back", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("Exit", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        if self.qr_pixmap and not self.qr_pixmap.isNull():
            max_size = min(w - 60, h - 100, 200)
            scaled = self.qr_pixmap.scaled(
                max_size, max_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            qr_x = (w - scaled.width()) // 2
            qr_y = (h - scaled.height() - 30) // 2
            painter.drawPixmap(qr_x, qr_y, scaled)

            # Email below QR
            if self.email:
                email_font = QFont()
                email_font.setPointSize(10)
                painter.setFont(email_font)
                painter.setPen(COLOR_GREEN)
                email_rect = QRect(0, qr_y + scaled.height() + 5, w, 20)
                painter.drawText(email_rect, Qt.AlignmentFlag.AlignCenter, self.email)

        painter.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 12
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back or ev.key() == Qt.Key.Key_Left:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            QApplication.instance().quit()


# ============================================================================
# Time / Date Page
# ============================================================================
class TimeDatePage(QWidget):
    COMMON_TIMEZONES = [
        "Asia/Shanghai",
        "Asia/Tokyo",
        "Asia/Seoul",
        "Asia/Singapore",
        "Asia/Kolkata",
        "Europe/London",
        "Europe/Berlin",
        "Europe/Paris",
        "America/New_York",
        "America/Los_Angeles",
        "America/Chicago",
        "Australia/Sydney",
    ]

    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.la = load_language()
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Get current timezone
        self._sync_timezone_from_system()

        # Time display (large)
        self.time_label = QLabel(self)
        time_font = QFont()
        time_font.setPointSize(30)
        time_font.setBold(True)
        self.time_label.setFont(time_font)
        self.time_label.setStyleSheet("color: #00E5FF; background: transparent;")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Date display
        self.date_label = QLabel(self)
        date_font = QFont()
        date_font.setPointSize(14)
        self.date_label.setFont(date_font)
        self.date_label.setStyleSheet("color: #8892c9; background: transparent;")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Saved hint
        self.saved_label = QLabel("", self)
        saved_font = QFont()
        saved_font.setPointSize(12)
        self.saved_label.setFont(saved_font)
        self.saved_label.setStyleSheet("color: #00E5FF; background: transparent;")
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.saved_label.hide()

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_tl = QLabel(self)
        self.corner_tl.setStyleSheet(hint_style)
        self.corner_tr = QLabel(self)
        self.corner_tr.setStyleSheet(hint_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel(self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel(self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._update_texts()
        self._update_clock()

        # Timer to refresh clock every second
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)

    def _sync_timezone_from_system(self):
        """Re-read system timezone (called on init and every page entry)."""
        self.current_tz = self._get_current_timezone()
        if self.current_tz not in self.COMMON_TIMEZONES:
            self.tz_list = self.COMMON_TIMEZONES + [self.current_tz]
        else:
            self.tz_list = list(self.COMMON_TIMEZONES)
        self.tz_index = self._find_tz_index()
        self.update()

    def _get_current_timezone(self):
        """Read current system timezone via timedatectl (most reliable)."""
        import subprocess
        try:
            result = subprocess.run(
                ["timedatectl", "show", "-p", "Timezone", "--value"],
                capture_output=True, text=True, timeout=5
            )
            tz = result.stdout.strip()
            if tz:
                return tz
        except Exception:
            pass
        # Fallback to /etc/timezone
        try:
            with open("/etc/timezone", "r") as f:
                return f.read().strip()
        except Exception:
            return "Asia/Shanghai"

    def _find_tz_index(self):
        try:
            return self.tz_list.index(self.current_tz)
        except ValueError:
            return 0

    def _update_clock(self):
        now = datetime.datetime.now()
        self.time_label.setText(now.strftime("%H:%M:%S"))
        weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekdays_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        lang = get_lang_code()
        if lang == "cn":
            wd = weekdays_cn[now.weekday()]
            self.date_label.setText(now.strftime(f"%Y-%m-%d {wd}"))
        else:
            wd = weekdays_en[now.weekday()]
            self.date_label.setText(now.strftime(f"%Y-%m-%d {wd}"))

    def _update_texts(self):
        self.la = load_language()
        t = self.la.get("DEMOEN", {})
        self.corner_tl.setText("A: ◀" + t.get("TIMEZONE", "TZ"))
        self.corner_tr.setText("B: " + t.get("TIMEZONE", "TZ") + "▶")
        self.corner_bl.setText("C: " + t.get("BACK", "Back"))
        self.corner_br.setText("D: " + t.get("CONFIRM", "Confirm"))
        self.update()

    def _current_tz_display(self):
        """Display name for the current timezone selection."""
        display_map = {
            "Asia/Shanghai":     "上海 UTC+8",
            "Asia/Tokyo":        "东京 UTC+9",
            "Asia/Seoul":        "首尔 UTC+9",
            "Asia/Singapore":    "新加坡 UTC+8",
            "Asia/Kolkata":      "印度 UTC+5:30",
            "Europe/London":     "伦敦 UTC+0",
            "Europe/Berlin":     "柏林 UTC+1",
            "Europe/Paris":      "巴黎 UTC+1",
            "America/New_York":  "纽约 UTC-5",
            "America/Los_Angeles": "洛杉矶 UTC-8",
            "America/Chicago":   "芝加哥 UTC-6",
            "Australia/Sydney":  "悉尼 UTC+10",
        }
        return display_map.get(self.current_tz, self.current_tz)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Timezone selector row
        tz_y = h // 2 + 20
        rect_w, rect_h = 220, 36
        rect_x = (w - rect_w) // 2

        # Background card
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_CARD)
        painter.drawRoundedRect(rect_x, tz_y, rect_w, rect_h, 10, 10)

        # Timezone text
        tz_font = QFont()
        tz_font.setPointSize(12)
        tz_font.setBold(True)
        painter.setFont(tz_font)
        painter.setPen(COLOR_GREEN)

        display = self._current_tz_display()
        painter.drawText(QRect(rect_x, tz_y, rect_w, rect_h), Qt.AlignmentFlag.AlignCenter, display)

        # Left / Right arrows on sides
        arrow_font = QFont()
        arrow_font.setPointSize(16)
        painter.setFont(arrow_font)
        painter.setPen(COLOR_GRAY)
        painter.drawText(QRect(rect_x - 30, tz_y, 30, rect_h), Qt.AlignmentFlag.AlignCenter, "◀")
        painter.drawText(QRect(rect_x + rect_w, tz_y, 30, rect_h), Qt.AlignmentFlag.AlignCenter, "▶")

        painter.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.time_label.setGeometry(0, h // 2 - 70, w, 40)
        self.date_label.setGeometry(0, h // 2 - 30, w, 25)
        self.saved_label.setGeometry(0, h - 60, w, 25)
        pad = 12
        self.corner_tl.move(pad, pad)
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Left or ev.key() == Qt.Key.Key_Up:
            self.tz_index = (self.tz_index - 1) % len(self.tz_list)
            self.current_tz = self.tz_list[self.tz_index]
            self.update()
        elif ev.key() == Qt.Key.Key_Right or ev.key() == Qt.Key.Key_Down:
            self.tz_index = (self.tz_index + 1) % len(self.tz_list)
            self.current_tz = self.tz_list[self.tz_index]
            self.update()
        elif ev.key() == Qt.Key.Key_Back:
            # Just go back, don't save
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            # Save timezone + sync time
            self._save_timezone()
            self._sync_time()
            self._update_clock()

    def _save_timezone(self):
        """Write timezone setting and refresh local clock."""
        os.system(f"echo pi | sudo -S timedatectl set-timezone {self.current_tz}")
        # Also sync /etc/timezone for consistency
        os.system(f"echo pi | sudo -S sh -c 'echo \"{self.current_tz}\" > /etc/timezone'")
        time.tzset()  # refresh Python's timezone cache

    def _sync_time(self):
        """Force NTP time sync and show result."""
        t = self.la.get("DEMOEN", {})
        self.saved_label.setText("⏳ " + t.get("TIME_SYNC", "Syncing..."))
        self.saved_label.show()
        ret = os.system("echo pi | sudo -S systemctl restart systemd-timesyncd 2>/dev/null")
        if ret == 0:
            msg = "✓ " + t.get("TIME_SYNC_OK", "Time synced!")
        else:
            msg = "✗ " + t.get("TIME_SYNC_FAIL", "Sync failed")
        self.saved_label.setText(msg)
        QTimer.singleShot(1500, lambda: self.stack.navigate_to("list"))

    def refresh_language(self):
        self._sync_timezone_from_system()
        self._update_texts()
        self._update_clock()


# ============================================================================
# Shutdown Confirmation Page
# ============================================================================
class ShutdownPage(QWidget):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.la = load_language()
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Warning icon
        self.icon_label = QLabel("⚠", self)
        icon_font = QFont()
        icon_font.setPointSize(36)
        self.icon_label.setFont(icon_font)
        self.icon_label.setStyleSheet("color: #FFD93D; background: transparent;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Warning title
        self.title_label = QLabel(self)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #ffffff; background: transparent;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)

        # Hint text
        self.hint_label = QLabel(self)
        hint_font = QFont()
        hint_font.setPointSize(11)
        self.hint_label.setFont(hint_font)
        self.hint_label.setStyleSheet("color: #8892c9; background: transparent;")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)

        # Corner hints
        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_bl = QLabel("C:取消", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("D:确认关机", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._update_texts()

    def _update_texts(self):
        self.la = load_language()
        title = self.la.get("DEMOEN", {}).get("SHUTDOWN_TITLE", "关机确认")
        hint = self.la.get("DEMOEN", {}).get("SHUTDOWN_HINT", "当前仅关机树莓派，机器狗关机还需按键")
        self.title_label.setText(title)
        self.hint_label.setText(hint)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.icon_label.setGeometry(0, h // 2 - 90, w, 40)
        self.title_label.setGeometry(20, h // 2 - 45, w - 40, 30)
        self.hint_label.setGeometry(20, h // 2 - 10, w - 40, 50)
        pad = 12
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            os.system("echo pi | sudo -S shutdown now")


# ============================================================================
# Reboot Confirmation Page
# ============================================================================
class RebootPage(QWidget):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.la = load_language()
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.icon_label = QLabel("🔄", self)
        icon_font = QFont()
        icon_font.setPointSize(36)
        self.icon_label.setFont(icon_font)
        self.icon_label.setStyleSheet("color: #FFD93D; background: transparent;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(self)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #ffffff; background: transparent;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)

        self.hint_label = QLabel(self)
        hint_font = QFont()
        hint_font.setPointSize(11)
        self.hint_label.setFont(hint_font)
        self.hint_label.setStyleSheet("color: #8892c9; background: transparent;")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)

        hint_style = "color: #8892c9; font-size: 12px; background: transparent;"
        self.corner_bl = QLabel("C:取消", self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("D:确认重启", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._update_texts()

    def _update_texts(self):
        self.la = load_language()
        title = self.la.get("DEMOEN", {}).get("REBOOT_TITLE", "重启确认")
        hint = self.la.get("DEMOEN", {}).get("REBOOT_HINT", "确定要重启树莓派？")
        self.title_label.setText(title)
        self.hint_label.setText(hint)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.icon_label.setGeometry(0, h // 2 - 90, w, 40)
        self.title_label.setGeometry(20, h // 2 - 45, w - 40, 30)
        self.hint_label.setGeometry(20, h // 2 - 10, w - 40, 50)
        pad = 12
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            os.system("echo pi | sudo -S reboot")


# ============================================================================
# SettingsStack (manages all pages)
# ============================================================================
class SettingsStack(QStackedWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0f1530;")

        self.list_page = SettingsListPage(self)
        self.about_page = AboutPage(self)
        self.sn_page = SNPage(self)
        self.volume_page = VolumePage(self)
        self.language_page = LanguagePage(self)
        self.contact_page = QRPage(self, " xgorobot_wx.png", "hello@xgorobot.com")
        self.download_page = QRPage(self, "app_down_qr.png")
        self.shutdown_page = ShutdownPage(self)
        self.reboot_page = RebootPage(self)
        self.time_page = TimeDatePage(self)

        self.addWidget(self.list_page)     # 0
        self.addWidget(self.about_page)    # 1
        self.addWidget(self.sn_page)       # 2
        self.addWidget(self.volume_page)   # 3
        self.addWidget(self.language_page) # 4
        self.addWidget(self.contact_page)  # 5
        self.addWidget(self.download_page) # 6
        self.addWidget(self.shutdown_page) # 7
        self.addWidget(self.reboot_page)   # 8
        self.addWidget(self.time_page)     # 9

        self.setCurrentIndex(0)
        self.page_map = {
            "list":         0,
            "about":        1,
            "sn":           2,
            "volume":       3,
            "language":     4,
            "contact_us":   5,
            "app_download": 6,
            "shutdown":     7,
            "reboot":       8,
            "time":         9,
        }

    def navigate_to(self, page_id: str):
        idx = self.page_map.get(page_id, 0)
        self.setCurrentIndex(idx)
        widget = self.widget(idx)
        if widget:
            widget.setFocus()
            # Refresh language on certain pages
            if hasattr(widget, 'refresh_language'):
                widget.refresh_language()
            if hasattr(widget, 'la'):
                widget.la = load_language()
                if hasattr(widget, 'update'):
                    widget.update()
            # 页面进入钩子，用于懒加载信息（如 AboutPage）
            if hasattr(widget, 'on_enter'):
                widget.on_enter()


# ============================================================================
# SettingsApp container
# ============================================================================
class SettingsApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0f1530;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = SettingsStack()
        layout.addWidget(self.stack)

        self.setFocusProxy(self.stack.list_page)

    def keyPressEvent(self, ev: QKeyEvent):
        # Forward all keys to current page
        current = self.stack.currentWidget()
        if current:
            current.keyPressEvent(ev)


# ============================================================================
# main() entry point (called by preload_app.py)
# ============================================================================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)

    w = SettingsApp()
    w.showFullScreen()

    rc = app.exec()
    print(f"[settings] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
