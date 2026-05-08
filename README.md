# Luwu-OS

基于 Raspberry Pi CM5 的嵌入式桌面系统。

## 架构

- **launcher/** — Qt C++ 主启动器，通过 QProcess 管理 PyQt6 子进程
- **apps/** — PyQt6 功能应用（demo_page 等）
- **configs/** — 系统配置文件（udev 规则、ALSA 配置、systemd 服务、内核 DTS 等）

## 硬件

| 硬件 | 接口 |
|------|------|
| SPI LCD (ST7789V, 240×320) | SPI0 + GPIO |
| 摄像头 (OV5647) | CSI |
| 4 按键 (A/B/C/D) | GPIO 17/22/23/24 |
| 音频 + 麦克风 (WM8960) | I2S + I2C |
| 散热风扇 (4线 PWM) | GPIO PWM |

详细硬件架构见 [HARDWARE_PLAN.md](HARDWARE_PLAN.md)。

## 安装

```bash
sudo bash configs/install.sh
```

## 启动

系统通过 systemd 服务自动启动：

```bash
sudo systemctl enable luwu-launcher
sudo systemctl start luwu-launcher
```
