"""根容器 AppFrame：与 launcher 视觉同源的浅色背景 + 4 角提示位 + 顶部标题。

子应用根 widget 推荐继承 :class:`AppFrame`，自动获得：
- launcher 同款浅色背景图
- 4 角图标 + 文字提示（与 launcher 角标布局一致）
- 顶部居中标题位
- 鼠标光标自动显隐（有鼠标连接时显示，否则隐藏）

例::

    class MyPage(AppFrame):
        def __init__(self):
            super().__init__()
            self.setTitle("我的页面")
            self.setCornerHints(
                tl=("上一项", Asset.icon_left),
                tr=("下一项", Asset.icon_right),
                bl=("返回",   Asset.icon_back),
                br=("确认",   Asset.icon_enter),
            )
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap, QCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme import Asset, Color, Spacing, qss
from .text import HintLabel, TitleLabel


# 手柄/游戏控制器的触摸板会被系统识别为鼠标(mouse)，
# 但手柄 App 场景下不需要显示光标，这里把它们排除掉。
_GAMEPAD_TOUCHPAD_KEYWORDS = [
    "wireless controller", "gamepad", "controller",
    "dualsense", "dualshock", "8bitdo", "joy-con",
    "pro controller", "xbox", "joystick",
]

# 用透明像素图创建不可见光标，避免 BlankCursor 在树莓派 X11 驱动下显示为黑块
_invisible_cursor_cache = None


def _invisible_cursor() -> QCursor:
    """返回一个真正透明的光标（1x1 全透明像素图）。"""
    global _invisible_cursor_cache
    if _invisible_cursor_cache is None:
        pix = QPixmap(1, 1)
        pix.fill(Qt.GlobalColor.transparent)
        _invisible_cursor_cache = QCursor(pix)
    return _invisible_cursor_cache


def _is_mouse_connected() -> bool:
    """检测 /proc/bus/input/devices 中是否存在「真正的」鼠标设备。

    手柄的触摸板（如 PS4/PS5/Xbox）虽然 Handlers 里包含 mouse，
    但其 Name 包含游戏控制器关键词，这类设备会被过滤掉。
    """
    try:
        with open('/proc/bus/input/devices', 'r') as f:
            content = f.read()
    except Exception:
        return False

    # 按设备块（空行分隔）逐个检查
    blocks = content.strip().split('\n\n')
    for block in blocks:
        lower = block.lower()
        if 'mouse' not in lower:
            continue
        # 提取设备名称
        name = ''
        for line in block.splitlines():
            if line.startswith('N: Name='):
                name = line.split('=', 1)[1].strip('"').lower()
                break
        # 如果是手柄触摸板，跳过
        if name and any(kw in name for kw in _GAMEPAD_TOUCHPAD_KEYWORDS):
            continue
        return True
    return False


class CornerKey:
    TL = "tl"
    TR = "tr"
    BL = "bl"
    BR = "br"


class CornerHint(QWidget):
    """单个角的图标+文字组合。

    左角图标在左、文字在右；右角则文字在左、图标在右，符合阅读视线。
    """

    ICON_SIZE = 18

    def __init__(self, parent: QWidget, corner: str):
        super().__init__(parent)
        self.corner = corner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._icon = QLabel(self)
        self._icon.setScaledContents(True)
        self._icon.setStyleSheet(qss.transparent())
        self._icon.hide()

        self._text = HintLabel("", self)
        # 角标文字用主深蓝 + 半粗 (Medium=500)，避免伪粗体糙感
        self._text.setStyleSheet(
            qss.text("hint", color=Color.text_primary) + "font-weight: 500;"
        )
        self._text.hide()

        if corner in (CornerKey.TR, CornerKey.BR):
            layout.addWidget(self._text)
            layout.addWidget(self._icon)
        else:
            layout.addWidget(self._icon)
            layout.addWidget(self._text)

    def setHint(self, text: str = "", icon: str = "") -> None:
        has_icon = False
        if icon:
            pix = QPixmap(icon)
            if not pix.isNull():
                self._icon.setPixmap(pix.scaled(
                    self.ICON_SIZE, self.ICON_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._icon.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
                has_icon = True
        self._icon.setVisible(has_icon)

        if text:
            self._text.setText(text)
            self._text.show()
        else:
            self._text.hide()

        self.adjustSize()


class AppFrame(QWidget):
    """子应用统一根容器。"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        # 背景图直接由本 widget 的 paintEvent 绘制，避免被子 widget 遮挡 paint 内容
        self.setAutoFillBackground(False)
        pix = QPixmap(Asset.bg_image)
        self._bg_pix = pix if not pix.isNull() else None
        if self._bg_pix is None:
            # 背景图缺失时兜底纯色
            self.setStyleSheet(qss.app_root())

        # 4 角提示
        self._corners = {
            CornerKey.TL: CornerHint(self, CornerKey.TL),
            CornerKey.TR: CornerHint(self, CornerKey.TR),
            CornerKey.BL: CornerHint(self, CornerKey.BL),
            CornerKey.BR: CornerHint(self, CornerKey.BR),
        }

        # 可选标题
        self._title = TitleLabel("", self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.hide()

        # 鼠标光标管理：初始隐藏，定时检测鼠标连接后恢复
        self._cursor_hidden = True
        self.setCursor(_invisible_cursor())
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._update_cursor)
        self._cursor_timer.start(3000)

    # ------------------------------------------------------------------ public
    def setTitle(self, text: str) -> None:
        self._title.setText(text)
        self._title.setVisible(bool(text))
        self._relayout()

    def setCornerHint(self, corner: str, text: str = "", icon: str = "") -> None:
        if corner not in self._corners:
            return
        self._corners[corner].setHint(text, icon)
        self._relayout()

    def setCornerHints(self, tl=None, tr=None, bl=None, br=None) -> None:
        """快速设置 4 个角。每个值可以是字符串或 (text, icon) tuple。"""
        for key, value in (
            (CornerKey.TL, tl), (CornerKey.TR, tr),
            (CornerKey.BL, bl), (CornerKey.BR, br),
        ):
            if value is None:
                continue
            if isinstance(value, tuple):
                text, icon = value
            else:
                text, icon = str(value), ""
            self._corners[key].setHint(text, icon)
        self._relayout()

    def cornerHint(self, corner: str) -> CornerHint:
        return self._corners.get(corner)

    def titleLabel(self) -> TitleLabel:
        return self._title

    # ----------------------------------------------------------------- events
    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._bg_pix is not None:
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self._bg_pix)
            painter.end()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout()

    # ----------------------------------------------------------------- layout
    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        if w == 0 or h == 0:
            return
        pad = 0  # 四角贴边
        for c in self._corners.values():
            c.adjustSize()
            c.raise_()
        self._corners[CornerKey.TL].move(pad, pad)
        tr = self._corners[CornerKey.TR]
        tr.move(w - tr.width() - pad, pad)
        bl = self._corners[CornerKey.BL]
        bl.move(pad, h - bl.height() - pad)
        br = self._corners[CornerKey.BR]
        br.move(w - br.width() - pad, h - br.height() - pad)

        if self._title.isVisible():
            self._title.adjustSize()
            self._title.move((w - self._title.width()) // 2, max(2, Spacing.md // 2))
            self._title.raise_()

    def _update_cursor(self) -> None:
        """定时检测鼠标连接状态，自动显隐光标。"""
        has_mouse = _is_mouse_connected()
        if has_mouse and self._cursor_hidden:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._cursor_hidden = False
        elif not has_mouse and not self._cursor_hidden:
            self.setCursor(_invisible_cursor())
            self._cursor_hidden = True
