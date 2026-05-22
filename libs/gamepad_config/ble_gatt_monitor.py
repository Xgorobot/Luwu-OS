#!/usr/bin/env python3
"""
BLE GATT 通知监视器 — 基于 bluetoothctl PTY 持久会话

用于读取不走标准 HOGP 通道的 BLE 手柄数据（如 ESP32-BLE-Gamepad vendor characteristic）。

作为子进程运行，输出 JSON Lines 格式的事件：
  {"type": "raw", "hex": "200f0080808080...", "ts": 1234567890.123}
  {"type": "status", "msg": "subscribed ok"}
"""

import os
import pty
import sys
import re
import time
import json
import select

# ── 配置 ────────────────────────────────────────────────
MAC = sys.argv[1] if len(sys.argv) > 1 else "04:25:0B:00:2B:C1"
ADAPTER = "hci0"
TIMEOUT = 15  # 初始化超时

# BLE characteristic UUIDs to try (按优先级)
TARGET_UUIDS = [
    "91680003-1111-6666-8888-0123456789ab",  # ESP32-BLE-Gamepad vendor
    "00002a4d-0000-1000-8000-00805f9b34fb",  # HID Report (标准HOGP)
]

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

# 通知值匹配：bluetoothctl 的输出格式类似：
#   [CHG] Characteristic ... Value:
#     20 0f 00 80 80 80 80 ...
# 或直接 hex 行
VALUE_LINE = re.compile(r'^[ \t]*([0-9a-fA-F]{2}( [0-9a-fA-F]{2})*)')


def _output(obj):
    """写入 JSON Lines 到 stdout"""
    print(json.dumps(obj), flush=True)


def _log(msg: str, level="info"):
    """写入 stderr（调试用）"""
    print(f"[monitor:{level}] {msg}", file=sys.stderr, flush=True)


def main():
    _log(f"Starting GATT monitor for {MAC}")

    dev_mac = MAC.replace(":", "_")
    dev_prefix = f"/org/bluez/{ADAPTER}/dev_{dev_mac}"

    # 1. 启动 bluetoothctl PTY 会话
    master_fd, slave_fd = pty.openpty()
    
    import subprocess
    proc = subprocess.Popen(
        ["bluetoothctl"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    def send(cmd: str):
        """发送命令到 bluetoothctl"""
        try:
            os.write(master_fd, (cmd + "\n").encode())
        except Exception as e:
            _log(f"Write error: {e}", "error")

    def read_until(timeout=TIMEOUT, idle=1.0):
        """读取直到静默"""
        buf = ""
        deadline = time.time() + timeout
        last_data = time.time()
        while time.time() < deadline:
            r, _, _ = select.select([master_fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(master_fd, 4096).decode("utf-8", errors="replace")
                except Exception:
                    break
                if chunk:
                    buf += chunk
                    last_data = time.time()
            elif buf and (time.time() - last_data) > idle:
                break
        return ANSI_RE.sub('', buf)

    # 等待 bluetoothctl 就绪
    init = read_until(timeout=8)
    if "bluetooth" not in init.lower():
        _log("bluetoothctl not ready", "error")
        sys.exit(1)
    _log("bluetoothctl ready")

    # 2. 进入 GATT 菜单，列出所有属性，找到目标 characteristic
    send("menu gatt")
    read_until(timeout=3)

    send("list-attributes")
    attrs_out = read_until(timeout=5)
    
    _log("Got attribute list", "debug")

    # 解析属性列表，找到目标 UUID
    target_path = None
    target_uuid = None

    for line in attrs_out.splitlines():
        line = line.strip()
        if dev_prefix not in line:
            continue
        for uuid in TARGET_UUIDS:
            if uuid.lower() in line.lower():
                # bluetoothctl list-attributes 输出格式: "cXX  UUID" 或路径
                # 尝试提取路径
                path_match = re.search(rf'{re.escape(dev_prefix)}/service[0-9a-f]+/char[0-9a-f]+', line, re.I)
                if path_match:
                    target_path = path_match.group()
                    target_uuid = uuid
                    _log(f"Found target: {target_path} UUID={uuid}")
                    break
        if target_path:
            break

    if not target_path:
        _log("Target characteristic not found in attribute list, searching...", "warn")
        # 尝试用 select-attribute + UUID 方式
        for uuid in TARGET_UUIDS:
            send(f"select-attribute {uuid}")
            out = read_until(timeout=3)
            _log(f"select-attribute UUID result: {out.strip()[:200]}", "debug")
            # 检查是否成功（无 "Failed" 等错误）
            if "failed" not in out.lower() and "not available" not in out.lower():
                send("attribute-info")
                info_out = read_until(timeout=3)
                _log(f"attribute-info: {info_out.strip()[:300]}", "debug")
                # 从 attribute-info 输出中提取路径
                path_match = re.search(rf'{re.escape(dev_prefix)}/service[0-9a-f]+/char[0-9a-f]+', info_out, re.I)
                if path_match:
                    target_path = path_match.group()
                    target_uuid = uuid
                    _log(f"Found via select-attribute: {target_path}")
                    break
                # 也可以直接用 UUID 作为选择器
                _log(f"Will use UUID directly: {uuid}")
                target_path = uuid  # bluetoothctl also accepts UUID
                target_uuid = uuid
                break

    if not target_path:
        _log("Cannot find target characteristic", "error")
        _output({"type": "status", "msg": "no characteristic found", "error": True})
        sys.exit(1)

    _output({"type": "status", "msg": f"subscribing to {target_path}"})

    # 3. 选择属性并启动通知
    send(f"select-attribute {target_path}")
    select_out = read_until(timeout=2, idle=0.5)
    _log(f"select-attribute result: {select_out.strip()[:200]}", "debug")

    send("notify on")
    # 不等回复，直接进读循环，通知数据会自然出现
    _output({"type": "status", "msg": "subscribed ok"})
    _log("Notifications enabled, entering read loop")

    # 4. 读取通知循环
    _log("Entering notification read loop")
    buf = ""
    last_hex = ""
    pending_hex_lines = []  # 积累多行 hex 数据
    in_chg = False

    def _flush_hex():
        nonlocal last_hex
        if pending_hex_lines:
            hex_str = ''.join(pending_hex_lines).replace(' ', '').lower()
            pending_hex_lines.clear()
            # 接受 1+ 行 hex，但最终长度要在合理范围（19字节 = 38chars，最小14chars=7bytes）
            if hex_str and hex_str != last_hex and 14 <= len(hex_str) <= 60:
                if not all(c == '0' for c in hex_str):
                    last_hex = hex_str
                    _output({"type": "raw", "hex": hex_str, "ts": time.time()})

    while True:
        r, _, _ = select.select([master_fd], [], [], 0.1)
        if not r:
            _flush_hex()  # 超时清空积压
            continue
        
        try:
            chunk = os.read(master_fd, 4096).decode("utf-8", errors="replace")
        except Exception:
            _log("Read error, exiting", "error")
            break
        
        if not chunk:
            time.sleep(0.1)
            continue

        buf += chunk

        # bluetoothctl PTY 用 \r 分隔行，统一转为 \n 再处理
        buf = buf.replace('\r', '\n')

        while '\n' in buf:
            line, buf = buf.split('\n', 1)
            clean = ANSI_RE.sub('', line).strip()
            
            if not clean:
                # 空行：仅在非通知上下文中冲刷
                if not in_chg and not pending_hex_lines:
                    in_chg = False
                continue

            # 跳过 bluetoothctl 提示符
            if '[bluetoothctl]' in clean or clean.startswith('#') or ']' in clean and '>' in clean and '/service' in clean:
                # GATT 提示符如 [BM769 24G:/service002a/char002d]> — 不冲刷
                continue

            # 检测 [CHG] — 新通知开始
            if '[CHG]' in clean or '[chg]' in clean.lower():
                _flush_hex()
                in_chg = True
                continue

            # 检测 hex 行（含空格分隔的十六进制）
            hex_match = re.match(r'^[\s]*([0-9a-fA-F]{2}( [0-9a-fA-F]{2})+)\s*', clean)
            if hex_match:
                hex_bytes = hex_match.group(1).strip()
                # 检查后面是否还有 hex（bluetoothctl 可能在 hex 后用 ... 表示截断）
                pending_hex_lines.append(hex_bytes)
                # 如果行末尾有 ASCII 表示（如 .......），说明这行 hex 是完整的段
                # 继续等待下一行 hex
                continue

            # 非 hex 行：清空积压
            _flush_hex()
            in_chg = False

    _log("Monitor exit", "info")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        _log(f"Fatal: {e}", "error")
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
