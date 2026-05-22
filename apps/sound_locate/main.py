#!/usr/bin/env python3
"""
声源定位 App — 由 Luwu OS launcher 启动。

利用板载 WM8960 双麦克风（L/R 立体声）实时采集音频，
比较左右声道能量，在屏幕上展示能量柱状图，
并控制机器狗向声源方向转身。

按键映射:
  A (左上 / KEY_LEFT)   → 开始/暂停 自动追踪
  B (右上 / KEY_RIGHT)  → 灵敏度调节（低/中/高）
  C (左下 / KEY_BACK)   → 退出
  D (右下 / KEY_ENTER)  → 手动转身一次（向能量大的方向转）
"""
import os
import sys
import struct
import signal
import threading
import time
import math
import numpy as np

# ---- PySide6 ----
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QPainter, QColor, QFont, QPen, QBrush, QLinearGradient
from PySide6.QtWidgets import QApplication, QWidget, QLabel

# ---- luwu-os libs ----
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
from libs.ui.frame import AppFrame
from libs.theme import apply_app_palette, Asset, Color, Spacing

# ---- FIFO 路径 ----
KEYS_FIFO = "/tmp/luwu_keys.fifo"
from PySide6.QtCore import QSocketNotifier

# ---- 常量 ----
SCREEN_W, SCREEN_H = 320, 240
RATE = 16000            # 采样率
CHANNELS = 2            # 立体声
CHUNK = 1600            # 每次读取帧数 (100ms @ 16kHz)
FORMAT_WIDTH = 2        # 16-bit = 2 bytes
AUTO_EXIT_SEC = 300     # 5分钟自动退出
ENERGY_HISTORY = 20     # 能量历史长度（平滑用）

# 转身参数
TURN_SPEED = 25         # 转身速度
TURN_DURATION = 0.3     # 转身持续时间(秒)
ENERGY_THRESHOLD_DB = 3.0  # 左右差异阈值(dB)，超过才转身

# 灵敏度预设
SENSITIVITY_PRESETS = [
    ("低", 6.0),   # 需要 6dB 差异才转
    ("中", 3.0),   # 需要 3dB 差异才转
    ("高", 1.5),   # 需要 1.5dB 差异就转
]

TAG = "[sound_locate]"


VOICE_LO = 300
VOICE_HI = 3400

def _voice_extract(samples: np.ndarray, rate: int) -> np.ndarray:
    """FFT 砖墙带通滤波器：仅保留 300-3400Hz 人声频段，其余频段置零。"""
    n = len(samples)
    fft = np.fft.rfft(samples.astype(np.float64))
    freq_per_bin = rate / n
    lo_bin = max(0, int(VOICE_LO / freq_per_bin))
    hi_bin = min(len(fft) - 1, int(VOICE_HI / freq_per_bin))
    fft[:lo_bin] = 0
    fft[hi_bin + 1:] = 0
    return np.fft.irfft(fft, n=n).real.astype(np.float64)


def _rms_db(samples: np.ndarray) -> float:
    """RMS → dB."""
    if len(samples) == 0:
        return -100.0
    rms = math.sqrt(np.mean(samples.astype(np.float64) ** 2))
    if rms < 1e-6:
        return -100.0
    return 20.0 * math.log10(rms / 32768.0)


class SoundLocatePage(AppFrame):
    """声源定位主界面。"""

    # 从录音线程发射到 GUI 线程
    energy_updated = Signal(float, float, int)  # left_db, right_db, crosscorr_dir

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
        self._sensitivity_idx = 1  # 默认"中"
        self._threshold = SENSITIVITY_PRESETS[1][1]
        self._left_db = -60.0
        self._right_db = -60.0
        self._direction = ""
        self._running = True
        self._dog = None
        self._dog_busy = False

        # 瞬态检测：EWMA 追踪稳态噪声基线，只响应急剧增量
        self._ewma_l = -60.0
        self._ewma_r = -60.0
        self._ewma_alpha = 0.05
        self._spike_thresh = 3.0

        # 启动校准：采集差值样本初始化 EWMA
        self._cal_offset = 0.0
        self._cal_samples = []
        self._cal_done = False
        self._cal_countdown = 20

        # ---- 状态标签 ----
        self._status = QLabel("校准中: 静默2秒...", self)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet(
            "color: #1a2b5e; font-size: 11px; background: transparent;"
        )

        # ---- 信号连接 ----
        self.energy_updated.connect(self._on_energy)

        # ---- 录音线程 ----
        self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
        self._mic_thread.start()

        # ---- 初始化机器狗 (后台) ----
        self._dog_thread = threading.Thread(target=self._init_dog, daemon=True)
        self._dog_thread.start()

        # ---- FIFO 按键 ----
        self._setup_keys_fifo()

        # ---- 自动退出 ----
        self._exit_timer = QTimer(self)
        self._exit_timer.setSingleShot(True)
        self._exit_timer.timeout.connect(self.close)
        self._exit_timer.start(AUTO_EXIT_SEC * 1000)

        # ---- 刷新定时器 (30fps 重绘) ----
        self._paint_timer = QTimer(self)
        self._paint_timer.timeout.connect(self.update)
        self._paint_timer.start(33)

        print(f"{TAG} init done", flush=True)

    # ================================================================ Dog
    def _init_dog(self):
        try:
            from xgolib import XGO
            self._dog = XGO()
            print(f"{TAG} XGO connected", flush=True)
        except Exception as e:
            print(f"{TAG} XGO init failed: {e}", flush=True)
            self._dog = None

    def _do_turn(self, direction: int):
        """direction: +1=右转, -1=左转。在后台线程执行。"""
        if self._dog is None or self._dog_busy:
            return
        self._dog_busy = True

        def _turn():
            try:
                speed = TURN_SPEED * direction
                self._dog.turn(speed)
                time.sleep(TURN_DURATION)
                self._dog.turn(0)
            except Exception as e:
                print(f"{TAG} turn error: {e}", flush=True)
            finally:
                self._dog_busy = False

        threading.Thread(target=_turn, daemon=True).start()

    # ================================================================ Mic
    def _mic_loop(self):
        """录音线程：持续读取立体声音频，计算左右声道能量。"""
        import pyaudio
        pa = None
        stream = None
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
            print(f"{TAG} mic stream opened (rate={RATE}, ch={CHANNELS})", flush=True)

            _debug_frame = 0
            while self._running:
                _debug_frame += 1
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                except Exception:
                    continue

                # 解析立体声 interleaved: [L0, R0, L1, R1, ...]
                samples = np.frombuffer(data, dtype=np.int16)
                left = samples[0::2]   # 偶数下标 = 左声道
                right = samples[1::2]  # 奇数下标 = 右声道

                # 提取人声
                voice_l = _voice_extract(left, RATE)
                voice_r = _voice_extract(right, RATE)
                left_db = _rms_db(voice_l)
                right_db = _rms_db(voice_r)

                # 互相关方向
                xdir = 0
                if left_db > -60 or right_db > -60:
                    xdir = int((left_db - right_db) / max(abs(left_db - right_db), 0.1))
                    if abs(left_db - right_db) < 2.0:
                        xdir = 0

                self.energy_updated.emit(left_db, right_db, xdir)
                if _debug_frame % 10 == 0:
                    raw_l = _rms_db(left); raw_r = _rms_db(right)
                    diff = left_db - right_db
                    print(f"{TAG} #{_debug_frame} raw L{raw_l:.0f} R{raw_r:.0f} | voice L{left_db:.0f} R{right_db:.0f} diff={diff:+.1f}", flush=True)

        except Exception as e:
            print(f"{TAG} mic error: {e}", flush=True)
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa:
                pa.terminate()
            print(f"{TAG} mic thread exit", flush=True)

    # ================================================================ Energy
    def _on_energy(self, left_db: float, right_db: float, xdir: int):
        """自适应基线：安静时缓慢追踪环境偏置，说话时冻结基线对比。"""
        self._left_db = left_db
        self._right_db = right_db
        diff = left_db - right_db

        # 初始化
        if not hasattr(self, '_baseline'):
            self._baseline = diff
            self._baseline_frames = 0

        self._baseline_frames += 1

        # 判断是否在说话：voice 能量高于 -30dB 认为有人声
        speaking = max(left_db, right_db) > -30

        if not speaking:
            # 安静环境：缓慢更新基线（EWMA, alpha=0.02）
            self._baseline = 0.02 * diff + 0.98 * self._baseline
        # 说话时 baseline 冻结

        corrected = diff - self._baseline
        thresh = self._threshold

        if corrected > thresh:
            self._direction = "← 左"
        elif corrected < -thresh:
            self._direction = "→ 右"
        else:
            self._direction = ""

        if self._auto_track and not self._dog_busy:
            if corrected > thresh:
                self._do_turn(-1)
            elif corrected < -thresh:
                self._do_turn(1)

        sens_name = SENSITIVITY_PRESETS[self._sensitivity_idx][0]
        track_str = "🟢 自动" if self._auto_track else "⚪ 手动"
        spk = "🎙" if speaking else "-"
        self._status.setText(
            f"{track_str} | 灵敏度:{sens_name} | {spk} diff{diff:+.1f} base{self._baseline:+.1f} corr{corrected:+.1f}"
        )
        if self._baseline_frames % 20 == 0:
            spk_label = "SPEAKING" if speaking else "silence"
            print(f"{TAG} GUI dir=\"{self._direction}\" {spk_label} diff{diff:+.1f} base{self._baseline:+.1f} corr{corrected:+.1f} thresh{thresh:.1f}", flush=True)
    # ================================================================ Paint
    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # ---- 绘制区域 ----
        bar_area_top = 38
        bar_area_bottom = h - 42
        bar_area_height = bar_area_bottom - bar_area_top
        bar_width = 50
        gap = 50  # 左右柱之间间距
        center_x = w // 2

        left_x = center_x - gap // 2 - bar_width
        right_x = center_x + gap // 2

        # dB 范围映射到像素高度 (-60dB ~ 0dB)
        db_min, db_max = -60.0, 0.0

        def db_to_height(db_val):
            clamped = max(db_min, min(db_max, db_val))
            ratio = (clamped - db_min) / (db_max - db_min)
            return int(ratio * (bar_area_height - 10))

        left_h = db_to_height(self._left_db)
        right_h = db_to_height(self._right_db)

        # ---- 绘制背景框 ----
        frame_color = QColor(180, 190, 210, 80)
        painter.setPen(QPen(QColor(140, 150, 170, 120), 1))
        painter.setBrush(QBrush(frame_color))
        painter.drawRoundedRect(left_x - 4, bar_area_top, bar_width + 8, bar_area_height, 6, 6)
        painter.drawRoundedRect(right_x - 4, bar_area_top, bar_width + 8, bar_area_height, 6, 6)

        # ---- 绘制能量柱：高的亮橙色，低的灰蓝色 ----
        left_wins = self._left_db > self._right_db + 0.5
        right_wins = self._right_db > self._left_db + 0.5

        if left_h > 0:
            grad_l = QLinearGradient(left_x, bar_area_bottom - left_h, left_x, bar_area_bottom)
            if left_wins:
                grad_l.setColorAt(0, QColor(255, 160, 40))   # 亮橙
                grad_l.setColorAt(1, QColor(230, 100, 20))   # 深橙
            else:
                grad_l.setColorAt(0, QColor(140, 160, 190))  # 灰蓝
                grad_l.setColorAt(1, QColor(110, 130, 160))  # 深灰蓝
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad_l))
            painter.drawRoundedRect(
                left_x, bar_area_bottom - left_h,
                bar_width, left_h, 4, 4
            )

        if right_h > 0:
            grad_r = QLinearGradient(right_x, bar_area_bottom - right_h, right_x, bar_area_bottom)
            if right_wins:
                grad_r.setColorAt(0, QColor(255, 160, 40))   # 亮橙
                grad_r.setColorAt(1, QColor(230, 100, 20))   # 深橙
            else:
                grad_r.setColorAt(0, QColor(140, 160, 190))  # 灰蓝
                grad_r.setColorAt(1, QColor(110, 130, 160))  # 深灰蓝
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad_r))
            painter.drawRoundedRect(
                right_x, bar_area_bottom - right_h,
                bar_width, right_h, 4, 4
            )

        # ---- 标签 ----
        painter.setPen(QColor(26, 43, 94))
        font = QFont("sans-serif", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(left_x, bar_area_bottom + 15, bar_width, 16,
                         Qt.AlignmentFlag.AlignCenter, "L 左")
        painter.drawText(right_x, bar_area_bottom + 15, bar_width, 16,
                         Qt.AlignmentFlag.AlignCenter, "R 右")

        # ---- dB 数值 ----
        font_small = QFont("sans-serif", 9)
        painter.setFont(font_small)
        painter.setPen(QColor(80, 90, 120))
        painter.drawText(left_x, bar_area_top - 16, bar_width, 14,
                         Qt.AlignmentFlag.AlignCenter, f"{self._left_db:.1f}dB")
        painter.drawText(right_x, bar_area_top - 16, bar_width, 14,
                         Qt.AlignmentFlag.AlignCenter, f"{self._right_db:.1f}dB")

        # ---- 方向指示 ----
        font_dir = QFont("sans-serif", 14, QFont.Weight.Bold)
        painter.setFont(font_dir)

        if "左" in self._direction:
            painter.setPen(QColor(230, 110, 20))
        elif "右" in self._direction:
            painter.setPen(QColor(230, 110, 20))
        else:
            painter.setPen(QColor(150, 150, 150))

        dir_y = bar_area_top + bar_area_height // 2 - 10
        painter.drawText(center_x - 25, dir_y, 50, 24,
                         Qt.AlignmentFlag.AlignCenter, self._direction if self._direction else "...")

        # ---- 刻度线 ----
        painter.setPen(QPen(QColor(160, 170, 190, 100), 1))
        font_tick = QFont("sans-serif", 7)
        painter.setFont(font_tick)
        for db in [-50, -40, -30, -20, -10, 0]:
            y = bar_area_bottom - db_to_height(db)
            painter.drawLine(left_x - 4, y, left_x - 1, y)
            painter.drawLine(right_x + bar_width + 1, y, right_x + bar_width + 4, y)

        painter.end()

    # ================================================================ Keys FIFO
    def _setup_keys_fifo(self):
        try:
            self._keys_fd = os.open(KEYS_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            self._keys_notifier = QSocketNotifier(
                self._keys_fd, QSocketNotifier.Type.Read, self
            )
            self._keys_notifier.activated.connect(self._on_key_fifo)
            print(f"{TAG} Keys FIFO opened", flush=True)
        except Exception as e:
            print(f"{TAG} Keys FIFO error: {e}", flush=True)
            self._keys_fd = -1

    def _on_key_fifo(self, fd: int):
        try:
            data = os.read(fd, 32)
            if data:
                for line in data.decode().strip().split("\n"):
                    if line.strip():
                        qt_key = int(line.strip())
                        ev = QKeyEvent(
                            QKeyEvent.Type.KeyPress,
                            qt_key,
                            Qt.KeyboardModifier.NoModifier,
                        )
                        QApplication.postEvent(self, ev)
        except Exception as e:
            print(f"{TAG} key fifo read error: {e}", flush=True)

    # ================================================================ Key events
    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()

        if key == Qt.Key.Key_Back:  # C → 退出
            print(f"{TAG} KEY_BACK → exit", flush=True)
            self.close()

        elif key == Qt.Key.Key_Left:  # A → 自动追踪开关
            self._auto_track = not self._auto_track
            state = "ON" if self._auto_track else "OFF"
            print(f"{TAG} auto-track {state}", flush=True)

        elif key == Qt.Key.Key_Right:  # B → 灵敏度切换
            self._sensitivity_idx = (self._sensitivity_idx + 1) % len(SENSITIVITY_PRESETS)
            name, thr = SENSITIVITY_PRESETS[self._sensitivity_idx]
            self._threshold = thr
            print(f"{TAG} sensitivity → {name} ({thr}dB)", flush=True)

        elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return):  # D → 手动转身
            if self._direction and "左" in self._direction:
                print(f"{TAG} manual turn LEFT", flush=True)
                self._do_turn(-1)
            elif self._direction and "右" in self._direction:
                print(f"{TAG} manual turn RIGHT", flush=True)
                self._do_turn(1)
            else:
                print(f"{TAG} manual turn: no clear direction", flush=True)

    # ================================================================ Layout
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        self._status.setGeometry(10, h - 40, w - 20, 16)

    # ================================================================ Cleanup
    def closeEvent(self, ev):
        print(f"{TAG} closing...", flush=True)
        self._running = False
        self._paint_timer.stop()
        self._exit_timer.stop()
        if hasattr(self, "_keys_fd") and self._keys_fd >= 0:
            try:
                os.close(self._keys_fd)
            except Exception:
                pass
        if self._dog:
            try:
                self._dog.turn(0)
            except Exception:
                pass
        super().closeEvent(ev)


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    app = QApplication(sys.argv)
    apply_app_palette(app)

    page = SoundLocatePage()
    page.setFixedSize(SCREEN_W, SCREEN_H)
    page.showFullScreen()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
