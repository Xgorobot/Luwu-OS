#!/usr/bin/env python3
"""
PySide6 蓝牙遥控 App — 由 Luwu OS launcher 启动。

功能：
- 自动开启蓝牙、扫描周围手柄设备
- 自动配对 + 信任 + 连接（Xbox / Wireless Controller / Gamepad 等）
- 已连接的手柄会优先复用，无需重新配对
- 连接成功后自动启动 evdev 手柄控制器，实时操控机器狗
- 断开自动重连

按键：
- C 键（Key_Back）：退出
- D 键（Key_Return）：手动重新扫描
"""
import os
import sys
import re
import time
import signal
import select
import subprocess
import threading
import pty

# ===================== 阶段计时 =====================
T0 = time.monotonic()


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    print(f"[bt_gamepad][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== PySide6 =====================
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QFrame,
)

# ---- luwu-os 主题层 ----
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
from libs.theme import (  # noqa: E402
    apply_app_palette, Asset as T_Asset, Color as T_Color,
    Spacing, qss as T_qss,
)
from libs.ui import AppFrame  # noqa: E402
from libs.ui.frame import _invisible_cursor  # noqa: E402
from libs.i18n import Translator as _Translator  # noqa: E402

# 键位映射相关
# 确保当前目录在 sys.path 中（launcher 通过 importlib 加载时工作目录可能不同）
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from qr_page import QRMappingPage  # noqa: E402
import mapping_server  # noqa: E402

mark("PySide6 imports done")

# ===================== i18n =====================
_T = _Translator({
    "cn": {
        "title": "蓝牙遥控",
        "init": "正在初始化蓝牙...",
        "scanning": "正在扫描蓝牙手柄...",
        "pairing": "正在配对：{}",
        "connecting": "正在连接：{}",
        "connected": "已连接：{}",
        "ready": "手柄就绪，正在控制机器狗",
        "already": "手柄已连接",
        "disconnected": "手柄已断开，重新扫描中...",
        "not_found": "未发现手柄，{} 秒后重试",
        "not_available": "手柄不在范围内，请确认已开机并进入配对模式",
        "pair_failed": "配对失败，重试中...",
        "connect_failed": "连接失败，重试中...",
        "bt_error": "蓝牙未就绪",
        "reconnecting": "正在重连 {}...",
        "hint_exit": "退出",
        "hint_rescan": "D 重新扫描",
        "hint_mapping": "键位映射",
        "hint_disconnect": "断开",
        "key_mapping": "键位映射",
        "back": "返回",
    },
    "en": {
        "title": "BT Gamepad",
        "init": "Initializing Bluetooth...",
        "scanning": "Scanning for gamepad...",
        "pairing": "Pairing: {}",
        "connecting": "Connecting: {}",
        "connected": "Connected: {}",
        "ready": "Gamepad ready, controlling robot",
        "already": "Gamepad already connected",
        "disconnected": "Disconnected, rescanning...",
        "not_found": "No gamepad found, retry in {}s",
        "not_available": "Gamepad out of range, turn on and enter pairing mode",
        "pair_failed": "Pair failed, retrying...",
        "connect_failed": "Connect failed, retrying...",
        "bt_error": "Bluetooth not ready",
        "reconnecting": "Reconnecting {}...",
        "hint_exit": "Exit",
        "hint_rescan": "D Rescan",
        "hint_mapping": "Key Map",
        "hint_disconnect": "Disc",
        "key_mapping": "Key Map",
        "back": "Back",
    },
})

# ===================== 常量 =====================
AUTO_EXIT_SEC = 1800  # 30 分钟无输入自动退出
GAMEPAD_KEYWORDS = [
    "xbox", "microsoft",
    "wireless controller", "pro controller",
    "gamepad", "controller",
    "8bitdo", "joystick",
    "tl_",        # 天龙/腾龙系列: TL_0002E13CC2067322 等
    "gp",         # 部分国产手柄前缀
    "bm769",      # BM769 2.4G 手柄（主用设备）
    "ipega", "betop", "flydigi", "razer", "dualsense", "dualshock",
]
SCAN_DURATION = 10       # 单次扫描时长（秒）
SCAN_RETRY_INTERVAL = 5  # 未找到设备的重试间隔（秒）
MAX_SCAN_RETRY = 12      # 最大重试次数

_LAUNCHER_ASSETS = os.path.dirname(T_Asset.bg_image)
DEMO_ICON = os.path.join(_LAUNCHER_ASSETS, "demo_gamepad.png")
_APP_BG_IMAGE = "/home/pi/luwu-os/assets/images/app_bg.png"


# ===================== 持久化 bluetoothctl 会话 =====================
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
BT_LOG_FILE = "/tmp/bt_gamepad.log"


def _bt_log(msg: str):
    """写入蓝牙调试日志文件"""
    try:
        ts = time.strftime("%m-%d %H:%M:%S")
        with open(BT_LOG_FILE, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


class BtSession:
    """维护一个持久 bluetoothctl 进程，保证 agent 注册不丢失
    
    关键：bluetoothctl 的 agent 命令需要进程保持运行才能维护 D-Bus 对象。
    之前每次 subprocess.run 都会让进程退出 → agent 注销 → 配对失败。
    这个类用一个 Popen 进程一直活着，所有命令通过 stdin 发送。
    
    健壮性改进：
    - 日志写入 /tmp/bt_gamepad.log 方便排查
    - ANSI 转义码过滤，避免干扰关键词匹配
    - 关键词命中后继续读 0.8s 捕获后续输出
    - 会话健康检查 + 自动重启 (ensure_alive)
    - 连接频率限制，避免 bluetoothd "Operation already in progress"
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._ready = False
        self._master_fd = None
        self._last_connect_ts = {}
        self._connect_min_interval = 3.0

    # ── 启动 / 停止 ──────────────────────────────────────────────

    def start(self):
        _bt_log("BtSession.start() called")
        print("[bt_gamepad] starting persistent bluetoothctl session (pty)...", flush=True)
        self._master_fd, slave_fd = pty.openpty()
        self._proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        # 等待 bluetoothctl 启动并出现提示符
        init_out = self._read_until(8)
        _bt_log(f"init output: {init_out[:200]}")
        self._ready = True
        # 初始设置
        self._send("power on", 5)
        self._send("pairable on", 5)
        out = self._send("agent NoInputNoOutput", 5)
        print(f"[bt_gamepad] agent: {out.strip()[:150]}", flush=True)
        _bt_log(f"agent output: {out.strip()[:200]}")
        clean = _ANSI_RE.sub('', out).lower()
        if "failed" in clean and "already" not in clean:
            print("[bt_gamepad] !! agent register FAILED", flush=True)
            _bt_log("WARNING: agent registration failed")
            self._ready = False
        else:
            print("[bt_gamepad] agent registered OK", flush=True)
            _bt_log("agent registered OK")
        self._send("default-agent", 5)
        print("[bt_gamepad] BtSession ready", flush=True)
        _bt_log("BtSession ready")

    def stop(self):
        _bt_log("BtSession.stop() called")
        self._ready = False
        if hasattr(self, '_master_fd') and self._master_fd is not None:
            try:
                os.write(self._master_fd, b"scan off\nquit\n")
            except Exception:
                pass
            try:
                os.close(self._master_fd)
            except Exception:
                pass
            self._master_fd = None
        if self._proc:
            try:
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()
            self._proc = None
            print("[bt_gamepad] BtSession stopped", flush=True)
            _bt_log("BtSession stopped")

    def is_alive(self):
        alive = self._ready and self._proc and self._proc.poll() is None
        if not alive and self._ready:
            _bt_log("WARNING: BtSession.is_alive()=False but _ready=True")
        return alive

    def ensure_alive(self):
        """如果会话挂了，自动重启"""
        if not self.is_alive():
            _bt_log("BtSession dead, auto-restarting...")
            print("[bt_gamepad] BtSession dead, restarting...", flush=True)
            self.stop()
            time.sleep(1)
            self.start()
            return self.is_alive()
        return True

    # ── 底层通信（PTY 读写）───────────────────────────────────

    def _read_until(self, timeout=8, idle=0.5, wait_for=None):
        """读取输出。
        - 普通命令：连续读取，遇到 idle 秒没新数据就返回。
        - 慢命令(pair/connect)：传入 wait_for=["successful","failed"] 等关键词，
          只有匹配到关键词 或 超时 才返回。关键词命中后继续读 0.8s 捕获后续状态。
        """
        buf = ""
        deadline = time.time() + timeout
        last_data = time.time()
        hit_keyword = False
        while time.time() < deadline:
            r, _, _ = select.select([self._master_fd], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(self._master_fd, 4096).decode("utf-8", errors="replace")
                except Exception:
                    break
                if chunk:
                    buf += chunk
                    last_data = time.time()
                    # 如果有关键词等待，检查是否命中（先清理 ANSI 码再匹配）
                    if wait_for and not hit_keyword:
                        clean = _ANSI_RE.sub('', buf).lower()
                        if any(kw in clean for kw in wait_for):
                            hit_keyword = True
                            # 命中后给 0.8s 的 grace period 继续读后续输出
                            deadline = min(deadline, time.time() + 0.8)
            else:
                # 没新数据，检查静默期（仅在无 wait_for 或已命中关键词时使用）
                if (not wait_for or hit_keyword) and buf and (time.time() - last_data) > idle:
                    break
        return buf

    def _send(self, cmd: str, timeout=8, idle=0.5, wait_for=None):
        """发送命令，返回完整输出。wait_for: 关键词列表，命中才返回。"""
        with self._lock:
            if not self.is_alive():
                _bt_log(f"SKIP (session dead): {cmd}")
                print(f"[bt_gamepad] !! session dead, skip: {cmd}", flush=True)
                return ""
            _bt_log(f"BT> {cmd}")
            print(f"[bt_gamepad] BT> {cmd}", flush=True)
            try:
                os.write(self._master_fd, (cmd + "\n").encode())
            except Exception as e:
                _bt_log(f"WRITE ERROR: {e}")
                print(f"[bt_gamepad] !! write error: {e}", flush=True)
                return ""
            out = self._read_until(timeout, idle, wait_for=wait_for)
            # 精简输出（过滤 ANSI 和提示行）
            for line in out.strip().splitlines():
                line = line.strip()
                if line and "[bluetoothctl]" not in line:
                    clean_line = _ANSI_RE.sub('', line).strip()
                    if clean_line:
                        print(f"[bt_gamepad] BT< {clean_line[:200]}", flush=True)
            _bt_log(f"BT< ({len(out)} bytes) {_ANSI_RE.sub('', out).strip()[:300]}")
            return out

    # ── 高级操作 ──────────────────────────────────────────────────

    def show(self):
        return self._send("show", 5)

    def devices(self):
        return self._send("devices", 5)

    def info(self, mac: str):
        return self._send(f"info {mac}", 5)

    def remove(self, mac: str):
        """移除设备（用于重新配对）"""
        return self._send(f"remove {mac}", 5)

    def unblock(self, mac: str):
        """解锁设备"""
        return self._send(f"unblock {mac}", 5)

    def scan(self, duration: int = SCAN_DURATION):
        """扫描指定时长后关闭"""
        # 先确保之前的扫描已关闭（处理异常退出残留）
        self._send("scan off", timeout=2, idle=0.3)
        time.sleep(0.3)
        # 启动扫描
        self._send("scan on", timeout=2, idle=0.3)
        # 扫描期间持锁后台消费 stdout，避免后续命令读到脱起的扫描输出
        with self._lock:
            deadline = time.time() + duration
            while time.time() < deadline:
                r, _, _ = select.select([self._master_fd], [], [], 0.5)
                if r:
                    try:
                        os.read(self._master_fd, 4096)
                    except Exception:
                        break
        self._send("scan off", timeout=2, idle=0.3)

    def pair(self, mac: str, timeout=25):
        return self._send(f"pair {mac}", timeout,
                          wait_for=["pairing successful", "failed to pair",
                                    "already exists", "not available",
                                    "authentication failed", "canceled"])

    def trust(self, mac: str):
        return self._send(f"trust {mac}", 5)

    def disconnect(self, mac: str, timeout=10):
        """断开指定设备，忽略设备不存在等错误"""
        return self._send(f"disconnect {mac}", timeout,
                          wait_for=["successful", "disconnected",
                                    "not connected", "not available",
                                    "failed", "device"])

    def connect(self, mac: str, timeout=20):
        # 频率限制：同一设备两次 connect 之间至少间隔 _connect_min_interval 秒
        now = time.time()
        last = self._last_connect_ts.get(mac, 0)
        if now - last < self._connect_min_interval:
            wait = self._connect_min_interval - (now - last)
            _bt_log(f"RATE LIMIT: waiting {wait:.1f}s before connect {mac}")
            print(f"[bt_gamepad] rate limit: waiting {wait:.1f}s before connect", flush=True)
            time.sleep(wait)
        self._last_connect_ts[mac] = time.time()
        return self._send(f"connect {mac}", timeout,
                          wait_for=["connection successful", "failed to connect",
                                    "already connected", "not available",
                                    "host is down", "operation already",
                                    "refused", "timed out", "br-connection"])


# 全局单例
_bt = BtSession()


# ===================== 工具函数（基于 BtSession）=====================

def bt_setup():
    """初始化蓝牙会话（启动持久进程）"""
    if not _bt.is_alive():
        _bt.start()

def bt_is_powered() -> bool:
    return "Powered: yes" in _bt.show()

def bt_list_devices():
    """返回 [(mac, name), ...]"""
    out = _bt.devices()
    items = []
    for line in out.splitlines():
        m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
        if m:
            items.append((m.group(1), m.group(2).strip()))
    return items

def bt_info(mac: str) -> str:
    return _bt.info(mac)

def bt_is_gaming_device(mac: str) -> bool:
    info = bt_info(mac)
    if "Icon: input-gaming" in info:
        return True
    m = re.search(r"Class:\s+0x([0-9a-fA-F]+)", info)
    if m:
        cls = int(m.group(1), 16)
        major = (cls >> 8) & 0x1F
        minor = (cls >> 2) & 0x3F
        if major == 5 and minor in (4, 8):
            return True
    return False

def bt_is_connected(mac: str) -> bool:
    return "Connected: yes" in bt_info(mac)

def bt_is_paired(mac: str) -> bool:
    return "Paired: yes" in bt_info(mac)

def bt_remove(mac: str):
    """移除已配对设备（用于 re-pair）"""
    return _bt.remove(mac)

def bt_unblock(mac: str):
    return _bt.unblock(mac)

def bt_pair(mac: str) -> bool:
    out = _bt.pair(mac, timeout=25)
    clean = _ANSI_RE.sub('', out).lower()
    _bt_log(f"bt_pair({mac}) result: {clean[:200]}")
    return "successful" in clean or "already" in clean

def bt_trust(mac: str) -> bool:
    out = _bt.trust(mac)
    clean = _ANSI_RE.sub('', out).lower()
    _bt_log(f"bt_trust({mac}) result: {clean[:200]}")
    return "succeeded" in clean or "changing" in clean

def bt_connect(mac: str) -> bool:
    out = _bt.connect(mac, timeout=20)
    clean = _ANSI_RE.sub('', out).lower()
    _bt_log(f"bt_connect({mac}) result: {clean[:200]}")
    # "host is down" / "operation already" 表示暂时失败，下次可重试
    return "successful" in clean or "already" in clean

def bt_disconnect(mac: str):
    """断开蓝牙设备连接"""
    out = _bt.disconnect(mac)
    clean = _ANSI_RE.sub('', out).lower()
    _bt_log(f"bt_disconnect({mac}) result: {clean[:200]}")
    return out

def bt_scan(duration: int = SCAN_DURATION):
    _bt.scan(duration)

def is_gamepad_name(name: str) -> bool:
    if not name:
        return False
    low = name.lower()
    return any(kw in low for kw in GAMEPAD_KEYWORDS)

def find_gamepads():
    """返回当前已知设备里的所有手柄 [(mac, name, connected), ...]"""
    result = []
    for mac, name in bt_list_devices():
        if is_gamepad_name(name):
            result.append((mac, name, bt_is_connected(mac)))
        elif bt_is_gaming_device(mac):
            result.append((mac, name, bt_is_connected(mac)))
    return result


# ===================== BT 后台线程 =====================
class BTWorker(QThread):
    """后台线程：扫描、自动配对、自动连接、断线重连"""

    status_changed = Signal(str, str)   # (key, detail)
    gamepad_ready = Signal(str, str)    # (mac, name) — 手柄已就绪可控制
    gamepad_lost = Signal()             # 手柄断开

    def __init__(self):
        super().__init__()
        self._running = True
        self._force_rescan = False
        self._force_disconnect = False
        self._current_mac = ""     # 当前连接的手柄 MAC，用于主动断开

    def stop(self):
        self._running = False

    def request_rescan(self):
        self._force_rescan = True

    def request_disconnect(self):
        """主动断开当前连接的手柄"""
        self._force_disconnect = True

    # ---- 主流程 ----
    def run(self):
        # 1. 初始化蓝牙
        _bt_log("BTWorker.run() started")
        self.status_changed.emit("init", "")
        bt_setup()
        if not bt_is_powered():
            _bt_log("ERROR: Bluetooth not powered")
            self.status_changed.emit("bt_error", "")
            return

        # 2. 主循环：连 → 维持 → 断 → 重扫
        while self._running:
            # 确保 BT 会话还活着
            _bt.ensure_alive()

            # 2.1 优先复用已连接的手柄
            # 但如果用户主动触发了重扫，不复用，先断开再扫描
            mac, name = self._find_connected()
            if mac and not self._force_rescan:
                _bt_log(f"Found already-connected gamepad: {name} ({mac})")
                self.status_changed.emit("already", name)
                self.gamepad_ready.emit(mac, name)
                self._monitor(mac, name)
                if not self._running:
                    break
                continue

            # 如果 force_rescan 且手柄仍连着，先断开它
            if mac and self._force_rescan:
                _bt_log(f"force_rescan: disconnecting {name} ({mac}) before scan")
                bt_disconnect(mac)
                # 等手柄真正断开（最多 3 秒）
                for _ in range(6):
                    if not bt_is_connected(mac):
                        break
                    time.sleep(0.5)
                _bt_log(f"force_rescan: disconnect done, connected={bt_is_connected(mac)}")

            # 清除 force_rescan 标志，后续流程正常执行
            self._force_rescan = False

            # 2.2 尝试直接连接已知已配对但未连接的手柄（无需扫描）
            mac, name = self._find_paired_disconnected()
            if mac:
                _bt_log(f"Found paired-but-disconnected gamepad: {name} ({mac}), trying connect")
                self.status_changed.emit("connecting", name)
                if self._try_connect_paired(mac, name):
                    self._monitor(mac, name)
                    continue
                _bt_log(f"Direct connect to {name} failed, falling through to scan")

            # 2.3 扫描 + 配对 + 连接
            ok = self._scan_and_connect()
            if not ok and self._running:
                # 连续多次失败，停顿一段时间后重试
                for _ in range(5):
                    if not self._running or self._force_rescan:
                        break
                    time.sleep(1)

    def _find_connected(self):
        for mac, name, connected in find_gamepads():
            if connected:
                return mac, name
        return None, None

    def _find_paired_disconnected(self):
        """找到已配对/信任但未连接的手柄（优先尝试直连）"""
        for mac, name, connected in find_gamepads():
            if not connected and bt_is_paired(mac):
                return mac, name
        return None, None

    def _try_connect_paired(self, mac: str, name: str) -> bool:
        """对已配对设备尝试直连，最多重试 2 次（轻量级，不会 remove 设备）"""
        for attempt in range(2):
            if not self._running:
                return False
            if attempt > 0:
                delay = 3  # 重试前等 3 秒
                _bt_log(f"  retry connect {name} attempt {attempt+1}/2, delay={delay}s")
                print(f"[bt_gamepad] reconnect attempt {attempt+1}/2 for {name}, waiting {delay}s...", flush=True)
                for _ in range(delay):
                    if not self._running:
                        return False
                    time.sleep(1)

            if bt_connect(mac):
                # 验证连接
                for _ in range(8):
                    if bt_is_connected(mac):
                        self.status_changed.emit("connected", name)
                        self.gamepad_ready.emit(mac, name)
                        _bt_log(f"SUCCESS: connected to {name} after {attempt+1} attempts")
                        return True
                    time.sleep(1)
                _bt_log(f"  connect reported success but not confirmed after 8s")
            else:
                _bt_log(f"  connect attempt {attempt+1} failed")

        # 直连失败不删除设备，留给扫描+配对流程处理
        _bt_log(f"  direct connect failed for {name}, will try scan+connect")
        return False

    def _scan_and_connect(self) -> bool:
        retry = 0
        while self._running and retry < MAX_SCAN_RETRY:
            self._force_rescan = False

            self.status_changed.emit("scanning", "")
            bt_scan(SCAN_DURATION)
            if not self._running:
                return False

            gamepads = find_gamepads()
            # 已连接的优先（极少见，但保险）
            for mac, name, conn in gamepads:
                if conn:
                    self.status_changed.emit("connected", name)
                    self.gamepad_ready.emit(mac, name)
                    self._monitor(mac, name)
                    return True

            # 未连接的尝试配对 + 连接
            for mac, name, _ in gamepads:
                if not self._running:
                    return False
                if self._try_pair_connect(mac, name):
                    self._monitor(mac, name)
                    return True

            retry += 1
            remaining = max(1, MAX_SCAN_RETRY - retry)
            # 多次完全找不到手柄 → 提示用户检查设备
            if len(gamepads) == 0 and retry >= 3:
                self.status_changed.emit("not_available", str(SCAN_RETRY_INTERVAL))
            else:
                self.status_changed.emit("not_found", str(SCAN_RETRY_INTERVAL))
            for _ in range(SCAN_RETRY_INTERVAL):
                if not self._running or self._force_rescan:
                    break
                time.sleep(1)
            if self._force_rescan:
                retry = 0

        return False

    def _try_pair_connect(self, mac: str, name: str) -> bool:
        # 额外尝试次数（已配对设备）
        max_connect_retries = 2

        # 已配对则跳过 pair，直接连；未配对的先配对
        if not bt_is_paired(mac):
            self.status_changed.emit("pairing", name)
            if not bt_pair(mac):
                self.status_changed.emit("pair_failed", name)
                _bt_log(f"pair failed for {name}")
                time.sleep(1)
                return False
            bt_trust(mac)
            time.sleep(1.5)  # 配对后给系统一点时间
        else:
            # 已配对设备：先 unlock 确保没有被 block
            bt_unblock(mac)
            time.sleep(0.3)

        # 连接尝试（最多 max_connect_retries 次）
        for attempt in range(max_connect_retries):
            if not self._running:
                return False

            self.status_changed.emit("connecting", name)
            if bt_connect(mac):
                # 连接结果验证（最多等 8 秒）
                for _ in range(8):
                    if bt_is_connected(mac):
                        self.status_changed.emit("connected", name)
                        self.gamepad_ready.emit(mac, name)
                        _bt_log(f"SUCCESS: connected to {name} (attempt {attempt+1})")
                        return True
                    time.sleep(1)
                _bt_log(f"  connect reported success but not confirmed after 8s")
            else:
                _bt_log(f"  connect failed for {name} (attempt {attempt+1}/{max_connect_retries})")

            # 还有重试机会
            if attempt < max_connect_retries - 1:
                wait = (attempt + 1) * 2  # 2s, 4s
                _bt_log(f"  waiting {wait}s before retry...")
                print(f"[bt_gamepad] connect retry {attempt+2}/{max_connect_retries} for {name} in {wait}s...", flush=True)
                for _ in range(wait):
                    if not self._running:
                        return False
                    time.sleep(1)

        # 所有尝试都失败 → remove + 下次扫描重新配对
        _bt_log(f"  all connect attempts failed for {name}, removing to re-pair")
        print(f"[bt_gamepad] removing {name} to attempt fresh re-pair...", flush=True)
        bt_remove(mac)
        time.sleep(0.5)

        self.status_changed.emit("connect_failed", name)
        return False

    def _monitor(self, mac: str, name: str):
        """连接成功后监控掉线，掉线时尝试快速重连。同时响应断开/重扫请求。"""
        self._current_mac = mac
        disconnect_count = 0
        while self._running and not self._force_rescan:
            time.sleep(3)
            
            # 主动断开：先通过 bluetoothctl 断开蓝牙
            if self._force_disconnect:
                _bt_log(f"monitor: user requested disconnect for {name} ({mac})")
                print(f"[bt_gamepad] user requested disconnect {name}", flush=True)
                bt_disconnect(mac)
                self._current_mac = ""
                self.status_changed.emit("disconnected", name)
                self.gamepad_lost.emit()
                break
            
            if not bt_is_connected(mac):
                disconnect_count += 1
                _bt_log(f"monitor: {name} disconnected (count={disconnect_count})")
                
                # 前 2 次掉线尝试快速重连
                if disconnect_count <= 2:
                    self.status_changed.emit("reconnecting", name)
                    _bt_log(f"  attempting quick reconnect #{disconnect_count}")
                    # 通知上层控制器已丢失，停止当前控制器线程
                    self.gamepad_lost.emit()
                    if bt_connect(mac):
                        for _ in range(5):
                            if bt_is_connected(mac):
                                _bt_log(f"  quick reconnect succeeded")
                                self.status_changed.emit("connected", name)
                                # 通知上层重新启动控制器线程
                                self.gamepad_ready.emit(mac, name)
                                disconnect_count = 0
                                break
                            time.sleep(0.5)
                        else:
                            _bt_log(f"  quick reconnect failed")
                            continue
                        continue
                    else:
                        _bt_log(f"  quick reconnect failed")
                        time.sleep(1)
                        continue
                
                # 多次掉线 → 通知上层重启扫描
                self._current_mac = ""
                self.status_changed.emit("disconnected", name)
                self.gamepad_lost.emit()
                time.sleep(2)
                return

        # force_rescan 触发的退出：不在这里断开，交给主循环统一处理
        # （主循环在进入扫描前会 disconnect + 等待断开确认）
        if self._current_mac:
            # 仅 force_rescan 正常退出的路径（_force_disconnect 已在上面 break 前处理）
            self._current_mac = ""
            self.gamepad_lost.emit()
        # 重置标志，让主循环进入扫描流程
        if self._force_disconnect:
            self._force_disconnect = False
            self._force_rescan = True  # 断开后自动开始扫描


# ===================== 控制器线程 =====================
# 预初始化 xgolib 单例，避免每次重连都重新初始化串口（耗时 1-2 秒）
_xgo_instance = None
_xgo_device_type = None


def _ensure_xgo():
    """懒初始化 xgolib 单例，全局复用"""
    global _xgo_instance, _xgo_device_type
    if _xgo_instance is not None:
        return _xgo_instance, _xgo_device_type
    try:
        import xgolib
        print("[bt_gamepad] initializing xgolib (one-time)...", flush=True)
        _xgo_instance = xgolib.XGO()
        fw = getattr(_xgo_instance, "version", "")
        if fw and fw[0] == "R":
            _xgo_device_type = "xgorider"
        elif fw and fw[0] == "L":
            _xgo_device_type = "xgolite"
        else:
            _xgo_device_type = "xgomini"
        print(f"[bt_gamepad] xgolib ready: {_xgo_device_type} (fw={fw})", flush=True)
    except ImportError:
        print("[bt_gamepad] xgolib not installed, debug mode", flush=True)
    except Exception as e:
        print(f"[bt_gamepad] xgolib init failed: {e}", flush=True)
    return _xgo_instance, _xgo_device_type


class ControllerThread(threading.Thread):
    """后台运行 libs/gamepad_config/gamepad_controller.py 中的 XGOController"""

    def __init__(self):
        super().__init__(daemon=True, name="xgo-gamepad-ctrl")
        self._controller = None

    def run(self):
        try:
            gp_dir = "/home/pi/luwu-os/libs/gamepad_config"
            if gp_dir not in sys.path:
                sys.path.insert(0, gp_dir)
            import gamepad_controller as gc
            # 强制纠正 CONFIG_FILE 路径（原文件含历史遗留错误）
            gc.CONFIG_FILE = os.path.join(gp_dir, "mappings.json")
            self._controller = gc.XGOController()
            # 复用全局 xgo 单例，跳过慢速串口初始化
            xgo, dev_type = _ensure_xgo()
            if xgo:
                self._controller.xgo = xgo
                self._controller.device_type = dev_type
            else:
                # 如果单例不可用，回退到普通初始化
                self._controller._init_xgo()
            self._controller._load_mapping()
            self._controller._running = True
            self._controller._start_config_watcher()
            # 直接进入手柄搜索+事件循环（跳过 run() 里的 _init_xgo）
            self._run_gamepad_loop()
        except Exception as e:
            print(f"[bt_gamepad] controller error: {e}", flush=True)
            import traceback
            traceback.print_exc()

    def _run_gamepad_loop(self):
        """手柄主循环：查找设备 + 进入事件循环"""
        c = self._controller
        import gamepad_controller as gc
        while c._running:
            dev = c._find_gamepad()
            if not dev:
                gc.log.warning("未找到手柄，2秒后重试...")
                time.sleep(2)
                continue
            c._gamepad_dev = dev
            if c._is_ble_gatt_gamepad(dev.name):
                gc.log.info(f"检测到 BLE GATT 手柄: {dev.name}，启用 BLE 路径")
                try:
                    c._run_ble_loop(dev)
                except Exception as e:
                    gc.log.error(f"[BLE] 循环异常: {e}")
                    time.sleep(1)
            else:
                try:
                    c._run_evdev_loop(dev)
                except Exception as e:
                    gc.log.error(f"[evdev] 循环异常: {e}")
                    time.sleep(1)
            c._gamepad_dev = None
            c._stop_movement()
            time.sleep(1)

    def stop(self):
        c = self._controller
        if not c:
            return
        try:
            c._running = False
            c._stop_movement()
            # 强制关闭 evdev 设备，让阻塞在 read_loop() 中的线程立即退出
            if c._gamepad_dev:
                try:
                    c._gamepad_dev.close()
                except Exception:
                    pass
                c._gamepad_dev = None
        except Exception:
            pass


# ===================== UI =====================
class BTGamepadPage(AppFrame):
    def __init__(self):
        super().__init__()
        # 与 settings/AI/hotspot 同款应用背景
        _pix = QPixmap(_APP_BG_IMAGE)
        if not _pix.isNull():
            self._bg_pix = _pix
            self.update()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._first_paint_logged = False

        # 手柄触摸板会被系统识别为鼠标，导致光标显示，
        # 这里强制隐藏光标（手柄 App 不需要鼠标光标）
        self._cursor_timer.stop()
        self._cursor_hidden = True
        self.setCursor(_invisible_cursor())

        self._bt_worker: BTWorker | None = None
        self._ctrl_thread: ControllerThread | None = None

        # ---- 标题 ----
        self.setTitle(_T("title"))

        # ---- QR 映射页（覆盖层，初始隐藏）----
        self._qr_page = QRMappingPage(self)
        self._qr_page.go_back = self._hide_qr_page
        self._qr_page.hide()

        # ---- 图标 ----
        self.icon_label = QLabel(self)
        pix = QPixmap(DEMO_ICON)
        if not pix.isNull():
            self.icon_label.setPixmap(pix.scaled(
                88, 88,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet(T_qss.transparent())

        # accent 装饰线
        self.accent_line = QFrame(self)
        self.accent_line.setFixedSize(60, 2)
        self.accent_line.setStyleSheet(
            f"background-color: {T_Color.accent}; border: none;"
        )

        # 设备名
        self.device_label = QLabel("", self)
        self.device_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.device_label.setStyleSheet(T_qss.text("subtitle"))

        # 状态 chip
        self.status_label = QLabel(_T("init"), self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(T_qss.chip("muted"))

        # 子状态（控制器是否启动）
        self.sub_label = QLabel("", self)
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setStyleSheet(T_qss.text("body", color=T_Color.accent))

        # ---- 主布局（垂直居中）----
        center = QWidget(self)
        center.setStyleSheet(T_qss.transparent())
        v = QVBoxLayout(center)
        v.setContentsMargins(0, 0, 0, 0)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.sm)
        v.addWidget(self.accent_line, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.md)
        v.addWidget(self.device_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.xs)
        v.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.xs)
        v.addWidget(self.sub_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self._center = center

        # ---- 角标 ----
        self.setCornerHints(
            tl=(_T("hint_mapping"), T_Asset.icon_left),
            tr=(_T("hint_disconnect"), T_Asset.icon_right),
            bl=(_T("hint_exit"), T_Asset.icon_back),
            br=(_T("hint_rescan"), T_Asset.icon_enter),
        )

        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)
        QTimer.singleShot(200, self._start_bt)

    # ---- 布局 ----
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        top = max(28, h * 14 // 100)
        bottom = max(20, h * 8 // 100)
        self._center.setGeometry(0, top, w, h - top - bottom)

        # QR 页面全屏覆盖
        if self._qr_page:
            self._qr_page.setGeometry(0, 0, w, h)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")

    # ---- BT worker 启停 ----
    def _start_bt(self):
        if self._bt_worker and self._bt_worker.isRunning():
            return
        self._bt_worker = BTWorker()
        self._bt_worker.status_changed.connect(self._on_status)
        self._bt_worker.gamepad_ready.connect(self._on_ready)
        self._bt_worker.gamepad_lost.connect(self._on_lost)
        self._bt_worker.start()
        # 同时启动键位映射 Web 服务器
        try:
            mapping_server.start_server()
        except Exception as e:
            print(f"[bt_gamepad] mapping server start failed: {e}", flush=True)

    # ---- QR 映射页面显示/隐藏 ----
    def _show_qr_page(self):
        """显示 QR 码映射页面"""
        print("[bt_gamepad] showing QR mapping page", flush=True)
        # 确保服务器在运行
        if not mapping_server.is_running():
            try:
                mapping_server.start_server()
            except Exception as e:
                print(f"[bt_gamepad] mapping server start failed: {e}", flush=True)
        # 重新生成 QR（IP 可能变了），捕获异常防止白屏
        try:
            self._qr_page._generate()
        except Exception as e:
            print(f"[bt_gamepad] QR generate failed: {e}", flush=True)
        self._qr_page.show()
        self._qr_page.raise_()
        self._qr_page.setFocus()
        # 隐藏主页面元素
        self._center.hide()
        self.icon_label.hide()
        self.accent_line.hide()
        self.device_label.hide()
        self.status_label.hide()
        self.sub_label.hide()
        for c in self._corners.values():
            c.hide()

    def _hide_qr_page(self):
        """隐藏 QR 码映射页面，返回主界面"""
        print("[bt_gamepad] hiding QR mapping page", flush=True)
        self._qr_page.hide()
        # 恢复主页面元素
        self._center.show()
        self.icon_label.show()
        self.accent_line.show()
        self.device_label.show()
        self.status_label.show()
        self.sub_label.show()
        for c in self._corners.values():
            c.show()
        self.setFocus()

    # ---- 状态更新 ----
    def _on_status(self, key: str, detail: str):
        muted = T_qss.chip("muted")
        success = T_qss.chip("success")

        if key == "init":
            self.device_label.setText("")
            self.status_label.setText(_T("init"))
            self.status_label.setStyleSheet(muted)
        elif key == "scanning":
            self.device_label.setText("")
            self.status_label.setText(_T("scanning"))
            self.status_label.setStyleSheet(muted)
        elif key == "pairing":
            self.device_label.setText(detail)
            self.status_label.setText(_T("pairing", detail))
            self.status_label.setStyleSheet(muted)
        elif key == "connecting":
            self.device_label.setText(detail)
            self.status_label.setText(_T("connecting", detail))
            self.status_label.setStyleSheet(muted)
        elif key == "connected":
            self.device_label.setText(detail)
            self.status_label.setText(_T("connected", detail))
            self.status_label.setStyleSheet(success)
        elif key == "already":
            self.device_label.setText(detail)
            self.status_label.setText(_T("already"))
            self.status_label.setStyleSheet(success)
        elif key == "disconnected":
            self.sub_label.setText("")
            self.device_label.setText("")
            self.status_label.setText(_T("disconnected"))
            self.status_label.setStyleSheet(muted)
        elif key == "not_found":
            self.device_label.setText("")
            self.status_label.setText(_T("not_found", detail))
            self.status_label.setStyleSheet(muted)
        elif key == "pair_failed":
            self.status_label.setText(_T("pair_failed"))
            self.status_label.setStyleSheet(muted)
        elif key == "connect_failed":
            self.status_label.setText(_T("connect_failed"))
            self.status_label.setStyleSheet(muted)
        elif key == "reconnecting":
            self.device_label.setText(detail)
            self.status_label.setText(_T("reconnecting", detail))
            self.status_label.setStyleSheet(muted)
        elif key == "not_available":
            self.device_label.setText("")
            self.sub_label.setText(_T("not_available"))
            self.status_label.setText(_T("not_found", detail))
            self.status_label.setStyleSheet(muted)
        elif key == "bt_error":
            self.status_label.setText(_T("bt_error"))
            self.status_label.setStyleSheet(muted)

    def _on_ready(self, mac: str, name: str):
        """手柄连接就绪 → 启动控制器"""
        self._start_controller()
        self.sub_label.setText(_T("ready"))

    def _on_lost(self):
        """手柄断开 → 停止控制器"""
        self._stop_controller()
        self.sub_label.setText("")

    # ---- 控制器启停 ----
    def _start_controller(self):
        # 确保旧线程已完全退出再启动新的
        if self._ctrl_thread is not None:
            if self._ctrl_thread.is_alive():
                print("[bt_gamepad] old controller still alive, waiting...", flush=True)
                self._ctrl_thread.stop()
                self._ctrl_thread.join(timeout=2.0)
                if self._ctrl_thread.is_alive():
                    print("[bt_gamepad] WARNING: old controller won't die, replacing anyway", flush=True)
            self._ctrl_thread = None

        self._ctrl_thread = ControllerThread()
        self._ctrl_thread.start()
        print("[bt_gamepad] controller started", flush=True)

    def _stop_controller(self):
        if self._ctrl_thread and self._ctrl_thread.is_alive():
            print("[bt_gamepad] signalling controller to stop...", flush=True)
            self._ctrl_thread.stop()
        # 不 join、不设 None，让 _start_controller 负责等待和清理
        print("[bt_gamepad] controller stop signalled", flush=True)

    # ---- 按键 ----
    def keyPressEvent(self, ev: QKeyEvent):
        key = ev.key()
        # 如果 QR 页面可见，先处理 QR 页面的返回
        if self._qr_page.isVisible():
            if key == Qt.Key.Key_Back:
                print("[bt_gamepad] C -> back from QR", flush=True)
                self._hide_qr_page()
                return
            # 其他按键传给 QR 页面
            self._qr_page.keyPressEvent(ev)
            return

        if key == Qt.Key.Key_Back:
            print("[bt_gamepad] C -> exit", flush=True)
            self.close()
        elif key == Qt.Key.Key_Left:
            # A 键 → 打开键位映射（物理左上角）
            print("[bt_gamepad] A -> key mapping", flush=True)
            self._show_qr_page()
        elif key == Qt.Key.Key_Right:
            # B 键 → 断开当前蓝牙手柄（物理右上角）
            print("[bt_gamepad] B -> disconnect", flush=True)
            self._disconnect_gamepad()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            print("[bt_gamepad] D -> rescan", flush=True)
            self._stop_controller()
            if self._bt_worker:
                self._bt_worker.request_rescan()
            else:
                self._start_bt()

    # ---- 主动断开手柄 ----
    def _disconnect_gamepad(self):
        """主动断开当前连接的手柄，停止控制器并自动开始扫描"""
        print("[bt_gamepad] disconnecting current gamepad...", flush=True)
        self._stop_controller()
        if self._bt_worker and self._bt_worker._current_mac:
            self._bt_worker.request_disconnect()
            self.sub_label.setText("")
            self.status_label.setText(_T("disconnected"))
            self.status_label.setStyleSheet(T_qss.chip("muted"))

    # ---- 退出清理 ----
    def closeEvent(self, ev):
        print("[bt_gamepad] closing", flush=True)
        self._stop_controller()
        if self._bt_worker:
            self._bt_worker.stop()
            self._bt_worker.quit()
            self._bt_worker.wait(3000)
            self._bt_worker = None
        # 停止持久 bluetoothctl 会话
        _bt.stop()
        # 停止键位映射 Web 服务器
        try:
            mapping_server.stop_server()
        except Exception:
            pass
        super().closeEvent(ev)


# ===================== 入口 =====================
def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    apply_app_palette(app)
    mark("QApplication created")

    w = BTGamepadPage()
    mark("widget constructed")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[bt_gamepad] exit rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
