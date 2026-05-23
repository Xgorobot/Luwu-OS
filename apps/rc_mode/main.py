#!/usr/bin/env python3
"""
PySide6 图传模式 (RC Mode) — 由 Luwu OS launcher 启动。
启动 Flask Web 服务器（视频流 + WebSocket 遥控），LCD 用 PySide6 显示 IP 地址。
C 键（左下物理键 → KEY_LEFT）退出。
"""
import os
import sys
import time
import signal
import socket
import struct
import fcntl
import threading

import cv2 as cv

# ===================== 阶段计时 =====================
T0 = time.monotonic()
_stages = []  # [(name, abs_ms)]


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[rc_mode][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== 重载导入 =====================
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
)

from flask import Flask, render_template, Response, jsonify
from flask_socketio import SocketIO, emit
from picamera2 import Picamera2
from concurrent.futures import ThreadPoolExecutor

# ---- 狗库 ----
sys.path.insert(0, "/home/pi/lib")
from xgolib import XGO, XGO_RIDER  # noqa: E402

# ---- 主题层 ----
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
from libs.theme import (  # noqa: E402
    apply_app_palette, Asset as T_Asset, Color as T_Color,
    Spacing, Radius, qss as T_qss,
)
from libs.ui import (  # noqa: E402
    AppFrame, BodyLabel, CaptionLabel, SubtitleLabel,
)

mark("imports done")

# ===================== 常量 =====================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
HTTP_PORT = 8080
AUTO_EXIT_SEC = 300  # 5 分钟无操作自动退出

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "title": "图传模式",
            "connected": "已连接 ({} 客户端)",
            "waiting": "等待连接...",
            "corner_exit": "",
        },
        "en": {
            "title": "RC Mode",
            "connected": "Connected ({} clients)",
            "waiting": "Waiting for connection...",
            "corner_exit": "",
        },
    })
except Exception:
    _T = lambda k, *a: k


# ===================== 网络工具 =====================
def get_ip(ifname: str = "wlan0") -> str:
    """获取指定网卡的 IPv4 地址。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            fcntl.ioctl(
                s.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack("256s", bytes(ifname[:15], "utf-8")),
            )[20:24]
        )
    except Exception:
        try:
            return get_ip("eth0")
        except Exception:
            return "0.0.0.0"


# ===================== 摄像头管理 =====================
class CameraHandler:
    """管理 Picamera2 生命周期，线程安全。"""

    def __init__(self, width: int = 640, height: int = 480):
        self._cam = None
        self._lock = threading.Lock()
        self._width = width
        self._height = height
        self._start()

    def _start(self):
        try:
            self._cam = Picamera2()
            config = self._cam.create_preview_configuration(
                main={"format": "RGB888", "size": (self._width, self._height)}
            )
            self._cam.configure(config)
            self._cam.start()
            print("[rc_mode] camera started OK", flush=True)
        except Exception as e:
            print(f"[rc_mode] camera start error: {e}", flush=True)

    def get_frame(self):
        """返回 np.ndarray (RGB888) 或 None。"""
        with self._lock:
            if self._cam is None:
                return None
            try:
                return self._cam.capture_array()
            except Exception as e:
                print(f"[rc_mode] camera capture error: {e}", flush=True)
                return None

    def stop(self):
        with self._lock:
            if self._cam:
                try:
                    self._cam.stop()
                    self._cam.close()
                except Exception:
                    pass
                self._cam = None
                print("[rc_mode] camera stopped", flush=True)


# ===================== Flask 服务 =====================
camera_handler = CameraHandler()

flask_app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, "templates"),
    static_folder=os.path.join(APP_DIR, "static"),
)
socketio = SocketIO(flask_app, cors_allowed_origins="*", async_mode="threading")

# ---- 执行器 ----
executor = ThreadPoolExecutor(max_workers=4)


def execute_action(action_func, *args):
    try:
        action_func(*args)
    except Exception as e:
        print(f"[rc_mode] action error: {e}", flush=True)


# ---- 狗（在 Flask 线程中可用） ----
dog = XGO()


# ---- 视频流生成器 ----
def video_handle():
    """MJPEG 视频流生成器。"""
    while True:
        frame = camera_handler.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        try:
            ret, img_encode = cv.imencode(".jpg", frame)
            if ret:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + img_encode.tobytes()
                    + b"\r\n"
                )
        except Exception as e:
            print(f"[rc_mode] video encode error: {e}", flush=True)
            time.sleep(0.05)


# ---- Flask 路由 ----
@flask_app.route("/")
def index():
    ip_address = get_ip("wlan0")
    return render_template("demo.html", device_ip=ip_address)


@flask_app.route("/camera")
def camera():
    return render_template("camera.html")


@flask_app.route("/video_feed")
def video_feed():
    return Response(
        video_handle(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@flask_app.route("/api/device_info")
def api_device_info():
    """返回当前设备类型，前端据此动态渲染 action 列表。"""
    if isinstance(dog, XGO_RIDER):
        device_type = "rider"
    else:
        device_type = "dog"
    return jsonify({"type": device_type})


# ---- 连接状态（线程安全） ----
_connected_clients = 0
_conn_lock = threading.Lock()


def get_connection_status() -> str:
    with _conn_lock:
        if _connected_clients > 0:
            return _T("connected", _connected_clients)
        return _T("waiting")


# ---- WebSocket 事件 ----
@socketio.on("connect")
def on_connect():
    global _connected_clients
    with _conn_lock:
        _connected_clients += 1
        count = _connected_clients
    print(f"[rc_mode] client connected (total: {count})", flush=True)


@socketio.on("disconnect")
def on_disconnect():
    global _connected_clients
    with _conn_lock:
        _connected_clients = max(0, _connected_clients - 1)
        count = _connected_clients
    print(f"[rc_mode] client disconnected (total: {count})", flush=True)


@socketio.on("balance")
def handle_balance(data):
    executor.submit(execute_action, dog.imu, int(data))


@socketio.on("reset")
def handle_reset(_data):
    executor.submit(execute_action, dog.reset)
    emit("reset_height", {"value": 50}, broadcast=True)


@socketio.on("action")
def handle_action(data):
    executor.submit(execute_action, dog.perform, int(data))


@socketio.on("do_action")
def handle_do_action(data):
    executor.submit(execute_action, dog.action, int(data))


@socketio.on("up")
def handle_up(data):
    executor.submit(execute_action, dog.move_x, int(data))


@socketio.on("down")
def handle_down(data):
    executor.submit(execute_action, dog.move_x, int(data))


@socketio.on("left")
def handle_left(data):
    val = int(data)
    if isinstance(dog, XGO_RIDER):
        executor.submit(execute_action, dog.turn, val)
    else:
        executor.submit(execute_action, dog.move_y, val)


@socketio.on("right")
def handle_right(data):
    val = int(data)
    if isinstance(dog, XGO_RIDER):
        executor.submit(execute_action, dog.turn, val)
    else:
        executor.submit(execute_action, dog.move_y, val)


@socketio.on("height")
def handle_height(data):
    val = int(data)
    if val < 50:
        height = int(95 - (50 - val) * 1.0)
    else:
        height = int(95 + (val - 50) * 1.0)
    height = max(30, min(160, height))
    executor.submit(execute_action, lambda: dog.translation("z", height))


# ---- Flask 启动线程 ----
def run_flask():
    socketio.run(
        flask_app,
        host="0.0.0.0",
        port=HTTP_PORT,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


# ===================== 资源路径 =====================
_LAUNCHER_ASSETS = os.path.dirname(T_Asset.bg_image)
DEMO_RC_ICON = os.path.join(_LAUNCHER_ASSETS, "demo_rc.png")
_RC_BG_IMAGE = "/home/pi/luwu-os/assets/images/app_bg.png"


# ===================== PySide6 页面 =====================
class RCModePage(AppFrame):
    """图传模式 LCD 界面（与 launcher / settings 同源浅色主题）。"""

    def __init__(self):
        super().__init__()
        # 覆盖背景为 app_bg.png（与 settings / AI 同款）
        _pix = QPixmap(_RC_BG_IMAGE)
        if not _pix.isNull():
            self._bg_pix = _pix
            self.update()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._first_paint_logged = False

        # ---- 顶部标题（AppFrame 提供） ----
        self.setTitle(_T("title"))

        # ---- 中间主体：图标 + accent 装饰线 + URL 卡片 + 状态 chip ----
        # 图标
        self.icon_label = QLabel(self)
        pix = QPixmap(DEMO_RC_ICON)
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

        # URL 文本（透明底 accent 蓝 subtitle，不加白色卡片以保证长链接完整显示）
        self.url_label = QLabel("http://...", self)
        self.url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.url_label.setStyleSheet(
            T_qss.text("subtitle", color=T_Color.accent)
        )

        # 状态 chip
        self.status_label = QLabel(_T("waiting"), self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(T_qss.chip("muted"))

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
        v.addWidget(self.url_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.md)
        v.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self._center = center

        # ---- 角标 ----
        self.setCornerHints(
            bl=(_T("corner_exit"), T_Asset.icon_back),
        )

        # ---- 定时刷新 ----
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_display)
        self.timer.start(2000)

        # ---- 立刻显示 IP ----
        self._update_display()

        # ---- 自动退出兜底 ----
        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)

    # ---- 布局事件 ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)  # AppFrame 负责背景与 4 角
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        # 中央区域避让顶部标题与底部角标
        top = max(28, h * 14 // 100)
        bottom = max(20, h * 8 // 100)
        self._center.setGeometry(0, top, w, h - top - bottom)


    # ---- 首帧日志 ----
    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")
            summary = self._stage_summary()
            print("[rc_mode] boot breakdown:\n" + summary, flush=True)

    def _stage_summary(self) -> str:
        lines = []
        prev = 0.0
        for name, ms in _stages:
            lines.append(f"{name}: {ms:.0f}ms (+{ms - prev:.0f})")
            prev = ms
        return " | ".join(lines)

    # ---- 按键 ----
    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            print("[rc_mode] KEY_Key_Back -> exit", flush=True)
            self.close()

    def closeEvent(self, ev):
        print("[rc_mode] closing", flush=True)
        self.timer.stop()
        super().closeEvent(ev)

    # ---- 定时刷新 ----
    def _update_display(self):
        ip_addr = get_ip("wlan0")
        self.url_label.setText(f"http://{ip_addr}:{HTTP_PORT}")
        with _conn_lock:
            connected = _connected_clients > 0
            count = _connected_clients
        if connected:
            self.status_label.setText(_T("connected", count))
            self.status_label.setStyleSheet(T_qss.chip("success"))
        else:
            self.status_label.setText(_T("waiting"))
            self.status_label.setStyleSheet(T_qss.chip("muted"))


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    apply_app_palette(app)
    mark("QApplication created")

    w = RCModePage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    # 后台启动 Flask（daemon 线程，随主线程退出自动结束）
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"[rc_mode] Flask starting on :{HTTP_PORT} ...", flush=True)

    rc = app.exec()

    # ---- 清理 ----
    camera_handler.stop()
    try:
        dog.reset()
    except Exception:
        pass

    print(f"[rc_mode] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
