#!/usr/bin/env python3
"""
luwu-os 硬件自动识别脚本 — CM4 新/老硬件自适应

逻辑:
  1. 非 CM4 → 直接退出 (CM5 无需处理)
  2. 检查防循环标记: 如果上次刚切换过配置且本次仍然探测失败 → 保持不动
  3. /dev/ttyAMA5 存在 (新配置) → 发 xgolib 固件查询帧
     - 有回应 → 新硬件确认, 退出
     - 无回应 → 老硬件, 切换到 cm4-old.config, 写标记, 重启
  4. /dev/ttyAMA5 不存在 (老配置) → 探测 ttyAMA0
     - 有回应 → 老硬件确认, 退出
     - 无回应 → 可能新硬件, 切换到 boot-config.txt, 写标记, 重启
  5. 两端口都无回应且有上次切换标记 → 保持当前配置 (机器狗未连接)
"""

import os
import sys
import time
import shutil
import subprocess

CONFIGS_DIR = os.path.join(os.path.dirname(__file__))
NEW_CONFIG  = os.path.join(CONFIGS_DIR, "boot-config.txt")   # 新CM4/CM5 配置
OLD_CONFIG  = os.path.join(CONFIGS_DIR, "cm4-old.config")    # 老CM4 配置
BOOT_CONFIG = "/boot/firmware/config.txt"
PORT_NEW    = "/dev/ttyAMA5"   # 新CM4 机器狗串口
PORT_OLD    = "/dev/ttyAMA0"   # 老CM4 机器狗串口
LOG_FILE    = "/tmp/luwu_hw_autoconf.log"
SWITCH_FLAG = "/tmp/luwu_hw_autoconf_switched"  # 防循环标记


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_cm4() -> bool:
    try:
        with open("/proc/device-tree/model", "rb") as f:
            model = f.read().decode("utf-8", errors="replace").strip("\x00")
        return "Compute Module 4" in model
    except Exception:
        return False


def probe_port(port: str) -> bool:
    """用 xgolib 读固件版本探测串口, 返回 True 表示有机器狗"""
    log(f"probe_port({port}) called")
    try:
        from xgolib import XGO
        dog = XGO(port=port)
        import time; time.sleep(0.3)
        ver = dog.read_firmware()
        log(f"{port} xgolib firmware: {ver}")
        if ver is None or ver == 'Null' or str(ver).strip() == '':
            return False
        return True
    except PermissionError as e:
        log(f"{port} busy (PermissionError): {e} → treat as responded")
        return True
    except Exception as e:
        err_msg = str(e)
        if "multiple access on port" in err_msg:
            # 其他进程(如 undervolt)正在使用 → 设备确认存在
            log(f"{port} multiple access detected: {err_msg} → treat as responded")
            return True
        log(f"{port} probe error: {e}")
        return False


def switch_config(src: str) -> bool:
    if not os.path.exists(src):
        log(f"ERROR: {src} not found!")
        return False
    try:
        shutil.copy2(src, BOOT_CONFIG)
        log(f"Copied {src} → {BOOT_CONFIG}")
        return True
    except Exception as e:
        log(f"ERROR copy failed: {e}")
        return False


# === 调试模式: True=只打日志不真重启, False=正常重启 ===
DRY_RUN = False

def reboot_after(sec: int = 2):
    if DRY_RUN:
        log(f"[DRY_RUN] Would reboot in {sec}s, but DRY_RUN=True → skipping reboot")
        return
    log(f"Rebooting in {sec}s...")
    time.sleep(sec)
    subprocess.run(["reboot"], check=False)


def had_recent_switch() -> bool:
    """检查上次启动是否刚做过配置切换（防循环标记存在）"""
    return os.path.exists(SWITCH_FLAG)


def mark_switch():
    """写入防循环标记，下次启动时如果仍然失败则不再切换"""
    try:
        with open(SWITCH_FLAG, "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass


def clear_switch_flag():
    """探测成功，清除防循环标记"""
    try:
        if os.path.exists(SWITCH_FLAG):
            os.remove(SWITCH_FLAG)
    except Exception:
        pass


def main():
    log("=== luwu hardware_autoconf start ===")
    log(f"DRY_RUN={DRY_RUN}")

    # 记录系统信息
    try:
        with open("/proc/device-tree/model", "rb") as f:
            model = f.read().decode("utf-8", errors="replace").strip("\x00")
        log(f"Model: {model}")
    except Exception as e:
        log(f"Cannot read model: {e}")

    # 记录串口设备
    for p in ["/dev/ttyAMA0", "/dev/ttyAMA1", "/dev/ttyAMA5", "/dev/ttyS0", "/dev/serial0", "/dev/serial1"]:
        exists = os.path.exists(p)
        extra = ""
        if exists and os.path.islink(p):
            extra = f" -> {os.readlink(p)}"
        log(f"  {p}: {'EXISTS' + extra if exists else 'NOT FOUND'}")

    # 记录防循环标记
    log(f"  SWITCH_FLAG ({SWITCH_FLAG}): {'EXISTS' if os.path.exists(SWITCH_FLAG) else 'NOT FOUND'}")

    if not is_cm4():
        log("Not CM4 (CM5 or other), exit")
        return

    log("CM4 detected")

    # ── 情况1/2: 当前是新硬件配置 (ttyAMA5 存在) ──────────────────
    if os.path.exists(PORT_NEW):
        log(f"{PORT_NEW} exists, probing...")
        if probe_port(PORT_NEW):
            log("New CM4 hardware confirmed (ttyAMA5 responded) ✓")
            clear_switch_flag()
            return
        # ttyAMA5 无回应 → 可能是老硬件误用了新配置, 也可能机器狗没连接
        if had_recent_switch():
            log("No response on ttyAMA5, but already switched last boot → keeping current config (dog may be disconnected)")
            return
        log("No response on ttyAMA5 → old CM4 hardware on new config")
        if switch_config(OLD_CONFIG):
            mark_switch()
            reboot_after()
        else:
            log("Switch to old config failed — manual intervention required")
            sys.exit(1)
        return

    # ── 情况3/4: 当前是老硬件配置 (ttyAMA5 不存在) ────────────────
    log(f"{PORT_NEW} not found → on old-hardware config")
    if os.path.exists(PORT_OLD):
        log(f"{PORT_OLD} exists, probing...")
        if probe_port(PORT_OLD):
            log("Old CM4 hardware confirmed (ttyAMA0 responded) ✓")
            clear_switch_flag()
            return
        # ttyAMA0 无回应 → 可能是新硬件误用了老配置, 也可能机器狗没连接
        if had_recent_switch():
            log("No response on ttyAMA0, but already switched last boot → keeping current config (dog may be disconnected)")
            return
        log("No response on ttyAMA0 → new CM4 hardware on old config")
    else:
        if had_recent_switch():
            log(f"{PORT_OLD} not found, but already switched last boot → keeping current config")
            return
        log(f"{PORT_OLD} not found either → assuming new CM4 hardware on old config")

    log("Switching back to new config (boot-config.txt) and rebooting...")
    if switch_config(NEW_CONFIG):
        mark_switch()
        reboot_after()
    else:
        log("Switch to new config failed — manual intervention required")
        sys.exit(1)


if __name__ == "__main__":
    main()
