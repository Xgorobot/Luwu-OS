"""Design tokens（颜色/字号/间距/圆角/资源路径）。

集中变量，便于一处改动全 app 跟随。子应用不要自己挑色或定字号，
统一通过本模块取值。
"""
from pathlib import Path

# launcher 的资产作为子应用的视觉资产源，保证视觉一致
_LAUNCHER_ASSETS = Path("/home/pi/luwu-os/launcher/assets")


def hex_to_rgb(hex_str: str):
    """把 ``"#1a3a6e"`` 或 ``"rgba(58,141,255,230)"`` 转成 ``(r, g, b)`` 元组。

    主要给 PIL/OpenCV 这类不识别 CSS 颜色字符串的库使用。
    """
    s = hex_str.strip()
    if s.startswith("#"):
        s = s[1:]
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    if s.startswith("rgb"):
        nums = s[s.index("(") + 1:s.rindex(")")].split(",")
        return tuple(int(float(n.strip())) for n in nums[:3])
    raise ValueError(f"unsupported color: {hex_str}")


class Color:
    """浅色调色盘（与 launcher 浅色背景一致）。"""

    # 文字
    text_primary = "#1a3a6e"      # 与 launcher gridview 文字一致
    text_secondary = "#5d7299"
    text_muted = "#8aa1c7"
    text_invert = "#ffffff"

    # 卡片
    card_bg = "rgba(255, 255, 255, 200)"
    card_border = "rgba(26, 58, 110, 40)"
    card_selected_bg = "rgba(58, 141, 255, 230)"
    card_selected_border = "#3a8dff"

    # 主色 / 语义色
    accent = "#3a8dff"
    success = "#18a957"
    warning = "#e69900"
    danger = "#d6453d"

    # 兜底背景（背景图加载失败时）
    bg_solid = "#eaf0fb"

    # 摄像头/全屏画面上的叠加层：要保证对比度，所以走半透明深底 + 主色边
    overlay_pill_bg = "rgba(0, 0, 0, 150)"        # 一般叠加胶囊
    overlay_pill_bg_strong = "rgba(0, 0, 0, 180)" # 强对比叠加（状态/标题）
    overlay_dim = "rgba(0, 0, 0, 160)"            # 模态遮罩


class ColorRGB:
    """颜色的 RGB 元组形态（PIL/OpenCV 用）。

    保持与 :class:`Color` 同名，便于按需替换。
    """

    text_primary = hex_to_rgb(Color.text_primary)
    text_secondary = hex_to_rgb(Color.text_secondary)
    text_muted = hex_to_rgb(Color.text_muted)
    text_invert = hex_to_rgb(Color.text_invert)
    accent = hex_to_rgb(Color.accent)
    success = hex_to_rgb(Color.success)
    warning = hex_to_rgb(Color.warning)
    danger = hex_to_rgb(Color.danger)
    bg_solid = hex_to_rgb(Color.bg_solid)
    # PIL 用的“屏底”深色（摄像头/全屏画布的纯色底）
    canvas_dark = (10, 14, 28)
    # 浅底 PIL 辅助色（与 launcher/settings 浅色主题同源）
    bg_card = (255, 255, 255)            # 浅底列表行/卡片内区
    bg_track = (220, 228, 242)           # 进度条背景轨道 / 分隔线
    # 列表项选中高亮（PIL 画在深底之上）
    list_highlight = (58, 141, 255)
    list_dialog_bg = (24, 32, 56)
    # 半透明叠加（RGBA），用于 PIL 在画布上叠加深色胶囊背景
    overlay_pill_bg = (0, 0, 0, 150)
    overlay_pill_bg_strong = (0, 0, 0, 180)
    overlay_dim = (0, 0, 0, 160)


class Font:
    """字号（像素），与 launcher 14px 正文对齐。"""

    family = "Noto Sans CJK SC"
    title = 18       # 页面大标题
    subtitle = 15    # 子标题
    body = 14        # 正文
    hint = 12        # 提示
    caption = 11     # 角标/说明


class Spacing:
    xs = 4
    sm = 8
    md = 12
    lg = 16
    xl = 20


class Radius:
    sm = 6
    md = 10
    lg = 14


# 主题字体路径：优先用 i18n 提供的字体，回落系统中文字体
def _resolve_font_path() -> str:
    try:
        import sys
        if "/home/pi/luwu-os" not in sys.path:
            sys.path.insert(0, "/home/pi/luwu-os")
        from libs.i18n import FONT_PATH as _F
        if _F:
            return _F
    except Exception:
        pass
    for cand in (
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(cand).exists():
            return cand
    return ""


class Asset:
    """复用 launcher 资产，避免视觉断层。"""

    bg_image = str(_LAUNCHER_ASSETS / "bg_macos.png")
    bg_image_alt = str(_LAUNCHER_ASSETS / "bg_macos_1.png")

    icon_back = str(_LAUNCHER_ASSETS / "icon_back.png")
    icon_enter = str(_LAUNCHER_ASSETS / "icon_enter.png")
    icon_left = str(_LAUNCHER_ASSETS / "icon_left.png")
    icon_right = str(_LAUNCHER_ASSETS / "icon_right.png")

    font_path = _resolve_font_path()


# 预留 DARK 主题接口（暂未实现，后续可在此分支基础上做夜间模式）
THEME_MODE = "light"
