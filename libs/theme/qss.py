"""按组件名返回 QSS 片段。子应用不要自己拼样式表，统一走本模块。"""
from .tokens import Color, Font, Radius


def app_palette() -> str:
    """全局 QSS，由 :func:`luwu-os.libs.theme.apply_app_palette` 应用到 QApplication。"""
    return f"""
    QWidget {{
        color: {Color.text_primary};
        background-color: transparent;
        font-family: "{Font.family}";
    }}
    QScrollBar:vertical {{
        width: 6px;
        background: transparent;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background: {Color.card_border};
        border-radius: 3px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        background: transparent;
    }}
    QScrollBar:horizontal {{
        height: 6px;
        background: transparent;
        border: none;
    }}
    QScrollBar::handle:horizontal {{
        background: {Color.card_border};
        border-radius: 3px;
        min-width: 24px;
    }}
    """


def app_root() -> str:
    """背景图加载失败时的兜底纯色。"""
    return f"background-color: {Color.bg_solid};"


def text(role: str = "body", color: str = None) -> str:
    """角色化文字样式。

    role: title / subtitle / body / hint / caption
    """
    role_map = {
        "title":    (Font.title,    True,  Color.text_primary),
        "subtitle": (Font.subtitle, True,  Color.text_primary),
        "body":     (Font.body,     False, Color.text_primary),
        "hint":     (Font.hint,     False, Color.text_secondary),
        "caption":  (Font.caption,  False, Color.text_muted),
    }
    size, bold, default_color = role_map.get(role, role_map["body"])
    weight = "bold" if bold else "normal"
    return (
        f"color: {color or default_color};"
        f"font-size: {size}px;"
        f"font-weight: {weight};"
        f"background: transparent;"
    )


def card(selected: bool = False) -> str:
    if selected:
        return (
            f"background-color: {Color.card_selected_bg};"
            f"border-radius: {Radius.md}px;"
            f"border: 1px solid {Color.card_selected_border};"
        )
    return (
        f"background-color: {Color.card_bg};"
        f"border-radius: {Radius.md}px;"
        f"border: 1px solid {Color.card_border};"
    )


def chip(state: str = "info") -> str:
    palette = {
        "success": Color.success,
        "warning": Color.warning,
        "danger":  Color.danger,
        "info":    Color.accent,
        "muted":   Color.text_muted,
    }
    color = palette.get(state, Color.accent)
    return (
        "background-color: rgba(255,255,255,210);"
        f"color: {color};"
        f"border: 1px solid {color};"
        "border-radius: 8px;"
        "padding: 1px 8px;"
        f"font-size: {Font.caption}px;"
    )


def transparent() -> str:
    return "background: transparent;"


def overlay_pill(role: str = "body", color: str = None, strong: bool = False) -> str:
    """摄像头/全屏画面上的叠加胶囊样式。

    主体是半透明深色底 + 主题色文字，保证有足够对比度。
    role: title / subtitle / body / hint / caption
    color: 可传入语义色覆盖默认白字（如 :data:`Color.success`）
    strong: True 时用更深的底、适合状态/标题
    """
    role_map = {
        "title":    (Font.title,    True),
        "subtitle": (Font.subtitle, True),
        "body":     (Font.body,     True),
        "hint":     (Font.hint,     False),
        "caption":  (Font.caption,  False),
    }
    size, bold = role_map.get(role, role_map["body"])
    weight = "bold" if bold else "normal"
    bg = Color.overlay_pill_bg_strong if strong else Color.overlay_pill_bg
    fg = color or Color.text_invert
    return (
        f"color: {fg};"
        f"font-size: {size}px;"
        f"font-weight: {weight};"
        f"background-color: {bg};"
        f"border-radius: {Radius.sm}px;"
        "padding: 4px 10px;"
    )


def corner_pill(color: str = None) -> str:
    """4 角按键提示胶囊（叠加在全屏画面上）。"""
    return overlay_pill("caption", color=color or Color.text_invert)
