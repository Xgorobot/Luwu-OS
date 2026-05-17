#!/usr/bin/env python3
"""
PySide6 热点模式 App — 由 Luwu OS launcher 启动。
创建 WiFi 热点，显示 SSID / 密码 / IP 地址。
按任意物理按键(C)退出并关闭热点。
"""
import os
import sys
import time
import signal
import random
import string
import subprocess
import socket
import struct
import fcntl

# ===================== 阶段计时 =====================
T0 = time.monotonic()
_stages = []


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[hotspot][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== PySide6 导入 =====================
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout

mark("PySide6 imports done")

# ===================== 常量 =====================
AUTO_EXIT_SEC = 300  # 5 分钟自动退出

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "title": "热点模式",
            "creating": "正在创建热点...",
            "hint": "C键: 关闭页面（保留热点）  D键: 关闭热点",
            "corner_close_page": "C: 关闭页面",
            "corner_close_hotspot": "D: 关闭热点",
            "step_activate": "激活无线网卡...",
            "step_disconnect": "断开当前连接...",
            "step_restart": "重启网络管理...",
            "step_creating": "创建热点中...",
            "step_created": "✅ 热点已创建",
            "step_warn": "⚠️ 热点创建中...",
            "step_ready": "✅ 热点已就绪",
            "password_label": "🔑 密码: {}",
            "getting": "获取中...",
        },
        "en": {
            "title": "Hotspot Mode",
            "creating": "Creating hotspot...",
            "hint": "C: Close page (keep hotspot)  D: Stop hotspot",
            "corner_close_page": "C: Close page",
            "corner_close_hotspot": "D: Stop hotspot",
            "step_activate": "Activating Wi-Fi adapter...",
            "step_disconnect": "Disconnecting current network...",
            "step_restart": "Restarting NetworkManager...",
            "step_creating": "Creating hotspot...",
            "step_created": "✅ Hotspot created",
            "step_warn": "⚠️ Hotspot starting...",
            "step_ready": "✅ Hotspot ready",
            "password_label": "🔑 Password: {}",
            "getting": "Getting...",
        },
    })
except Exception:
    _T = lambda k, *a: k.format(*a) if a else k

# ===================== 工具函数 =====================
def generate_wifi_ssid():
    prefix = "xgo-"
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return prefix + suffix


def generate_wifi_password():
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))


def get_ip(ifname):
    """获取指定网络接口的 IP 地址"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            fcntl.ioctl(
                s.fileno(), 0x8915, struct.pack("256s", bytes(ifname[:15], "utf-8"))
            )[20:24]
        )
    except Exception:
        return _T("getting")


def run_cmd(cmd):
    """运行 shell 命令并返回结果"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


mark("utils defined")


# ===================== PySide6 页面 =====================
class HotspotPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0a0a1a;")
        self._first_paint_logged = False

        self.ssid = ""
        self.password = ""
        self.ip_address = ""
        self.hotspot_active = False
        self._keep_hotspot_on_close = False  # True 表示关闭页面但保留热点运行

        # ---- 标题 ----
        self.title = QLabel(_T("title"))
        f1 = QFont()
        f1.setPointSize(20)
        f1.setBold(True)
        self.title.setFont(f1)
        self.title.setStyleSheet("color: white;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- WiFi 图标 ----
        self.icon_label = QLabel("📶")
        self.icon_label.setStyleSheet("font-size: 48px; color: white;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 状态 ----
        self.status_label = QLabel(_T("creating"))
        f2 = QFont()
        f2.setPointSize(13)
        self.status_label.setFont(f2)
        self.status_label.setStyleSheet("color: #18df6b;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        # ---- SSID 显示 ----
        self.ssid_label = QLabel("")
        f3 = QFont()
        f3.setPointSize(15)
        f3.setBold(True)
        self.ssid_label.setFont(f3)
        self.ssid_label.setStyleSheet("color: #FFD93D;")
        self.ssid_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 密码显示 ----
        self.pwd_label = QLabel("")
        f4 = QFont()
        f4.setPointSize(15)
        f4.setBold(True)
        self.pwd_label.setFont(f4)
        self.pwd_label.setStyleSheet("color: #FF6B6B;")
        self.pwd_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- IP 显示 ----
        self.ip_label = QLabel("")
        f5 = QFont()
        f5.setPointSize(13)
        self.ip_label.setFont(f5)
        self.ip_label.setStyleSheet("color: #4A90D9;")
        self.ip_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 提示 ----
        self.hint = QLabel(_T("hint"))
        self.hint.setStyleSheet("color: #5c6a9c; font-size: 11px;")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 四角按键提示 ----
        corner_style = "color: #5c6a9c; font-size: 11px; background: transparent;"
        self.corner_bl = QLabel(_T("corner_close_page"), self)
        self.corner_bl.setStyleSheet(corner_style)
        self.corner_br = QLabel(_T("corner_close_hotspot"), self)
        self.corner_br.setStyleSheet(corner_style)

        # ---- 布局 ----
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addSpacing(16)
        layout.addWidget(self.icon_label)
        layout.addSpacing(12)
        layout.addWidget(self.status_label)
        layout.addSpacing(20)
        layout.addWidget(self.ssid_label)
        layout.addSpacing(6)
        layout.addWidget(self.pwd_label)
        layout.addSpacing(6)
        layout.addWidget(self.ip_label)
        layout.addStretch()
        layout.addWidget(self.hint)

        # ---- 自动退出兜底 ----
        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 延迟启动热点（让 UI 先渲染）
        QTimer.singleShot(200, self._start_hotspot)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 16
        self.corner_bl.adjustSize()
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.adjustSize()
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")

    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()
        if key == Qt.Key.Key_Back:
            # C 键：关闭页面但保留热点
            print("[hotspot] KEY_BACK (C) -> close page, keep hotspot", flush=True)
            self._keep_hotspot_on_close = True
            self.close()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # D 键：关闭热点并退出
            print("[hotspot] KEY_RETURN (D) -> stop hotspot and exit", flush=True)
            self._stop_hotspot()
            self.close()

    def _start_hotspot(self):
        """创建 WiFi 热点"""
        self.ssid = generate_wifi_ssid()
        self.password = generate_wifi_password()

        print(f"[hotspot] creating hotspot: SSID={self.ssid}", flush=True)

        # Step 1: 激活 wlan0
        self.status_label.setText(_T("step_activate"))
        QApplication.processEvents()
        run_cmd("sudo ifconfig wlan0 up")
        time.sleep(2)

        # Step 2: 断开当前连接
        self.status_label.setText(_T("step_disconnect"))
        QApplication.processEvents()
        run_cmd("sudo nmcli device disconnect wlan0")
        time.sleep(1)

        # Step 3: 重启 NetworkManager
        self.status_label.setText(_T("step_restart"))
        QApplication.processEvents()
        run_cmd("sudo systemctl restart NetworkManager")
        time.sleep(5)

        # Step 4: 清理可能的残留连接
        run_cmd("sudo nmcli connection delete Hotspot-7 2>/dev/null")
        time.sleep(1)

        # Step 5: 创建热点
        self.status_label.setText(_T("step_creating"))
        QApplication.processEvents()

        hotspot_cmd = f"sudo nmcli device wifi hotspot ssid {self.ssid} password {self.password}"
        rc, stdout, stderr = run_cmd(hotspot_cmd)

        if rc == 0:
            print("[hotspot] Wi-Fi Hotspot Created Successfully", flush=True)
            self.hotspot_active = True
            self.status_label.setText(_T("step_created"))
            time.sleep(3)

            # 获取 IP
            self.ip_address = get_ip("wlan0")
            if not self.ip_address:
                self.ip_address = "192.168.7.1"
        else:
            print(f"[hotspot] Wi-Fi Hotspot Create Failed: rc={rc} stderr={stderr}", flush=True)
            self.hotspot_active = True  # 有时返回非0但热点实际已创建
            self.status_label.setText(_T("step_warn"))
            time.sleep(3)
            self.ip_address = get_ip("wlan0")
            if not self.ip_address:
                self.ip_address = "192.168.7.1"

        # 更新显示
        self.ssid_label.setText(f"📡 SSID: {self.ssid}")
        self.pwd_label.setText(_T("password_label", self.password))
        self.ip_label.setText(f"🌐 IP: {self.ip_address}")

        if self.hotspot_active:
            self.status_label.setText(_T("step_ready"))

    def _stop_hotspot(self):
        """关闭 WiFi 热点"""
        print("[hotspot] stopping hotspot...", flush=True)
        run_cmd("sudo nmcli connection down Hotspot-7 2>/dev/null")
        time.sleep(1)
        print("[hotspot] hotspot stopped", flush=True)

    def closeEvent(self, ev):
        print(f"[hotspot] closing (keep_hotspot={self._keep_hotspot_on_close})", flush=True)
        if self.hotspot_active and not self._keep_hotspot_on_close:
            self._stop_hotspot()
        super().closeEvent(ev)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = HotspotPage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[hotspot] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
