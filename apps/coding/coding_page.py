"""Luwu OS - Coding 主界面 Widget（AppFrame + CodingPage）。"""
import sys
import os
import time
import threading

# 确保能找到 luwu-os 全局库
LUWU_ROOT = "/home/pi/luwu-os"
if LUWU_ROOT not in sys.path:
    sys.path.insert(0, LUWU_ROOT)

from PIL import Image, ImageDraw, ImageFont

from PySide6.QtCore import Qt, QTimer, QSocketNotifier
from PySide6.QtGui import QKeyEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from libs.theme import (
    apply_app_palette,
    qss as T_qss,
    Color as T_Color,
    ColorRGB as T_RGB,
    Asset as T_Asset,
)
from libs.ui import AppFrame as _BaseAppFrame

from config import (
    APP_DIR, PICS_DIR, KEYS_FIFO, BLOCKLY_PORT,
    _CODING_BG_IMAGE, FONT_PATH, LOCK_JSON_PATH,
    PAGE_LOADING, PAGE_MAIN, PAGE_FILE_LIST,
    PAGE_UPGRADE, PAGE_UPGRADE_DONE, PAGE_UPGRADE_PROMPT,
    t, get_local_ip, port_in_use, kill_blockly_service,
)
from managers import (
    BlocklyServiceManager, ProgramRunner, UpgradeManager, FileListManager,
)


# ========================================================================
# AppFrame 子类（coding 专属背景）
# ========================================================================
class AppFrame(_BaseAppFrame):
    """coding 子应用根容器：使用专属背景图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        pix = QPixmap(_CODING_BG_IMAGE)
        if not pix.isNull():
            self._bg_pix = pix
            self.update()


# ========================================================================
# 主界面 Widget
# ========================================================================
class CodingPage(AppFrame):
    """图形编程主界面。"""

    def __init__(self):
        super().__init__()
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
        self.display_label.setStyleSheet(f"background-color: rgb{T_RGB.bg_solid};")
        self.display_label.lower()  # 让 AppFrame 的角标 widget 浮在上面
        self.display_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # --- 加密锁按钮区域（PIL 坐标，用于点击检测）---
        self._lock_btn_rect = None  # (x1, y1, x2, y2) 或 None
        self._upgrade_banner_rect = None  # 升级横幅点击区域
        self._last_upgrade_status = None  # 去重：上次渲染时的升级状态

        # --- 管理器 ---
        self.service_manager = BlocklyServiceManager()
        self.file_list_manager = FileListManager()
        self.program_runner = ProgramRunner()

        # --- 升级管理器 ---
        self.upgrade_manager = UpgradeManager()
        self.upgrade_manager.set_service_manager(self.service_manager)

        # --- 本地 IP ---
        self.local_ip = get_local_ip()
        print(f"[coding] Local IP: {self.local_ip}", flush=True)

        # --- Keys FIFO ---
        self._keys_fd = -1
        self._keys_notifier = None
        self._setup_keys_fifo()

        # --- 字体加载（用于 PIL 渲染） ---
        self._font26 = None
        self._font22 = None
        self._font16 = None
        self._font14 = None
        self._font12 = None
        self._font10 = None
        try:
            self._font26 = ImageFont.truetype(FONT_PATH, 26)
            self._font22 = ImageFont.truetype(FONT_PATH, 22)
            self._font16 = ImageFont.truetype(FONT_PATH, 16)
            self._font14 = ImageFont.truetype(FONT_PATH, 14)
            self._font12 = ImageFont.truetype(FONT_PATH, 12)
            self._font10 = ImageFont.truetype(FONT_PATH, 10)
        except Exception:
            self._font26 = ImageFont.load_default()
            self._font22 = ImageFont.load_default()
            self._font16 = ImageFont.load_default()
            self._font14 = ImageFont.load_default()
            self._font12 = ImageFont.load_default()
            self._font10 = ImageFont.load_default()

        # --- 图标预加载 ---
        self._icon_ai = None
        self._icon_blockly = None
        self._load_icons()

        # --- coding 专属桌面背景图（PIL 画布底） ---
        self._bg_pil = None
        try:
            if os.path.exists(_CODING_BG_IMAGE):
                self._bg_pil = Image.open(_CODING_BG_IMAGE).convert("RGB").resize((320, 240))
            elif os.path.exists(T_Asset.bg_image):
                self._bg_pil = Image.open(T_Asset.bg_image).convert("RGB").resize((320, 240))
        except Exception as e:
            print(f"[coding] bg image load error: {e}", flush=True)

        # --- 加密状态检查 ---
        self._is_encrypted = os.path.exists(LOCK_JSON_PATH)

        # --- 启动就绪标志（服务 + 版本检查都完成后才离开 loading）---
        self._service_ready = False
        self._loading_start_time = time.time()  # 用于超时兜底

        # --- 启动 loading 动画（每 400ms 刷新一次）---
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._animate_loading)
        self._loading_timer.start(400)

        # --- 提前启动状态轮询（监听服务+版本检查完成，进入主页/升级提示）---
        self._check_timer = QTimer(self)
        self._check_timer.timeout.connect(self._check_status)
        self._check_timer.start(300)

        # 先应用一次角标（LOADING 页只亮 C 返回）
        self._update_corner_labels()

        # --- 延迟启动服务
        QTimer.singleShot(200, self._start_service)
        # 同时启动版本检查（后台线程，与 Flask 启动并行）
        QTimer.singleShot(200, self.upgrade_manager.start_check)

    # ====================================================================
    # Loading 动画 & 图片加载 & 服务启动
    # ====================================================================
    def _animate_loading(self):
        """loading 动画：循环 ... 动画帧。"""
        self._loading_frame = (self._loading_frame + 1) % 4
        self._render_and_display()

    def _load_icons(self):
        ai_path = os.path.join(PICS_DIR, "icon_ai.png")
        blockly_path = os.path.join(PICS_DIR, "icon_blockly.png")
        right_path = T_Asset.icon_right  # 右上角角标图标

        try:
            if os.path.exists(ai_path):
                self._icon_ai = Image.open(ai_path).resize((60, 60))
            if os.path.exists(blockly_path):
                self._icon_blockly = Image.open(blockly_path).resize((60, 60))
            # 加载角标 right 图标（18x18，右上角关闭加密旁）
            self._icon_right_small = None
            if right_path and os.path.exists(right_path):
                self._icon_right_small = Image.open(right_path).resize((18, 18))
        except Exception as e:
            print(f"[coding] icon load error: {e}", flush=True)
            self._icon_right_small = None

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

        # 轮询等待服务端口就绪
        self._wait_service_ready()

    def _wait_service_ready(self):
        """每 200ms 检查一次端口，就绪后立即标记。"""
        if port_in_use(BLOCKLY_PORT):
            self._on_service_ready()
        else:
            QTimer.singleShot(200, self._wait_service_ready)

    def _on_service_ready(self):
        """服务启动完成后的回调。标记就绪，不直接跳转（等版本检查结束）。"""
        self._service_ready = True
        print("[coding] service ready, waiting for version check...", flush=True)
        self._try_leave_loading()

    def _try_leave_loading(self):
        """当服务就绪 + 版本检查完成（或超时）→ 离开 loading 进入主页或升级提示。"""
        if self.current_page != PAGE_LOADING:
            return
        if not self._service_ready:
            return
        um = self.upgrade_manager
        # 版本检查还在跑 → 继续等（最长等 15 秒，超时直接进主页）
        if um.status == um.STATUS_CHECKING:
            if time.time() - self._loading_start_time < 15:
                return
            # 超时兜底
            print("[coding] version check timeout, entering main page", flush=True)
        # 可以离开了
        if self._loading_timer:
            self._loading_timer.stop()
            self._loading_timer = None
        if um.status == um.STATUS_AVAILABLE:
            self.current_page = PAGE_UPGRADE_PROMPT
            print("[coding] update available, showing prompt", flush=True)
        else:
            self.current_page = PAGE_MAIN
            print("[coding] entering main page", flush=True)
        self._page_needs_redraw = True
        self._render_and_display()
        self._update_corner_labels()

    # ====================================================================
    # 状态轮询
    # ====================================================================
    def _check_status(self):
        """定期检查服务和程序状态。"""
        # === loading 阶段：轮询服务就绪 + 版本检查完成 ===
        if self.current_page == PAGE_LOADING:
            if not self._service_ready and port_in_use(BLOCKLY_PORT):
                self._service_ready = True
            self._try_leave_loading()
            return

        # 检查程序是否意外退出
        if (self.program_runner.process is not None and
                self.program_runner.process.poll() is not None):
            print("[coding] program exited unexpectedly", flush=True)
            self.program_runner.is_running = False
            if self.current_page == PAGE_FILE_LIST:
                self._update_corner_labels()
                self._page_needs_redraw = True
                self._render_and_display()

        # 检查升级状态变化 → 更新 UI
        um = self.upgrade_manager
        if um.status != self._last_upgrade_status:
            self._last_upgrade_status = um.status
            if um.status in (um.STATUS_AVAILABLE, um.STATUS_NO_UPDATE, um.STATUS_FAILED):
                if self.current_page == PAGE_MAIN:
                    self._page_needs_redraw = True
                    self._render_and_display()
        if self.current_page == PAGE_UPGRADE:
            self._page_needs_redraw = True
            self._render_and_display()
            if um.status in (um.STATUS_SUCCESS, um.STATUS_FAILED):
                self.current_page = PAGE_UPGRADE_DONE
                self._page_needs_redraw = True
                self._render_and_display()
                self._update_corner_labels()

    # ====================================================================
    # Keys FIFO
    # ====================================================================
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

    # ====================================================================
    # 鼠标 / 触摸点击
    # ====================================================================
    def mousePressEvent(self, ev):
        """处理触摸/鼠标点击：点击「关闭加密」删除 lock.json。"""
        if self.current_page == PAGE_MAIN:
            x, y = ev.position().x(), ev.position().y()
            if self._lock_btn_rect is not None:
                rx1, ry1, rx2, ry2 = self._lock_btn_rect
                if rx1 <= x <= rx2 and ry1 <= y <= ry2:
                    self._disable_encryption()
                    return
        super().mousePressEvent(ev)

    def _disable_encryption(self):
        """删除 lock.json 以关闭加密保护。"""
        try:
            os.remove(LOCK_JSON_PATH)
            self._is_encrypted = False
            self._lock_btn_rect = None
            self._page_needs_redraw = True
            self._render_and_display()
            print("[coding] encryption disabled, lock.json removed", flush=True)
        except FileNotFoundError:
            self._is_encrypted = False
            self._lock_btn_rect = None
            self._page_needs_redraw = True
            self._render_and_display()
            print("[coding] lock.json already removed", flush=True)
        except Exception as e:
            print(f"[coding] failed to remove lock.json: {e}", flush=True)

    # ====================================================================
    # 按键处理
    # ====================================================================
    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()
        print(f"[coding] key: {key} page={self.current_page}", flush=True)

        if self.current_page == PAGE_LOADING:
            self._handle_loading_keys(key)
        elif self.current_page == PAGE_MAIN:
            self._handle_main_keys(key)
        elif self.current_page == PAGE_FILE_LIST:
            self._handle_filelist_keys(key)
        elif self.current_page == PAGE_UPGRADE_PROMPT:
            self._handle_upgrade_prompt_keys(key)
        elif self.current_page == PAGE_UPGRADE:
            self._handle_upgrade_keys(key)
        elif self.current_page == PAGE_UPGRADE_DONE:
            self._handle_upgrade_done_keys(key)

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
                print("[coding] D pressed → stop program", flush=True)
                self.program_runner.stop()
            else:
                print(f"[coding] D pressed → run: {selected}", flush=True)
                self.program_runner.run(selected)
            self._update_corner_labels()
            self._page_needs_redraw = True
            self._render_and_display()
        elif key == Qt.Key.Key_Back:  # C → 返回主页
            print("[coding] C pressed → back to main", flush=True)
            self.current_page = PAGE_MAIN
            self._page_needs_redraw = True
            self._render_and_display()
            self._update_corner_labels()

    def _handle_upgrade_prompt_keys(self, key):
        """升级确认对话框的按键处理。"""
        if key == Qt.Key.Key_Back:  # C（左下）→ 取消升级，进入主页
            print("[coding] upgrade prompt → cancel, entering main", flush=True)
            self.current_page = PAGE_MAIN
            self._page_needs_redraw = True
            self._render_and_display()
            self._update_corner_labels()
        elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D（右下）→ 确认升级
            print("[coding] upgrade prompt → confirm, starting upgrade", flush=True)
            self.current_page = PAGE_UPGRADE
            self._page_needs_redraw = True
            self._render_and_display()
            self._update_corner_labels()
            self.upgrade_manager.start_upgrade()

    def _do_exit(self):
        """退出应用。"""
        if self.program_runner.check_alive():
            self.program_runner.stop()
        self.service_manager.stop()
        self.close()

    def _handle_upgrade_keys(self, key):
        """升级进行中的按键处理。"""
        um = self.upgrade_manager
        if key == Qt.Key.Key_Back:  # C → 取消
            if um.status in (um.STATUS_IDLE, um.STATUS_CHECKING, um.STATUS_AVAILABLE):
                print("[coding] C pressed → cancel upgrade", flush=True)
                self.current_page = PAGE_MAIN
                self._page_needs_redraw = True
                self._render_and_display()
                self._update_corner_labels()

    def _handle_upgrade_done_keys(self, key):
        """升级结果页的按键处理。"""
        if key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D → 重试
            um = self.upgrade_manager
            if um.status == um.STATUS_FAILED:
                print("[coding] D pressed → retry upgrade", flush=True)
                self.current_page = PAGE_UPGRADE
                self._page_needs_redraw = True
                self._render_and_display()
                self._update_corner_labels()
                um.start_upgrade()
        elif key == Qt.Key.Key_Back:  # C → 返回主页
            print("[coding] C pressed → back to main", flush=True)
            self.current_page = PAGE_MAIN
            self._page_needs_redraw = True
            self._render_and_display()
            self._update_corner_labels()

    # ====================================================================
    # 四角标签
    # ====================================================================
    def _update_corner_labels(self):
        if self.current_page == PAGE_MAIN:
            self.setCornerHints(
                tl="",
                tr="",
                bl=("", T_Asset.icon_back),
                br=(t("program_list"), T_Asset.icon_enter),
            )
        elif self.current_page == PAGE_FILE_LIST:
            running = self.program_runner.check_alive()
            d_text = t("d_stop") if running else t("d_run")
            self.setCornerHints(
                tl=(t("a_up"), T_Asset.icon_left),
                tr=(t("b_down"), T_Asset.icon_right),
                bl=(t("c_back"), T_Asset.icon_back),
                br=(d_text, T_Asset.icon_enter),
            )
        elif self.current_page == PAGE_LOADING:
            self.setCornerHints(
                tl="", tr="", br="",
                bl=(t("c_back"), T_Asset.icon_back),
            )
        elif self.current_page == PAGE_UPGRADE_PROMPT:
            self.setCornerHints(
                tl="", tr="",
                bl=(t("upgrade_prompt_cancel"), T_Asset.icon_back),
                br=(t("upgrade_prompt_confirm"), T_Asset.icon_enter),
            )
        elif self.current_page == PAGE_UPGRADE:
            um = self.upgrade_manager
            cancel_ok = um.status in (um.STATUS_IDLE, um.STATUS_CHECKING, um.STATUS_AVAILABLE)
            self.setCornerHints(
                tl="", tr="", br="",
                bl=(t("c_back") if cancel_ok else "", T_Asset.icon_back if cancel_ok else ""),
            )
        elif self.current_page == PAGE_UPGRADE_DONE:
            um = self.upgrade_manager
            if um.status == um.STATUS_FAILED:
                self.setCornerHints(
                    tl="", tr="",
                    bl=(t("upgrade_back"), T_Asset.icon_back),
                    br=(t("upgrade_retry"), T_Asset.icon_enter),
                )
            else:
                self.setCornerHints(
                    tl="", tr="", br="",
                    bl=(t("upgrade_back"), T_Asset.icon_back),
                )

    # ====================================================================
    # PIL 渲染框架
    # ====================================================================
    def _render_and_display(self):
        """使用 PIL 渲染当前页面，转换为 QPixmap 显示。"""
        if self._bg_pil is not None:
            bg = self._bg_pil.copy()
        else:
            bg = Image.new("RGB", (320, 240), T_RGB.bg_solid)
        draw = ImageDraw.Draw(bg)

        if self.current_page == PAGE_LOADING:
            self._render_loading_page(draw, bg)
        elif self.current_page == PAGE_MAIN:
            self._render_main_page(draw, bg)
        elif self.current_page == PAGE_FILE_LIST:
            self._render_file_list(draw, bg)
        elif self.current_page == PAGE_UPGRADE_PROMPT:
            self._render_upgrade_prompt(draw, bg)
        elif self.current_page == PAGE_UPGRADE:
            self._render_upgrade_page(draw, bg)
        elif self.current_page == PAGE_UPGRADE_DONE:
            self._render_upgrade_result(draw, bg)

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

    # ====================================================================
    # 页面渲染：Loading
    # ====================================================================
    def _render_loading_page(self, draw, bg):
        """渲染加载页面：图标 + 启动中 + 动画点。"""
        bar_y = 130
        bar_h = 4
        bar_max_w = 200
        bar_x = (320 - bar_max_w) // 2
        draw.rectangle(
            [(bar_x, bar_y), (bar_x + bar_max_w, bar_y + bar_h)],
            fill=T_RGB.bg_track,
        )
        progress = (self._loading_frame + 1) * (bar_max_w // 4)
        if progress > 0:
            draw.rectangle(
                [(bar_x, bar_y), (bar_x + progress, bar_y + bar_h)],
                fill=T_RGB.accent,
            )

        self._paste_icon(bg, self._icon_ai, (170, 25))
        self._paste_icon(bg, self._icon_blockly, (90, 25))

        title = t("main_title")
        tw = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw) // 2, 100), title, font=self._font16, fill=T_RGB.text_primary)

        dots = "." * (self._loading_frame + 1)
        loading_text = t("loading") + dots
        lw = draw.textbbox((0, 0), loading_text, font=self._font14)[2]
        draw.text(((320 - lw) // 2, 145), loading_text, font=self._font14, fill=T_RGB.accent)

        hint = t("loading_hint")
        hw = draw.textbbox((0, 0), hint, font=self._font12)[2]
        draw.text(((320 - hw) // 2, 170), hint, font=self._font12, fill=T_RGB.text_muted)

    # ====================================================================
    # 页面渲染：主页
    # ====================================================================
    def _render_main_page(self, draw, bg):
        """渲染主页面：图标 + IP:port + 加密状态。"""
        title = t("main_title")
        tw2 = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw2) // 2, 14), title, font=self._font16, fill=T_RGB.text_primary)

        line_w = 60
        draw.rectangle(
            [((320 - line_w) // 2, 38), ((320 + line_w) // 2, 40)],
            fill=T_RGB.accent,
        )

        self._paste_icon(bg, self._icon_ai, (170, 56))
        self._paste_icon(bg, self._icon_blockly, (90, 56))

        # IP 卡片
        card_x, card_y, card_w, card_h = 30, 130, 260, 56
        draw.rounded_rectangle(
            [(card_x, card_y), (card_x + card_w, card_y + card_h)],
            radius=10,
            fill=T_RGB.bg_card,
            outline=T_RGB.bg_track,
            width=1,
        )
        ip_text = self.local_ip if BLOCKLY_PORT == 80 else f"{self.local_ip}:{BLOCKLY_PORT}"
        tw = draw.textbbox((0, 0), ip_text, font=self._font26)[2]
        draw.text(
            (card_x + (card_w - tw) // 2, card_y + (card_h - 26) // 2),
            ip_text, font=self._font26, fill=T_RGB.accent,
        )

        # 版本信息
        um = self.upgrade_manager
        version_text = f"{t('version_label')}: {um.current_version}" if um.current_version else ""
        if version_text:
            vw = draw.textbbox((0, 0), version_text, font=self._font10)[2]
            draw.text(((320 - vw) // 2, 225), version_text, font=self._font10, fill=T_RGB.text_primary)

        # 加密保护状态
        self._lock_btn_rect = None
        hint_text = t("browser_hint")
        hw = draw.textbbox((0, 0), hint_text, font=self._font14)[2]
        if self._is_encrypted:
            btn_text = t("disable_encryption")
            tb = draw.textbbox((0, 0), btn_text, font=self._font12)
            bw = tb[2] - tb[0]
            th = tb[3] - tb[1]
            btn_pad_h = 4
            btn_h = 20
            btn_w = bw + btn_pad_h * 2
            btn_y = 4
            icon_w = 18
            icon_h = 18
            btn_x = 320 - icon_w - btn_w
            btn_rect = (btn_x, btn_y, btn_x + btn_w, btn_y + btn_h)
            draw.rounded_rectangle(btn_rect, radius=4, fill=(211, 69, 61), outline=(211, 69, 61))
            text_y = btn_y + (btn_h - th) // 2 - tb[1]
            draw.text((btn_x + btn_pad_h, text_y), btn_text, font=self._font12, fill=T_RGB.text_invert)
            if self._icon_right_small is not None:
                icon_x = 320 - icon_w
                icon_y = btn_y + (btn_h - icon_h) // 2
                self._paste_icon(bg, self._icon_right_small, (icon_x, icon_y))
            self._lock_btn_rect = btn_rect
            draw.text(((320 - hw) // 2, 202), hint_text, font=self._font14, fill=T_RGB.text_primary)
        else:
            draw.text(((320 - hw) // 2, 202), hint_text, font=self._font14, fill=T_RGB.text_primary)

    # ====================================================================
    # 页面渲染：文件列表
    # ====================================================================
    def _render_file_list(self, draw, bg):
        """渲染文件列表页面。"""
        title = t("program_list")
        tw = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw) // 2, 8), title, font=self._font16, fill=T_RGB.text_primary)

        line_w = 50
        draw.rectangle(
            [((320 - line_w) // 2, 30), ((320 + line_w) // 2, 32)],
            fill=T_RGB.accent,
        )

        fm = self.file_list_manager

        if not fm.files:
            no_text = t("no_program")
            nw = draw.textbbox((0, 0), no_text, font=self._font14)[2]
            draw.text(((320 - nw) // 2, 110), no_text, font=self._font14, fill=T_RGB.text_muted)
            return

        start_y = 42
        item_h = 28
        visible = fm.visible_count

        for i in range(fm.scroll_offset, min(fm.scroll_offset + visible, len(fm.files))):
            rel = i - fm.scroll_offset
            y = start_y + rel * item_h
            is_sel = (i == fm.selected_index)

            if is_sel:
                draw.rounded_rectangle(
                    [(8, y), (312, y + item_h - 4)],
                    radius=6, fill=T_RGB.accent,
                )
                text_color = T_RGB.text_invert
            else:
                text_color = T_RGB.text_primary

            filename = fm.files[i]
            display_name = filename[:-3] if filename.endswith(".py") else filename
            if len(display_name) > 16:
                display_name = display_name[:13] + "..."

            draw.text((18, y + 5), display_name, font=self._font12, fill=text_color)

        if self.program_runner.check_alive():
            fn = fm.selected_filename()
            if fn:
                short_name = fn[:-3] if fn.endswith(".py") else fn
                if len(short_name) > 18:
                    short_name = short_name[:15] + "..."
                status_txt = f"{t('running')} {short_name}"
                sw = draw.textbbox((0, 0), status_txt, font=self._font10)[2]
                draw.text(((320 - sw) // 2, 218), status_txt, font=self._font10, fill=T_RGB.success)

    # ====================================================================
    # 页面渲染：升级确认
    # ====================================================================
    def _render_upgrade_prompt(self, draw, bg):
        """渲染升级确认对话框：居中弹窗，左下取消/右下确认。"""
        um = self.upgrade_manager

        overlay = Image.new("RGBA", (320, 240), (0, 0, 0, 120))
        bg.paste(overlay, (0, 0), overlay)

        card_w, card_h = 260, 150
        card_x = (320 - card_w) // 2
        card_y = (240 - card_h) // 2
        draw.rounded_rectangle(
            [(card_x, card_y), (card_x + card_w, card_y + card_h)],
            radius=12,
            fill=T_RGB.bg_card,
            outline=T_RGB.accent,
            width=2,
        )

        title = t("upgrade_prompt_title")
        tw = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw) // 2, card_y + 16), title, font=self._font16, fill=T_RGB.text_primary)

        line_w = 50
        draw.rectangle(
            [((320 - line_w) // 2, card_y + 42), ((320 + line_w) // 2, card_y + 44)],
            fill=T_RGB.accent,
        )

        desc = t("upgrade_prompt_desc", um.current_version, um.latest_version)
        dw = draw.textbbox((0, 0), desc, font=self._font14)[2]
        draw.text(((320 - dw) // 2, card_y + 58), desc, font=self._font14, fill=T_RGB.text_secondary)

        sep_y = card_y + card_h - 40
        draw.line([(card_x + 10, sep_y), (card_x + card_w - 10, sep_y)], fill=T_RGB.bg_track, width=1)

        btn_y = sep_y + 6
        cancel_text = t("upgrade_prompt_cancel")
        cw = draw.textbbox((0, 0), cancel_text, font=self._font14)[2]
        draw.text((card_x + 24, btn_y), cancel_text, font=self._font14, fill=T_RGB.text_muted)
        confirm_text = t("upgrade_prompt_confirm")
        cfw = draw.textbbox((0, 0), confirm_text, font=self._font14)[2]
        draw.text((card_x + card_w - cfw - 24, btn_y), confirm_text, font=self._font14, fill=T_RGB.accent)

    # ====================================================================
    # 页面渲染：升级进度
    # ====================================================================
    def _render_upgrade_page(self, draw, bg):
        """渲染升级进度页面。"""
        um = self.upgrade_manager

        title = t("upgrade_title")
        tw = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw) // 2, 20), title, font=self._font16, fill=T_RGB.text_primary)

        line_w = 60
        draw.rectangle(
            [((320 - line_w) // 2, 44), ((320 + line_w) // 2, 46)],
            fill=T_RGB.accent,
        )

        ver_line = f"{um.current_version} → {um.latest_version}"
        vw = draw.textbbox((0, 0), ver_line, font=self._font14)[2]
        draw.text(((320 - vw) // 2, 62), ver_line, font=self._font14, fill=T_RGB.text_secondary)

        status_map = {
            um.STATUS_UPGRADING: (t("upgrading"), T_RGB.accent),
            um.STATUS_RESTARTING: (t("upgrade_restarting"), T_RGB.accent),
        }
        label, color = status_map.get(um.status, (t("upgrading"), T_RGB.accent))
        if um.message:
            label = f"{label} ({um.message})"
        lw = draw.textbbox((0, 0), label, font=self._font14)[2]
        draw.text(((320 - lw) // 2, 100), label, font=self._font14, fill=color)

        bar_y = 140
        bar_h = 6
        bar_w = 220
        bar_x = (320 - bar_w) // 2
        draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=T_RGB.bg_track)
        t_count = int(time.time() * 3) % 20
        for i in range(t_count):
            seg_x = bar_x + i * (bar_w // 20)
            seg_w = bar_w // 20
            draw.rectangle([(seg_x, bar_y), (seg_x + seg_w, bar_y + bar_h)], fill=T_RGB.accent)

        hint = t("loading_hint")
        hw = draw.textbbox((0, 0), hint, font=self._font12)[2]
        draw.text(((320 - hw) // 2, 175), hint, font=self._font12, fill=T_RGB.text_muted)

    # ====================================================================
    # 页面渲染：升级结果
    # ====================================================================
    def _render_upgrade_result(self, draw, bg):
        """渲染升级结果页面。"""
        um = self.upgrade_manager

        title = t("upgrade_title")
        tw = draw.textbbox((0, 0), title, font=self._font16)[2]
        draw.text(((320 - tw) // 2, 20), title, font=self._font16, fill=T_RGB.text_primary)

        line_w = 60
        draw.rectangle(
            [((320 - line_w) // 2, 44), ((320 + line_w) // 2, 46)],
            fill=T_RGB.accent,
        )

        if um.status == um.STATUS_SUCCESS:
            icon_color = T_RGB.success
            result_title = t("upgrade_success")
            result_detail = t("refresh_hint")
            ver_line = f"v{um.current_version}"
        else:
            icon_color = T_RGB.danger
            result_title = t("upgrade_failed")
            result_detail = t("upgrade_network_error")
            ver_line = ""

        cx, cy = 160, 90
        r = 28
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline=icon_color, width=3)
        if um.status == um.STATUS_SUCCESS:
            pts = [(cx - 14, cy), (cx - 4, cy + 10), (cx + 14, cy - 12)]
            for i in range(len(pts) - 1):
                draw.line([pts[i], pts[i + 1]], fill=icon_color, width=3)
        else:
            off = 12
            draw.line([(cx - off, cy - off), (cx + off, cy + off)], fill=icon_color, width=3)
            draw.line([(cx + off, cy - off), (cx - off, cy + off)], fill=icon_color, width=3)

        rtw = draw.textbbox((0, 0), result_title, font=self._font22)[2]
        draw.text(((320 - rtw) // 2, 135), result_title, font=self._font22, fill=icon_color)

        if ver_line:
            vw = draw.textbbox((0, 0), ver_line, font=self._font16)[2]
            draw.text(((320 - vw) // 2, 165), ver_line, font=self._font16, fill=T_RGB.text_primary)

        rtw2 = draw.textbbox((0, 0), result_detail, font=self._font14)[2]
        draw.text(((320 - rtw2) // 2, 195), result_detail, font=self._font14, fill=T_RGB.text_secondary)

    # ====================================================================
    # 布局 & 关闭
    # ====================================================================
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        if w > 100 and h > 100:
            self.display_label.setGeometry(0, 0, w, h)
            self.display_label.lower()
            self._render_and_display()

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