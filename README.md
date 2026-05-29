<p align="center">
  <img src="snaps/luwuos.png" width="120" alt="Luwu-OS Logo" />
</p>

<h1 align="center">Luwu-OS</h1>

<p align="center">
  <strong>A Lightweight Embedded Desktop OS for Educational Robots</strong>
  <br />
  Raspberry Pi CM4 / CM5 · Qt C++ Launcher · PySide6 Apps · Linux Framebuffer
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python" />
  <img src="https://img.shields.io/badge/c%2B%2B-17-blue.svg" alt="C++" />
  <img src="https://img.shields.io/badge/qt-5.15-green.svg" alt="Qt" />
  <img src="https://img.shields.io/badge/platform-Raspberry%20Pi%20CM4%2FCM5-red.svg" alt="Platform" />
  <img src="https://img.shields.io/badge/license-MIT-lightgrey.svg" alt="License" />
</p>

<p align="center">
  <a href="https://pypi.org/project/xgolib/"><img src="https://img.shields.io/pypi/v/xgolib.svg?label=xgolib" alt="xgolib" /></a>
  <a href="https://pypi.org/project/xgoedu-luwuos/"><img src="https://img.shields.io/pypi/v/xgoedu-luwuos.svg?label=xgoedu-luwuos" alt="xgoedu-luwuos" /></a>
  <a href="https://pypi.org/project/xgo-blockly-luwuos/"><img src="https://img.shields.io/pypi/v/xgo-blockly-luwuos.svg?label=xgo-blockly-luwuos" alt="xgo-blockly-luwuos" /></a>
</p>

<p align="center">
  <a href="README_CN.md">📖 中文文档</a>
</p>

---

## 📑 Table of Contents

- [Overview](#-overview)
- [Screenshots](#-screenshots)
- [Features](#-features)
- [Hardware](#-hardware)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Project Structure](#-project-structure)
- [Applications](#-applications)
- [Development](#-development)
- [Contributing](#-contributing)

---

## 📖 Overview

**Luwu-OS** is a purpose-built embedded operating system for educational robotics. Running on **Raspberry Pi CM4 and CM5**, it powers a suite of interactive applications — from AI-powered voice chat and gesture recognition to Blockly-based visual programming and multi-robot group performances.

The system features a **Qt C++ launcher** that manages a fleet of **PySide6 application processes**, all rendering directly to a 240×320 SPI LCD via the Linux framebuffer — no X11, no Wayland, no display server overhead.

> **Mission**: Give every student an intuitive, responsive, and delightful robotics experience — right on the robot itself.

---

## 📸 Screenshots

### 🏠 Home & Gallery

<p align="center">
 <img src="snaps/英文截图/coding.png" width="23%" alt="Blockly Coding" />
  <img src="snaps/英文截图/demos.png" width="23%" alt="Demo Gallery" />
  <img src="snaps/英文截图/demos——1.png" width="23%" alt="Demo Gallery Page 1" />
  <img src="snaps/英文截图/demos——2.png" width="23%" alt="Demo Gallery Page 2" />
</p>

### 🧠 AI Chat

<p align="center">
  <img src="snaps/英文截图/ai-chat-detail.png" width="23%" alt="AI Chat Detail" />
    <img src="snaps/英文截图/aichat 配置.png" width="23%" alt="AI Chat Config" />
</p>



### 🧩 Coding

<p align="center">
  <img src="snaps/英文截图/blockly.png" width="33%" alt="Blockly Editor" />
  <img src="snaps/英文截图/coding-detail.png" width="23%" alt="Coding Detail" />
  <img src="snaps/英文截图/coding-detail-1.png" width="23%" alt="Coding Detail Page" />
</p>

### 🤖 Interactive Demos

<p align="center">
  <img src="snaps/英文截图/手势.png" width="32%" alt="Gesture Recognition" />
  <img src="snaps/英文截图/face.png" width="32%" alt="Face Tracking" />
  <img src="snaps/英文截图/ball.png" width="32%" alt="Ball Tracking" />
</p>

### 🌐 Network & RC

<p align="center">
  <img src="snaps/英文截图/wifi-detail.png" width="23%" alt="WiFi Detail" />
  <img src="snaps/英文截图/wifi-detail-1.png" width="23%" alt="WiFi Detail 1" />
  <img src="snaps/英文截图/wifi-detail-2.png" width="23%" alt="WiFi Detail 2" />
    <img src="snaps/英文截图/rc-mode-detail.png" width="23%" alt="RC Mode" />
</p>


### 🎮 More Features

<p align="center">
  <img src="snaps/英文截图/群控.png" width="32%" alt="Group Control" />
  <img src="snaps/英文截图/蓝牙手柄.png" width="32%" alt="Bluetooth Gamepad" />
   <img src="snaps/英文截图/雷达.png" width="32%" alt="Radar Scan" />
</p>


---

## ✨ Features

| Category | Capability |
|----------|------------|
| 🧠 **AI Assistant** | Voice conversation, emotion recognition & expression, real-time TTS/ASR |
| 🎮 **Gamepad Control** | Bluetooth & wired gamepad support with joystick calibration |
| 🖐️ **Gesture Recognition** | Real-time hand pose estimation via MediaPipe |
| 👤 **Face Tracking** | Face detection & follow using onboard camera |
| ⚽ **Ball Tracking** | Color-based ball detection, tracking & catching |
| 📡 **RC Mode** | Remote control via mobile web interface with live camera feed |
| 🗺️ **Radar Scan** | LiDAR-based 360° environment scanning |
| 🎭 **Group Performance** | MQTT-synchronized multi-robot choreography |
| 🧩 **Blockly Coding** | Visual programming with drag-and-drop blocks, generates Python on-device |
| 🌐 **WiFi Setup** | QR code scan-to-connect, hotspot management |
| 🔊 **Sound Localization** | Microphone-array based sound source detection |
| ⚙️ **System Settings** | Language switching (EN/CN), volume control, device info |

---

## 🔧 Hardware

| # | Component | Interface | Driver |
|---|-----------|-----------|--------|
| 1 | **SPI LCD** (ST7789V, 240×320) | SPI0 + GPIO 27/25/0 | `fbtft` kernel driver → `/dev/fb-spi` |
| 2 | **Camera** (OV5647) | CSI | Picamera2 (per-app lifecycle) |
| 3 | **4 Physical Buttons** (A/B/C/D) | GPIO 17/22/23/24 | `gpio-keys` → `/dev/input/eventX` |
| 4 | **Bluetooth** (Cypress) | mini-UART / PL011 | `bluetoothd` via D-Bus |
| 5 | **Audio + Microphone** (WM8960) | I2S + I2C | ALSA `dmix` + `dsnoop` |
| 6 | **Robot Dog UART** | ttyAMA0 / UART5 | `xgolib` (auto-detect) |
| 7 | **Servo / IMU / Battery** | UART → MCU | `xgolib` forwarding |
| 8 | **Cooling Fan** (4-wire PWM) | GPIO PWM + TACH | `pwm-fan` kernel driver (hwmon) |
| 9 | **Power + Battery** | VC voltage monitor + UART | `vcgencmd` + `xgolib` joint monitoring |

> See [HARDWARE_PLAN.md](HARDWARE_PLAN.md) for the full hardware architecture, pinouts, and migration strategy.

### Design Principle

> **Each hardware peripheral has exactly one owner — the kernel driver or system service.**
> **Apps access hardware through standard interfaces, never by fighting over raw devices.**

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Qt C++ Launcher                        │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ Gallery │  │ StatusBar│  │ KeyFilter │  │ QProcess │  │
│  │  View   │  │ (battery)│  │  (evdev)  │  │  Manager │  │
│  └─────────┘  └──────────┘  └───────────┘  └────┬─────┘ │
└─────────────────────────────────────────────────┼────────┘
                                                  │ spawn
         ┌────────────────────────────────────────┼───────┐
         │            PySide6 Apps (LinuxFB)      │       │
         │  ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐│       │
         │  │  AI  │ │Coding│ │Demos │ │Settings││  ...  │
         │  │ Chat │ │Blockly│ │Page  │ │ Page   ││       │
         │  └──────┘ └──────┘ └──────┘ └────────┘│       │
         └────────────────────────────────────────┘       │
                                                          │
  ┌───────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│                  Linux Kernel Layer                       │
│  fbtft │ gpio-keys │ ALSA dmix │ pwm-fan │ vcgencmd     │
└──────────────────────────────────────────────────────────┘
```

| Layer | Technology | Role |
|-------|-----------|------|
| **Launcher** | Qt 5.15 C++ (EGLFS / LinuxFB) | Process lifecycle, key routing, status bar |
| **Applications** | PySide6 Python (LinuxFB) | Feature apps, rendered to framebuffer |
| **Hardware Abstraction** | Kernel drivers + systemd services | Single-owner hardware access via standard Linux interfaces |
| **IPC** | FIFO (`/tmp/luwu_keys.fifo`), D-Bus, MQTT | Inter-process communication |

---

## 🚀 Quick Start

### Prerequisites

- Raspberry Pi CM4 or CM5 with compatible carrier board
- SPI LCD (ST7789V) connected via SPI0
- Debian-based Linux OS

### Installation

```bash
git clone https://github.com/your-org/Luwu-OS.git /opt/luwu-os
cd /opt/luwu-os
sudo bash configs/install.sh
```

This single script handles everything:
- Kernel overlays (SPI LCD, gpio-keys, fan control)
- udev rules (`/dev/fb-spi` symlink)
- ALSA multi-app audio sharing (`dmix` + `dsnoop`)
- systemd services (launcher, undervolt monitor, hardware auto-detect)
- Python dependencies via `pip`

Key Python packages installed:
- [`xgolib`](https://pypi.org/project/xgolib/) — Robot dog motion library
- [`xgoedu-luwuos`](https://pypi.org/project/xgoedu-luwuos/) — Educational library (PySide6 QPainter)
- [`xgo-blockly-luwuos`](https://pypi.org/project/xgo-blockly-luwuos/) — Blockly visual programming

### Launch

The system starts automatically on boot via systemd:

```bash
sudo systemctl enable luwu-launcher
sudo systemctl start luwu-launcher
```

### Development Mode

Run individual apps directly for development:

```bash
# Navigate to the app directory
cd apps/ai
python main.py

# Or for the coding app
cd apps/coding
python main.py
```

---

## 📂 Project Structure

```
Luwu-OS/
├── launcher/              # Qt C++ main launcher (Gallery, StatusBar, KeyFilter)
│   ├── main.cpp           # Entry point, QProcess management, key routing
│   ├── galleryview.cpp    # Card-based app gallery UI
│   ├── statusbar.cpp      # Battery + time status bar
│   └── ...
├── apps/                  # PySide6 feature applications
│   ├── ai/                # AI voice chat, emotion recognition, TTS/ASR
│   ├── coding/            # Blockly visual programming
│   ├── demo_page/         # Demo launcher page
│   ├── ball_track/        # Color-based ball tracking
│   ├── ball_catch/        # Ball catching with servo control
│   ├── face_follow/       # Face detection & tracking
│   ├── gesture/           # Hand gesture recognition
│   ├── gamepad/           # Bluetooth/USB gamepad control
│   ├── rc_mode/           # Remote control via web UI
│   ├── radar/             # LiDAR 360° scanning
│   ├── perform/           # Pre-programmed performance routines
│   ├── group_perform/     # MQTT-synchronized multi-robot group performance
│   ├── joystick/          # Joystick control interface
│   ├── hotspot/           # WiFi hotspot management
│   ├── network/           # Network configuration
│   └── settings/          # System settings (language, volume, device info)
├── configs/               # System configuration files
│   ├── install.sh         # One-click deployment script
│   ├── boot-config.txt    # Kernel config template (new hardware)
│   ├── cm4-old.config     # Kernel config template (legacy hardware)
│   ├── luwu-keys.dts      # gpio-keys device tree source
│   ├── asound.conf        # ALSA dmix/dsnoop configuration
│   ├── asound.state       # Mixer state (anti-feedback + safe volume)
│   └── *.service          # systemd service unit files
├── libs/                  # Shared Python libraries
│   ├── xgolib/            # Robot dog motion library
│   ├── xgoedu-luwuos/     # Educational library (PySide6 QPainter)
│   ├── theme/             # UI theming
│   ├── ui/                # Reusable UI components
│   └── i18n.py            # Internationalization support
├── model/                 # ONNX AI models
│   ├── emotion.onnx       # Emotion recognition
│   ├── face_detection_*.onnx  # Face detection (YuNet)
│   ├── handpose_*.onnx    # Hand pose estimation (MediaPipe)
│   ├── person_detection_*.onnx  # Person detection
│   ├── pose_estimation_*.onnx   # Pose estimation
│   └── yolo_coco.onnx     # Object detection
├── assets/                # Static assets
│   ├── expressions/       # Robot facial expression frames
│   ├── images/            # UI images & backgrounds
│   └── music/             # Audio files (system sounds, music)
├── docs/                  # Documentation
│   └── HARDWARE_PLAN.md   # Detailed hardware architecture plan
└── scripts/               # Utility scripts
```

---

## 📱 Applications

| App | Description | Key Tech |
|-----|-------------|----------|
| **AI Chat** | Voice conversation with emotion-aware responses | LLM, TTS, ASR, ONNX emotion detection |
| **Blockly Coding** | Visual drag-and-drop programming for robots | Blockly, Python code generation |
| **Face Follow** | Real-time face detection and tracking | MediaPipe Face Detection, Picamera2 |
| **Ball Track** | Color-based ball detection and following | OpenCV color filtering, PID control |
| **Gesture Control** | Hand gesture recognition for robot commands | MediaPipe Hands, ONNX |
| **Gamepad** | Bluetooth/USB gamepad robot control | evdev, Bluetooth HID, joystick calibration |
| **RC Mode** | Remote control via mobile web browser | Flask web server, MJPEG streaming |
| **Radar** | 360° environment scanning visualization | YDLiDAR SDK, real-time rendering |
| **Group Perform** | Synchronized multi-robot choreography | MQTT pub/sub, time-sync |
| **Settings** | System configuration and device info | Language switching, volume control |

---

## 🛠️ Development

### Python Apps

All applications are built with **PySide6** and render directly to the Linux framebuffer:

```python
# apps/ball_track/main.py (simplified example)
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

### Key Events

Key events are routed from the C++ launcher to Python apps via a FIFO (`/tmp/luwu_keys.fifo`):

| Physical Button | GPIO | Linux Key Code |
|----------------|------|---------------|
| A (top-left) | 17 | `KEY_LEFT` |
| B (top-right) | 22 | `KEY_RIGHT` |
| C (bottom-left) | 23 | `KEY_BACK` |
| D (bottom-right) | 24 | `KEY_ENTER` |

### AI Models

ONNX models are located in `model/` and loaded via `onnxruntime`. Models include:
- Face detection (YuNet)
- Hand pose estimation (MediaPipe)
- Person detection (MediaPipe)
- Pose estimation (MediaPipe)
- Emotion recognition (custom)
- Object detection (YOLO COCO)

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

---

<p align="center">
  <sub>Built with ❤️ by <strong>LuwuDynamics</strong></sub>
</p>
