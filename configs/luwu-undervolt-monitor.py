#!/usr/bin/env python3
"""Luwu OS 电池电量读取（一次性，由 launcher QTimer 定期调用）"""
import subprocess

def log(msg):
    subprocess.run(["systemd-cat","-t","battery","-p","info"],
                   input=f"[battery] {msg}\n", text=True)

def read_battery_once():
    """临时打开串口读取电量，读完立即释放（finally 保证不泄露 fd）"""
    dog = None
    try:
        from xgolib import XGO as X
        dog = X("xgomini")
        val = dog.read_battery()
        if val is None or str(val).strip() == '' or val == 'Null':
            return None
        battery = int(val)
        if battery <= 0:
            return None
        return battery
    except Exception as e:
        log(f"读取电池失败: {e}")
        return None
    finally:
        if dog is not None:
            try:
                dog.ser.close()
            except Exception:
                pass

if __name__ == "__main__":
    battery = read_battery_once()
    if battery is not None:
        with open("/tmp/luwu_battery_level","w") as f:
            f.write(str(battery))
