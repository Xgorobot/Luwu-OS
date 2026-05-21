#!/usr/bin/env python3
"""
QR 码展示页面 — 显示键位映射配置入口二维码。
用户手机扫码后打开 Web 页面，可在线配置手柄按键映射。
"""
import io
import socket
import subprocess
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

# 复用 luwu-os 主题体系
import sys
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
from libs.theme import Asset as T_Asset, Color as T_Color, Spacing, qss as T_qss
from libs.ui import AppFrame
from libs.i18n import Translator as _Translator

_T = _Translator({
    "cn": {
        "qr_title": "键位映射",
        "qr_hint": "手机扫码配置手柄按键",
        "qr_url_hint": "或访问：",
        "back": "返回",
    },
    "en": {
        "qr_title": "Key Mapping",
        "qr_hint": "Scan QR code to configure",
        "qr_url_hint": "Or visit:",
        "back": "Back",
    },
})

MAPPING_PORT = 8088


def _get_local_ip() -> str:
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    # fallback: 通过 hostname -I
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True).strip()
        if out:
            return out.split()[0]
    except Exception:
        pass
    return "127.0.0.1"


def generate_qr_pixmap(url: str, size: int = 150) -> QPixmap:
    """生成 QR 码 QPixmap"""
    import qrcode
    from qrcode.image.pil import PilImage
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(
        fill_color="#1a3a6e",
        back_color="white",
        image_factory=PilImage,
    ).convert("RGBA")

    # PIL Image → QPixmap
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    pix = QPixmap.fromImage(qimg).scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return pix


class QRMappingPage(AppFrame):
    """键位映射 QR 码页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._url = ""

        # ---- 角标 ----
        self.setCornerHints(
            bl=(_T("back"), T_Asset.icon_back),
        )

        # ---- 中心内容 ----
        center = QWidget(self)
        center.setStyleSheet(T_qss.transparent())
        v = QVBoxLayout(center)
        v.setContentsMargins(0, 0, 0, 0)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(Spacing.xs)

        # QR 码图片（屏幕 320x240，标题+角标占 ~60px，QR 区域控制在 ~180px）
        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setStyleSheet(
            f"background: white; border: 2px solid {T_Color.accent}; border-radius: 8px; padding: 4px;"
        )
        self.qr_label.setFixedSize(140, 140)

        # 提示文字
        self.hint_label = QLabel(_T("qr_hint"), self)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet(T_qss.text("hint"))

        # URL 文字
        self.url_label = QLabel("", self)
        self.url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.url_label.setStyleSheet(
            T_qss.text("caption", color=T_Color.accent)
            + "font-family: monospace;"
        )

        v.addStretch()
        v.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.xs)
        v.addWidget(self.hint_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(2)
        v.addWidget(self.url_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch()

        self._center = center

        # 生成 QR 码
        self._generate()

    def _generate(self):
        """生成二维码"""
        try:
            ip = _get_local_ip()
            self._url = f"http://{ip}:{MAPPING_PORT}"
            pix = generate_qr_pixmap(self._url)
            if pix and not pix.isNull():
                self.qr_label.setPixmap(pix)
            self.url_label.setText(f"{_T('qr_url_hint')} {self._url}")
        except Exception as e:
            print(f"[qr_page] QR generate failed: {e}", flush=True)
            self.url_label.setText(f"{_T('qr_url_hint')} (生成失败)")

    def url(self) -> str:
        return self._url

    def resizeEvent(self, ev):
        """布局：内容区域定位 + 标题强制水平居中"""
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        # 居中内容区域
        top = max(26, h * 12 // 100)
        bottom = max(16, h * 6 // 100)
        self._center.setGeometry(0, top, w, h - top - bottom)
    def keyPressEvent(self, ev):
        key = ev.key()
        if key == Qt.Key.Key_Back:
            # C 键 → 返回
            if hasattr(self, 'go_back') and callable(self.go_back):
                self.go_back()
