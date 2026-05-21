#!/bin/bash
# Luwu OS — 一键部署系统配置到树莓派
# 用法: sudo bash install.sh

set -e

echo "=== Luwu OS 系统配置部署 ==="

# 0. 系统依赖
echo "[0/12] 安装系统依赖 ..."
apt install -y \
    python3-pip python3-pyside6 python3-numpy python3-picamera2 python3-evdev \
    python3-flask python3-flask-socketio python3-opencv \
    mplayer alsa-utils ffmpeg libzbar0t64 portaudio19-dev
echo "  ✓ 系统依赖已安装"

# 0b. pip 依赖（apt 中没有的包 / 本地开发包）
echo "[0b/12] 安装 pip 依赖 ..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/../requirements.txt" ]; then
    pip3 install --break-system-packages -r "$SCRIPT_DIR/../requirements.txt" || echo "  ! pip 依赖安装警告（非致命）"
    echo "  ✓ pip 依赖已安装"
else
    echo "  ! requirements.txt 未找到，跳过"
fi

# 1. /boot/firmware/config.txt
echo "[1/12] 部署 /boot/firmware/config.txt ..."
cp boot-config.txt /boot/firmware/config.txt
echo "  ✓ 已写入"

# 2. gpio-keys 设备树覆盖层
echo "[2/12] 编译并部署 gpio-keys 设备树覆盖层 ..."
if ! command -v dtc &>/dev/null; then
    echo "  ! dtc 未安装，正在安装 device-tree-compiler ..."
    apt install -y device-tree-compiler
fi
dtc -@ -I dts -O dtb -o /boot/firmware/overlays/luwu-keys.dtbo luwu-keys.dts
echo "  ✓ luwu-keys.dtbo 已部署"

# 3. udev 规则 (fb-spi 软链接)
echo "[3/12] 部署 udev 规则 ..."
cp 99-fb-spi.rules /etc/udev/rules.d/
cp 99-gamepad-no-mouse.rules /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger --subsystem-match=graphics
udevadm trigger --subsystem-match=input
echo "  ✓ 已生效 (fb-spi + 蓝牙手柄触摸板屏蔽)"

# 4. ALSA 音频配置 (dmix + dsnoop + 默认音量)
echo "[4/12] 部署 ALSA 音频配置 ..."
cp asound.conf /etc/asound.conf
# 恢复混音器状态（啸叫修复 + 默认音量 71%）
if [ -f asound.state ]; then
    alsactl restore -f asound.state
    cp asound.state /var/lib/alsa/asound.state
    echo "  ✓ 混音器状态已恢复"
else
    echo "  ! asound.state 不存在，使用当前系统音量"
fi
echo "  ✓ asound.conf 已写入"

# 5. systemd 服务
echo "[5/12] 部署 systemd 服务 ..."
cp luwu-splash.service /etc/systemd/system/
cp luwu-launcher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable luwu-splash.service
systemctl enable luwu-launcher.service
echo "  ✓ 已启用"

# 5b. CM4 硬件自动识别服务
echo "[5b/12] 部署 CM4 硬件自动识别服务 ..."
cp luwu-hw-autoconf.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable luwu-hw-autoconf.service
echo "  ✓ luwu-hw-autoconf.service 已启用 (开机自动检测 CM4 新/老硬件并切换 config.txt)"

# 6. 开机画面脚本权限
echo "[6/12] 设置开机画面脚本权限 ..."
chmod +x luwu-splash.sh
echo "  ✓ 已设置"

# 7. ext4 文件系统加固 — 防止断电丢文件
echo "[7/12] 文件系统加固 ..."
tune2fs -c 5 /dev/mmcblk0p2
echo "  ✓ fsck 每5次挂载自动执行"

# 内核命令行 rootflags=data=journal：文件数据也进日志，掉电不丢文件内容
if ! grep -q 'rootflags=data=journal' /boot/firmware/cmdline.txt; then
    sed -i 's|rootfstype=ext4|rootfstype=ext4 rootflags=data=journal|' /boot/firmware/cmdline.txt
fi
echo "  ✓ data=journal (rootflags) 已设置"

# 挂载选项 commit=1：journal 每秒刷盘，断电最多丢1秒数据
if ! grep -q 'commit=1' /etc/fstab; then
    sed -i 's|defaults,noatime|defaults,noatime,commit=1|' /etc/fstab
fi
echo "  ✓ commit=1 (fstab) 已设置"

# 8. 硬件看门狗 — 系统卡死自动重启
echo "[8/12] 硬件看门狗 ..."
apt install -y watchdog
cat > /etc/watchdog.conf << 'WDOG'
watchdog-device = /dev/watchdog
watchdog-timeout = 15
interval = 5
max-load-1 = 24
realtime = yes
priority = 1
WDOG
systemctl enable watchdog
systemctl start watchdog
echo "  ✓ 看门狗已启用 (15秒超时)"

# 9. 持久化系统日志 — 出问题可追溯
echo "[9/12] 持久化系统日志 ..."
mkdir -p /var/log/journal
sed -i 's/#Storage=auto/Storage=persistent/' /etc/systemd/journald.conf
systemctl restart systemd-journald
echo "  ✓ 日志持久化已配置"

# 10. 欠压+电池联合监控 — 分级响应防误关机
echo "[10/12] 欠压+电池监控 ..."
cp luwu-undervolt-monitor.py /usr/local/bin/
chmod +x /usr/local/bin/luwu-undervolt-monitor.py
cp luwu-undervolt.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable luwu-undervolt.service
systemctl start luwu-undervolt.service
echo "  ✓ 欠压+电池监控已启用 (电池>10%忽略, 5~10%延迟关, <5%立即关)"

# 11. 完成
echo "[12/12] 部署完成。必须重启以加载新的设备树和防护配置: sudo reboot"
echo "=== 完毕 ==="
