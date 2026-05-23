#!/usr/bin/env python3
"""
通用 BLE HID 游戏手柄数据读取器

支持所有走 BLE GATT 非标准通道的手柄（如 ESP32-BLE-Gamepad）。
通过解析设备声明的 HID Report Map 自动理解数据格式，无需硬编码。

工作原理：
1. 通过 BlueZ D-Bus 读取 HID Report Map descriptor (0x2a4b)
2. 解析 Report Map → 得到 Report ID + 字段布局（axes/buttons/hats/sliders）
3. 订阅设备上所有带 notify 的 characteristic
4. 对每条通知，尝试按 Report Map 解析 → 输出标准化事件
5. 如果 HID Report 通道来数据就直接走 evdev；否则 fallback 到 BLE 路径
"""

import asyncio
import logging
import struct
import time
import os
import sys
import threading
import traceback
from typing import Optional, Callable, Dict, List, Tuple
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ============================================================
#  1. HID Report Map 解析器
# ============================================================

# HID item types
class HidItemType:
    MAIN = 0
    GLOBAL = 1
    LOCAL = 2

class HidGlobalTag:
    USAGE_PAGE      = 0x00
    LOGICAL_MIN     = 0x01
    LOGICAL_MAX     = 0x02
    PHYSICAL_MIN    = 0x03  # 跳过，不影响布局
    PHYSICAL_MAX    = 0x04  # 跳过
    UNIT_EXPONENT   = 0x05  # 跳过
    UNIT            = 0x06  # 跳过
    REPORT_SIZE     = 0x07
    REPORT_ID       = 0x08
    REPORT_COUNT    = 0x09

class HidLocalTag:
    USAGE = 0x00
    USAGE_MIN = 0x01
    USAGE_MAX = 0x02

class HidMainTag:
    INPUT = 0x08
    OUTPUT = 0x09
    FEATURE = 0x0B
    COLLECTION = 0x0A
    END_COLLECTION = 0x0C

# Usage Pages
USAGE_PAGE_GENERIC_DESKTOP = 0x01
USAGE_PAGE_BUTTON = 0x09
USAGE_PAGE_SIMULATION = 0x02
USAGE_PAGE_CONSUMER = 0x0C

# Generic Desktop Usages
GD_X = 0x30
GD_Y = 0x31
GD_Z = 0x32
GD_RX = 0x33
GD_RY = 0x34
GD_RZ = 0x35
GD_HAT_SWITCH = 0x39
GD_SLIDER = 0x36
GD_DIAL = 0x37
GD_WHEEL = 0x38

# Simulation Usages
SIM_ACCELERATOR = 0xC4
SIM_BRAKE = 0xC5
SIM_RUDDER = 0xBA
SIM_THROTTLE = 0xBB
SIM_STEERING = 0xC8

@dataclass
class HidField:
    """描述 HID Report 中的一个字段"""
    usage_page: int          # 所属 Usage Page
    usage: int               # Usage ID
    report_id: int           # 所属 Report ID
    bit_offset: int          # 在 report 中的 bit 偏移（不含 report ID）
    bit_size: int            # 位宽
    logical_min: int         # 逻辑最小值
    logical_max: int         # 逻辑最大值
    is_constant: bool        # 常量字段（padding）
    is_variable: bool        # 变量（False = Array）
    has_null: bool           # 支持 null state（hat switch 用）
    axis_type: str = ""      # 'axis', 'hat', 'button', 'slider', 'sim', ''

    @property
    def byte_offset(self) -> int:
        return self.bit_offset // 8

    @property
    def byte_size(self) -> int:
        return (self.bit_size + 7) // 8

    @property
    def is_signed(self) -> bool:
        return self.logical_min < 0


@dataclass
class HidReportLayout:
    """一个 HID Input Report 的完整布局"""
    report_id: int
    fields: List[HidField] = field(default_factory=list)
    total_bytes: int = 0      # Report 数据总字节数（不含 Report ID）
    button_start: int = -1    # 按钮 bitmask 起始 bit 偏移
    button_count: int = 0
    axes: List[HidField] = field(default_factory=list)      # 轴字段 (X/Y/Z/Rx/Ry/Rz)
    hats: List[HidField] = field(default_factory=list)      # Hat 字段
    sliders: List[HidField] = field(default_factory=list)   # Slider/Dial/Wheel
    sims: List[HidField] = field(default_factory=list)      # Simulation controls

    def get_field(self, usage_page: int, usage: int) -> Optional[HidField]:
        for f in self.fields:
            if f.usage_page == usage_page and f.usage == usage:
                return f
        return None


def _decode_value(data: int, signed: bool, bits: int, lmin: int, lmax: int) -> int:
    """将原始数值解码为有符号值"""
    if signed and (data & (1 << (bits - 1))):
        data -= 1 << bits
    return data


def _extract_bits(report: bytes, bit_offset: int, bit_size: int) -> int:
    """从报告中提取指定位宽的值"""
    value = 0
    for i in range(bit_size):
        byte_idx = (bit_offset + i) // 8
        bit_idx = (bit_offset + i) % 8
        if report[byte_idx] & (1 << bit_idx):
            value |= (1 << i)
    return value


def _field_name(field: HidField) -> str:
    """人类可读的字段名"""
    if field.usage_page == USAGE_PAGE_GENERIC_DESKTOP:
        names = {GD_X: 'X', GD_Y: 'Y', GD_Z: 'Z', GD_RX: 'RX', GD_RY: 'RY', GD_RZ: 'RZ',
                 GD_HAT_SWITCH: 'Hat', GD_SLIDER: 'Slider', GD_DIAL: 'Dial', GD_WHEEL: 'Wheel'}
        return names.get(field.usage, f'GD.{field.usage:02X}')
    if field.usage_page == USAGE_PAGE_SIMULATION:
        names = {SIM_ACCELERATOR: 'Accel', SIM_BRAKE: 'Brake', SIM_RUDDER: 'Rudder',
                 SIM_THROTTLE: 'Throttle', SIM_STEERING: 'Steering'}
        return names.get(field.usage, f'Sim.{field.usage:02X}')
    if field.usage_page == USAGE_PAGE_BUTTON:
        return f'Btn{field.usage}'
    if field.usage_page == USAGE_PAGE_CONSUMER:
        return f'Consumer.{field.usage:02X}'
    return f'{field.usage_page:04X}:{field.usage:04X}'


def parse_report_map(data: bytes) -> Dict[int, HidReportLayout]:
    """
    解析 HID Report Map 描述符，返回 {report_id: HidReportLayout}
    """
    reports: Dict[int, HidReportLayout] = {}
    if not data:
        return reports

    # 全局状态
    g = {
        'usage_page': 0,
        'logical_min': 0,
        'logical_max': 0,
        'report_size': 0,
        'report_count': 0,
        'report_id': 0,
    }

    # 局部状态
    l_usages = []
    l_usage_min = 0
    l_usage_max = 0

    # bit 光标
    bit_cursor = 0
    report_bit_cursors = {}

    def _ensure_report(rid):
        if rid not in reports:
            reports[rid] = HidReportLayout(report_id=rid)
        if rid not in report_bit_cursors:
            report_bit_cursors[rid] = 0
        return reports[rid]

    def _add_fields(usages, is_constant=False, is_variable=True, has_null=False, input_val=0):
        nonlocal bit_cursor
        rid = g['report_id']
        size = g['report_size']
        count = g['report_count']
        up = g['usage_page']
        lmin = g['logical_min']
        lmax = g['logical_max']

        if not is_constant and up == 0:
            # 未设置 usage page 的输入当作 padding
            is_constant = True

        for i in range(count):
            usage = usages[i] if i < len(usages) else (usages[-1] if usages else 0)
            layout = _ensure_report(rid)
            offset = report_bit_cursors.get(rid, 0)

            field = HidField(
                usage_page=up,
                usage=usage,
                report_id=rid,
                bit_offset=offset,
                bit_size=size,
                logical_min=lmin,
                logical_max=lmax,
                is_constant=is_constant,
                is_variable=is_variable,
                has_null=has_null,
                axis_type='' if is_constant else '',
            )

            # 分类
            if not is_constant:
                if up == USAGE_PAGE_GENERIC_DESKTOP:
                    if usage == GD_HAT_SWITCH:
                        field.axis_type = 'hat'
                        layout.hats.append(field)
                    elif usage in (GD_X, GD_Y, GD_Z, GD_RX, GD_RY, GD_RZ):
                        field.axis_type = 'axis'
                        layout.axes.append(field)
                    elif usage in (GD_SLIDER, GD_DIAL, GD_WHEEL):
                        field.axis_type = 'slider'
                        layout.sliders.append(field)
                elif up == USAGE_PAGE_SIMULATION:
                    field.axis_type = 'sim'
                    layout.sims.append(field)
                elif up == USAGE_PAGE_BUTTON:
                    field.axis_type = 'button'
                    if layout.button_start < 0:
                        layout.button_start = offset
                    layout.button_count = max(layout.button_count, usage)

            layout.fields.append(field)
            report_bit_cursors[rid] = offset + size

    pos = 0
    while pos < len(data):
        header = data[pos]
        if header == 0xFE:
            length = data[pos + 1]
            pos += length + 3
            continue

        item_type = (header >> 2) & 0x03
        item_tag = (header >> 4) & 0x0F
        item_size = header & 0x03

        byte_count = {0: 0, 1: 1, 2: 2, 3: 4}[item_size]

        # 读取原始字节
        if byte_count == 1:
            raw_val = data[pos + 1]
        elif byte_count == 2:
            raw_val = data[pos + 1] | (data[pos + 2] << 8)
        elif byte_count == 4:
            raw_val = data[pos + 1] | (data[pos + 2] << 8) | (data[pos + 3] << 16) | (data[pos + 4] << 24)
        else:
            raw_val = 0

        # 值解释：仅 LOGICAL_MIN/LOGICAL_MAX 使用有符号，其余无符号
        if item_type == HidItemType.GLOBAL and item_tag in (HidGlobalTag.LOGICAL_MIN, HidGlobalTag.LOGICAL_MAX):
            if byte_count == 1:
                val = struct.unpack('b', bytes([raw_val]))[0]
            elif byte_count == 2:
                val = struct.unpack('<h', bytes([raw_val & 0xFF, (raw_val >> 8) & 0xFF]))[0]
            elif byte_count == 4:
                val = struct.unpack('<i', bytes([raw_val & 0xFF, (raw_val >> 8) & 0xFF, (raw_val >> 16) & 0xFF, (raw_val >> 24) & 0xFF]))[0]
            else:
                val = 0
        else:
            val = raw_val

        pos += 1 + byte_count

        if item_type == HidItemType.MAIN and item_tag == HidMainTag.COLLECTION:
            l_usages = []
            l_usage_min = 0
            l_usage_max = 0
            continue

        if item_type == HidItemType.MAIN and item_tag == HidMainTag.END_COLLECTION:
            l_usages = []
            l_usage_min = 0
            l_usage_max = 0
            continue

        if item_type == HidItemType.GLOBAL:
            if item_tag == HidGlobalTag.USAGE_PAGE:
                g['usage_page'] = val
            elif item_tag == HidGlobalTag.LOGICAL_MIN:
                g['logical_min'] = val
            elif item_tag == HidGlobalTag.LOGICAL_MAX:
                g['logical_max'] = val
            elif item_tag == HidGlobalTag.REPORT_SIZE:
                g['report_size'] = val
            elif item_tag == HidGlobalTag.REPORT_COUNT:
                g['report_count'] = val
            elif item_tag == HidGlobalTag.REPORT_ID:
                g['report_id'] = val
                _ensure_report(val)
            # PHYSICAL_MIN, PHYSICAL_MAX, UNIT_EXPONENT, UNIT: 不影响布局，跳过
            continue

        if item_type == HidItemType.LOCAL:
            if item_tag == HidLocalTag.USAGE:
                l_usages.append(val)
            elif item_tag == HidLocalTag.USAGE_MIN:
                l_usage_min = val
            elif item_tag == HidLocalTag.USAGE_MAX:
                l_usage_max = val
            continue

        if item_type == HidItemType.MAIN and item_tag in (HidMainTag.INPUT, HidMainTag.OUTPUT):
            if item_tag != HidMainTag.INPUT:
                l_usages = []
                l_usage_min = 0
                l_usage_max = 0
                continue

            is_constant = (val & 0x01) == 1
            is_variable = (val & 0x02) == 2
            has_null = (val & 0x40) == 0x40

            # 确定 usage 列表
            if l_usages:
                usages = list(l_usages)
            elif l_usage_max > 0:
                usages = list(range(l_usage_min, l_usage_max + 1))
            else:
                usages = [0]

            _add_fields(usages, is_constant, is_variable, has_null, val)

            # 清理局部状态
            l_usages = []
            l_usage_min = 0
            l_usage_max = 0
            continue

    # 计算每个 report 的 total_bytes
    for rid, layout in reports.items():
        if layout.fields:
            max_bit = max(f.bit_offset + f.bit_size for f in layout.fields)
            layout.total_bytes = (max_bit + 7) // 8

    return reports


# ============================================================
#  2. BLE GATT 通知读取器（基于 dbus-fast）
# ============================================================

# 已知的 BLE HID 相关 UUID
UUID_HID_SERVICE = "00001812-0000-1000-8000-00805f9b34fb"
UUID_HID_REPORT_MAP = "00002a4b-0000-1000-8000-00805f9b34fb"
UUID_HID_REPORT = "00002a4d-0000-1000-8000-00805f9b34fb"
UUID_HID_INFO = "00002a4a-0000-1000-8000-00805f9b34fb"
UUID_BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
UUID_BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

# ESP32-BLE-Gamepad 常见 vendor UUID
ESP32_VENDOR_SERVICE_UUID = "91680001-1111-6666-8888-0123456789ab"
ESP32_VENDOR_CHAR_UUID = "91680003-1111-6666-8888-0123456789ab"


@dataclass
class GamepadEvent:
    """标准化游戏手柄事件"""
    type: str           # 'button', 'axis', 'hat'
    index: int          # 按键号（1-based）/ 轴号（0-based=axis_0,1...）/ hat号
    value: int          # 0/1 for button, -32767~32767 for axis, 0-7 for hat
    timestamp: float = 0.0


class BleHidReader:
    """
    BLE HID 游戏手柄数据读取器

    使用 BlueZ D-Bus API 订阅 BLE GATT 通知，
    解析 HID Report Map 自动理解数据格式。

    使用方法:
        reader = BleHidReader(mac="04:25:0B:00:2B:C1", on_event=callback)
        loop = asyncio.new_event_loop()
        threading.Thread(target=reader.run, args=(loop,), daemon=True).start()
    """

    def __init__(self, mac: str, on_event: Callable[[GamepadEvent], None],
                 adapter: str = "hci0"):
        self.mac = mac.upper()
        self.adapter = adapter
        self.on_event = on_event
        self._device_path = f"/org/bluez/{adapter}/dev_{mac.replace(':', '_')}"
        self._bus = None
        self._report_layouts: Dict[int, HidReportLayout] = {}
        self._primary_rid: int = 0          # 主要的 Input Report ID
        self._notify_paths: List[str] = []  # 已订阅 notify 的 characteristic 路径
        self._alive = False
        self._loop = None

    # ── 公共 API ──────────────────────────────────────────

    def run(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """在独立线程中启动（阻塞直到 stop）"""
        self._loop = loop or asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start())
        except Exception as e:
            log.error(f"BleHidReader fatal: {e}")
            traceback.print_exc()
        finally:
            self._loop.close()

    async def _shutdown(self):
        self._alive = False
        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None

    async def _start(self):
        """连接 D-Bus，读取 Report Map，订阅通知"""
        from dbus_fast.aio import MessageBus
        from dbus_fast import Variant

        self._bus = await MessageBus().connect()
        self._alive = True

        log.info(f"BleHidReader: connecting to {self.mac} via {self.adapter}")

        # 1. 确保设备已连接
        connected = await self._ensure_connected()
        if not connected:
            log.warning(f"BleHidReader: device {self.mac} not connected, will retry")
            # 等待连接
            await self._wait_for_connect()
            connected = await self._ensure_connected()
        if not connected:
            log.error(f"BleHidReader: cannot connect to {self.mac}")
            return

        # 2. 发现 GATT services
        await self._ensure_services_resolved()

        # 3. 读取 Report Map
        layout = await self._read_report_map()
        if layout:
            self._report_layouts = layout
            log.info(f"BleHidReader: parsed {len(layout)} report(s):")
            for rid, rlayout in layout.items():
                log.info(f"  Report {rid}: {len(rlayout.axes)} axes, "
                         f"{rlayout.button_count} buttons, {len(rlayout.hats)} hats, "
                         f"{len(rlayout.sims)} sims, {len(rlayout.sliders)} sliders")
                # 找第一个非 consumer 的 report 作为主控 report
                has_game = any(f.usage_page == USAGE_PAGE_GENERIC_DESKTOP for f in rlayout.fields)
                if has_game and self._primary_rid == 0:
                    self._primary_rid = rid

        if self._primary_rid == 0 and self._report_layouts:
            self._primary_rid = next(iter(self._report_layouts.keys()))

        # 4. 扫描所有 notify-capable characteristic 并订阅
        await self._subscribe_all_notify()

        # 5. 等待通知
        log.info(f"BleHidReader: ready, listening on {len(self._notify_paths)} characteristic(s)")
        while self._alive:
            await asyncio.sleep(3600)  # 保持事件循环活着

    async def _ensure_connected(self) -> bool:
        """确保设备已连接"""
        from dbus_fast import Variant
        try:
            introspection = await self._bus.introspect('org.bluez', self._device_path)
            obj = self._bus.get_proxy_object('org.bluez', self._device_path, introspection)
            dev_props = obj.get_interface('org.freedesktop.DBus.Properties')
            connected = await dev_props.call_get('org.bluez.Device1', 'Connected')
            if connected.value:
                log.info(f"BleHidReader: {self.mac} already connected")
                return True

            dev_iface = obj.get_interface('org.bluez.Device1')
            await dev_iface.call_connect()
            log.info(f"BleHidReader: {self.mac} connect() called")
            return True
        except Exception as e:
            log.warning(f"BleHidReader: connect failed: {e}")
            return False

    async def _wait_for_connect(self, timeout=15.0):
        """等待设备连接"""
        from dbus_fast import Variant
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                obj = self._bus.get_proxy_object(
                    'org.bluez', self._device_path,
                    await self._bus.introspect('org.bluez', self._device_path))
                dev_props = obj.get_interface('org.freedesktop.DBus.Properties')
                connected = await dev_props.call_get('org.bluez.Device1', 'Connected')
                if connected.value:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return False

    async def _ensure_services_resolved(self):
        """等待 ServicesResolved = True"""
        from dbus_fast import Variant
        deadline = time.time() + 10.0
        while time.time() < deadline:
            try:
                obj = self._bus.get_proxy_object(
                    'org.bluez', self._device_path,
                    await self._bus.introspect('org.bluez', self._device_path))
                dev_props = obj.get_interface('org.freedesktop.DBus.Properties')
                resolved = await dev_props.call_get('org.bluez.Device1', 'ServicesResolved')
                if resolved.value:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        log.warning("ServicesResolved timeout")

    async def _read_report_map(self) -> Optional[Dict[int, HidReportLayout]]:
        """读取并解析 HID Report Map"""
        try:
            # 先获取设备下的所有子节点
            root_introspection = await self._bus.introspect('org.bluez', '/')
            obj_mgr = self._bus.get_proxy_object('org.bluez', '/', root_introspection)
            iface = obj_mgr.get_interface('org.freedesktop.DBus.ObjectManager')
            managed = await iface.call_get_managed_objects()

            # 在托管对象中查找 Report Map characteristic
            for path, props in managed.items():
                uuid = props.get('org.bluez.GattCharacteristic1', {}).get('UUID', Variant('s', '')).value
                if uuid == UUID_HID_REPORT_MAP and self._device_path in path:
                    # 读取 Report Map
                    ch_intro = await self._bus.introspect('org.bluez', path)
                    ch_obj = self._bus.get_proxy_object('org.bluez', path, ch_intro)
                    ch_iface = ch_obj.get_interface('org.bluez.GattCharacteristic1')
                    value = await ch_iface.call_read_value({})
                    raw = bytes(value)
                    log.info(f"BleHidReader: Report Map {len(raw)} bytes: {raw[:32].hex()}...")
                    return parse_report_map(raw)

            log.warning("BleHidReader: Report Map characteristic not found")
        except Exception as e:
            log.error(f"BleHidReader: read Report Map failed: {e}")
            traceback.print_exc()
        return None

    async def _subscribe_all_notify(self):
        """订阅所有带 notify 属性的 characteristic"""
        try:
            root_introspection = await self._bus.introspect('org.bluez', '/')
            obj_mgr = self._bus.get_proxy_object('org.bluez', '/', root_introspection)
            iface = obj_mgr.get_interface('org.freedesktop.DBus.ObjectManager')
            managed = await iface.call_get_managed_objects()

            for path, props in managed.items():
                if self._device_path not in path:
                    continue
                ch_props = props.get('org.bluez.GattCharacteristic1', {})
                flags = ch_props.get('Flags', Variant('as', [])).value
                uuid = ch_props.get('UUID', Variant('s', '')).value

                if 'notify' not in flags:
                    continue

                # 优先订阅 HID Report 和 vendor characteristic
                is_hid = uuid == UUID_HID_REPORT
                is_vendor = uuid == ESP32_VENDOR_CHAR_UUID

                if not (is_hid or is_vendor):
                    # 跳过非游戏相关的 characteristic（如电池）
                    continue

                try:
                    ch_intro = await self._bus.introspect('org.bluez', path)
                    ch_obj = self._bus.get_proxy_object('org.bluez', path, ch_intro)
                    ch_iface = ch_obj.get_interface('org.bluez.GattCharacteristic1')

                    # 连接 PropertiesChanged 信号
                    def make_callback(p):
                        def callback(iface_name, changed, invalidated):
                            if 'Value' in changed:
                                raw = bytes(changed['Value'].value)
                                self._on_notification(p, raw)
                        return callback

                    ch_obj.on_properties_changed(make_callback(path))

                    await ch_iface.call_start_notify()
                    self._notify_paths.append(path)
                    log.info(f"BleHidReader: subscribed to {path} (UUID={uuid})")

                except Exception as e:
                    log.warning(f"BleHidReader: subscribe {path} failed: {e}")

        except Exception as e:
            log.error(f"BleHidReader: subscribe_all failed: {e}")
            traceback.print_exc()

    def _on_notification(self, path: str, data: bytes):
        """收到一条 BLE 通知"""
        if not data or not self._report_layouts:
            return

        # 方法1：如果通知包含 Report ID 前缀，用 Report ID 匹配
        if data[0] in self._report_layouts:
            self._decode_report(data[0], data[1:])
            return

        # 方法2：自动对齐：在 data 中滑动窗口，找到按钮 bitmask 匹配的位置
        best_layout = None
        best_data = None
        for rid, layout in self._report_layouts.items():
            # 跳过 consumer control reports
            has_game = any(f.usage_page == USAGE_PAGE_GENERIC_DESKTOP for f in layout.fields)
            if not has_game and rid != self._primary_rid:
                continue
            # 尝试是否包含 Report ID
            if rid in data:
                idx = data.index(rid)
                try:
                    self._decode_report(rid, data[idx+1:])
                except Exception:
                    pass
                return
            # Report ID 可能在数据中但不是第一个字节
            # 尝试滑动窗口
            for offset in range(min(10, len(data) - layout.total_bytes + 1)):
                try:
                    self._decode_report(rid, data[offset:offset+layout.total_bytes])
                    return
                except Exception:
                    continue

        # 如果都匹配不上，用 primary report layout 直接解析（去掉可能的前缀）
        if self._primary_rid in self._report_layouts:
            layout = self._report_layouts[self._primary_rid]
            # 尝试常见偏移
            for offset in range(min(8, len(data) - layout.total_bytes)):
                try:
                    self._decode_report(self._primary_rid, data[offset:offset+layout.total_bytes])
                    return
                except Exception:
                    continue

    def _decode_report(self, rid: int, report: bytes):
        """解析数据并发出事件"""
        if rid not in self._report_layouts:
            return
        layout = self._report_layouts[rid]
        if len(report) < layout.total_bytes:
            return

        ts = time.time()

        # 解析按钮
        if layout.button_start >= 0 and layout.button_count > 0:
            # 从 bit offset 读取 button_count 个 bit
            for btn in range(layout.button_count):
                bit = layout.button_start + btn
                byte_idx = bit // 8
                bit_idx = bit % 8
                if byte_idx < len(report):
                    pressed = (report[byte_idx] >> bit_idx) & 1
                    self.on_event(GamepadEvent('button', btn + 1, pressed, ts))

        # 解析轴
        axis_index = 0
        for field in layout.axes:
            raw = _extract_bits(report, field.bit_offset, field.bit_size)
            # 映射到 -32767 ~ 32767 范围（兼容 XGOController）
            if field.logical_max > field.logical_min:
                normalized = (raw - field.logical_min) / (field.logical_max - field.logical_min)
                mapped = int(normalized * 65535 - 32767)
            else:
                mapped = 0
            self.on_event(GamepadEvent('axis', axis_index, mapped, ts))
            axis_index += 1

        # 解析 hat switch
        for field in layout.hats:
            raw = _extract_bits(report, field.bit_offset, field.bit_size)
            self.on_event(GamepadEvent('hat', 1, raw, ts))

        # 解析 simulation controls (映射为额外 axis)
        for field in layout.sims:
            raw = _extract_bits(report, field.bit_offset, field.bit_size)
            if field.logical_max > field.logical_min:
                normalized = (raw - field.logical_min) / (field.logical_max - field.logical_min)
                mapped = int(normalized * 65535 - 32767)
            else:
                mapped = 0
            self.on_event(GamepadEvent('axis', axis_index, mapped, ts))
            axis_index += 1

        # 解析 sliders (映射为额外 axis)
        for field in layout.sliders:
            raw = _extract_bits(report, field.bit_offset, field.bit_size)
            if field.logical_max > field.logical_min:
                normalized = (raw - field.logical_min) / (field.logical_max - field.logical_min)
                mapped = int(normalized * 65535 - 32767)
            else:
                mapped = 0
            self.on_event(GamepadEvent('axis', axis_index, mapped, ts))
            axis_index += 1


# ============================================================
#  3. 自动对齐解码器（用于 vendor characteristic 等非标准通道）
# ============================================================

def decode_notification(report: bytes, layout: HidReportLayout) -> Optional[List[GamepadEvent]]:
    """
    尝试从原始 BLE 通知中提取 HID 报告数据并解码为 GamepadEvent 列表
    
    自动尝试不同字节偏移，找到最佳对齐位置。
    返回 None 表示对齐失败。
    """
    if not report or not layout or layout.total_bytes <= 0:
        return None

    ts = time.time()

    # 方法1：检查 report 是否以 Report ID 开头
    if report[0] == layout.report_id and len(report) >= 1 + layout.total_bytes:
        return _decode_aligned(report[1:1+layout.total_bytes], layout, ts)

    # 方法2：在 report 中搜索 Report ID
    for offset in range(min(16, len(report) - layout.total_bytes)):
        if report[offset] == layout.report_id:
            data = report[offset+1:offset+1+layout.total_bytes]
            if len(data) == layout.total_bytes:
                events = _decode_aligned(data, layout, ts)
                if events and _check_events_plausible(events):
                    return events

    # 方法3：滑动窗口，找最佳对齐（按钮字段无 phantom press）
    best_offset = -1
    best_score = -999
    for offset in range(min(12, len(report) - layout.total_bytes + 1)):
        data = report[offset:offset+layout.total_bytes]
        if len(data) < layout.total_bytes:
            continue
        events = _decode_aligned(data, layout, ts)
        if events:
            score = _score_alignment(events)
            if score > best_score:
                best_score = score
                best_offset = offset

    if best_offset >= 0:
        data = report[best_offset:best_offset+layout.total_bytes]
        events = _decode_aligned(data, layout, ts)
        if events is not None:
            # ESP32-BLE-Gamepad 面键补偿（对齐确定后执行，不影响评分）
            #   高4位编码: bit4(0x10)→B(btn5), bit5(0x20)→A(btn6), bit6(0x40)→Y(btn7), bit7(0x80)→X(btn8)
            _apply_esp32_face_buttons(data, layout, events, ts)
        return events

    return None


def _apply_esp32_face_buttons(data: bytes, layout, events: list, ts: float):
    """ESP32-BLE-Gamepad 面键补偿：把 hat 字节高4位的面键映射为 btn5-8"""
    if len(data) < 6:
        return
    if layout.button_start != 40:
        return
    std_buttons_high = data[5] & 0xF0
    if std_buttons_high != 0:
        return
    face_raw = (data[4] >> 4) & 0x0F
    if not face_raw:
        return
    for i in range(4):
        pressed = bool(face_raw & (1 << i))
        events.append(GamepadEvent('button', 5 + i, pressed, ts))


def _decode_aligned(data: bytes, layout: HidReportLayout, ts: float) -> List[GamepadEvent]:
    """解码已对齐的 HID 报告数据"""
    events = []

    # 按钮
    if layout.button_start >= 0 and layout.button_count > 0:
        for btn in range(layout.button_count):
            bit = layout.button_start + btn
            byte_idx = bit // 8
            bit_idx = bit % 8
            if byte_idx < len(data):
                pressed = (data[byte_idx] >> bit_idx) & 1
                events.append(GamepadEvent('button', btn + 1, pressed, ts))

    # ESP32-BLE-Gamepad 面键补偿已移至 decode_notification 中执行
    # 避免影响 _score_alignment 的对齐评分

    # 轴（按 bit_offset 升序排列保证 axis_index 稳定）
    sorted_axes = sorted(layout.axes, key=lambda f: f.bit_offset)
    for axis_index, field in enumerate(sorted_axes):
        raw = _extract_bits(data, field.bit_offset, field.bit_size)
        rng = field.logical_max - field.logical_min
        if rng > 0:
            normalized = (raw - field.logical_min) / rng
            mapped = int(normalized * 65535 - 32767)
        else:
            mapped = 0
        events.append(GamepadEvent('axis', axis_index, mapped, ts))

    # hat
    for fi, field in enumerate(layout.hats):
        raw = _extract_bits(data, field.bit_offset, field.bit_size)
        # 映射 hat(0-7) 为 axis 事件（兼容现有映射）
        events.append(GamepadEvent('hat', fi + 1, raw, ts))

    # simulation controls → extra axes
    sim_start = len(sorted_axes)
    for si, field in enumerate(layout.sims):
        raw = _extract_bits(data, field.bit_offset, field.bit_size)
        rng = field.logical_max - field.logical_min
        if rng > 0:
            normalized = (raw - field.logical_min) / rng
            mapped = int(normalized * 65535 - 32767)
        else:
            mapped = 0
        events.append(GamepadEvent('axis', sim_start + si, mapped, ts))

    return events


def _check_events_plausible(events: List[GamepadEvent]) -> bool:
    """检查解码结果是否合理（大致检查）"""
    btn_count = sum(1 for e in events if e.type == 'button' and e.value == 1)
    # 同时按下超过 8 个按钮大概率是误对齐
    if btn_count > 8:
        return False
    # 所有轴都在极端值 → 误对齐
    extreme_axes = sum(1 for e in events if e.type == 'axis' and abs(e.value) > 32000)
    if extreme_axes > len([e for e in events if e.type == 'axis']) * 0.7:
        return False
    return True


def _score_alignment(events: List[GamepadEvent]) -> int:
    """给对齐打分：按钮越少越好，轴越接近中心越好"""
    score = 0
    for e in events:
        if e.type == 'button':
            score -= e.value * 10  # 避免 phantom button press
        elif e.type == 'axis':
            score -= abs(e.value) // 1000  # 轴偏离中心扣分
    return score

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(name)s] %(message)s')

    # 测试 Report Map 解析
    test_map = bytes.fromhex(
        "050c0901a1018503751095011501269c0219012a9c028100c0"
        "05010905a10185040901a1000930093109320935150026ff00"
        "750895048102c00939150025073500463b0165147504950181"
        "42750495018101050919012910150025017501951081020502"
        "150026ff0009c409c5950275088102750895018101c0"
    )
    reports = parse_report_map(test_map)
    for rid, layout in reports.items():
        print(f"\nReport {rid}: {layout.total_bytes} bytes")
        for f in layout.fields:
            if f.is_constant:
                continue
            print(f"  {_field_name(f)}: bit[{f.bit_offset}:{f.bit_offset+f.bit_size-1}] "
                  f"sz={f.bit_size} range=[{f.logical_min},{f.logical_max}] "
                  f"type={f.axis_type}")

    # 测试数据解析
    if 4 in reports:
        layout = reports[4]
        print("\n=== Test parse neutral state ===")
        # Neutral report (from BM769): skip 3-byte wrapper 20 0f 00 → 80 80 80 80 00 00 00 ff 00 00 00
        sample = bytes([0x80, 0x80, 0x80, 0x80, 0x00, 0x00, 0x00, 0xff, 0x00, 0x00])
        print(f"Input: {sample.hex()}")
        for f in layout.fields:
            if f.is_constant:
                continue
            raw = _extract_bits(sample, f.bit_offset, f.bit_size)
            print(f"  {_field_name(f)}: {raw} ({raw:#x})")
