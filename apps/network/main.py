#!/usr/bin/env python3
"""
Luwu OS - WiFi QR Code Network Setup (PySide6)
Launched from the Luwu OS launcher when WiFi card is selected.
Scans QR codes (Android/XGO-APP WiFi format) and connects via nmcli.

Physical button mapping (from luwu-keys.dts gpio-keys):
  A (GPIO17, top-left)     KEY_LEFT   → reset to XGO2
  B (GPIO22, top-right)    KEY_RIGHT  → (unused)
  C (GPIO23, bottom-left)  KEY_BACK   → exit
  D (GPIO24, bottom-right) KEY_ENTER  → (unused)
"""
import sys
import os
import time
import signal
import subprocess
import fcntl
import select

import cv2
import numpy as np
import pyzbar.pyzbar as pyzbar
from picamera2 import Picamera2
from PIL import Image, ImageDraw, ImageFont

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QSocketNotifier
from PySide6.QtGui import QKeyEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout

# ========================================================================
# Configuration
# ========================================================================
# 接入 luwu-os 全局 i18n（去除 XGO-PI-CM5 依赖）
LUWU_ROOT = "/home/pi/luwu-os"
if LUWU_ROOT not in sys.path:
    sys.path.insert(0, LUWU_ROOT)
try:
    from libs.i18n import get_lang as _i18n_get_lang, FONT_PATH as _I18N_FONT_PATH
except Exception:
    _i18n_get_lang = None
    _I18N_FONT_PATH = ""

# 主题层：QSS 工厂 / RGB 调色盘 / 资产
from libs.theme import (
    apply_app_palette,
    qss as T_qss,
    Color as T_Color,
    ColorRGB as T_RGB,
    Asset as T_Asset,
)

LANGUAGE_INI = "/home/pi/luwu-os/configs/language.ini"
FONT_PATH = T_Asset.font_path or _I18N_FONT_PATH or "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
KEYS_FIFO = "/tmp/luwu_keys.fifo"

# ========================================================================
# Language support
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
        "scanning":    "请将WiFi二维码对准摄像头",
        "no_qr":       "未检测到二维码",
        "connecting":  "正在连接 {}...",
        "success":     "WiFi连接成功!",
        "failed":      "连接失败,请重试",
        "reset":       "网络已重置为XGO2",
        "reset_hint":  "A:重置",
        "exit_hint":   "C:退出",
        "manual_hint": "D:手动",
        "qr_hint":     "支持Android/XGO-APP WiFi二维码",
        "wifi_list_title":    "选择WiFi网络",
        "wifi_scanning":      "扫描WiFi...",
        "wifi_no_networks":   "未发现WiFi网络",
        "wifi_now":           "已连接: {}",
        "wifi_none":          "未连接WiFi",
        "password_title":     "输入密码",
        "password_for":       "密码: {}",
        "keyboard_shift":     "↑",
        "keyboard_del":       "←",
        "keyboard_space":     "SP",
        "keyboard_ok":        "确定",
        # 四角按键提示 — WiFi列表页
        "corner_tl_list":     "A:上",
        "corner_tr_list":     "B:下",
        "corner_bl_list":     "C:返回",
        "corner_br_list":     "D:选择",
        # 四角按键提示 — 密码键盘页
        "corner_tl_kb":       "A:左",
        "corner_tr_kb":       "B:右",
        "corner_bl_kb":       "C:退格",
        "corner_br_kb":       "D:选择",
        # 国家选择
        "country_hint":       "B:国家",
        "country_title":      "选择国家/地区",
        "country_current":    "当前: {}",
        "country_set_ok":     "已设置: {}",
        "country_set_fail":   "设置失败",
        "corner_tl_country":  "A:上",
        "corner_tr_country":  "B:下",
        "corner_bl_country":  "C:返回",
        "corner_br_country":  "D:确认",
        # 确认重启
        "country_confirm_title":  "将重启机器",
        "country_confirm_desc":   "确定要设置为 {} 吗？\n重启后生效",
        "country_confirm_c":      "C:取消",
        "country_confirm_d":      "D:确认重启",
    },
    "en": {
        "scanning":    "Point WiFi QR code at camera",
        "no_qr":       "No QR code detected",
        "connecting":  "Connecting {}...",
        "success":     "WiFi Connected!",
        "failed":      "Connection failed, retry",
        "reset":       "Network reset to XGO2",
        "reset_hint":  "A:Reset",
        "exit_hint":   "C:Exit",
        "manual_hint": "D:Manual",
        "qr_hint":     "Android/XGO-APP WiFi QR supported",
        "wifi_list_title":    "Select WiFi",
        "wifi_scanning":      "Scanning WiFi...",
        "wifi_no_networks":   "No WiFi networks found",
        "wifi_now":           "Now: {}",
        "wifi_none":          "Not connected",
        "password_title":     "Enter password",
        "password_for":       "Password: {}",
        "keyboard_shift":     "↑",
        "keyboard_del":       "←",
        "keyboard_space":     "SP",
        "keyboard_ok":        "OK",
        # Corner key hints — WiFi list
        "corner_tl_list":     "A:Up",
        "corner_tr_list":     "B:Down",
        "corner_bl_list":     "C:Back",
        "corner_br_list":     "D:Select",
        # Corner key hints — Keyboard
        "corner_tl_kb":       "A:Left",
        "corner_tr_kb":       "B:Right",
        "corner_bl_kb":       "C:Del",
        "corner_br_kb":       "D:Select",
        # Country selection
        "country_hint":       "B:Country",
        "country_title":      "Select Country/Region",
        "country_current":    "Current: {}",
        "country_set_ok":     "Set: {}",
        "country_set_fail":   "Set Failed",
        "corner_tl_country":  "A:Up",
        "corner_tr_country":  "B:Down",
        "corner_bl_country":  "C:Back",
        "corner_br_country":  "D:OK",
        # Confirm reboot
        "country_confirm_title":  "Will reboot device",
        "country_confirm_desc":   "Set to {}?\nTakes effect after reboot",
        "country_confirm_c":      "C:Cancel",
        "country_confirm_d":      "D:Reboot Now",
    },
}

def t(key, *args):
    """Get translated text for current language."""
    text = _TEXTS.get(LA, _TEXTS["cn"]).get(key, key)
    if args:
        text = text.format(*args)
    return text

# ========================================================================
# WiFi connection helper
# ========================================================================
def _connect_wifi(ssid: str, password: str, security: str = "wpa") -> bool:
    """Connect to WiFi using nmcli connection add + up (nmcli 1.52+ compatible).
    Uses subprocess.run so nmcli output is captured and printed for debug.
    """
    def _run(args):
        r = subprocess.run(
            args, capture_output=True, text=True
        )
        out = (r.stdout + r.stderr).strip()
        if out:
            print(f"[wifi_setup] nmcli: {out}", flush=True)
        return r.returncode

    con_name = "luwu-wifi"
    # Remove any stale connection with same name
    _run(["sudo", "nmcli", "connection", "delete", con_name])

    # Build add command; key-mgmt based on QR T: field
    sec = security.upper()
    if sec in ("NOPASS", ""):
        add_args = ["sudo", "nmcli", "connection", "add",
                    "type", "wifi", "con-name", con_name, "ssid", ssid]
    elif sec == "WEP":
        add_args = ["sudo", "nmcli", "connection", "add",
                    "type", "wifi", "con-name", con_name, "ssid", ssid,
                    "wifi-sec.key-mgmt", "ieee8021x",
                    "wifi-sec.wep-key0", password]
    else:  # WPA / WPA2 / WPA3 / default
        add_args = ["sudo", "nmcli", "connection", "add",
                    "type", "wifi", "con-name", con_name, "ssid", ssid,
                    "wifi-sec.key-mgmt", "wpa-psk",
                    "wifi-sec.psk", password]

    print(f"[wifi_setup] add: {' '.join(add_args)}", flush=True)
    if _run(add_args) != 0:
        print("[wifi_setup] connection add failed", flush=True)
        return False

    # Rescan so NetworkManager knows the AP is visible
    _run(["sudo", "nmcli", "device", "wifi", "rescan"])
    import time as _time; _time.sleep(2)

    # Bring up
    up_args = ["sudo", "nmcli", "connection", "up", con_name]
    print(f"[wifi_setup] up: {' '.join(up_args)}", flush=True)
    return _run(up_args) == 0

# ========================================================================
# Main widget
# ========================================================================
class WifiSetupPage(QWidget):
    def __init__(self):
        super().__init__()
        # 本页面是“摄像头全屏”型，背景被 camera_label 覆盖；这里只保留一个
        # 深色兜底供摄像头未启动 / PIL 画面加载间隘使用
        self.setStyleSheet(f"background-color: rgb{T_RGB.canvas_dark};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._state = "scanning"       # scanning | connecting | success | failed | reset
        self._connecting_ssid = ""
        self._connecting_password = ""
        self._connecting_security = "wpa"
        self._frame_count = 0

        # --- Country selection state ---
        self._country_step = None         # None | "country_list" | "reboot"
        self._country_changed = False     # True if user just set a new country
        self._reboot_countdown = 0         # >0 when showing reboot countdown
        self._country_list = [
            ("CN","中国"),("US","美国"),("JP","日本"),
            ("KR","韩国"),("DE","德国"),("GB","英国"),
            ("FR","法国"),("TW","台湾"),("HK","香港"),
            ("SG","新加坡"),("IN","印度"),("AU","澳大利亚"),
            ("CA","加拿大"),("BR","巴西"),("RU","俄罗斯"),
        ]
        self._country_cursor = 0
        self._country_current_code = ""

        # --- Manual WiFi setup state ---
        self._manual_step = None         # None | "wifi_list" | "keyboard"
        self._wifi_list = []             # [(ssid, signal, security), ...]
        self._wifi_cursor = 0
        self._manual_ssid = ""
        self._manual_security = ""
        self._manual_password = ""
        self._kb_shift = False           # keyboard shift state
        self._kb_cursor_row = 0
        self._kb_cursor_col = 0

        # Keyboard layout: rows of (display_char, value_char_or_None)
        # None value means special key (shift, del, space, ok)
        self._kb_lower = [
            [('1','1'),('2','2'),('3','3'),('4','4'),('5','5'),('6','6'),('7','7'),('8','8'),('9','9'),('0','0')],
            [('q','q'),('w','w'),('e','e'),('r','r'),('t','t'),('y','y'),('u','u'),('i','i'),('o','o'),('p','p')],
            [('a','a'),('s','s'),('d','d'),('f','f'),('g','g'),('h','h'),('j','j'),('k','k'),('l','l')],
            [('↑',None),('z','z'),('x','x'),('c','c'),('v','v'),('b','b'),('n','n'),('m','m'),('←',None)],
            [('.','.'),('-','-'),('_','_'),('@','@'),('SP',None),('确定',None)],
        ]
        self._kb_upper = [
            [('1','1'),('2','2'),('3','3'),('4','4'),('5','5'),('6','6'),('7','7'),('8','8'),('9','9'),('0','0')],
            [('Q','Q'),('W','W'),('E','E'),('R','R'),('T','T'),('Y','Y'),('U','U'),('I','I'),('O','O'),('P','P')],
            [('A','A'),('S','S'),('D','D'),('F','F'),('G','G'),('H','H'),('J','J'),('K','K'),('L','L')],
            [('↑',None),('Z','Z'),('X','X'),('C','C'),('V','V'),('B','B'),('N','N'),('M','M'),('←',None)],
            [('.','.'),('-','-'),('_','_'),('@','@'),('SP',None),('确定',None)],
        ]

        # --- Camera display (fullscreen background) ---
        self.camera_label = QLabel(self)
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setStyleSheet(f"background-color: rgb{T_RGB.canvas_dark};")
        self.camera_label.lower()  # behind text overlays

        # --- Status label (top center overlay) ---
        self.status_label = QLabel(t("scanning"), self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(T_qss.overlay_pill("title", strong=True))

        # --- Hint label (bottom center overlay) ---
        self.hint_label = QLabel(t("qr_hint"), self)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet(T_qss.overlay_pill("hint"))

        # --- Corner overlays — 统一走主题 corner_pill ---
        corner_style = T_qss.corner_pill()
        self.corner_tl = QLabel(t("reset_hint"), self)
        self.corner_tl.setStyleSheet(corner_style)
        self.corner_bl = QLabel(t("exit_hint"), self)
        self.corner_bl.setStyleSheet(corner_style)

        self.corner_br = QLabel(t("manual_hint"), self)
        self.corner_br.setStyleSheet(corner_style)

        self.corner_tr = QLabel(t("country_hint"), self)
        self.corner_tr.setStyleSheet(corner_style)

        # --- Current WiFi label (top-center) — 已连接提示走 success 色 ---
        self.wifi_now_label = QLabel("", self)
        self.wifi_now_label.setStyleSheet(
            T_qss.overlay_pill("caption", color=T_Color.success)
        )

        # --- Camera ---
        self.picam2 = None  # Picamera2 | None
        self.camera_active = False

        self.camera_timer = QTimer(self)
        self.camera_timer.timeout.connect(self._process_frame)

        # --- Keys FIFO from launcher ---
        self._keys_fd = -1
        self._keys_notifier = None
        self._setup_keys_fifo()

        QTimer.singleShot(100, self._start_camera)
        QTimer.singleShot(200, self._update_current_wifi)

    # ---- Camera lifecycle ----
    def _start_camera(self):
        try:
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(
                main={"size": (320, 240), "format": "RGB888"}
            )
            self.picam2.configure(config)
            self.picam2.start()
            self.camera_active = True
            self.camera_timer.start(66)  # ~15 fps
            print("[wifi_setup] Camera started", flush=True)
        except Exception as e:
            self.status_label.setText(f"Camera error: {e}")
            print(f"[wifi_setup] Camera error: {e}", flush=True)

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
        # Country selection mode
        if self._country_step is not None:
            self._draw_country_ui()
            return
        # Manual mode: draw custom UI instead of camera feed
        if self._manual_step is not None:
            self._draw_manual_ui()
            return

        if not self.camera_active or self.picam2 is None:
            return

        try:
            # --- Capture frame (follow same pattern as original network_app.py) ---
            img = self.picam2.capture_array()
            img = cv2.flip(img, 1)  # mirror for selfie view

            # --- QR detection ---
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            barcodes = pyzbar.decode(gray)

            # --- Convert BGR → RGB → PIL for Chinese text overlay ---
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)
            draw = ImageDraw.Draw(pil_img)

            try:
                font_hint = ImageFont.truetype(FONT_PATH, 14)
                font_status = ImageFont.truetype(FONT_PATH, 18)
            except Exception:
                font_hint = ImageFont.load_default()
                font_status = ImageFont.load_default()

            # --- State machine ---
            if self._state == "scanning":
                if barcodes:
                    for barcode in barcodes:
                        data = barcode.data.decode("utf-8")
                        if data.startswith("WIFI:"):
                            # Parse standard WiFi QR format:
                            #   WIFI:S:ssid;T:WPA;P:password;H:false;;
                            wifi_data = data[5:].rstrip(";")
                            wifi_config = {}
                            for part in wifi_data.split(";"):
                                if ":" in part:
                                    key, value = part.split(":", 1)
                                    wifi_config[key] = value

                            ssid = wifi_config.get("S", "")
                            password = wifi_config.get("P", "")
                            security = wifi_config.get("T", "WPA")

                            if ssid:
                                self._connecting_ssid = ssid
                                self._connecting_password = password
                                self._connecting_security = security
                                self._state = "connecting"
                                self.status_label.setText(t("connecting", ssid))
                                self.status_label.setStyleSheet(
                                    T_qss.overlay_pill("title", color=T_Color.accent, strong=True)
                                )
                                # Defer connection to avoid blocking UI
                                QTimer.singleShot(100, self._do_connect)
                                break  # only handle first WiFi QR

                if not barcodes and self._state == "scanning":
                    pass  # no_qr shown via Qt label, not PIL overlay

            # --- Draw QR bounding rectangles ---
            if barcodes:
                for barcode in barcodes:
                    (x, y, bw, bh) = barcode.rect
                    # Draw rect on PIL image
                    draw.rectangle(
                        [(x, y), (x + bw, y + bh)], outline=T_RGB.success, width=2
                    )

            # --- Update hint label text ---
            self.hint_label.setText(t("qr_hint"))

            # --- Convert PIL → QImage → QPixmap ---
            result = np.array(pil_img)
            h, w, c = result.shape
            qimg = QImage(
                result.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888
            )
            pixmap = QPixmap.fromImage(qimg).scaled(
                self.camera_label.width(),
                self.camera_label.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.camera_label.setPixmap(pixmap)

            # --- Auto-advance for terminal states ---
            if self._state in ("success", "failed", "reset"):
                self._frame_count += 1
                if self._frame_count > 45:  # ~3 seconds at 15fps
                    if self._state == "success":
                        QTimer.singleShot(0, self.close)
                    else:
                        self._state = "scanning"
                        self.status_label.setText(t("scanning"))
                        self.status_label.setStyleSheet(T_qss.overlay_pill("title", strong=True))
                        self._frame_count = 0

        except Exception as e:
            print(f"[wifi_setup] Frame error: {e}", flush=True)

    # ---- WiFi connection worker thread ----
    def _do_connect(self):
        ssid = self._connecting_ssid
        password = self._connecting_password
        security = self._connecting_security
        if not ssid:
            return

        class _Worker(QThread):
            finished = Signal(bool)
            def __init__(self, ssid, password, security):
                super().__init__()
                self._ssid = ssid
                self._password = password
                self._security = security
            def run(self):
                ok = _connect_wifi(self._ssid, self._password, self._security)
                self.finished.emit(ok)

        self._worker = _Worker(ssid, password, security)
        self._worker.finished.connect(self._on_connect_done)
        self._worker.start()

    def _on_connect_done(self, success: bool):
        ssid = self._connecting_ssid
        if success:
            self._state = "success"
            self._frame_count = 0
            self.status_label.setText(t("success"))
            self.status_label.setStyleSheet(
                T_qss.overlay_pill("title", color=T_Color.success, strong=True)
            )
            self._update_current_wifi()
            self._reposition_wifi_label()
            print(f"[wifi_setup] Connected to {ssid}", flush=True)
        else:
            self._state = "failed"
            self._frame_count = 0
            self.status_label.setText(t("failed"))
            self.status_label.setStyleSheet(
                T_qss.overlay_pill("title", color=T_Color.danger, strong=True)
            )
            print(f"[wifi_setup] Failed to connect to {ssid}", flush=True)

    # ---- Get current WiFi SSID ----
    def _update_current_wifi(self):
        try:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid", "device", "wifi"],
                capture_output=True, text=True, timeout=5
            )
            for line in r.stdout.strip().split('\n'):
                if ':' in line:
                    parts = line.split(':', 1)
                    if parts[0].lower() in ('yes', '是'):
                        ssid = parts[1]
                        self.wifi_now_label.setText(t("wifi_now", ssid))
                        self._reposition_wifi_label()
                        return
            self.wifi_now_label.setText(t("wifi_none"))
        except Exception:
            self.wifi_now_label.setText("")
        self._reposition_wifi_label()

    def _reposition_wifi_label(self):
        self.wifi_now_label.adjustSize()
        w = self.width()
        pad = 12
        self.wifi_now_label.move((w - self.wifi_now_label.width()) // 2, pad + 4)
        self.wifi_now_label.raise_()

    # ---- Reset to default network (threaded) ----
    def _reset_network(self):
        self._state = "reset"
        self._frame_count = 0
        self.status_label.setText(t("reset"))
        self.status_label.setStyleSheet(
            T_qss.overlay_pill("title", color=T_Color.success, strong=True)
        )

        class _ResetWorker(QThread):
            def run(self):
                _connect_wifi("XGO2", "LuwuDynamics", "wpa")
                print("[wifi_setup] Network reset to XGO2", flush=True)

        self._reset_worker = _ResetWorker()
        self._reset_worker.start()

    # ================================================================
    # Country selection (B key)
    # ================================================================
    def _enter_country_mode(self):
        self._country_step = "country_list"
        self._country_cursor = 0
        # Use raspi-config to get the real alpha-2 country code
        # (iw reg get returns internal kernel enum numbers, not alpha-2)
        try:
            r = subprocess.run(
                ["sudo", "raspi-config", "nonint", "get_wifi_country"],
                capture_output=True, text=True, timeout=5
            )
            self._country_current_code = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            self._country_current_code = ""

        self.status_label.hide()
        self.hint_label.hide()
        self.corner_tl.hide()
        self.corner_bl.hide()
        self.corner_br.hide()
        self.corner_tr.hide()
        self.wifi_now_label.hide()

    def _exit_country_mode(self):
        self._country_step = None
        self.status_label.show()
        self.hint_label.show()
        self.corner_tl.show()
        self.corner_bl.show()
        self.corner_br.show()
        self.corner_tr.show()
        self.wifi_now_label.show()
        self._reposition_wifi_label()
        self.status_label.setText(t("scanning"))
        self.status_label.setStyleSheet(T_qss.overlay_pill("title", strong=True))
        self.hint_label.setText(t("qr_hint"))
        self._state = "scanning"
        self._frame_count = 0

    def _country_key_handler(self, key):
        # Ignore all keys during reboot countdown
        if self._reboot_countdown > 0:
            return

        if self._country_step == "country_confirm":
            if key == Qt.Key.Key_Back:  # C → cancel, back to list
                self._country_step = "country_list"
            elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D → confirm reboot
                self._reboot_countdown = 3
                QTimer.singleShot(1000, self._tick_reboot)
            return

        n = len(self._country_list)
        if key == Qt.Key.Key_Back:
            self._exit_country_mode()
        elif key == Qt.Key.Key_Left:
            if n > 0:
                self._country_cursor = (self._country_cursor - 1) % n
        elif key == Qt.Key.Key_Right:
            if n > 0:
                self._country_cursor = (self._country_cursor + 1) % n
        elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:
            if n > 0 and self._country_cursor < n:
                code = self._country_list[self._country_cursor][0]
                self._apply_country(code)

    def _apply_country(self, code):
        try:
            r = subprocess.run(
                ["sudo", "raspi-config", "nonint", "do_wifi_country", code],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                print(f"[wifi_setup] Country set to {code}", flush=True)
                self._country_changed = True
                self._reboot_code = code
                # Show confirmation dialog before reboot
                self._country_step = "country_confirm"
                return  # stay on country UI to show confirm dialog
            else:
                print(f"[wifi_setup] Country set failed: {r.stderr}", flush=True)
        except Exception as e:
            print(f"[wifi_setup] Country set error: {e}", flush=True)
        self._exit_country_mode()

    def _tick_reboot(self):
        self._reboot_countdown -= 1
        if self._reboot_countdown >= 1:
            if self._reboot_countdown == 1:
                self._do_reboot()  # reboot at 1, keep countdown screen visible
            else:
                QTimer.singleShot(1000, self._tick_reboot)

    def _do_reboot(self):
        print("[wifi_setup] Rebooting...", flush=True)
        subprocess.run(["sudo", "reboot"], capture_output=True, text=True)

    def _draw_country_ui(self):
        bg = Image.new('RGB', (320, 240), T_RGB.canvas_dark)
        draw = ImageDraw.Draw(bg)
        try:
            font14 = ImageFont.truetype(FONT_PATH, 14)
            font12 = ImageFont.truetype(FONT_PATH, 12)
            font18 = ImageFont.truetype(FONT_PATH, 18)
        except Exception:
            font14 = ImageFont.load_default()
            font12 = ImageFont.load_default()
            font18 = ImageFont.load_default()

        if self._reboot_countdown > 0:
            # Reboot countdown overlay
            lines = [
                t("country_set_ok", self._reboot_code),
                f"{self._reboot_countdown}s " + ("后自动重启..." if LA == "cn" else "until reboot..."),
            ]
            y = 70
            for line in lines:
                tw = draw.textbbox((0, 0), line, font=font18)[2]
                draw.text(((320 - tw) // 2, y), line, font=font18, fill=T_RGB.success)
                y += 36
        elif self._country_step == "country_confirm":
            # Confirm reboot dialog (solid overlay on country list)
            self._draw_country_list(draw, font14, font12)
            # Dim background — cover entire image with semi-dark fill
            dim = Image.new('RGBA', (320, 240), (0, 0, 0, 160))
            bg_rgba = bg.convert('RGBA')
            bg_rgba.paste(dim, (0, 0), dim)
            bg = bg_rgba.convert('RGB')
            draw = ImageDraw.Draw(bg)
            # Dialog box
            draw.rectangle([(25, 45), (295, 195)], fill=T_RGB.list_dialog_bg, outline=T_RGB.accent)
            # Confirm text
            title = t("country_confirm_title")
            tw = draw.textbbox((0, 0), title, font=font18)[2]
            draw.text(((320 - tw) // 2, 62), title, font=font18, fill=T_RGB.warning)
            desc = t("country_confirm_desc", self._reboot_code)
            for i, subline in enumerate(desc.split('\n')):
                tw2 = draw.textbbox((0, 0), subline, font=font14)[2]
                draw.text(((320 - tw2) // 2, 102 + i * 24), subline, font=font14, fill=T_RGB.text_invert)
            # Bottom hints — 取消走 muted，确认走 accent
            hint_c = t("country_confirm_c")
            hint_d = t("country_confirm_d")
            draw.text((50, 170), hint_c, font=font12, fill=T_RGB.text_muted)
            dw = draw.textbbox((0, 0), hint_d, font=font12)[2]
            draw.text((320 - dw - 50, 170), hint_d, font=font12, fill=T_RGB.accent)
        else:
            self._draw_country_list(draw, font14, font12)
        result = np.array(bg)
        h, w, c = result.shape
        qimg = QImage(result.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg).scaled(
            self.camera_label.width(),
            self.camera_label.height(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.camera_label.setPixmap(pixmap)

    def _draw_country_list(self, draw, font_title, font_item):
        title = t("country_title")
        tw = draw.textbbox((0, 0), title, font=font_title)[2]
        draw.text(((320 - tw) // 2, 4), title, font=font_title, fill=T_RGB.text_invert)
        if self._country_current_code:
            sub = t("country_current", self._country_current_code)
            stw = draw.textbbox((0, 0), sub, font=font_item)[2]
            draw.text(((320 - stw) // 2, 24), sub, font=font_item, fill=T_RGB.success)
        start_y = 44
        visible_start = max(0, self._country_cursor - 5)
        visible_count = min(8, len(self._country_list))
        for i in range(visible_start, min(visible_start + visible_count, len(self._country_list))):
            code, name = self._country_list[i]
            y = start_y + (i - visible_start) * 24
            if i == self._country_cursor:
                draw.rectangle([(2, y - 1), (318, y + 21)], fill=T_RGB.list_highlight)
            draw.text((10, y + 2), f"{code}  {name}", font=font_item, fill=T_RGB.text_invert)
        self._draw_corners(draw, font_item, "country")

    # ================================================================
    # Manual WiFi setup (D key)
    # ================================================================
    def _enter_manual_mode(self):
        self._manual_step = "wifi_list"
        self._wifi_list = []
        self._wifi_cursor = 0
        self._manual_ssid = ""
        self._manual_security = ""
        self._manual_password = ""

        # Hide camera-era labels
        self.status_label.hide()
        self.hint_label.hide()
        self.corner_tl.hide()
        self.corner_bl.hide()
        self.corner_br.hide()
        self.corner_tr.hide()
        self.wifi_now_label.hide()

        # NOTE: keep camera running — _process_frame() checks
        # _manual_step first and draws manual UI, skipping camera.

        country_changed = self._country_changed
        self._country_changed = False

        class _WifiScanWorker(QThread):
            result = Signal(list)
            def run(self):
                try:
                    import subprocess as sp
                    import time as _t
                    # If country was just changed, wait longer for the
                    # regulatory domain to fully take effect before scanning
                    if country_changed:
                        _t.sleep(4)
                    # rescan needs sudo to actually trigger hardware scan
                    sp.run(
                        ["sudo", "nmcli", "device", "wifi", "rescan"],
                        capture_output=True, text=True, timeout=15
                    )
                    _t.sleep(3)
                    r = sp.run(
                        ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
                        capture_output=True, text=True, timeout=10
                    )
                    lines = r.stdout.strip().split('\n') if r.stdout.strip() else []
                    nets = []
                    for line in lines:
                        line = line.strip()
                        if not line or line.startswith('*'):
                            continue
                        parts = line.split(':')
                        if len(parts) >= 3:
                            ssid = parts[0]
                            if ssid:
                                signal_strength = parts[1] if parts[1] else "0"
                                security = parts[2] if parts[2] else "NOPASS"
                                # Deduplicate by SSID (keep strongest)
                                dup = False
                                for j, (s, _, _) in enumerate(nets):
                                    if s == ssid:
                                        dup = True
                                        if int(signal_strength) > int(nets[j][1]):
                                            nets[j] = (ssid, signal_strength, security)
                                        break
                                if not dup:
                                    nets.append((ssid, signal_strength, security))
                    nets.sort(key=lambda x: -int(x[1]) if x[1].isdigit() else 0)
                    self.result.emit(nets)
                except Exception as e:
                    print(f"[wifi_setup] scan error: {e}", flush=True)
                    self.result.emit([])

        self._scan_worker = _WifiScanWorker()
        self._scan_worker.result.connect(self._on_wifi_scan_done)
        self._scan_worker.start()
        print("[wifi_setup] WiFi scan started", flush=True)

    def _on_wifi_scan_done(self, nets):
        self._wifi_list = nets
        self._wifi_cursor = 0
        print(f"[wifi_setup] found {len(nets)} networks", flush=True)

    def _exit_manual_mode(self):
        self._manual_step = None
        self._wifi_list = []
        self._manual_password = ""

        # Show labels again
        self.status_label.show()
        self.hint_label.show()
        self.corner_tl.show()
        self.corner_bl.show()
        self.corner_br.show()
        self.corner_tr.show()
        self.wifi_now_label.show()
        self._reposition_wifi_label()

        self.status_label.setText(t("scanning"))
        self.status_label.setStyleSheet(T_qss.overlay_pill("title", strong=True))
        self.hint_label.setText(t("qr_hint"))
        self._state = "scanning"
        self._frame_count = 0

        # Camera never stopped — no need to restart

    def _manual_key_handler(self, key):
        if self._manual_step == "wifi_list":
            self._manual_wifi_list_key(key)
        elif self._manual_step == "keyboard":
            self._manual_keyboard_key(key)

    def _manual_wifi_list_key(self, key):
        n = len(self._wifi_list)
        if key == Qt.Key.Key_Back:  # C → back to camera
            self._exit_manual_mode()
        elif key == Qt.Key.Key_Left:  # A → up
            if n > 0:
                self._wifi_cursor = (self._wifi_cursor - 1) % n
        elif key == Qt.Key.Key_Right:  # B → down
            if n > 0:
                self._wifi_cursor = (self._wifi_cursor + 1) % n
        elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D → select
            if n > 0 and self._wifi_cursor < n:
                self._manual_ssid = self._wifi_list[self._wifi_cursor][0]
                self._manual_security = self._wifi_list[self._wifi_cursor][2]
                self._manual_password = ""
                self._kb_shift = False
                self._kb_cursor_row = 0
                self._kb_cursor_col = 0
                self._manual_step = "keyboard"

    def _manual_keyboard_key(self, key):
        kb = self._kb_upper if self._kb_shift else self._kb_lower

        # Compute flat index and total count for cross-row wrapping
        flat_idx = 0
        for r in range(self._kb_cursor_row):
            flat_idx += len(kb[r])
        flat_idx += self._kb_cursor_col
        total_keys = sum(len(row) for row in kb)

        if key == Qt.Key.Key_Back:  # C → backspace if has input, else back to WiFi list
            if self._manual_password:
                self._manual_password = self._manual_password[:-1]
            else:
                self._manual_step = "wifi_list"
            return
        elif key == Qt.Key.Key_Left:  # A → left (wrap across rows)
            flat_idx = (flat_idx - 1) % total_keys
        elif key == Qt.Key.Key_Right:  # B → right (wrap across rows)
            flat_idx = (flat_idx + 1) % total_keys
        elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D → select
            display, value = kb[self._kb_cursor_row][self._kb_cursor_col]
            if value is None:
                # Special key
                if display in ('↑',):
                    self._kb_shift = not self._kb_shift
                elif display in ('←',):
                    self._manual_password = self._manual_password[:-1]
                elif display in ('SP',):
                    self._manual_password += ' '
                elif display in ('确定', 'OK'):
                    self._confirm_manual_connect()
            else:
                self._manual_password += value

        # Convert flat index back to (row, col)
        remaining = flat_idx
        for r in range(len(kb)):
            ncols = len(kb[r])
            if remaining < ncols:
                self._kb_cursor_row = r
                self._kb_cursor_col = remaining
                break
            remaining -= ncols

    def _confirm_manual_connect(self):
        if not self._manual_ssid:
            return
        self._manual_step = None
        self._connecting_ssid = self._manual_ssid
        self._connecting_password = self._manual_password
        self._connecting_security = self._manual_security
        self._state = "connecting"

        # Show labels again
        self.status_label.show()
        self.hint_label.show()
        self.corner_tl.show()
        self.corner_bl.show()
        self.corner_br.show()
        self.corner_tr.show()
        self.wifi_now_label.show()

        self.status_label.setText(t("connecting", self._manual_ssid))
        self.status_label.setStyleSheet(
            T_qss.overlay_pill("title", color=T_Color.accent, strong=True)
        )
        self.hint_label.setText(t("qr_hint"))

        # Camera never stopped — no need to restart

        QTimer.singleShot(100, self._do_connect)

    # ---- Manual UI drawing (PIL-based) ----
    def _draw_corners(self, draw, font, page_type="list"):
        """Draw A/B/C/D corner labels with semi-transparent black backgrounds."""
        if page_type == "list":
            tl = t("corner_tl_list")
            tr = t("corner_tr_list")
            bl = t("corner_bl_list")
            br = t("corner_br_list")
        elif page_type == "country":
            tl = t("corner_tl_country")
            tr = t("corner_tr_country")
            bl = t("corner_bl_country")
            br = t("corner_br_country")
        else:  # keyboard
            tl = t("corner_tl_kb")
            tr = t("corner_tr_kb")
            bl = t("corner_bl_kb")
            br = t("corner_br_kb")

        margin = 0   # 贴边，与 AppFrame 规范一致
        pad = 4

        for text, pos in [(tl, "tl"), (tr, "tr"), (bl, "bl"), (br, "br")]:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]

            if pos == "tl":
                x, y = margin, margin
            elif pos == "tr":
                x, y = 320 - tw - pad * 2 - margin, margin
            elif pos == "bl":
                x, y = margin, 240 - th - pad * 2 - margin
            else:  # br
                x, y = 320 - tw - pad * 2 - margin, 240 - th - pad * 2 - margin

            # 半透明黑背 + 纯白文字（与 Qt 侧 corner_pill 保持一致）
            draw.rectangle(
                [(x, y), (x + tw + pad * 2, y + th + pad * 2)],
                fill=T_RGB.overlay_dim
            )
            draw.text((x + pad, y + pad), text, font=font, fill=T_RGB.text_invert)

    def _draw_manual_ui(self):
        bg = Image.new('RGB', (320, 240), T_RGB.canvas_dark)
        draw = ImageDraw.Draw(bg)
        try:
            font14 = ImageFont.truetype(FONT_PATH, 14)
            font12 = ImageFont.truetype(FONT_PATH, 12)
            font11 = ImageFont.truetype(FONT_PATH, 11)
        except Exception:
            font14 = ImageFont.load_default()
            font12 = ImageFont.load_default()
            font11 = ImageFont.load_default()

        if self._manual_step == "wifi_list":
            self._draw_wifi_list(draw, font14, font12)
        elif self._manual_step == "keyboard":
            self._draw_keyboard(draw, font14, font12, font11)

        result = np.array(bg)
        h, w, c = result.shape
        qimg = QImage(result.data.tobytes(), w, h, w * c, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg).scaled(
            self.camera_label.width(),
            self.camera_label.height(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.camera_label.setPixmap(pixmap)

    def _draw_wifi_list(self, draw, font_title, font_item):
        title = t("wifi_list_title")
        tw = draw.textbbox((0, 0), title, font=font_title)[2]
        draw.text(((320 - tw) // 2, 4), title, font=font_title, fill=T_RGB.text_invert)

        if not self._wifi_list:
            draw.text((10, 40), t("wifi_scanning"), font=font_item, fill=T_RGB.text_muted)
            self._draw_corners(draw, font_item, "list")
            return

        start_y = 28
        visible_start = max(0, self._wifi_cursor - 4)
        visible_count = min(8, len(self._wifi_list))

        for i in range(visible_start, min(visible_start + visible_count, len(self._wifi_list))):
            ssid, sig, sec = self._wifi_list[i]
            y = start_y + (i - visible_start) * 24

            # Cursor highlight — 主题 accent
            if i == self._wifi_cursor:
                draw.rectangle([(2, y - 1), (318, y + 21)], fill=T_RGB.list_highlight)

            # Signal bar — 语义色（强/中/弱 = success/warning/danger）
            try:
                sval = int(sig)
            except Exception:
                sval = 0
            bars = '▂▄▆█' if sval > 60 else ('▂▄▆' if sval > 40 else ('▂▄' if sval > 20 else '▂'))
            color = T_RGB.success if sval > 60 else (T_RGB.warning if sval > 30 else T_RGB.danger)
            draw.text((6, y + 1), bars, font=font_item, fill=color)

            # SSID (truncated)
            display_ssid = ssid[:18] + '..' if len(ssid) > 20 else ssid
            draw.text((52, y + 1), display_ssid, font=font_item, fill=T_RGB.text_invert)

            # Security badge (asterisk = has security)
            if sec.upper() not in ('', 'NOPASS'):
                draw.text((280, y + 2), '*', font=font_item, fill=T_RGB.text_muted)

        # Four-corner key hints
        self._draw_corners(draw, font_item, "list")

    def _draw_keyboard(self, draw, font_title, font_key, font_small):
        # Title (centered)
        pwd_display = self._manual_password[-20:] if len(self._manual_password) > 20 else self._manual_password
        title = t("password_for", pwd_display if pwd_display else '_' * 8)
        tw1 = draw.textbbox((0, 0), title, font=font_title)[2]
        draw.text(((320 - tw1) // 2, 4), title, font=font_title, fill=T_RGB.text_invert)

        # Subtitle (centered)
        sub = t("password_title")
        tw2 = draw.textbbox((0, 0), sub, font=font_small)[2]
        draw.text(((320 - tw2) // 2, 24), sub, font=font_small, fill=T_RGB.text_muted)

        kb = self._kb_upper if self._kb_shift else self._kb_lower
        key_w = 30
        key_h = 24
        margin_x = 10
        start_y = 48
        gap = 2

        # 键盘色板（PIL 用，与主题 token 对齐）
        KEY_BG_NORMAL   = (52, 60, 84)      # 普通键底 — 深蓝灰
        KEY_BG_SPECIAL  = (72, 84, 112)     # 特殊键底 — 略亮
        KEY_BG_OK       = T_RGB.success     # 确认键 — success
        KEY_BG_OK_HOVER = (52, 200, 110)    # 确认键选中 — success 亮一档
        KEY_BG_HOVER    = T_RGB.accent      # 一般键选中 — accent
        KEY_OUTLINE     = T_RGB.text_invert

        for row_idx, row in enumerate(kb):
            y = start_y + row_idx * (key_h + gap)
            # Center the row
            row_width = len(row) * (key_w + gap) - gap
            x = (320 - row_width) // 2

            for col_idx, (display, value) in enumerate(row):
                kx = x + col_idx * (key_w + gap)
                is_cursor = (row_idx == self._kb_cursor_row and col_idx == self._kb_cursor_col)
                is_ok = (display in ('确定', 'OK'))

                if is_cursor:
                    fill = KEY_BG_OK_HOVER if is_ok else KEY_BG_HOVER
                    draw.rectangle([(kx - 1, y - 1), (kx + key_w, y + key_h)],
                                   fill=fill, outline=KEY_OUTLINE)
                else:
                    if is_ok:
                        fill = KEY_BG_OK
                    else:
                        is_special = (value is None)
                        fill = KEY_BG_SPECIAL if is_special else KEY_BG_NORMAL
                    draw.rectangle([(kx, y), (kx + key_w - 1, y + key_h - 1)], fill=fill)

                # Key label
                bbox = draw.textbbox((0, 0), display, font=font_small)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                draw.text((kx + (key_w - tw) // 2, y + (key_h - th) // 2 - 1),
                          display, font=font_small, fill=T_RGB.text_invert)

        # Four-corner key hints
        self._draw_corners(draw, font_small, "kb")

    # ---- Keys FIFO from launcher ----
    def _setup_keys_fifo(self):
        try:
            self._keys_fd = os.open(KEYS_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            self._keys_notifier = QSocketNotifier(self._keys_fd, QSocketNotifier.Type.Read, self)
            self._keys_notifier.activated.connect(self._on_key_fifo)
            print("[wifi_setup] Keys FIFO opened", flush=True)
        except Exception as e:
            print(f"[wifi_setup] Keys FIFO error: {e}", flush=True)

    def _on_key_fifo(self, fd: int):
        try:
            data = os.read(fd, 32)
            if data:
                for line in data.decode().strip().split('\n'):
                    if line.strip():
                        qt_key = int(line.strip())
                        print(f"[wifi_setup] FIFO recv Qt key={qt_key} (0x{qt_key:x})", flush=True)
                        ev = QKeyEvent(QKeyEvent.Type.KeyPress, qt_key, Qt.KeyboardModifier.NoModifier)
                        QApplication.postEvent(self, ev)
        except Exception as e:
            print(f"[wifi_setup] key fifo read error: {e}", flush=True)

    # ---- Key events (aligned with luwu-keys.dts) ----
    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()

        # --- Country mode key handling ---
        if self._country_step is not None:
            self._country_key_handler(key)
            return

        # --- Manual mode key handling ---
        if self._manual_step is not None:
            self._manual_key_handler(key)
            return

        # --- Normal mode ---
        if key == Qt.Key.Key_Back:   # C button (KEY_BACK) → exit
            print("[wifi_setup] KEY_BACK (C) pressed → exit", flush=True)
            self.close()
        elif key == Qt.Key.Key_Left:  # A button (KEY_LEFT) → reset
            print("[wifi_setup] KEY_LEFT (A) pressed → reset network", flush=True)
            self._reset_network()
        elif key == Qt.Key.Key_Right:  # B button (KEY_RIGHT) → country
            print("[wifi_setup] KEY_RIGHT (B) pressed → country", flush=True)
            self._enter_country_mode()
        elif key == Qt.Key.Key_Enter or key == Qt.Key.Key_Return:  # D button → manual
            print("[wifi_setup] KEY_ENTER (D) pressed → manual setup", flush=True)
            self._enter_manual_mode()

    # ---- Resize ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 12

        # Camera fills entire screen
        self.camera_label.setGeometry(0, 0, w, h)

        # Center text group (vertically centered)
        self.status_label.adjustSize()
        self.hint_label.adjustSize()
        total_h = self.status_label.height() + 8 + self.hint_label.height()  # 8px gap
        group_y = (h - total_h) // 2

        sw = self.status_label.width()
        self.status_label.move((w - sw) // 2, group_y)

        hw = self.hint_label.width()
        self.hint_label.move((w - hw) // 2, group_y + self.status_label.height() + 8)

        # Corners on left side (raised above center text)
        self.corner_tl.raise_()
        self.corner_tl.adjustSize()
        self.corner_tl.move(pad, pad + 4)
        self.corner_bl.raise_()
        self.corner_bl.adjustSize()
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad - 4)

        # Bottom-right corner
        self.corner_br.raise_()
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad - 4)

        # Top-right corner (country)
        self.corner_tr.raise_()
        self.corner_tr.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad + 4)

        # Current WiFi label (top-center, no width limit)
        self.wifi_now_label.raise_()
        self.wifi_now_label.adjustSize()
        self.wifi_now_label.move((w - self.wifi_now_label.width()) // 2, pad + 4)

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
        print("[wifi_setup] closing", flush=True)
        super().closeEvent(ev)


# ========================================================================
# Entry point
# ========================================================================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    apply_app_palette(app)
    w = WifiSetupPage()
    w.showFullScreen()

    rc = app.exec()
    print(f"[wifi_setup] exit rc={rc}", flush=True)
    # Do not call sys.exit() — let preload script exit naturally


if __name__ == "__main__":
    main()
