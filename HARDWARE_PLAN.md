# CM5 硬件架构改造计划

## 核心原则

> **硬件永远只有一个主人（内核驱动/系统服务），App 通过标准接口共享访问。**

```
老方案：每个 App 直接抢 /dev/spidev、GPIO、摄像头 → 独占锁死
新方案：内核/守护进程独占硬件 → 标准接口 → 多 App 共享
```

---

## 硬件总览

| # | 硬件 | 接口 | 当前驱动 | 问题 | 目标 |
|---|------|------|---------|------|------|
| 1 | SPI LCD (ST7789V, 240×320) | SPI0 + GPIO 27/25/0 | userspace spidev | 独占 | /dev/fb-spi（udev软链接） |
| 2 | 摄像头 (OV5647) | CSI | Picamera2 独占 | 独占 | 各 App 自行 Picamera2.start/stop |
| 3 | 4 按键 (A/B/C/D) | GPIO 17/22/23/24 | pinctrl 轮询 | 独占+慢 | /dev/input/eventX |
| 4 | 蓝牙 (Cypress) | mini-UART ttyS0 | bluetoothd | 串口不稳 | 等硬件换真 UART |
| 5 | 音频 + 麦克风 (WM8960) | I2S + I2C | ALSA card 0 | 独占+啸叫 | ALSA dmix + dsnoop |
| 6 | 板载麦克风 (WM8960 内置) | LINPUT1/RINPUT1 | Capture via ALSA | 输入增益未调 | LINPUT1/RINPUT1 + dsnoop |
| 7 | 机器狗 UART | ttyAMA0 (PL011) → 迁移到其他 UART | xgolib_dog.py | 跟蓝牙抢串口 | 不写死 + 传参 + 自动扫描 |
| 8 | 舵机/机械臂/IMU/电池 | UART → 下位机 MCU | xgolib_dog.py 转发 | 无 | ✅ 无需改动 |
| 9 | 散热风扇 (4线 PWM) | GPIO PWM + TACH | pwm-fan 内核驱动 (hwmon2) | 无 | ✅ 温控自动调速 |

---

## 改造 1：SPI LCD → /dev/fb-spi（udev软链接）

### 当前
```
App Python → spidev → GPIO → ST7789V  （独占）
```

### 目标
```
Qt App → LinuxFB → /dev/fb-spi（软链接） → fbtft 内核驱动 → DMA → ST7789V  （共享）
```

### 做法
1. `/boot/firmware/config.txt` 加 fbtft overlay：
   ```
   dtoverlay=fbtft,spi0-0,st7789v,reset_pin=27,dc_pin=25,rotate=270,speed=40000000
   ```
2. 创建 udev 规则 `/etc/udev/rules.d/99-fb-spi.rules`：
   ```
   SUBSYSTEM=="graphics", ATTR{name}=="fb_st7789v", SYMLINK+="fb-spi"
   ```
   > udev 按设备名匹配（`fb_st7789v`），不管实际编号是 fb0/fb1/fb2，软链接 `/dev/fb-spi` 始终指向它。
3. Qt 启动时用软链接：
   ```bash
   QT_QPA_PLATFORM=linuxfb:fb=/dev/fb-spi ./my_app
   ```

### 效果
- 多程序可同时 open `/dev/fb-spi`
- DMA 搬运像素，CPU 零开销
- Qt 原生支持，无需自己写 SPI
- **以后加 HDMI 桌面（KMS/DRM），fb 编号变了，Qt 代码零改动**

---

## 改造 2：摄像头 → 各 App 自行管理 Picamera2

### 当前
```
App Python → Picamera2 独占摄像头
```

### 目标
```
PySide6 App → Picamera2.start() → 采帧 → Picamera2.stop() → 释放
```

### 决策依据
- 系统采用**单 App 全屏独占**模式（启动器同一时间只跑一个 PySide6 App），不存在多 App 并发抢摄像头
- camera_daemon + 共享内存方案在当前约束下属于过度设计
- OV5647 激活功耗约 200~300mW，电池供电场景下用完即关最省电

### 做法
- 每个 PySide6 App 自行 `Picamera2().start()` / `.stop()`，生命周期跟 App 一致
- 不再依赖 `edulib.py`（老硬件库），直接使用 Picamera2 API
- App 退出时必须 `close()`，防止 `/dev/video0` 残留锁定
- 后续可从 `edulib.py` 中抽离 AI 功能（人脸/手势/颜色识别）到 `apps/` 下的独立工具模块

### 启动延迟
- `Picamera2.start()` + sensor 初始化约 1 秒，每个 App 冷启动均需等待
- 可接受，PySide6 App 自身的 import + widget 构建也需数百毫秒，总延迟在体感范围内

---

## 改造 3：按键 → /dev/input

### 当前
```python
# 每读一次按键，fork 一个子进程
subprocess.run(["sudo", "pinctrl", "level", "24"])
time.sleep(0.02)
```

### 目标
```
Qt App → QKeyEvent → /dev/input/eventX → gpio-keys 内核驱动 → 硬件中断
```

### 按键映射
| 物理按键 | GPIO | 映射 |
|---------|------|------|
| A (左上) | 17 | KEY_LEFT |
| B (右上) | 22 | KEY_RIGHT |
| C (左下) | 23 | KEY_BACK |
| D (右下) | 24 | KEY_ENTER |

### 做法
- device tree 配置 gpio-keys
- 内核自动去抖 + 中断驱动
- Qt 原生 keyPressEvent 处理

---

## 改造 4：蓝牙 → 拿回 PL011，机器人搬家

### 物理约束
> Cypress 蓝牙芯片焊死在 GPIO 14/15。这组引脚内部只能接 PL011 或 mini-UART（二选一），无法接 RP1 的其他 UART。

### 当前
```
GPIO 14/15 ──→ mini-UART (ttyS0) ──→ 蓝牙 ← 飘，依赖 core_freq=250 锁频
GPIO 14/15  ─→ PL011 (ttyAMA0)   ──→ 机器人 ← 稳但抢了蓝牙的好串口
```

### 目标
```
GPIO 14/15 ──→ PL011 (ttyAMA0)    ──→ 蓝牙 ✅ 稳了
其他 GPIO   ──→ RP1 UARTx        ──→ 机器人 ✅ 稳了
```

### 硬件要做什么
- 机器人下位机的 TX/RX 飞线到 RP1 空闲 UART 对应引脚
- config.txt 启用对应 UART overlay

### 软件要做什么
- `bluetoothd` 配置改回 ttyAMA0
- `xgolib_dog.py` 第 188 行：`port` 参数原本就有，别写死，改用参数传入（改一行）

```python
# xgolib_dog.py 第 186-188 行，改前：
def __init__(self, port, baud=115200, ...):
    self.ser = serial.Serial("/dev/ttyAMA0", baud, timeout=0.5)  # 写死了

# 改后：
def __init__(self, port="/dev/ttyAMA0", baud=115200, ...):
    self.ser = serial.Serial(port, baud, timeout=0.5)  # 用参数
```

### 自动扫描（可选增强）
> 利用下位机固件查询协议（返回版本号 `M...`/`L...`/`W...`/`R...`），可遍历 `/dev/ttyAMA*`、`/dev/ttyS*` 自动发现匹配串口。即使硬件换 UART 也无需手动配路径。

扫描原理：
- 向候选串口发送固件版本查询命令
- 收到合法版本号回复 → 即命中
- 未命中 → 退回默认值 `/dev/ttyAMA0`

- 其余 App 通过 D-Bus 调用蓝牙，完全不受影响

---

## 改造 5：音频 → ALSA dmix + dsnoop

### 当前
```
App A → aplay → hw:0,0 独占  → App B 再开报错 "Device busy"
App   → edulib/XGOEDU → 混音器全开 → 麦克风-扬声器硬件回授啸叫
```

### 目标
```
App A ──→ default ──→ plug:dmixer ──→ dmix ──→ hw:0,0
App B ──→ default ──→ plug:dmixer ──→ dmix ──→ hw:0,0  （可同时播放）
App C ──→ default ──→ plug:dsnooper ──→ dsnoop ──→ hw:0,0  （可同时录音）
```

### 决策依据
- 嵌入式场景下 PulseAudio/PipeWire 太重（常驻进程 + 上百 MB 内存）
- ALSA `dmix` / `dsnoop` 是纯配置方案，零额外进程，资源占用接近零
- `plug` 插件自动做采样率/通道转换，App 无需关心底层格式

### 做法
1. `/etc/asound.conf` 配置 `dmixer` + `dsnooper`：
   ```
   default ──→ asym ──┬─ playback.pcm → plug → dmix  → hw:0,0
                      └─ capture.pcm  → plug → dsnoop → hw:0,0
   ```
2. 恢复混音器状态 `alsactl restore`：
   - 啸叫修复：关闭 4 条 Output Mixer 回授通路（`mute`）
   - 默认音量限制在 71%（Capture 45/63），播放 100%（Playback 255/255），Speaker/Headphone 109/127（86%）
3. App 通过 `aplay` / `arecord` 或 Python `subprocess` 直接播放/录音，天然支持混音

### 效果
- 多个 App 可同时播放/录音，不再 `Device busy`
- 开机默认音量安全，避免炸耳
- 无啸叫

### 麦克风通路现状
- 板载麦克风走 LINPUT1（左）和 RINPUT1（右），输入增益当前为最低档（1/7），Input Mixer Boost 已开
- 录音通路：麦克风 → LINPUT1/RINPUT1 → Boost Mixer → Input Mixer → ADC → Capture (71%, on)
- dsnoop 已配置，多个 App 可同时录音
- 待后续按需调整：输入增益（`Left/Right Input Boost Mixer LINPUT1 Volume`）、高通滤波器（`ADC High Pass Filter`）

---

## 改造 6：风扇 → pwm-fan 内核温控

### 当前
```
config.txt → dtparam=fan_temp → pwm-fan 内核驱动 → /sys/class/hwmon/hwmon2 → 4线 PWM 风扇
```

### 实际
- 4线 PWM 风扇（VCC/GND/PWM/TACH），TACH 反馈真实转速
- 内核驱动 `pwm-fan` 按温度自动调速，`/sys/class/hwmon/hwmon2/` 暴露 pwm1 + fan1_input
- config.txt 温控曲线：36°C起转80%，40°C/52°C/58°C 逐级提速

### 效果
- 零用户态进程，内核自动温控
- 无需额外改造，已是目标状态

---

## 终局架构图

```
硬件          → 唯一主人          → 标准接口       → 多 App 共享
──────────────────────────────────────────────────────────
SPI LCD      → fbtft 内核驱动     → /dev/fb-spi（udev）→ mmap 写
摄像头       → 各 App (Picamera2)  → 用完即关        → 单 App 独占
按键         → gpio-keys 驱动     → /dev/input     → open 读
蓝牙         → bluetoothd         → D-Bus          → D-Bus 调用
音频         → ALSA dmix/dsnoop   → default PCM    → 多 App 混音
麦克风       → ALSA dsnoop        → Capture PCM    → 多 App 录音
机器狗串口   → xgolib_dog.py      → RP1 UARTx（参数化）→ 单程序
散热风扇     → pwm-fan 内核驱动   → hwmon2          → 温控自动
```

## 相关代码

| 文件 | 说明 |
|------|------|
| `/boot/firmware/config.txt` | 硬件配置（overlay + 风扇温控 + UART）|
| `/home/pi/luwu-os/configs/boot-config.txt` | config.txt 模板，install.sh 部署 |
| `/home/pi/luwu-os/configs/install.sh` | 一键部署脚本（依赖 + overlay + udev + ALSA + systemd）|
| `/home/pi/luwu-os/configs/luwu-keys.dts` | gpio-keys 设备树源文件 |
| `/home/pi/luwu-os/configs/luwu-launcher.service` | Luwu OS 桌面启动器 systemd 服务 |
| `/home/pi/luwu-os/configs/99-fb-spi.rules` | udev 规则（/dev/fb-spi 软链接）|
| `/home/pi/lib/edulib.py` | 老硬件库，已弃用（摄像头改用 Picamera2 直调）|
| `/home/pi/lib/xgolib_dog.py` | 机器狗运动库，保留 |
| `/home/pi/lib/xgoscreen/` | 老 LCD 驱动，已被 fbtft 替代 |
| `/etc/asound.conf` | ALSA dmix/dsnoop 配置，多 App 音频共享 |
| `/var/lib/alsa/asound.state` | 混音器持久化状态（啸叫修复 + 默认音量）|
