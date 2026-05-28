#!/usr/bin/env python3
"""Luwu OS - Coding 升级 HTTP API 服务。

提供 REST API 给 Blockly 网页前端调用：
  GET  /api/upgrade/check   → 触发版本检查，阻塞等待结果后返回（无需轮询）
  GET  /api/upgrade/status  → 查询升级进度（供升级中轮询）
  POST /api/upgrade/start   → 开始升级（升级过程较长，需轮询 /status）

在 daemon 线程中运行，绑定 0.0.0.0:8765，允许跨域。
"""

import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

SERVER_PORT = 8765
CHECK_TIMEOUT = 15  # 版本检查最大等待秒数


class _ThreadingServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP Server，防止阻塞请求影响其他接口。"""
    daemon_threads = True


class _UpgradeHandler(BaseHTTPRequestHandler):
    """升级 API 请求处理器。

    类属性 upgrade_manager 在 start_server() 时注入，
    指向 coding.managers.UpgradeManager 实例。
    """
    # 由外部注入
    upgrade_manager = None

    # ---- 日志 ----
    def log_message(self, fmt, *args):
        print(f"[coding-upgrade-api] {args[0]}", flush=True)

    # ---- CORS ----
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- 路由 ----
    def do_OPTIONS(self):
        """CORS 预检。"""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/upgrade/check":
            self._handle_check()
        elif self.path == "/api/upgrade/status":
            self._handle_status()
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/upgrade/start":
            self._handle_start()
        else:
            self._json({"error": "not found"}, 404)

    # ---- 处理器 ----
    def _handle_check(self):
        """触发版本检查并阻塞等待结果（每次调用都重新检查，最长 15s）。"""
        um = self.upgrade_manager
        if um is None:
            self._json({"error": "upgrade manager not ready"}, 503)
            return

        # 每次调用都触发新的后台检查（start_check 内部防重复启动）
        um.start_check()

        # 等待后台线程拿到锁并将状态改为 CHECKING（消除竞态）
        deadline = time.time() + CHECK_TIMEOUT
        while um.status == um.STATUS_IDLE:
            if time.time() > deadline:
                self._json({"status": "timeout", "current_version": "", "latest_version": ""}, 504)
                return
            time.sleep(0.1)

        # 阻塞等待检查完成
        while um.status == um.STATUS_CHECKING:
            if time.time() > deadline:
                print("[coding-upgrade-api] check timeout", flush=True)
                self._json({
                    "status": "timeout",
                    "current_version": um.current_version,
                    "latest_version": um.latest_version,
                }, 504)
                return
            time.sleep(0.3)

        self._json({
            "status": um.status,
            "current_version": um.current_version,
            "latest_version": um.latest_version,
            "mirror": um.get_mirror_name(),
        })

    def _handle_status(self):
        """查询当前升级状态。"""
        um = self.upgrade_manager
        if um is None:
            self._json({"error": "upgrade manager not ready"}, 503)
            return

        self._json({
            "status": um.status,
            "current_version": um.current_version,
            "latest_version": um.latest_version,
            "message": um.message,
            "mirror": um.get_mirror_name(),
        })

    def _handle_start(self):
        """开始升级。"""
        um = self.upgrade_manager
        if um is None:
            self._json({"error": "upgrade manager not ready"}, 503)
            return

        # 允许 available（首次）和 failed（重试）状态下启动升级
        if um.status not in (um.STATUS_AVAILABLE, um.STATUS_FAILED):
            self._json({
                "success": False,
                "message": f"cannot start upgrade in status '{um.status}'",
            }, 409)
            return

        um.start_upgrade()
        self._json({
            "success": True,
            "message": "upgrade started",
            "status": um.status,
        })


def start_server(upgrade_manager, port=SERVER_PORT) -> HTTPServer:
    """启动升级 HTTP API 服务（daemon 线程），返回 HTTPServer 实例。"""
    _UpgradeHandler.upgrade_manager = upgrade_manager
    server = _ThreadingServer(("0.0.0.0", port), _UpgradeHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="upgrade-api")
    t.start()
    print(f"[coding] upgrade HTTP API server started on 0.0.0.0:{port}", flush=True)
    return server
