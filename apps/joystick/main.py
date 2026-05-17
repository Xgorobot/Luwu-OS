#!/usr/bin/env python3
"""
PySide6 手柄控制 (Joystick Control) — 由 Luwu OS launcher 启动。
读取 /dev/input/js* 手柄设备，控制 XGO 机器狗运动。
C 键（左下物理键 → KEY_BACK）退出。
"""
import os
import sys
import time
import struct
import signal
import threading

# ===================== 阶段计时 =====================
T0 = time.monotonic()
_stages = []


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[joystick][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== 重载导入 =====================
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QFont, QKeyEvent, QColor, QPalette, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFrame,
)

mark("PySide6 import done")

# ===================== 狗库 =====================
sys.path.insert(0, "/home/pi/lib")
from xgolib import XGO

mark("xgolib import done")

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "title": "🎮 手柄控制",
            "joystick": "手柄",
            "dog": "机器狗",
            "joystick_connected": "手柄 已连接",
            "joystick_disconnected": "手柄 未连接",
            "dog_ready": "机器狗 就绪",
            "dog_offline": "机器狗 离线",
            "step": "步幅",
            "pace": "步频",
            "height": "高度",
            "pace_slow": "慢",
            "pace_med": "中",
            "pace_fast": "快",
            "hint_exit": "C: 退出",
            "hint_action": "START: 复位   SELECT: 跨障",
        },
        "en": {
            "title": "🎮 Gamepad",
            "joystick": "Pad",
            "dog": "Dog",
            "joystick_connected": "Pad: Connected",
            "joystick_disconnected": "Pad: Disconnected",
            "dog_ready": "Dog: Ready",
            "dog_offline": "Dog: Offline",
            "step": "Step",
            "pace": "Pace",
            "height": "Height",
            "pace_slow": "Slow",
            "pace_med": "Med",
            "pace_fast": "Fast",
            "hint_exit": "C: Exit",
            "hint_action": "START: Reset   SELECT: Climb",
        },
    })
except Exception:
    _T = lambda k, *a: k

# ===================== 常量 =====================
AUTO_EXIT_SEC = 600  # 10 分钟无操作自动退出


# ===================== 手柄读取类 =====================
class JoystickReader:
    """读取 Linux /dev/input/js* 手柄设备。"""

    # 按钮映射
    BUTTON_NAMES = {
        0x0100: "A",
        0x0101: "B",
        0x0102: "X",
        0x0103: "Y",
        0x0104: "L1",
        0x0105: "R1",
        0x0106: "SELECT",
        0x0107: "START",
        0x0108: "MODE",
        0x0109: "BTN_RK1",
        0x010A: "BTN_RK2",
    }

    # 轴映射
    AXIS_NAMES = {
        0x0200: "RK1_LEFT_RIGHT",
        0x0201: "RK1_UP_DOWN",
        0x0202: "L2",
        0x0203: "RK2_LEFT_RIGHT",
        0x0204: "RK2_UP_DOWN",
        0x0205: "R2",
        0x0206: "WSAD_LEFT_RIGHT",
        0x0207: "WSAD_UP_DOWN",
    }

    def __init__(self, js_id=0):
        self._js_id = js_id
        self._jsdev = None
        self._connected = False
        self._running = False
        self._thread = None
        self._last_reconnect_attempt = 0  # 上次重连尝试时间

        # 状态缓存
        self.button_states = {name: 0 for name in self.BUTTON_NAMES.values()}
        self.axis_states = {name: 0.0 for name in self.AXIS_NAMES.values()}

        self._try_open()

    def _try_open(self):
        """尝试打开手柄设备。"""
        js_path = f"/dev/input/js{self._js_id}"
        try:
            self._jsdev = open(js_path, "rb")
            self._connected = True
            print(f"[joystick] 手柄已连接: {js_path}", flush=True)
        except Exception:
            self._connected = False
            print(f"[joystick] 未找到手柄: {js_path}", flush=True)

    @property
    def connected(self):
        return self._connected

    def start(self):
        """启动后台读取线程。"""
        if not self._connected:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止读取。"""
        self._running = False
        if self._jsdev:
            try:
                self._jsdev.close()
            except Exception:
                pass
            self._jsdev = None
        self._connected = False

    def _read_loop(self):
        """后台持续读取手柄事件。"""
        while self._running and self._connected:
            try:
                evbuf = self._jsdev.read(8)
                if evbuf:
                    t, value, etype, number = struct.unpack("IhBB", evbuf)
                    func = (etype << 8) | number

                    if func in self.BUTTON_NAMES:
                        name = self.BUTTON_NAMES[func]
                        self.button_states[name] = value
                    elif func in self.AXIS_NAMES:
                        name = self.AXIS_NAMES[func]
                        self.axis_states[name] = value / 32767.0
            except BlockingIOError:
                time.sleep(0.01)
            except Exception as e:
                print(f"[joystick] 读取错误: {e}", flush=True)
                self._connected = False
                break

    def try_reconnect(self):
        """尝试重连手柄（每 2 秒最多一次）。"""
        now = time.monotonic()
        if now - self._last_reconnect_attempt < 2.0:
            return
        self._last_reconnect_attempt = now
        if not self._connected:
            self._try_open()
            if self._connected:
                self.start()


# ===================== 机器狗控制 =====================
class DogController:
    """将手柄输入翻译为机器狗控制指令。"""

    STEP_SCALE_X = 0.25
    STEP_SCALE_Y = 0.2
    STEP_SCALE_Z = 0.7

    def __init__(self):
        self._dog = None
        self._step_control = 70
        self._pace_freq = 2
        self._height = 105
        self._play_ball = 0
        self._crossing_state = False
        self._init_dog()

    def _init_dog(self):
        try:
            self._dog = XGO()
            print("[joystick] XGO 机器狗初始化成功", flush=True)
        except Exception as e:
            self._dog = None
            print(f"[joystick] XGO 初始化失败: {e}", flush=True)

    @property
    def dog_available(self):
        return self._dog is not None

    def _my_map(self, x, in_min, in_max, out_min, out_max):
        return (out_max - out_min) * (x - in_min) / (in_max - in_min) + out_min

    def reset(self):
        if self._dog:
            try:
                self._dog.reset()
            except Exception:
                pass
        self._step_control = 70
        self._pace_freq = 2
        self._height = 105
        self._crossing_state = False

    def process_event(self, name, value):
        """处理单个手柄事件。"""
        if not self._dog:
            return

        try:
            if name == "RK1_LEFT_RIGHT":
                v = -value
                if self._crossing_state:
                    return
                fvalue = int(self._step_control * self.STEP_SCALE_Y * v)
                self._dog.move("y", fvalue)

            elif name == "RK1_UP_DOWN":
                v = -value
                if self._crossing_state:
                    return
                fvalue = int(self._step_control * self.STEP_SCALE_X * v)
                self._dog.move("x", fvalue)

            elif name == "RK2_UP_DOWN":
                v = -value
                if self._crossing_state:
                    return
                if v == 0:
                    self._dog.turn(0)
                elif abs(v) > 0.9:
                    fvalue = int(self._my_map(self._step_control, 0, 100, 20, self.STEP_SCALE_Z * 100)) * (1 if v > 0 else -1)
                    self._dog.turn(fvalue)

            elif name == "RK2_LEFT_RIGHT":
                v = value
                if self._crossing_state:
                    return
                fvalue = int(v * 15)
                self._dog.attitude("p", fvalue)

            elif name == "A":
                if value == 1 and not self._crossing_state:
                    self._height = max(75, self._height - 10)
                    self._dog.translation("z", self._height)

            elif name == "B":
                if value == 1:
                    self._dog.attitude("y", -35)
                else:
                    self._dog.attitude("r", 0)
                    self._dog.attitude("y", 0)

            elif name == "X":
                if value == 1:
                    self._dog.attitude("y", 35)
                else:
                    self._dog.attitude("r", 0)
                    self._dog.attitude("y", 0)

            elif name == "Y":
                if value == 1 and not self._crossing_state:
                    self._height = min(115, self._height + 10)
                    self._dog.translation("z", self._height)

            elif name == "L1":
                if value == 1 and not self._crossing_state:
                    self._dog.action(10)

            elif name == "R1":
                if value == 1 and not self._crossing_state:
                    self._dog.action(11)

            elif name == "SELECT":
                if value == 1:
                    if not self._crossing_state:
                        self._crossing_state = True
                        self._dog.gait_type("high_walk")
                        time.sleep(0.01)
                        self._dog.pace("slow")
                        time.sleep(0.01)
                        self._dog.translation("z", 95)
                        time.sleep(0.01)
                        self._dog.forward(25)
                    else:
                        self.reset()

            elif name == "START":
                if value == 1:
                    self.reset()

            elif name == "BTN_RK1":
                if value == 1:
                    self._step_control += 30
                    if self._step_control > 100:
                        self._step_control = 40

            elif name == "BTN_RK2":
                if value == 1:
                    self._pace_freq += 1
                    if self._pace_freq > 3:
                        self._pace_freq = 1
                    pace_map = {1: "slow", 2: "normal", 3: "high"}
                    self._dog.pace(pace_map.get(self._pace_freq, "normal"))

            elif name == "L2":
                v = (value + 1) / 2
                if v > 0.95:
                    self._dog.action(16)

            elif name == "R2":
                v = (value + 1) / 2
                if v > 0.95:
                    self._dog.action(11)

            elif name == "WSAD_LEFT_RIGHT":
                v = -value
                if self._crossing_state:
                    return
                fvalue = v * self._step_control * self.STEP_SCALE_Y
                self._dog.move("y", fvalue)

            elif name == "WSAD_UP_DOWN":
                v = -value
                if self._crossing_state:
                    return
                fvalue = int(v * self._step_control * self.STEP_SCALE_X)
                self._dog.move("x", fvalue)

        except Exception as e:
            print(f"[joystick] 控制错误 ({name}={value}): {e}", flush=True)

    @property
    def step_control(self):
        return self._step_control

    @property
    def pace_freq(self):
        return self._pace_freq

    @property
    def height(self):
        return self._height


# ===================== 自绘小控件 =====================
COLOR_BG = "#0b1124"
COLOR_PANEL = "#161c38"
COLOR_PANEL_BORDER = "#232a4a"
COLOR_GRID = "#22284a"
COLOR_TEXT = "#e6ecff"
COLOR_TEXT_DIM = "#8892c9"
COLOR_TEXT_FAINT = "#5c6a9c"
COLOR_ACCENT = "#18df6b"
COLOR_ACCENT2 = "#3a7bd5"
COLOR_DANGER = "#ff6b6b"


class StickIndicator(QWidget):
    """圆形摇杆指示器：外圈 + 十字 + 当前位置圆点。"""

    def __init__(self, label: str, size: int = 70, parent=None):
        super().__init__(parent)
        self._label = label
        self._x = 0.0
        self._y = 0.0
        self.setFixedSize(size, size + 14)

    def set_position(self, x: float, y: float):
        nx = max(-1.0, min(1.0, x))
        ny = max(-1.0, min(1.0, y))
        if abs(nx - self._x) > 0.01 or abs(ny - self._y) > 0.01:
            self._x, self._y = nx, ny
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        circle_h = self.height() - 14
        cx, cy = w / 2, circle_h / 2
        r = min(w, circle_h) / 2 - 3

        # 外圈背景
        p.setPen(QPen(QColor(COLOR_PANEL_BORDER), 1))
        p.setBrush(QBrush(QColor(COLOR_PANEL)))
        p.drawEllipse(QPointF(cx, cy), r, r)
        # 十字参考线
        p.setPen(QPen(QColor(COLOR_GRID), 1))
        p.drawLine(int(cx - r + 3), int(cy), int(cx + r - 3), int(cy))
        p.drawLine(int(cx), int(cy - r + 3), int(cx), int(cy + r - 3))

        # 点
        active = abs(self._x) > 0.05 or abs(self._y) > 0.05
        dot_r = 6
        px = cx + self._x * (r - dot_r)
        py = cy - self._y * (r - dot_r)
        if active:
            # 轨迹线
            p.setPen(QPen(QColor(COLOR_ACCENT), 1.5))
            p.drawLine(QPointF(cx, cy), QPointF(px, py))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(COLOR_ACCENT) if active else QColor(COLOR_TEXT_FAINT)))
        p.drawEllipse(QPointF(px, py), dot_r, dot_r)

        # 标签
        p.setPen(QColor(COLOR_TEXT_DIM if active else COLOR_TEXT_FAINT))
        f = QFont()
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(0, circle_h, w, 14, Qt.AlignmentFlag.AlignCenter, self._label)


class TriggerBar(QWidget):
    """扫机扬机型竖直进度条（用于 L2 / R2）。"""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._value = 0.0  # 0 ~ 1
        self.setFixedSize(22, 70)

    def set_value(self, v: float):
        nv = max(0.0, min(1.0, v))
        if abs(nv - self._value) > 0.01:
            self._value = nv
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        label_h = 12
        bar_y, bar_h = label_h + 2, h - label_h - 2

        # 背景槽
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(COLOR_PANEL))
        p.drawRoundedRect(0, bar_y, w, bar_h, 4, 4)
        # 填充
        fill_h = int(bar_h * self._value)
        if fill_h > 0:
            color = QColor(COLOR_ACCENT) if self._value > 0.95 else QColor(COLOR_ACCENT2)
            p.setBrush(color)
            p.drawRoundedRect(0, bar_y + bar_h - fill_h, w, fill_h, 4, 4)
        # 标签
        p.setPen(QColor(COLOR_TEXT_DIM))
        f = QFont()
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(0, 0, w, label_h, Qt.AlignmentFlag.AlignCenter, self._label)


def _make_panel(border_radius: int = 8) -> QFrame:
    f = QFrame()
    f.setStyleSheet(
        f"QFrame {{ background-color: {COLOR_PANEL}; border: 1px solid {COLOR_PANEL_BORDER};"
        f" border-radius: {border_radius}px; }}"
    )
    return f


# ===================== PySide6 页面 =====================
class JoystickPage(QWidget):
    """手柄控制 LCD 界面 (320x240)。"""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background-color: {COLOR_BG};")
        self._first_paint_logged = False

        self._js = JoystickReader(js_id=0)
        self._controller = DogController()

        # ---- 顶部状态栏 ----
        self.js_dot = QLabel("●")
        self.js_dot.setStyleSheet(f"color: {COLOR_DANGER}; font-size: 14px;")
        self.js_text = QLabel(_T("joystick"))
        self.js_text.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")

        self.title = QLabel(_T("title"))
        tf = QFont(); tf.setPointSize(13); tf.setBold(True)
        self.title.setFont(tf)
        self.title.setStyleSheet(f"color: {COLOR_TEXT};")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.dog_text = QLabel(_T("dog"))
        self.dog_text.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        self.dog_dot = QLabel("●")
        self.dog_dot.setStyleSheet(f"color: {COLOR_DANGER}; font-size: 14px;")

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(4)
        top_bar.addWidget(self.js_dot)
        top_bar.addWidget(self.js_text)
        top_bar.addStretch(1)
        top_bar.addWidget(self.title)
        top_bar.addStretch(1)
        top_bar.addWidget(self.dog_text)
        top_bar.addWidget(self.dog_dot)

        # ---- 左侧：摇杆区 ----
        self.stick_rk1 = StickIndicator("RK1", size=64)
        self.stick_rk2 = StickIndicator("RK2", size=64)
        self.stick_wsad = StickIndicator("WSAD", size=52)
        self.bar_l2 = TriggerBar("L2")
        self.bar_r2 = TriggerBar("R2")

        sticks_row = QHBoxLayout()
        sticks_row.setSpacing(6)
        sticks_row.setContentsMargins(0, 0, 0, 0)
        sticks_row.addWidget(self.bar_l2, 0, Qt.AlignmentFlag.AlignVCenter)
        sticks_row.addWidget(self.stick_rk1, 0, Qt.AlignmentFlag.AlignVCenter)
        sticks_row.addWidget(self.stick_rk2, 0, Qt.AlignmentFlag.AlignVCenter)
        sticks_row.addWidget(self.bar_r2, 0, Qt.AlignmentFlag.AlignVCenter)

        wsad_wrap = QHBoxLayout()
        wsad_wrap.setContentsMargins(0, 0, 0, 0)
        wsad_wrap.addStretch(1)
        wsad_wrap.addWidget(self.stick_wsad)
        wsad_wrap.addStretch(1)

        left_panel = _make_panel()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 4, 6, 4)
        left_layout.setSpacing(2)
        left_layout.addLayout(sticks_row)
        left_layout.addLayout(wsad_wrap)

        # ---- 右侧：按钮区 + 参数 ----
        self.btn_labels = {}
        btn_grid = QGridLayout()
        btn_grid.setSpacing(3)
        btn_grid.setContentsMargins(0, 0, 0, 0)
        # ABXY 采用颜色区分
        abxy_colors = {
            "A": "#2fbf71", "B": "#e74c3c",
            "X": "#3498db", "Y": "#f1c40f",
        }
        layout_def = [
            ("L1", 0, 0), ("R1", 0, 1), ("SELECT", 0, 2), ("START", 0, 3),
            ("X", 1, 0), ("Y", 1, 1), ("RK1", 1, 2), ("RK2", 1, 3),
            ("A", 2, 0), ("B", 2, 1), ("MODE", 2, 2),
        ]
        for name, r, c in layout_def:
            lbl = QLabel(name)
            lbl.setFixedSize(36, 18)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            color = abxy_colors.get(name)
            lbl.setProperty("_color", color)
            lbl.setStyleSheet(self._btn_style(False, color))
            self.btn_labels[name] = lbl
            btn_grid.addWidget(lbl, r, c)

        # 参数卡片
        self.lbl_step = QLabel("70")
        self.lbl_pace = QLabel(_T("pace_med"))
        self.lbl_height = QLabel("105")
        params_row = QHBoxLayout()
        params_row.setSpacing(3)
        params_row.setContentsMargins(0, 0, 0, 0)
        for tag, val in ((_T("step"), self.lbl_step), (_T("pace"), self.lbl_pace), (_T("height"), self.lbl_height)):
            cell = QFrame()
            cell.setStyleSheet(
                f"QFrame {{ background-color: {COLOR_PANEL_BORDER}; border-radius: 4px; }}"
            )
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(2, 1, 2, 1)
            cl.setSpacing(0)
            t = QLabel(tag)
            t.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 9px; background: transparent;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet(f"color: {COLOR_ACCENT}; font-size: 12px; font-weight: bold; background: transparent;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(t)
            cl.addWidget(val)
            params_row.addWidget(cell)

        right_panel = _make_panel()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 4, 6, 4)
        right_layout.setSpacing(4)
        right_layout.addLayout(btn_grid)
        right_layout.addLayout(params_row)

        body = QHBoxLayout()
        body.setSpacing(6)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(left_panel, 5)
        body.addWidget(right_panel, 4)

        # ---- 底部提示 ----
        hint_style = f"color: {COLOR_TEXT_FAINT}; font-size: 10px; background: transparent;"
        self.hint_bl = QLabel(_T("hint_exit"))
        self.hint_bl.setStyleSheet(hint_style)
        self.hint_br = QLabel(_T("hint_action"))
        self.hint_br.setStyleSheet(hint_style)
        self.hint_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self.hint_bl)
        bottom.addStretch(1)
        bottom.addWidget(self.hint_br)

        # ---- 主布局 ----
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(5)
        main_layout.addLayout(top_bar)
        main_layout.addLayout(body, 1)
        main_layout.addLayout(bottom)

        # ---- 定时器 ----
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_ui)
        self._refresh_timer.start(60)  # ~16fps

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_joystick)
        self._poll_timer.start(20)

        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._js.start()

    @staticmethod
    def _btn_style(pressed: bool, color: str | None) -> str:
        if pressed:
            bg = color if color else COLOR_ACCENT
            return (
                f"color: #fff; background-color: {bg}; border-radius: 4px;"
                f" font-size: 10px; font-weight: bold;"
            )
        accent = color if color else COLOR_TEXT_FAINT
        return (
            f"color: {accent}; background-color: {COLOR_PANEL_BORDER};"
            f" border-radius: 4px; font-size: 10px; font-weight: bold;"
        )

    def _poll_joystick(self):
        if not self._js.connected:
            self._js.try_reconnect()
            return
        for name, value in self._js.button_states.items():
            if value != 0:
                self._controller.process_event(name, value)
        for name, value in self._js.axis_states.items():
            self._controller.process_event(name, value)

    def _refresh_ui(self):
        # 状态点
        if self._js.connected:
            self.js_dot.setStyleSheet(f"color: {COLOR_ACCENT}; font-size: 14px;")
            self.js_text.setText(_T("joystick_connected"))
        else:
            self.js_dot.setStyleSheet(f"color: {COLOR_DANGER}; font-size: 14px;")
            self.js_text.setText(_T("joystick_disconnected"))

        if self._controller.dog_available:
            self.dog_dot.setStyleSheet(f"color: {COLOR_ACCENT}; font-size: 14px;")
            self.dog_text.setText(_T("dog_ready"))
        else:
            self.dog_dot.setStyleSheet(f"color: {COLOR_DANGER}; font-size: 14px;")
            self.dog_text.setText(_T("dog_offline"))

        # 摇杆位置
        ax = self._js.axis_states
        self.stick_rk1.set_position(ax.get("RK1_LEFT_RIGHT", 0), -ax.get("RK1_UP_DOWN", 0))
        self.stick_rk2.set_position(ax.get("RK2_LEFT_RIGHT", 0), -ax.get("RK2_UP_DOWN", 0))
        self.stick_wsad.set_position(ax.get("WSAD_LEFT_RIGHT", 0), -ax.get("WSAD_UP_DOWN", 0))
        # 扬机：L2 / R2 原始值范围 -1~1，转为 0~1
        self.bar_l2.set_value((ax.get("L2", -1.0) + 1) / 2)
        self.bar_r2.set_value((ax.get("R2", -1.0) + 1) / 2)

        # 按钮高亮
        btn_key_map = {
            "A": "A", "B": "B", "X": "X", "Y": "Y",
            "L1": "L1", "R1": "R1",
            "SELECT": "SELECT", "START": "START", "MODE": "MODE",
            "RK1": "BTN_RK1", "RK2": "BTN_RK2",
        }
        for display_name, internal_name in btn_key_map.items():
            lbl = self.btn_labels.get(display_name)
            if not lbl:
                continue
            pressed = bool(self._js.button_states.get(internal_name, 0))
            color = lbl.property("_color")
            lbl.setStyleSheet(self._btn_style(pressed, color))

        # 参数卡片
        pace_names = {1: _T("pace_slow"), 2: _T("pace_med"), 3: _T("pace_fast")}
        self.lbl_step.setText(str(self._controller.step_control))
        self.lbl_pace.setText(pace_names.get(self._controller.pace_freq, _T("pace_med")))
        self.lbl_height.setText(str(self._controller.height))

    # ---- 首帧日志 ----
    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")
            summary = self._stage_summary()
            print("[joystick] boot breakdown:\n" + summary, flush=True)

    def _stage_summary(self) -> str:
        lines = []
        prev = 0.0
        for name, ms in _stages:
            lines.append(f"{name}: {ms:.0f}ms (+{ms - prev:.0f})")
            prev = ms
        return " | ".join(lines)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            print("[joystick] KEY_BACK -> exit", flush=True)
            self.close()

    def closeEvent(self, ev):
        print("[joystick] closing", flush=True)
        self._poll_timer.stop()
        self._refresh_timer.stop()
        self._js.stop()
        self._controller.reset()
        super().closeEvent(ev)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = JoystickPage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()

    print(f"[joystick] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
