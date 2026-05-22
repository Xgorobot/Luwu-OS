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
for line in os.popen(f"pgrep -f '{monitor_script} {mac}'").read().strip().split():
    if line.isdigit():
        try:
            os.kill(int(line), signal.SIGTERM)
            print(f"🔧 已清理旧 monitor 进程 (PID {line})")
        except:
            pass
time.sleep(0.5)

# ── 1. 准备 ──
for d in glob.glob("/sys/devices/virtual/misc/uhid/0005:*"):
    rp = d + "/report_descriptor"
    if os.path.exists(rp):
        with open(rp, "rb") as f:
            report_map = f.read().rstrip(b'\x00')
        break

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

# ── 3. 要校准的按键（页面上的名称） ──
buttons_to_test = [
    (0, "A 键"),
    (1, "B 键"),
    (2, "X 键"),
    (3, "Y 键"),
    (4, "LB (左肩键)"),
    (5, "RB (右肩键)"),
    (8, "Back (视图)"),
    (9, "Start (菜单)"),
    (10, "L3 (左摇杆按)"),
    (11, "R3 (右摇杆按)"),
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

    print(f"  等待按键... (5秒超时)")
    detected = None
    start = time.time()
    while time.time() - start < 5:
        line = readline_timeout(0.15)
        if not line:
            continue
        try:
            d = json.loads(line.strip())
            if d.get("type") != "raw":
                continue
            cur = get_pressed_buttons(d["hex"])
            new_btns = cur - last_btns
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
        results.append((name, expected_idx, -1, False))

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
