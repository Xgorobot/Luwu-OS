#!/usr/bin/env python3
"""
XGO 手柄控制器
  读取 gamepad_config/mappings.json 中的按键映射，
  通过 evdev 监听手柄输入，调用 xgolib 实时控制机器人。

用法：
  python3 /home/luwu/XGO-Rider/gamepad_controller.py

依赖：pip install evdev xgolib
"""

import json
import os
import sys
import time
import signal
import threading
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [gamepad] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# ── 路径 ──────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
# mappings.json 与本脚本同目录
CONFIG_FILE = os.path.join(ROOT, "mappings.json")

# ── 手柄识别 ──────────────────────────────────────────────────────
GAMEPAD_KEYWORDS = ["xbox", "microsoft", "wireless controller", "controller", "tl_", "8bitdo", "ipega", "gamepad"]

# evdev 按键码 → 按钮索引
# 支持 PS4 DualShock4 / Xbox / 通用手柄（多种 code 映射同一 index）
BUTTON_MAP = {
    # A / × (Cross)
    304: 0,   # BTN_SOUTH
    # B / ○ (Circle)
    305: 1,   # BTN_EAST
    # X / □ (Square)
    308: 2,   # BTN_WEST  (PS4 □)
    306: 2,   # BTN_C     (部分 Xbox 兼容)
    # Y / △ (Triangle)
    307: 3,   # BTN_NORTH
    # LB / L1
    310: 4,   # BTN_TL
    # RB / R1
    311: 5,   # BTN_TR
    # LT / L2（数字按键事件，部分手柄有）
    312: 6,   # BTN_TL2
    # RT / R2
    313: 7,   # BTN_TR2
    # Back / Share
    314: 8,   # BTN_SELECT
    # Start / Options
    315: 9,   # BTN_START
    # L3
    317: 10,  # BTN_THUMBL
    # R3
    318: 11,  # BTN_THUMBR
    # Home / PS
    316: 16,  # BTN_MODE
    139: 16,  # Home (旧键盘兼容)
}
# evdev ABS 码 → 轴索引
AXIS_MAP = {0: 0, 1: 1, 3: 2, 4: 3, 2: 4, 5: 5}

# ── 功能分类 ──────────────────────────────────────────────────────
# 按下立刻执行一次（不需要松开）
# action_X 动态匹配：action_1 ~ action_24, action_128~130, action_144, action_255
_ACTION_IDS = list(range(1, 25)) + [128, 129, 130, 144, 255]
ONE_SHOT = {
    "stop",
    *(f"action_{i}" for i in _ACTION_IDS),
    "rider_balance_on", "rider_balance_off",
    "rider_perform_on", "rider_perform_off",
    "rider_height_up", "rider_height_down",
    "imu_on", "imu_off",
    "perform_on", "perform_off",
    "pace_normal", "pace_slow", "pace_high",
    "gait_trot", "gait_walk",
    "claw_open", "claw_close",
    "height_up", "height_down",
    "arm_forward", "arm_back",
    "rumble_short", "rumble_long", "rumble_pulse",
    "play_ball",
    "crossing_toggle",
    "step_up", "step_down",
    "pace_up", "pace_down",
}

# 持续按住时保持运动，松开归零
HOLD = {
    "rider_forward", "rider_back", "rider_turn_left", "rider_turn_right",
    "forward", "back", "left", "right", "turn_left", "turn_right",
    "roll_left", "roll_right",
}

# 轴映射（持续发送）
AXIS_FUNC = {
    "rider_axis_x", "rider_axis_yaw", "rider_roll_axis",
    "axis_x", "axis_y", "axis_yaw",
}


class XGOController:
    def __init__(self):
        self.xgo = None
        self.device_type = None   # "xgorider" / "xgomini" / "xgolite"
        self.mapping = {}         # {"button_0": "stop", "axis_1": "rider_axis_x", ...}
        self._held = set()        # 当前持续按住的按钮索引集合
        self._axes = {}           # {轴索引: 值}
        self._height = 90         # 当前车身高度（用于增减控制）
        self._step_control = 70   # 步幅 (40/70/100 循环)
        self._pace_freq = 2       # 步频 (1=慢/2=中/3=快)
        self._crossing_state = False  # 跨障模式标志
        self._play_ball = 0       # play_ball 序列中断标志 (0=空闲)
        self._roll_dir = 0        # roll 方向 (-1=左, 0=无, 1=右)
        self._running = False
        self._config_mtime = 0    # 配置文件最后修改时间
        self._gamepad_dev = None  # 当前手柄设备（用于震动）
        # signal 只能在主线程注册，子线程中跳过（不影响功能）
        try:
            signal.signal(signal.SIGTERM, self._on_exit)
            signal.signal(signal.SIGINT, self._on_exit)
        except ValueError:
            pass

    # ── 初始化 ────────────────────────────────────────────────────

    def _init_xgo(self):
        try:
            import xgolib
            log.info("正在初始化 xgolib（自动识别设备）...")
            self.xgo = xgolib.XGO()
            fw = getattr(self.xgo, "version", "")
            if fw and fw[0] == "R":
                self.device_type = "xgorider"
            elif fw and fw[0] == "L":
                self.device_type = "xgolite"
            else:
                self.device_type = "xgomini"
            log.info(f"设备类型: {self.device_type}  固件: {fw}")
        except ImportError:
            log.warning("xgolib 未安装，仅输出日志（调试模式）")
        except Exception as e:
            log.error(f"xgolib 初始化失败: {e}")

    def _load_mapping(self):
        log.info(f">>> 加载配置文件: {CONFIG_FILE}")
        try:
            with open(CONFIG_FILE) as f:
                all_cfg = json.load(f)
            log.info(f"  JSON 顶层 keys: {list(all_cfg.keys())}")
            self.mapping = all_cfg.get(self.device_type or "xgorider", {})
            self._config_mtime = os.path.getmtime(CONFIG_FILE)
            log.info(f"  设备类型={self.device_type}, 映射项数={len(self.mapping)}")
            for k, v in self.mapping.items():
                if v != "none":
                    log.info(f"    {k} → {v}")
        except FileNotFoundError:
            self._config_mtime = 0
            log.warning(f"配置文件不存在: {CONFIG_FILE}，使用空映射")
        except Exception as e:
            log.error(f"读取配置失败: {e}")

    def _start_config_watcher(self):
        """后台线程：每2秒检测配置文件变更并热重载"""
        def _watch():
            while self._running:
                time.sleep(2)
                try:
                    mtime = os.path.getmtime(CONFIG_FILE)
                    if mtime != self._config_mtime:
                        log.info("检测到配置更新，热重载中...")
                        self._load_mapping()
                except Exception:
                    pass
        t = threading.Thread(target=_watch, daemon=True, name="config-watcher")
        t.start()

    def _find_gamepad(self):
        import evdev
        all_devs = evdev.list_devices()
        log.debug(f"evdev 扫描到 {len(all_devs)} 个输入设备:")
        candidates = []  # [(priority, dev)] 0=主手柄 1=触摸板/其他
        for path in all_devs:
            try:
                dev = evdev.InputDevice(path)
                name_low = dev.name.lower()
                matched = any(kw in name_low for kw in GAMEPAD_KEYWORDS)
                log.debug(f"  {path}: '{dev.name}' → {'✓ 匹配' if matched else '✗ 跳过'}")
                if matched:
                    # 触摸板/运动传感器优先级降低，优先选主手柄
                    is_secondary = any(x in name_low for x in ["touchpad", "touch pad", "motion", "gyro", "accel"])
                    candidates.append((1 if is_secondary else 0, dev))
            except Exception as e:
                log.debug(f"  {path}: 打开失败 {e}")
        if candidates:
            candidates.sort(key=lambda x: x[0])
            dev = candidates[0][1]
            log.info(f"找到手柄: {dev.name} ({dev.path})")
            return dev
        return None

    # ── 震动反馈 ──────────────────────────────────────────────────

    def _rumble(self, strong=0x8000, weak=0x4000, duration_ms=250):
        """触发手柄震动反馈"""
        dev = self._gamepad_dev
        if not dev:
            return
        try:
            from evdev import ff, ecodes as ec
            rumble = ff.Rumble(strong_magnitude=strong, weak_magnitude=weak)
            effect = ff.Effect(
                ec.FF_RUMBLE, -1, 0,
                ff.Trigger(0, 0),
                ff.Replay(duration_ms, 0),
                ff.EffectType(ff_rumble_effect=rumble)
            )
            eid = dev.upload_effect(effect)
            dev.write(ec.EV_FF, eid, 1)
            threading.Timer(
                duration_ms / 1000 + 0.15,
                lambda: self._erase_effect(eid)
            ).start()
        except Exception as e:
            log.debug(f"震动失败: {e}")

    def _erase_effect(self, eid):
        try:
            if self._gamepad_dev:
                self._gamepad_dev.erase_effect(eid)
        except Exception:
            pass

    # ── xgolib 调用层 ─────────────────────────────────────────────

    def _call(self, func_id, axis_val=None):
        """根据 func_id 调用对应的 xgolib 方法"""
        if not func_id or func_id == "none":
            return

        xgo = self.xgo
        is_rider = (self.device_type == "xgorider")
        log.info(f"CALL func={func_id}, axis_val={axis_val}, xgo={'✓' if xgo else '✗ None'}, type={self.device_type}")
        if not xgo:
            log.warning(f"  xgo=None, 无法执行 {func_id}（仅日志输出）")

        # ── 移动 ──
        if func_id == "stop":
            if xgo: xgo.stop()
            log.debug("stop")

        elif func_id == "rider_axis_x":
            v = -((axis_val or 0) * 1.5)  # 取反：推杆向上=前进
            if xgo: xgo.rider_move_x(v) if is_rider else xgo.move_x(v)
            log.debug(f"rider_axis_x {v:.2f}")

        elif func_id == "rider_axis_yaw":
            v = (axis_val or 0) * 360
            if xgo: xgo.rider_turn(v) if is_rider else xgo.turn(v)
            log.debug(f"rider_axis_yaw {v:.0f}")

        elif func_id == "rider_roll_axis":
            v = (axis_val or 0) * 17
            if xgo: xgo.rider_roll(v)
            log.debug(f"rider_roll {v:.2f}")

        elif func_id == "axis_x":
            v = -(axis_val or 0) * 25  # 向上推杆 → axis_val 负值 → 前进
            if xgo: xgo.move_x(v)
            log.debug(f"axis_x {v:.1f}")

        elif func_id == "axis_y":
            v = -(axis_val or 0) * 18
            if xgo: xgo.move_y(v)
            log.debug(f"axis_y {v:.1f}")

        elif func_id == "axis_yaw":
            v = -(axis_val or 0) * 100
            if xgo: xgo.turn(v)
            log.debug(f"axis_yaw {v:.1f}")

        # ── 持续按住移动 ──
        elif func_id == "rider_forward":
            if xgo: xgo.rider_move_x(1.5) if is_rider else xgo.move_x(25)
        elif func_id == "rider_back":
            if xgo: xgo.rider_move_x(-1.5) if is_rider else xgo.move_x(-25)
        elif func_id == "rider_turn_left":
            if xgo: xgo.rider_turn(90) if is_rider else xgo.turn(50)
        elif func_id == "rider_turn_right":
            if xgo: xgo.rider_turn(-90) if is_rider else xgo.turn(-50)

        elif func_id == "forward":
            if xgo: xgo.move_x(25)
        elif func_id == "back":
            if xgo: xgo.move_x(-25)
        elif func_id == "left":
            if xgo: xgo.move_y(18)
        elif func_id == "right":
            if xgo: xgo.move_y(-18)
        elif func_id == "turn_left":
            if xgo: xgo.turn(50)
        elif func_id == "turn_right":
            if xgo: xgo.turn(-50)

        # ── 高度控制（每次按键增减5） ──
        elif func_id in ("rider_height_up", "height_up"):
            self._height = min(self._height + 5, 120)
            if xgo:
                if is_rider: xgo.rider_height(self._height)
                else: xgo.translation("z", self._height)
            log.info(f"高度 → {self._height}")

        elif func_id in ("rider_height_down", "height_down"):
            self._height = max(self._height - 5, 60)
            if xgo:
                if is_rider: xgo.rider_height(self._height)
                else: xgo.translation("z", self._height)
            log.info(f"高度 → {self._height}")

        # ── 平衡 / IMU ──
        elif func_id == "rider_balance_on":
            if xgo: xgo.rider_balance_roll(1)
            log.info("横滚平衡 ON")
        elif func_id == "rider_balance_off":
            if xgo: xgo.rider_balance_roll(0)
            log.info("横滚平衡 OFF")
        elif func_id == "imu_on":
            if xgo: xgo.imu(1)
            log.info("自稳 ON")
        elif func_id == "imu_off":
            if xgo: xgo.imu(0)
            log.info("自稳 OFF")

        # ── 循环动作 ──
        elif func_id == "rider_perform_on":
            if xgo: xgo.rider_perform(1)
            log.info("循环动作 ON")
        elif func_id == "rider_perform_off":
            if xgo: xgo.rider_perform(0)
            log.info("循环动作 OFF")
        elif func_id == "perform_on":
            if xgo: xgo.perform(1)
            log.info("循环动作 ON")
        elif func_id == "perform_off":
            if xgo: xgo.perform(0)
            log.info("循环动作 OFF")

        # ── 步态 ──
        elif func_id == "pace_normal":
            if xgo: xgo.pace("normal")
        elif func_id == "pace_slow":
            if xgo: xgo.pace("slow")
        elif func_id == "pace_high":
            if xgo: xgo.pace("high")
        elif func_id == "gait_trot":
            if xgo: xgo.gait_type("trot")
        elif func_id == "gait_walk":
            if xgo: xgo.gait_type("walk")

        # ── 机械臂 ──
        elif func_id == "claw_open":
            if xgo: xgo.claw(255)
        elif func_id == "claw_close":
            if xgo: xgo.claw(0)
        elif func_id == "arm_forward":
            if xgo: xgo.arm(80, 60)
        elif func_id == "arm_back":
            if xgo: xgo.arm(-80, 60)

        # ── 震动 ──
        elif func_id == "rumble_short":
            self._rumble(0x8000, 0x4000, 150)
            log.debug("rumble short")
        elif func_id == "rumble_long":
            self._rumble(0xFFFF, 0x8000, 600)
            log.debug("rumble long")
        elif func_id == "rumble_pulse":
            self._rumble(0x5000, 0x3000, 80)
            log.debug("rumble pulse")

        # ── 动作 ──
        elif func_id.startswith("action_"):
            try:
                action_id = int(func_id.split("_")[1])
                if xgo:
                    if is_rider: xgo.rider_action(action_id)
                    else: xgo.action(action_id)
                log.info(f"action({action_id})")
                self._rumble(0xC000, 0x6000, 300)  # 动作执行时震动确认
            except (IndexError, ValueError):
                pass

        # ── play_ball 玩球序列（仅 dog 机型） ──
        elif func_id == "play_ball":
            if self._crossing_state or self._play_ball != 0:
                return
            if is_rider:
                if xgo: xgo.rider_action(5)
            else:
                self._play_ball = 2
                t = threading.Thread(
                    target=self._play_ball_task,
                    args=(self._play_ball,),
                    daemon=True, name="play-ball"
                )
                t.start()
            log.info("play_ball")

        # ── crossing_toggle 跨障模式切换 ──
        elif func_id == "crossing_toggle":
            if is_rider:
                # Rider: 切换横滚平衡模式
                if not self._crossing_state:
                    self._crossing_state = True
                    if xgo: xgo.rider_balance_roll(1)
                    log.info("横滚平衡 ON (Rider 跨障)")
                else:
                    self._crossing_state = False
                    if xgo: xgo.rider_balance_roll(0)
                    log.info("横滚平衡 OFF")
            else:
                # Dog: 跨障模式
                if not self._crossing_state:
                    self._crossing_state = True
                    if xgo:
                        xgo.gait_type("high_walk")
                        time.sleep(0.01)
                        xgo.pace("slow")
                        time.sleep(0.01)
                        xgo.translation("z", 95)
                        time.sleep(0.01)
                        xgo.forward(25)
                    log.info("跨障模式 ON")
                else:
                    self._reset_state()
                    log.info("跨障模式 OFF")

        # ── 步幅调节 ──
        elif func_id == "step_up":
            self._step_control += 30
            if self._step_control > 100:
                self._step_control = 40
            log.info(f"步幅 → {self._step_control}")
        elif func_id == "step_down":
            self._step_control -= 30
            if self._step_control < 40:
                self._step_control = 100
            log.info(f"步幅 → {self._step_control}")

        # ── 步频调节 ──
        elif func_id == "pace_up":
            self._pace_freq += 1
            if self._pace_freq > 3:
                self._pace_freq = 1
            pace_map = {1: "slow", 2: "normal", 3: "high"}
            if xgo:
                if is_rider:
                    pass  # Rider 无 pace 概念，仅记录
                else:
                    xgo.pace(pace_map.get(self._pace_freq, "normal"))
            log.info(f"步频 → {self._pace_freq}")
        elif func_id == "pace_down":
            self._pace_freq -= 1
            if self._pace_freq < 1:
                self._pace_freq = 3
            pace_map = {1: "slow", 2: "normal", 3: "high"}
            if xgo:
                if not is_rider:
                    xgo.pace(pace_map.get(self._pace_freq, "normal"))
            log.info(f"步频 → {self._pace_freq}")

        # ── roll 姿态（持续按住） ──
        elif func_id == "roll_left":
            self._roll_dir = -1
            if xgo:
                if is_rider: xgo.rider_roll(-15)
                else: xgo.attitude("y", -35)
            log.info("roll left")
        elif func_id == "roll_right":
            self._roll_dir = 1
            if xgo:
                if is_rider: xgo.rider_roll(15)
                else: xgo.attitude("y", 35)
            log.info("roll right")

        else:
            log.debug(f"未知功能 ID: {func_id}")

    def _stop_movement(self):
        """松开移动按键时归零"""
        if self.xgo:
            try:
                self.xgo.stop()
            except Exception:
                pass
        # 复位 roll 姿态
        if self._roll_dir != 0:
            self._roll_dir = 0
            if self.xgo:
                try:
                    if self.device_type == "xgorider":
                        self.xgo.rider_roll(0)
                    else:
                        self.xgo.attitude("r", 0)
                        self.xgo.attitude("y", 0)
                except Exception:
                    pass

    def _reset_state(self):
        """复位机器狗状态（跨障退出 / START 复位）"""
        self._play_ball = 0
        self._crossing_state = False
        self._step_control = 70
        self._pace_freq = 2
        self._height = 105
        if self.xgo:
            try:
                if self.device_type == "xgorider":
                    self.xgo.rider_reset()
                else:
                    self.xgo.reset()
            except Exception:
                pass

    def _play_ball_task(self, leg_id):
        """玩球动作序列（从 joystick 移植）"""
        if leg_id != 2 or not self.xgo or self.device_type == "xgorider":
            self._play_ball = 0
            return
        motor_id = [11, 12, 13, 21, 22, 23, 31, 32, 33, 41, 42, 43]
        angle_down = [-16, 66, 1, -17, 66, 1, -14, 74, 1, -14, 72, 1]
        motor_2 = [21, 22, 23]
        angle_hand = [-15, 51, 2, -13, 33, -1, -15, 64, 3, -19, 59, 0]
        angle_play_2 = [10, 0, 0]
        try:
            dog = self.xgo
            if self._play_ball:
                dog.motor_speed(100)
                dog.motor(motor_id, angle_down)
                time.sleep(0.3)
            if self._play_ball:
                dog.motor(motor_id, angle_hand)
                time.sleep(0.2)
            if self._play_ball:
                dog.motor_speed(255)
                time.sleep(0.01)
            if self._play_ball:
                dog.motor(motor_2, angle_play_2)
                time.sleep(0.3)
            if self._play_ball:
                dog.motor(motor_id, angle_hand)
                time.sleep(0.3)
            if self._play_ball:
                dog.motor_speed(100)
                dog.motor(motor_id, angle_down)
                time.sleep(0.3)
            if self._play_ball:
                dog.action(0xFF)
        except Exception as e:
            log.info(f"play_ball 异常: {e}")
        self._height = 105
        self._play_ball = 0

    # ── 事件处理 ──────────────────────────────────────────────────

    def _on_button(self, btn_idx, pressed):
        key = f"button_{btn_idx}"
        func = self.mapping.get(key, "none")
        log.info(f"BTN  {key} {'按下' if pressed else '松开'} → func={func}")
        if func == "none":
            return

        # 跨障模式下只响应 crossing_toggle / action_255 (复位)
        if self._crossing_state and func not in ("crossing_toggle", "action_255"):
            return

        if pressed:
            if func in ONE_SHOT:
                log.info(f"  → ONE_SHOT 执行: {func}")
                self._call(func)
            elif func in HOLD:
                self._held.add(btn_idx)
                log.info(f"  → HOLD 开始: {func}")
                self._call(func)
            else:
                log.warning(f"  → 未归类的 func={func}，不在 ONE_SHOT/HOLD 中")
        else:
            if btn_idx in self._held:
                self._held.discard(btn_idx)
                still_moving = any(
                    self.mapping.get(f"button_{i}", "none") in HOLD
                    for i in self._held
                )
                if not still_moving:
                    log.info(f"  → HOLD 释放，停止移动")
                    self._stop_movement()

    def _on_axis(self, axis_idx, value):
        self._axes[axis_idx] = value
        key = f"axis_{axis_idx}"
        func = self.mapping.get(key, "none")
        if func in AXIS_FUNC:
            DEADZONE = 0.12
            v = value if abs(value) > DEADZONE else 0.0
            if self.mapping.get(f"{key}_reversed", False):
                v = -v
            # 只在超出死区时打日志，避免刷屏
            if abs(value) > DEADZONE:
                log.info(f"AXIS {key}={value:+.3f} → func={func}, v={v:+.3f}")
            self._call(func, axis_val=v)

    # ── 主循环 ────────────────────────────────────────────────────

    def run(self):
        log.info("=" * 60)
        log.info("XGOController.run() 启动")
        log.info(f"  CONFIG_FILE = {CONFIG_FILE}")
        log.info(f"  GAMEPAD_KEYWORDS = {GAMEPAD_KEYWORDS}")
        log.info("=" * 60)
        self._init_xgo()
        log.info(f"xgo 初始化完成: xgo={'✓' if self.xgo else '✗ None'}, device_type={self.device_type}")
        self._load_mapping()
        self._running = True
        self._start_config_watcher()

        import evdev
        while self._running:
            dev = self._find_gamepad()
            if not dev:
                log.warning("未找到手柄，2秒后重试...")
                time.sleep(2)
                continue

            self._gamepad_dev = dev
            try:
                abs_info = {code: dev.absinfo(code) for code in AXIS_MAP if hasattr(dev, "absinfo")}
                log.info(f"开始监听: {dev.name} ({', '.join(f'ABS {c}' for c in abs_info)})")

                ev_count = 0
                for event in dev.read_loop():
                    if not self._running:
                        break

                    ev_count += 1
                    # 每 50 个事件打一次心跳，确保知道线程还活着
                    if ev_count % 50 == 1:
                        log.debug(f"[事件循环 alive] 已处理 {ev_count} 个事件")

                    if event.type == evdev.ecodes.EV_KEY:
                        idx = BUTTON_MAP.get(event.code)
                        if idx is not None:
                            log.debug(f"EV_KEY code={event.code} idx={idx} value={event.value}")
                            self._on_button(idx, event.value == 1)
                        else:
                            log.debug(f"EV_KEY code={event.code} → 不在 BUTTON_MAP 中，忽略")

                    elif event.type == evdev.ecodes.EV_ABS:
                        code = event.code
                        if code in AXIS_MAP:
                            info = abs_info.get(code)
                            if info and info.max != info.min:
                                norm = (event.value - info.min) / (info.max - info.min) * 2 - 1
                                self._on_axis(AXIS_MAP[code], round(norm, 4))
                        elif code == 16:  # D-pad X
                            self._on_button(14, event.value == -1)
                            self._on_button(15, event.value == 1)
                            if event.value == 0:
                                self._on_button(14, False)
                                self._on_button(15, False)
                        elif code == 17:  # D-pad Y
                            self._on_button(12, event.value == -1)
                            self._on_button(13, event.value == 1)
                            if event.value == 0:
                                self._on_button(12, False)
                                self._on_button(13, False)

            except OSError:
                log.warning("手柄断开连接，等待重连...")
                self._gamepad_dev = None
                self._stop_movement()
                time.sleep(1)
            except Exception as e:
                log.error(f"事件循环异常: {e}")
                time.sleep(1)

    def _on_exit(self, *_):
        log.info("收到退出信号，停止机器人...")
        self._running = False
        self._stop_movement()
        sys.exit(0)


if __name__ == "__main__":
    log.info("XGO 手柄控制器启动")
    XGOController().run()
