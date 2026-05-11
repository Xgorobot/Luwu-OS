#!/bin/bash
# Luwu OS — 一键部署系统配置到树莓派
# 用法: sudo bash install.sh

set -e

echo "=== Luwu OS 系统配置部署 ==="

# 0. 依赖包
echo "[0/6] 安装依赖包 ..."
apt install -y python3-pip python3-pyside6 python3-numpy python3-picamera2 python3-evdev mplayer alsa-utils
echo "  ✓ 依赖包已安装"

# 1. /boot/firmware/config.txt
echo "[1/6] 部署 /boot/firmware/config.txt ..."
cp boot-config.txt /boot/firmware/config.txt
echo "  ✓ 已写入"

# 2. gpio-keys 设备树覆盖层
echo "[2/6] 编译并部署 gpio-keys 设备树覆盖层 ..."
if ! command -v dtc &>/dev/null; then
    echo "  ! dtc 未安装，正在安装 device-tree-compiler ..."
    apt install -y device-tree-compiler
fi
dtc -@ -I dts -O dtb -o /boot/firmware/overlays/luwu-keys.dtbo luwu-keys.dts
echo "  ✓ luwu-keys.dtbo 已部署"

# 3. udev 规则 (fb-spi 软链接)
echo "[3/6] 部署 udev 规则 ..."
cp 99-fb-spi.rules /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger --subsystem-match=graphics
echo "  ✓ 已生效"

# 4. ALSA 音频配置 (dmix + dsnoop + 默认音量)
echo "[4/6] 部署 ALSA 音频配置 ..."
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
echo "[5/6] 部署 systemd 服务 ..."
cp luwu-launcher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable luwu-launcher.service
echo "  ✓ 已启用"

# 6. 完成
echo "[6/6] 部署完成。必须重启以加载新的设备树: sudo reboot"
echo "=== 完毕 ==="
