#!/usr/bin/env python3
"""
PySide6 群组表演 (Group Performance) — 由 Luwu OS launcher 启动。
基于 MQTT 实现多台 XGO 机器狗同步表演。
C 键（左下物理键 → KEY_BACK）退出。
D 键（右下物理键 → KEY_RETURN）开始/停止表演。
"""
import os
import sys
import time
import signal
import json
import socket
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from subprocess import Popen, DEVNULL

# ===================== 阶段计时 =====================
T0 = time.monotonic()
_stages = []


def mark(name: str):
    ms = (time.monotonic() - T0) * 1000.0
    _stages.append((name, ms))
    print(f"[group][+{ms:7.1f}ms] {name}", flush=True)


mark("python entry")

# ===================== PySide6 导入 =====================
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea,
)

mark("PySide6 import done")

# ===================== MQTT =====================
import paho.mqtt.client as mqtt
try:
    from paho.mqtt.client import CallbackAPIVersion
    PAHO_V2 = True
except ImportError:
    PAHO_V2 = False

# ===================== 狗库 =====================
sys.path.insert(0, "/home/pi/lib")
from xgolib import XGO

mark("xgolib import done")

# ===================== i18n =====================
if "/home/pi/luwu-os" not in sys.path:
    sys.path.insert(0, "/home/pi/luwu-os")
try:
    from libs.i18n import Translator as _Translator
    _T = _Translator({
        "cn": {
            "title": "🎭 群组表演",
            "room_connecting": "房间: 连接中...",
            "room": "🏠 房间: {}",
            "devices": "📡 在线设备: {}",
            "devices_init": "在线设备: 0",
            "mqtt_connecting": "MQTT: 连接中...",
            "mqtt_connected": "🟢 MQTT 已连接",
            "mqtt_disconnected": "🔴 MQTT 未连接",
            "network_warn": "⚠ 网络未连接",
            "dog_type": "设备型号: {}",
            "status_idle": "就绪 - 等待开始",
            "status_idle2": "⏸ 就绪 - 等待开始",
            "status_perform": "🎬 表演中...",
            "action_list_title": "— 动作列表 —",
            "corner_switch": "◀ ▶ : 切换",
            "corner_start": "D: 开始表演",
            "corner_stop": "D: 停止表演",
            "corner_exit": "C: 退出",
        },
        "en": {
            "title": "🎭 Group Performance",
            "room_connecting": "Room: connecting...",
            "room": "🏠 Room: {}",
            "devices": "📡 Online devices: {}",
            "devices_init": "Online devices: 0",
            "mqtt_connecting": "MQTT: connecting...",
            "mqtt_connected": "🟢 MQTT connected",
            "mqtt_disconnected": "🔴 MQTT disconnected",
            "network_warn": "⚠ No network",
            "dog_type": "Device: {}",
            "status_idle": "Ready - Waiting",
            "status_idle2": "⏸ Ready - Waiting",
            "status_perform": "🎬 Performing...",
            "action_list_title": "— Action List —",
            "corner_switch": "◀ ▶ : Switch",
            "corner_start": "D: Start",
            "corner_stop": "D: Stop",
            "corner_exit": "C: Exit",
        },
    })
except Exception:
    _T = lambda k, *a: k

# ===================== 常量 =====================
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
HEARTBEAT_INTERVAL = 5.0
DEVICE_TIMEOUT = 15.0
ACTION_PREP_DELAY = 3.0
AUTO_EXIT_SEC = 600

# ===================== 全局状态 =====================
exitmark = False
group_perform = False
action_plan = None
executor_running = False  # 防止重复启动执行线程
proc = None
proc_lock = threading.Lock()
known_devices = {}
known_devices_lock = threading.Lock()
mqtt_client = None
room_id = None
local_ip = None
dog = None
dog_type = "R"

# ===================== 动作组配置（按 dog_type 区分）=====================
ACTION_GROUPS = {
    "R": [
        {"id": 1, "name": "趴下", "duration": 3},
        {"id": 2, "name": "站立", "duration": 3},
    ],
    "L": [
        {"id": 1, "name": "趴下", "duration": 3},
        {"id": 2, "name": "站立", "duration": 3},
        {"id": 6, "name": "蹲起", "duration": 4},
        {"id": 129, "name": "中抓", "duration": 10},
        {"id": 7, "name": "转动Roll", "duration": 4},
        {"id": 8, "name": "转动Pitch", "duration": 4},
        {"id": 9, "name": "转动Yaw", "duration": 4},
        {"id": 10, "name": "三轴转动", "duration": 7},
        {"id": 11, "name": "撒尿", "duration": 7},
        {"id": 130, "name": "下抓", "duration": 10},
        {"id": 12, "name": "坐下", "duration": 5},
        {"id": 13, "name": "招手", "duration": 7},
        {"id": 14, "name": "伸懒腰", "duration": 10},
        {"id": 15, "name": "波浪", "duration": 6},
        {"id": 19, "name": "握手", "duration": 10},
        {"id": 22, "name": "张望", "duration": 8},
        {"id": 128, "name": "上抓", "duration": 10},
    ],
    "M": [
        {"id": 1, "name": "趴下", "duration": 3},
        {"id": 2, "name": "站立", "duration": 3},
        {"id": 6, "name": "蹲起", "duration": 4},
        {"id": 129, "name": "中抓", "duration": 10},
        {"id": 7, "name": "转动Roll", "duration": 4},
        {"id": 8, "name": "转动Pitch", "duration": 4},
        {"id": 9, "name": "转动Yaw", "duration": 4},
        {"id": 10, "name": "三轴转动", "duration": 7},
        {"id": 11, "name": "撒尿", "duration": 7},
        {"id": 130, "name": "下抓", "duration": 10},
        {"id": 12, "name": "坐下", "duration": 5},
        {"id": 13, "name": "招手", "duration": 7},
        {"id": 14, "name": "伸懒腰", "duration": 10},
        {"id": 15, "name": "波浪", "duration": 6},
        {"id": 19, "name": "握手", "duration": 10},
        {"id": 22, "name": "张望", "duration": 8},
        {"id": 128, "name": "上抓", "duration": 10},
    ],
    "W": [
        {"id": 1, "name": "趴下", "duration": 3},
        {"id": 2, "name": "站立", "duration": 3},
        {"id": 6, "name": "蹲起", "duration": 4},
        {"id": 129, "name": "中抓", "duration": 10},
        {"id": 7, "name": "转动Roll", "duration": 4},
        {"id": 8, "name": "转动Pitch", "duration": 4},
        {"id": 9, "name": "转动Yaw", "duration": 4},
        {"id": 10, "name": "三轴转动", "duration": 7},
        {"id": 11, "name": "撒尿", "duration": 7},
        {"id": 130, "name": "下抓", "duration": 10},
        {"id": 12, "name": "坐下", "duration": 5},
        {"id": 13, "name": "招手", "duration": 7},
        {"id": 14, "name": "伸懒腰", "duration": 10},
        {"id": 15, "name": "波浪", "duration": 6},
        {"id": 19, "name": "握手", "duration": 10},
        {"id": 22, "name": "张望", "duration": 8},
        {"id": 128, "name": "上抓", "duration": 10},
    ],
    "B": [
        {"id": 0, "name": "待机", "duration": 1},
    ]
}

actions_to_perform = []

# ===================== 工具函数 =====================


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('114.114.114.114', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


_external_ip_cache = None


def get_external_ip():
    global _external_ip_cache
    if _external_ip_cache:
        return _external_ip_cache
    import urllib.request

    # 国内 + 国际源混合，并发请求，谁先返回用谁
    apis = [
        # --- 国内源（国内用户秒回）---
        ("http://ip.3322.net",                   "plain"),   # 3322，返回纯IP
        ("http://cip.cc",                         "re"),      # 国内IP查询
        ("http://members.3322.org/dyndns/getip",  "plain"),   # 3322备用
        # --- 国际源（国外用户可用）---
        ("http://ipv4.icanhazip.com",             "plain"),   # icanhazip
        ("http://api.ipify.org",                  "plain"),   # ipify
    ]

    def _query(api, mode):
        """单个API查询，成功返回IP字符串，失败返回None"""
        try:
            req = urllib.request.Request(api, headers={"User-Agent": "curl/7.0"})
            resp = urllib.request.urlopen(req, timeout=5)
            raw = resp.read().decode().strip()
            if not raw:
                return None
            if mode == "re":
                m = re.search(r'\d+\.\d+\.\d+\.\d+', raw)
                if m:
                    return m.group(0)
                return None
            else:
                return raw
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=len(apis)) as pool:
        futures = {pool.submit(_query, api, mode): api for api, mode in apis}
        for fut in as_completed(futures):
            ip = fut.result()
            if ip:
                if ":" in ip:
                    ip = ip.replace(":", "-")
                _external_ip_cache = ip
                print(f"[ROOM] 外网IP(房间号): {ip}")
                # 取消剩余任务
                for f in futures:
                    f.cancel()
                return ip

    # 全部失败，回退
    lip = get_local_ip()
    fallback = ".".join(lip.split(".")[:3])
    _external_ip_cache = fallback
    print(f"[ROOM] 外网IP获取失败, 回退房间号: {fallback}")
    return fallback


def check_network():
    try:
        lip = get_local_ip()
        return lip != "127.0.0.1"
    except:
        return False


def kill_proc_safe(p, timeout=0.5):
    if p is None:
        return
    try:
        p.terminate()
        p.wait(timeout=timeout)
    except Exception:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass


def force_kill_all_mplayer():
    try:
        os.system("pkill -f 'mplayer.*dog.mp3' 2>/dev/null")
    except Exception as e:
        print(f"[ERROR] 强制 kill mplayer 失败: {e}")


# ===================== MQTT 群组通信 =====================


def get_topics():
    return {
        "presence": f"xgo/group/{room_id}/presence",
        "command":  f"xgo/group/{room_id}/command",
        "plan":     f"xgo/group/{room_id}/plan",
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    rc = reason_code if isinstance(reason_code, int) else reason_code.value
    if rc == 0:
        topics = get_topics()
        for t in topics.values():
            client.subscribe(t)
        print(f"[MQTT] 已连接并订阅房间: {room_id}")
        publish_presence("join")
    else:
        print(f"[MQTT] 连接失败, rc={rc}")


def on_disconnect(client, userdata, flags_or_rc, reason_code=None, properties=None):
    print(f"[MQTT] 连接断开")
    if not exitmark:
        print("[MQTT] 尝试自动重连...")


def on_message(client, userdata, msg):
    global group_perform, action_plan, exitmark
    try:
        data = json.loads(msg.payload.decode())
    except:
        return

    topic = msg.topic
    topics = get_topics()

    if topic == topics["presence"]:
        handle_presence(data)
    elif topic == topics["command"]:
        handle_command(data)
    elif topic == topics["plan"]:
        handle_plan(data)


def handle_presence(data):
    msg_type = data.get("type")
    ip = data.get("ip")
    if not ip:
        return

    if msg_type in ("join", "heartbeat"):
        with known_devices_lock:
            is_new = ip not in known_devices
            known_devices[ip] = {
                "dog_type": data.get("dog_type", "?"),
                "last_seen": time.time()
            }
        if is_new and msg_type == "join":
            print(f"[ROOM] 新设备加入: {ip} (型号: {data.get('dog_type', '?')})")
    elif msg_type == "leave":
        with known_devices_lock:
            known_devices.pop(ip, None)
        print(f"[ROOM] 设备离开: {ip}")


def handle_command(data):
    global group_perform, exitmark, action_plan, proc
    msg_type = data.get("type")

    if msg_type == "stop":
        print("[CMD] 收到停止指令")
        group_perform = False
        action_plan = None
        with proc_lock:
            if proc is not None:
                kill_proc_safe(proc)
                proc = None
        force_kill_all_mplayer()
    elif msg_type == "exit":
        print("[CMD] 收到退出指令")
        group_perform = False
        action_plan = None
        with proc_lock:
            if proc is not None:
                kill_proc_safe(proc)
                proc = None
        force_kill_all_mplayer()
        exitmark = True


def handle_plan(data):
    global group_perform, action_plan
    msg_type = data.get("type")
    if msg_type == "start":
        actions = data.get("actions", [])
        music_start = data.get("music_start", 0)
        if not actions:
            return
        action_plan = {
            "actions": actions,
            "music_start": music_start
        }
        group_perform = True
        print(f"[PLAN] 收到动作计划: {len(actions)} 个动作, 音乐开始于 {music_start:.3f}")


def publish_presence(msg_type):
    if not mqtt_client:
        return
    topics = get_topics()
    payload = json.dumps({
        "type": msg_type,
        "ip": local_ip,
        "dog_type": dog_type
    })
    mqtt_client.publish(topics["presence"], payload)


def publish_start():
    if not mqtt_client:
        return
    topics = get_topics()
    base_time = time.time() + ACTION_PREP_DELAY
    scheduled_actions = []
    current_time = base_time
    for act in actions_to_perform:
        scheduled_actions.append({
            "id": act["id"],
            "name": act["name"],
            "start_at": round(current_time, 3),
            "duration": act["duration"]
        })
        current_time += act["duration"] + 0.5

    payload = json.dumps({
        "type": "start",
        "actions": scheduled_actions,
        "music_start": round(base_time, 3)
    })
    mqtt_client.publish(topics["plan"], payload)
    print(f"[PLAN] 已发布动作计划, 共 {len(scheduled_actions)} 个动作")


def publish_stop():
    if not mqtt_client:
        return
    topics = get_topics()
    mqtt_client.publish(topics["command"], json.dumps({"type": "stop"}))


def publish_exit():
    if not mqtt_client:
        return
    topics = get_topics()
    mqtt_client.publish(topics["command"], json.dumps({"type": "exit"}))


def setup_mqtt():
    global mqtt_client, room_id, local_ip
    local_ip = get_local_ip()
    room_id = get_external_ip()

    client_id = f"xgo-{local_ip}-{int(time.time()) % 100000}"
    if PAHO_V2:
        mqtt_client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=client_id, protocol=mqtt.MQTTv311)
    else:
        mqtt_client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    topics = get_topics()
    will_payload = json.dumps({"type": "leave", "ip": local_ip})
    mqtt_client.will_set(topics["presence"], will_payload)

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        print(f"[MQTT] 正在连接 {MQTT_BROKER}:{MQTT_PORT} ...")
        return True
    except Exception as e:
        print(f"[MQTT] 连接失败: {e}")
        return False


# ===================== 后台线程 =====================


def heartbeat_thread():
    while not exitmark:
        publish_presence("heartbeat")
        now = time.time()
        with known_devices_lock:
            offline = [ip for ip, info in known_devices.items()
                       if now - info["last_seen"] > DEVICE_TIMEOUT]
            for ip in offline:
                del known_devices[ip]
                print(f"[ROOM] 设备超时离线: {ip}")
        time.sleep(HEARTBEAT_INTERVAL)


def action_executor():
    global group_perform, action_plan, proc, executor_running

    plan = action_plan
    if not plan:
        executor_running = False
        return

    actions = plan["actions"]
    music_start = plan["music_start"]

    wait = music_start - time.time()
    if wait > 0:
        print(f"[EXEC] 等待 {wait:.1f}s 后开始...")
        end_wait = time.time() + wait
        while time.time() < end_wait:
            if not group_perform or exitmark:
                return
            time.sleep(0.05)

    force_kill_all_mplayer()
    music_path = "/home/pi/RaspberryPi-CM5/common/music/dog.mp3"
    with proc_lock:
        try:
            proc = Popen(
                f"mplayer -really-quiet -loop 0 {music_path}",
                shell=True, preexec_fn=os.setsid, stdout=DEVNULL)
        except Exception:
            proc = None

    try:
        for act in actions:
            if not group_perform or exitmark:
                break
            target_time = act["start_at"]
            wait = target_time - time.time()
            if wait > 0:
                end_wait = time.time() + wait
                while time.time() < end_wait:
                    if not group_perform or exitmark:
                        break
                    time.sleep(0.05)
            if not group_perform or exitmark:
                break
            aid = act["id"]
            dur = act["duration"]
            print(f"[ACTION] 执行动作 id={aid} name={act.get('name','')} duration={dur}")
            if dog:
                try:
                    dog.action(aid)
                except Exception as e:
                    print(f"[ACTION] 动作执行失败: {e}")
            end_action = time.time() + dur
            while time.time() < end_action:
                if not group_perform or exitmark:
                    break
                time.sleep(0.1)
            if dog:
                try:
                    dog.stop()
                except Exception:
                    pass
    finally:
        with proc_lock:
            kill_proc_safe(proc)
            proc = None
        force_kill_all_mplayer()
        group_perform = False
        action_plan = None
        executor_running = False
        print("[EXEC] 动作执行结束")


# ===================== PySide6 页面 =====================


class GroupPerformPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #0f1530;")
        self._first_paint_logged = False

        # ---- 标题 ----
        self.title = QLabel(_T("title"))
        f1 = QFont()
        f1.setPointSize(18)
        f1.setBold(True)
        self.title.setFont(f1)
        self.title.setStyleSheet("color: white;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 分隔线 ----
        self.sep1 = QFrame()
        self.sep1.setFrameShape(QFrame.Shape.HLine)
        self.sep1.setStyleSheet("color: #2a3050;")

        # ---- 房间信息 ----
        self.room_label = QLabel(_T("room_connecting"))
        self.room_label.setStyleSheet("color: #64b5f6; font-size: 14px;")
        self.room_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.device_label = QLabel(_T("devices_init"))
        self.device_label.setStyleSheet("color: #8892c9; font-size: 13px;")
        self.device_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 连接状态 ----
        self.conn_label = QLabel(_T("mqtt_connecting"))
        self.conn_label.setStyleSheet("color: #ffa726; font-size: 12px;")
        self.conn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.network_label = QLabel("")
        self.network_label.setStyleSheet("color: #ef5350; font-size: 11px;")
        self.network_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 狗类型 ----
        self.dog_label = QLabel(_T("dog_type", dog_type))
        self.dog_label.setStyleSheet("color: #5c6a9c; font-size: 11px;")
        self.dog_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 状态 ----
        self.status_label = QLabel(_T("status_idle"))
        self.status_label.setStyleSheet("color: #18df6b; font-size: 14px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 动作列表标题 ----
        self.action_title = QLabel(_T("action_list_title"))
        self.action_title.setStyleSheet("color: #5c6a9c; font-size: 11px;")
        self.action_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 动作列表滚动区域 ----
        self.action_list_widget = QWidget()
        self.action_list_widget.setStyleSheet("background-color: #1a1f3a; border-radius: 8px;")
        self.action_list_layout = QVBoxLayout(self.action_list_widget)
        self.action_list_layout.setContentsMargins(10, 8, 10, 8)
        self.action_list_layout.setSpacing(3)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }"
                                        "QScrollBar:vertical { width: 6px; background: #1a1f3a; }"
                                        "QScrollBar::handle:vertical { background: #2a3050; border-radius: 3px; }"
                                        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }")
        self.scroll_area.setWidget(self.action_list_widget)
        self.scroll_area.setFixedHeight(140)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._action_items = []  # 存储动作Label引用

        # ---- 分隔线 ----
        self.sep2 = QFrame()
        self.sep2.setFrameShape(QFrame.Shape.HLine)
        self.sep2.setStyleSheet("color: #2a3050;")

        # ---- 提示 ----
        hint_style = "color: #5c6a9c; font-size: 11px; background: transparent;"
        self.corner_tl = QLabel(_T("corner_switch"), self)
        self.corner_tl.setStyleSheet(hint_style)
        self.corner_tr = QLabel(_T("corner_start"), self)
        self.corner_tr.setStyleSheet(hint_style)
        self.corner_tr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.corner_bl = QLabel(_T("corner_exit"), self)
        self.corner_bl.setStyleSheet(hint_style)
        self.corner_br = QLabel("", self)
        self.corner_br.setStyleSheet(hint_style)
        self.corner_br.setAlignment(Qt.AlignmentFlag.AlignRight)

        # ---- 布局 ----
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(6)
        main_layout.addWidget(self.title)
        main_layout.addWidget(self.sep1)
        main_layout.addWidget(self.room_label)
        main_layout.addWidget(self.device_label)
        main_layout.addWidget(self.conn_label)
        main_layout.addWidget(self.network_label)
        main_layout.addWidget(self.dog_label)
        main_layout.addSpacing(4)
        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.action_title)
        main_layout.addWidget(self.scroll_area)
        main_layout.addWidget(self.sep2)
        main_layout.addStretch()

        # ---- 刷新定时器 ----
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_ui)
        self._refresh_timer.start(500)  # 2fps

        # ---- 表演状态监控定时器 ----
        self._perf_monitor = QTimer(self)
        self._perf_monitor.timeout.connect(self._check_performance)
        self._perf_monitor.start(300)

        # ---- 自动退出 ----
        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 构建动作列表UI
        self._build_action_list()

    def _build_action_list(self):
        """构建动作列表UI"""
        # 清除旧项目
        for item in self._action_items:
            self.action_list_layout.removeWidget(item)
            item.deleteLater()
        self._action_items.clear()

        for act in actions_to_perform:
            lbl = QLabel(f"  #{act['id']:>3}  {act['name']}  ({act['duration']}s)")
            lbl.setStyleSheet(
                "color: #8892c9; font-size: 11px; background: transparent; padding: 2px 4px;"
            )
            self._action_items.append(lbl)
            self.action_list_layout.addWidget(lbl)

        # 添加弹簧
        self.action_list_layout.addStretch()

    def _refresh_ui(self):
        """刷新UI显示"""
        # 连接状态
        if mqtt_client and mqtt_client.is_connected():
            self.conn_label.setText(_T("mqtt_connected"))
            self.conn_label.setStyleSheet("color: #18df6b; font-size: 12px;")
        else:
            self.conn_label.setText(_T("mqtt_disconnected"))
            self.conn_label.setStyleSheet("color: #ff5252; font-size: 12px;")

        # 网络状态
        if check_network():
            self.network_label.setText("")
        else:
            self.network_label.setText(_T("network_warn"))
            self.network_label.setStyleSheet("color: #ffa726; font-size: 11px;")

        # 房间号
        if room_id:
            self.room_label.setText(_T("room", room_id))

        # 在线设备数
        with known_devices_lock:
            count = len(known_devices)
        self.device_label.setText(_T("devices", count))

        # 表演状态
        if group_perform:
            self.status_label.setText(_T("status_perform"))
            self.status_label.setStyleSheet("color: #ff9800; font-size: 14px;")
            self.corner_tr.setText(_T("corner_stop"))
        else:
            self.status_label.setText(_T("status_idle2"))
            self.status_label.setStyleSheet("color: #18df6b; font-size: 14px;")
            self.corner_tr.setText(_T("corner_start"))

    def _check_performance(self):
        """监控表演状态，触发动作执行"""
        global group_perform, action_plan, executor_running
        if group_perform and action_plan and not executor_running:
            executor_running = True
            t = threading.Thread(target=action_executor, daemon=True)
            t.start()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        w, h = self.width(), self.height()
        pad = 16
        self.corner_tl.move(pad, pad)
        self.corner_tr.adjustSize()
        self.corner_bl.adjustSize()
        self.corner_br.adjustSize()
        self.corner_tr.move(w - self.corner_tr.width() - pad, pad)
        self.corner_bl.move(pad, h - self.corner_bl.height() - pad)
        self.corner_br.move(w - self.corner_br.width() - pad, h - self.corner_br.height() - pad)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self._first_paint_logged:
            self._first_paint_logged = True
            mark("first paintEvent")
            summary = self._stage_summary()
            print("[group] boot breakdown:\n" + summary, flush=True)

    def _stage_summary(self) -> str:
        lines = []
        prev = 0.0
        for name, ms in _stages:
            lines.append(f"{name}: {ms:.0f}ms (+{ms - prev:.0f})")
            prev = ms
        return " | ".join(lines)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Back:
            # C 键 → 退出
            print("[group] KEY_BACK -> exit", flush=True)
            self.close()
        elif ev.key() == Qt.Key.Key_Return:
            # D 键 → 开始/停止
            global group_perform
            if group_perform:
                print("[group] KEY_RETURN -> stop", flush=True)
                publish_stop()
            else:
                print("[group] KEY_RETURN -> start", flush=True)
                publish_start()

    def closeEvent(self, ev):
        global exitmark
        print("[group] closing", flush=True)
        exitmark = True
        self._refresh_timer.stop()
        self._perf_monitor.stop()

        publish_exit()
        time.sleep(0.3)

        if mqtt_client:
            publish_presence("leave")
            time.sleep(0.2)
            mqtt_client.loop_stop()
            mqtt_client.disconnect()

        with proc_lock:
            if proc is not None:
                kill_proc_safe(proc)

        force_kill_all_mplayer()
        if dog:
            try:
                dog.reset()
            except Exception:
                pass
        super().closeEvent(ev)


# ===================== 初始化 =====================


def init_dog():
    global dog, dog_type, actions_to_perform
    try:
        dog = XGO()
        fm = dog.read_firmware()
        if isinstance(fm, str):
            candidate = fm.split('-')[0].upper()
            dog_type = candidate if candidate in ["R", "L", "M", "W", "B"] else "R"
        else:
            dog_type = "R"
        print(f"[INFO] 设备型号: {dog_type}")
    except Exception as e:
        dog_type = "R"
        dog = None
        print(f"[INFO] 狗初始化失败: {e}")

    if dog_type not in ACTION_GROUPS:
        dog_type = "R"
    actions_to_perform = ACTION_GROUPS[dog_type]
    print(f"[INFO] 动作列表: {[a['name'] for a in actions_to_perform]}")


# ===================== 入口 =====================


def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    # 初始化狗
    init_dog()
    mark("dog init done")

    app = QApplication(sys.argv)
    mark("QApplication created")

    w = GroupPerformPage()
    mark("widget constructed")

    # 启动MQTT
    QTimer.singleShot(200, lambda: _start_mqtt(w))
    mark("MQTT setup scheduled")

    w.showFullScreen()
    mark("showFullScreen returned")

    rc = app.exec()
    print(f"[group] exit rc={rc}", flush=True)
    sys.exit(rc)


def _start_mqtt(widget):
    """延迟启动MQTT连接"""
    if not check_network():
        print("[MQTT] 网络未连接, 稍后重试...")
        QTimer.singleShot(3000, lambda: _start_mqtt(widget))
        return

    if setup_mqtt():
        threading.Thread(target=heartbeat_thread, daemon=True).start()
        print("[INFO] 群组表演 MQTT 模式已就绪")
    else:
        print("[MQTT] 初始化失败, 稍后重试...")
        QTimer.singleShot(3000, lambda: _start_mqtt(widget))


if __name__ == "__main__":
    main()
