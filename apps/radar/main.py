#!/usr/bin/env python3
"""
PySide6 雷达扫描 App — 由 Luwu OS launcher 启动。
在屏幕上绘制 YDLidar 雷达扫描数据。
物理按键：C=退出
"""
import sys
import os
import time
import signal
import math
import threading

# ---- 阶段计时 ----
T0 = time.monotonic()
_stages = []

def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[radar][+{ms:7.1f}ms] {name}", flush=True)

mark("python entry")

# ---- 添加 ydlidar SDK 路径（已迁移至 luwu-os/libs/ydlidar_sdk，解耦 XGO-PI-CM5）----
sys.path.insert(0, '/home/pi/luwu-os/libs/ydlidar_sdk')
import ydlidar

# ---- PySide6 导入 ----
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont, QKeyEvent, QPainter, QColor, QPen, QBrush, QPixmap, QPaintEvent
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout

mark("imports done")

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "title": "雷达扫描",
            "init": "正在初始化雷达...",
            "corner_exit": "C:退出",
            "radar_disconnected": "雷达未连接",
            "radar_connected": "雷达已连接",
        },
        "en": {
            "title": "Lidar Scan",
            "init": "Initializing lidar...",
            "corner_exit": "C: Exit",
            "radar_disconnected": "Lidar not connected",
            "radar_connected": "Lidar connected",
        },
    })
except Exception:
    _T = lambda k, *a: k

# ===================== 常量 =====================
AUTO_EXIT_SEC = 120
MAX_DISPLAY_RANGE = 5.0  # 最大显示距离 (米)
RADAR_PORT = "/dev/ttyUSB0"
RADAR_BAUDRATE = 230400

# ===================== 雷达数据读取线程 =====================
class RadarReaderThread(QThread):
    """后台线程：读取 YDLidar 数据"""
    points_ready = Signal(list)        # 发送雷达点列表 [(distance, angle_deg), ...]
    radar_status = Signal(bool, str)   # 雷达状态: (connected, message)

    def __init__(self):
        super().__init__()
        self._running = False
        self.laser = None
        self.radar_connected = False

    def run(self):
        self._running = True
        # 初始化雷达
        self._init_radar()

        if not self.radar_connected:
            self.radar_status.emit(False, _T("radar_disconnected"))
            # 仍然循环，尝试重连
            while self._running:
                time.sleep(2)
                self._init_radar()
            return

        self.radar_status.emit(True, _T("radar_connected"))
        print("[radar] reader thread started")

        # 主读取循环
        while self._running and ydlidar.os_isOk():
            try:
                scan = ydlidar.LaserScan()
                if self.laser.doProcessSimple(scan):
                    points = []
                    for point in scan.points:
                        if point.range <= 0:
                            continue
                        angle_deg = math.degrees(point.angle)
                        distance = point.range
                        if 0.05 <= distance <= MAX_DISPLAY_RANGE:
                            points.append((distance, angle_deg))
                    self.points_ready.emit(points)
                else:
                    time.sleep(0.05)
                time.sleep(0.01)
            except Exception as e:
                print(f"[radar] read error: {e}")
                time.sleep(0.1)

        # 清理雷达
        self._cleanup_radar()
        print("[radar] reader thread ended")

    def _init_radar(self):
        try:
            print("[radar] initializing YDLidar...")
            ydlidar.os_init()

            self.laser = ydlidar.CYdLidar()
            self.laser.setlidaropt(ydlidar.LidarPropSerialPort, RADAR_PORT)
            self.laser.setlidaropt(ydlidar.LidarPropIgnoreArray, "")
            self.laser.setlidaropt(ydlidar.LidarPropSerialBaudrate, RADAR_BAUDRATE)
            self.laser.setlidaropt(ydlidar.LidarPropLidarType, ydlidar.TYPE_TRIANGLE)
            self.laser.setlidaropt(ydlidar.LidarPropDeviceType, ydlidar.YDLIDAR_TYPE_SERIAL)
            self.laser.setlidaropt(ydlidar.LidarPropSampleRate, 4)
            self.laser.setlidaropt(ydlidar.LidarPropIntenstiyBit, 8)
            self.laser.setlidaropt(ydlidar.LidarPropFixedResolution, True)
            self.laser.setlidaropt(ydlidar.LidarPropReversion, False)
            self.laser.setlidaropt(ydlidar.LidarPropInverted, False)
            self.laser.setlidaropt(ydlidar.LidarPropAutoReconnect, True)
            self.laser.setlidaropt(ydlidar.LidarPropSingleChannel, False)
            self.laser.setlidaropt(ydlidar.LidarPropIntenstiy, True)
            self.laser.setlidaropt(ydlidar.LidarPropSupportMotorDtrCtrl, False)
            self.laser.setlidaropt(ydlidar.LidarPropSupportHeartBeat, False)
            self.laser.setlidaropt(ydlidar.LidarPropMaxAngle, 180.0)
            self.laser.setlidaropt(ydlidar.LidarPropMinAngle, -180.0)
            self.laser.setlidaropt(ydlidar.LidarPropMaxRange, 64.0)
            self.laser.setlidaropt(ydlidar.LidarPropMinRange, 0.05)
            self.laser.setlidaropt(ydlidar.LidarPropScanFrequency, 10.0)

            try:
                self.laser.enableGlassNoise(False)
                self.laser.enableSunNoise(False)
            except AttributeError:
                try:
                    self.laser.setGlassNoise(False)
                    self.laser.setSunNoise(False)
                except AttributeError:
                    pass

            ret = self.laser.initialize()
            if not ret:
                print(f"[radar] init failed: {self.laser.DescribeError()}")
                self.laser = None
                self.radar_connected = False
                return

            ret = self.laser.turnOn()
            if not ret:
                print(f"[radar] turnOn failed: {self.laser.DescribeError()}")
                self.laser.disconnecting()
                self.laser = None
                self.radar_connected = False
                return

            self.radar_connected = True
            print("[radar] YDLidar initialized and scanning")
        except Exception as e:
            print(f"[radar] init error: {e}")
            if self.laser:
                try:
                    self.laser.disconnecting()
                except:
                    pass
            self.laser = None
            self.radar_connected = False

    def _cleanup_radar(self):
        if self.laser:
            try:
                self.laser.turnOff()
                self.laser.disconnecting()
                print("[radar] lidar turned off")
            except Exception as e:
                print(f"[radar] cleanup error: {e}")

    def stop(self):
        self._running = False


# ===================== PySide6 雷达绘制组件 =====================
class RadarCanvas(QWidget):
    """自定义 QWidget，用 QPainter 绘制雷达扫描画面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.radar_points = []
        self.radar_connected = False
        self.status_text = ""

    def update_points(self, points, connected, status):
        self.radar_points = points
        self.radar_connected = connected
        self.status_text = status
        self.update()  # 触发重绘

    def _polar_to_xy(self, distance, angle_deg):
        """极坐标转画布坐标"""
        w = self.width()
        h = self.height()
        cx = w // 2
        cy = h // 2
        max_radius = min(w, h) // 2 - 20

        angle_rad = math.radians(angle_deg)
        r = (distance / MAX_DISPLAY_RANGE) * max_radius
        x = int(cx + r * math.cos(angle_rad))
        y = int(cy - r * math.sin(angle_rad))
        return x, y

    def _get_dist_color(self, distance):
        """根据距离返回颜色"""
        if distance < 1.5:
            return QColor(255, 0, 0)     # 红色 - 近
        elif distance < 3.0:
            return QColor(255, 255, 0)   # 黄色 - 中
        else:
            return QColor(0, 255, 0)     # 绿色 - 远

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w // 2
        cy = h // 2
        max_radius = min(w, h) // 2 - 20

        # 背景
        painter.fillRect(0, 0, w, h, QColor(0, 0, 0))

        if not self.radar_connected:
            # 未连接状态
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setPointSize(18)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.status_text)
            painter.end()
            return

        # ---- 绘制坐标系统 ----
        # 同心圆
        for i in range(1, 6):
            r = int(i * max_radius / 5)
            painter.setPen(QPen(QColor(64, 64, 64), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

            # 距离标签
            painter.setPen(QColor(180, 180, 180))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(cx + r - 12, cy - 4, f"{i}m")

        # 十字线
        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.drawLine(0, cy, w, cy)
        painter.drawLine(cx, 0, cx, h)

        # 角度射线
        painter.setPen(QPen(QColor(50, 50, 50), 1))
        for angle in range(0, 360, 30):
            rad = math.radians(angle)
            ex = int(cx + max_radius * math.cos(rad))
            ey = int(cy - max_radius * math.sin(rad))
            painter.drawLine(cx, cy, ex, ey)

        # 中心点
        painter.setPen(QPen(QColor(255, 0, 0), 2))
        painter.setBrush(QBrush(QColor(255, 0, 0)))
        painter.drawEllipse(cx - 3, cy - 3, 6, 6)

        # ---- 绘制雷达点 ----
        for dist, angle in self.radar_points:
            if dist > MAX_DISPLAY_RANGE:
                continue
            px, py = self._polar_to_xy(dist, angle)
            if 0 <= px < w and 0 <= py < h:
                color = self._get_dist_color(dist)
                painter.setPen(QPen(color, 2))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(px - 1, py - 1, 2, 2)

        # ---- 信息文字 ----
        painter.setPen(QColor(200, 200, 200))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        info = f"Points: {len(self.radar_points)}  Range: {MAX_DISPLAY_RANGE}m"
        painter.drawText(8, 16, info)

        painter.end()


# ===================== PySide6 主页面 =====================
class RadarPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0a0a1a;")
        self._first_paint_logged = False

        # ---- 标题 ----
        self.title = QLabel(_T("title"))
        f1 = QFont()
        f1.setPointSize(18)
        f1.setBold(True)
        self.title.setFont(f1)
        self.title.setStyleSheet("color: white;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 雷达画布 ----
        self.radar_canvas = RadarCanvas(self)

        # ---- 状态行 ----
        self.status_label = QLabel(_T("init"))
        self.status_label.setStyleSheet("color: #8892c9; font-size: 12px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        # ---- 提示 ----
        self.hint = QLabel(_T("corner_exit"))
        self.hint.setStyleSheet("color: #5c6a9c; font-size: 11px;")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 四角按键说明 ----
        corner_style = "color: #5c6a9c; font-size: 11px; background: transparent;"
        self.corner_bl = QLabel(_T("corner_exit"), self)
        self.corner_bl.setStyleSheet(corner_style)

        # ---- 布局 ----
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addSpacing(4)
        layout.addWidget(self.radar_canvas, 1)
        layout.addSpacing(4)
        layout.addWidget(self.status_label)
        layout.addWidget(self.hint)

        # ---- 自动退出 ----
        self._auto_exit_timer = QTimer(self)
        self._auto_exit_timer.timeout.connect(self.close)
        self._auto_exit_timer.start(AUTO_EXIT_SEC * 1000)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ---- 启动雷达读取线程 ----
        self._radar_thread = RadarReaderThread()
        self._radar_thread.points_ready.connect(self._on_points_ready)
        self._radar_thread.radar_status.connect(self._on_radar_status)
        self._radar_thread.start()

        print("[radar] page initialized")

    def _on_points_ready(self, points):
        self.radar_canvas.update_points(points, True, "")

    def _on_radar_status(self, connected, message):
        if connected:
            self.status_label.setText(f"✅ {message}")
        else:
            self.status_label.setText(f"⚠️ {message}")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 16
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")

    def keyPressEvent(self, ev: QKeyEvent):
        # C 键 (KEY_UP) 退出
        if ev.key() == Qt.Key.Key_Up:
            print("[radar] KEY_UP (C) -> exit", flush=True)
            self.close()

    def closeEvent(self, ev):
        print("[radar] closing", flush=True)
        self._auto_exit_timer.stop()
        if self._radar_thread and self._radar_thread.isRunning():
            self._radar_thread.stop()
            self._radar_thread.wait(3000)
        super().closeEvent(ev)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = RadarPage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[radar] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
