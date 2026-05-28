#!/usr/bin/env python3
"""
Sound Locate — 声源定位 App（无风扇环境专用）

利用 WM8960 双麦克风立体声采集，通过人声频段能量对比判定声源方向，
驱动 XGO 机器狗向声源方向转身。

信号处理链：
  PyAudio 16-bit 立体声采集
  → FFT 带通滤波 (300–3400 Hz，人声频段)
  → RMS 能量计算 + EMA 平滑
  → 左右声道能量差 → 方向判定（含迟滞）

按键映射：
  A (左上 / KEY_LEFT)  → 自动追踪 开/关
  B (右上 / KEY_RIGHT) → 灵敏度切换（低/中/高）
  C (左下 / KEY_BACK)  → 退出
  D (右下 / KEY_ENTER) → 手动转身一次

无风扇环境下噪声基底稳定，仅需简单启动校准即可。
"""
import os
import sys
import signal
import threading
import time
import math
import logging
from enum import Enum, auto
from typing import Optional, Tuple

import numpy as np

# ---- PySide6 ----
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QSocketNotifier
from PySide6.QtGui import (
    QKeyEvent, QPainter, QColor, QFont, QPen, QBrush, QLinearGradient,
)
from PySide6.QtWidgets import QApplication, QLabel

# ---- luwu-os ----
LUWU_ROOT = os.environ.get("LUWU_ROOT", "/opt/luwu-os")
if LUWU_ROOT not in sys.path:
    sys.path.insert(0, LUWU_ROOT)
from libs.ui.frame import AppFrame
from libs.theme import apply_app_palette, Asset, Color, hex_to_rgb

# ============================================================================
# 日志
# ============================================================================
logging.basicConfig(
    level=logging.DEBUG,
    format="[sound_locate] %(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("sound_locate")

# ============================================================================
# 常量
# ============================================================================
SCREEN_W, SCREEN_H = 320, 240
SAMPLE_RATE = 16000
CHANNELS = 2
CHUNK_SIZE = 1600          # 100 ms @ 16 kHz
AUTO_EXIT_SEC = 300        # 5 分钟自动退出
KEYS_FIFO = "/tmp/luwu_keys.fifo"

# 人声带通范围
VOICE_LO_HZ = 300
VOICE_HI_HZ = 3400

# 机器人转身参数
TURN_SPEED = 25            # 转身速度
TURN_DURATION_S = 0.3      # 单次转身时长

# 方向判定迟滞 (dB) —— 避免在阈值附近反复抖动
HYSTERESIS_DB = 1.0

# dB 显示范围
DB_FLOOR = -60.0
DB_CEIL = 0.0


class Sensitivity(Enum):
    """灵敏度级别。"""
    LOW = ("低", 6.0)
    MEDIUM = ("中", 3.0)
    HIGH = ("高", 1.5)

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def threshold_db(self) -> float:
        return self.value[1]

    def next(self) -> "Sensitivity":
        members = list(Sensitivity)
        idx = members.index(self)
        return members[(idx + 1) % len(members)]


class Direction(Enum):
    """声源方向。"""
    NONE = auto()
    LEFT = auto()
    RIGHT = auto()


# ============================================================================
# 信号处理
# ============================================================================

class VoiceBandFilter:
    """FFT 砖墙带通滤波器：仅保留人声频段 (300–3400 Hz)。

    相比 IIR 滤波器，FFT 方法在 Python 中实现简单、不依赖 scipy，
    且对于 100ms 帧长（1600 点）计算开销极低。
    """

    def __init__(self, sample_rate: int):
        self._sample_rate = sample_rate

    def apply(self, signal: np.ndarray) -> np.ndarray:
        """对一维信号做带通滤波，返回滤波后的时域信号。"""
        n = len(signal)
        if n < 2:
            return signal
        fft = np.fft.rfft(signal.astype(np.float64))
        freq_per_bin = self._sample_rate / n
        lo = max(0, int(VOICE_LO_HZ / freq_per_bin))
        hi = min(len(fft) - 1, int(VOICE_HI_HZ / freq_per_bin))
        fft[:lo] = 0.0
        fft[hi + 1:] = 0.0
        return np.fft.irfft(fft, n=n).real.astype(np.float64)


class EnergyTracker:
    """能量追踪器：RMS → dB + EMA 平滑。"""

    def __init__(self, ema_alpha: float = 0.15):
        self._alpha = ema_alpha
        self._db_smoothed: Optional[float] = None

    def update(self, signal: np.ndarray) -> float:
        """给定一帧信号，返回平滑后的 dB 值。"""
        rms = self._compute_rms(signal)
        db = self._rms_to_db(rms)
        if self._db_smoothed is None:
            self._db_smoothed = db
        else:
            self._db_smoothed = self._alpha * db + (1 - self._alpha) * self._db_smoothed
        return self._db_smoothed

    def reset(self) -> None:
        self._db_smoothed = None

    @staticmethod
    def _compute_rms(signal: np.ndarray) -> float:
        n = len(signal)
        if n == 0:
            return 0.0
        return math.sqrt(np.mean(signal.astype(np.float64) ** 2))

    @staticmethod
    def _rms_to_db(rms: float) -> float:
        if rms < 1e-6:
            return DB_FLOOR
        return 20.0 * math.log10(rms / 32768.0)


class DirectionEstimator:
    """方向估计器。

    基于左右声道能量差做方向判定，带迟滞以防止边界抖动。
    """

    def __init__(self, threshold_db: float = 3.0, hysteresis_db: float = HYSTERESIS_DB):
        self._threshold = threshold_db
        self._hysteresis = hysteresis_db
        self._prev_direction = Direction.NONE

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self._threshold = value

    def update(self, left_db: float, right_db: float) -> Tuple[Direction, float]:
        """返回 (方向, 修正后的差值)。"""
        diff = left_db - right_db

        # 带迟滞的阈值比较
        if self._prev_direction == Direction.LEFT:
            on_threshold = self._threshold - self._hysteresis
        elif self._prev_direction == Direction.RIGHT:
            on_threshold = self._threshold - self._hysteresis
        else:
            on_threshold = self._threshold

        if diff > on_threshold:
            direction = Direction.LEFT
        elif diff < -on_threshold:
            direction = Direction.RIGHT
        else:
            direction = Direction.NONE

        self._prev_direction = direction
        return direction, diff


# ============================================================================
# 音频采集引擎（后台线程）
# ============================================================================

class AudioEngine(QObject):
    """音频采集 + 信号处理引擎。

    在独立线程中运行，通过 Qt Signal 将处理结果发送到 GUI 线程。
    """

    #: 发射 (left_db, right_db, direction, diff)
    processed = Signal(float, float, int, float)

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._running = False
        self._filter = VoiceBandFilter(SAMPLE_RATE)
        self._tracker_l = EnergyTracker()
        self._tracker_r = EnergyTracker()

    def start(self) -> None:
        self._running = True
        t = threading.Thread(target=self._run, daemon=True, name="audio-engine")
        t.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        import pyaudio
        pa: Optional[pyaudio.PyAudio] = None
        stream: Optional[pyaudio.Stream] = None
        try:
            pa = pyaudio.PyAudio()
            # 不指定 input_device_index，走 default PCM → dsnoop → hw:0,0
            # 这样才能与其他 App 同时录音（见 HARDWARE_PLAN.md 改造 5）
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            log.info("audio stream opened (rate=%d, ch=%d) via default/dsnoop",
                     SAMPLE_RATE, CHANNELS)

            frame_no = 0
            while self._running:
                frame_no += 1
                try:
                    raw = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except OSError:
                    continue

                samples = np.frombuffer(raw, dtype=np.int16)
                left_raw = samples[0::2]
                right_raw = samples[1::2]

                # 带通滤波 → 能量 → dB
                voice_l = self._filter.apply(left_raw)
                voice_r = self._filter.apply(right_raw)
                left_db = self._tracker_l.update(voice_l)
                right_db = self._tracker_r.update(voice_r)

                # 粗方向 (仅用于日志)
                if abs(left_db - right_db) < 2.0:
                    dir_int = 0
                else:
                    dir_int = 1 if left_db > right_db else -1

                self.processed.emit(left_db, right_db, dir_int, left_db - right_db)

                if frame_no % 10 == 0:
                    log.debug(
                        "#%d L=%5.1f R=%5.1f  diff=%+5.1f",
                        frame_no, left_db, right_db, left_db - right_db,
                    )

        except Exception:
            log.exception("audio engine fatal error")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass
            log.info("audio engine stopped")


# ============================================================================
# 机器人驱动
# ============================================================================

class RobotDriver:
    """XGO 机器狗运动驱动，封装串口实例化与线程安全控制。"""

    def __init__(self):
        self._dog: object = None
        self._lock = threading.Lock()
        self._init_thread = threading.Thread(
            target=self._connect, daemon=True, name="robot-init",
        )
        self._init_thread.start()

    @property
    def ready(self) -> bool:
        return self._dog is not None

    def _connect(self) -> None:
        try:
            from xgolib import XGO
            self._dog = XGO()
            log.info("XGO connected")
        except Exception:
            log.warning("XGO init failed (robot may be absent)")
            self._dog = None

    def turn(self, direction: Direction) -> None:
        """非阻塞：在后台线程执行一次向指定方向的转身。

        如果上一次转身尚未完成则跳过本次请求。
        """
        if self._dog is None or direction == Direction.NONE:
            return
        if not self._lock.acquire(blocking=False):
            return  # 上一次转身还在执行

        sign = -1 if direction == Direction.LEFT else 1

        def _do():
            try:
                self._dog.turn(TURN_SPEED * sign)
                time.sleep(TURN_DURATION_S)
                self._dog.turn(0)
            except Exception:
                log.exception("turn failed")
            finally:
                self._lock.release()

        threading.Thread(target=_do, daemon=True, name="robot-turn").start()

    def stop(self) -> None:
        if self._dog is not None:
            try:
                self._dog.turn(0)
            except Exception:
                pass


# ============================================================================
# GUI 主页面
# ============================================================================

class SoundLocatePage(AppFrame):
    """声源定位主界面。"""

    def __init__(self):
        super().__init__()
        self.setTitle("声源定位")
        self.setCornerHints(
            tl=("A:追踪", Asset.icon_left),
            tr=("B:灵敏度", Asset.icon_right),
            bl=("返回", Asset.icon_back),
            br=("D:转身", Asset.icon_enter),
        )

        # ---- 状态 ----
        self._auto_track = False
        self._sensitivity = Sensitivity.MEDIUM
        self._left_db = DB_FLOOR
        self._right_db = DB_FLOOR
        self._diff = 0.0
        self._direction = Direction.NONE

        # 方向估计器
        self._estimator = DirectionEstimator(self._sensitivity.threshold_db)

        # 机器人
        self._robot = RobotDriver()

        # ---- 状态标签 ----
        self._status_label = QLabel("就绪", self)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet(
            "color: #1a2b5e; font-size: 11px; background: transparent;"
        )

        # ---- 音频引擎 ----
        self._audio = AudioEngine()
        self._audio.processed.connect(self._on_audio_frame)
        self._audio.start()

        # ---- FIFO 按键 ----
        self._setup_fifo()

        # ---- 定时器 ----
        self._exit_timer = QTimer(self)
        self._exit_timer.setSingleShot(True)
        self._exit_timer.timeout.connect(self.close)
        self._exit_timer.start(AUTO_EXIT_SEC * 1000)

        self._paint_timer = QTimer(self)
        self._paint_timer.timeout.connect(self.update)
        self._paint_timer.start(33)  # ~30 fps

        log.info("page initialized")

    # ================================================================ Slots

    def _on_audio_frame(self, left_db: float, right_db: float, _dir_int: int, diff: float):
        """接收音频引擎的每一帧处理结果（在 GUI 线程执行）。"""
        self._left_db = left_db
        self._right_db = right_db
        self._diff = diff

        direction, _ = self._estimator.update(left_db, right_db)
        self._direction = direction

        # 自动追踪
        if self._auto_track and direction != Direction.NONE:
            self._robot.turn(direction)

        # 更新状态栏
        self._update_status()

    def _update_status(self) -> None:
        track = "AUTO" if self._auto_track else "MANUAL"
        sens = self._sensitivity.label
        if self._direction == Direction.LEFT:
            arrow = "←"
        elif self._direction == Direction.RIGHT:
            arrow = "→"
        else:
            arrow = "–"
        self._status_label.setText(
            f"{track} | 灵敏度:{sens} | {arrow} | diff={self._diff:+.1f}dB"
        )

    # ================================================================ Paint

    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        if w < 10 or h < 10:
            painter.end()
            return

        # ---- 布局计算 ----
        top_margin = 38
        bottom_margin = 42
        bar_area_top = top_margin
        bar_area_bottom = h - bottom_margin
        bar_area_h = bar_area_bottom - bar_area_top
        bar_w = 50
        gap = 50
        cx = w // 2
        left_x = cx - gap // 2 - bar_w
        right_x = cx + gap // 2

        # dB → 像素
        def db_to_px(db_val: float) -> int:
            clamped = max(DB_FLOOR, min(DB_CEIL, db_val))
            ratio = (clamped - DB_FLOOR) / (DB_CEIL - DB_FLOOR)
            return max(0, int(ratio * (bar_area_h - 10)))

        left_h = db_to_px(self._left_db)
        right_h = db_to_px(self._right_db)

        # ---- 背景框 ----
        painter.setPen(QPen(QColor(140, 150, 170, 120), 1))
        painter.setBrush(QBrush(QColor(180, 190, 210, 80)))
        for bx in (left_x, right_x):
            painter.drawRoundedRect(bx - 4, bar_area_top, bar_w + 8, bar_area_h, 6, 6)

        # ---- 能量柱 ----
        left_hot = self._left_db > self._right_db + 0.5
        right_hot = self._right_db > self._left_db + 0.5

        for bx, bh, is_hot in (
            (left_x, left_h, left_hot),
            (right_x, right_h, right_hot),
        ):
            if bh <= 0:
                continue
            grad = QLinearGradient(bx, bar_area_bottom - bh, bx, bar_area_bottom)
            if is_hot:
                grad.setColorAt(0.0, QColor(255, 160, 40))
                grad.setColorAt(1.0, QColor(230, 100, 20))
            else:
                grad.setColorAt(0.0, QColor(140, 160, 190))
                grad.setColorAt(1.0, QColor(110, 130, 160))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(bx, bar_area_bottom - bh, bar_w, bh, 4, 4)

        # ---- 标签 ----
        painter.setPen(QColor(*hex_to_rgb(Color.text_primary)))
        font_label = QFont("sans-serif", 10, QFont.Weight.Bold)
        painter.setFont(font_label)
        painter.drawText(left_x, bar_area_bottom + 14, bar_w, 18,
                         Qt.AlignmentFlag.AlignCenter, "L 左")
        painter.drawText(right_x, bar_area_bottom + 14, bar_w, 18,
                         Qt.AlignmentFlag.AlignCenter, "R 右")

        # ---- dB 数值 ----
        font_small = QFont("sans-serif", 9)
        painter.setFont(font_small)
        painter.setPen(QColor(80, 90, 120))
        painter.drawText(left_x, bar_area_top - 16, bar_w, 14,
                         Qt.AlignmentFlag.AlignCenter, f"{self._left_db:.1f} dB")
        painter.drawText(right_x, bar_area_top - 16, bar_w, 14,
                         Qt.AlignmentFlag.AlignCenter, f"{self._right_db:.1f} dB")

        # ---- 方向指示 ----
        font_dir = QFont("sans-serif", 14, QFont.Weight.Bold)
        painter.setFont(font_dir)
        if self._direction == Direction.LEFT:
            painter.setPen(QColor(230, 110, 20))
            arrow_text = "← 左"
        elif self._direction == Direction.RIGHT:
            painter.setPen(QColor(230, 110, 20))
            arrow_text = "→ 右"
        else:
            painter.setPen(QColor(150, 150, 150))
            arrow_text = "···"
        dir_y = bar_area_top + bar_area_h // 2 - 10
        painter.drawText(cx - 25, dir_y, 50, 24,
                         Qt.AlignmentFlag.AlignCenter, arrow_text)

        # ---- 刻度 ----
        painter.setPen(QPen(QColor(160, 170, 190, 100), 1))
        font_tick = QFont("sans-serif", 7)
        painter.setFont(font_tick)
        for db in (-50, -40, -30, -20, -10, 0):
            y = bar_area_bottom - db_to_px(db)
            painter.drawLine(left_x - 5, y, left_x - 1, y)
            painter.drawLine(right_x + bar_w + 1, y, right_x + bar_w + 5, y)

        painter.end()

    # ================================================================ FIFO

    def _setup_fifo(self) -> None:
        try:
            self._keys_fd = os.open(KEYS_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            self._keys_notifier = QSocketNotifier(
                self._keys_fd, QSocketNotifier.Type.Read, self,
            )
            self._keys_notifier.activated.connect(self._on_fifo_data)
            log.info("FIFO opened")
        except Exception:
            log.exception("FIFO open failed")
            self._keys_fd = -1

    def _on_fifo_data(self, *args) -> None:
        try:
            data = os.read(self._keys_fd, 32)
            if not data:
                return
            for line in data.decode(errors="replace").strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                qt_key = int(line)
                QApplication.postEvent(
                    self,
                    QKeyEvent(
                        QKeyEvent.Type.KeyPress,
                        qt_key,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
        except Exception:
            pass

    # ================================================================ Keys

    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()

        if key == Qt.Key.Key_Back:          # C → 退出
            log.info("KEY_BACK → exit")
            self.close()

        elif key == Qt.Key.Key_Left:         # A → 自动追踪
            self._auto_track = not self._auto_track
            log.info("auto-track = %s", self._auto_track)

        elif key == Qt.Key.Key_Right:        # B → 灵敏度
            self._sensitivity = self._sensitivity.next()
            self._estimator.threshold = self._sensitivity.threshold_db
            log.info("sensitivity → %s (%.1f dB)",
                     self._sensitivity.label, self._sensitivity.threshold_db)

        elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return):  # D → 手动转身
            self._robot.turn(self._direction)

    # ================================================================ Layout

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self._status_label.setGeometry(10, h - 40, w - 20, 18)

    # ================================================================ Cleanup

    def closeEvent(self, ev):
        log.info("shutting down...")
        self._paint_timer.stop()
        self._exit_timer.stop()
        self._audio.stop()

        if hasattr(self, "_keys_fd") and self._keys_fd >= 0:
            try:
                os.close(self._keys_fd)
            except Exception:
                pass

        self._robot.stop()
        super().closeEvent(ev)
        log.info("closed")


# ============================================================================
# 入口
# ============================================================================

def main():
    # 使用 udev 软链接 /dev/fb-spi，避免 fb 编号漂移
    # （见 HARDWARE_PLAN.md 改造 1）
    if "QT_QPA_PLATFORM" not in os.environ:
        fb_path = "/dev/fb-spi"
        if os.path.exists(fb_path):
            os.environ["QT_QPA_PLATFORM"] = f"linuxfb:fb={fb_path}"
        else:
            os.environ["QT_QPA_PLATFORM"] = "linuxfb"

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    app = QApplication(sys.argv)
    apply_app_palette(app)

    page = SoundLocatePage()
    page.setFixedSize(SCREEN_W, SCREEN_H)
    page.showFullScreen()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
