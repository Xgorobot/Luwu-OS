#!/usr/bin/env python3
"""
手柄事件广播 — gamepad_controller → mapping_server SSE 桥接

用法:
  # 生产者 (gamepad_controller.py)
  from mapping_events import push
  push({'type': 'button', 'index': 0, 'value': 1})

  # 消费者 (mapping_server.py)
  from mapping_events import get_queue
  q = get_queue()
  event = q.get(timeout=1)
"""

import queue
import time

# 最多缓存 500 个事件，超出丢弃最旧的
_event_queue = queue.Queue(maxsize=500)


def push(evt: dict):
    """
    推送手柄事件到映射页面。
    evt 格式: {'type': 'button'|'axis', 'index': int, 'value': int, 'ts': float}
    - button: index=按钮索引(0-15), value=1按下/0松开
    - axis:   index=轴索引(0-5), value=归一化值(-1.0~1.0)
    """
    if 'ts' not in evt:
        evt['ts'] = time.time()
    try:
        _event_queue.put_nowait(evt)
    except queue.Full:
        # 丢弃最旧事件腾空间
        try:
            _event_queue.get_nowait()
            _event_queue.put_nowait(evt)
        except queue.Full:
            pass


def get_queue() -> queue.Queue:
    return _event_queue
