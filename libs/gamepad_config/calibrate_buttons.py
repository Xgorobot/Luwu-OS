#!/usr/bin/env python3
"""
手柄按键校准工具 —— 交互式引导你逐个按按钮，记录物理键值。
"""
import sys, os, glob, time, json, subprocess, select, signal

sys.path.insert(0, '/home/pi/luwu-os/libs/gamepad_config')
from ble_hid_reader import parse_report_map, decode_notification

# ── 0. 清理旧 monitor 进程 ──
mac = "04:25:0B:00:2B:C1"
monitor_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ble_gatt_monitor.py")
pids = os.popen(f"pgrep -f '{monitor_script} {mac}'").read().strip()
if pids:
    for line in pids.split():
        if line.isdigit():
            try:
                os.kill(int(line), signal.SIGTERM)
                print(f"已清理旧 monitor 进程 (PID {line})")
            except:
                pass
    time.sleep(0.5)

# ── 1. 准备 ──
report_map = None
for d in glob.glob("/sys/devices/virtual/misc/uhid/0005:*"):
    rp = d + "/report_descriptor"
    if os.path.exists(rp):
        with open(rp, "rb") as f:
            report_map = f.read().rstrip(b'\x00')
        break

if not report_map:
    print("错误: 找不到 uhid 设备！手柄可能未连接。")
    sys.exit(1)

layouts = parse_report_map(report_map)
game_layout = None
for rid, layout in layouts.items():
    if any(f.usage_page == 1 and not f.is_constant for f in layout.fields):
        game_layout = layout; break
if not game_layout and layouts:
    game_layout = next(iter(layouts.values()))

# ── 2. 启动 monitor ──
print("正在连接手柄...")
proc = subprocess.Popen(
    [sys.executable, monitor_script, mac],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
)

def readline_timeout(timeout=0.3):
    """带超时的 readline，超时返回 None"""
    fd = proc.stdout.fileno()
    r, _, _ = select.select([fd], [], [], timeout)
    if r:
        return proc.stdout.readline()
    return None

# 等待订阅完成
t0 = time.time()
while time.time() - t0 < 10:
    line = readline_timeout(0.5)
    if not line:
        continue
    if '"subscribed ok"' in line:
        print("✅ 手柄已连接，开始校准\n")
        break

# ── 3. 要校准的按键（页面上的名称 → 期待物理键值） ──
# 经实测: 面键在 byte7 高4位 → btn5-8, 肩键在 byte8 → btn1-2
buttons_to_test = [
    (6, "A 键"),         # 0x20 → face bit1 → btn6
    (5, "B 键"),         # 0x10 → face bit0 → btn5
    (8, "X 键"),         # 0x80 → face bit3 → btn8 (需要连续3包稳定)
    (7, "Y 键"),         # 0x40 → face bit2 → btn7
    (1, "LB (左肩键)"),   # HID usage 1
    (2, "RB (右肩键)"),   # HID usage 2
    (3, "Back (视图)"),   # HID usage 3
    (4, "Start (菜单)"),  # HID usage 4
]

results = []

def get_pressed_buttons(raw_hex):
    """解析 hex 并返回按下的按钮索引列表（1-based, HID 解码器返回值）"""
    try:
        events = decode_notification(bytes.fromhex(raw_hex), game_layout)
        if not events:
            return set()
        return {e.index for e in events if e.type == 'button' and e.value == 1}
    except:
        return set()

# ── 4. 逐键校准 ──
for expected_idx, name in buttons_to_test:
    print(f"\n{'='*45}")
    input(f"👉 请准备好，然后按「{name}」→ 按 Enter 继续")
    
    # 清空缓冲区：在等待期间读取所有旧数据
    last_btns = set()
    start = time.time()
    while time.time() - start < 0.5:
        line = readline_timeout(0.15)
        if not line:
            continue
        try:
            d = json.loads(line.strip())
            if d.get("type") == "raw":
                last_btns = get_pressed_buttons(d["hex"])
        except:
            pass

    # 如果期望按键已在基线中（如 X/0x80 的空闲心跳），等待释放再重新捕获
    if expected_idx in last_btns:
        print(f"  ⚠️ btn{expected_idx} 已在基线（空闲心跳），等待释放...")
        rel_start = time.time()
        released = False
        while time.time() - rel_start < 3:
            line = readline_timeout(0.15)
            if not line:
                continue
            try:
                d = json.loads(line.strip())
                if d.get("type") == "raw":
                    cur = get_pressed_buttons(d["hex"])
                    if expected_idx not in cur:
                        last_btns = cur
                        released = True
                        break
                    last_btns = cur
            except:
                pass
        if not released:
            last_btns.discard(expected_idx)
            print(f"  (已手动从基线移除 btn{expected_idx})")

    is_x_button = (expected_idx == 8)
    timeout = 8 if is_x_button else 5
    hint = " ⚠️ 请长按 X 键不要松手" if is_x_button else ""
    print(f"  等待按键... ({timeout}秒超时){hint}")
    detected = None
    start = time.time()
    
    while time.time() - start < timeout:
        line = readline_timeout(0.15)
        if not line:
            continue
        try:
            d = json.loads(line.strip())
            if d.get("type") != "raw":
                continue
            cur = get_pressed_buttons(d["hex"])
            new_btns = cur - last_btns
            
            if is_x_button:
                # X 键特殊: 由于 0x80 与空闲心跳状态完全一致，
                # 空闲时 5x80 与 4x80 会交替振荡，btn8 占比约 50%。
                # 用户长按 X 键时，5x80 占比 > 80%。
                # 策略: 记录最近 N=10 包中 btn8 占比，>=70% 判定为按下
                if not hasattr(get_pressed_buttons, '_x_window'):
                    get_pressed_buttons._x_window = []
                w = get_pressed_buttons._x_window
                w.append(expected_idx in cur)
                if len(w) > 10:
                    w.pop(0)
                if len(w) >= 10 and sum(w) / len(w) >= 0.7:
                    detected = [expected_idx]
                    break
                last_btns = cur
            else:
                # 普通按键: 新出现即可
                if new_btns:
                    detected = sorted(new_btns)
                    break
                last_btns = cur
        except:
            pass

    if detected:
        phys = detected[0]
        match = "✅ 匹配" if phys == expected_idx else f"⚠️ 不匹配 (期望{expected_idx})"
        print(f"  结果: 物理按键索引 = {phys}  →  {match}")
        results.append((name, expected_idx, phys, phys == expected_idx))
    else:
        print(f"  结果: ⏭️ 超时，未检测到按键")
        if is_x_button:
            print(f"      提示: X 键需长按 1.5秒以上（固件限制）")
        results.append((name, expected_idx, -1, False))
    
    # 清理 X 键状态
    if hasattr(get_pressed_buttons, '_x_window'):
        get_pressed_buttons._x_window = []

# ── 5. 清理 ──
proc.terminate()
proc.wait(timeout=2)

# ── 6. 汇总 ──
print(f"\n\n{'='*50}")
print(f"{'📊 校准结果汇总':^45}")
print(f"{'='*50}")
print(f"{'按键名称':<18} {'期望':>4} {'实际':>4} {'结果'}")
print(f"{'-'*45}")
match_count = 0
for name, exp, got, ok in results:
    if got < 0:
        print(f"{name:<18} {exp:>4} {'--':>4} {'⏭️ 跳过'}")
    elif ok:
        match_count += 1
        print(f"{name:<18} {exp:>4} {got:>4} {'✅ 匹配'}")
    else:
        print(f"{name:<18} {exp:>4} {got:>4} {'❌ 不匹配'}")
print(f"{'-'*45}")
print(f"匹配: {match_count}/{len(buttons_to_test)}")
print()

# 如果全部不匹配+1，可能是 1-based vs 0-based 偏移
if match_count == 0 and all(r[2] == r[1] + 1 for r in results if r[2] > 0):
    print("💡 提示: 所有实际值 = 期望值+1，可能是 HID 解码器使用了 1-based 索引")
    print("   需要在 _on_button 推送时减 1 或在页面 JS 中处理偏移")
