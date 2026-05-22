#!/usr/bin/env python3
"""
键位映射配置服务器 (Flask)
提供 Web 页面让用户通过手机扫码配置手柄按键与动作的映射关系。

API:
  GET  /api/mappings?device=xgomini     — 获取指定设备的映射配置
  POST /api/mappings                    — 保存映射配置 (JSON body)
  GET  /                                — Web 配置页面
"""
import json
import os
import sys
import threading
import queue
from flask import Flask, request, jsonify, send_from_directory, Response

# 手柄事件广播模块
_GP_DIR = '/home/pi/luwu-os/libs/gamepad_config'
if _GP_DIR not in sys.path:
    sys.path.insert(0, _GP_DIR)
try:
    from mapping_events import get_queue as _get_event_queue
except ImportError:
    _get_event_queue = None

# 配置文件路径
MAPPINGS_FILE = "/home/pi/luwu-os/libs/gamepad_config/mappings.json"
# TEMPLATE_DIR 已随文件迁移到 gamepad/ 目录，无需修改
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

app = Flask(__name__, template_folder=TEMPLATE_DIR)

# ── 可用的功能列表（与 gamepad_controller.py 同步）─────────────────
# 按设备类型分离，rider 和 dog 的选项不混用

# --- 所有设备通用 ---
_COMMON_ONE_SHOT = ["none", "stop"]
_COMMON_RUMBLE = ["rumble_short", "rumble_long", "rumble_pulse"]

# --- Mini / Lite (Dog) 专用 ---
DOG_ONE_SHOT = _COMMON_ONE_SHOT + [
    # 姿态/步态
    "imu_on", "imu_off",
    "perform_on", "perform_off",
    "pace_normal", "pace_slow", "pace_high",
    "gait_trot", "gait_walk",
    # 高度
    "height_up", "height_down",
    # 机械臂
    "claw_open", "claw_close",
    "arm_forward", "arm_back",
    # joystick 扩展
    "play_ball",
    "crossing_toggle",
    "step_up", "step_down",
    "pace_up", "pace_down",
    # 震动反馈
    *_COMMON_RUMBLE,
]
# Mini/Lite action 中文名
DOG_ACTION_NAMES = {
    1: "趴下", 2: "站起", 3: "匍匐前进", 4: "转圈",
    5: "踏步", 6: "蹲起", 7: "转动Roll", 8: "转动Pitch",
    9: "转动Yaw", 10: "三轴转动", 11: "撒尿", 12: "坐下",
    13: "招手", 14: "伸懒腰", 15: "波浪", 16: "摇摆",
    17: "乞讨", 18: "找食物", 19: "握手", 20: "鸡头",
    21: "俯卧撑", 22: "张望", 23: "动作23", 24: "调皮",
    128: "上抓", 129: "动作129", 130: "下抓", 144: "上楼梯",
    255: "复位",
}
DOG_HOLD = ["forward", "back", "left", "right", "turn_left", "turn_right", "roll_left", "roll_right"]
DOG_AXIS = ["none", "axis_x", "axis_y", "axis_yaw"]

# --- Rider 专用 ---
RIDER_ONE_SHOT = _COMMON_ONE_SHOT + [
    # 平衡/表演
    "rider_balance_on", "rider_balance_off",
    "rider_perform_on", "rider_perform_off",
    # 高度
    "rider_height_up", "rider_height_down",
    # joystick 扩展
    "play_ball",
    "crossing_toggle",
    "step_up", "step_down",
    "pace_up", "pace_down",
    # 震动反馈
    *_COMMON_RUMBLE,
]
# Rider action 中文名
RIDER_ACTION_NAMES = {
    1: "高低起伏", 2: "前进后退", 3: "匍匐前进", 4: "四方蛇形",
    5: "升降旋转", 6: "蹲起", 7: "转动Roll", 8: "转动Pitch",
    9: "转动Yaw", 10: "三轴转动", 11: "撒尿", 12: "坐下",
    13: "招手", 14: "伸懒腰", 15: "波浪", 16: "摇摆",
    17: "乞讨", 18: "找食物", 19: "握手", 20: "鸡头",
    21: "俯卧撑", 22: "张望", 23: "动作23", 24: "调皮",
    128: "上抓", 129: "动作129", 130: "下抓", 144: "上楼梯",
    255: "复位",
}
RIDER_HOLD = ["rider_forward", "rider_back", "rider_turn_left", "rider_turn_right", "roll_left", "roll_right"]
RIDER_AXIS = ["none", "rider_axis_x", "rider_axis_yaw", "rider_roll_axis"]

# 生成完整的 action_1 到 action_255 列表（用于按键映射下拉框）
def _action_options(device):
    """返回该设备可用的 action_X 函数ID列表"""
    names = DOG_ACTION_NAMES if device in ("xgomini", "xgolite") else RIDER_ACTION_NAMES
    # 只列出有中文名的 action（1~24, 128~130, 144, 255）
    result = []
    for aid, cname in sorted(names.items()):
        result.append(f"action_{aid}")
    return result


def _get_device_actions(device):
    """根据设备类型返回可用的功能列表"""
    if device in ("xgomini", "xgolite"):
        return {
            "one_shot": DOG_ONE_SHOT + _action_options(device),
            "hold": DOG_HOLD,
            "axis": DOG_AXIS,
            "action_names": DOG_ACTION_NAMES,
        }
    else:  # xgorider
        return {
            "one_shot": RIDER_ONE_SHOT + _action_options(device),
            "hold": RIDER_HOLD,
            "axis": RIDER_AXIS,
            "action_names": RIDER_ACTION_NAMES,
        }

# 按钮名称
BUTTON_NAMES = {
    0: "A 键", 1: "B 键", 2: "X 键", 3: "Y 键",
    4: "LB (左肩键)", 5: "RB (右肩键)",
    6: "LT (左扳机-轴)", 7: "RT (右扳机-轴)",
    8: "Back (视图)", 9: "Start (菜单)",
    10: "L3 (左摇杆按)", 11: "R3 (右摇杆按)",
    12: "十字键 上", 13: "十字键 下",
    14: "十字键 左", 15: "十字键 右",
}

AXIS_NAMES = {
    0: "左摇杆 Y (上下)",
    1: "左摇杆 X (左右)",
    2: "右摇杆 X / LT",
    3: "右摇杆 Y",
    4: "RT (右扳机)",
    5: "D-Pad X",
}

DEVICE_TYPES = ["xgomini", "xgolite", "xgorider"]


def _load_mappings():
    """读取完整 mappings.json"""
    try:
        with open(MAPPINGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_mappings(data):
    """保存 mappings.json，同时写备份"""
    # 先备份
    backup = MAPPINGS_FILE + ".bak"
    try:
        with open(MAPPINGS_FILE, "r") as f:
            old = f.read()
        with open(backup, "w") as f:
            f.write(old)
    except Exception:
        pass
    # 写新内容
    with open(MAPPINGS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # 确保换行结尾
    with open(MAPPINGS_FILE, "a") as f:
        f.write("\n")


# ── API ─────────────────────────────────────────────────────────────

@app.route("/api/mappings", methods=["GET"])
def get_mappings():
    """获取指定设备类型的映射"""
    device = request.args.get("device", "xgomini")
    all_mappings = _load_mappings()
    mapping = all_mappings.get(device, {})
    actions = _get_device_actions(device)
    return jsonify({
        "device": device,
        "mapping": mapping,
        "available": actions,
        "buttons": BUTTON_NAMES,
        "axes": AXIS_NAMES,
        "devices": DEVICE_TYPES,
    })


@app.route("/api/mappings", methods=["POST"])
def save_mappings():
    """保存指定设备的映射配置"""
    body = request.get_json(force=True)
    device = body.get("device", "xgomini")
    new_mapping = body.get("mapping", {})

    all_mappings = _load_mappings()
    all_mappings[device] = new_mapping
    _save_mappings(all_mappings)

    print(f"[mapping_server] 已保存 {device} 映射，共 {len(new_mapping)} 项")
    return jsonify({"status": "ok", "device": device})


@app.route("/api/info", methods=["GET"])
def get_info():
    """获取完整配置信息（按钮名、可用动作、所有设备映射）"""
    all_mappings = _load_mappings()
    # 为每个设备类型返回各自的可用动作
    available_by_device = {}
    for d in DEVICE_TYPES:
        available_by_device[d] = _get_device_actions(d)
    return jsonify({
        "mappings": all_mappings,
        "available": available_by_device,
        "buttons": BUTTON_NAMES,
        "axes": AXIS_NAMES,
        "devices": DEVICE_TYPES,
    })


# ── Web 页面 ────────────────────────────────────────────────────────

@app.route("/")
def index():
    """配置页面"""
    return send_from_directory(TEMPLATE_DIR, "mapping.html")


@app.route("/api/events")
def gamepad_events():
    """SSE 端点 — 实时推送手柄按键/摇杆事件到映射页面"""
    def event_stream():
        if _get_event_queue is None:
            yield "data: {\"error\": \"event queue not available\"}\n\n"
            return
        q = _get_event_queue()
        while True:
            try:
                evt = q.get(timeout=1)
                yield f"data: {json.dumps(evt)}\n\n"
            except queue.Empty:
                # 心跳防止连接超时
                yield ": heartbeat\n\n"
    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        }
    )

@app.route("/<path:filename>")
def static_files(filename):
    """静态文件"""
    return send_from_directory(TEMPLATE_DIR, filename)


# ── 服务启动/停止 ──────────────────────────────────────────────────

_server_thread = None
_server_running = False


def _run_server(port=8088):
    global _server_running
    _server_running = True
    print(f"[mapping_server] 启动在 http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def start_server(port=8088):
    """后台启动 Flask 服务器"""
    global _server_thread, _server_running
    if _server_running:
        print("[mapping_server] 已在运行中")
        return
    _server_thread = threading.Thread(
        target=_run_server, args=(port,),
        daemon=True, name="mapping-server"
    )
    _server_thread.start()
    print(f"[mapping_server] 已启动 (port={port})")


def stop_server():
    """停止服务器（daemon 线程会自动随进程退出，这里仅标记）"""
    global _server_running
    _server_running = False
    print("[mapping_server] 已停止")


def is_running():
    return _server_running


if __name__ == "__main__":
    start_server()
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        stop_server()
