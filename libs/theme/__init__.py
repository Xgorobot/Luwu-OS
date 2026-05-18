"""luwu-os PySide6 子应用主题层。

提供与 launcher（C++/Qt）视觉同源的浅色主题：
- 颜色 / 字号 / 间距 / 圆角 / 资源路径常量见 :mod:`tokens`
- 生成 QSS 字符串的工具函数见 :mod:`qss`

新 app 推荐用法::

    from libs.theme import apply_app_palette
    from libs.ui import AppFrame, CornerKey
    ...
    app = QApplication(sys.argv)
    apply_app_palette(app)
"""
from PySide6.QtWidgets import QApplication

from . import qss
from . import tokens
from .tokens import Asset, Color, ColorRGB, Font, Radius, Spacing, hex_to_rgb


def apply_app_palette(app: QApplication) -> None:
    """对 QApplication 应用全局基础 QSS（字体颜色、滚动条等）。"""
    app.setStyleSheet(qss.app_palette())


__all__ = [
    "apply_app_palette",
    "qss",
    "tokens",
    "Asset",
    "Color",
    "ColorRGB",
    "Font",
    "Radius",
    "Spacing",
    "hex_to_rgb",
]
