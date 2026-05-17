#!/usr/bin/env python3
"""
PySide6 表演模式 (Performance Mode) — 由 Luwu OS launcher 启动。
对应 XGO 原厂 dog_show.py：
  - dog.perform(1)  让狗硬件层自动循环表演
  - mplayer 循环播放 Dream.mp3
  - 屏幕循环显示表情动画序列
物理按键：B(下键/KEY_BACK)=退出
"""
import os
import sys
import time
import signal
import subprocess

# ===================== 阶段计时 =====================
T0 = time.monotonic()
_stages = []

def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[perform][+{ms:7.1f}ms] {name}", flush=True)

mark("python entry")

# ===================== PySide6 导入 =====================
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QKeyEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QLabel

mark("PySide6 import done")

# PIL 用于读取 PNG 表情帧
from PIL import Image

mark("PIL import done")

# ===================== XGO 狗 =====================
sys.path.insert(0, "/home/pi/lib")
from xgolib import XGO

mark("xgolib import done")


# ===================== 常量 =====================
AUTO_EXIT_SEC = 600       # 10分钟自动退出
# 资源已迁移至 luwu-os/assets，彻底解耦 XGO-PI-CM5 依赖
EXPR_PATH = "/home/pi/luwu-os/assets/expressions/dog_LM"
MUSIC_PATH = "/home/pi/luwu-os/assets/music/Dream.mp3"

# 表情列表：(目录名, 帧数)
EXPRESSIONS = [
    ("sad", 85), ("naughty", 105), ("angry", 96), ("shy", 85),
    ("surprise", 72), ("happy", 82), ("sleepy", 88), ("wake", 58),
    ("lookaround", 107), ("love", 84), ("awkwardness", 80), ("eyes", 77),
    ("guffaw", 51), ("query", 81), ("shakehead", 64), ("dizzy", 56),
    ("wronged", 136),
]

FRAME_DELAY = 0.01   # 帧间延迟(秒), 约 100fps

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {"hint_exit": "B 退出"},
        "en": {"hint_exit": "B Exit"},
    })
except Exception:
    _T = lambda k, *a: k


# ===================== 表情播放线程 =====================
class ExpressionWorker(QThread):
    """后台循环播放表情动画序列。"""
    frame_ready = Signal(object)   # 发送 PIL Image 对象
    status_update = Signal(str)    # 当前播放的表情名

    def __init__(self):
        super().__init__()
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            for name, count in EXPRESSIONS:
                if not self._running:
                    return
                self.status_update.emit(name)
                for i in range(1, count + 1):
                    if not self._running:
                        return
                    img_path = os.path.join(EXPR_PATH, name, f"{i}.png")
                    try:
                        img = Image.open(img_path)
                        self.frame_ready.emit(img)
                    except Exception:
                        continue
                    # 用 sleep 控制帧率 (可中断)
                    t0 = time.time()
                    while time.time() - t0 < FRAME_DELAY:
                        if not self._running:
                            return
                        time.sleep(0.001)


# ===================== PySide6 页面 =====================
class PerformPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0f1530;")
        self._first_paint_logged = False

        # 状态
        self._dog = None
        self._music_proc = None
        self._active = False
        self._expression_worker = None
        self._current_expr_name = ""

        # ---- 表情画面 (全屏) ----
        self.expr_label = QLabel(self)
        self.expr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.expr_label.setStyleSheet("background-color: black;")
        self.expr_label.setScaledContents(False)

        # ---- 操作提示 (底部覆盖) ----
        hint_style = "color: #3a4060; font-size: 10px; background: transparent;"
        self.hint_bl = QLabel(_T("hint_exit"), self)
        self.hint_bl.setStyleSheet(hint_style)

        # ---- 自动退出 ----
        self._auto_exit_timer = QTimer(self)
        self._auto_exit_timer.timeout.connect(self.close)
        self._auto_exit_timer.start(AUTO_EXIT_SEC * 1000)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 延迟初始化硬件（避免阻塞 UI 创建）
        QTimer.singleShot(100, self._init_and_start)

    # ===================== 初始化和启动 =====================
    def _init_and_start(self):
        """初始化狗、启动音乐和表情循环。"""
        # 1. 初始化 XGO
        try:
            self._dog = XGO()
            fm = self._dog.read_firmware()
            if fm:
                type_map = {'M': 'Mini', 'L': 'Lite', 'W': 'Mini3W', 'R': 'Rider'}
                dog_type = type_map.get(fm[0].upper(), fm)
            else:
                dog_type = "?"
            print(f"[perform] XGO 初始化成功, 机型: {dog_type}")
            self._dog.reset()
            time.sleep(1)
        except Exception as e:
            self._dog = None
            print(f"[perform] XGO 初始化失败: {e}")

        # 2. 启动表演模式 (硬件层自动循环)
        try:
            self._dog.perform(1)
            print("[perform] dog.perform(1) 已启动")
            self._active = True
        except Exception as e:
            print(f"[perform] perform(1) 失败: {e}")

        # 3. 启动音乐
        try:
            self._music_proc = subprocess.Popen(
                f"mplayer -ao alsa:device=hw=0,0 -really-quiet -loop 0 '{MUSIC_PATH}'",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[perform] 音乐已启动: {MUSIC_PATH}")
        except Exception as e:
            print(f"[perform] 音乐启动失败: {e}")

        # 4. 启动表情动画线程
        self._expression_worker = ExpressionWorker()
        self._expression_worker.frame_ready.connect(self._on_frame)
        self._expression_worker.status_update.connect(self._on_expr_name)
        self._expression_worker.start()

        print("[perform] 表演模式运行中")

    # ===================== 帧显示 =====================
    def _on_frame(self, pil_img: Image.Image):
        """将 PIL Image 转为 QPixmap 显示。"""
        if not self._active:
            return
        try:
            # PIL RGBA → QImage
            pil_img = pil_img.convert("RGBA")
            data = pil_img.tobytes("raw", "RGBA")
            qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)

            # 缩放到显示区域,保持宽高比
            pixmap = QPixmap.fromImage(qimg).scaled(
                self.expr_label.width(), self.expr_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.expr_label.setPixmap(pixmap)
        except Exception:
            pass

    def _on_expr_name(self, name: str):
        self._current_expr_name = name

    # ===================== 布局事件 =====================
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        # 表情全屏填充
        self.expr_label.setGeometry(0, 0, w, h)
        # 退出提示固定在左下角
        pad = 12
        self.hint_bl.adjustSize()
        self.hint_bl.move(pad, h - self.hint_bl.height() - pad)

    # ---- 首帧日志 ----
    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")
            summary = self._stage_summary()
            print("[perform] boot breakdown:\n" + summary, flush=True)

    def _stage_summary(self) -> str:
        lines = []
        prev = 0.0
        for name, ms in _stages:
            lines.append(f"{name}: {ms:.0f}ms (+{ms - prev:.0f})")
            prev = ms
        return " | ".join(lines)

    # ===================== 按键 =====================
    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Down or ev.key() == Qt.Key.Key_Back:
            # B 键 → 退出
            print("[perform] B key -> exit", flush=True)
            self.close()

        # 重置自动退出计时器
        self._auto_exit_timer.start(AUTO_EXIT_SEC * 1000)

    # ===================== 关闭清理 =====================
    def closeEvent(self, ev):
        print("[perform] closing", flush=True)
        self._active = False
        self._auto_exit_timer.stop()

        # 1. 停止表情线程
        if self._expression_worker:
            self._expression_worker.stop()
            self._expression_worker.wait(3000)

        # 2. 停止表演模式
        if self._dog:
            try:
                self._dog.perform(0)
                time.sleep(0.3)
                self._dog.reset()
                time.sleep(0.3)
            except Exception:
                pass

        # 3. 停止音乐
        if self._music_proc:
            try:
                self._music_proc.terminate()
                self._music_proc.wait(timeout=2)
            except Exception:
                pass
        # 兜底：强制杀掉 mplayer
        try:
            os.system("pkill mplayer 2>/dev/null")
        except Exception:
            pass

        print("[perform] cleaned up")
        super().closeEvent(ev)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = PerformPage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[perform] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
