#!/usr/bin/env python3
"""
PySide6 2.4G 遥控 — Luwu OS 统一手柄控制。
使用 gamepad_controller 读取 evdev 手柄设备，控制 XGO 机器狗。

与蓝牙页面共享同一套 UI 风格（居中布局 + 状态驱动）。

按键：
- C 键（Key_Back）：退出
- D 键（Key_Return）：切换蓝牙模式（由 gamepad 入口拦截）
- A 键（Key_Left）：键位映射 QR 页
"""
import os
import sys
import time
import signal
import threading

# ===================== 阶段计时 =====================
T0 = time.monotonic()


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    print(f"[joystick][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== PySide6 =====================
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QFrame,
)

mark("PySide6 import done")

# ===================== 主题 =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")

from libs.theme import (  # noqa: E402
    apply_app_palette, Asset as T_Asset, Color as T_Color,
    Spacing, qss as T_qss,
)
from libs.ui import AppFrame  # noqa: E402
from libs.ui.frame import _invisible_cursor  # noqa: E402
from libs.i18n import Translator as _Translator  # noqa: E402

mark("theme import done")

# ===================== 键位映射相关 =====================
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# mapping_server 和 qr_page 统一从 bluetooth_gamepad 目录导入
# 与蓝牙页面共享同一个 mapping_server 模块实例，避免端口/全局状态冲突
_BT_GAMEPAD_DIR = "/home/pi/luwu-os/apps/bluetooth_gamepad"
if _BT_GAMEPAD_DIR not in sys.path:
    sys.path.insert(0, _BT_GAMEPAD_DIR)
import mapping_server  # noqa: E402
from qr_page import QRMappingPage  # noqa: E402

mark("mapping imports done")

# ===================== i18n =====================
_T = _Translator({
    "cn": {
        "title": "2.4G 遥控",
        "init": "正在初始化 2.4G 遥控...",
        "connected": "2.4G 已连接",
        "ready": "手柄就绪，正在控制机器狗",
        "disconnected": "2.4G 未连接，请插入接收器",
        "hint_exit": "退出",
        "hint_mapping": "键位映射",
        "hint_bt": "D 切换蓝牙",
        "key_mapping": "键位映射",
        "back": "返回",
    },
    "en": {
        "title": "2.4G Remote",
        "init": "Initializing 2.4G remote...",
        "connected": "2.4G Connected",
        "ready": "Gamepad ready, controlling robot",
        "disconnected": "2.4G Disconnected, insert receiver",
        "hint_exit": "Exit",
        "hint_mapping": "Key Map",
        "hint_bt": "D BT Mode",
        "key_mapping": "Key Mapping",
        "back": "Back",
    },
})

# ===================== 常量 =====================
AUTO_EXIT_SEC = 1800  # 30 分钟无操作自动退出
_LAUNCHER_ASSETS = os.path.dirname(T_Asset.bg_image)
DEMO_ICON = os.path.join(_LAUNCHER_ASSETS, "demo_gamepad.png")
_APP_BG_IMAGE = "/home/pi/luwu-os/assets/images/app_bg.png"

# ===================== XGO 单例 =====================
_xgo_instance = None
_xgo_device_type = None


def _ensure_xgo():
    """懒初始化 xgolib 单例，全局复用"""
    global _xgo_instance, _xgo_device_type
    if _xgo_instance is not None:
        return _xgo_instance, _xgo_device_type
    try:
        import xgolib
        print("[joystick] initializing xgolib (one-time)...", flush=True)
        _xgo_instance = xgolib.XGO()
        fw = getattr(_xgo_instance, "version", "")
        if fw and fw[0] == "R":
            _xgo_device_type = "xgorider"
        elif fw and fw[0] == "L":
            _xgo_device_type = "xgolite"
        else:
            _xgo_device_type = "xgomini"
        print(f"[joystick] xgolib ready: {_xgo_device_type} (fw={fw})", flush=True)
    except ImportError:
        print("[joystick] xgolib not installed, debug mode", flush=True)
    except Exception as e:
        print(f"[joystick] xgolib init failed: {e}", flush=True)
    return _xgo_instance, _xgo_device_type


# ===================== 控制器线程 =====================
class ControllerThread(threading.Thread):
    """后台运行 gamepad_controller.py 中的 XGOController"""

    def __init__(self):
        super().__init__(daemon=True, name="joystick-gamepad-ctrl")
        self._controller = None
        self._device_name = ""

    @property
    def connected(self) -> bool:
        c = self._controller
        return c is not None and c._running and c._gamepad_dev is not None

    @property
    def device_name(self) -> str:
        return self._device_name

    def run(self):
        try:
            gp_dir = "/home/pi/luwu-os/libs/gamepad_config"
            if gp_dir not in sys.path:
                sys.path.insert(0, gp_dir)
            import gamepad_controller as gc
            gc.CONFIG_FILE = os.path.join(gp_dir, "mappings.json")
            self._controller = gc.XGOController()
            xgo, dev_type = _ensure_xgo()
            if xgo:
                self._controller.xgo = xgo
                self._controller.device_type = dev_type
            else:
                self._controller._init_xgo()
            self._controller._load_mapping()
            self._controller._running = True
            self._controller._start_config_watcher()
            self._run_gamepad_loop()
        except Exception as e:
            print(f"[joystick] controller error: {e}", flush=True)
            import traceback
            traceback.print_exc()

    def _run_gamepad_loop(self):
        c = self._controller
        import gamepad_controller as gc
        while c._running:
            dev = c._find_gamepad()
            if not dev:
                self._device_name = ""
                gc.log.warning("未找到手柄，2 秒后重试...")
                time.sleep(2)
                continue
            self._device_name = dev.name
            c._gamepad_dev = dev
            if c._is_ble_gatt_gamepad(dev.name):
                gc.log.info(f"检测到 BLE GATT 手柄: {dev.name}，启用 BLE 路径")
                try:
                    c._run_ble_loop(dev)
                except Exception as e:
                    gc.log.error(f"[BLE] 循环异常: {e}")
                    time.sleep(1)
            else:
                try:
                    c._run_evdev_loop(dev)
                except Exception as e:
                    gc.log.error(f"[evdev] 循环异常: {e}")
                    time.sleep(1)
            c._gamepad_dev = None
            self._device_name = ""
            c._stop_movement()
            time.sleep(1)

    def stop(self):
        c = self._controller
        if not c:
            return
        try:
            c._running = False
            c._stop_movement()
            if c._gamepad_dev:
                try:
                    c._gamepad_dev.close()
                except Exception:
                    pass
                c._gamepad_dev = None
            self._device_name = ""
        except Exception as e:
            print(f"[joystick] controller stop error: {e}", flush=True)


# ===================== UI =====================
class JoystickPage(AppFrame):
    """2.4G 遥控界面 — 与蓝牙页面相同的居中布局风格"""

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._first_paint_logged = False
        self._exiting = False

        # 隐藏光标（手柄 App 不需要鼠标光标）
        self._cursor_timer.stop()
        self._cursor_hidden = True
        self.setCursor(_invisible_cursor())

        self._ctrl_thread: ControllerThread | None = None

        # ---- 标题 ----
        self.setTitle(_T("title"))

        # ---- QR 映射页（覆盖层，初始隐藏）----
        self._qr_page = QRMappingPage(self)
        self._qr_page.go_back = self._hide_qr_page
        self._qr_page.hide()

        # ---- 背景 ----
        _pix = QPixmap(_APP_BG_IMAGE)
        if not _pix.isNull():
            self._bg_pix = _pix
            self.update()

        # ---- 图标 ----
        self.icon_label = QLabel(self)
        pix = QPixmap(DEMO_ICON)
        if not pix.isNull():
            self.icon_label.setPixmap(pix.scaled(
                88, 88,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet(T_qss.transparent())

        # accent 装饰线
        self.accent_line = QFrame(self)
        self.accent_line.setFixedSize(60, 2)
        self.accent_line.setStyleSheet(
            f"background-color: {T_Color.accent}; border: none;"
        )

        # 设备名
        self.device_label = QLabel("", self)
        self.device_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.device_label.setStyleSheet(T_qss.text("subtitle"))

        # 状态 chip
        self.status_label = QLabel(_T("init"), self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(T_qss.chip("muted"))

        # 子状态（控制器是否启动）
        self.sub_label = QLabel("", self)
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setStyleSheet(T_qss.text("body", color=T_Color.accent))

        # ---- 主布局（垂直居中）----
        center = QWidget(self)
        center.setStyleSheet(T_qss.transparent())
        v = QVBoxLayout(center)
        v.setContentsMargins(0, 0, 0, 0)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.sm)
        v.addWidget(self.accent_line, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.md)
        v.addWidget(self.device_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.xs)
        v.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.xs)
        v.addWidget(self.sub_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self._center = center

        # ---- 角标 ----
        self.setCornerHints(
            tl=(_T("hint_mapping"), T_Asset.icon_left),
            bl=(_T("hint_exit"), T_Asset.icon_back),
            br=(_T("hint_bt"), T_Asset.icon_enter),
        )

        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)
        QTimer.singleShot(200, self._start_controller)

    # ---- 布局 ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        top = max(28, h * 14 // 100)
        bottom = max(20, h * 8 // 100)
        self._center.setGeometry(0, top, w, h - top - bottom)

        if self._qr_page:
            self._qr_page.setGeometry(0, 0, w, h)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")

    # ---- 控制器启停 ----
    def _start_controller(self):
        """启动手柄控制器线程"""
        # 启动键位映射 Web 服务器
        try:
            mapping_server.start_server()
        except Exception as e:
            print(f"[joystick] mapping server start failed: {e}", flush=True)

        if self._ctrl_thread and self._ctrl_thread.is_alive():
            return
        self._ctrl_thread = ControllerThread()
        self._ctrl_thread.start()
        print("[joystick] controller started", flush=True)
        # 启动状态轮询
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(1000)

    def _stop_controller(self):
        """停止手柄控制器"""
        if self._ctrl_thread:
            print("[joystick] stopping controller...", flush=True)
            self._ctrl_thread.stop()
        # 停止键位映射服务器
        try:
            mapping_server.stop_server()
        except Exception:
            pass
        print("[joystick] controller stopped", flush=True)

    def _poll_status(self):
        """每秒轮询控制器状态并更新 UI"""
        if not self._ctrl_thread or not self._ctrl_thread.is_alive():
            return
        if self._ctrl_thread.connected:
            name = self._ctrl_thread.device_name
            self.device_label.setText(name)
            self.status_label.setText(_T("connected"))
            self.status_label.setStyleSheet(T_qss.chip("success"))
            self.sub_label.setText(_T("ready"))
        else:
            self.device_label.setText("")
            self.status_label.setText(_T("disconnected"))
            self.status_label.setStyleSheet(T_qss.chip("danger"))
            self.sub_label.setText("")

    # ---- QR 映射页 ----
    def _show_qr_page(self):
        print("[joystick] showing QR mapping page", flush=True)
        if not mapping_server.is_running():
            try:
                mapping_server.start_server()
            except Exception as e:
                print(f"[joystick] mapping server start failed: {e}", flush=True)
        try:
            self._qr_page._generate()
        except Exception as e:
            print(f"[joystick] QR generate failed: {e}", flush=True)
        self._qr_page.show()
        self._qr_page.raise_()
        self._qr_page.setFocus()
        self._center.hide()
        self.icon_label.hide()
        self.accent_line.hide()
        self.device_label.hide()
        self.status_label.hide()
        self.sub_label.hide()
        for c in self._corners.values():
            c.hide()

    def _hide_qr_page(self):
        print("[joystick] hiding QR mapping page", flush=True)
        self._qr_page.hide()
        self._center.show()
        self.icon_label.show()
        self.accent_line.show()
        self.device_label.show()
        self.status_label.show()
        self.sub_label.show()
        for c in self._corners.values():
            c.show()
        self.setFocus()

    # ---- 按键 ----
    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()
        # QR 页面可见时优先处理返回
        if self._qr_page.isVisible():
            if key == Qt.Key.Key_Back:
                print("[joystick] C -> back from QR", flush=True)
                self._hide_qr_page()
                return
            self._qr_page.keyPressEvent(ev)
            return

        if key == Qt.Key.Key_Back:
            if self._exiting:
                return
            self._exiting = True
            print("[joystick] C -> exit", flush=True)
            self.close()
            QApplication.instance().quit()
        elif key == Qt.Key.Key_Left:
            # A 键 → 打开键位映射
            print("[joystick] A -> key mapping", flush=True)
            self._show_qr_page()

    # ---- 退出清理 ----
    def closeEvent(self, ev):
        print("[joystick] closing", flush=True)
        self._stop_controller()
        try:
            mapping_server.stop_server()
        except Exception:
            pass
        super().closeEvent(ev)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    apply_app_palette(app)
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
