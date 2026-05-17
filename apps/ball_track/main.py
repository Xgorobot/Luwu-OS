#!/usr/bin/env python3
"""
PySide6 小球抓取模式 (Ball Track & Catch) — 由 Luwu OS launcher 启动。
功能：识别红/绿/蓝小球，自动跟踪并抓取。

按键说明：
  A(左) → 退出程序
  B(右) → 开始/停止抓取
  C(下) → 切换颜色 (red→green→blue)
  D(上) → 切换颜色 (blue→green→red)
"""
import os
import sys
import time
import signal
import threading
import numpy as np
import cv2

# ===================== 阶段计时 =====================
T0 = time.monotonic()
_stages = []


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[ball_track][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== 导入 PySide6 =====================
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QFont, QKeyEvent, QImage, QPixmap, QPainter, QPen, QColor,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton,
)

mark("imports done")

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "title": "小球抓取",
            "starting": "启动中...",
            "ready": "就绪",
            "info_idle": "按 D(右) 开始抓取 | C(下) 切换颜色",
            "info_catching": "正在抓取中... 按 D(右) 停止 | C(下) 换色",
            "corner_exit": "A: 退出",
            "corner_b": "B: —",
            "corner_color": "C: 换色",
            "corner_catch": "D: 抓取",
            "corner_stop": "D: 停止",
            "btn_red": "🔴 红色",
            "btn_green": "🟢 绿色",
            "btn_blue": "🔵 蓝色",
            "color_red": "红色",
            "color_green": "绿色",
            "color_blue": "蓝色",
            "color_label": "颜色: {}",
            "prepare_catch": "准备抓取 {}...",
            "tracking": "追踪 {}: 距 {:.1f}cm, 偏 {:.2f}°",
            "searching": "搜索 {} 小球中...",
        },
        "en": {
            "title": "Ball Catch",
            "starting": "Starting...",
            "ready": "Ready",
            "info_idle": "Press D(R) to catch | C(Down) to change color",
            "info_catching": "Catching... Press D(R) to stop | C(Down) to change color",
            "corner_exit": "A: Exit",
            "corner_b": "B: —",
            "corner_color": "C: Color",
            "corner_catch": "D: Catch",
            "corner_stop": "D: Stop",
            "btn_red": "🔴 Red",
            "btn_green": "🟢 Green",
            "btn_blue": "🔵 Blue",
            "color_red": "Red",
            "color_green": "Green",
            "color_blue": "Blue",
            "color_label": "Color: {}",
            "prepare_catch": "Preparing to catch {}...",
            "tracking": "Tracking {}: dist {:.1f}cm, yaw {:.2f}°",
            "searching": "Searching for {} ball...",
        },
    })
    def _color_name(c):
        return _T(f"color_{c}")
except Exception:
    _T = lambda k, *a: k
    def _color_name(c):
        return COLOR_LABELS_CN.get(c, c)

# ===================== XGO 库 =====================
sys.path.insert(0, "/home/pi/lib")


def _init_dog():
    """初始化 XGO 机器狗，自动检测机型。"""
    try:
        from xgolib import XGO
    except ImportError:
        print("[ball_track] xgolib 不可用", flush=True)
        return None

    # 先用 xgomini 检测固件
    try:
        temp = XGO("xgomini")
        fm = temp.read_firmware()
        if fm:
            first = fm[0].upper() if fm else ""
            if first == 'W':
                return XGO("xgomini3w")
            elif first == 'R':
                return XGO("xgorider")
            elif first == 'M':
                return XGO("xgomini")
            else:
                return XGO("xgomini")
        return XGO("xgomini")
    except Exception as e:
        print(f"[ball_track] dog init error: {e}", flush=True)
    return None


# ===================== 颜色定义 =====================
COLORS = ["red", "green", "blue"]
COLOR_LABELS_CN = {"red": "红色", "green": "绿色", "blue": "蓝色"}


# HSV 范围（参考 Blockly 配置 + ball.py）
HSV_RANGES = {
    "red": (
        (np.array([0, 110, 70]), np.array([10, 255, 255])),
        (np.array([170, 110, 70]), np.array([180, 255, 255])),
    ),
    "green": (
        (np.array([46, 80, 10]), np.array([74, 255, 255])),
    ),
    "blue": (
        (np.array([90, 50, 50]), np.array([140, 255, 255])),
    ),
}

# ===================== 常量 =====================
CAMERA_W, CAMERA_H = 320, 240
FRAME_INTERVAL_MS = 50  # ~20fps
AUTO_EXIT_SEC = 600      # 10 分钟自动退出兜底
X_CENTER_DEFAULT = 160.0
X_DISTANCE_DEFAULT = 22.0
MIN_RADIUS_DEFAULT = 8.0


# ===================== 小球检测与跟踪 =====================
class BallDetector:
    """小球颜色识别 + HoughCircles 检测 + EMA 滤波"""

    def __init__(self):
        self.mx = 0.0   # EMA 滤波后的球心 x
        self.my = 0.0
        self.mr = 0.0
        self.distance = 0.0
        self.yaw_err = 0.0
        self.found = False
        self.ema_alpha = 0.4
        self.min_area = 50
        self.min_radius = MIN_RADIUS_DEFAULT

    def reset(self):
        self.mx = self.my = self.mr = 0.0
        self.distance = 0.0
        self.yaw_err = 0.0
        self.found = False

    def detect(self, frame: np.ndarray, color: str):
        """
        在 frame (BGR) 中检测指定颜色的小球。
        返回 (found, annotated_frame, info_dict)
        """
        info = {"x": 0, "y": 0, "r": 0, "distance": 0, "yaw_err": 0}
        annotated = frame.copy()

        if frame is None:
            return False, annotated, info

        h, w = frame.shape[:2]
        cx = w / 2.0

        # 高斯模糊
        blurred = cv2.GaussianBlur(frame, (3, 3), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        # 颜色掩码
        ranges = HSV_RANGES.get(color, HSV_RANGES["red"])
        mask = None
        for lower, upper in ranges:
            m = cv2.inRange(hsv, lower, upper)
            if mask is None:
                mask = m
            else:
                mask = cv2.bitwise_or(mask, m)

        # 形态学处理
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.dilate(mask, kernel, iterations=2)

        # 最小面积过滤
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filtered = np.zeros_like(mask)
        for cnt in contours:
            if cv2.contourArea(cnt) >= self.min_area:
                cv2.drawContours(filtered, [cnt], -1, 255, -1)
        mask = filtered

        # 位与后灰度化
        masked_img = cv2.bitwise_and(frame, frame, mask=mask)
        gray = cv2.cvtColor(masked_img, cv2.COLOR_BGR2GRAY)

        # HoughCircles
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1, minDist=30,
            param1=28, param2=12,
            minRadius=int(self.min_radius), maxRadius=45,
        )

        found = False
        if circles is not None:
            circles = np.round(circles[0, :]).astype("int")
            # 取最大半径圆
            idx = np.argmax(circles[:, 2])
            x, y, r = circles[idx]
            info["x"], info["y"], info["r"] = int(x), int(y), int(r)
            found = True

            # EMA 滤波
            if self.mx == 0 and self.my == 0 and self.mr == 0:
                self.mx, self.my, self.mr = float(x), float(y), float(r)
            else:
                self.mx = self.ema_alpha * x + (1 - self.ema_alpha) * self.mx
                self.my = self.ema_alpha * y + (1 - self.ema_alpha) * self.my
                self.mr = self.ema_alpha * r + (1 - self.ema_alpha) * self.mr

            # 距离估算 (cm)
            self.distance = 54.82 - self.mr if self.mr > 0 else 999
            if self.distance <= 0:
                self.distance = 1

            # 偏航误差
            self.yaw_err = -(self.mx - cx) / self.distance

            info["distance"] = self.distance
            info["yaw_err"] = self.yaw_err

            # 在画面上绘制
            cv2.circle(annotated, (int(x), int(y)), int(r), (0, 255, 0), 2)
            cv2.circle(annotated, (int(x), int(y)), 2, (0, 0, 255), 3)
            cv2.line(annotated, (int(self.mx), int(self.my)),
                     (int(cx), int(self.my)), (255, 255, 0), 1)
        else:
            self.found = False

        self.found = found
        return found, annotated, info


# ===================== PySide6 主页面 =====================
class BallTrackPage(QWidget):
    """小球抓取 PySide6 UI"""

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0a0a1a;")
        self._first_paint_logged = False

        # ---- 颜色参数 ----
        self._color_idx = 0   # 0=red, 1=green, 2=blue
        self._color = COLORS[self._color_idx]

        # ---- 抓取参数 ----
        self._x_distance = X_DISTANCE_DEFAULT
        self._x_center = X_CENTER_DEFAULT
        self._min_radius = MIN_RADIUS_DEFAULT

        # ---- 状态 ----
        self._catching = False     # 抓取模式
        self._catch_thread = None
        self._stop_catch = False
        self._close_mode = False
        self._un_circle = 0

        # ---- 摄像头 ----
        self._picam2 = None
        self._frame = None
        self._frame_lock = threading.Lock()

        # ---- 检测器 ----
        self._detector = BallDetector()

        # ---- 标题 ----
        self.title = QLabel(_T("title"))
        f1 = QFont()
        f1.setPointSize(18)
        f1.setBold(True)
        self.title.setFont(f1)
        self.title.setStyleSheet("color: #50C878;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 相机画面 ----
        self.camera_label = QLabel()
        self.camera_label.setFixedSize(CAMERA_W, CAMERA_H)
        self.camera_label.setStyleSheet(
            "background-color: black; border: 2px solid #333;"
        )
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setText(_T("starting"))

        # ---- 状态信息 ----
        self.status_label = QLabel(_T("ready"))
        self.status_label.setStyleSheet(
            "color: #18df6b; font-size: 14px; padding: 4px;"
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.info_label = QLabel(_T("info_idle"))
        self.info_label.setStyleSheet("color: #8892c9; font-size: 11px;")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setWordWrap(True)

        # ---- 颜色按钮 ----
        btn_style = (
            "QPushButton { background: #1a1a3e; color: #ccc; border: 1px solid #444; "
            "border-radius: 6px; padding: 6px 14px; font-size: 12px; }"
            "QPushButton:pressed { background: #333; }"
        )
        active_btn_style = (
            "QPushButton { background: #2a5a3e; color: #fff; border: 1px solid #50C878; "
            "border-radius: 6px; padding: 6px 14px; font-size: 12px; font-weight: bold; }"
        )

        self.btn_red = QPushButton(_T("btn_red"))
        self.btn_green = QPushButton(_T("btn_green"))
        self.btn_blue = QPushButton(_T("btn_blue"))
        self._color_btns = [self.btn_red, self.btn_green, self.btn_blue]

        for i, btn in enumerate(self._color_btns):
            btn.clicked.connect(lambda checked, idx=i: self._on_color_btn(idx))
        self._update_btn_styles()

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        color_row.addWidget(self.btn_red)
        color_row.addWidget(self.btn_green)
        color_row.addWidget(self.btn_blue)
        color_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 四角按键提示 ----
        corner_style = "color: #5c6a9c; font-size: 11px; background: transparent;"
        self.corner_tl = QLabel(_T("corner_exit"), self)
        self.corner_tl.setStyleSheet(corner_style)
        self.corner_tr = QLabel(_T("corner_b"), self)
        self.corner_tr.setStyleSheet(corner_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel(_T("corner_color"), self)
        self.corner_bl.setStyleSheet(corner_style)
        self.corner_br = QLabel(_T("corner_catch"), self)
        self.corner_br.setStyleSheet(corner_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        # ---- 布局 ----
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.setSpacing(8)
        main_layout.addWidget(self.title)
        main_layout.addWidget(self.camera_label, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.status_label)
        main_layout.addLayout(color_row)
        main_layout.addWidget(self.info_label)

        # ---- 定时刷新相机 ----
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._update_frame)
        self.frame_timer.start(FRAME_INTERVAL_MS)

        # ---- 自动退出 ----
        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ---- 启动摄像头 ----
        self._start_camera()

        mark("widget constructed")

    # ==================== 布局事件 ====================
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 12
        self.corner_tl.move(pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_tr.adjustSize()
        self.corner_br.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")

    # ==================== 按键事件 ====================
    # 物理按键映射 (gpio-keys):
    #   A=KEY_UP, B=KEY_DOWN, C=KEY_LEFT, D=KEY_RIGHT
    # 但 launcher 的 keyfilter 在 blocked 模式下通过 KEYS_FIFO 转发，
    # 实际收到的是 Qt 按键值。这里按 Key_Left/Key_Right 映射。
    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Left:
            # C 键 → 退出
            print("[ball_track] KEY_LEFT -> exit", flush=True)
            self._stop_catching()
            self.close()
        elif ev.key() == Qt.Key.Key_Right:
            # D 键 → 开始/停止抓取
            if self._catching:
                print("[ball_track] KEY_RIGHT -> stop catch", flush=True)
                self._stop_catching()
            else:
                print("[ball_track] KEY_RIGHT -> start catch", flush=True)
                self._start_catching()
        elif ev.key() == Qt.Key.Key_Up:
            # A 键 → 上一颜色
            self._color_idx = (self._color_idx + 2) % 3
            self._color = COLORS[self._color_idx]
            self._update_btn_styles()
            self.status_label.setText(_T("color_label", _color_name(self._color)))
            self.status_label.setStyleSheet("color: #18df6b; font-size: 14px; padding: 4px;")
            print(f"[ball_track] color -> {self._color}", flush=True)
        elif ev.key() == Qt.Key.Key_Down:
            # B 键 → 下一颜色
            self._color_idx = (self._color_idx + 1) % 3
            self._color = COLORS[self._color_idx]
            self._update_btn_styles()
            self.status_label.setText(_T("color_label", _color_name(self._color)))
            self.status_label.setStyleSheet("color: #18df6b; font-size: 14px; padding: 4px;")
            print(f"[ball_track] color -> {self._color}", flush=True)

    def closeEvent(self, ev):
        print("[ball_track] closing", flush=True)
        self._stop_catching()
        self._stop_camera()
        super().closeEvent(ev)

    # ==================== 颜色切换 ====================
    def _on_color_btn(self, idx: int):
        self._color_idx = idx
        self._color = COLORS[idx]
        self._update_btn_styles()
        self.status_label.setText(_T("color_label", _color_name(self._color)))
        self.status_label.setStyleSheet("color: #18df6b; font-size: 14px; padding: 4px;")
        print(f"[ball_track] color -> {self._color}", flush=True)

    def _update_btn_styles(self):
        style = (
            "QPushButton { background: #1a1a3e; color: #ccc; border: 1px solid #444; "
            "border-radius: 6px; padding: 6px 14px; font-size: 12px; }"
            "QPushButton:pressed { background: #333; }"
        )
        active_style = (
            "QPushButton { background: #2a5a3e; color: #fff; border: 2px solid #50C878; "
            "border-radius: 6px; padding: 6px 14px; font-size: 12px; font-weight: bold; }"
        )
        for i, btn in enumerate(self._color_btns):
            btn.setStyleSheet(active_style if i == self._color_idx else style)

    # ==================== 摄像头 ====================
    def _start_camera(self):
        try:
            from picamera2 import Picamera2
            from libcamera import Transform
            self._picam2 = Picamera2()
            config = self._picam2.create_preview_configuration(
                main={"format": "RGB888", "size": (CAMERA_W, CAMERA_H)},
                transform=Transform(hflip=0, vflip=0),
            )
            self._picam2.configure(config)
            self._picam2.start()
            time.sleep(0.5)
            print("[ball_track] camera started", flush=True)
        except Exception as e:
            print(f"[ball_track] camera error: {e}", flush=True)

    def _stop_camera(self):
        if self._picam2:
            try:
                self._picam2.stop()
                self._picam2.close()
            except Exception:
                pass
            self._picam2 = None
            print("[ball_track] camera stopped", flush=True)

    def _capture_raw(self):
        """从摄像头抓一帧 (BGR)"""
        if self._picam2 is None:
            return None
        try:
            frame = self._picam2.capture_array()
            if frame is not None:
                # Picamera2 RGB888 → BGR (OpenCV 格式)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return frame
        except Exception:
            return None

    # ==================== UI 帧更新 ====================
    def _update_frame(self):
        """定时器回调：捕获帧 → 检测 → 显示"""
        frame = self._capture_raw()
        if frame is None:
            return

        # 检测小球
        found, annotated, info = self._detector.detect(frame, self._color)

        # 如果在抓取模式下，更新状态信息
        if self._catching:
            if found:
                dist = info.get("distance", 0)
                yaw = info.get("yaw_err", 0)
                self.status_label.setText(
                    _T("tracking", _color_name(self._color), dist, yaw)
                )
                self.status_label.setStyleSheet(
                    "color: #18df6b; font-size: 12px; padding: 4px;"
                )
            else:
                self.status_label.setText(
                    _T("searching", _color_name(self._color))
                )
                self.status_label.setStyleSheet(
                    "color: #FFD93D; font-size: 12px; padding: 4px;"
                )

        # 转为 QPixmap 显示
        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        h, w, c = rgb.shape
        qimg = QImage(rgb.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg).scaled(
            CAMERA_W, CAMERA_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.camera_label.setPixmap(pixmap)

    # ==================== 抓取逻辑 ====================
    def _start_catching(self):
        if self._catching:
            return
        self._catching = True
        self._stop_catch = False
        self._close_mode = False
        self._un_circle = 0
        self._detector.reset()
        self.corner_br.setText(_T("corner_stop"))
        self.info_label.setText(_T("info_catching"))
        self.info_label.setStyleSheet("color: #FFD93D; font-size: 11px;")
        self.status_label.setText(_T("prepare_catch", _color_name(self._color)))
        self.status_label.setStyleSheet("color: #18df6b; font-size: 12px; padding: 4px;")

        self._catch_thread = threading.Thread(target=self._catch_loop, daemon=True)
        self._catch_thread.start()

    def _stop_catching(self):
        self._stop_catch = True
        self._catching = False
        if self._catch_thread and self._catch_thread.is_alive():
            self._catch_thread.join(timeout=3)
        self._catch_thread = None
        # 停狗
        try:
            dog = _init_dog()
            if dog:
                dog.move_x(0)
                dog.turn(0)
                dog.translation('x', 0)
                dog.attitude('y', 0)
        except Exception:
            pass
        self.corner_br.setText(_T("corner_catch"))
        self.info_label.setText(_T("info_idle"))
        self.info_label.setStyleSheet("color: #8892c9; font-size: 11px;")
        self.status_label.setText(_T("ready"))
        self.status_label.setStyleSheet("color: #18df6b; font-size: 14px; padding: 4px;")

    def _catch_loop(self):
        """抓取模式主循环（后台线程）"""
        print("[ball_track] catch loop started", flush=True)
        dog = _init_dog()
        if dog is None:
            print("[ball_track] dog not available for catch", flush=True)
            self._catching = False
            return

        try:
            # 趴下准备
            dog.attitude('p', 15)
            dog.translation('z', 75)
            time.sleep(2)

            mintime_yaw = 0.8
            mintime_x = 0.1
            x_speed_far = 10
            x_speed_mid = 5
            turn_speed = 8

            while not self._stop_catch:
                found = self._detector.found
                if not found:
                    self._un_circle += 1
                    lost_threshold = 15 if self._close_mode else 12
                    if self._un_circle >= lost_threshold:
                        self._un_circle = 0
                        self._detector.reset()
                        if self._close_mode:
                            dog.translation('x', 0)
                            dog.attitude('y', 0)
                            time.sleep(0.3)
                        else:
                            dog.move_x(0)
                            dog.turn(0)
                            time.sleep(0.5)
                    time.sleep(0.05)
                    continue

                self._un_circle = 0
                mr = self._detector.mr
                mx = self._detector.mx
                distance = self._detector.distance
                yaw_err = self._detector.yaw_err
                x_distance = self._x_distance
                x_center = self._x_center

                if mr <= 0 or distance <= 0:
                    time.sleep(0.05)
                    continue

                y_1, y_2 = 20, 25

                # 近距离模式切换
                if distance < x_distance + 5:
                    if not self._close_mode:
                        self._close_mode = True
                        dog.move_x(0)
                        dog.turn(0)
                        dog.attitude('p', 22)
                        time.sleep(0.2)
                else:
                    if self._close_mode:
                        self._close_mode = False
                        dog.attitude('y', 0)
                        time.sleep(0.1)

                # 抓取判定
                catch_dist = x_distance + 5 if self._close_mode else x_distance

                # 条件一
                if distance < catch_dist and -y_1 / distance <= yaw_err <= y_1 / distance:
                    print("[ball_track] 条件一满足，执行抓取!", flush=True)
                    dog.attitude('y', 5 * yaw_err)
                    dog.translation('x', max(0, distance - 20))
                    time.sleep(0.5)
                    self._catch_arm(dog)
                    time.sleep(2)
                    self._stop_catch = True
                    break

                # 条件二
                if distance < catch_dist and -y_2 <= mx - x_center <= y_2:
                    print("[ball_track] 条件二满足，执行抓取!", flush=True)
                    dog.attitude('y', 0.25 * (x_center - mx))
                    dog.translation('x', max(0, distance - 20))
                    time.sleep(0.5)
                    self._catch_arm(dog)
                    time.sleep(2)
                    self._stop_catch = True
                    break

                # 运动控制
                if self._close_mode:
                    if distance >= x_distance:
                        trans_x = min((distance - x_distance) * 1.5, 15)
                        dog.translation('x', trans_x)
                        time.sleep(0.2)
                    if abs(yaw_err) > y_1 / distance:
                        att_y = max(-10, min(10, 3 * yaw_err))
                        dog.attitude('y', att_y)
                        time.sleep(0.15)
                else:
                    if yaw_err > y_1 / distance:
                        turn_t = min(abs(0.4 * yaw_err) / 8 + mintime_yaw, 1.5)
                        dog.gait_type('slow_trot')
                        dog.turn(turn_speed)
                        time.sleep(turn_t)
                        dog.turn(0)
                        time.sleep(0.15)
                    elif yaw_err < -y_1 / distance:
                        turn_t = min(abs(0.4 * yaw_err) / 8 + mintime_yaw, 1.5)
                        dog.gait_type('slow_trot')
                        dog.turn(-turn_speed)
                        time.sleep(turn_t)
                        dog.turn(0)
                        time.sleep(0.15)
                    else:
                        if distance >= x_distance + 10:
                            step_t = 0.5
                            speed = x_speed_far
                        elif distance >= x_distance + 3:
                            step_t = 0.3
                            speed = x_speed_mid
                        else:
                            step_t = 0.2
                            speed = x_speed_mid
                        dog.gait_type('slow_trot')
                        dog.move_x(speed)
                        time.sleep(step_t)
                        dog.move_x(0)
                        time.sleep(0.2)

                time.sleep(0.05)

            # 抓取完成后的清理
            dog.move_x(0)
            dog.turn(0)
            dog.translation('x', 0)
            dog.translation('y', 0)
            dog.attitude('y', 0)
            dog.attitude('p', 0)
            self._down_arm(dog)
            time.sleep(2)
            dog.reset()

        except Exception as e:
            print(f"[ball_track] catch error: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            self._catching = False
            print("[ball_track] catch loop ended", flush=True)

    def _catch_arm(self, dog):
        """机械臂抓取"""
        try:
            dog.translation('z', 10)
            dog.attitude('p', 15)
            dog.claw(5)
            time.sleep(1)
            dog.arm_polar(200, 130)
            time.sleep(2)
            dog.claw(245)
            time.sleep(1)
            dog.arm_polar(90, 100)
            print("[ball_track] catch arm executed", flush=True)
        except Exception as e:
            print(f"[ball_track] catch arm error: {e}", flush=True)

    def _down_arm(self, dog):
        """放下机械臂"""
        try:
            dog.translation('z', 10)
            dog.attitude('p', 15)
            time.sleep(1)
            dog.arm_polar(200, 130)
            time.sleep(2)
            dog.claw(15)
            time.sleep(1)
            dog.arm_polar(90, 100)
            print("[ball_track] arm down", flush=True)
        except Exception as e:
            print(f"[ball_track] down arm error: {e}", flush=True)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = BallTrackPage()
    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[ball_track] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
