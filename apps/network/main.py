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
LANGUAGE_INI = "/home/pi/XGO-PI-CM5/common/language/language.ini"
FONT_PATH = "/home/pi/XGO-PI-CM5/common/model/msyh.ttc"
KEYS_FIFO = "/tmp/luwu_keys.fifo"

# ========================================================================
# Language support
# ========================================================================
def _detect_language():
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
        "success":     "WiFi连接成功！",
        "failed":      "连接失败，请重试",
        "reset":       "网络已重置为XGO2",
        "reset_hint":  "A:重置",
        "exit_hint":   "C:退出",
        "qr_hint":     "支持Android/XGO-APP WiFi二维码",
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
        "qr_hint":     "Android/XGO-APP WiFi QR supported",
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
        self.setStyleSheet("background-color: #0a0a1a;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._state = "scanning"       # scanning | connecting | success | failed | reset
        self._connecting_ssid = ""
        self._connecting_password = ""
        self._connecting_security = "wpa"
        self._frame_count = 0

        # --- Camera display (fullscreen background) ---
        self.camera_label = QLabel(self)
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setStyleSheet("background-color: black;")
        self.camera_label.lower()  # behind text overlays

        # --- Status label (top center overlay) ---
        self.status_label = QLabel(t("scanning"), self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(
            "color: white; font-size: 18px; font-weight: bold; "
            "background-color: rgba(0,0,0,0.6); padding: 6px 12px; border-radius: 4px;"
        )

        # --- Hint label (bottom center overlay) ---
        self.hint_label = QLabel(t("qr_hint"), self)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet(
            "color: #aabbee; font-size: 12px; "
            "background-color: rgba(0,0,0,0.5); padding: 4px 8px; border-radius: 4px;"
        )

        # --- Corner overlays (left side) ---
        corner_style = (
            "color: #ffffff; font-size: 14px; font-weight: bold; "
            "background-color: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 4px;"
        )
        self.corner_tl = QLabel(t("reset_hint"), self)
        self.corner_tl.setStyleSheet(corner_style)
        self.corner_bl = QLabel(t("exit_hint"), self)
        self.corner_bl.setStyleSheet(corner_style)

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
                                    "color: cyan; font-size: 18px; font-weight: bold; background-color: rgba(0,0,0,0.6); padding: 6px 12px; border-radius: 4px;"
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
                        [(x, y), (x + bw, y + bh)], outline=(0, 255, 0), width=2
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
                        self.status_label.setStyleSheet(
                            "color: white; font-size: 18px; font-weight: bold; background-color: rgba(0,0,0,0.6); padding: 6px 12px; border-radius: 4px;"
                        )
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
                "color: #18df6b; font-size: 18px; font-weight: bold; background-color: rgba(0,0,0,0.6); padding: 6px 12px; border-radius: 4px;"
            )
            print(f"[wifi_setup] Connected to {ssid}", flush=True)
        else:
            self._state = "failed"
            self._frame_count = 0
            self.status_label.setText(t("failed"))
            self.status_label.setStyleSheet(
                "color: #ff6b6b; font-size: 18px; font-weight: bold; background-color: rgba(0,0,0,0.6); padding: 6px 12px; border-radius: 4px;"
            )
            print(f"[wifi_setup] Failed to connect to {ssid}", flush=True)

    # ---- Reset to default network (threaded) ----
    def _reset_network(self):
        self._state = "reset"
        self._frame_count = 0
        self.status_label.setText(t("reset"))
        self.status_label.setStyleSheet(
            "color: #18df6b; font-size: 18px; font-weight: bold; background-color: rgba(0,0,0,0.6); padding: 6px 12px; border-radius: 4px;"
        )

        class _ResetWorker(QThread):
            def run(self):
                _connect_wifi("XGO2", "LuwuDynamics", "wpa")
                print("[wifi_setup] Network reset to XGO2", flush=True)

        self._reset_worker = _ResetWorker()
        self._reset_worker.start()

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
        if ev.key() == Qt.Key.Key_Back:   # C button (KEY_BACK) → exit
            print("[wifi_setup] KEY_BACK (C) pressed → exit", flush=True)
            self.close()
        elif ev.key() == Qt.Key.Key_Left:  # A button (KEY_LEFT) → reset
            print("[wifi_setup] KEY_LEFT (A) pressed → reset network", flush=True)
            self._reset_network()

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
    w = WifiSetupPage()
    w.showFullScreen()

    rc = app.exec()
    print(f"[wifi_setup] exit rc={rc}", flush=True)
    # Do not call sys.exit() — let preload script exit naturally


if __name__ == "__main__":
    main()
