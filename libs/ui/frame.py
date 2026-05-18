"""根容器 AppFrame：与 launcher 视觉同源的浅色背景 + 4 角提示位 + 顶部标题。

子应用根 widget 推荐继承 :class:`AppFrame`，自动获得：
- launcher 同款浅色背景图
- 4 角图标 + 文字提示（与 launcher 角标布局一致）
- 顶部居中标题位

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
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme import Asset, Color, Spacing, qss
from .text import HintLabel, TitleLabel


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
        # 角标文字加重为主深蓝 + bold，以保证在桌面渐变背景上的对比度
        self._text.setStyleSheet(
            qss.text("hint", color=Color.text_primary) + "font-weight: bold;"
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
