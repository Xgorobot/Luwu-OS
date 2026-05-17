#!/usr/bin/env python3
"""
Luwu OS - Coding (Blockly 图形编程) 应用
由 Luwu OS launcher 启动，完全参考 XGO-PI-CM5/common/demos/run_blockly.py 实现。

功能：
- 启动 xgo_blockly Web 服务（独立虚拟环境）
- LCD 显示 IP:port 及图标
- 浏览 / 运行 xgoBlocklyProjects 下的 .py 程序
- 四角按键导航

物理按键映射（luwu-keys.dts gpio-keys）：
  A (GPIO17, top-left)     KEY_LEFT   → 上移 / 上一个
  B (GPIO22, top-right)    KEY_RIGHT  → 下移 / 下一个
  C (GPIO23, bottom-left)  KEY_BACK   → 返回 / 退出
  D (GPIO24, bottom-right) KEY_ENTER  → 进入列表 / 运行 / 停止
"""
import sys
import os
import time
import signal
import socket
import struct
import fcntl
import threading
import subprocess

from PIL import Image, ImageDraw, ImageFont

from PySide6.QtCore import Qt, QTimer, QSocketNotifier
from PySide6.QtGui import QKeyEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QLabel

# ========================================================================
# 配置常量
# ========================================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PICS_DIR = os.path.join(APP_DIR, "pics")
KEYS_FIFO = "/tmp/luwu_keys.fifo"
BLOCKLY_PORT = 8000

# 接入 luwu-os 全局 i18n（去除 XGO-PI-CM5 依赖）
LUWU_ROOT = "/home/pi/luwu-os"
if LUWU_ROOT not in sys.path:
    sys.path.insert(0, LUWU_ROOT)
try:
    from libs.i18n import get_lang as _i18n_get_lang, FONT_PATH as _I18N_FONT_PATH
except Exception:
    _i18n_get_lang = None
    _I18N_FONT_PATH = ""

LANGUAGE_INI = "/home/pi/luwu-os/configs/language.ini"
FONT_PATH = _I18N_FONT_PATH or "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"

# xgo_blockly 相关路径（使用系统 Python，xgo_blockly 已安装）
BLOCKLY_PYTHON = sys.executable  # 系统 python3
BLOCKLY_SERVICES_DIR = os.path.expanduser(
    "~/.local/lib/python3.13/site-packages/xgo_blockly/services"
)
# Blockly 用户项目目录（用户态目录，自动创建；不再依赖 XGO-PI-CM5）
BLOCKLY_PROJECTS_DIR = os.path.expanduser("~/xgoBlocklyProjects")
try:
    os.makedirs(BLOCKLY_PROJECTS_DIR, exist_ok=True)
except Exception:
    pass

# ========================================================================
# 页面状态
# ========================================================================
PAGE_LOADING = -1
PAGE_MAIN = 0
PAGE_FILE_LIST = 1

# ========================================================================
# 语言支持
# ========================================================================
def _detect_language():
    if _i18n_get_lang:
        try:
            return _i18n_get_lang()
        except Exception:
            pass
    try:
        with open(LANGUAGE_INI, "r") as f:
            lang = f.read().strip()
            return lang if lang in ("cn", "en") else "cn"
    except Exception:
        return "cn"

LA = _detect_language()

_TEXTS = {
    "cn": {
        "main_title": "图形编程",
        "loading": "正在启动服务",
        "loading_dots": "...",
        "loading_hint": "请稍候",
        "starting": "正在启动服务...",
        "starting_hint": "请稍候",
        "program_list": "程序列表",
        "no_program": "暂无程序",
        "a_up": "A:上移",
        "b_down": "B:下移",
        "d_run": "D:运行",
        "d_stop": "D:停止",
        "c_back": "C:返回",
        "d_enter": "D:进入",
        "running": "运行中:",
        "stopped": "已停止",
        "service_running": "服务运行中",
    },
    "en": {
        "main_title": "Blockly Coding",
        "loading": "Starting service",
        "loading_dots": "...",
        "loading_hint": "Please wait",
        "starting": "Starting service...",
        "starting_hint": "Please wait",
        "program_list": "Program List",
        "no_program": "No programs",
        "a_up": "A:Up",
        "b_down": "B:Down",
        "d_run": "D:Run",
        "d_stop": "D:Stop",
        "c_back": "C:Back",
        "d_enter": "D:Enter",
        "running": "Running:",
        "stopped": "Stopped",
        "service_running": "Service running",
    },
}

def t(key, *args):
    text = _TEXTS.get(LA, _TEXTS["cn"]).get(key, key)
    if args:
        text = text.format(*args)
    return text


# ========================================================================
# 网络工具
# ========================================================================
def get_ip_address(ifname: str) -> str:
    """获取指定网络接口的 IP 地址。"""
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
        return ""

def get_local_ip() -> str:
    """获取本地 IP 地址。"""
    for iface in ["wlan0", "eth0"]:
        try:
            ip = get_ip_address(iface)
            if ip:
                return ip
        except Exception:
            continue
    return "127.0.0.1"


# ========================================================================
# 端口检测
# ========================================================================
def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检测端口是否已被占用。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return True


def kill_blockly_service():
    """清理可能残留的 xgo_blockly 服务进程。"""
    try:
        subprocess.run(["pkill", "-f", "xgo_blockly"], capture_output=True)
        subprocess.run(["fuser", "-k", "8000/tcp"], capture_output=True)
        time.sleep(0.5)
        print("[coding] cleaned up blockly processes", flush=True)
    except Exception as e:
        print(f"[coding] cleanup error: {e}", flush=True)


# ========================================================================
# Blockly 服务管理器
# ========================================================================
class BlocklyServiceManager:
    """管理 xgo_blockly 服务的启动/停止。"""

    def __init__(self):
        self.process = None
        self.is_running = False

    def start(self):
        """在独立线程中启动 Blockly 服务。"""
        if not os.path.exists(BLOCKLY_PYTHON):
            print(f"[coding] ERROR: venv not found at {BLOCKLY_PYTHON}", flush=True)
            return

        # 验证 xgo_blockly 已安装
        try:
            result = subprocess.run(
                [BLOCKLY_PYTHON, "-c", "import xgo_blockly"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                print("[coding] ERROR: xgo_blockly not installed in venv", flush=True)
                return
            print("[coding] xgo_blockly verified OK", flush=True)
        except Exception as e:
            print(f"[coding] verification warning: {e}", flush=True)

        print(f"[coding] starting xgo_blockly via {BLOCKLY_PYTHON}", flush=True)

        child_env = os.environ.copy()
        child_env.pop("FLASK_ENV", None)
        child_env.setdefault("FLASK_DEBUG", "1")

        try:
            self.process = subprocess.Popen(
                [BLOCKLY_PYTHON, "-m", "xgo_blockly.cli"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=child_env,
            )
            self.is_running = True
            print(f"[coding] Blockly service started PID={self.process.pid}", flush=True)

            # 阻塞等待进程结束
            return_code = self.process.wait()
            self.is_running = False
            if return_code != 0:
                stderr_output = self.process.stderr.read() if self.process.stderr else ""
                print(f"[coding] service exited code={return_code}: {stderr_output}", flush=True)
            else:
                print("[coding] service exited normally", flush=True)
        except Exception as e:
            print(f"[coding] service start error: {e}", flush=True)
            self.is_running = False

    def stop(self):
        """优雅地停止 Blockly 服务。"""
        if not self.process or not self.is_running:
            return True
        try:
            print("[coding] stopping Blockly service...", flush=True)
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
                print("[coding] service stopped gracefully", flush=True)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
                print("[coding] service force killed", flush=True)
            self.is_running = False
            return True
        except ProcessLookupError:
            self.is_running = False
            return True
        except Exception as e:
            print(f"[coding] stop error: {e}", flush=True)
            return False

    def is_alive(self):
        if not self.process:
            return False
        poll = self.process.poll()
        if poll is not None:
            self.is_running = False
        return self.is_running


# ========================================================================
# 程序运行管理器
# ========================================================================
class ProgramRunner:
    """管理 Blockly 程序的运行和停止。"""

    def __init__(self):
        self.process = None
        self.is_running = False

    def run(self, file_path: str) -> bool:
        """在虚拟环境中运行 .py 程序。"""
        if not os.path.exists(file_path):
            print(f"[coding] file not found: {file_path}", flush=True)
            return False
        if not os.path.exists(BLOCKLY_PYTHON):
            print(f"[coding] venv not found: {BLOCKLY_PYTHON}", flush=True)
            return False

        try:
            print(f"[coding] running: {file_path}", flush=True)
            child_env = os.environ.copy()
            child_env.pop("FLASK_ENV", None)
            child_env.setdefault("FLASK_DEBUG", "1")

            if os.path.exists(BLOCKLY_SERVICES_DIR):
                child_env["PYTHONPATH"] = BLOCKLY_SERVICES_DIR
            child_env["PYTHONUNBUFFERED"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"

            self.process = subprocess.Popen(
                [BLOCKLY_PYTHON, "-u", file_path],
                stdout=None,
                stderr=None,
                text=True,
                env=child_env,
            )
            self.is_running = True
            print(f"[coding] program started PID={self.process.pid}", flush=True)
            return True
        except Exception as e:
            print(f"[coding] run error: {e}", flush=True)
            self.is_running = False
            return False

    def stop(self) -> bool:
        """停止正在运行的程序。"""
        if not self.process or not self.is_running:
            return True
        try:
            print("[coding] stopping program...", flush=True)
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
                print("[coding] program stopped gracefully", flush=True)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
                print("[coding] program force killed", flush=True)
            self.is_running = False
            return True
        except ProcessLookupError:
            self.is_running = False
            return True
        except Exception as e:
            print(f"[coding] stop error: {e}", flush=True)
            return False

    def check_alive(self) -> bool:
        if not self.process:
            return False
        poll = self.process.poll()
        if poll is not None:
            self.is_running = False
        return self.is_running


# ========================================================================
# 文件列表管理器
# ========================================================================
class FileListManager:
    """管理 Blockly 项目文件列表。"""

    def __init__(self):
        self.files = []
        self.selected_index = 0
        self.scroll_offset = 0
        self.visible_count = 6
        self.refresh()

    def refresh(self):
        """刷新文件列表。"""
        try:
            if not os.path.exists(BLOCKLY_PROJECTS_DIR):
                self.files = []
                return
            all_files = [
                f for f in os.listdir(BLOCKLY_PROJECTS_DIR) if f.endswith(".py")
            ]
            self.files = sorted(all_files)
            self.selected_index = 0
            self.scroll_offset = 0
        except Exception as e:
            print(f"[coding] file list error: {e}", flush=True)
            self.files = []

    def count(self) -> int:
        return len(self.files)

    def selected_filepath(self):
        """返回当前选中文件的完整路径。"""
        if not self.files:
            return None
        return os.path.join(BLOCKLY_PROJECTS_DIR, self.files[self.selected_index])

    def selected_filename(self):
        """返回当前选中文件名。"""
        if not self.files:
            return None
        return self.files[self.selected_index]

    def move_up(self):
        if self.selected_index > 0:
            self.selected_index -= 1
            if self.selected_index < self.scroll_offset:
                self.scroll_offset = self.selected_index

    def move_down(self):
        if self.selected_index < len(self.files) - 1:
            self.selected_index += 1
            if self.selected_index >= self.scroll_offset + self.visible_count:
                self.scroll_offset = self.selected_index - self.visible_count + 1


# ========================================================================
# 主界面 Widget
# ========================================================================
class CodingPage(QWidget):
    """图形编程主界面。"""

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0a0a1a;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # --- 页面状态 ---
        self.current_page = PAGE_LOADING  # 启动时显示 loading
        self._page_needs_redraw = True

        # --- loading 动画 ---
        self._loading_frame = 0           # 0..3 循环
        self._loading_timer = None

        # --- 显示 Label (fullscreen) ---
        self.display_label = QLabel(self)
        self.display_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_label.setStyleSheet("background-color: #0a0a1a;")

        # --- 四角按键提示 Label ---
        corner_style = (
            "color: #ffffff; font-size: 13px; font-weight: bold; "
            "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
        )
        self.corner_tl = QLabel("", self)
        self.corner_tl.setStyleSheet(corner_style)
        self.corner_tr = QLabel("", self)
        self.corner_tr.setStyleSheet(corner_style)
        self.corner_bl = QLabel("", self)
        self.corner_bl.setStyleSheet(corner_style)
        self.corner_br = QLabel("", self)
        self.corner_br.setStyleSheet(corner_style)

        # --- 状态栏 (底部中间，选中文案/运行状态) ---
        self.status_label = QLabel("", self)
        self.status_label.setStyleSheet(
            "color: #18df6b; font-size: 11px; "
            "background-color: rgba(0,0,0,0.5); padding: 2px 8px; border-radius: 3px;"
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # --- 管理器 ---
        self.service_manager = BlocklyServiceManager()
        self.file_list_manager = FileListManager()
        self.program_runner = ProgramRunner()

        # --- 本地 IP ---
        self.local_ip = get_local_ip()
        print(f"[coding] Local IP: {self.local_ip}", flush=True)

        # --- Keys FIFO ---
        self._keys_fd = -1
        self._keys_notifier = None
        self._setup_keys_fifo()

        # --- 字体加载（用于 PIL 渲染） ---
        self._font16 = None
        self._font14 = None
        self._font12 = None
        self._font10 = None
        try:
            self._font16 = ImageFont.truetype(FONT_PATH, 16)
            self._font14 = ImageFont.truetype(FONT_PATH, 14)
            self._font12 = ImageFont.truetype(FONT_PATH, 12)
            self._font10 = ImageFont.truetype(FONT_PATH, 10)
        except Exception:
            self._font16 = ImageFont.load_default()
            self._font14 = ImageFont.load_default()
            self._font12 = ImageFont.load_default()
            self._font10 = ImageFont.load_default()

        # --- 图标预加载 ---
        self._icon_ai = None
        self._icon_blockly = None
        self._icon_wifi = None
        self._load_icons()

        # --- 启动 loading 动画（每 400ms 刷新一次）---
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._animate_loading)
        self._loading_timer.start(400)

        # --- 延迟启动服务 ---
        QTimer.singleShot(200, self._start_service)

    def _animate_loading(self):
        """loading 动画：循环 ... 动画帧。"""
        self._loading_frame = (self._loading_frame + 1) % 4
        self._render_and_display()

    # ---- 图片加载 ----
    def _load_icons(self):
        ai_path = os.path.join(PICS_DIR, "icon_ai.png")
        blockly_path = os.path.join(PICS_DIR, "icon_blockly.png")
        wifi_path = os.path.join(PICS_DIR, "wifi@2x.jpg")

        try:
            if os.path.exists(ai_path):
                self._icon_ai = Image.open(ai_path).resize((60, 60))
            if os.path.exists(blockly_path):
                self._icon_blockly = Image.open(blockly_path).resize((60, 60))
            if os.path.exists(wifi_path):
                self._icon_wifi = Image.open(wifi_path).resize((30, 26))
        except Exception as e:
            print(f"[coding] icon load error: {e}", flush=True)

    # ---- 服务启动 ----
    def _start_service(self):
        # 清理残留
        kill_blockly_service()

        # 检查端口
        if port_in_use(BLOCKLY_PORT):
            print(f"[coding] port {BLOCKLY_PORT} in use, waiting...", flush=True)
            QTimer.singleShot(2000, self._start_service)
            return

        # 后台线程启动服务
        t = threading.Thread(target=self.service_manager.start, daemon=True)
        t.start()

        # 等待服务就绪
        QTimer.singleShot(3000, self._on_service_ready)

    def _on_service_ready(self):
        """服务启动完成后的回调。"""
        # 停止 loading 动画
        if self._loading_timer:
            self._loading_timer.stop()
            self._loading_timer = None

        self.current_page = PAGE_MAIN
        self._page_needs_redraw = True
        self._render_and_display()
        self._update_corner_labels()
        print("[coding] service ready, main page shown", flush=True)

        # 定期检查服务状态
        self._check_timer = QTimer(self)
        self._check_timer.timeout.connect(self._check_status)
        self._check_timer.start(1000)

    def _check_status(self):
        """定期检查服务和程序状态。"""
        # 检查程序是否意外退出
        if (self.program_runner.process is not None and
                self.program_runner.process.poll() is not None):
            print("[coding] program exited unexpectedly", flush=True)
            self.program_runner.is_running = False
            if self.current_page == PAGE_FILE_LIST:
                self._page_needs_redraw = True
                self._render_and_display()

        # 检查服务是否意外退出
        if not self.service_manager.is_alive():
            pass  # 服务可能在主页面退出

    # ---- Keys FIFO ----
    def _setup_keys_fifo(self):
        try:
            self._keys_fd = os.open(KEYS_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            self._keys_notifier = QSocketNotifier(
                self._keys_fd, QSocketNotifier.Type.Read, self
            )
            self._keys_notifier.activated.connect(self._on_key_fifo)
            print("[coding] Keys FIFO opened", flush=True)
        except Exception as e:
            print(f"[coding] Keys FIFO error: {e}", flush=True)

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
            print(f"[coding] key fifo read error: {e}", flush=True)

    # ---- 按键处理 ----
    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()
        print(f"[coding] key: {key} page={self.current_page}", flush=True)

        if self.current_page == PAGE_LOADING:
            self._handle_loading_keys(key)
        elif self.current_page == PAGE_MAIN:
            self._handle_main_keys(key)
        elif self.current_page == PAGE_FILE_LIST:
            self._handle_filelist_keys(key)

    def _handle_loading_keys(self, key):
        if key == Qt.Key.Key_Back:  # C → 退出
            print("[coding] C pressed during loading → exit", flush=True)
            self._do_exit()

    def _handle_main_keys(self, key):
        if key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D → 进入程序列表
            print("[coding] D pressed → enter file list", flush=True)
            self.current_page = PAGE_FILE_LIST
            self.file_list_manager.refresh()
            self._page_needs_redraw = True
            self._render_and_display()
            self._update_corner_labels()
        elif key == Qt.Key.Key_Back:  # C → 退出
            print("[coding] C pressed → exit", flush=True)
            self._do_exit()

    def _handle_filelist_keys(self, key):
        if key == Qt.Key.Key_Left:  # A → 上移
            print("[coding] A pressed → up", flush=True)
            self.file_list_manager.move_up()
            self._page_needs_redraw = True
            self._render_and_display()
        elif key == Qt.Key.Key_Right:  # B → 下移
            print("[coding] B pressed → down", flush=True)
            self.file_list_manager.move_down()
            self._page_needs_redraw = True
            self._render_and_display()
        elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D → 运行 / 停止
            selected = self.file_list_manager.selected_filepath()
            if not selected:
                return
            if self.program_runner.check_alive():
                # 正在运行 → 停止
                print("[coding] D pressed → stop program", flush=True)
                self.program_runner.stop()
                self._page_needs_redraw = True
                self._render_and_display()
            else:
                # 未运行 → 启动
                print(f"[coding] D pressed → run: {selected}", flush=True)
                self.program_runner.run(selected)
                self._page_needs_redraw = True
                self._render_and_display()
        elif key == Qt.Key.Key_Back:  # C → 返回主页
            print("[coding] C pressed → back to main", flush=True)
            self.current_page = PAGE_MAIN
            self._page_needs_redraw = True
            self._render_and_display()
            self._update_corner_labels()

    def _do_exit(self):
        """退出应用。"""
        if self.program_runner.check_alive():
            self.program_runner.stop()
        self.service_manager.stop()
        self.close()

    # ---- 四角标签更新 ----
    def _update_corner_labels(self):
        if self.current_page == PAGE_MAIN:
            self.corner_tl.setText("")          # 左上：无
            self.corner_tr.setText("")           # 右上：无
            self.corner_bl.setText(t("c_back"))  # 左下：C 返回退出
            self.corner_br.setText(t("d_enter")) # 右下：D 进入列表
            self.corner_bl.setStyleSheet(
                "color: #cccccc; font-size: 13px; font-weight: bold; "
                "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
            )
            self.corner_br.setStyleSheet(
                "color: #a78bfa; font-size: 13px; font-weight: bold; "
                "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
            )
        elif self.current_page == PAGE_FILE_LIST:
            self.corner_tl.setText(t("a_up"))     # 左上：A 上移
            self.corner_tr.setText(t("b_down"))   # 右上：B 下移
            self.corner_bl.setText(t("c_back"))   # 左下：C 返回
            if self.program_runner.check_alive():
                self.corner_br.setText(t("d_stop"))  # 右下：D 停止
                self.corner_br.setStyleSheet(
                    "color: #ff6b6b; font-size: 13px; font-weight: bold; "
                    "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
                )
            else:
                self.corner_br.setText(t("d_run"))   # 右下：D 运行
                self.corner_br.setStyleSheet(
                    "color: #18df6b; font-size: 13px; font-weight: bold; "
                    "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
                )
            self.corner_tl.setStyleSheet(
                "color: #cccccc; font-size: 13px; font-weight: bold; "
                "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
            )
            self.corner_tr.setStyleSheet(
                "color: #cccccc; font-size: 13px; font-weight: bold; "
                "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
            )
            self.corner_bl.setStyleSheet(
                "color: #cccccc; font-size: 13px; font-weight: bold; "
                "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
            )

        self._reposition_corners()

    def _reposition_corners(self):
        w, h = self.width(), self.height()
        pad = 8
        for lbl in [self.corner_tl, self.corner_tr, self.corner_bl, self.corner_br]:
            lbl.adjustSize()
            lbl.raise_()
        self.corner_tl.move(pad, pad)
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    # ---- PIL 渲染 ----
    def _render_and_display(self):
        """使用 PIL 渲染当前页面，转换为 QPixmap 显示。"""
        bg = Image.new("RGB", (320, 240), (10, 10, 26))
        draw = ImageDraw.Draw(bg)

        if self.current_page == PAGE_LOADING:
            self._render_loading_page(draw, bg)
        elif self.current_page == PAGE_MAIN:
            self._render_main_page(draw, bg)
        elif self.current_page == PAGE_FILE_LIST:
            self._render_file_list(draw, bg)

        # 转换为 QPixmap
        result = bg
        h_img, w_img = result.size[1], result.size[0]
        qimg = QImage(
            result.tobytes(), w_img, h_img, w_img * 3, QImage.Format.Format_RGB888
        )
        pixmap = QPixmap.fromImage(qimg).scaled(
            self.display_label.width(),
            self.display_label.height(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.display_label.setPixmap(pixmap)
        self._page_needs_redraw = False

    def _paste_icon(self, bg, icon, pos):
        """安全粘贴图标（自动处理透明度）。"""
        if icon is None:
            return
        if icon.mode == "RGBA":
            bg.paste(icon, pos, icon)
        elif icon.mode == "P" and "transparency" in icon.info:
            icon_rgba = icon.convert("RGBA")
            bg.paste(icon_rgba, pos, icon_rgba)
        else:
            bg.paste(icon, pos)

    def _render_loading_page(self, draw, bg):
        """渲染加载页面：图标 + 启动中 + 动画点。"""
        # 背景渐变条（简单的视觉元素）
        bar_y = 130
        bar_h = 4
        bar_max_w = 200
        bar_x = (320 - bar_max_w) // 2
        # 进度条背景
        draw.rectangle(
            [(bar_x, bar_y), (bar_x + bar_max_w, bar_y + bar_h)],
            fill=(30, 30, 50),
        )
        # 进度条前景（根据 loading_frame 增长）
        progress = (self._loading_frame + 1) * (bar_max_w // 4)
        if progress > 0:
            draw.rectangle(
                [(bar_x, bar_y), (bar_x + progress, bar_y + bar_h)],
                fill=(100, 140, 255),
            )

        # AI 图标 (右侧)
        self._paste_icon(bg, self._icon_ai, (170, 25))

        # Blockly 图标 (左侧)
        self._paste_icon(bg, self._icon_blockly, (90, 25))

        # 标题
        title = t("main_title")
        tw = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw) // 2, 100), title, font=self._font16, fill=(180, 200, 255))

        # "正在启动服务" + 动画点
        dots = "." * (self._loading_frame + 1)
        loading_text = t("loading") + dots
        lw = draw.textbbox((0, 0), loading_text, font=self._font14)[2]
        draw.text(((320 - lw) // 2, 145), loading_text, font=self._font14, fill=(200, 200, 200))

        # 副标题
        hint = t("loading_hint")
        hw = draw.textbbox((0, 0), hint, font=self._font12)[2]
        draw.text(((320 - hw) // 2, 170), hint, font=self._font12, fill=(120, 120, 140))

        # 左下 C 退出提示
        hint_c = t("c_back")
        cw = draw.textbbox((0, 0), hint_c, font=self._font10)[2]
        draw.text((10, 220), hint_c, font=self._font10, fill=(180, 180, 180))

    def _render_main_page(self, draw, bg):
        """渲染主页面：图标 + IP:port。"""
        # AI 图标 (右侧)
        self._paste_icon(bg, self._icon_ai, (170, 40))

        # Blockly 图标 (左侧)
        self._paste_icon(bg, self._icon_blockly, (90, 40))

        # WiFi 图标
        self._paste_icon(bg, self._icon_wifi, (26, 160))

        # IP:port
        ip_text = f"{self.local_ip}:{BLOCKLY_PORT}"
        tw = draw.textbbox((0, 0), ip_text, font=self._font14)[2]
        draw.text(((320 - tw) // 2, 160), ip_text, font=self._font14, fill=(255, 255, 255))

        # 标题
        title = t("main_title")
        tw2 = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw2) // 2, 120), title, font=self._font16, fill=(180, 200, 255))

        # 提示：右下 D 进入，左下 C 退出
        hint_d = t("d_enter")
        hint_c = t("c_back")
        dw = draw.textbbox((0, 0), hint_d, font=self._font10)[2]
        cw = draw.textbbox((0, 0), hint_c, font=self._font10)[2]
        draw.text((310 - dw, 220), hint_d, font=self._font10, fill=(170, 140, 255))
        draw.text((10, 220), hint_c, font=self._font10, fill=(180, 180, 180))

    def _render_file_list(self, draw, bg):
        """渲染文件列表页面。"""
        # 标题
        title = t("program_list")
        tw = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw) // 2, 6), title, font=self._font16, fill=(200, 200, 255))

        fm = self.file_list_manager

        # 无文件
        if not fm.files:
            no_text = t("no_program")
            nw = draw.textbbox((0, 0), no_text, font=self._font14)[2]
            draw.text(((320 - nw) // 2, 100), no_text, font=self._font14, fill=(150, 150, 150))
            return

        # 列出文件
        start_y = 36
        item_h = 28
        visible = fm.visible_count

        for i in range(fm.scroll_offset, min(fm.scroll_offset + visible, len(fm.files))):
            rel = i - fm.scroll_offset
            y = start_y + rel * item_h
            is_sel = (i == fm.selected_index)

            # 背景
            if is_sel:
                draw.rectangle([(6, y), (314, y + item_h - 2)], fill=(70, 55, 140))
                text_color = (255, 255, 255)
            else:
                draw.rectangle([(6, y), (314, y + item_h - 2)], fill=(40, 40, 60))
                text_color = (200, 200, 200)

            # 文件名（去 .py 后缀，截断）
            filename = fm.files[i]
            display_name = filename[:-3] if filename.endswith(".py") else filename
            if len(display_name) > 16:
                display_name = display_name[:13] + "..."

            draw.text((14, y + 5), display_name, font=self._font12, fill=text_color)

        # 四角提示（在 PIL 上也画一份作视觉参考）
        a_up = t("a_up")
        b_down = t("b_down")
        c_back = t("c_back")
        d_action = t("d_stop") if self.program_runner.check_alive() else t("d_run")
        d_color = (255, 100, 100) if self.program_runner.check_alive() else (100, 255, 120)

        draw.text((6, 6), a_up, font=self._font10, fill=(180, 180, 180))
        bw = draw.textbbox((0, 0), b_down, font=self._font10)[2]
        draw.text((314 - bw, 6), b_down, font=self._font10, fill=(180, 180, 180))
        draw.text((6, 220), c_back, font=self._font10, fill=(180, 180, 180))
        dw2 = draw.textbbox((0, 0), d_action, font=self._font10)[2]
        draw.text((314 - dw2, 220), d_action, font=self._font10, fill=d_color)

        # 底部状态：运行中/已停止 + 文件名
        if self.program_runner.check_alive():
            fn = fm.selected_filename()
            if fn:
                short_name = fn[:-3] if fn.endswith(".py") else fn
                if len(short_name) > 18:
                    short_name = short_name[:15] + "..."
                status_txt = f"{t('running')} {short_name}"
                sw = draw.textbbox((0, 0), status_txt, font=self._font10)[2]
                draw.text(((320 - sw) // 2, 195), status_txt, font=self._font10, fill=(100, 255, 120))

    # ---- 布局 ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        if w > 100 and h > 100:
            self.display_label.setGeometry(0, 0, w, h)
            self._reposition_corners()
            self.status_label.adjustSize()
            self.status_label.move(
                (w - self.status_label.width()) // 2,
                h - self.status_label.height() - 34,
            )
            self.status_label.raise_()
            # 尺寸变化时立即重绘，避免先小后大
            self._render_and_display()

    # ---- 关闭 ----
    def closeEvent(self, ev):
        if self._loading_timer:
            self._loading_timer.stop()
            self._loading_timer = None
        if self.program_runner.check_alive():
            self.program_runner.stop()
        self.service_manager.stop()
        if self._keys_notifier:
            self._keys_notifier.setEnabled(False)
        if self._keys_fd >= 0:
            try:
                os.close(self._keys_fd)
            except Exception:
                pass
        print("[coding] closing", flush=True)
        super().closeEvent(ev)


# ========================================================================
# 入口
# ========================================================================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    w = CodingPage()
    w.showFullScreen()

    rc = app.exec()
    print(f"[coding] exit rc={rc}", flush=True)


if __name__ == "__main__":
    main()
