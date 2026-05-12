# SPI LCD 开机显示调研报告

## 用户诉求

开机后第一秒就在 SPI LCD（ST7789V, 320×240）上看到画面，像手机/电脑的启动动画一样。

## 结论

**不可行。** SPI LCD 从按下电源到屏幕物理可显示，最少需要约 6 秒。这不是代码慢，是硬件物理限制。

---

## 完整开机时间线

```
按电源
 ├─ 0~0.5s  电流到达芯片，ST7789V 处于 Reset 状态（黑屏）
 ├─ 0~2s    VC4 GPU 固件（start4.elf）初始化 CPU/RAM
 │          ST7789V 仍然是 Reset（黑屏）
 ├─ 2~3.5s  Linux 内核启动，初始化 SPI 控制器、GPIO 引脚
 │          ST7789V 仍是 Reset（黑屏）
 ├─ ~3.5s   fbtft 模块从磁盘加载
 ├─ ~3.9s   fb_st7789v 模块加载，通过 SPI 发初始化命令
 │          设置旋转、色深、时序……ST7789V 此时才"醒来"
 ├─ ~4.2s   graphics fb1 注册 → 屏幕物理可显示 ★（最早可画像素）
 ├─ ~7.5s   luwu-splash 启动 → mplayer 播视频 → 用户看到画面
 └─ ~10s    Qt launcher 启动 → 桌面出现
```

SPI LCD 从通电到能显示，**前 ~4.2 秒是硬件初始化死时间，软件无法干预。**

---

## 为什么手机/电脑第一秒就有动画

| 设备 | 显示链路 | 屏幕亮起时间 |
|------|---------|------------|
| 手机 | BootROM → MIPI DSI 控制器 → 屏幕（固件内置驱动） | ~0.1s |
| 电脑 | UEFI/BIOS → GPU GOP 驱动 → HDMI/DP 显示器 | ~0.5s |
| 我们的 SPI | BootROM → 内核 → SPI 控制器 → fbtft → ST7789V | ~6s |

手机和电脑的固件/BIOS 阶段就内置了屏幕驱动。我们的 SPI LCD 必须等到 Linux 内核跑完 SPI 初始化链才能亮。

---

## 为什么 HDMI 比 SPI 快

```
树莓派通电
 ├─ 0.0s  VC4 GPU 先醒（ARM CPU 还在睡觉）
 ├─ 0.3s  GPU 初始化 SDRAM + HDMI PHY
 └─ 0.5s  GPU 往 HDMI 口画彩虹方块 ← 此时 ARM 还没启动！

SPI 外设挂在 ARM CPU 总线这边，GPU 固件：
  - 不认识 SPI 控制器（物理上接在 ARM 侧，GPU 够不着）
  - 闭源（Broadcom 授权，无法修改）
  - 没有人会为 SPI LCD 给 GPU 固件写驱动（投入产出比为零）
```

---

## fb0 vs fb1：内核 logo 为什么不在 SPI 上

- `fb0`：VC4 HDMI 帧缓冲（1920×1080），由 VC 固件的 `bcm2708_fb.*` 参数创建
- `fb1`：SPI LCD（320×240），由 fbtft/fb_st7789v 模块创建
- 内核 boot logo 只画在第一个注册的帧缓冲（fb0 = HDMI）上
- `fbcon=map:99` 已被移除并替换为 `quiet loglevel=3 vt.global_cursor_default=0`，但 logo 仍走 HDMI
- **结论**：SPI 屏幕拿不到内核 logo（除非关掉 HDMI fb0 并让 SPI 变 fb0，但注册时序问题不一定能解决）

---

## HDMI fb0 代价分析

| 项目 | 代价 |
|------|------|
| CPU | 0%（DMA 搬运） |
| GPU 3D | 0%（HVS 独立模块） |
| 显存 | 4MB / 4GB = 0.1% |
| 内存带宽 | ~250MB/s / 20GB+ ≈ 1%（HDMI 拔出时很可能为 0，HVS 不扫描） |
| 功耗 | HVS 模块一直供电，多扫不扫一样 |

**结论：不接 HDMI 线时，fb0 代价可忽略，不需要关。**

---

## fbtft 编进内核

- 当前是模块（.ko），开机后从磁盘加载（约 3.5s 处）
- 编进内核镜像可提早约 0.5~1s 初始化
- 代价：需重新编译内核（每次系统更新都要重编），fbtft 是 staging 驱动质量未知
- **结论：收益有限（6s → 5s 黑屏），不推荐。**

---

## 已实施的优化

| 优化 | 效果 |
|------|------|
| udev 触发 splash（`99-fb-spi.rules` + `SYSTEMD_WANTS`） | 屏幕一亮即播视频（4.2s → 7.5s 的延迟压到最小） |
| splash 服务 `User=pi`，与 launcher 同用户 | 避免 root/pi 权限隔离导致 kill 失败 |
| mplayer `-slave` 模式 + FIFO 优雅退出 | 替代暴力 `kill -9` 循环 |
| `kill-splash.sh` 三级策略：FIFO quit → 等待 → kill -9 兜底 | 无 D 状态残留风险 |
| launcher `ExecStartPre` 先停 splash 再启动 | 避免 Qt/mplayer 同时写 fb-spi 导致 SPI 总线堵塞 |

---

## 当前开机视觉动线

```
开机 0s ────────────── 6s ──────── 7.5s ─────── 10s
      黑屏（不可避免）    屏幕物理亮   视频播放    桌面
```

前 6 秒黑屏受 ST7789V 硬件初始化时间限制，软件层面已做到极限。
