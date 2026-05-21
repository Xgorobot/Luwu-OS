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
import struct
import random
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
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
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
            "title": "",
            "room": "房间 {}",
            "room_wait": "房间 ---",
            "devices": "设备 {}",
            "dog_type": "型号 {}",
            "mqtt_ok": "MQTT",
            "mqtt_bad": "MQTT断开",
            "net_bad": "无网络",
            "sync_wait": "时间同步中",
            "sync_ok": "时间已同步",
            "sync_fail": "时间同步失败",
            "status_idle": "就绪 - 等待开始",
            "status_perform": "表演中",
            "status_blocked": "等待时间同步",
            "action_list_title": "动作列表",
            "corner_switch": "◀▶ 切换",
            "corner_start": "开始",
            "corner_stop": "停止",
            "corner_exit": "退出",
        },
        "en": {
            "title": "",
            "room": "Room {}",
            "room_wait": "Room ---",
            "devices": "Devices {}",
            "dog_type": "Type {}",
            "mqtt_ok": "MQTT",
            "mqtt_bad": "MQTT off",
            "net_bad": "No net",
            "sync_wait": "Time syncing",
            "sync_ok": "Time synced",
            "sync_fail": "Sync failed",
            "status_idle": "Ready - Waiting",
            "status_perform": "Performing",
            "status_blocked": "Waiting time sync",
            "action_list_title": "Action List",
            "corner_switch": "◀▶ Switch",
            "corner_start": "Start",
            "corner_stop": "Stop",
            "corner_exit": "Exit",
        },
    })
except Exception:
    _T = lambda k, *a: k

# ---- 主题层 ----
from libs.theme import (  # noqa: E402
    apply_app_palette, Asset as T_Asset, Color as T_Color,
    Spacing, Radius, qss as T_qss,
)
from libs.ui import AppFrame  # noqa: E402

_LAUNCHER_ASSETS = os.path.dirname(T_Asset.bg_image)
DEMO_GROUP_ICON = os.path.join(_LAUNCHER_ASSETS, "demo_group.png")
_APP_BG_IMAGE = "/home/pi/luwu-os/assets/images/app_bg.png"

# ===================== 常量 =====================
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
HEARTBEAT_INTERVAL = 5.0
DEVICE_TIMEOUT = 15.0
ACTION_PREP_DELAY = 3.0
AUTO_EXIT_SEC = 600

# NTP 同步配置
NTP_SERVERS = ["ntp.aliyun.com", "ntp1.aliyun.com", "cn.pool.ntp.org", "pool.ntp.org"]
NTP_TIMEOUT = 3.0

# 时间同步状态
SYNC_WAIT = 0    # 同步中
SYNC_OK = 1      # 已同步
SYNC_FAIL = 2    # 同步失败

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

# 时间同步
_time_offset = 0.0          # 软偏移：synced_time() = time.time() + _time_offset
_sync_status = SYNC_WAIT    # 当前同步状态
_sync_server = ""           # 成功同步使用的服务器


def synced_time():
    """用于群控调度的统一时间，已加 NTP 软偏移。"""
    return time.time() + _time_offset

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


# ===================== NTP 时间同步 =====================


def _sntp_query(host, timeout=NTP_TIMEOUT):
    """向单个 NTP 服务器发 SNTP 请求，返回偏移（服务器时间 - 本地时间）。"""
    addr = (host, 123)
    pkt = b'\x1b' + 47 * b'\0'  # LI=0, VN=3, Mode=3 (client)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t1 = time.time()
        s.sendto(pkt, addr)
        data, _ = s.recvfrom(48)
        t4 = time.time()
    finally:
        s.close()
    if len(data) < 48:
        raise ValueError("short ntp packet")
    # 服务器接收时间戳 offset 32；服务器发送时间戳 offset 40
    rx_int, rx_frac = struct.unpack('!II', data[32:40])
    tx_int, tx_frac = struct.unpack('!II', data[40:48])
    NTP_EPOCH = 2208988800  # 1900 -> 1970
    t2 = (rx_int - NTP_EPOCH) + rx_frac / 2 ** 32
    t3 = (tx_int - NTP_EPOCH) + tx_frac / 2 ** 32
    # 标准 NTP offset 公式
    offset = ((t2 - t1) + (t3 - t4)) / 2.0
    return offset


_sync_thread_started = False
_sync_kick = threading.Event()  # 立即唤醒后台同步线程


def sync_time_thread():
    """后台线程：循环执行 NTP 同步。
    - 失败 → 5s 后重试
    - 成功 → 60s 后再次刷新（防止时钟漂移）
    - 任意时刻可通过 _sync_kick.set() 提前唤醒
    """
    global _time_offset, _sync_status, _sync_server
    while not exitmark:
        ok = False
        for srv in NTP_SERVERS:
            if exitmark:
                return
            try:
                offset = _sntp_query(srv)
                _time_offset = offset
                _sync_server = srv
                _sync_status = SYNC_OK
                print(f"[NTP] 同步成功 via {srv}, offset={offset*1000:.1f}ms")
                ok = True
                break
            except Exception as e:
                print(f"[NTP] {srv} 失败: {e}")
                continue
        if not ok:
            # 仅在原状态非 OK 时退化为 FAIL，已同步过的保留 OK 不打断使用
            if _sync_status != SYNC_OK:
                _sync_status = SYNC_FAIL
            print("[NTP] 所有服务器同步失败, 5s 后重试")
            wait_sec = 5
        else:
            wait_sec = 60  # 周期 resync 防漂移

        # 可中断等待：exit / kick 立即返回
        _sync_kick.clear()
        woke = _sync_kick.wait(timeout=wait_sec)
        if exitmark:
            return
        if woke:
            print("[NTP] 收到 kick, 立即重新同步")


def start_time_sync():
    """启动后台时间同步。线程只起一次，重复调用相当于 kick 重试。"""
    global _sync_status, _sync_thread_started
    if not _sync_thread_started:
        _sync_thread_started = True
        if _sync_status != SYNC_OK:
            _sync_status = SYNC_WAIT
        threading.Thread(target=sync_time_thread, daemon=True).start()
    else:
        # 已存在后台线程，唤醒它立即重试
        _sync_kick.set()


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
        # 1.5s 后再补一次 heartbeat，对抗 join 与 broker 订阅建立竞态丢包,
        # 同时也促使其他设备把我加入它们的列表。
        threading.Timer(1.5, lambda: publish_presence("heartbeat")).start()
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

        # 关键：收到他人的 join 时立即回应一次 heartbeat,
        # 让新加入设备无需等待下个心跳周期 (5s) 就能发现自己,
        # 解决"A 显示设备2 / B 显示设备1"的临时不同步窗口。
        # 加 50~300ms 随机抖动避免 N 台设备同时回包形成风暴。
        if msg_type == "join" and ip != local_ip:
            def _reply_hb():
                try:
                    publish_presence("heartbeat")
                except Exception:
                    pass
            threading.Timer(random.uniform(0.05, 0.3), _reply_hb).start()
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

        # 诊断本机时间偏差：发起者 wait 应≈ACTION_PREP_DELAY；接收者大幅偏离意味着时钟未对齐
        now = synced_time()
        wait = music_start - now
        sync_tag = "OK" if _sync_status == SYNC_OK else (
            "WAIT" if _sync_status == SYNC_WAIT else "FAIL")
        print(f"[PLAN] 收到计划: {len(actions)} 动作, music_start={music_start:.3f}, "
              f"本机now={now:.3f}, wait={wait:.2f}s [sync={sync_tag}]")

        # 本机未同步 → 立即触发后台重新同步（不阻塞回调线程）
        if _sync_status != SYNC_OK:
            print("[PLAN] 本机时间未同步, 触发立即重新同步")
            start_time_sync()  # 已存在则 kick

        # 时差过大保护：避免狗"半天才动"或"立即乱跑"
        # 计划已大幅过期 → 拒绝
        if wait < -2.0:
            print(f"[PLAN] 计划已过期 wait={wait:.2f}s, 拒绝执行 (检查时钟同步)")
            return
        # 偏差超过 30s（绝对不可能是正常 prep delay）→ 拒绝
        if wait > 30.0:
            print(f"[PLAN] 偏差过大 wait={wait:.2f}s, 拒绝执行 (检查时钟同步)")
            return

        action_plan = {
            "actions": actions,
            "music_start": music_start
        }
        group_perform = True


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
    # 使用 synced_time() 以保证各设备时间基准一致
    base_time = synced_time() + ACTION_PREP_DELAY
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

    # 启动前若本机未同步，最多用 2.5s（< ACTION_PREP_DELAY=3s）等待后台同步
    if _sync_status != SYNC_OK:
        print("[EXEC] 本机时间未同步, 等待最多 2.5s 同步完成...")
        start_time_sync()  # 唤醒后台线程
        deadline = time.time() + 2.5
        while _sync_status != SYNC_OK and time.time() < deadline:
            if not group_perform or exitmark:
                executor_running = False
                return
            time.sleep(0.05)
        if _sync_status != SYNC_OK:
            print("[EXEC] 警告: 时间仍未同步, 后续 wait 偏差可能较大")

    wait = music_start - synced_time()
    print(f"[EXEC] 启动: music_start={music_start:.3f}, now={synced_time():.3f}, wait={wait:.2f}s")
    if wait > 0:
        end_wait = synced_time() + wait
        while synced_time() < end_wait:
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
            wait = target_time - synced_time()
            if wait > 0:
                end_wait = synced_time() + wait
                while synced_time() < end_wait:
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
            # 动作持续时长用 synced_time() 保持与全局时基一致
            end_action = synced_time() + dur
            while synced_time() < end_action:
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


class GroupPerformPage(AppFrame):
    def __init__(self):
        super().__init__()
        # 覆盖背景为 app_bg.png（与 settings / AI / rc_mode / hotspot 同款）
        _pix = QPixmap(_APP_BG_IMAGE)
        if not _pix.isNull():
            self._bg_pix = _pix
            self.update()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._first_paint_logged = False

        # ---- 顶部标题 ----
        self.setTitle(_T("title"))

        # ---- 图标（demo_group.png）----
        self.icon_label = QLabel(self)
        pix = QPixmap(DEMO_GROUP_ICON)
        if not pix.isNull():
            self.icon_label.setPixmap(pix.scaled(
                64, 64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet(T_qss.transparent())

        # ---- 房间卡片：白底圆角 + 房间号 + 设备数/型号副信息 ----
        self.info_card = QFrame(self)
        self.info_card.setObjectName("infoCard")
        # 严格限定选择器到外层 QFrame#infoCard，避免子 QLabel 被描边/背景串扰
        self.info_card.setStyleSheet(
            "QFrame#infoCard {"
            f"  background-color: rgba(255,255,255,235);"
            f"  border: 1px solid {T_Color.card_border};"
            f"  border-radius: {Radius.md}px;"
            "}"
        )
        self.room_label = QLabel(_T("room_wait"), self.info_card)
        self.room_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.room_label.setStyleSheet(T_qss.text("subtitle", color=T_Color.accent))
        self.meta_label = QLabel("", self.info_card)
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 副信息用 text_primary 深蓝，浮在白卡上对比度足够
        self.meta_label.setStyleSheet(T_qss.text("hint", color=T_Color.text_primary))
        card_v = QVBoxLayout(self.info_card)
        card_v.setContentsMargins(Spacing.lg, Spacing.sm, Spacing.lg, Spacing.sm)
        card_v.setSpacing(2)
        card_v.addWidget(self.room_label)
        card_v.addWidget(self.meta_label)

        # ---- accent 装饰线 ----
        self.accent_line = QFrame(self)
        self.accent_line.setFixedSize(60, 2)
        self.accent_line.setStyleSheet(
            f"background-color: {T_Color.accent}; border: none;"
        )

        # ---- 健康 chip 行：MQTT / 同步 / 网络 ----
        self.mqtt_chip = QLabel("", self)
        self.mqtt_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mqtt_chip.setStyleSheet(T_qss.chip("muted"))
        self.sync_chip = QLabel("", self)
        self.sync_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sync_chip.setStyleSheet(T_qss.chip("muted"))
        self.net_chip = QLabel("", self)
        self.net_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.net_chip.setStyleSheet(T_qss.chip("success"))

        chip_row = QWidget(self)
        chip_row.setStyleSheet(T_qss.transparent())
        h = QHBoxLayout(chip_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(Spacing.sm)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(self.mqtt_chip)
        h.addWidget(self.sync_chip)
        h.addWidget(self.net_chip)

        # ---- 大状态 chip ----
        self.status_label = QLabel(_T("status_idle"), self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(self._status_qss(T_Color.success))

        # 保留原限占位
        self._action_items = []

        # ---- 主布局（垂直居中）----
        center = QWidget(self)
        center.setStyleSheet(T_qss.transparent())
        v = QVBoxLayout(center)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.xs)
        v.addWidget(self.info_card, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.sm)
        v.addWidget(self.accent_line, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.sm)
        v.addWidget(chip_row, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(Spacing.md)
        v.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self._center = center

        # ---- 角标 ----
        self.setCornerHints(
            bl=(_T("corner_exit"), T_Asset.icon_back),
            br=(_T("corner_start"), T_Asset.icon_enter),
        )

        # ---- 刷新定时器 ----
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_ui)
        self._refresh_timer.start(500)

        # ---- 表演状态监控定时器 ----
        self._perf_monitor = QTimer(self)
        self._perf_monitor.timeout.connect(self._check_performance)
        self._perf_monitor.start(300)

        # ---- 自动退出 ----
        QTimer.singleShot(AUTO_EXIT_SEC * 1000, self.close)

    # ---- 大状态 chip 样式（深色描边 + 白底 + 大字 bold）----
    @staticmethod
    def _status_qss(color: str) -> str:
        return (
            "background-color: rgba(255,255,255,220);"
            f"color: {color};"
            f"border: 2px solid {color};"
            "border-radius: 12px;"
            "padding: 6px 18px;"
            "font-size: 15px;"
            "font-weight: bold;"
        )

    def _build_action_list(self):
        self._action_items.clear()

    def _refresh_ui(self):
        """刷新UI显示"""
        # 房间卡片
        with known_devices_lock:
            count = len(known_devices)
        if room_id:
            self.room_label.setText(_T("room", room_id))
        else:
            self.room_label.setText(_T("room_wait"))
        self.meta_label.setText(
            f"{_T('devices', count)}  ·  {_T('dog_type', dog_type)}"
        )

        # MQTT chip
        if mqtt_client and mqtt_client.is_connected():
            self.mqtt_chip.setText(_T("mqtt_ok"))
            self.mqtt_chip.setStyleSheet(T_qss.chip("success"))
        else:
            self.mqtt_chip.setText(_T("mqtt_bad"))
            self.mqtt_chip.setStyleSheet(T_qss.chip("danger"))

        # 同步 chip
        if _sync_status == SYNC_OK:
            self.sync_chip.setText(_T("sync_ok"))
            self.sync_chip.setStyleSheet(T_qss.chip("success"))
        elif _sync_status == SYNC_WAIT:
            self.sync_chip.setText(_T("sync_wait"))
            self.sync_chip.setStyleSheet(T_qss.chip("muted"))
        else:
            self.sync_chip.setText(_T("sync_fail"))
            self.sync_chip.setStyleSheet(T_qss.chip("danger"))

        # 网络 chip——正常不显示，异常时红字提示
        if not check_network():
            self.net_chip.setText(_T("net_bad"))
            self.net_chip.setStyleSheet(T_qss.chip("danger"))
            self.net_chip.show()
        else:
            self.net_chip.hide()

        # 表演状态 + D 角标文案
        if group_perform:
            self.status_label.setText(_T("status_perform"))
            self.status_label.setStyleSheet(self._status_qss(T_Color.warning))
            self.setCornerHint("br", _T("corner_stop"), T_Asset.icon_enter)
        elif _sync_status != SYNC_OK:
            self.status_label.setText(_T("status_blocked"))
            self.status_label.setStyleSheet(self._status_qss(T_Color.warning))
            self.setCornerHint("br", _T("corner_start"), T_Asset.icon_enter)
        else:
            self.status_label.setText(_T("status_idle"))
            self.status_label.setStyleSheet(self._status_qss(T_Color.success))
            self.setCornerHint("br", _T("corner_start"), T_Asset.icon_enter)

    def _check_performance(self):
        """监控表演状态，触发动作执行"""
        global group_perform, action_plan, executor_running
        if group_perform and action_plan and not executor_running:
            executor_running = True
            t = threading.Thread(target=action_executor, daemon=True)
            t.start()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)  # AppFrame 负责背景与 4 角
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        top = max(28, h * 12 // 100)
        bottom = max(20, h * 8 // 100)
        self._center.setGeometry(0, top, w, h - top - bottom)
        # 房间卡片宽度随屏伸缩
        self.info_card.setFixedWidth(min(int(w * 0.78), 320))

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
                # 未同步时不允许开始，同时触发一次重试
                if _sync_status != SYNC_OK:
                    print("[group] KEY_RETURN -> blocked: time not synced, retry sync", flush=True)
                    if _sync_status == SYNC_FAIL:
                        start_time_sync()
                    return
                print("[group] KEY_RETURN -> start", flush=True)
                publish_start()

    def closeEvent(self, ev):
        global exitmark
        print("[group] closing", flush=True)
        exitmark = True
        # 唤醒后台 NTP 线程让其立即退出
        try:
            _sync_kick.set()
        except Exception:
            pass
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
    apply_app_palette(app)
    mark("QApplication created")

    w = GroupPerformPage()
    mark("widget constructed")

    # 启动 NTP 同步（后台线程，不阻塞 UI）
    QTimer.singleShot(100, start_time_sync)
    mark("time sync scheduled")

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
