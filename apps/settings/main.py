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
LUWU_ROOT = Path(os.environ.get("LUWU_ROOT", "/opt/luwu-os"))

# 主题层 & 组件层
if str(LUWU_ROOT) not in sys.path:
    sys.path.insert(0, str(LUWU_ROOT))
from libs.theme import apply_app_palette, Asset, Color as T_Color, Spacing, qss as T_qss
from libs.ui import (
    AppFrame as _BaseAppFrame, CardPanel, InfoRow, TitleLabel, SubtitleLabel,
    BodyLabel, HintLabel, CaptionLabel, CornerKey,
)

# settings 专属背景：覆盖 launcher 默认背景
_SETTINGS_BG_IMAGE = str(LUWU_ROOT / "assets" / "images" / "app_bg.png")


class AppFrame(_BaseAppFrame):
    """settings 子应用根容器：使用专属背景图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QPixmap as _QPixmap
        _pix = _QPixmap(_SETTINGS_BG_IMAGE)
        if not _pix.isNull():
            self._bg_pix = _pix
            self.update()

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

def get_cpu_load():
    try:
        with open("/proc/loadavg", "r") as f:
            load_1m = float(f.read().strip().split()[0])
            return f"{load_1m:.2f}"
    except Exception:
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
        # Return raw data; caller applies i18n formatting
        return (fmt(total), fmt(used), fmt(free))
    except Exception:
        return ("Unknown", "Unknown", "Unknown")

def _get_pkg_version(pkg_name: str) -> str:
    """使用 importlib.metadata 直接读取已安装包的版本号（毫秒级，无子进程）。
    避免在主线程调用 `pip show` 造成 1~2 秒阻塞，导致打开 settings 时屏幕黑一半。
    返回值可能是版本号字符串、"Not installed" 或 "Unknown"（需由调用方通过 la 翻译）。
    """
    try:
        try:
            from importlib.metadata import version, PackageNotFoundError  # py3.8+
        except Exception:
            from importlib_metadata import version, PackageNotFoundError  # type: ignore
        try:
            return version(pkg_name)
        except PackageNotFoundError:
            return "Not installed"
    except Exception:
        return "Unknown"

def get_xgolib_version():
    return _get_pkg_version("xgolib")

def get_xgoedu_version():
    return _get_pkg_version("xgoedu-luwuos")

# ---- Color constants ----
# 保留主题化前的 QColor 常量供 paintEvent 使用，数值重新映射到主题 token
COLOR_BG = QColor(T_Color.bg_solid)
COLOR_CARD = QColor(255, 255, 255, 220)              # 卡片白底
COLOR_SELECT = QColor(T_Color.card_selected_border)   # 选中紫→主题蓝 accent
COLOR_WHITE = QColor(T_Color.text_invert)
COLOR_GRAY = QColor(T_Color.text_muted)
COLOR_PURPLE = QColor(T_Color.accent)                 # 原紫色动作色→主题 accent
COLOR_UNSELECT = QColor(T_Color.card_border)          # 未选中卡片边框/进度条底色
COLOR_GREEN = QColor(T_Color.success)
COLOR_CARD_SELECTED_BG = QColor(58, 141, 255, 230)    # 选中卡片底色（带透明度）
COLOR_CARD_BG = QColor(255, 255, 255, 200)             # 未选中卡片白底
COLOR_TEXT = QColor(T_Color.text_primary)              # 深色主文字

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
    {"id": "reboot",      "icon": "power.png",         "label_key": "REBOOT"},
]

def _recolor_pixmap(source: QPixmap, color: str) -> QPixmap:
    """使用 alpha 通道作为遮罩，将图标重新着色为目标颜色。"""
    result = QPixmap(source.size())
    result.fill(Qt.GlobalColor.transparent)
    p = QPainter(result)
    p.drawPixmap(0, 0, source)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(result.rect(), QColor(color))
    p.end()
    return result


# ============================================================================
# SettingsListPage
# ============================================================================
class SettingsListPage(AppFrame):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.selected_idx = 0
        self.scroll_offset = 0
        self.la = load_language()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 主题化角标（与 launcher 同款图标）
        self._apply_corner_hints()

        # Item widgets
        self.item_widgets = []
        for i, item in enumerate(SETTING_ITEMS):
            w = self._make_item_widget(item, i)
            self.item_widgets.append(w)

        self.update_selection()

    def _apply_corner_hints(self):
        t = self.la.get("DEMOEN", {})
        self.setCornerHints(
            tl=(t.get("UP", "Up"),    Asset.icon_left),
            tr=(t.get("DOWN", "Down"),  Asset.icon_right),
            bl=(t.get("BACK", "Back"),  Asset.icon_back),
            br=(t.get("CONFIRM", "Confirm"), Asset.icon_enter),
        )

    def _make_item_widget(self, item, index):
        """Create a container widget for a settings list item."""
        container = QWidget(self)
        container.setFixedSize(250, 26)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 1, 8, 1)
        layout.setSpacing(8)

        # Icon — 正常态用 text_primary（深蓝），选中态用 text_invert（白）
        icon_label = QLabel()
        icon_path = str(PICS_DIR / item["icon"])
        source_pix = QPixmap(icon_path)
        if not source_pix.isNull():
            normal_pix = _recolor_pixmap(source_pix, T_Color.text_primary)
            normal_pix = normal_pix.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            selected_pix = _recolor_pixmap(source_pix, T_Color.text_invert)
            selected_pix = selected_pix.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            icon_label.setProperty("normal_pix", normal_pix)
            icon_label.setProperty("selected_pix", selected_pix)
            icon_label.setPixmap(normal_pix)
        icon_label.setFixedSize(20, 20)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(T_qss.transparent())
        icon_label.setObjectName(f"icon_{index}")
        layout.addWidget(icon_label)

        # Label
        label_key = item["label_key"]
        text = self.la.get("DEMOEN", {}).get(label_key, label_key)
        text_label = QLabel(text)
        text_label.setStyleSheet(T_qss.text("body"))
        text_label.setObjectName(f"text_{index}")
        layout.addWidget(text_label)

        layout.addStretch()

        # Arrow indicator
        arrow = QLabel(">")
        arrow.setStyleSheet(T_qss.text("caption"))
        arrow.setObjectName(f"arrow_{index}")
        layout.addWidget(arrow)

        container.setObjectName(f"item_{index}")
        container.setStyleSheet(f"#item_{index} {{ {T_qss.card(False)} }}")
        return container

    def update_selection(self):
        """Update visual styles based on selected index."""
        for i, w in enumerate(self.item_widgets):
            sel = (i == self.selected_idx)
            self._update_item_style(w, i, sel)

    def _update_item_style(self, container, idx, selected):
        container.setStyleSheet(f"#item_{idx} {{ {T_qss.card(selected)} }}")
        text_label = container.findChild(QLabel, f"text_{idx}")
        icon_label = container.findChild(QLabel, f"icon_{idx}")
        arrow = container.findChild(QLabel, f"arrow_{idx}")
        if selected:
            if text_label:
                text_label.setStyleSheet(T_qss.text("body", color=T_Color.text_invert))
            if arrow:
                arrow.setStyleSheet(T_qss.text("caption", color=T_Color.text_invert))
            if icon_label:
                pix = icon_label.property("selected_pix")
                if pix:
                    icon_label.setPixmap(pix)
        else:
            if text_label:
                text_label.setStyleSheet(T_qss.text("body"))
            if arrow:
                arrow.setStyleSheet(T_qss.text("caption"))
            if icon_label:
                pix = icon_label.property("normal_pix")
                if pix:
                    icon_label.setPixmap(pix)

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
        self._apply_corner_hints()
        # 重建列表项文本
        for i, item in enumerate(SETTING_ITEMS):
            text_label = self.item_widgets[i].findChild(QLabel, f"text_{i}")
            if text_label:
                label_key = item["label_key"]
                text_label.setText(self.la.get("DEMOEN", {}).get(label_key, label_key))
        self.update_selection()


# ============================================================================
# About Page (关于本机)
# ============================================================================
class AboutPage(AppFrame):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.la = load_language()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.scroll_offset = 0
        self.visible_count = 1

        # 懒加载：__init__ 时不采集信息，避免 settings 启动时被 AboutPage 拖慢。
        # info_items / info_labels 在 on_enter() 首次进入页面时才生成。
        self.info_items = []
        self.info_labels = []
        self._info_loaded = False

        self._apply_corner_hints()

        # 加载中占位
        t = self.la.get("DEMOEN", {})
        self.loading_label = HintLabel(t.get("LOADING", "Loading..."), self)
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _apply_corner_hints(self):
        t = self.la.get("DEMOEN", {})
        self.setCornerHints(
            tl=(t.get("UP", "Up"),    Asset.icon_left),
            tr=(t.get("DOWN", "Down"),  Asset.icon_right),
            bl=(t.get("BACK", "Back"),  Asset.icon_back),
            br=(t.get("BACK", "Back"),  Asset.icon_enter),
        )

    def _gather_info(self):
        t = self.la.get("DEMOEN", {})
        items = []
        items.append((t.get("LUWUOS_VER", "LuwuOS Version"), "2.0.0"))
        items.append((t.get("XGOLIB_VER", "xgolib Version"), self._tr_val(get_xgolib_version())))
        items.append((t.get("XGOEDU_VER", "xgoedu Version"), self._tr_val(get_xgoedu_version())))
        items.append((t.get("CPU_MODEL", "CPU"), self._tr_val(get_cpu_model())))
        items.append((t.get("CPU_CORES", "CPU Cores"), str(get_cpu_cores())))
        items.append((t.get("CPU_LOAD", "CPU Load"), self._tr_val(get_cpu_load())))
        disk_total, disk_used, disk_free = get_disk_info()
        disk_fmt = t.get("DISK_FORMAT", "{total} | Used {used} | Free {free}")
        items.append((t.get("DISK", "Disk"), disk_fmt.format(
            total=self._tr_val(disk_total),
            used=self._tr_val(disk_used),
            free=self._tr_val(disk_free)
        )))
        items.append((t.get("SN_LABEL", "SN"), f"{get_sn_short()}{get_mac_address()}"))
        return items

    def _tr_val(self, val):
        """Translate status/value strings (Not installed / Unknown)."""
        t = self.la.get("DEMOEN", {})
        if val == "Not installed":
            return t.get("PKG_NOT_INSTALLED", "Not installed")
        if val == "Unknown":
            return t.get("PKG_UNKNOWN", "Unknown")
        return val

    def _build_info_labels(self):
        """首次进入 about 页时才采集信息并创建 labels。"""
        # 清理旧 label（多次重入也能刷新）
        for lbl in self.info_labels:
            lbl.setParent(None)
            lbl.deleteLater()
        self.info_labels = []

        self.info_items = self._gather_info()
        for key, value in self.info_items:
            lbl = CaptionLabel(f"{key}:  {value}", self)
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
            self.stack.navigate_to("list")

    def refresh_language(self):
        self.la = load_language()
        self._apply_corner_hints()
        # 重新采集并重建信息标签（语言变更后刷新）
        if self._info_loaded:
            self._build_info_labels()
            self._relayout_items()
            self.update()


# ============================================================================
# SN Page
# ============================================================================
class SNPage(AppFrame):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.la = load_language()

        # SN display
        self.sn_id = get_sn_short() + get_mac_address()
        t = self.la.get("DEMOEN", {})
        sn_prefix = t.get("SN_PREFIX", "SN: ")
        full_sn = f"{sn_prefix}{self.sn_id}"
        self.sn_label = SubtitleLabel(full_sn, self)
        self.sn_label.setColor(T_Color.accent)
        self.sn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Barcode display
        self.barcode_label = QLabel(self)
        self.barcode_label.setStyleSheet(T_qss.transparent())
        self.barcode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._generate_barcode()

        self.setCornerHints(
            bl=(self.la.get("DEMOEN", {}).get("BACK", "Back"), Asset.icon_back),
            br=(self.la.get("DEMOEN", {}).get("BACK", "Back"), Asset.icon_enter),
        )

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
            self.barcode_label.setStyleSheet(T_qss.text("body", color=T_Color.danger))

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

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back or ev.key() == Qt.Key.Key_Left:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            self.stack.navigate_to("list")

    def refresh_language(self):
        self.la = load_language()
        t = self.la.get("DEMOEN", {})
        sn_prefix = t.get("SN_PREFIX", "SN: ")
        self.sn_label.setText(f"{sn_prefix}{self.sn_id}")
        self.setCornerHints(
            bl=(t.get("BACK", "Back"), Asset.icon_back),
            br=(t.get("BACK", "Back"), Asset.icon_enter),
        )


# ============================================================================
# Volume Page
# ============================================================================
class VolumePage(AppFrame):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.volume = read_volume()
        self.la = load_language()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Title
        self.title_label = TitleLabel(self.la.get("DEMOEN", {}).get("VOLUME_TITLE", "Volume"), self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Volume percent label
        self.percent_label = TitleLabel(f"{self.volume}%", self)
        self.percent_label.setColor(T_Color.accent)
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Saved hint (hidden by default)
        self.saved_label = HintLabel("", self)
        self.saved_label.setColor(T_Color.success)
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.saved_label.hide()

        self.setCornerHints(
            tl="-5%",
            tr="+5%",
            bl=(self.la.get("DEMOEN", {}).get("BACK", "Back"), Asset.icon_back),
            br=(self.la.get("DEMOEN", {}).get("SAVE", "Save"), Asset.icon_enter),
        )

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
            # 返回列表（左下角）
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            # Save and go back (右下角）
            write_volume(self.volume)
            saved_text = self.la.get("DEMOEN", {}).get("SAVED_MSG", "Saved!")
            self.saved_label.setText(saved_text)
            self.saved_label.show()
            QTimer.singleShot(800, lambda: self.stack.navigate_to("list"))

    def refresh_language(self):
        self.la = load_language()
        t = self.la.get("DEMOEN", {})
        self.title_label.setText(t.get("VOLUME_TITLE", "Volume"))
        self.setCornerHints(
            tl="-5%",
            tr="+5%",
            bl=(t.get("BACK", "Back"), Asset.icon_back),
            br=(t.get("SAVE", "Save"), Asset.icon_enter),
        )


# ============================================================================
# Language Page
# ============================================================================
class LanguagePage(AppFrame):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.content = get_lang_code()
        self.la = load_language()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Title
        self.title_label = TitleLabel(self.la.get("DEMOEN", {}).get("LANGUAGE_TITLE", "Language"), self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Option buttons (drawn manually)
        self.cn_selected = (self.content == "cn")
        self.en_selected = (self.content == "en")

        # Saved hint
        self.saved_label = HintLabel("", self)
        self.saved_label.setColor(T_Color.success)
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.saved_label.hide()

        self.setCornerHints(
            tl="CN",
            tr="EN",
            bl=(self.la.get("DEMOEN", {}).get("BACK", "Back"), Asset.icon_back),
            br=(self.la.get("DEMOEN", {}).get("SAVE", "Save"), Asset.icon_enter),
        )

    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        btn_w, btn_h = 100, 50
        total_w = btn_w * 2 + 20
        start_x = (w - total_w) // 2
        btn_y = h // 2 - btn_h // 2

        font = QFont()
        font.setPointSize(16)
        font.setBold(True)

        # ---- CN button ----
        cn_rect = QRect(start_x, btn_y, btn_w, btn_h)
        self._draw_lang_btn(painter, cn_rect, "CN", self.cn_selected, font)

        # ---- EN button ----
        en_rect = QRect(start_x + btn_w + 20, btn_y, btn_w, btn_h)
        self._draw_lang_btn(painter, en_rect, "EN", self.en_selected, font)

        painter.end()

    def _draw_lang_btn(self, painter, rect, text, selected, font):
        """统一绘制语言选择按钮：选中↔未选中视觉对比强烈。"""
        if selected:
            # 选中态：蓝色半透明底 + 蓝色实线边框 + 白色文字
            painter.setBrush(COLOR_CARD_SELECTED_BG)
            painter.setPen(QPen(COLOR_SELECT, 2))
        else:
            # 未选中态：白色半透明底 + 浅色边框 + 深色文字（清晰可见）
            painter.setBrush(COLOR_CARD_BG)
            painter.setPen(QPen(COLOR_UNSELECT, 1))
        painter.drawRoundedRect(rect, 10, 10)

        painter.setFont(font)
        painter.setPen(COLOR_WHITE if selected else COLOR_TEXT)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.title_label.setGeometry(0, 30, w, 30)
        self.saved_label.setGeometry(0, h - 60, w, 25)

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
            # 左下角：退出，返回列表
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            # 右下角：保存语言并重启
            set_lang_code(self.content)
            saved_text = self.la.get("DEMOEN", {}).get("SAVED_MSG", "Saved!")
            self.saved_label.setText(saved_text)
            self.saved_label.show()
            QTimer.singleShot(1500, lambda: self._do_restart())

    def _do_restart(self):
        # Quit app; launcher will restart preload process automatically
        QApplication.instance().quit()

    def refresh_language(self):
        self.la = load_language()
        t = self.la.get("DEMOEN", {})
        self.title_label.setText(t.get("LANGUAGE_TITLE", "Language"))
        self.setCornerHints(
            tl="CN",
            tr="EN",
            bl=(t.get("BACK", "Back"), Asset.icon_back),
            br=(t.get("SAVE", "Save"), Asset.icon_enter),
        )


# ============================================================================
# QR Code Page (Contact Us / App Download)
# ============================================================================
class QRPage(AppFrame):
    def __init__(self, stack: QStackedWidget, qr_image: str, email: str = None):
        super().__init__()
        self.stack = stack
        self.qr_image = qr_image
        self.email = email
        self.la = load_language()
        self.qr_pixmap = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Load QR image
        qr_path = str(PICS_DIR / qr_image)
        pix = QPixmap(qr_path)
        if not pix.isNull():
            self.qr_pixmap = pix

        self._apply_corner_hints()

    def _apply_corner_hints(self):
        t = self.la.get("DEMOEN", {})
        self.setCornerHints(
            bl=(t.get("BACK", "Back"), Asset.icon_back),
            br=(t.get("BACK", "Back"), Asset.icon_enter),
        )

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
                painter.setPen(QColor(T_Color.text_primary))
                email_rect = QRect(0, qr_y + scaled.height() + 5, w, 20)
                painter.drawText(email_rect, Qt.AlignmentFlag.AlignCenter, self.email)

        painter.end()

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back or ev.key() == Qt.Key.Key_Left:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            self.stack.navigate_to("list")

    def refresh_language(self):
        self.la = load_language()
        self._apply_corner_hints()


# ============================================================================
# Time / Date Page
# ============================================================================
class TimeDatePage(AppFrame):
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
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Get current timezone
        self._sync_timezone_from_system()

        # Time display (large) — 主题文字色（强调）
        self.time_label = TitleLabel("", self)
        time_font = QFont()
        time_font.setPointSize(30)
        time_font.setBold(True)
        self.time_label.setFont(time_font)
        self.time_label.setColor(T_Color.accent)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Date display
        self.date_label = SubtitleLabel("", self)
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Saved hint
        self.saved_label = HintLabel("", self)
        self.saved_label.setColor(T_Color.accent)
        self.saved_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.saved_label.hide()

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
        t = self.la.get("DEMOEN", {})
        weekdays = t.get("WEEKDAYS", ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"])
        wd = weekdays[now.weekday()] if isinstance(weekdays, list) and len(weekdays) > now.weekday() else now.strftime("%a")
        self.date_label.setText(now.strftime(f"%Y-%m-%d {wd}"))

    def _update_texts(self):
        self.la = load_language()
        t = self.la.get("DEMOEN", {})
        tz_text = t.get("TIMEZONE", "TZ")
        self.setCornerHints(
            tl=(tz_text, Asset.icon_left),
            tr=(tz_text, Asset.icon_right),
            bl=(t.get("BACK", "Back"), Asset.icon_back),
            br=(t.get("CONFIRM", "Confirm"), Asset.icon_enter),
        )
        self.update()

    def _current_tz_display(self):
        """Display name for the current timezone selection (via i18n)."""
        t = self.la.get("DEMOEN", {})
        tz_key = "TZ_" + self.current_tz.replace("/", "_")
        return t.get(tz_key, self.current_tz)

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
        painter.setPen(QPen(COLOR_UNSELECT, 1))
        painter.setBrush(COLOR_CARD)
        painter.drawRoundedRect(rect_x, tz_y, rect_w, rect_h, 10, 10)

        # Timezone text
        tz_font = QFont()
        tz_font.setPointSize(12)
        tz_font.setBold(True)
        painter.setFont(tz_font)
        painter.setPen(QColor(T_Color.text_primary))

        display = self._current_tz_display()
        painter.drawText(QRect(rect_x, tz_y, rect_w, rect_h), Qt.AlignmentFlag.AlignCenter, display)

        # Left / Right arrows on sides
        arrow_font = QFont()
        arrow_font.setPointSize(16)
        painter.setFont(arrow_font)
        painter.setPen(COLOR_PURPLE)
        painter.drawText(QRect(rect_x - 30, tz_y, 30, rect_h), Qt.AlignmentFlag.AlignCenter, "◀")
        painter.drawText(QRect(rect_x + rect_w, tz_y, 30, rect_h), Qt.AlignmentFlag.AlignCenter, "▶")

        painter.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.time_label.setGeometry(0, h // 2 - 70, w, 40)
        self.date_label.setGeometry(0, h // 2 - 30, w, 25)
        self.saved_label.setGeometry(0, h - 60, w, 25)

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
# Reboot Confirmation Page
# ============================================================================
class RebootPage(AppFrame):
    def __init__(self, stack: QStackedWidget):
        super().__init__()
        self.stack = stack
        self.la = load_language()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.title_label = TitleLabel("", self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)

        self.hint_label = HintLabel("", self)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)

        self._update_texts()

    def _update_texts(self):
        self.la = load_language()
        t = self.la.get("DEMOEN", {})
        self.title_label.setText(t.get("REBOOT_TITLE", "Reboot Confirm"))
        self.hint_label.setText(t.get("REBOOT_HINT", "Are you sure to reboot Raspberry Pi?"))
        self.setCornerHints(
            bl=(t.get("CANCEL", "Cancel"), Asset.icon_back),
            br=(t.get("CONFIRM", "Confirm"), Asset.icon_enter),
        )

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self.title_label.setGeometry(20, h // 2 - 60, w - 40, 30)
        self.hint_label.setGeometry(20, h // 2 - 10, w - 40, 50)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            self.stack.navigate_to("list")
        elif ev.key() == Qt.Key.Key_Return:
            os.system("echo pi | sudo -S reboot")

    def refresh_language(self):
        self._update_texts()


# ============================================================================
# SettingsStack (manages all pages)
# ============================================================================
class SettingsStack(QStackedWidget):
    def __init__(self):
        super().__init__()
        # 不设置背景色：背景由各页面的 AppFrame 统一提供

        self.list_page = SettingsListPage(self)
        self.about_page = AboutPage(self)
        self.sn_page = SNPage(self)
        self.volume_page = VolumePage(self)
        self.language_page = LanguagePage(self)
        self.contact_page = QRPage(self, " xgorobot_wx.png", "hello@xgorobot.com")
        self.download_page = QRPage(self, "app_down_qr.png")
        self.reboot_page = RebootPage(self)
        self.time_page = TimeDatePage(self)

        self.addWidget(self.list_page)     # 0
        self.addWidget(self.about_page)    # 1
        self.addWidget(self.sn_page)       # 2
        self.addWidget(self.volume_page)   # 3
        self.addWidget(self.language_page) # 4
        self.addWidget(self.contact_page)  # 5
        self.addWidget(self.download_page) # 6
        self.addWidget(self.reboot_page)   # 7
        self.addWidget(self.time_page)     # 8

        self.setCurrentIndex(0)
        self.page_map = {
            "list":         0,
            "about":        1,
            "sn":           2,
            "volume":       3,
            "language":     4,
            "contact_us":   5,
            "app_download": 6,
            "reboot":       7,
            "time":         8,
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
        # 背景由 AppFrame 提供，本容器不重复填色
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
    apply_app_palette(app)

    w = SettingsApp()
    w.showFullScreen()

    rc = app.exec()
    print(f"[settings] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
