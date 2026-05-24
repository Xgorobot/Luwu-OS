#!/usr/bin/env python3
"""
Luwu OS - Coding 应用配置常量、语言支持、网络工具。
"""
import sys
import os
import time
import socket
import struct
import fcntl
import subprocess

# ========================================================================
# 配置常量
# ========================================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PICS_DIR = os.path.join(APP_DIR, "pics")
KEYS_FIFO = "/tmp/luwu_keys.fifo"
BLOCKLY_PORT = 80

# 接入 luwu-os 全局 i18n 与主题
LUWU_ROOT = "/home/pi/luwu-os"
if LUWU_ROOT not in sys.path:
    sys.path.insert(0, LUWU_ROOT)

try:
    from libs.i18n import get_lang as _i18n_get_lang, FONT_PATH as _I18N_FONT_PATH
except Exception:
    _i18n_get_lang = None
    _I18N_FONT_PATH = ""

from libs.theme import Asset as T_Asset

# coding 专属背景图路径
_CODING_BG_IMAGE = LUWU_ROOT + "/assets/images/app_bg.png"

LANGUAGE_INI = "/home/pi/luwu-os/configs/language.ini"
FONT_PATH = T_Asset.font_path or _I18N_FONT_PATH or "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"

# xgo_blockly 相关路径（系统 Python）
BLOCKLY_PYTHON = sys.executable
BLOCKLY_SERVICES_DIR = os.path.expanduser(
    "~/.local/lib/python3.13/site-packages/xgo_blockly/services"
)
BLOCKLY_PROJECTS_DIR = os.path.expanduser("~/xgoBlocklyProjects")
LOCK_JSON_PATH = os.path.expanduser("~/.xgo-blockly/lock.json")

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
PAGE_UPGRADE = 2
PAGE_UPGRADE_DONE = 3
PAGE_UPGRADE_PROMPT = 4

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
        "browser_hint": "在浏览器输入上方地址访问",
        "encryption_active": "加密保护中",
        "disable_encryption": "关闭加密",
        "update_available": "更新",
        "no_update": "已是最新",
        "version_label": "版本",
        "upgrade_title": "系统升级",
        "upgrading": "正在升级",
        "upgrade_restarting": "升级完成，正在重启",
        "upgrade_success": "升级完成",
        "upgrade_failed": "升级失败",
        "refresh_hint": "请在浏览器刷新页面",
        "upgrade_cancel": "C:返回",
        "upgrade_retry": "D:重试",
        "upgrade_back": "C:返回首页",
        "upgrade_network_error": "请检查网络后重试",
        "upgrade_prompt_title": "发现新版本",
        "upgrade_prompt_confirm": "D:确认升级",
        "upgrade_prompt_cancel": "C:取消",
        "upgrade_prompt_desc": "当前版本 {0}，可升级到 {1}",
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
        "browser_hint": "Open the address above in browser",
        "encryption_active": "Encryption Active",
        "disable_encryption": "Disable",
        "update_available": "Update",
        "no_update": "Up to date",
        "version_label": "Version",
        "upgrade_title": "System Update",
        "upgrading": "Upgrading",
        "upgrade_restarting": "Upgrade complete, restarting",
        "upgrade_success": "Upgrade complete",
        "upgrade_failed": "Upgrade failed",
        "refresh_hint": "Please refresh the browser",
        "upgrade_cancel": "C:Back",
        "upgrade_retry": "D:Retry",
        "upgrade_back": "C:Home",
        "upgrade_network_error": "Check network and retry",
        "upgrade_prompt_title": "New Version",
        "upgrade_prompt_confirm": "D:Upgrade",
        "upgrade_prompt_cancel": "C:Cancel",
        "upgrade_prompt_desc": "Current {0}, latest {1}",
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
# 端口检测与清理
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
        subprocess.run(["fuser", "-k", "80/tcp"], capture_output=True)
        time.sleep(0.5)
        print("[coding] cleaned up blockly processes", flush=True)
    except Exception as e:
        print(f"[coding] cleanup error: {e}", flush=True)