#!/usr/bin/env python3
"""
PySide6 小球抓取 App — 由 Luwu OS launcher 启动。
识别并抓取指定颜色（红/绿/蓝）的小球。
物理按键：A=切换颜色  B=调试  C=退出  D=抓取

抓取逻辑在后台线程运行，主线程负责 UI 和按键响应。
"""
import os
import sys
import time
import signal
import numpy as np
import cv2

# ---- 阶段计时 ----
T0 = time.monotonic()
_stages = []


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[ball_catch][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ---- PySide6 导入 ----
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont, QKeyEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QLabel

# ---- 摄像头 ----
from picamera2 import Picamera2

# ---- XGO 狗 ----
from xgolib import XGO

mark("imports done")

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "camera_starting": "摄像头启动中...",
            "corner_color": "A:颜色",
            "corner_debug": "B:调试",
            "corner_exit": "C:退出",
            "corner_catch": "D:抓取",
            "hint_idle": "A:切换颜色 | B:调试 | C:退出 | D:抓取",
            "hint_catch": "D:停止抓取",
            "hint_debug": "A:颜色 | B:距离+ | C:退出 | D:保存",
            "catching_dist": "🔍 抓取 {} | 距离:{:.1f}cm",
            "catching_search": "🔍 抓取 {} | 搜索中...",
            "debug_status": "调试模式 | 颜色:{} | 抓取距离:{:.1f} | 中心:{:.0f} | 最小半径:{:.0f}",
            "idle_status": "就绪 | 目标: {} | 距离阈值: {:.1f}",
            "color_red": "红色",
            "color_green": "绿色",
            "color_blue": "蓝色",
        },
        "en": {
            "camera_starting": "Camera starting...",
            "corner_color": "A:Color",
            "corner_debug": "B:Debug",
            "corner_exit": "C:Exit",
            "corner_catch": "D:Catch",
            "hint_idle": "A:Color | B:Debug | C:Exit | D:Catch",
            "hint_catch": "D:Stop",
            "hint_debug": "A:Color | B:Dist+ | C:Exit | D:Save",
            "catching_dist": "🔍 Catching {} | dist:{:.1f}cm",
            "catching_search": "🔍 Catching {} | searching...",
            "debug_status": "Debug | Color:{} | CatchDist:{:.1f} | Center:{:.0f} | MinR:{:.0f}",
            "idle_status": "Ready | Target: {} | Dist threshold: {:.1f}",
            "color_red": "Red",
            "color_green": "Green",
            "color_blue": "Blue",
        },
    })
    def _color_name(c):
        return _T(f"color_{c}")
except Exception:
    _T = lambda k, *a: k
    def _color_name(c):
        return COLOR_NAMES.get(c, c)

# ===================== 常量 =====================
AUTO_EXIT_SEC = 120
CAM_W, CAM_H = 320, 240
EMA_ALPHA = 0.4
MIN_AREA_THRESHOLD = 50
MIN_RADIUS_DEFAULT = 8

COLOR_RANGES = {
    "red": ([np.array([0, 110, 70]), np.array([10, 255, 255])],
            [np.array([170, 110, 70]), np.array([180, 255, 255])]),
    "green": ([np.array([46, 80, 10]), np.array([74, 255, 255])],),
    "blue": ([np.array([90, 50, 50]), np.array([140, 255, 255])],),
}
COLOR_NAMES = {"red": "红色", "green": "绿色", "blue": "蓝色"}
COLORS = ["red", "green", "blue"]

VAR_DIR = "/home/pi/luwu-os/apps/ball_catch"


def save_var(value, filename):
    try:
        with open(os.path.join(VAR_DIR, filename), 'w') as f:
            f.write(str(value))
    except Exception as e:
        print(f"[ball_catch] save error: {e}")


def load_var(default, filename):
    try:
        fpath = os.path.join(VAR_DIR, filename)
        if os.path.exists(fpath):
            with open(fpath, 'r') as f:
                return float(f.read().strip())
    except Exception:
        pass
    return default


# ===================== 抓取工作线程 =====================
class CatchingWorker(QThread):
    """后台线程：摄像头采集、球检测、机器人控制"""
    frame_ready = Signal(np.ndarray)       # 发送带标注的画面
    status_update = Signal(str)            # 状态文字更新
    catching_done = Signal()               # 抓取完成
    ball_found = Signal(bool, float)       # 是否找到, 距离

    def __init__(self):
        super().__init__()
        self._running = False
        self._pause = False
        self.color_idx = 0
        self.x_distance = 22.0
        self.x_center = 160.0
        self.min_radius = float(MIN_RADIUS_DEFAULT)
        self.picam2 = None
        self.dog = None
        self.dog_type = 'L'

        # 球检测状态
        self.mx = self.my = self.mr = 0.0
        self.uncircle_count = 0
        self.close_mode = False

        # 运动参数
        self.mintime_yaw = 0.8
        self.mintime_x = 0.1
        self.x_speed_far = 10
        self.x_speed_mid = 5
        self.turn_speed = 8

    def setup_robot(self):
        self.dog = XGO()
        fm = self.dog.read_firmware()
        self.dog_type = fm[0] if fm else 'L'
        print(f"[ball_catch] worker dog type: {self.dog_type}")
        self.dog.attitude('p', 15)
        self.dog.translation('z', 75)
        time.sleep(1)
        if self.dog_type == 'L':
            self.mintime_yaw = 0.8
            self.mintime_x = 0.1
            self.x_speed_far = 10
            self.x_speed_mid = 5
            self.turn_speed = 8
        else:
            self.mintime_yaw = 0.7
            self.mintime_x = 0.3
            self.x_speed_far = 10
            self.x_speed_mid = 5
            self.turn_speed = 8

    def setup_camera(self):
        self.picam2 = Picamera2()
        config = self.picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (CAM_W, CAM_H)}
        )
        self.picam2.configure(config)
        self.picam2.start()
        print("[ball_catch] worker camera started")

    def reset_ball_state(self):
        self.mx = self.my = self.mr = 0.0
        self.uncircle_count = 0
        self.close_mode = False

    def stop_robot(self):
        try:
            if self.dog:
                self.dog.move_x(0)
                self.dog.turn(0)
        except Exception:
            pass

    def cleanup(self):
        self._running = False
        self.wait(3000)
        try:
            if self.dog:
                self.dog.reset()
                time.sleep(0.3)
        except Exception:
            pass
        try:
            if self.picam2:
                self.picam2.stop()
                self.picam2.close()
        except Exception:
            pass

    def run(self):
        """主循环：抓取模式下的帧处理 + 机器人控制"""
        self._running = True
        self.reset_ball_state()
        self.stop_robot()
        self.dog.attitude('p', 15)
        print("[ball_catch] worker catching loop started")

        while self._running:
            if self._pause:
                time.sleep(0.1)
                continue

            try:
                frame = self.picam2.capture_array()
                if frame is None:
                    time.sleep(0.05)
                    continue
            except Exception as e:
                print(f"[ball_catch] worker capture error: {e}")
                time.sleep(0.1)
                continue

            image_copy = frame.copy()
            x, y, r, image_copy = self._detect_ball(image_copy)

            if r > 0:
                self._on_ball_detected(x, y, r)
                self.ball_found.emit(True, 54.82 - self.mr)
            else:
                self.uncircle_count += 1
                self.ball_found.emit(False, 0)
                lost_threshold = 15 if self.close_mode else 12
                if self.uncircle_count >= lost_threshold:
                    self._handle_lost()

            self.frame_ready.emit(image_copy)
            time.sleep(0.05)  # ~15fps + processing time

        # 清理摄像头
        try:
            if self.picam2:
                self.picam2.stop()
                self.picam2.close()
                self.picam2 = None
        except Exception:
            pass
        print("[ball_catch] worker loop ended")

    def _detect_ball(self, frame):
        color = COLORS[self.color_idx]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ranges = COLOR_RANGES[color]

        mask = None
        for lower, upper in ranges:
            m = cv2.inRange(hsv, lower, upper)
            mask = m if mask is None else cv2.bitwise_or(mask, m)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.dilate(mask, kernel, iterations=2)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filtered_mask = np.zeros_like(mask)
        valid_contours = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area >= MIN_AREA_THRESHOLD:
                cv2.drawContours(filtered_mask, [cnt], -1, 255, -1)
                valid_contours += 1

        if valid_contours == 0:
            return 0, 0, 0, frame

        masked = cv2.bitwise_and(frame, frame, mask=filtered_mask)
        gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, 1, 30,
            param1=28, param2=12,
            minRadius=int(self.min_radius), maxRadius=45,
        )

        if circles is not None:
            circles = np.round(circles[0, :]).astype("int")
            max_idx = np.argmax(circles[:, 2])
            x, y, r = int(circles[max_idx][0]), int(circles[max_idx][1]), int(circles[max_idx][2])
            cv2.circle(frame, (x, y), r, (0, 255, 0), 2)
            cv2.circle(frame, (x, y), 2, (0, 0, 255), 3)
            return x, y, r, frame
        return 0, 0, 0, frame

    def _on_ball_detected(self, x, y, r):
        self.uncircle_count = 0

        if self.mx == 0 and self.my == 0 and self.mr == 0:
            self.mx, self.my, self.mr = float(x), float(y), float(r)
        else:
            self.mx = EMA_ALPHA * x + (1 - EMA_ALPHA) * self.mx
            self.my = EMA_ALPHA * y + (1 - EMA_ALPHA) * self.my
            self.mr = EMA_ALPHA * r + (1 - EMA_ALPHA) * self.mr

        distance = 54.82 - self.mr
        if distance <= 0:
            distance = 1
        yaw_err = -(self.mx - self.x_center) / distance

        self._catching_decision(distance, yaw_err)

    def _catching_decision(self, distance, yaw_err):
        xd = self.x_distance
        xc = self.x_center

        # 近距离模式切换
        if distance < xd + 5:
            if not self.close_mode:
                self.close_mode = True
                self.dog.move_x(0)
                self.dog.turn(0)
                self.dog.attitude('p', 22)
                time.sleep(0.2)
        else:
            if self.close_mode:
                self.close_mode = False
                self.dog.attitude('y', 0)
                time.sleep(0.1)

        y1, y2 = 20, 25
        catch_dist = xd + 5 if self.close_mode else xd

        # 条件一抓取
        if distance < catch_dist and -y1 / distance <= yaw_err <= y1 / distance:
            print("[ball_catch] 满足条件一，抓取!")
            self.dog.attitude('y', 5 * yaw_err)
            self.dog.translation('x', distance - 20)
            time.sleep(0.5)
            self._do_catch()
            return

        # 条件二抓取
        if distance < catch_dist and -y2 <= self.mx - xc <= y2:
            print("[ball_catch] 满足条件二，抓取!")
            self.dog.attitude('y', 0.25 * (xc - self.mx))
            self.dog.translation('x', distance - 20)
            time.sleep(0.5)
            self._do_catch()
            return

        # 移动控制
        if self.close_mode:
            if distance >= xd:
                trans_x = min((distance - xd) * 1.5, 15)
                self.dog.translation('x', trans_x)
                time.sleep(0.2)
            if abs(yaw_err) > y1 / distance:
                att_y = max(-10, min(10, 3 * yaw_err))
                self.dog.attitude('y', att_y)
                time.sleep(0.15)
        else:
            if yaw_err > y1 / distance:
                turn_time = min(abs(0.4 * yaw_err) / 8 + self.mintime_yaw, 1.5)
                self.dog.gait_type('slow_trot')
                self.dog.turn(self.turn_speed)
                time.sleep(turn_time)
                self.dog.turn(0)
                time.sleep(0.15)
            elif yaw_err < -y1 / distance:
                turn_time = min(abs(0.4 * yaw_err) / 8 + self.mintime_yaw, 1.5)
                self.dog.gait_type('slow_trot')
                self.dog.turn(-self.turn_speed)
                time.sleep(turn_time)
                self.dog.turn(0)
                time.sleep(0.15)
            else:
                if distance >= xd + 10:
                    step_time = 0.5
                    speed = self.x_speed_far
                elif distance >= xd + 3:
                    step_time = 0.3
                    speed = self.x_speed_mid
                else:
                    step_time = 0.2
                    speed = self.x_speed_mid
                self.dog.gait_type('slow_trot')
                self.dog.move_x(speed)
                time.sleep(step_time)
                self.dog.move_x(0)
                time.sleep(0.2)

    def _do_catch(self):
        """执行机械臂抓取"""
        self.dog.translation('z', 10)
        self.dog.attitude('p', 15)
        self.dog.claw(5)
        time.sleep(1)
        self.dog.arm_polar(200, 130)
        time.sleep(2)
        self.dog.claw(245)
        time.sleep(1)
        self.dog.arm_polar(90, 100)
        time.sleep(1)
        self._on_catch_finish()

    def _on_catch_finish(self):
        """抓取后处理"""
        self.dog.move_x(0)
        self.dog.turn(0)
        self.dog.translation('x', 0)
        self.dog.translation('y', 0)
        self.dog.attitude('y', 0)
        self.dog.attitude('p', 0)
        # 放下手臂
        self.dog.translation('z', 10)
        self.dog.attitude('p', 15)
        time.sleep(1)
        self.dog.arm_polar(200, 130)
        time.sleep(2)
        self.dog.claw(15)
        time.sleep(1)
        self.dog.arm_polar(90, 100)
        time.sleep(2)
        self._running = False
        self.catching_done.emit()
        print("[ball_catch] 抓取完成")

    def _handle_lost(self):
        self.uncircle_count = 0
        if self.close_mode:
            print("[ball_catch] 近距离丢球，复位姿态")
            self.dog.translation('x', 0)
            self.dog.attitude('y', 0)
            time.sleep(0.3)
        else:
            print("[ball_catch] 远距离丢球，停腿重找")
            self.dog.move_x(0)
            self.dog.turn(0)
            time.sleep(0.5)


# ===================== PySide6 页面 =====================
class BallCatchPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: black;")
        self._first_paint_logged = False

        # 状态
        self.color_idx = 0
        self.mode = "idle"  # idle, catching, debug
        self.x_distance = load_var(22.0, "x_distance.txt")
        self.x_center = load_var(160.0, "x_center.txt")
        self.min_radius = load_var(float(MIN_RADIUS_DEFAULT), "min_radius.txt")

        # ---- 摄像头画面（全屏填充） ----
        self.camera_label = QLabel(_T("camera_starting"), self)
        self.camera_label.setStyleSheet(
            "background-color: black; color: #666; border: none;"
        )
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setScaledContents(True)

        # ---- 状态文字（底部叠加） ----
        self.status_label = QLabel(self)
        self.status_label.setStyleSheet(
            "color: #00ff88; font-size: 13px; background: rgba(0,0,0,120); border: none; padding: 4px 8px;"
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        # ---- 四角按键说明（叠加在视频上） ----
        corner_style = "color: #aaccee; font-size: 11px; background: rgba(0,0,0,100); border: none; padding: 2px 6px;"
        self.corner_tl = QLabel(_T("corner_color"), self)
        self.corner_tl.setStyleSheet(corner_style)
        self.corner_tr = QLabel(_T("corner_debug"), self)
        self.corner_tr.setStyleSheet(corner_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel(_T("corner_exit"), self)
        self.corner_bl.setStyleSheet(corner_style)
        self.corner_br = QLabel(_T("corner_catch"), self)
        self.corner_br.setStyleSheet(corner_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        # ---- 提示（顶部叠加） ----
        self.hint = QLabel(_T("hint_idle"), self)
        self.hint.setStyleSheet(
            "color: #8899bb; font-size: 11px; background: rgba(0,0,0,100); border: none; padding: 2px 8px;"
        )
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 空闲模式摄像头 ----
        self._idle_picam2 = None
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._show_idle_frame)
        self._idle_timer.start(100)  # 10fps idle

        # ---- 自动退出 ----
        self._auto_exit_timer = QTimer(self)
        self._auto_exit_timer.timeout.connect(self.close)
        self._auto_exit_timer.start(AUTO_EXIT_SEC * 1000)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ---- 后台抓取线程 ----
        self._worker = None

        # 启动硬件
        QTimer.singleShot(100, self._init_idle_camera)

    def _init_idle_camera(self):
        try:
            self._idle_picam2 = Picamera2()
            config = self._idle_picam2.create_preview_configuration(
                main={"format": "RGB888", "size": (CAM_W, CAM_H)}
            )
            self._idle_picam2.configure(config)
            self._idle_picam2.start()
            print("[ball_catch] idle camera started")
        except Exception as e:
            print(f"[ball_catch] idle camera error: {e}")
        self._update_status()

    def _show_idle_frame(self):
        if self.mode != "idle" or self._idle_picam2 is None:
            return
        try:
            frame = self._idle_picam2.capture_array()
            if frame is None:
                return
        except Exception:
            return

        if self.mode == "idle":
            # picamera2 "RGB888" 实际是 BGR，转为 RGB，再水平翻转
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.flip(frame, 1)
            h, w, c = frame.shape
            qimg = QImage(frame.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888)
            self.camera_label.setPixmap(QPixmap.fromImage(qimg))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        if w == 0 or h == 0:
            return

        # 摄像头全屏填充
        self.camera_label.setGeometry(0, 0, w, h)

        # 四角按键
        pad = 8
        self.corner_tl.adjustSize()
        self.corner_tl.move(pad, pad)
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.adjustSize()
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

        # 顶部提示
        self.hint.adjustSize()
        self.hint.move((w - self.hint.width()) // 2, pad + self.corner_tl.height() + 4)

        # 底部状态
        self.status_label.setFixedWidth(w - 32)
        self.status_label.adjustSize()
        self.status_label.move((w - self.status_label.width()) // 2,
                               h - self.status_label.height() - pad - self.corner_bl.height() - 4)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")

    def keyPressEvent(self, ev: QKeyEvent):
        # 物理按键映射 (gpio-keys DTS):
        #   A(GPIO17)→Key_Left, B(GPIO22)→Key_Right, C(GPIO23)→Key_Back, D(GPIO24)→Key_Return
        if ev.key() == Qt.Key.Key_Left:
            # A 键：切换颜色
            self._change_color()
        elif ev.key() == Qt.Key.Key_Right:
            # B 键：调试模式 / 调试模式下增加距离
            if self.mode == "debug":
                self.x_distance += 0.5
                self._update_status()
            else:
                self._toggle_debug()
        elif ev.key() == Qt.Key.Key_Back:
            # C 键：退出
            print("[ball_catch] KEY_BACK(C) -> exit", flush=True)
            self.close()
        elif ev.key() == Qt.Key.Key_Return or ev.key() == Qt.Key.Key_Enter:
            # D 键：抓取 / 调试模式下保存
            if self.mode == "debug":
                self._save_debug()
            else:
                self._start_catching()

    def _change_color(self):
        self.color_idx = (self.color_idx + 1) % 3
        self._update_status()
        print(f"[ball_catch] color changed to {COLORS[self.color_idx]}")

    def _start_catching(self):
        if self.mode == "catching":
            # 停止抓取
            self.mode = "idle"
            if self._worker:
                self._worker._running = False
                self._worker.stop_robot()
            self.hint.setText(_T("hint_idle"))
            self._update_status()
            print("[ball_catch] catch mode exit")
            return

        # 开始抓取
        self.mode = "catching"
        self._auto_exit_timer.start(AUTO_EXIT_SEC * 1000)
        self.hint.setText(_T("hint_catch"))
        self._update_status()

        # 停止空闲摄像头
        if self._idle_picam2:
            try:
                self._idle_picam2.stop()
                self._idle_picam2.close()
            except Exception:
                pass
            self._idle_picam2 = None

        # 启动后台抓取线程
        self._worker = CatchingWorker()
        self._worker.color_idx = self.color_idx
        self._worker.x_distance = self.x_distance
        self._worker.x_center = self.x_center
        self._worker.min_radius = self.min_radius

        self._worker.frame_ready.connect(self._on_worker_frame)
        self._worker.ball_found.connect(self._on_ball_status)
        self._worker.catching_done.connect(self._on_catching_finished)

        self._worker.setup_robot()
        self._worker.setup_camera()
        self._worker.start()
        print("[ball_catch] catch mode enter")

    def _on_worker_frame(self, frame: np.ndarray):
        """接收后台线程的帧并显示"""
        if self.mode != "catching":
            return
        try:
            # picamera2 "RGB888" 实际是 BGR，转为 RGB，再水平翻转
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.flip(frame, 1)
            h, w, c = frame.shape
            qimg = QImage(frame.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888)
            self.camera_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception:
            pass

    def _on_ball_status(self, found, distance):
        """后台线程发来的球检测状态"""
        if self.mode != "catching":
            return
        color_name = _color_name(COLORS[self.color_idx])
        if found:
            self.status_label.setText(_T("catching_dist", color_name, distance))
        else:
            self.status_label.setText(_T("catching_search", color_name))

    def _on_catching_finished(self):
        """抓取完成后的处理"""
        print("[ball_catch] catch mode finished, waiting for worker...")
        # 等待工作线程完全退出（包括摄像头释放）
        if self._worker:
            self._worker.wait(5000)
        self.mode = "idle"
        self.hint.setText(_T("hint_idle"))
        self._update_status()

        # 重新打开空闲摄像头
        if self._idle_picam2 is None:
            try:
                self._idle_picam2 = Picamera2()
                config = self._idle_picam2.create_preview_configuration(
                    main={"format": "RGB888", "size": (CAM_W, CAM_H)}
                )
                self._idle_picam2.configure(config)
                self._idle_picam2.start()
                print("[ball_catch] idle camera restarted")
            except Exception as e:
                print(f"[ball_catch] reinit camera error: {e}")

    def _toggle_debug(self):
        if self.mode == "debug":
            self.mode = "idle"
            self.hint.setText(_T("hint_idle"))
        else:
            self.mode = "debug"
            self.hint.setText(_T("hint_debug"))
        self._update_status()

    def _save_debug(self):
        save_var(self.x_distance, "x_distance.txt")
        save_var(self.x_center, "x_center.txt")
        save_var(self.min_radius, "min_radius.txt")
        self._update_status()
        print(f"[ball_catch] saved: x_dist={self.x_distance}, x_center={self.x_center}, min_r={self.min_radius}")

    def _update_status(self):
        color_name = _color_name(COLORS[self.color_idx])
        if self.mode == "debug":
            self.status_label.setText(
                _T("debug_status", color_name, self.x_distance, self.x_center, self.min_radius)
            )
        elif self.mode == "catching":
            self.status_label.setText(_T("catching_search", color_name))
        else:
            self.status_label.setText(_T("idle_status", color_name, self.x_distance))

    def closeEvent(self, ev):
        print("[ball_catch] closing", flush=True)
        self._idle_timer.stop()
        self._auto_exit_timer.stop()

        if self._worker and self._worker.isRunning():
            self._worker._running = False
            self._worker.stop_robot()
            self._worker.quit()
            self._worker.wait(5000)

        if self._idle_picam2:
            try:
                self._idle_picam2.stop()
                self._idle_picam2.close()
            except Exception:
                pass
        super().closeEvent(ev)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = BallCatchPage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[ball_catch] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
