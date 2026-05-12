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
from PySide6.QtGui import QFont, QKeyEvent, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
)

from flask import Flask, render_template, Response
from flask_socketio import SocketIO, emit
from picamera2 import Picamera2
from concurrent.futures import ThreadPoolExecutor

# ---- 狗库 ----
sys.path.insert(0, "/home/pi/lib")
from xgolib_dog import XGO_DOG  # noqa: E402

mark("imports done")

# ===================== 常量 =====================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
HTTP_PORT = 8080
AUTO_EXIT_SEC = 300  # 5 分钟无操作自动退出


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
dog = XGO_DOG(port="/dev/ttyAMA0", baud=115200, version="xgomini")


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


# ---- WebSocket 事件 ----
@socketio.on("connect")
def on_connect():
    print("[rc_mode] client connected", flush=True)


@socketio.on("disconnect")
def on_disconnect():
    print("[rc_mode] client disconnected", flush=True)


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


@socketio.on("PushUp")
def handle_pushup(data):
    executor.submit(execute_action, dog.action, int(data))


@socketio.on("TakeAPee")
def handle_takeapee(data):
    executor.submit(execute_action, dog.action, int(data))


@socketio.on("WaveHand")
def handle_wavehand(data):
    executor.submit(execute_action, dog.action, int(data))


@socketio.on("UpDown")
def handle_updown(data):
    executor.submit(execute_action, dog.action, int(data))


@socketio.on("LookFood")
def handle_lookfood(data):
    executor.submit(execute_action, dog.action, int(data))


@socketio.on("Dance")
def handle_dance(data):
    executor.submit(execute_action, dog.action, int(data))


@socketio.on("up")
def handle_up(data):
    executor.submit(execute_action, dog.move_x, int(data))


@socketio.on("down")
def handle_down(data):
    executor.submit(execute_action, dog.move_x, int(data))


@socketio.on("left")
def handle_left(data):
    executor.submit(execute_action, dog.move_y, int(data))


@socketio.on("right")
def handle_right(data):
    executor.submit(execute_action, dog.move_y, int(data))


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


# ===================== PySide6 页面 =====================
class RCModePage(QWidget):
    """图传模式 LCD 界面。"""

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0f1530;")
        self._first_paint_logged = False

        # ---- 标题 ----
        self.title = QLabel("图传模式")
        f1 = QFont()
        f1.setPointSize(18)
        f1.setBold(True)
        self.title.setFont(f1)
        self.title.setStyleSheet("color: white;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- IP 显示 ----
        self.ip_label = QLabel("...")
        f2 = QFont()
        f2.setPointSize(13)
        self.ip_label.setFont(f2)
        self.ip_label.setStyleSheet("color: #18df6b;")
        self.ip_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 状态 ----
        self.status_label = QLabel("等待连接...")
        self.status_label.setStyleSheet("color: #8892c9; font-size: 12px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 启动耗时 ----
        self.info = QLabel("boot: -- ms")
        self.info.setStyleSheet("color: #5c6a9c; font-size: 9px;")
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info.setWordWrap(True)

        # ---- 四角按键提示 ----
        corner_style = "color: #5c6a9c; font-size: 11px; background: transparent;"
        self.corner_bl = QLabel("Exit", self)
        self.corner_bl.setStyleSheet(corner_style)

        # ---- 布局 ----
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(30)
        layout.addWidget(self.title)
        layout.addSpacing(15)
        layout.addWidget(self.ip_label)
        layout.addSpacing(10)
        layout.addWidget(self.status_label)
        layout.addSpacing(20)
        layout.addWidget(self.info)

        # ---- 定时刷新 ----
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_display)
        self.timer.start(2000)

        # ---- 立刻显示 IP ----
        self._update_display()

        # ---- 自动退出兜底 ----
        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---- 布局事件 ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 16
        self.corner_bl.adjustSize()
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)

    # ---- 首帧日志 ----
    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")
            summary = self._stage_summary()
            self.info.setText(summary)
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
        if ev.key() == Qt.Key.Key_Left:
            # KEY_LEFT = 物理左下 C 键 → 退出
            print("[rc_mode] KEY_LEFT -> exit", flush=True)
            self.close()

    def closeEvent(self, ev):
        print("[rc_mode] closing", flush=True)
        self.timer.stop()
        super().closeEvent(ev)

    # ---- 定时刷新 ----
    def _update_display(self):
        ip_addr = get_ip("wlan0")
        self.ip_label.setText(f"http://{ip_addr}:{HTTP_PORT}")


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
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
