#!/bin/bash
# 停止 splash mplayer，等待其彻底退出后再让 Qt 启动
# 用于 luwu-launcher.service 的 ExecStartPre
# 优先通过 FIFO 优雅退出，超时则强杀

FIFO_PATH="/tmp/splash.fifo"

# 策略 1：通过 slave 模式的 FIFO 发 quit 命令，让 mplayer 自己停
if [ -p "$FIFO_PATH" ]; then
    echo "quit" > "$FIFO_PATH" 2>/dev/null || true
fi

# 策略 2：等待 mplayer 退出（最多 3 秒）
for i in $(seq 1 30); do
    pid=$(pgrep -x mplayer 2>/dev/null)
    [ -z "$pid" ] && break
    sleep 0.1
done

# 策略 3：仍未退出，强杀兜底
pid=$(pgrep -x mplayer 2>/dev/null)
if [ -n "$pid" ]; then
    kill -9 $pid 2>/dev/null || true
    sleep 0.5
fi

# 清理 FIFO
rm -f "$FIFO_PATH"

# 额外等待 SPI 总线空闲
sleep 0.3
exit 0
