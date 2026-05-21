#!/usr/bin/env python3
"""
Joystick → XGOController 适配层

将 2.4G 手柄的 /dev/input/js* 事件映射为 XGOController 的
button_X / axis_X 标准化索引，复用统一的键位映射配置体系。

映射规则：
  - joystick func code（如 0x0100=A 键）→ button_X 索引
  - joystick func code（如 0x0201=RK1_UP_DOWN）→ axis_X 索引
  - 映射配置使用 "joystick_<device_type>" 键（如 joystick_xgorider）
"""
import os
import sys
import time
import struct
import threading

# 确保能 import gamepad_controller
_GP_DIR = "/home/pi/luwu-os/libs/gamepad_config"
if _GP_DIR not in sys.path:
    sys.path.insert(0, _GP_DIR)
import gamepad_controller as gc

# ── Joystick 物理按键/轴 → 标准化 button_X / axis_X 索引 ──────

# joystick func code → button index
JOYSTICK_BUTTON_MAP = {
    0x0100: 0,   # A
    0x0101: 1,   # B
    0x0102: 2,   # X
    0x0103: 3,   # Y
    0x0104: 4,   # L1
    0x0105: 5,   # R1
    0x0106: 8,   # SELECT
    0x0107: 9,   # START
    0x0108: 16,  # MODE
    0x0109: 10,  # BTN_RK1 (左摇杆按下)
    0x010A: 11,  # BTN_RK2 (右摇杆按下)
}

# joystick func code → axis index
JOYSTICK_AXIS_MAP = {
    0x0200: 0,   # RK1_LEFT_RIGHT
    0x0201: 1,   # RK1_UP_DOWN
    0x0202: 2,   # L2
    0x0203: 3,   # RK2_LEFT_RIGHT
    0x0204: 4,   # RK2_UP_DOWN
    0x0205: 5,   # R2
    0x0206: 0,   # WSAD_LEFT_RIGHT → 合并到 axis_0
    0x0207: 1,   # WSAD_UP_DOWN → 合并到 axis_1
}

# joystick button/axis 的可读名称（调试用）
JOYSTICK_BUTTON_NAMES = {
    0x0100: "A",      0x0101: "B",      0x0102: "X",      0x0103: "Y",
    0x0104: "L1",     0x0105: "R1",     0x0106: "SELECT", 0x0107: "START",
    0x0108: "MODE",   0x0109: "BTN_RK1", 0x010A: "BTN_RK2",
}

JOYSTICK_AXIS_NAMES = {
    0x0200: "RK1_LR", 0x0201: "RK1_UD", 0x0202: "L2",
    0x0203: "RK2_LR", 0x0204: "RK2_UD", 0x0205: "R2",
    0x0206: "WSAD_LR", 0x0207: "WSAD_UD",
}


class JoystickReader:
    """读取 Linux /dev/input/js* 手柄设备（从 joystick 应用移植）"""

    def __init__(self, js_id: int = 0):
        self._js_id = js_id
        self._jsdev = None
        self._connected = False
        self._running = False
        self._thread = None
        self._last_reconnect_attempt = 0.0

        # 缓存当前状态
        self.button_states: dict[int, int] = {}   # func_code → value (0/1)
        self.axis_states: dict[int, float] = {}    # func_code → normalized (-1..1)
        self._lock = threading.Lock()

        self._try_open()

    def _try_open(self):
        js_path = f"/dev/input/js{self._js_id}"
        try:
            self._jsdev = open(js_path, "rb")
            self._connected = True
            print(f"[joystick_adapter] 手柄已连接: {js_path}", flush=True)
        except Exception:
            self._connected = False
            print(f"[joystick_adapter] 未找到手柄: {js_path}", flush=True)

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self):
        if not self._connected:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._jsdev:
            try:
                self._jsdev.close()
            except Exception:
                pass
            self._jsdev = None
        self._connected = False

    def _read_loop(self):
        while self._running and self._connected:
            try:
                evbuf = self._jsdev.read(8)
                if evbuf:
                    t, value, etype, number = struct.unpack("IhBB", evbuf)
                    func = (etype << 8) | number
                    with self._lock:
                        if func in JOYSTICK_BUTTON_MAP:
                            self.button_states[func] = value
                        elif func in JOYSTICK_AXIS_MAP:
                            self.axis_states[func] = value / 32767.0
            except BlockingIOError:
                time.sleep(0.01)
            except Exception as e:
                print(f"[joystick_adapter] 读取错误: {e}", flush=True)
                self._connected = False
                break

    def try_reconnect(self):
        now = time.monotonic()
        if now - self._last_reconnect_attempt < 2.0:
            return
        self._last_reconnect_attempt = now
        if not self._connected:
            self._try_open()
            if self._connected:
                self.start()

    def get_states_snapshot(self):
        """线程安全地获取当前状态快照"""
        with self._lock:
            btns = dict(self.button_states)
            axes = dict(self.axis_states)
        return btns, axes


class JoystickController(gc.XGOController):
    """
    继承 XGOController，重写 run() 以支持 joystick 输入源。

    与 evdev 模式的区别：
      - 使用 JoystickReader 而非 evdev 读取输入
      - 映射配置使用 "joystick_<device_type>" 键
      - 步幅参数 (self._step_control) 影响轴速度缩放
    """

    def __init__(self, js_id: int = 0):
        super().__init__()
        self._js_id = js_id
        self._js_reader: JoystickReader | None = None

    # ── 重写：使用 joystick 专属映射键 ──────────────────────────

    def _load_mapping(self):
        """使用 joystick_<device_type> 映射键"""
        log = gc.log
        log.info(f">>> 加载 Joystick 配置文件: {self.CONFIG_FILE}")
        try:
            with open(self.CONFIG_FILE) as f:
                all_cfg = gc.json.load(f)
            log.info(f"  JSON 顶层 keys: {list(all_cfg.keys())}")
            # 优先用 joystick_<device_type>，fallback 到 <device_type>
            js_key = f"joystick_{self.device_type}"
            if js_key in all_cfg:
                self.mapping = all_cfg[js_key]
                log.info(f"  使用 joystick 专属映射: {js_key}")
            else:
                self.mapping = all_cfg.get(self.device_type or "xgorider", {})
                log.info(f"  使用通用映射: {self.device_type}")
            self._config_mtime = os.path.getmtime(self.CONFIG_FILE)
            log.info(f"  设备类型={self.device_type}, 映射项数={len(self.mapping)}")
            for k, v in self.mapping.items():
                if v != "none":
                    log.info(f"    {k} → {v}")
        except FileNotFoundError:
            self._config_mtime = 0
            log.warning(f"配置文件不存在: {self.CONFIG_FILE}，使用空映射")
        except Exception as e:
            log.error(f"读取配置失败: {e}")

    # ── 重写：joystick 主循环 ──────────────────────────────────

    def run(self):
        log = gc.log
        log.info("=" * 60)
        log.info("JoystickController.run() 启动 (2.4G 模式)")
        log.info(f"  CONFIG_FILE = {self.CONFIG_FILE}")
        log.info("=" * 60)

        self._init_xgo()
        log.info(f"xgo 初始化完成: xgo={'✓' if self.xgo else '✗ None'}, "
                 f"device_type={self.device_type}")

        self._load_mapping()
        self._running = True
        self._start_config_watcher()

        # 启动 joystick 读取
        self._js_reader = JoystickReader(js_id=self._js_id)
        self._js_reader.start()

        # 主轮询循环
        _prev_btns: dict[int, int] = {}
        _prev_axes: dict[int, float] = {}

        while self._running:
            if not self._js_reader.connected:
                self._js_reader.try_reconnect()
                time.sleep(0.5)
                continue

            btns, axes = self._js_reader.get_states_snapshot()

            # 处理按钮变化（边沿触发）
            for func_code, value in btns.items():
                prev = _prev_btns.get(func_code, 0)
                if value != prev:
                    _prev_btns[func_code] = value
                    btn_idx = JOYSTICK_BUTTON_MAP.get(func_code)
                    if btn_idx is not None:
                        self._on_button(btn_idx, value == 1)

            # 处理轴变化（阈值过滤 + 步幅缩放）
            for func_code, value in axes.items():
                prev = _prev_axes.get(func_code, 0.0)
                # 仅在变化超过死区时触发
                if abs(value - prev) > 0.03:
                    _prev_axes[func_code] = value
                    axis_idx = JOYSTICK_AXIS_MAP.get(func_code)
                    if axis_idx is not None:
                        # 步幅缩放：_step_control / 70 作为倍率
                        scaled = value * (self._step_control / 70.0)
                        # clamp 到 [-1, 1]
                        scaled = max(-1.0, min(1.0, scaled))
                        self._on_axis(axis_idx, round(scaled, 4))

            time.sleep(0.02)  # ~50Hz 轮询

        # 清理
        self._js_reader.stop()
        self._stop_movement()

    def _on_exit(self, *_):
        log = gc.log
        log.info("收到退出信号，停止机器人...")
        self._running = False
        self._stop_movement()
        if self._js_reader:
            self._js_reader.stop()
        sys.exit(0)


# ── 更新 CONFIG_FILE 引用（指向实际路径） ──
JoystickController.CONFIG_FILE = os.path.join(_GP_DIR, "mappings.json")
