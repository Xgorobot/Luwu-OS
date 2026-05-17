#!/usr/bin/env python3
"""
PySide6 手势识别应用 — 由 Luwu OS launcher 启动。
使用摄像头 + ONNX (cv2.dnn) 实时识别手势：
  Good, 1, 2, 3, 4, 5, Stone, OK, Rock
C 键（左下物理键 → KEY_LEFT）退出。

模型来自 MediaPipe ONNX：
  - palm_detection_mediapipe_2023feb.onnx（手掌检测）
  - handpose_estimation_mediapipe_2023feb.onnx（手部关键点）
"""
import os
import sys
import time
import signal
import math
import subprocess
import numpy as np
import cv2

# ===================== 阶段计时 =====================
T0 = time.monotonic()
_stages = []  # [(name, abs_ms)]


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[gesture][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== 重载导入 =====================
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QLabel

from picamera2 import Picamera2

mark("PySide6 imports done")

# 导入 ONNX 手部模块
sys.path.insert(0, '/home/pi/luwu-os/model')
from mp_palmdet import MPPalmDet
from mp_handpose import MPHandPose

mark("onnx model imports done")

# 导入 XGO 机器狗库
sys.path.insert(0, '/home/pi/lib')
try:
    from xgolib import XGO
    mark("xgolib import done")
except Exception as _e:
    XGO = None
    print(f"[gesture] xgolib import failed: {_e}", flush=True)

# ===================== 常量 =====================
CAM_W, CAM_H = 320, 240

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "camera_starting": "启动摄像头中...",
            "corner_exit": "C: 退出",
            "camera_error": "摄像头错误: {}",
        },
        "en": {
            "camera_starting": "Camera starting...",
            "corner_exit": "C: Exit",
            "camera_error": "Camera error: {}",
        },
    })
except Exception:
    _T = lambda k, *a: k

PALM_MODEL = '/home/pi/luwu-os/model/palm_detection_mediapipe_2023feb.onnx'
HAND_MODEL = '/home/pi/luwu-os/model/handpose_estimation_mediapipe_2023feb.onnx'

# 手部关键点连接（用于绘制骨架），基于 MediaPipe 21 点模型
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),       # 拇指
    (0, 5), (5, 6), (6, 7), (7, 8),       # 食指
    (0, 9), (9, 10), (10, 11), (11, 12),  # 中指
    (0, 13), (13, 14), (14, 15), (15, 16),# 无名指
    (0, 17), (17, 18), (18, 19), (19, 20),# 小指
    (5, 9), (9, 13), (13, 17),             # 指根横向
]

# 手势名称映射
GESTURE_NAMES = {
    "Good": "Good",
    "1": "One",
    "2": "Two",
    "3": "Three",
    "4": "Four",
    "5": "Five",
    "Stone": "Stone",
    "OK": "OK",
    "Rock": "Rock",
}

# 手势 → 音频文件映射（不含扩展名）
GESTURE_AUDIO = {
    'Good': 'good',
    '1': 'one',
    '2': 'two',
    '3': 'three',
    '4': 'four',
    '5': 'five',
    'Stone': 'stone',
    'OK': 'OK',
    'Rock': 'six',  # Rock 复用 six 音效
}

# 手势 → 机器狗动作 ID 映射（参考 hands.py）
GESTURE_ACTION = {
    'Good': 23,
    '1': 7,
    '2': 8,
    '3': 9,
    '4': 22,
    '5': 13,  # 招手
    'Stone': 20,
    'OK': 19,
    'Rock': 24,  # 等同 six
}

# 音频目录
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music')

# 动作冷却时间（秒），避免重复触发
ACTION_COOLDOWN = 3.0


# ===================== 手势识别核心 =====================

def vector_2d_angle(v1, v2):
    """计算两个二维向量之间的角度。"""
    v1_x, v1_y = v1[0], v1[1]
    v2_x, v2_y = v2[0], v2[1]
    try:
        angle_ = math.degrees(
            math.acos(
                (v1_x * v2_x + v1_y * v2_y)
                / (((v1_x ** 2 + v1_y ** 2) ** 0.5) * ((v2_x ** 2 + v2_y ** 2) ** 0.5))
            )
        )
    except:
        angle_ = 180
    return angle_


def hand_angle(hand_):
    """计算 21 个手部关键点的 5 个手指角度。"""
    angle_list = []
    # 大拇指角度
    angle_ = vector_2d_angle(
        (int(hand_[0][0]) - int(hand_[2][0]), int(hand_[0][1]) - int(hand_[2][1])),
        (int(hand_[3][0]) - int(hand_[4][0]), int(hand_[3][1]) - int(hand_[4][1])),
    )
    angle_list.append(angle_)
    # 食指角度
    angle_ = vector_2d_angle(
        (int(hand_[0][0]) - int(hand_[6][0]), int(hand_[0][1]) - int(hand_[6][1])),
        (int(hand_[7][0]) - int(hand_[8][0]), int(hand_[7][1]) - int(hand_[8][1])),
    )
    angle_list.append(angle_)
    # 中指角度
    angle_ = vector_2d_angle(
        (int(hand_[0][0]) - int(hand_[10][0]), int(hand_[0][1]) - int(hand_[10][1])),
        (int(hand_[11][0]) - int(hand_[12][0]), int(hand_[11][1]) - int(hand_[12][1])),
    )
    angle_list.append(angle_)
    # 无名指角度
    angle_ = vector_2d_angle(
        (int(hand_[0][0]) - int(hand_[14][0]), int(hand_[0][1]) - int(hand_[14][1])),
        (int(hand_[15][0]) - int(hand_[16][0]), int(hand_[15][1]) - int(hand_[16][1])),
    )
    angle_list.append(angle_)
    # 小拇指角度
    angle_ = vector_2d_angle(
        (int(hand_[0][0]) - int(hand_[18][0]), int(hand_[0][1]) - int(hand_[18][1])),
        (int(hand_[19][0]) - int(hand_[20][0]), int(hand_[19][1]) - int(hand_[20][1])),
    )
    angle_list.append(angle_)
    return angle_list


def hand_pos(angle):
    """
    手势识别函数 — 根据手指角度判断手势。
    返回手势名称字符串，未识别到返回 None。
    """
    if not angle or len(angle) != 5:
        return None

    pos = None
    thumb_threshold = 55
    finger_threshold = 55

    f1, f2, f3, f4, f5 = angle[0], angle[1], angle[2], angle[3], angle[4]

    thumb_up = f1 < thumb_threshold
    index_up = f2 < finger_threshold
    middle_up = f3 < finger_threshold
    ring_up = f4 < finger_threshold
    pinky_up = f5 < finger_threshold

    thumb_down = f1 >= thumb_threshold
    index_down = f2 >= finger_threshold
    middle_down = f3 >= finger_threshold
    ring_down = f4 >= finger_threshold
    pinky_down = f5 >= finger_threshold

    # 五指张开
    if thumb_up and index_up and middle_up and ring_up and pinky_up:
        pos = '5'
    # 拳头
    elif thumb_down and index_down and middle_down and ring_down and pinky_down:
        pos = 'Stone'
    # 竖起大拇指
    elif thumb_up and index_down and middle_down and ring_down and pinky_down:
        pos = 'Good'
    # 摇滚手势
    elif thumb_up and index_down and middle_up and ring_up and pinky_down:
        pos = 'Rock'
    # OK 手势
    elif thumb_up and index_down and middle_up and ring_up and pinky_up:
        pos = 'OK'
    # 数字手势
    elif thumb_down and index_up and middle_down and ring_down and pinky_down:
        pos = '1'
    elif thumb_down and index_up and middle_up and ring_down and pinky_down:
        pos = '2'
    elif thumb_down and index_up and middle_up and ring_up and pinky_down:
        pos = '3'
    elif thumb_down and index_up and middle_up and ring_up and pinky_up:
        pos = '4'

    # 放宽阈值兜底
    if not pos:
        relaxed = 70
        t_up = f1 < relaxed
        i_up = f2 < relaxed
        m_up = f3 < relaxed
        r_up = f4 < relaxed
        p_up = f5 < relaxed
        if t_up and i_up and m_up and r_up and p_up:
            pos = '5'
        elif not t_up and not i_up and not m_up and not r_up and not p_up:
            pos = 'Stone'
        elif t_up and not i_up and not m_up and not r_up and not p_up:
            pos = 'Good'
        elif not t_up and i_up and not m_up and not r_up and not p_up:
            pos = '1'
        elif not t_up and i_up and m_up and not r_up and not p_up:
            pos = '2'

    return pos


def draw_hand_landmarks(img, pts, color=(0, 255, 0), thickness=2):
    """在手部图像上绘制 21 个关键点和连接线。"""
    h, w = img.shape[:2]
    for x, y in pts:
        if 0 <= x < w and 0 <= y < h:
            cv2.circle(img, (x, y), 3, color, -1)
    for i, j in HAND_CONNECTIONS:
        if i < len(pts) and j < len(pts):
            x1, y1 = pts[i]
            x2, y2 = pts[j]
            if 0 <= x1 < w and 0 <= y1 < h and 0 <= x2 < w and 0 <= y2 < h:
                cv2.line(img, (x1, y1), (x2, y2), color, thickness)


# ===================== ONNX 手部检测器 =====================
class HandDetectorONNX:
    """封装 MPPalmDet + MPHandPose，提供简化的单帧推理。"""

    def __init__(self, max_num_hands=1, conf=0.7):
        self._palm_det = MPPalmDet(PALM_MODEL, scoreThreshold=conf)
        self._hand_pose = MPHandPose(HAND_MODEL, confThreshold=conf)
        self.max_num_hands = max_num_hands

    def run(self, cv_img_bgr):
        """
        输入 BGR 图像 (H,W,3)，返回检测结果列表。
        每个结果 dict 含: rect, dlandmark(21个(x,y)元组), hand_angle(5个角度), right_left
        """
        palms = self._palm_det.infer(cv_img_bgr)
        results = []
        if palms is None or len(palms) == 0:
            return results
        for palm in palms[:self.max_num_hands]:
            hand = self._hand_pose.infer(cv_img_bgr, palm)
            if hand is None:
                continue
            x1, y1, x2, y2 = int(hand[0]), int(hand[1]), int(hand[2]), int(hand[3])
            lm = hand[4:67].reshape(21, 3)
            pts = [(int(lm[i, 0]), int(lm[i, 1])) for i in range(21)]
            rect = [x1, y1, x2 - x1, y2 - y1]
            right_left = 'R' if float(hand[130]) > 0.5 else 'L'
            results.append({
                'rect': rect,
                'dlandmark': pts,
                'hand_angle': hand_angle(pts),
                'right_left': right_left,
            })
        return results


# ===================== PySide6 页面 =====================
class GesturePage(QWidget):
    """手势识别 LCD 界面 — 全屏视频。"""

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: black;")
        self._first_paint_logged = False

        # ---- 全屏摄像头画面 ----
        self.camera_label = QLabel(_T("camera_starting"), self)
        self.camera_label.setStyleSheet(
            "background-color: black; color: #666;"
        )
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setScaledContents(True)

        # ---- 四角按键提示 ----
        corner_style = "color: rgba(255,255,255,100); font-size: 12px; background: transparent;"
        self.corner_bl = QLabel(_T("corner_exit"), self)
        self.corner_bl.setStyleSheet(corner_style)

        # ---- ONNX 手势检测器 ----
        self.detector = HandDetectorONNX(max_num_hands=1, conf=0.7)
        mark("ONNX hand detector init")

        # ---- XGO 机器狗初始化 ----
        self._dog = None
        self._init_dog()

        # ---- 摄像头初始化 ----
        self.picam2 = None
        self._init_camera()

        # ---- 稳定性追踪 ----
        self._prev_gesture = None
        self._stable_count = 0
        self.MIN_STABLE_FRAMES = 3

        # ---- 动作冷却 ----
        self._last_action_time = 0

        # ---- 定时抓帧 ----
        self.camera_timer = QTimer(self)
        self.camera_timer.timeout.connect(self._process_frame)
        self.camera_timer.start(100)  # ~10fps (ONNX 推理较慢)

        # ---- 自动退出兜底 ----

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _init_dog(self):
        """初始化 XGO 机器狗。"""
        if XGO is None:
            print("[gesture] XGO 库不可用，仅启用手势识别", flush=True)
            return
        try:
            self._dog = XGO()
            self._dog.reset()
            print("[gesture] XGO 初始化成功", flush=True)
            mark("xgo dog ready")
        except Exception as e:
            self._dog = None
            print(f"[gesture] XGO 初始化失败: {e}", flush=True)

    def _init_camera(self):
        """初始化 Picamera2。"""
        try:
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(
                main={"format": "RGB888", "size": (CAM_W, CAM_H)}
            )
            self.picam2.configure(config)
            self.picam2.start()
            time.sleep(0.3)
            for _ in range(5):
                self.picam2.capture_array()
            mark("camera started")
        except Exception as e:
            print(f"[gesture] camera init error: {e}", flush=True)
            self.camera_label.setText(_T("camera_error", e))

    # ---- 布局事件 ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        # 全屏铺满
        self.camera_label.setGeometry(0, 0, w, h)
        # 左下角按键提示
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
            print("[gesture] boot breakdown:\n" + summary, flush=True)

    def _stage_summary(self) -> str:
        lines = []
        prev = 0.0
        for name, ms in _stages:
            lines.append(f"{name}: {ms:.0f}ms (+{ms - prev:.0f})")
            prev = ms
        return " | ".join(lines)

    # ---- 按键 ----
    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Left or ev.key() == Qt.Key.Key_Back:
            print("[gesture] KEY_LEFT/Key_Back -> exit", flush=True)
            self.close()

    def closeEvent(self, ev):
        print("[gesture] closing", flush=True)
        self.camera_timer.stop()
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception:
                pass
            self.picam2 = None
        if self._dog is not None:
            try:
                self._dog.reset()
            except Exception:
                pass
        # 兜底：杀掉残留 aplay/ffplay/mplayer
        try:
            os.system("pkill -f 'aplay.*gesture/music' 2>/dev/null")
            os.system("pkill -f 'ffplay.*gesture/music' 2>/dev/null")
            os.system("pkill -f 'mplayer.*gesture/music' 2>/dev/null")
        except Exception:
            pass
        super().closeEvent(ev)

    # ---- 手势动作 & 声音 ----
    def _process_gesture(self, gesture_result):
        """根据手势触发机器狗动作并播放对应音频。"""
        now = time.time()
        if now - self._last_action_time < ACTION_COOLDOWN:
            return  # 冷却中，跳过

        action_id = GESTURE_ACTION.get(gesture_result)
        audio_key = GESTURE_AUDIO.get(gesture_result)
        if action_id is None and audio_key is None:
            return

        self._last_action_time = now

        # 1. 触发机器狗动作
        if action_id is not None and self._dog is not None:
            try:
                self._dog.action(action_id)
                print(f"[gesture] dog.action({action_id}) for {gesture_result}", flush=True)
            except Exception as e:
                print(f"[gesture] dog.action error: {e}", flush=True)

        # 2. 播放音频（拄 ai 程序的成功写法：os.system + aplay + & 后台；
        #    需要提前将 mp3 转为 wav，aplay 不支持 mp3 解码）
        if audio_key:
            audio_path = os.path.join(MUSIC_DIR, f"{audio_key}.wav")
            if os.path.exists(audio_path):
                try:
                    os.system(f"aplay -D default -q '{audio_path}' 2>/dev/null &")
                    print(f"[gesture] play audio: {audio_key}.wav", flush=True)
                except Exception as e:
                    print(f"[gesture] aplay error: {e}", flush=True)
            else:
                print(f"[gesture] audio file not found: {audio_path}", flush=True)

    # ---- 帧处理 ----
    def _process_frame(self):
        if self.picam2 is None:
            return
        try:
            frame = self.picam2.capture_array()
        except Exception as e:
            print(f"[gesture] capture error: {e}", flush=True)
            return

        # 水平翻转（镜像）
        frame_bgr = cv2.flip(frame, 1)
        frame_display = frame_bgr.copy()

        # ONNX 手势检测
        gesture_result = None
        try:
            results = self.detector.run(frame_bgr)
        except Exception as e:
            print(f"[gesture] detector error: {e}", flush=True)
            results = []

        if results:
            for res in results:
                # 绘制手部关键点
                pts = res['dlandmark']
                draw_hand_landmarks(frame_display, pts, color=(0, 255, 0), thickness=2)

                # 手势识别
                ges = hand_pos(res['hand_angle'])
                if ges:
                    gesture_result = ges

        # 稳定性计数
        current_gesture = gesture_result
        if current_gesture == self._prev_gesture:
            self._stable_count += 1
        else:
            self._prev_gesture = current_gesture
            self._stable_count = 1

        # 在帧上绘制已确认的手势文字，并触发动作/声音
        if self._prev_gesture is not None and self._stable_count >= self.MIN_STABLE_FRAMES:
            display_name = GESTURE_NAMES.get(self._prev_gesture, self._prev_gesture)
            cv2.putText(
                frame_display,
                display_name,
                (10, 40),
                cv2.FONT_HERSHEY_COMPLEX,
                1.2,
                (0, 255, 0),
                2,
            )
            # 触发手势动作 & 播放声音
            self._process_gesture(self._prev_gesture)

        # 转换为 QPixmap 显示
        frame_rgb = cv2.cvtColor(frame_display, cv2.COLOR_BGR2RGB)
        h, w, c = frame_rgb.shape
        qimg = QImage(frame_rgb.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self.camera_label.setPixmap(pixmap)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = GesturePage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[gesture] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
