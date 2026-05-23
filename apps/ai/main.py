#!/usr/bin/env python3
"""
AI Chat - PySide6 版 (Luwu OS App)
由 Luwu OS launcher 启动，使用 PySide6 全屏界面替代 LCD 驱动。

ASR -> LLM (streaming + Function Call) -> TTS + Expression -> Loop

按键映射:
  D 键 (右下 / KEY_ENTER)   → 二维码页: 开始聊天 / 聊天中: 回到二维码页
  C 键 (左下 / KEY_BACK)    → 退出
"""

import os
import sys
import time
import json
import base64
import signal
import threading
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# ===== 路径配置 =====
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# ===== PySide6 imports =====
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeyEvent, QPixmap, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
)

# ===== Backend modules =====
from web_server import ConfigWebServer, load_config, save_config
from asr_manager import create_asr
from llm_manager import LLMManager, StreamSentenceSplitter
from tts_manager import create_tts
from emotion_manager import EmotionManager, EMOTION_NUM_MAP
from tools import ToolManager
from state_machine import StateMachine, State

# ===== Constants =====
SCREEN_W, SCREEN_H = 320, 240
BG_COLOR = (15, 21, 46)
DING_WAV = "/home/pi/luwu-os/assets/music/ding.wav"  # 资源已迁移，解耦 XGO-PI-CM5
AUTO_EXIT_SEC = 600  # 10 minutes auto exit

# Loading 页 PIL 绘制使用的字体路径（优先 app 本地 msyh.ttc，后退系统字体）
_AI_FONT_CANDIDATES = [
    os.path.join(APP_DIR, "msyh.ttc"),
    "/home/pi/luwu-os/assets/fonts/msyh.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]
_LOADING_FONT_PATH = next((p for p in _AI_FONT_CANDIDATES if os.path.exists(p)), "")

# Loading / 启动页背景图（全屏底图）
_APP_BG_IMAGE_PATH = "/home/pi/luwu-os/assets/images/app_bg.png"

# ===== i18n =====
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    from libs.theme import Asset as T_Asset
    _T = _Translator({
        "cn": {
            "corner_exit": "退出",
            "corner_start": "开始",
            "loading_title": "AI 启动中",
            "loading_hint": "正在加载服务···",
        },
        "en": {
            "corner_exit": "Exit",
            "corner_start": "Start",
            "loading_title": "AI starting",
            "loading_hint": "Loading services···",
        },
    })
except Exception:
    _T = lambda k, *a: k

print("=" * 50)
print("  AI Chat (PySide6) - Starting...")
print("=" * 50)

# ===== Helper: PIL Image -> QPixmap =====


def pil_to_qpixmap(pil_image):
    """Convert PIL Image to QPixmap"""
    try:
        buffer = BytesIO()
        pil_image.save(buffer, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue(), "PNG")
        return pixmap
    except Exception as e:
        print(f"[UI] pil_to_qpixmap error: {e}")
        return QPixmap(SCREEN_W, SCREEN_H)


def play_ding():
    """Play ding sound to indicate wake-up"""
    try:
        os.system(f"aplay {DING_WAV} 2>/dev/null &")
    except Exception:
        pass


# ===== PySide6 Fullscreen Page =====


class AIChatPage(QWidget):
    """AI Chat PySide6 全屏界面"""

    # Signal: emit PIL Image from any thread → slot runs on GUI thread
    _display_signal = Signal(object)
    # Signal: toggle bottom corner hints from any thread → slot runs on GUI thread
    _corner_visible_signal = Signal(bool)
    # Signal: request idle screen refresh from non-GUI thread
    _refresh_idle_signal = Signal()
    # Signal: backend init done (emitted from worker thread)
    _init_done_signal = Signal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0f1530;")
        self._first_paint_logged = False
        self.running = True
        # 启动阶段标识：在后端初始化完成前为 True，期间只响应 C 退出键
        self._is_loading = True
        self._loading_frame = 0
        # 后端就绪标志（由 worker 线程设置，主线程轮询）
        self._backend_ready = False
        # 对话取消标志：按 D 回到配置页面时置 True，对话线程检查此标志退出
        self._conversation_cancelled = False

        # ---- Display label (acts as LCD, fills entire widget) ----
        self.display = QLabel(self)
        self.display.setGeometry(0, 0, self.width(), self.height())
        self.display.setScaledContents(True)
        self.display.setStyleSheet("background-color: #0f1530;")

        # ---- Status label (overlay on bottom center) ----
        # 中间提示文字与左下/右下 corner 重复，默认隐藏；仅在需要显示状态信息时使用
        self.status_label = QLabel("", self)
        f2 = QFont()
        f2.setPointSize(10)
        self.status_label.setFont(f2)
        self.status_label.setStyleSheet("color: #8892c9; background: transparent;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.hide()

        # ---- Loading字体（PIL 绘制整张 pixmap 到 display，避免子控件层叠问题） ----
        try:
            self._loading_font_title = ImageFont.truetype(_LOADING_FONT_PATH, 20) if _LOADING_FONT_PATH else ImageFont.load_default()
            self._loading_font_hint = ImageFont.truetype(_LOADING_FONT_PATH, 12) if _LOADING_FONT_PATH else ImageFont.load_default()
        except Exception:
            self._loading_font_title = ImageFont.load_default()
            self._loading_font_hint = ImageFont.load_default()

        # ---- 启动页背景底图（PIL 缓存，每帧 .copy() 后再添加进度动画） ----
        self._bg_pil = None
        try:
            if os.path.exists(_APP_BG_IMAGE_PATH):
                self._bg_pil = Image.open(_APP_BG_IMAGE_PATH).convert("RGB").resize((SCREEN_W, SCREEN_H))
        except Exception as e:
            print(f"[ai_chat] bg image load error: {e}", flush=True)

        # ---- Loading overlay（启动期间快速可见，避免黑屏） ----
        # 采用 Coding 同样的 PIL→QPixmap→QLabel 整张贴图方案，
        # 上一版用独立 QLabel 子控件 + hide() 在 linuxfb 上不生效、导致 loading 不能被覆盖
        # 立刻贴上首帧 loading，让用户启动后第一眼就看到内容不是黑屏
        self._render_loading()

        # ---- Corner hints (icon + text) ----
        ICON_SIZE = 16
        corner_style = "color: #5c6a9c; font-size: 11px; background: transparent;"

        # Bottom-left: C: 退出 (icon_back on left)
        self.corner_bl = QWidget(self)
        self.corner_bl.setStyleSheet("background: transparent;")
        bl_layout = QHBoxLayout(self.corner_bl)
        bl_layout.setContentsMargins(0, 0, 0, 0)
        bl_layout.setSpacing(3)
        bl_icon = QLabel(self.corner_bl)
        bl_icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        bl_icon.setScaledContents(True)
        bl_pix = QPixmap(T_Asset.icon_back)
        if not bl_pix.isNull():
            bl_icon.setPixmap(bl_pix.scaled(ICON_SIZE, ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        bl_text = QLabel(_T("corner_exit"), self.corner_bl)
        bl_text.setStyleSheet(corner_style)
        bl_layout.addWidget(bl_icon)
        bl_layout.addWidget(bl_text)

        # Bottom-right: D: 开始 (text on left, icon_enter on right)
        # 文案动态：二维码页且配置完成时显示"开始"；配置未完成时隐藏整个按钮
        self.corner_br = QWidget(self)
        self.corner_br.setStyleSheet("background: transparent;")
        br_layout = QHBoxLayout(self.corner_br)
        br_layout.setContentsMargins(0, 0, 0, 0)
        br_layout.setSpacing(3)
        self._br_text_label = QLabel(_T("corner_start"), self.corner_br)
        self._br_text_label.setStyleSheet(corner_style)
        br_icon = QLabel(self.corner_br)
        br_icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        br_icon.setScaledContents(True)
        br_pix = QPixmap(T_Asset.icon_enter)
        if not br_pix.isNull():
            br_icon.setPixmap(br_pix.scaled(ICON_SIZE, ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        br_layout.addWidget(self._br_text_label)
        br_layout.addWidget(br_icon)
        # 启动期间 D 不可用，隐藏右下提示
        self.corner_br.hide()

        # ---- Focus for key events ----
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ---- Auto exit fallback ----
        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)

        # ---- Connect display signal ----
        self._display_signal.connect(self._on_display_update)
        self._corner_visible_signal.connect(self._on_corner_visible)
        self._refresh_idle_signal.connect(self._on_refresh_idle)
        self._init_done_signal.connect(self._on_init_done)

        # ---- Loading动画定时器（动态小点） ----
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._on_loading_tick)
        self._loading_timer.start(400)

        # ---- 主线程轮询后端就绪标志（比跨线程 signal 更可靠） ----
        # 上一版用 Signal.emit 跨线程招手 GUI slot，实际环境下 slot 未被调度导致 loading 永不退出
        self._ready_poll_timer = QTimer(self)
        self._ready_poll_timer.timeout.connect(self._poll_backend_ready)
        self._ready_poll_timer.start(150)

        # 后端初始化：以 Coding 同样的方式，用 QTimer.singleShot 在首帧上屏后起后台线程
        # 不在 __init__ 同步走重初始化，避免主线程被 time.sleep / SDK 导入阻塞导致黑屏与按键不响应
        self._init_thread = None
        QTimer.singleShot(200, self._kick_init_worker)

    def _kick_init_worker(self):
        """在 GUI 事件循环跑起来之后启动后台 worker。"""
        if self._init_thread is not None:
            return
        # 关键修复：在 GUI 主线程预热 XGOEDU 单例。
        # XGOEDU.__init__ 会创建 QApplication / QPixmap / QLabel 并 .show()，
        # 这些 Qt GUI 类不能在后台线程创建（在 worker 里创建会死锁卡住）。
        # 预热后 singleton 生效，worker 后续 import tools / robot_tools 时会直接拿到缓存实例。
        self._preheat_xgoedu_on_gui_thread()
        print("[ai_chat] kick init worker", flush=True)
        self._init_thread = threading.Thread(
            target=self._init_backend_worker,
            name="ai_init_worker",
            daemon=True,
        )
        self._init_thread.start()

    def _preheat_xgoedu_on_gui_thread(self):
        """主线程预创建 XGOEDU 单例，并隐藏其顶层 QLabel、重新将 ai 窗口提到最上层。"""
        try:
            from edulib import XGOEDU
            edu = XGOEDU()
            try:
                if getattr(edu, "_label", None) is not None:
                    edu._label.hide()
            except Exception:
                pass
            print("[ai_chat] XGOEDU pre-warmed on GUI thread", flush=True)
        except Exception as e:
            print(f"[ai_chat] XGOEDU preheat error: {e}", flush=True)
        # ai 主窗口提到最上层，并重新贴 loading 首帧（避免被 XGOEDU 顶层 QLabel 瞬间遮住后不刷新）
        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        try:
            self._render_loading()
        except Exception:
            pass

    # ===== Layout Events =====

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 12

        # Display fills entire widget
        self.display.setGeometry(0, 0, w, h)

        # Status label centered near bottom
        self.status_label.adjustSize()
        self.status_label.move((w - self.status_label.width()) // 2, h - self.status_label.height() - pad)

        # Corners — 紧贴边缘
        self.corner_bl.adjustSize()
        self.corner_br.adjustSize()
        self.corner_bl.move(0, h - self.corner_bl.height())
        self.corner_br.move(w - self.corner_br.width(), h - self.corner_br.height())

        # 尺寸变化后重画 loading（避免先小后大闪烁）
        if self._is_loading:
            self._render_loading()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            print("[ai_chat] first paintEvent", flush=True)
        # 双保险：若 QTimer.singleShot 未起作用，首帧也会拉起 worker
        if self._init_thread is None:
            self._kick_init_worker()

    # ===== Key Events =====

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            # C key (bottom-left, KEY_BACK) → 退出
            print("[ai_chat] KEY_BACK (C) -> exit", flush=True)
            self.close()
            return
        # 启动未完成时忽略其他按键，避免误触发对话流程
        if self._is_loading:
            return
        if ev.key() == Qt.Key.Key_Enter or ev.key() == Qt.Key.Key_Return:
            # D key (bottom-right, KEY_ENTER):
            #   - 二维码页(IDLE) → 开始聊天
            #   - 聊天中 → 中断对话，回到二维码页
            if self.sm.is_idle():
                print("[ai_chat] KEY_ENTER (D) -> start chat", flush=True)
                self._start_conversation()
            else:
                print("[ai_chat] KEY_ENTER (D) -> back to config", flush=True)
                self._back_to_config()
        elif ev.key() == Qt.Key.Key_Left:
            # A key (top-left, KEY_LEFT) → 开始聊天
            print("[ai_chat] KEY_LEFT (A) -> start chat", flush=True)
            self._start_conversation()

    def closeEvent(self, ev):
        print("[ai_chat] closing", flush=True)
        self.running = False
        self._cleanup()
        super().closeEvent(ev)

    # ===== Display Methods =====

    def _on_display_update(self, pil_image):
        """Slot: update display QLabel from PIL Image (runs on GUI thread)"""
        try:
            pixmap = pil_to_qpixmap(pil_image)
            self.display.setPixmap(pixmap)
        except Exception as e:
            print(f"[UI] _on_display_update error: {e}")

    def _update_display(self, pil_image):
        """Thread-safe: emit signal to update display"""
        try:
            self._display_signal.emit(pil_image)
        except Exception as e:
            print(f"[UI] _update_display error: {e}")

    def _show_idle(self):
        """Show idle screen with QR code"""
        try:
            img = self.web_server.generate_idle_image(show_start_button=True)
            self._on_display_update(img)
            # 中间提示与下角 corner_bl/corner_br 重复，保持隐藏
            self.status_label.setText("")
            self.status_label.hide()
            # 显示底部按钮：C:退出 始终显示；D:开始 仅当配置完成时显示
            self._corner_visible_signal.emit(True)
        except Exception as e:
            print(f"[UI] _show_idle error: {e}")

    def _show_status(self, text, color=(102, 178, 255)):
        """Show status text on display"""
        try:
            img = self.web_server.generate_status_image(text, color)
            self._update_display(img)
        except Exception as e:
            print(f"[UI] _show_status error: {e}")

    def _show_listening_text(self, text):
        """Show ASR partial text on display"""
        try:
            img = self.web_server.generate_listening_text_image(text)
            self._update_display(img)
        except Exception as e:
            print(f"[UI] _show_listening_text error: {e}")

    # ===== Backend Initialization =====

    def _on_loading_tick(self):
        """动态小点动画，让用户感知不是卡死。"""
        self._loading_frame = (self._loading_frame + 1) % 4
        self._render_loading()

    def _render_loading(self):
        """用 PIL 画出 loading 页并整张贴到 self.display。不依赖子控件可见性/层叠。"""
        try:
            # 背景：优先用 app_bg 背景图，缺失时回落纯色
            if self._bg_pil is not None:
                img = self._bg_pil.copy()
            else:
                img = Image.new("RGB", (SCREEN_W, SCREEN_H), BG_COLOR)
            draw = ImageDraw.Draw(img)

            # 标题（启动中 ·/··/···/····）
            dots = "·" * (self._loading_frame + 1)
            title = _T("loading_title") + dots
            try:
                bbox = draw.textbbox((0, 0), title, font=self._loading_font_title)
                tw = bbox[2] - bbox[0]
            except Exception:
                tw = len(title) * 16
            draw.text(((SCREEN_W - tw) // 2, 95), title, font=self._loading_font_title, fill=(230, 236, 255))

            # 进度条
            bar_y = 138
            bar_w = 200
            bar_x = (SCREEN_W - bar_w) // 2
            draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + 4)], fill=(34, 40, 74))
            progress = (self._loading_frame + 1) * (bar_w // 4)
            if progress > 0:
                draw.rectangle([(bar_x, bar_y), (bar_x + progress, bar_y + 4)], fill=(24, 223, 107))

            # 副标题
            hint = _T("loading_hint")
            try:
                hbox = draw.textbbox((0, 0), hint, font=self._loading_font_hint)
                hw = hbox[2] - hbox[0]
            except Exception:
                hw = len(hint) * 8
            draw.text(((SCREEN_W - hw) // 2, 158), hint, font=self._loading_font_hint, fill=(136, 146, 201))

            self._on_display_update(img)
        except Exception as e:
            print(f"[Main] _render_loading error: {e}", flush=True)

    def _deferred_init(self):
        """已废弃：保留名字只为向后兼容，实际入口在 _init_backend_worker。"""
        self._init_backend_worker()

    def _init_backend_worker(self):
        """后台线程调用：走耗时后端初始化，完成后由主线程轮询标志切页。"""
        try:
            self._init_backend()
        except Exception as e:
            print(f"[Main] backend init error: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            print("[Main] backend worker finished, set ready flag", flush=True)
            self._backend_ready = True
            # 双保险：同时 emit 信号（若 Signal 可用则更快）
            try:
                self._init_done_signal.emit()
            except Exception:
                pass

    def _poll_backend_ready(self):
        """主线程轮询：后端就绪后切到待机页面。"""
        if self._backend_ready and self._is_loading:
            try:
                self._ready_poll_timer.stop()
            except Exception:
                pass
            self._on_init_done()

    def _on_init_done(self):
        """GUI 线程：后端就绪后关闭 loading 动画，若配置已完成则直接开始对话，否则显示二维码待机页面。"""
        if not self._is_loading:
            return  # 已被轮询/信号其中一路调过
        print("[Main] _on_init_done -> check config", flush=True)
        self._is_loading = False
        try:
            if self._loading_timer.isActive():
                self._loading_timer.stop()
        except Exception:
            pass
        try:
            if self._ready_poll_timer.isActive():
                self._ready_poll_timer.stop()
        except Exception:
            pass
        # 配置已完成 → 直接进入对话模式，跳过二维码配置页
        if self._is_config_ready():
            print("[Main] Config ready -> auto start conversation", flush=True)
            try:
                self._start_conversation()
            except Exception as e:
                print(f"[Main] auto start conversation error: {e}", flush=True)
                self._show_idle()
        else:
            print("[Main] Config not ready -> show idle (QR code)", flush=True)
            try:
                self._show_idle()
            except Exception as e:
                print(f"[Main] _show_idle after init error: {e}", flush=True)

    def _init_backend(self):
        """Initialize config, web server, and services"""
        print("[Main] Initializing backend...")

        # Config
        self.config = load_config()
        self.sm = StateMachine()
        self.sm.on_state_changed(self._on_state_changed)

        # Web config server
        self.web_server = ConfigWebServer(
            on_config_changed=self._on_config_changed,
            on_generate_prompt=self._generate_prompt
        )
        self.web_server.set_display_callback(self._update_display)

        # Emotion manager (with display callback for animation frames)
        self.emotion_mgr = EmotionManager(display_callback=self._update_display)

        # Tool manager
        self.tool_mgr = ToolManager()
        self.tool_mgr.set_photo_callback(self._take_photo_vlm)

        # Services (lazy init)
        self.asr = None
        self.llm = None
        self.tts = None
        self._picam = None

        # Start web server
        self.web_server.start()
        time.sleep(0.5)

        # Init services
        self._init_services()

        print("[Main] Backend ready!")

    def _init_services(self):
        """Initialize ASR / LLM / TTS from config"""
        cfg = self.config
        print("[Main] Initializing services...")

        # ASR
        try:
            self.asr = create_asr(cfg.get("asr", {}))
            print(f"[Main] ASR: {cfg.get('asr', {}).get('provider', 'aliyun')}")
        except Exception as e:
            print(f"[Main] ASR init error: {e}")

        # LLM
        try:
            tool_defs = self.tool_mgr.get_tool_definitions()
            self.llm = LLMManager(cfg.get("llm", {}), tool_definitions=tool_defs,
                                   role_config=cfg.get("role", {}), lang="cn")
            self.llm.set_tool_executor(self.tool_mgr.execute)
            mem_cfg = cfg.get("memory", {})
            self.llm.set_memory(mem_cfg.get("enabled", False), mem_cfg.get("content", ""))
            print(f"[Main] LLM: {cfg.get('llm', {}).get('model', 'unknown')}, memory={'on' if mem_cfg.get('enabled') else 'off'}")
        except Exception as e:
            print(f"[Main] LLM init error: {e}")

        # TTS
        try:
            self.tts = create_tts(cfg.get("tts", {}))
            print(f"[Main] TTS: {cfg.get('tts', {}).get('provider', 'aliyun')}")
        except Exception as e:
            print(f"[Main] TTS init error: {e}")

    # ===== Config Change Handler =====

    def _generate_prompt(self, requirements, agent_name="", user_nickname="", user_personality=""):
        """回调：调用 LLM 生成角色定义提示词"""
        if not self.llm:
            return {"ok": False, "prompt": "", "error": "LLM not initialized"}
        return self.llm.generate_system_prompt(requirements, agent_name=agent_name, user_nickname=user_nickname, user_personality=user_personality)

    def _on_config_changed(self, new_cfg):
        """Hot-reload when H5 page saves config"""
        print("[Main] Config changed, reloading...")
        self.config = new_cfg

        try:
            self.asr = create_asr(new_cfg.get("asr", {}))
        except Exception as e:
            print(f"[Main] ASR reload error: {e}")

        # LLM：若已存在则热更新；若先前未初始化则重建一次
        try:
            if self.llm:
                self.llm.reload_config(new_cfg.get("llm", {}), role_config=new_cfg.get("role", {}))
            else:
                tool_defs = self.tool_mgr.get_tool_definitions()
                self.llm = LLMManager(new_cfg.get("llm", {}), tool_definitions=tool_defs,
                                       role_config=new_cfg.get("role", {}), lang="cn")
                self.llm.set_tool_executor(self.tool_mgr.execute)
                print(f"[Main] LLM (re)initialized: {new_cfg.get('llm', {}).get('model', 'unknown')}")
            mem_cfg = new_cfg.get("memory", {})
            self.llm.set_memory(mem_cfg.get("enabled", False), mem_cfg.get("content", ""))
        except Exception as e:
            print(f"[Main] LLM reload error: {e}")

        try:
            if self.tts:
                self.tts.cleanup()
            self.tts = create_tts(new_cfg.get("tts", {}))
        except Exception as e:
            print(f"[Main] TTS reload error: {e}")

        # 配置变更后：若在 IDLE 状态且配置完成 → 自动开始对话
        # 注意：_on_config_changed 在 Flask 工作线程被回调，
        # 必须通过 Signal/Slot 切回 GUI 线程执行自动开始对话。
        self._refresh_idle_signal.emit()
        print("[Main] Config reload complete")

    # ===== State Machine Callback =====

    def _on_state_changed(self, old_state, new_state):
        """Update display based on state"""
        if new_state == State.IDLE:
            self.emotion_mgr.stop_expression()
            QTimer.singleShot(0, self._show_idle)
            # 回到待机：重新显示底部两个提示
            self._corner_visible_signal.emit(True)
        elif new_state == State.LISTENING:
            QTimer.singleShot(0, lambda: self.status_label.setText("Listening... Speak now"))
            self.emotion_mgr.play_expression("mic", fps=15, loop=True)
            # 聊天中：隐藏底部两个提示
            self._corner_visible_signal.emit(False)
        elif new_state == State.THINKING:
            QTimer.singleShot(0, lambda: self.status_label.setText("Thinking..."))
            self.emotion_mgr.play_expression("think", fps=15, loop=True)
            self._corner_visible_signal.emit(False)
        elif new_state == State.SPEAKING:
            QTimer.singleShot(0, lambda: self.status_label.setText("Speaking..."))
            self._corner_visible_signal.emit(False)

    def _on_corner_visible(self, visible: bool):
        """Slot: show/hide bottom corner hints on GUI thread"""
        try:
            if visible:
                # 退出按钮永远显示
                self.corner_bl.show()
                self.corner_bl.raise_()
                # 开始按钮：仅当配置完成时显示，文案为"开始"
                if self._is_config_ready():
                    self._br_text_label.setText(_T("corner_start"))
                    self.corner_br.show()
                    self.corner_br.raise_()
                else:
                    self.corner_br.hide()
            else:
                self.corner_bl.hide()
                self.corner_br.hide()
            # 触发刷新，确保 linuxfb 同步
            self.update()
        except Exception as e:
            print(f"[UI] _on_corner_visible error: {e}")

    def _is_config_ready(self) -> bool:
        """检查 ASR/LLM/TTS/Role 是否全部就绪"""
        try:
            from web_server import is_config_complete
            return is_config_complete(self.config)
        except Exception:
            return False

    def _on_refresh_idle(self):
        """Slot: GUI 线程刷新空闲界面（配置保存热更新后调用）。
        若配置已完成且处于 IDLE 状态 → 自动开始对话；否则刷新二维码页面。"""
        try:
            if self.sm.is_idle():
                if self._is_config_ready():
                    # 配置完成 → 自动进入对话模式
                    print("[Main] Config complete while idle -> auto start conversation", flush=True)
                    self._start_conversation()
                else:
                    self._show_idle()
            else:
                # 非 IDLE（对话中）：配置热重载已生效，不中断当前对话
                pass
        except Exception as e:
            print(f"[UI] _on_refresh_idle error: {e}")

    def _hide_corner_hints(self):
        self._corner_visible_signal.emit(False)

    def _show_corner_hints(self):
        self._corner_visible_signal.emit(True)

    # ===== D Key: Back to Config =====

    def _back_to_config(self):
        """中断当前对话，切回二维码配置页面。
        - 设置 _conversation_cancelled 让对话线程尽快退出
        - 中止 ASR 录音（若有）
        - 重置状态机到 IDLE
        - 显示二维码待机画面（供用户重新配置）
        """
        # 1. 标记取消对话（对话线程在每轮循环开头检查此标志）
        self._conversation_cancelled = True

        # 2. 中止正在进行的 ASR 录音（最多 ~100ms 内退出录音循环）
        if self.asr:
            try:
                self.asr._abort = True
            except Exception:
                pass

        # 3. 重置状态机到 IDLE（触发 _on_state_changed 显示 IDLE 界面）
        try:
            self.sm.set_state(State.IDLE)
        except Exception:
            pass

        # 4. 直接显示二维码配置页面（覆盖对话画面）
        try:
            self._show_idle()
        except Exception as e:
            print(f"[Main] _back_to_config show_idle error: {e}", flush=True)

        print("[Main] Back to config mode (QR screen)", flush=True)

    # ===== Conversation Flow =====

    def _start_conversation(self):
        """Start conversation in background thread"""
        if not self.sm.is_idle() or not self.running:
            return
        # 配置未完成时禁止开始对话
        if not self._is_config_ready():
            print("[Main] Config not ready, ignore start conversation")
            return

        # 重置取消标志（上次按 D 回配置可能遗留 True）
        self._conversation_cancelled = False
        print("[Main] Starting conversation...")
        play_ding()
        threading.Thread(target=self._conversation_flow, daemon=True).start()

    def _conversation_flow(self):
        """Multi-turn conversation: ASR -> LLM -> TTS -> loop until silence"""
        MAX_SILENT_ROUNDS = 2
        silent_count = 0

        try:
            while self.running and not self._conversation_cancelled:
                # === 1. ASR: Record and recognize ===
                self.sm.set_state(State.LISTENING)
                if not self.asr:
                    print("[Main] ASR not configured")
                    break

                self._partial_text = ""

                def on_partial(text):
                    if self.asr.partial_mode == "cumulative":
                        self._partial_text = text
                    else:
                        self._partial_text += text
                    self._show_listening_text(self._partial_text)

                self.asr.on_partial = on_partial
                user_text = self.asr.start_recording(max_duration=15)
                print(f"[Main] ASR result: '{user_text}'")

                # ASR 录音期间可能被长按 C 取消，立即退出对话
                if self._conversation_cancelled:
                    print("[Main] Conversation cancelled during ASR", flush=True)
                    break

                if not user_text or not user_text.strip():
                    silent_count += 1
                    print(f"[Main] No speech detected ({silent_count}/{MAX_SILENT_ROUNDS})")
                    if silent_count >= MAX_SILENT_ROUNDS:
                        print("[Main] Too many silent rounds, ending conversation")
                        break
                    play_ding()
                    time.sleep(0.5)
                    continue

                silent_count = 0

                # === 2. LLM: Streaming response with Function Call ===
                self.sm.set_state(State.THINKING)
                if not self.llm:
                    print("[Main] LLM not configured")
                    break

                sentences = []
                tts_lock = threading.Lock()
                tts_done = threading.Event()
                use_session = self.tts.supports_session if self.tts else False

                def on_sentence(sentence):
                    with tts_lock:
                        sentences.append(sentence)

                splitter = StreamSentenceSplitter(on_sentence)
                full_response = []
                detected_emotion = [None]
                emotion_extracted = [False]

                def on_token(token):
                    if "__VLM_PHOTO__" in token:
                        return
                    if not emotion_extracted[0]:
                        stripped = token.lstrip()
                        if stripped and stripped[0] in EMOTION_NUM_MAP:
                            detected_emotion[0] = EMOTION_NUM_MAP[stripped[0]]
                            token = stripped[1:]
                            emotion_extracted[0] = True
                            print(f"[Main] Emotion prefix detected: {detected_emotion[0]}")
                            if not token:
                                return
                        elif stripped:
                            emotion_extracted[0] = True
                    splitter.feed(token)
                    full_response.append(token)

                def on_tool_call(name, args):
                    print(f"[Main] Tool called: {name} {args}")

                # Start TTS session
                session_ok = False
                if use_session and self.tts:
                    session_ok = self.tts.start_session()

                # TTS consumer thread
                tts_stop = threading.Event()

                def tts_consumer():
                    first_sentence = True
                    while not tts_stop.is_set():
                        sentence = None
                        with tts_lock:
                            if sentences:
                                sentence = sentences.pop(0)
                        if sentence:
                            if first_sentence:
                                self.sm.set_state(State.SPEAKING)
                                if detected_emotion[0]:
                                    self.emotion_mgr.play_expression(detected_emotion[0])
                                else:
                                    self.emotion_mgr.play_for_text(sentence)
                                first_sentence = False
                            if self.tts:
                                try:
                                    if use_session and session_ok:
                                        self.tts.send_text(sentence)
                                    else:
                                        self.tts.speak_sentence(sentence)
                                except Exception as e:
                                    print(f"[Main] TTS error: {e}")
                        else:
                            time.sleep(0.05)
                    # Process remaining
                    with tts_lock:
                        remaining = list(sentences)
                        sentences.clear()
                    for s in remaining:
                        if self.tts:
                            try:
                                if use_session and session_ok:
                                    self.tts.send_text(s)
                                else:
                                    self.tts.speak_sentence(s)
                            except Exception:
                                pass
                    tts_done.set()

                tts_thread = threading.Thread(target=tts_consumer, daemon=True)
                tts_thread.start()

                # Run LLM streaming
                try:
                    result = self.llm.chat_stream(
                        user_text,
                        on_token=on_token,
                        on_tool_call=on_tool_call
                    )
                except Exception as e:
                    print(f"[Main] LLM error: {e}")
                    result = ""

                # Handle VLM photo in result
                if result and "__VLM_PHOTO__" in result:
                    parts = result.split("__VLM_PHOTO__|")
                    for part in parts[1:]:
                        try:
                            prompt, img_b64 = part.split("|", 1)
                            vlm_result = self._do_vlm_chat(prompt, img_b64)
                            if vlm_result:
                                for char in vlm_result:
                                    splitter.feed(char)
                        except Exception as e:
                            print(f"[Main] VLM processing error: {e}")

                splitter.flush()
                time.sleep(0.3)

                # Signal TTS consumer
                tts_stop.set()
                tts_done.wait(timeout=60)

                # Finish session
                if use_session and session_ok and self.tts:
                    try:
                        self.tts.finish_session()
                    except Exception as e:
                        print(f"[Main] TTS finish_session error: {e}")

                # Emotion at end
                full_text = "".join(full_response)
                if full_text:
                    if detected_emotion[0]:
                        self.emotion_mgr.play_expression(detected_emotion[0], loop=False)
                    else:
                        self.emotion_mgr.play_for_text(full_text, loop=False)
                    time.sleep(0.5)

                self.emotion_mgr.stop_expression()

                # Async memory update
                self._async_update_memory()

                print("[Main] Round complete, listening for next input...")
                play_ding()
                time.sleep(0.8)

        except Exception as e:
            print(f"[Main] Conversation error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.emotion_mgr.stop_expression()
            self.sm.set_state(State.IDLE)
            QTimer.singleShot(0, self._show_idle)
            print("[Main] Conversation ended, back to idle")

    # ===== Memory =====

    def _async_update_memory(self):
        """异步更新长期记忆"""
        mem_cfg = self.config.get("memory", {})
        if not mem_cfg.get("enabled", False) or not self.llm:
            return
        current_memory = mem_cfg.get("content", "")
        messages_snapshot = list(self.llm.messages)

        def _do_update():
            try:
                print("[Main] Updating long-term memory (async)...")
                new_memory = self.llm.update_memory(current_memory)
                if new_memory and new_memory != current_memory:
                    self.config.setdefault("memory", {})["content"] = new_memory
                    save_config(self.config)
                    self.llm.set_memory(True, new_memory)
                    print(f"[Main] Memory saved ({len(new_memory)} chars)")
                else:
                    print("[Main] Memory unchanged")
            except Exception as e:
                print(f"[Main] Memory update error: {e}")

        threading.Thread(target=_do_update, daemon=True).start()

    # ===== VLM Photo =====

    def _take_photo_vlm(self, prompt):
        """Photo callback for tools.py"""
        try:
            import cv2
            from picamera2 import Picamera2

            photo_path = "/tmp/ai_chat_photo.jpg"
            picam = Picamera2()
            picam.configure(picam.create_preview_configuration(
                main={"format": "RGB888", "size": (640, 480)}
            ))
            picam.start()
            time.sleep(0.5)
            # 注意：Picamera2 在树莓派上 format="RGB888" 实际输出的是 BGR 排列
            image_bgr = picam.capture_array()
            # cv2.imwrite 期望 BGR 输入，直接写
            cv2.imwrite(photo_path, image_bgr)
            picam.stop()
            picam.close()

            # 预览照片：必须先停掉 think 表情，否则 emotion_mgr 后台 15fps 会立刻覆盖
            try:
                from PIL import Image as PILImage
                try:
                    self.emotion_mgr.stop_expression()
                except Exception:
                    pass
                # 给停止动作一点缓冲，避免残余帧覆盖照片
                time.sleep(0.05)
                # PIL 需要 RGB，转换后再交给显示层
                image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
                photo_img = PILImage.fromarray(image_rgb).resize((320, 240))
                self._update_display(photo_img)
                # 停留 2 秒让用户看清照片
                time.sleep(2.0)
            except Exception as e:
                print(f"[Main] Photo preview error: {e}")

            with open(photo_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")
            return self._do_vlm_chat(prompt, img_b64)
        except Exception as e:
            return f"Camera error: {e}"

    def _do_vlm_chat(self, prompt, img_b64):
        """VLM chat using LLM's vision capability"""
        if not self.llm:
            return "LLM not configured"
        try:
            return self.llm.vlm_describe(prompt, img_b64)
        except Exception as e:
            return f"VLM error: {e}"

    # ===== Cleanup =====

    def _cleanup(self):
        """Clean up all resources"""
        print("[Main] Cleaning up...")
        self.running = False
        self.emotion_mgr.stop_expression()
        if self.tts:
            try:
                self.tts.cleanup()
            except Exception:
                pass
        self.web_server.stop()

        # 关闭 XGOEDU 隐藏顶层窗口并释放 fb 句柄，
        # 否则它会阻止 quitOnLastWindowClosed 生效，导致 app.exec() 不返回
        try:
            from robot_tools import get_xgo_edu
            edu = get_xgo_edu()
            if edu is not None:
                lbl = getattr(edu, "_label", None)
                if lbl is not None:
                    try:
                        lbl.hide()
                        lbl.close()
                        lbl.deleteLater()
                    except Exception:
                        pass
                fb_fd = getattr(edu, "_fb_fd", None)
                if fb_fd is not None:
                    try:
                        os.close(fb_fd)
                    except Exception:
                        pass
                    edu._fb_fd = None
        except Exception as e:
            print(f"[Main] XGOEDU cleanup error: {e}")

        print("[Main] Cleanup done. Goodbye!")

        # 强制退出事件循环，防止残留隐藏顶层窗口/守护线程阻止 app.exec() 返回
        try:
            qapp = QApplication.instance()
            if qapp is not None:
                QTimer.singleShot(0, qapp.quit)
        except Exception:
            pass


# ===== Entry Point =====


def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)

    w = AIChatPage()
    w.showFullScreen()

    rc = app.exec()
    print(f"[ai_chat] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
