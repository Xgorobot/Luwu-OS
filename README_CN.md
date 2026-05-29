<p align="center">
  <img src="snaps/luwuos.png" width="360" alt="Luwu-OS Logo" />
</p>

<h1 align="center">Luwu-OS</h1>

<p align="center">
  <strong>面向教育机器人的轻量级嵌入式桌面操作系统</strong>
  <br />
  Raspberry Pi CM4 / CM5 · Qt C++ 启动器 · PySide6 应用 · Linux Framebuffer
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python" />
  <img src="https://img.shields.io/badge/c%2B%2B-17-blue.svg" alt="C++" />
  <img src="https://img.shields.io/badge/qt-5.15-green.svg" alt="Qt" />
  <img src="https://img.shields.io/badge/platform-Raspberry%20Pi%20CM4%2FCM5-red.svg" alt="Platform" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
</p>

<p align="center">
  <a href="https://pypi.org/project/xgolib/"><img src="https://img.shields.io/pypi/v/xgolib.svg?label=xgolib" alt="xgolib" /></a>
  <a href="https://pypi.org/project/xgoedu-luwuos/"><img src="https://img.shields.io/pypi/v/xgoedu-luwuos.svg?label=xgoedu-luwuos" alt="xgoedu-luwuos" /></a>
  <a href="https://pypi.org/project/xgo-blockly-luwuos/"><img src="https://img.shields.io/pypi/v/xgo-blockly-luwuos.svg?label=xgo-blockly-luwuos" alt="xgo-blockly-luwuos" /></a>
</p>

<p align="center">
  <a href="README.md">📖 English</a>
</p>

---

## 目录

- [概述](#-概述)
- [界面截图](#-界面截图)
- [功能亮点](#-功能亮点)
- [硬件规格](#-硬件规格)
- [系统架构](#-系统架构)
- [快速开始](#-快速开始)
- [项目结构](#-项目结构)
- [应用列表](#-应用列表)
- [开发指南](#-开发指南)
- [参与贡献](#-参与贡献)
- [开源协议](#-开源协议)

---

## 📖 概述

**Luwu-OS** 是一款专为教育机器人打造的嵌入式操作系统。它运行在 **Raspberry Pi CM4 和 CM5** 上，驱动一整套交互式应用 — 从 AI 语音对话、手势识别，到 Blockly 图形化编程和多机器人群组表演。

系统采用 **Qt C++ 启动器** 统一管理多个 **PySide6 应用进程**，所有界面直接渲染到 240×320 SPI LCD 的 Linux 帧缓冲上 — 无需 X11、无需 Wayland，零显示服务器开销。

> **使命**：让每一位学生都能在机器人上获得直观、流畅、充满乐趣的交互体验。

---

## 📸 界面截图

### 🏠 主页 & 卡片

<p align="center">
  <img src="snaps/中文截图/01_gallery_card_ai.png" width="32%" alt="AI 对话卡片" />
  <img src="snaps/中文截图/01_gallery_card_coding.png" width="32%" alt="编程卡片" />
  <img src="snaps/中文截图/01_gallery_card_demos.png" width="32%" alt="演示卡片" />
</p>

<p align="center">
  <img src="snaps/中文截图/01_gallery_card_network.png" width="32%" alt="网络卡片" />
  <img src="snaps/中文截图/01_gallery_card_settings.png" width="32%" alt="设置卡片" />
</p>

### 🧠 AI 配置 & 编程

<p align="center">
  <img src="snaps/中文截图/ai-chat-配置.png" width="48%" alt="AI 对话配置" />
  <img src="snaps/中文截图/blockly.png" width="48%" alt="Blockly 编程" />
</p>



### 🤖 交互功能

<p align="center">
  <img src="snaps/中文截图/图传模式.png" width="32%" alt="图传模式" />
  <img src="snaps/中文截图/人脸识别.png" width="32%" alt="人脸识别" />
  <img src="snaps/中文截图/图传模式-网页控制.png" width="32%" alt="图传模式网页控制" />
</p>

<p align="center">
  <img src="snaps/中文截图/小球抓取.png" width="48%" alt="小球抓取" />
  <img src="snaps/中文截图/手势识别.png" width="48%" alt="手势识别" />
</p>

### 🎭 更多功能

<p align="center">
  <img src="snaps/中文截图/群组表演.png" width="32%" alt="群组表演" />
  <img src="snaps/中文截图/群组表演动作编辑.png" width="32%" alt="群组表演动作编辑" />
  <img src="snaps/中文截图/蓝牙遥控.png" width="32%" alt="蓝牙遥控" />
</p>

<p align="center">
  <img src="snaps/中文截图/手柄键位映射.png" width="32%" alt="手柄键位映射" />
  <img src="snaps/中文截图/表演模式.png" width="32%" alt="表演模式" />
  <img src="snaps/中文截图/雷达扫描.png" width="32%" alt="雷达扫描" />
</p>

---

## ✨ 功能亮点

| 分类 | 能力描述 |
|------|---------|
| 🧠 **AI 助手** | 语音对话、情绪识别与表情反馈、实时 TTS/ASR |
| 🎮 **手柄控制** | 蓝牙 / 有线手柄支持，带摇杆校准功能 |
| 🖐️ **手势识别** | 基于 MediaPipe 的实时手势姿态估计 |
| 👤 **人脸跟随** | 机载摄像头实时人脸检测与跟踪 |
| ⚽ **小球跟随** | 基于颜色的小球检测、跟踪与抓取 |
| 📡 **图传模式** | 手机网页远程遥控，实时摄像头画面回传 |
| 🗺️ **雷达扫描** | 基于激光雷达的 360° 环境扫描可视化 |
| 🎭 **群组表演** | MQTT 同步的多机器人协同编舞 |
| 🧩 **Blockly 编程** | 拖拽式图形化编程，设备端直接生成 Python 代码 |
| 🌐 **WiFi 配网** | 扫码连接 WiFi、热点管理 |
| ⚙️ **系统设置** | 中英文切换、音量控制、设备信息 |

---

## 🔧 硬件规格

| # | 组件 | 接口 | 驱动 |
|---|------|------|------|
| 1 | **SPI LCD** (ST7789V, 240×320) | SPI0 + GPIO 27/25/0 | `fbtft` 内核驱动 → `/dev/fb-spi` |
| 2 | **摄像头** (OV5647) | CSI | Picamera2（每个应用自行管理生命周期） |
| 3 | **4 颗实体按键** (A/B/C/D) | GPIO 17/22/23/24 | `gpio-keys` → `/dev/input/eventX` |
| 4 | **蓝牙** (Cypress) | mini-UART / PL011 | `bluetoothd` 通过 D-Bus |
| 5 | **音频 + 麦克风** (WM8960) | I2S + I2C | ALSA `dmix` + `dsnoop` |
| 6 | **机器狗串口** | ttyAMA0 / UART5 | `xgolib`（自动识别） |
| 7 | **舵机 / IMU / 电池** | UART → MCU | `xgolib` 转发 |
| 8 | **散热风扇** (4线 PWM) | GPIO PWM + TACH | `pwm-fan` 内核驱动 (hwmon) |
| 9 | **电源 + 电池** | VC 电压监控 + UART | `vcgencmd` + `xgolib` 联合监控 |

> 完整硬件架构、引脚定义及迁移方案详见 [HARDWARE_PLAN.md](HARDWARE_PLAN.md)。

### 设计原则

> **每个硬件外设有且只有一个主人 — 内核驱动或系统服务。**
> **应用通过标准接口访问硬件，绝不互相争抢裸设备。**

---

## 🏗️ 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                    Qt C++ 启动器                          │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ Gallery │  │ StatusBar│  │ KeyFilter │  │ QProcess │  │
│  │  视图   │  │ (电池)   │  │  (evdev)  │  │  管理器  │  │
│  └─────────┘  └──────────┘  └───────────┘  └────┬─────┘ │
└─────────────────────────────────────────────────┼────────┘
                                                  │ 启动子进程
         ┌────────────────────────────────────────┼───────┐
         │            PySide6 应用 (LinuxFB)      │       │
         │  ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐│       │
         │  │  AI  │ │编程  │ │演示  │ │ 设置   ││  ...  │
         │  │ 对话 │ │页面  │ │页面  │ │ 页面   ││       │
         │  └──────┘ └──────┘ └──────┘ └────────┘│       │
         └────────────────────────────────────────┘       │
                                                          │
  ┌───────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│                  Linux 内核层                             │
│  fbtft │ gpio-keys │ ALSA dmix │ pwm-fan │ vcgencmd     │
└──────────────────────────────────────────────────────────┘
```

| 层级 | 技术栈 | 职责 |
|------|--------|------|
| **启动器** | Qt 5.15 C++ (EGLFS / LinuxFB) | 进程生命周期管理、按键路由、状态栏 |
| **应用层** | PySide6 Python (LinuxFB) | 功能应用，渲染到帧缓冲 |
| **硬件抽象** | 内核驱动 + systemd 服务 | 单点独占硬件访问，通过标准 Linux 接口暴露 |
| **进程通信** | FIFO (`/tmp/luwu_keys.fifo`)、D-Bus、MQTT | 跨进程数据交换 |

---

## 🚀 快速开始

### 环境要求

- Raspberry Pi CM4 或 CM5 及兼容底板
- SPI LCD (ST7789V) 通过 SPI0 连接
- Debian 系 Linux 操作系统

### 安装

```bash
git clone https://github.com/your-org/Luwu-OS.git /opt/luwu-os
cd /opt/luwu-os
sudo bash configs/install.sh
```

一条命令完成全部部署：
- 内核 Overlay（SPI LCD、gpio-keys、风扇控制）
- udev 规则（`/dev/fb-spi` 软链接）
- ALSA 多应用音频共享（`dmix` + `dsnoop`）
- systemd 服务（启动器、欠压监控、硬件自动识别）
- Python 依赖安装（`pip`）

核心 Python 包：
- [`xgolib`](https://pypi.org/project/xgolib/) — 机器狗运动库
- [`xgoedu-luwuos`](https://pypi.org/project/xgoedu-luwuos/) — 教育库（PySide6 QPainter）
- [`xgo-blockly-luwuos`](https://pypi.org/project/xgo-blockly-luwuos/) — Blockly 图形化编程

### 启动

系统开机通过 systemd 自动启动：

```bash
sudo systemctl enable luwu-launcher
sudo systemctl start luwu-launcher
```

### 开发模式

可直接运行单个应用进行开发调试：

```bash
# 进入应用目录
cd apps/ai
python main.py

# 或者运行编程应用
cd apps/coding
python main.py
```

---

## 📂 项目结构

```
Luwu-OS/
├── launcher/              # Qt C++ 主启动器（Gallery、StatusBar、KeyFilter）
│   ├── main.cpp           # 入口，QProcess 管理、按键路由
│   ├── galleryview.cpp    # 卡片式应用列表界面
│   ├── statusbar.cpp      # 电池 + 时间状态栏
│   └── ...
├── apps/                  # PySide6 功能应用
│   ├── ai/                # AI 语音对话、情绪识别、TTS/ASR
│   ├── coding/            # Blockly 图形化编程
│   ├── demo_page/         # 演示启动页
│   ├── ball_track/        # 颜色小球跟随
│   ├── ball_catch/        # 小球抓取（舵机控制）
│   ├── face_follow/       # 人脸检测与跟踪
│   ├── gesture/           # 手势识别
│   ├── gamepad/           # 蓝牙/USB 手柄控制
│   ├── rc_mode/           # 网页远程遥控
│   ├── radar/             # 激光雷达 360° 扫描
│   ├── perform/           # 预编排表演动作
│   ├── group_perform/     # MQTT 同步群组表演
│   ├── joystick/          # 摇杆控制界面
│   ├── hotspot/           # WiFi 热点管理
│   ├── network/           # 网络配置
│   └── settings/          # 系统设置（语言、音量、设备信息）
├── configs/               # 系统配置文件
│   ├── install.sh         # 一键部署脚本
│   ├── boot-config.txt    # 内核配置模板（新硬件）
│   ├── cm4-old.config     # 内核配置模板（旧硬件）
│   ├── luwu-keys.dts      # gpio-keys 设备树源文件
│   ├── asound.conf        # ALSA dmix/dsnoop 配置
│   ├── asound.state       # 混音器状态（防啸叫 + 安全音量）
│   └── *.service          # systemd 服务单元文件
├── libs/                  # 共享 Python 库
│   ├── xgolib/            # 机器狗运动库
│   ├── xgoedu-luwuos/     # 教育库（PySide6 QPainter）
│   ├── theme/             # UI 主题
│   ├── ui/                # 可复用 UI 组件
│   └── i18n.py            # 国际化支持
├── model/                 # ONNX AI 模型
│   ├── emotion.onnx       # 情绪识别
│   ├── face_detection_*.onnx  # 人脸检测 (YuNet)
│   ├── handpose_*.onnx    # 手势姿态估计 (MediaPipe)
│   ├── person_detection_*.onnx  # 人体检测
│   ├── pose_estimation_*.onnx   # 姿态估计
│   └── yolo_coco.onnx     # 目标检测
├── assets/                # 静态资源
│   ├── expressions/       # 机器人表情帧动画
│   ├── images/            # UI 图片与背景
│   └── music/             # 音频文件（系统提示音、音乐）
├── docs/                  # 文档
│   └── HARDWARE_PLAN.md   # 详细硬件架构规划
└── scripts/               # 工具脚本
```

---

## 📱 应用列表

| 应用 | 描述 | 核心技术 |
|------|------|---------|
| **AI 对话** | 带情绪感知的语音对话 | LLM、TTS、ASR、ONNX 情绪检测 |
| **Blockly 编程** | 拖拽式图形化编程控制机器人 | Blockly、Python 代码生成 |
| **人脸跟随** | 实时人脸检测与跟踪 | MediaPipe Face Detection、Picamera2 |
| **小球跟随** | 颜色小球识别与跟踪 | OpenCV 颜色过滤、PID 控制 |
| **手势识别** | 手势命令控制机器人 | MediaPipe Hands、ONNX |
| **手柄控制** | 蓝牙/USB 手柄操控机器人 | evdev、蓝牙 HID、摇杆校准 |
| **图传模式** | 手机浏览器远程遥控 | Flask Web 服务、MJPEG 推流 |
| **雷达扫描** | 360° 环境扫描可视化 | YDLiDAR SDK、实时渲染 |
| **群组表演** | 多机器人同步编舞 | MQTT 发布/订阅、时间同步 |
| **系统设置** | 系统配置与设备信息 | 语言切换、音量控制 |

---

## 🛠️ 开发指南

### Python 应用

所有应用均基于 **PySide6** 构建，直接渲染到 Linux 帧缓冲：

```python
# apps/ball_track/main.py（简化示例）
import sys
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QTimer
from picamera2 import Picamera2
import cv2

class BallTracker(QWidget):
    def __init__(self):
        super().__init__()
        self.camera = Picamera2()
        self.camera.start()
        self.timer = QTimer()
        self.timer.timeout.connect(self.process_frame)
        self.timer.start(33)  # ~30 FPS
```

### 按键事件

按键事件通过 FIFO（`/tmp/luwu_keys.fifo`）从 C++ 启动器转发到 Python 应用：

| 物理按键 | GPIO | Linux 键码 |
|---------|------|-----------|
| A（左上） | 17 | `KEY_LEFT` |
| B（右上） | 22 | `KEY_RIGHT` |
| C（左下） | 23 | `KEY_BACK` |
| D（右下） | 24 | `KEY_ENTER` |

### AI 模型

ONNX 模型位于 `model/` 目录，通过 `onnxruntime` 加载运行：
- 人脸检测（YuNet）
- 手势姿态估计（MediaPipe）
- 人体检测（MediaPipe）
- 姿态估计（MediaPipe）
- 情绪识别（自研）
- 目标检测（YOLO COCO）

---

## 🤝 参与贡献

欢迎提交 Issue 和 Pull Request！

---

## 📜 开源协议

本项目基于 **Apache License 2.0** 开源。

Copyright © 2024–2026 [LuwuDynamics](https://github.com/LuwuDynamics)

```
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

这意味着你可以自由地：

- **使用** — 将本软件用于任何目的
- **修改** — 修改源代码并创建衍生作品
- **分发** — 分发原始或修改后的软件副本
- **商用** — 在商业产品中使用本软件

但需遵守以下条件：

- 所有分发中必须包含 Apache 2.0 许可证副本
- 必须说明对原始代码所做的重大修改
- 必须保留所有版权、专利、商标和署名声明
- 项目名称 "Luwu-OS" 及相关商标不在授权范围内

完整许可证文本请参阅 [LICENSE](LICENSE)。

---

<p align="center">
  <sub>Built with ❤️ by <strong>LuwuDynamics</strong></sub>
</p>
