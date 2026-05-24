#!/usr/bin/env python3
"""
Luwu OS - Coding (Blockly 图形编程) 应用

物理按键映射：
  A (GPIO17, top-left)     KEY_LEFT   → 上移 / 上一个
  B (GPIO22, top-right)    KEY_RIGHT  → 下移 / 下一个
  C (GPIO23, bottom-left)  KEY_BACK   → 返回 / 退出
  D (GPIO24, bottom-right) KEY_ENTER  → 进入列表 / 运行 / 停止
"""
import sys
import signal

# 确保能找到 luwu-os 全局库
LUWU_ROOT = "/home/pi/luwu-os"
if LUWU_ROOT not in sys.path:
    sys.path.insert(0, LUWU_ROOT)

from PySide6.QtWidgets import QApplication

from libs.theme import apply_app_palette
from coding_page import CodingPage


def main():
    signal.signal(signal.SIGINT, lambda *_: QApplication.instance().quit())
    signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

    app = QApplication(sys.argv)
    apply_app_palette(app)
    w = CodingPage()
    w.showFullScreen()

    rc = app.exec()
    print(f"[coding] exit rc={rc}", flush=True)


if __name__ == "__main__":
    main()