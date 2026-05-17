#!/usr/bin/env python3
"""
Luwu OS - Face Follow App (PySide6)
人脸跟随: 检测人脸位置并控制机器狗头部/机身跟随

Physical button mapping (from luwu-keys.dts gpio-keys):
  A (GPIO17, top-left)     KEY_LEFT   → toggle camera mirror
  B (GPIO22, top-right)    KEY_RIGHT  → toggle robot control
  C (GPIO23, bottom-left)  KEY_BACK   → exit
  D (GPIO24, bottom-right) KEY_ENTER  → 启动 API 标定序列(调试用)

Debug logging:
  启动后自动在 /tmp/face_follow_log/<timestamp>/ 创建:
    - video.mp4    带标注的画面录制(人脸框/十字/误差线/文字)
    - frames.csv   每帧误差与下发控制量
    - calib.txt    标定步骤时间表
  按 D 键会按顺序依次试验 attitude(y,±15) / attitude(p,±18) /
  translation(y,±18) / translation(x,±25),每个动作保持 1.5 秒,
  让我们从录像里看出每个 API 实际让机器人怎么动。
"""

import os
import sys
import csv
import time
import signal
import datetime

import cv2
from picamera2 import Picamera2

from PySide6.QtCore import Qt, QTimer, QSocketNotifier
from PySide6.QtGui import QKeyEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QLabel

# ---- Paths ----
FACE_MODEL_PATH = "/home/pi/luwu-os/model/face_detection_yunet_2023mar.onnx"
KEYS_FIFO = "/tmp/luwu_keys.fifo"

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "title": "人脸跟随",
            "corner_mirror": "A:镜像",
            "corner_control": "B:控制",
            "corner_exit": "C:退出",
            "corner_calib": "D:标定",
        },
        "en": {
            "title": "Face Follow",
            "corner_mirror": "A:Mirror",
            "corner_control": "B:Control",
            "corner_exit": "C:Exit",
            "corner_calib": "D:Calibrate",
        },
    })
except Exception:
    _T = lambda k, *a: k

# ---- Robot control ----
_xgo_dog = None
_robot_available = False
_is_rider = False

try:
    from xgolib import XGO, XGO_RIDER
    _xgo_dog = XGO()
    _robot_available = True
    _is_rider = isinstance(_xgo_dog, XGO_RIDER)
    print(f"[face_follow] XGO 初始化成功 (rider={_is_rider})", flush=True)
except Exception as e:
    print(f"[face_follow] XGO 初始化失败: {e} (仅预览模式)", flush=True)
    _robot_available = False


def robot_attitude(axis_list, angle_list):
    """安全地调整机器人姿态"""
    if _robot_available and _xgo_dog:
        try:
            _xgo_dog.attitude(axis_list, angle_list)
        except Exception as e:
            print(f"[face_follow] robot_attitude error: {e}", flush=True)


def robot_translation(axis_list, value_list):
    """安全地调整机器人平移(足端不动,机身三轴平移)。
    dog 范围: x±35, y±19.5, z[60,120] 等,会被 xgolib 内部 clamp。"""
    if _robot_available and _xgo_dog:
        try:
            _xgo_dog.translation(axis_list, value_list)
        except Exception as e:
            print(f"[face_follow] robot_translation error: {e}", flush=True)


def robot_reset():
    """重置机器人姿态"""
    if _robot_available and _xgo_dog:
        try:
            _xgo_dog.reset()
        except Exception as e:
            print(f"[face_follow] robot_reset error: {e}", flush=True)


# ============================================================================
# Face Follow Widget
# ============================================================================
class FaceFollowWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0a0a1a;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ---- State ----
        self.robot_control_enabled = True
        self.mirror_enabled = True

        # ---- 闭环跟随控制(基于实测标定数据重写) ----
        # 标定结果(/tmp/face_follow_log/20260517_210055):
        #   attitude('y', +15) → 画面人脸左移 ≈60px  → 增益 ≈4 px/度
        #   attitude('p', -18) → 画面人脸下移 ≈60px  → 增益 ≈3.3 px/度
        #     （p 正值是前俰,人脸会往画面顶上跑出画!）
        #   translation('y',±18) 跨度仅 ≈12px,补偿意义不大→ 不用
        #   translation('x') / attitude('r') 几乎无效 → 不用
        # 所以只用 attitude('y','p'),且 pitch 符号与之前相反。
        self.current_yaw = 0.0      # 机身偏航(左右),单位度
        self.current_pitch = 0.0    # 机身俯仰(上下),单位度
        self.current_tx = 0.0       # 保留字段(未使用)
        self.current_ty = 0.0       # 保留字段(未使用)
        # 增益: 实测增益 px/度 × kp 度/像素 = 单帧误差衰减率,取 ≈0.3
        self.kp_x = 0.08            # 0.08 * 4 ≈ 0.32 单帧衰减
        self.kp_y = 0.10            # 0.10 * 3.3 ≈ 0.33
        self.deadzone_px = 10       # 像素死区
        # 机械范围(以仪表上限为准,dog yaw±16/pitch±22; rider±1)
        self.yaw_limit = 1.0 if _is_rider else 15.0
        self.pitch_limit = 1.0 if _is_rider else 15.0  # 預留余量避免人脸出画
        self.ty_limit = 0.0         # 不再用 translation y

        # ---- 调试日志与录像 ----
        # phase 记录当前状态: 'follow' / 'calib_<name>' / 'calib_done'
        self.phase = "follow"
        self._calib_queue = []
        ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = f"/tmp/face_follow_log/{ts_str}"
        os.makedirs(self.log_dir, exist_ok=True)
        self.video_path = os.path.join(self.log_dir, "video.mp4")
        self.csv_path = os.path.join(self.log_dir, "frames.csv")
        self.calib_path = os.path.join(self.log_dir, "calib.txt")
        # VideoWriter 在首帧到达后才创建(需要确定画面尺寸)
        self._video_writer = None
        try:
            self._csv_file = open(self.csv_path, "w", newline="", buffering=1)
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow([
                "t_ms", "phase", "found",
                "err_x", "err_y",
                "cur_yaw", "cur_pitch", "cur_ty",
                "sent_yaw", "sent_pitch", "sent_ty",
                "mirror", "ctrl",
            ])
            self._calib_log = open(self.calib_path, "w", buffering=1)
            print(f"[face_follow] log dir: {self.log_dir}", flush=True)
        except Exception as e:
            print(f"[face_follow] log open error: {e}", flush=True)
            self._csv_file = None
            self._csv_writer = None
            self._calib_log = None
        self._t_start = time.monotonic()

        # ---- Face detector (YuNet) ----
        self.face_detector = None
        self._init_face_detector()

        # ---- Camera ----
        self.picam2 = None
        self.camera_active = False
        self.camera_size = (320, 240)

        # ---- Camera display ----
        self.camera_label = QLabel(self)
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setStyleSheet("background-color: black;")

        # ---- Status label ----
        self.status_label = QLabel(_T("title"), self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(
            "color: #18df6b; font-size: 16px; font-weight: bold; "
            "background-color: rgba(0,0,0,0.6); padding: 4px 10px; border-radius: 4px;"
        )

        # ---- Info label (bottom) ----
        self.info_label = QLabel("", self)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setStyleSheet(
            "color: #aabbee; font-size: 12px; "
            "background-color: rgba(0,0,0,0.5); padding: 3px 8px; border-radius: 4px;"
        )

        # ---- Corner hints ----
        corner_style = (
            "color: #ffffff; font-size: 13px; font-weight: bold; "
            "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
        )
        self.corner_tl = QLabel(_T("corner_mirror"), self)
        self.corner_tl.setStyleSheet(corner_style)
        self.corner_tr = QLabel(_T("corner_control"), self)
        self.corner_tr.setStyleSheet(corner_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel(_T("corner_exit"), self)
        self.corner_bl.setStyleSheet(corner_style)
        self.corner_br = QLabel(_T("corner_calib"), self)
        self.corner_br.setStyleSheet(corner_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        # ---- Timers ----
        self.camera_timer = QTimer(self)
        self.camera_timer.timeout.connect(self._process_frame)

        self._frame_count = 0

        # ---- Keys FIFO ----
        self._keys_fd = -1
        self._keys_notifier = None
        self._setup_keys_fifo()

        # ---- Start ----
        QTimer.singleShot(100, self._start_camera)

    # ---- Face detector init ----
    def _init_face_detector(self):
        if not os.path.exists(FACE_MODEL_PATH):
            print(f"[face_follow] 人脸检测模型不存在: {FACE_MODEL_PATH}", flush=True)
            return
        try:
            self.face_detector = cv2.FaceDetectorYN.create(
                FACE_MODEL_PATH, "", (320, 240), 0.7, 0.3, 5000
            )
            print("[face_follow] FaceDetectorYN 初始化成功", flush=True)
        except Exception as e:
            print(f"[face_follow] FaceDetectorYN 初始化失败: {e}", flush=True)

    # ---- Camera lifecycle ----
    def _start_camera(self):
        try:
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(
                main={"size": self.camera_size, "format": "RGB888"}
            )
            self.picam2.configure(config)
            self.picam2.start()
            self.camera_active = True
            self.camera_timer.start(50)  # ~20 fps
            print("[face_follow] Camera started", flush=True)
        except Exception as e:
            self.status_label.setText(f"Camera error: {e}")
            print(f"[face_follow] Camera error: {e}", flush=True)

    def _stop_camera(self):
        self.camera_timer.stop()
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception:
                pass
            self.picam2 = None
        self.camera_active = False

    # ---- Frame processing ----
    def _process_frame(self):
        if not self.camera_active or self.picam2 is None:
            return

        try:
            # Capture frame (Picamera2 "RGB888" actually delivers BGR on Pi)
            img = self.picam2.capture_array()
            img = cv2.flip(img, 1) if self.mirror_enabled else img

            h, w = img.shape[:2]

            # Resize detector input size if needed
            if self.face_detector is not None:
                self.face_detector.setInputSize((w, h))
                _, faces = self.face_detector.detect(img)

                err_x_px = 0.0
                err_y_px = 0.0
                found_face = False

                if faces is not None and len(faces) > 0:
                    # Take the largest face
                    best_face = max(faces, key=lambda f: f[2] * f[3])
                    x1, y1, fw, fh = (
                        int(best_face[0]), int(best_face[1]),
                        int(best_face[2]), int(best_face[3])
                    )

                    # Face center
                    face_cx = x1 + fw // 2
                    face_cy = y1 + fh // 2

                    # 像素误差: 人脸中心相对画面中心
                    # err_x_px > 0 表示人脸在画面右侧 → 头部需要向右转(yaw 增大)
                    # err_y_px > 0 表示人脸在画面下方 → 头部需要向下俯(pitch 减小)
                    err_x_px = face_cx - w / 2
                    err_y_px = face_cy - h / 2

                    found_face = True

                    # Draw bounding box (green) in BGR
                    cv2.rectangle(img, (x1, y1), (x1 + fw, y1 + fh), (0, 255, 100), 2)

                    # Draw center point (yellow)
                    cv2.circle(img, (face_cx, face_cy), 4, (0, 255, 255), -1)

                    # 连接人脸中心到画面中心的线(品红)
                    cv2.line(img, (face_cx, face_cy), (w // 2, h // 2), (255, 0, 255), 1)

                # Draw crosshair at image center (red)
                cv2.line(img, (w // 2 - 15, h // 2), (w // 2 + 15, h // 2), (0, 0, 255), 1)
                cv2.line(img, (w // 2, h // 2 - 15), (w // 2, h // 2 + 15), (0, 0, 255), 1)

                # ---- 闭环 P 控制(基于标定数据重写) ----
                # 仅用 attitude('y','p'),pitch 符号取反后才对!
                in_calib = self.phase != "follow"
                if found_face and not in_calib:
                    # 镜像模式下画面被水平翻转,err_x_px 物理方向相反
                    # 实测(mirror=ON):
                    #   attitude('y', +15) → 人脸左移 (err_x: -5 → -63)
                    #   attitude('y', -15) → 人脸右移 (err_x: -5 → +81)
                    # 所以 mirror=ON 时: yaw+ 让人脸左移 → 人脸偏右(err_x>0)
                    # 需要 yaw 增加 → 公式: yaw += kp * err_x
                    # mirror=OFF 时方向相反,用 -err_x
                    eff_err_x = err_x_px if self.mirror_enabled else -err_x_px

                    # ---- yaw 控制左右 ----
                    if abs(eff_err_x) > self.deadzone_px:
                        # eff_err_x>0 → yaw 增加 → 机身左转 → 画面人脸左移回中心
                        self.current_yaw += self.kp_x * eff_err_x

                    # ---- pitch 控制上下 (符号根据实测修正) ----
                    if abs(err_y_px) > self.deadzone_px:
                        # 实测: attitude('p',-18) → 画面人脸下移 60px (err_y 增大)
                        #       attitude('p',+18) → 人脸跑出画面顶部(危险!)
                        # 所以: err_y<0(人脸偏上) 需要 pitch 减小(负值) 让人脸下移
                        #       公式: current_pitch += kp * err_y  (err_y<0 时 pitch 减小)
                        self.current_pitch += self.kp_y * err_y_px
                # 丢失人脸或标定模式 → 跟随逻辑冻结,保持当前姿态

                # 全局限幅
                if self.current_yaw > self.yaw_limit:
                    self.current_yaw = self.yaw_limit
                elif self.current_yaw < -self.yaw_limit:
                    self.current_yaw = -self.yaw_limit
                if self.current_pitch > self.pitch_limit:
                    self.current_pitch = self.pitch_limit
                elif self.current_pitch < -self.pitch_limit:
                    self.current_pitch = -self.pitch_limit

                # ---- 下发控制(每帧都发) ----
                sy = sp = sty = None
                if self.robot_control_enabled and not in_calib:
                    sy = int(round(self.current_yaw))
                    sp = int(round(self.current_pitch))
                    # 根据实测只用 attitude('y','p'),不再用 translation
                    robot_attitude(['y', 'p'], [sy, sp])

                # Update info
                ctrl_status = "控制:ON" if self.robot_control_enabled else "控制:OFF"
                mirror_status = "镜像:ON" if self.mirror_enabled else "镜像:OFF"
                if in_calib:
                    face_status = f"标定中: {self.phase}"
                elif found_face:
                    face_status = (
                        f"err=({err_x_px:+.0f},{err_y_px:+.0f})px "
                        f"yaw={self.current_yaw:+.1f} pitch={self.current_pitch:+.1f} "
                        f"ty={self.current_ty:+.1f}"
                    )
                else:
                    face_status = f"No face | yaw={self.current_yaw:+.1f} pitch={self.current_pitch:+.1f}"

                self.info_label.setText(f"{ctrl_status} | {mirror_status} | {face_status}")

                # 画面上贴一行 phase 信息(用于录像里能看到当前动作)
                cv2.putText(
                    img, f"phase={self.phase}", (5, h - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1
                )
                if found_face:
                    cv2.putText(
                        img,
                        f"err=({err_x_px:+.0f},{err_y_px:+.0f}) y={self.current_yaw:+.1f} p={self.current_pitch:+.1f} ty={self.current_ty:+.1f}",
                        (5, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 255), 1
                    )

                # ---- 写入调试日志与录像 ----
                self._write_log(found_face, err_x_px, err_y_px, sy, sp, sty)
                self._write_video_frame(img)
            else:
                cv2.line(img, (w // 2 - 15, h // 2), (w // 2 + 15, h // 2), (0, 0, 255), 1)
                cv2.line(img, (w // 2, h // 2 - 15), (w // 2, h // 2 + 15), (0, 0, 255), 1)

                cv2.putText(img, "Face Detector N/A", (10, h // 2),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)

            # Convert BGR → RGB for QImage display
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, c = img.shape
            qimg = QImage(img.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg).scaled(
                self.camera_label.width(),
                self.camera_label.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.camera_label.setPixmap(pixmap)

        except Exception as e:
            print(f"[face_follow] Frame error: {e}", flush=True)

    # ---- 调试辅助: 写入 csv / video ----
    def _write_log(self, found_face, err_x, err_y, sent_yaw, sent_pitch, sent_ty):
        if self._csv_writer is None:
            return
        try:
            t_ms = (time.monotonic() - self._t_start) * 1000.0
            self._csv_writer.writerow([
                f"{t_ms:.1f}", self.phase, int(bool(found_face)),
                f"{err_x:.1f}" if found_face else "",
                f"{err_y:.1f}" if found_face else "",
                f"{self.current_yaw:.2f}",
                f"{self.current_pitch:.2f}",
                f"{self.current_ty:.2f}",
                "" if sent_yaw is None else sent_yaw,
                "" if sent_pitch is None else sent_pitch,
                "" if sent_ty is None else sent_ty,
                int(self.mirror_enabled), int(self.robot_control_enabled),
            ])
        except Exception as e:
            print(f"[face_follow] csv write error: {e}", flush=True)

    def _write_video_frame(self, bgr_img):
        try:
            if self._video_writer is None:
                fh, fw = bgr_img.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                self._video_writer = cv2.VideoWriter(
                    self.video_path, fourcc, 20.0, (fw, fh)
                )
                if not self._video_writer.isOpened():
                    print(f"[face_follow] VideoWriter open failed: {self.video_path}", flush=True)
                    self._video_writer = None
                    return
                print(f"[face_follow] VideoWriter opened {fw}x{fh} -> {self.video_path}", flush=True)
            self._video_writer.write(bgr_img)
        except Exception as e:
            print(f"[face_follow] video write error: {e}", flush=True)

    def _calib_log_event(self, msg):
        try:
            if self._calib_log:
                t_ms = (time.monotonic() - self._t_start) * 1000.0
                self._calib_log.write(f"{t_ms:8.1f}ms  {msg}\n")
        except Exception:
            pass
        print(f"[face_follow][calib] {msg}", flush=True)

    # ---- API 标定序列 ----
    def _start_calibration(self):
        if self.phase != "follow":
            print("[face_follow] calibration already running, ignore", flush=True)
            return
        self._calib_log_event("=== calibration start ===")
        # 先复位,为后续动作提供一致初始状态
        robot_reset()
        self.current_yaw = 0.0
        self.current_pitch = 0.0
        self.current_tx = 0.0
        self.current_ty = 0.0
        # 标定期间不跳跳跟随控制
        self.phase = "calib_init"

        steps = [
            ("reset_zero",      lambda: robot_reset()),
            ("attitude_y_+15",  lambda: robot_attitude(['y'], [15])),
            ("attitude_y_-15",  lambda: robot_attitude(['y'], [-15])),
            ("attitude_y_0",    lambda: robot_attitude(['y'], [0])),
            ("attitude_p_+18",  lambda: robot_attitude(['p'], [18])),
            ("attitude_p_-18",  lambda: robot_attitude(['p'], [-18])),
            ("attitude_p_0",    lambda: robot_attitude(['p'], [0])),
            ("attitude_r_+15",  lambda: robot_attitude(['r'], [15])),
            ("attitude_r_-15",  lambda: robot_attitude(['r'], [-15])),
            ("attitude_r_0",    lambda: robot_attitude(['r'], [0])),
            ("translation_y_+18", lambda: robot_translation(['y'], [18])),
            ("translation_y_-18", lambda: robot_translation(['y'], [-18])),
            ("translation_y_0",   lambda: robot_translation(['y'], [0])),
            ("translation_x_+25", lambda: robot_translation(['x'], [25])),
            ("translation_x_-25", lambda: robot_translation(['x'], [-25])),
            ("translation_x_0",   lambda: robot_translation(['x'], [0])),
        ]
        self._calib_queue = list(steps)
        QTimer.singleShot(800, self._calib_next)

    def _calib_next(self):
        if not self._calib_queue:
            self.phase = "calib_done"
            self._calib_log_event("=== calibration done, resuming follow in 1s ===")
            QTimer.singleShot(1000, self._calib_finish)
            return
        name, action = self._calib_queue.pop(0)
        self.phase = f"calib_{name}"
        self._calib_log_event(f"step {name}")
        try:
            action()
        except Exception as e:
            self._calib_log_event(f"step {name} ERROR: {e}")
        QTimer.singleShot(1500, self._calib_next)

    def _calib_finish(self):
        try:
            robot_reset()
        except Exception:
            pass
        self.current_yaw = 0.0
        self.current_pitch = 0.0
        self.current_tx = 0.0
        self.current_ty = 0.0
        self.phase = "follow"
        self._calib_log_event("resumed follow")

    # ---- Keys FIFO from launcher ----
    def _setup_keys_fifo(self):
        try:
            self._keys_fd = os.open(KEYS_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            self._keys_notifier = QSocketNotifier(self._keys_fd, QSocketNotifier.Type.Read, self)
            self._keys_notifier.activated.connect(self._on_key_fifo)
            print("[face_follow] Keys FIFO opened", flush=True)
        except Exception as e:
            print(f"[face_follow] Keys FIFO error: {e}", flush=True)

    def _on_key_fifo(self, fd: int):
        try:
            data = os.read(fd, 32)
            if data:
                for line in data.decode().strip().split('\n'):
                    if line.strip():
                        qt_key = int(line.strip())
                        ev = QKeyEvent(QKeyEvent.Type.KeyPress, qt_key, Qt.KeyboardModifier.NoModifier)
                        QApplication.postEvent(self, ev)
        except Exception as e:
            print(f"[face_follow] key fifo read error: {e}", flush=True)

    # ---- Key events ----
    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()
        if key == Qt.Key.Key_Back:   # C → exit
            print("[face_follow] KEY_BACK (C) → exit", flush=True)
            self.close()
        elif key == Qt.Key.Key_Left:  # A → toggle mirror
            print("[face_follow] KEY_LEFT (A) → toggle mirror", flush=True)
            self.mirror_enabled = not self.mirror_enabled
        elif key == Qt.Key.Key_Right:  # B → toggle robot control
            print("[face_follow] KEY_RIGHT (B) → toggle robot control", flush=True)
            self.robot_control_enabled = not self.robot_control_enabled
        elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D → 启动标定
            print("[face_follow] KEY_ENTER (D) → start calibration", flush=True)
            self._start_calibration()

    # ---- Resize ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 12

        # Camera fills entire screen
        self.camera_label.setGeometry(0, 0, w, h)

        # Status label at top center
        self.status_label.adjustSize()
        sw = self.status_label.width()
        self.status_label.move((w - sw) // 2, pad)

        # Info label at bottom center
        self.info_label.adjustSize()
        iw = self.info_label.width()
        self.info_label.move((w - iw) // 2, h - self.info_label.height() - pad - 4)

        # Corners
        self.corner_tl.raise_()
        self.corner_tl.adjustSize()
        self.corner_tl.move(pad, pad + 4)
        self.corner_bl.raise_()
        self.corner_bl.adjustSize()
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad - 4)

        self.corner_tr.raise_()
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad + 4)
        self.corner_br.raise_()
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad - 4)

    # ---- Close ----
    def closeEvent(self, ev):
        self._stop_camera()
        if self._keys_notifier:
            self._keys_notifier.setEnabled(False)
        if self._keys_fd >= 0:
            try:
                os.close(self._keys_fd)
            except Exception:
                pass
        robot_reset()
        # 关闭调试输出
        try:
            if self._video_writer is not None:
                self._video_writer.release()
                self._video_writer = None
        except Exception:
            pass
        try:
            if self._csv_file is not None:
                self._csv_file.close()
                self._csv_file = None
        except Exception:
            pass
        try:
            if self._calib_log is not None:
                self._calib_log.close()
                self._calib_log = None
        except Exception:
            pass
        print(f"[face_follow] closing, log dir: {self.log_dir}", flush=True)
        super().closeEvent(ev)


# ============================================================================
# Entry point
# ============================================================================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    w = FaceFollowWidget()
    w.showFullScreen()

    rc = app.exec()
    print(f"[face_follow] exit rc={rc}", flush=True)


if __name__ == "__main__":
    main()
