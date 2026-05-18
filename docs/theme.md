# luwu-os 主题系统设计与使用

面向 `apps/` 下所有 PySide6 子应用的统一视觉层。目标是让任何一个子应用都能与 launcher（C++/Qt）保持视觉同源，并且**不允许**子应用里出现硬编码颜色 / 字号 / 间距字符串。

> **硬性约束**：子应用里不要写 `"color: #1a3a6e"`、`(0, 0, 0, 160)` 这种字面量。所有视觉变量必须从 `libs/theme` 取，所有样式表必须由 `libs/theme/qss.py` 工厂函数拼出。

---

## 1 · 设计目标

| 目标 | 落地手段 |
|---|---|
| 与 launcher 视觉同源 | 复用 launcher 的 `bg_macos.png` 背景、`#1a3a6e` 主深蓝、`icon_back/enter/left/right` 角标资源 |
| 一处改动，全局跟随 | 颜色 / 字号 / 间距 / 圆角 / 资源路径全部集中到 `libs/theme/tokens.py` |
| Qt 与 PIL 双管线统一 | 同一套色板暴露两种形态：CSS 字符串（`Color`）+ RGB 元组（`ColorRGB`），PIL/OpenCV 直接取元组用 |
| 摄像头/全屏画面有设计感 | 提供 `overlay_pill` / `corner_pill` 半透明胶囊样式，文字浮在画面上不糊不脏 |
| 新 app 5 分钟接入 | `AppFrame` 根容器一行 `super().__init__()` 即获得背景 + 4 角占位 + 标题位 |
| 多语言无副作用 | 主题层不耦合任何文案，切语言只需 app 自己重新调一次 `setCornerHints(...)` |

---

## 2 · 架构分层

```
libs/
├── theme/                 设计变量 + QSS 工厂（不依赖 ui）
│   ├── tokens.py          Color / ColorRGB / Font / Spacing / Radius / Asset
│   ├── qss.py             text() / card() / chip() / overlay_pill() / corner_pill() / app_palette()
│   └── __init__.py        apply_app_palette(app) 入口
└── ui/                    通用 widget 组件（依赖 theme）
    ├── frame.py           AppFrame + CornerHint + CornerKey
    ├── text.py            TitleLabel / SubtitleLabel / BodyLabel / HintLabel / CaptionLabel
    ├── card.py            CardPanel / InfoRow
    ├── chip.py            StatusChip
    ├── scroll.py          ScrollList
    └── camera.py          CameraOverlay（摄像头三件套用）
```

**依赖方向**：`apps/* → libs/ui → libs/theme`。`libs/theme` 自身不引用 `libs/ui`，保证主题层可单独被 PIL/OpenCV 那种"非 widget"代码引用（network 子应用就是这种用法）。

---

## 3 · Token 体系

### 3.1 颜色（`tokens.Color` / `tokens.ColorRGB`）

| 分组 | Key | 值 | 用途 |
|---|---|---|---|
| 文字 | `text_primary` | `#1a3a6e` | 与 launcher gridview 一致的主深蓝 |
|  | `text_secondary` | `#5d7299` | 次级文字 |
|  | `text_muted` | `#8aa1c7` | 提示 / 弱化 |
|  | `text_invert` | `#ffffff` | 深色背景 / 摄像头画面上的反白 |
| 卡片 | `card_bg` | `rgba(255,255,255,200)` | 默认卡片底 |
|  | `card_border` | `rgba(26,58,110,40)` | 默认卡片边 |
|  | `card_selected_bg` | `rgba(58,141,255,230)` | 选中卡片底 |
|  | `card_selected_border` | `#3a8dff` | 选中卡片边 |
| 语义 | `accent` | `#3a8dff` | 主交互蓝 |
|  | `success` | `#18a957` | 成功 / 正向 |
|  | `warning` | `#e69900` | 警告 |
|  | `danger` | `#d6453d` | 失败 / 危险 |
| 兜底 | `bg_solid` | `#eaf0fb` | 背景图加载失败时的纯色兜底 |
| 叠加 | `overlay_pill_bg` | `rgba(0,0,0,150)` | 摄像头画面上的轻胶囊背景 |
|  | `overlay_pill_bg_strong` | `rgba(0,0,0,180)` | 状态 / 标题用的强对比胶囊 |
|  | `overlay_dim` | `rgba(0,0,0,160)` | 模态遮罩 |

`ColorRGB` 对应同名键，但值是 PIL 直接吃的元组：

```python
from libs.theme import Color, ColorRGB
Color.accent         # "#3a8dff"        → QSS / setStyleSheet
ColorRGB.accent      # (58, 141, 255)   → PIL ImageDraw.text(..., fill=...)
ColorRGB.overlay_dim # (0, 0, 0, 160)   → PIL 半透明叠加
```

**额外的 PIL-only 颜色**（仅画在深底全屏画面上时使用，QSS 那边没必要存在）：

| Key | 值 | 用途 |
|---|---|---|
| `ColorRGB.canvas_dark` | `(10, 14, 28)` | 全屏深色画布底（network 国家页 / 键盘页）|
| `ColorRGB.list_highlight` | `(58, 141, 255)` | 列表项选中色（与 accent 一致） |
| `ColorRGB.list_dialog_bg` | `(24, 32, 56)` | 深底对话框背景 |

需要把任意 hex 字符串转成 PIL 元组时用辅助函数：

```python
from libs.theme import hex_to_rgb
hex_to_rgb("#1a3a6e")           # (26, 58, 110)
hex_to_rgb("rgba(58,141,255)")  # (58, 141, 255)   # 忽略 alpha
```

### 3.2 字号（`tokens.Font`）

固定 5 级，与 launcher 14px 正文对齐：

| Key | 值（px） | 用途 |
|---|---|---|
| `Font.title` | 18 | 页面大标题 |
| `Font.subtitle` | 15 | 子标题 |
| `Font.body` | 14 | 正文（默认） |
| `Font.hint` | 12 | 提示 / 状态 |
| `Font.caption` | 11 | 角标 / 极次要说明 |

`Font.family = "Noto Sans CJK SC"`；`Asset.font_path` 是 PIL `ImageFont.truetype` 用的字体文件路径，按 i18n FONT_PATH → 系统 droid → noto 顺序回落。

### 3.3 间距 / 圆角

```python
Spacing.xs / sm / md / lg / xl   == 4 / 8 / 12 / 16 / 20   # 4 的倍数
Radius.sm / md / lg              == 6 / 10 / 14            # sm=控件 / md=卡片 / lg=容器
```

### 3.4 资源（`tokens.Asset`）

直接复用 launcher `assets/` 资源，避免视觉断层：

```python
Asset.bg_image      # bg_macos.png
Asset.bg_image_alt  # bg_macos_1.png
Asset.icon_back     # ←
Asset.icon_enter    # ⏎
Asset.icon_left     # ◀
Asset.icon_right    # ▶
Asset.font_path     # PIL 用的中文字体绝对路径
```

---

## 4 · QSS 工厂（`libs.theme.qss`）

需要写局部样式时**只走这些函数**，不要拼颜色字符串。

| 函数 | 一句话用法 |
|---|---|
| `text(role, color=None)` | 角色化文字样式。`role ∈ title/subtitle/body/hint/caption`。`color` 可覆盖默认色 |
| `card(selected=False)` | 圆角白底卡片；`selected=True` 用 accent 边 + 选中蓝底 |
| `chip(state)` | 状态色块。`state ∈ success/warning/danger/info/muted` |
| `transparent()` | 透明背景，常用于 layout 中的 spacer / wrapper |
| `app_root()` | 背景图加载失败时的纯色兜底 |
| `app_palette()` | 全局基础 QSS（字体 + 滚动条），由 `apply_app_palette()` 应用 |
| `overlay_pill(role, color=None, strong=False)` | 摄像头/全屏画面上的叠加胶囊（半透明深底 + 反白文字）|
| `corner_pill(color=None)` | 4 角按键提示胶囊（`overlay_pill("caption")` 的语义化别名）|

示例：

```python
from libs.theme import qss as T_qss, Color as T

label.setStyleSheet(T_qss.text("body"))
label.setStyleSheet(T_qss.text("caption", color=T.accent))
panel.setStyleSheet(T_qss.card(selected=True))
chip.setStyleSheet(T_qss.chip("success"))

# 摄像头画面上的状态文字（强对比 + 主色）
status.setStyleSheet(T_qss.overlay_pill("title", color=T.success, strong=True))
```

---

## 5 · UI 组件（`libs.ui`）

| 组件 | 一句话用法 |
|---|---|
| `AppFrame` | **必用根容器**；自带浅色背景 + 4 角占位 + 标题。子类 `resizeEvent` 必须先 `super().resizeEvent(ev)` |
| `TitleLabel` / `SubtitleLabel` / `BodyLabel` / `HintLabel` / `CaptionLabel` | 5 级文字。统一字号 + 主色；可 `.setColor(T.accent)` 临时改色 |
| `CardPanel(parent, selected=False)` | 圆角白底卡片容器 |
| `InfoRow(label, value, parent)` | 一行两列信息条，适合 About 类页面 |
| `StatusChip("已连接", state="success")` | 状态色块，`state ∈ default/success/warning/danger` |
| `ScrollList` | 纵向滚动列表，适合 wifi / 蓝牙列表 |
| `CameraOverlay` | 摄像头全屏 + 主题化叠层（处理好 z-order 与压暗对比度）|
| `AppFrame.setCornerHints(tl=, tr=, bl=, br=)` | 一次设 4 角；值可为字符串或 `(text, icon_path)` 元组 |

### 5.1 AppFrame 角标布局规则

- **左角**：图标在左 + 文字在右（如 `[←] Back`）
- **右角**：文字在左 + 图标在右（如 `Confirm [⏎]`）
- 角标**贴边**（pad=0），不要再人工挪位置
- 自动随窗口变化重排，子类无需写定位代码

### 5.2 settings 子应用的角标约定

为避免在不同 app 里出现"返回 / 确认"位置不一致：

- **左下角 = 退出**（`Asset.icon_back`，按键 `Key_Back`）
- **右下角 = 保存 / 确认**（`Asset.icon_enter`，按键 `Key_Return`）

---

## 6 · 双管线：Qt 与 PIL 同源

子应用里有两类页面：

1. **纯 Qt widget 页面**（settings/ 的所有页面）：用 `Color` + `qss.text/card/chip` 即可。
2. **PIL 自绘 + Qt overlay 混合页面**（network/ 的国家列表 / WiFi 列表 / 软键盘 / 二维码框）：摄像头帧由 PIL 画，然后转成 `QPixmap` 贴到 `QLabel` 上；同时还有 `QLabel` 状态文字浮在画面上。

第二类要保证"PIL 文字"和"Qt 文字"看起来是一套设计语言，做法：

| 维度 | Qt 侧 | PIL 侧 |
|---|---|---|
| 颜色 | `Color.accent`（CSS 字符串）| `ColorRGB.accent`（RGB 元组）|
| 字体 | `Font.family` + `apply_app_palette` | `ImageFont.truetype(Asset.font_path, Font.body)` |
| 半透明胶囊 | `qss.overlay_pill(...)` | `draw.rectangle(..., fill=ColorRGB.overlay_dim)` + 反白文字 |
| 角标 | `setCornerHints(...)` 自动布局 | `_draw_corners` 用同样的字号 + `ColorRGB.text_invert` 写在画面 4 角 |

只要两边都引主题模块，色板就天然对齐。**不要**在 PIL 里写 `(0, 255, 0)` 这种"绿色框"，应该 `ColorRGB.success`；不要写 `(255, 200, 0)`，应该 `ColorRGB.warning`。

---

## 7 · 启动入口（必加）

```python
from libs.theme import apply_app_palette

app = QApplication(sys.argv)
apply_app_palette(app)        # 全局字体 + 调色板 + 滚动条
```

不加这一行，本机系统默认字体（DejaVu）会带歪整套排版。

---

## 8 · 新建 app 模板（最小骨架）

复制即跑。已经包含背景、4 角提示、标题、入口调色板：

```python
import sys
from pathlib import Path
LUWU_ROOT = Path("/home/pi/luwu-os")
if str(LUWU_ROOT) not in sys.path:
    sys.path.insert(0, str(LUWU_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from libs.theme import apply_app_palette, Asset, Color as T
from libs.ui import AppFrame, BodyLabel


class HelloPage(AppFrame):
    def __init__(self):
        super().__init__()
        self.setTitle("Hello Luwu")
        self.body = BodyLabel("正文文字示例，自动用主题字号与深蓝主色。", self)
        self.setCornerHints(
            tl="选项",
            tr=("帮助", Asset.icon_right),
            bl=("退出", Asset.icon_back),
            br=("保存", Asset.icon_enter),
        )

    def resizeEvent(self, ev):
        super().resizeEvent(ev)       # 必须：父类负责标题与 4 角布局
        w, h = self.width(), self.height()
        self.body.setGeometry(20, h // 2 - 12, w - 40, 24)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    apply_app_palette(app)
    w = HelloPage()
    w.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

---

## 9 · 摄像头/全屏画面 app 模板

`network/` 是这种 app 的样板：摄像头帧铺满，文字浮在画面上。**不**继承 `AppFrame`（它会去画自己的浅色背景图，被 camera_label 盖掉就浪费），而是直接用 `overlay_pill` / `corner_pill` 给 QLabel 上样式：

```python
from libs.theme import (
    apply_app_palette, qss as T_qss,
    Color as T_Color, ColorRGB as T_RGB, Asset as T_Asset,
)

class CamPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background-color: rgb{T_RGB.canvas_dark};")

        self.camera_label = QLabel(self)         # 摄像头帧
        self.camera_label.lower()

        self.status_label = QLabel("扫描中...", self)
        self.status_label.setStyleSheet(T_qss.overlay_pill("title", strong=True))

        self.hint_label = QLabel("把二维码对准画面", self)
        self.hint_label.setStyleSheet(T_qss.overlay_pill("hint"))

        corner_style = T_qss.corner_pill()
        for lbl in (self.corner_tl, self.corner_tr, self.corner_bl, self.corner_br):
            lbl.setStyleSheet(corner_style)
```

PIL 自绘画面里：

```python
from PIL import Image, ImageDraw, ImageFont
from libs.theme import ColorRGB as T_RGB, Asset as T_Asset

bg = Image.new("RGB", (320, 240), T_RGB.canvas_dark)
draw = ImageDraw.Draw(bg)
font = ImageFont.truetype(T_Asset.font_path, 14)

draw.text((10, 10), "WiFi", font=font, fill=T_RGB.text_invert)
draw.rectangle([10, 30, 310, 56], fill=T_RGB.list_highlight)   # 选中条
draw.text((20, 36), ssid, font=font, fill=T_RGB.text_invert)

# 信号强度走语义色
sig_color = (T_RGB.success if rssi > 60
             else T_RGB.warning if rssi > 30
             else T_RGB.danger)
```

---

## 10 · 改造已有 app 的检查清单

- [ ] 顶部 `sys.path.insert(0, "/home/pi/luwu-os")` + `from libs.theme import ...` + `from libs.ui import ...`
- [ ] 所有 `class XxxPage(QWidget)` → `class XxxPage(AppFrame)`（纯 widget app）
- [ ] 删除所有 `self.setStyleSheet("background-color: #...;")`
- [ ] 删除手写的 `corner_tl/tr/bl/br QLabel` 及其 `resizeEvent` 定位
- [ ] 改用 `self.setCornerHints(tl=, tr=, bl=, br=)`
- [ ] `QLabel + setFont + setStyleSheet("color: #xxx; ...")` 三连 → 换 `TitleLabel/SubtitleLabel/BodyLabel/HintLabel/CaptionLabel`
- [ ] 自绘 `QPainter` 中的 `QColor("#xxxxxx")` 全部走 `QColor(Color.xxx)`
- [ ] PIL `ImageDraw` 中的 `(r, g, b)` 颜色全部走 `ColorRGB.xxx`
- [ ] PIL `ImageFont.truetype` 字体路径走 `Asset.font_path`
- [ ] `main()` 加 `apply_app_palette(app)`
- [ ] `resizeEvent` 第一行必须 `super().resizeEvent(ev)`，否则 `AppFrame` 角标不重排
- [ ] 切语言钩子 `refresh_language(self)` 内重新调一次 `setCornerHints(...)`，保证多语言同步
- [ ] 用 `grep -nE 'setStyleSheet|#[0-9a-fA-F]{6}|rgba?\(|fill=\(\s*\d|outline=\(\s*\d' apps/<your_app>/main.py` 自查，结果应全部走主题函数 / 主题常量

---

## 11 · 参考样板

| 样板 | 路径 | 适用场景 |
|---|---|---|
| 纯 Qt widget app | [apps/settings/main.py](../apps/settings/main.py) | 含 list / about / sn / volume / language / contact / download / time / shutdown / reboot 共 10 个子页面，全部继承 `AppFrame`，0 处硬编码颜色 |
| Qt + PIL 混合 app | [apps/network/main.py](../apps/network/main.py) | 摄像头全屏 + PIL 国家列表 / WiFi 列表 / 软键盘 / 二维码框；展示双管线统一色板的用法 |

---

## 12 · 扩展主题的原则

如果一个 app 的需求**确实**主题里没有覆盖的颜色 / 字号 / 间距 / 资源，**不要**在 app 里硬编码，而是去扩展 `libs/theme`：

1. 先判断是不是已有 token 能复用（比如想要"半透明深底"就是 `Color.overlay_pill_bg`）。
2. 如果是新语义，加到 `tokens.py` 对应的类里，**同时**在 `Color` 和 `ColorRGB` 都加（两种形态成对存在）。
3. 如果是新组合样式，加到 `qss.py` 作为新工厂函数。
4. 如果是新 widget 组件，加到 `libs/ui` 而不是 app 本地。
5. 加完跑一次 `python3 -c "from libs.theme import *"` 冒烟。
6. 如果改了向下兼容的旧 token（如调色），跑 settings 和 network 两个样板验证视觉无回归。

主题层是**契约**，不是仓库；它的尺寸由谁需要决定，但谁都不能绕过它。
