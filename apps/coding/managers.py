"""Luwu OS - Coding 服务、程序、升级、文件列表管理器。"""
import sys
import os
import time
import signal
import threading
import subprocess
import json
import re
import urllib.request
import traceback

# --- 启动日志（stdout → launcher QProcess::ForwardedChannels → journal） ---
_M_TRACE_FILE = "/tmp/coding_startup.log"
_M_T0 = time.monotonic()

def _mtrace(msg):
    """输出带毫秒偏移的启动日志到 stdout 和文件。"""
    elapsed = (time.monotonic() - _M_T0) * 1000
    line = f"[coding][+{elapsed:7.1f}ms] {msg}"
    print(line, flush=True)
    try:
        with open(_M_TRACE_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

from config import (
    BLOCKLY_PYTHON, BLOCKLY_SERVICES_DIR, BLOCKLY_PROJECTS_DIR,
    kill_blockly_service,
)


# ========================================================================
# Blockly 服务管理器
# ========================================================================
class BlocklyServiceManager:
    """管理 xgo_blockly 服务的启动/停止。"""

    def __init__(self):
        self.process = None
        self.is_running = False

    def start(self):
        """在独立线程中启动 Blockly 服务。"""
        _mtrace("BlocklyServiceManager.start enter")
        if not os.path.exists(BLOCKLY_PYTHON):
            _mtrace(f"ERROR: venv not found at {BLOCKLY_PYTHON}")
            return

        # 验证 xgo_blockly 已安装
        _mtrace("verifying xgo_blockly import...")
        try:
            result = subprocess.run(
                [BLOCKLY_PYTHON, "-c", "import xgo_blockly"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                err = result.stderr.strip() if result.stderr else "(no stderr)"
                out = result.stdout.strip()[:200] if result.stdout else ""
                _mtrace(f"ERROR: xgo_blockly import failed rc={result.returncode} stderr={err} stdout={out}")
                return
            _mtrace("xgo_blockly verified OK")
        except Exception as e:
            _mtrace(f"verification exception: {e}")

        _mtrace(f"spawning Popen: {BLOCKLY_PYTHON} -m xgo_blockly.cli --port 80")

        child_env = os.environ.copy()
        child_env.pop("FLASK_ENV", None)
        child_env.setdefault("FLASK_DEBUG", "1")

        try:
            self.process = subprocess.Popen(
                [BLOCKLY_PYTHON, "-m", "xgo_blockly.cli", "--port", "80"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=child_env,
            )
            self.is_running = True
            _mtrace(f"Popen started PID={self.process.pid}, now blocking on wait()")

            # 阻塞等待进程结束
            return_code = self.process.wait()
            self.is_running = False
            if return_code != 0:
                stderr_output = self.process.stderr.read() if self.process.stderr else ""
                print(f"[coding] service exited code={return_code}: {stderr_output}", flush=True)
            else:
                print("[coding] service exited normally", flush=True)
        except Exception as e:
            print(f"[coding] service start error: {e}", flush=True)
            self.is_running = False

    def stop(self):
        """优雅地停止 Blockly 服务。"""
        if not self.process or not self.is_running:
            return True
        try:
            print("[coding] stopping Blockly service...", flush=True)
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
                print("[coding] service stopped gracefully", flush=True)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
                print("[coding] service force killed", flush=True)
            self.is_running = False
            return True
        except ProcessLookupError:
            self.is_running = False
            return True
        except Exception as e:
            print(f"[coding] stop error: {e}", flush=True)
            return False

    def is_alive(self):
        if not self.process:
            return False
        poll = self.process.poll()
        if poll is not None:
            self.is_running = False
        return self.is_running


# ========================================================================
# 程序运行管理器
# ========================================================================
class ProgramRunner:
    """管理 Blockly 程序的运行和停止。"""

    def __init__(self):
        self.process = None
        self.is_running = False

    def run(self, file_path: str) -> bool:
        """运行 .py 程序。"""
        if not os.path.exists(file_path):
            print(f"[coding] file not found: {file_path}", flush=True)
            return False
        if not os.path.exists(BLOCKLY_PYTHON):
            print(f"[coding] venv not found: {BLOCKLY_PYTHON}", flush=True)
            return False

        try:
            print(f"[coding] running: {file_path}", flush=True)
            child_env = os.environ.copy()
            child_env.pop("FLASK_ENV", None)
            child_env.setdefault("FLASK_DEBUG", "1")

            if os.path.exists(BLOCKLY_SERVICES_DIR):
                child_env["PYTHONPATH"] = BLOCKLY_SERVICES_DIR
            child_env["PYTHONUNBUFFERED"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"

            self.process = subprocess.Popen(
                [BLOCKLY_PYTHON, "-u", file_path],
                stdout=None,
                stderr=None,
                text=True,
                env=child_env,
            )
            self.is_running = True
            print(f"[coding] program started PID={self.process.pid}", flush=True)
            return True
        except Exception as e:
            print(f"[coding] run error: {e}", flush=True)
            self.is_running = False
            return False

    def stop(self) -> bool:
        """停止正在运行的程序。"""
        if not self.process or not self.is_running:
            return True
        try:
            print("[coding] stopping program...", flush=True)
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
                print("[coding] program stopped gracefully", flush=True)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
                print("[coding] program force killed", flush=True)
            self.is_running = False
            return True
        except ProcessLookupError:
            self.is_running = False
            return True
        except Exception as e:
            print(f"[coding] stop error: {e}", flush=True)
            return False

    def check_alive(self) -> bool:
        if not self.process:
            return False
        poll = self.process.poll()
        if poll is not None:
            self.is_running = False
        return self.is_running


# ========================================================================
# 升级管理器
# ========================================================================
class UpgradeManager:
    """管理 xgo-blockly-luwuos 的版本检查和升级。"""

    STATUS_IDLE = 'idle'
    STATUS_CHECKING = 'checking'
    STATUS_AVAILABLE = 'available'
    STATUS_NO_UPDATE = 'no_update'
    STATUS_UPGRADING = 'upgrading'
    STATUS_RESTARTING = 'restarting'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'

    PYPI_MIRRORS = [
        {
            'name': 'tsinghua',
            'index': 'https://pypi.tuna.tsinghua.edu.cn/simple/',
            'trusted_host': 'pypi.tuna.tsinghua.edu.cn',
            'api_url': 'https://pypi.tuna.tsinghua.edu.cn/simple/xgo-blockly-luwuos/',
        },
        {
            'name': 'pypi',
            'index': None,
            'trusted_host': None,
            'api_url': 'https://pypi.org/pypi/xgo-blockly-luwuos/json',
        },
    ]

    def __init__(self):
        self.status = self.STATUS_IDLE
        self.current_version = ''
        self.latest_version = ''
        self.message = ''
        self._lock = threading.Lock()
        self._service_manager = None  # BlocklyServiceManager 引用
        self._check_thread = None
        self._upgrade_thread = None
        self._winning_mirror = None  # 版本检查竞速胜出的镜像索引
        self._cancel_flag = threading.Event()
        self._pip_process = None  # pip 子进程引用，用于取消时杀死

    def set_service_manager(self, sm):
        """绑定 BlocklyServiceManager，用于升级后重启服务。"""
        self._service_manager = sm

    def cancel(self):
        """取消正在进行的升级（线程安全）。"""
        self._cancel_flag.set()
        with self._lock:
            if self._pip_process and self._pip_process.poll() is None:
                try:
                    self._pip_process.kill()
                    print("[coding] upgrade: pip process killed by user", flush=True)
                except Exception as e:
                    print(f"[coding] upgrade: failed to kill pip: {e}", flush=True)

    @staticmethod
    def get_current_version():
        """获取当前安装的 xgo-blockly-luwuos 版本号。"""
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'show', 'xgo-blockly-luwuos'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith('Version:'):
                        return line.split(':', 1)[1].strip()
        except Exception:
            pass
        return 'unknown'

    @staticmethod
    def get_latest_version_parallel():
        """并行查询所有 PyPI 源，返回 (version, winning_mirror_index)。

        多个源并发查询，谁先返回就用谁的结果，不做版本号比较。
        因为不同源的同一个版本号含义相同，先到先得即可。
        """
        mirrors = UpgradeManager.PYPI_MIRRORS
        results = {}
        winner = {'idx': None, 'version': None}  # mutable closure
        event = threading.Event()

        def _fetch(mirror, idx):
            try:
                url = mirror['api_url']
                req = urllib.request.Request(url)
                response = urllib.request.urlopen(req, timeout=8)
                data = response.read().decode('utf-8')

                if 'pypi.org' in url and 'tuna' not in url:
                    info = json.loads(data)
                    version = info.get('info', {}).get('version')
                    if version:
                        results[idx] = version
                        if not event.is_set():
                            winner['idx'] = idx
                            winner['version'] = version
                        event.set()
                else:
                    versions = re.findall(
                        r'xgo-blockly-luwuos-([0-9][0-9a-zA-Z.]*?)(?:-py\d|\.tar\.gz)',
                        data
                    )
                    if versions:
                        try:
                            from packaging.version import Version
                            results[idx] = str(max(versions, key=lambda v: Version(v)))
                        except ImportError:
                            def _parse(v):
                                try:
                                    return tuple(int(x) for x in v.split('.'))
                                except Exception:
                                    return (0,)
                            max_ver = max((_parse(v) for v in versions), key=lambda t: t)
                            results[idx] = '.'.join(str(x) for x in max_ver)
                        if not event.is_set():
                            winner['idx'] = idx
                            winner['version'] = results[idx]
                        event.set()
            except Exception:
                pass

        threads = []
        for i, mirror in enumerate(mirrors):
            t = threading.Thread(target=_fetch, args=(mirror, i), daemon=True)
            threads.append(t)
            t.start()

        # 等第一个结果返回（最长 10s）
        event.wait(timeout=10)

        if winner['idx'] is not None:
            return winner['version'], winner['idx']
        return None, None

    @staticmethod
    def _compare_versions(current, latest):
        """比较版本号，返回 True 表示 latest > current。"""
        try:
            from packaging.version import Version
            return Version(latest) > Version(current)
        except ImportError:
            def _parse(v):
                try:
                    return tuple(int(x) for x in v.split('.'))
                except Exception:
                    return (0,)
            return _parse(latest) > _parse(current)

    def start_check(self):
        """在后台线程中检查更新。"""
        if self._check_thread and self._check_thread.is_alive():
            return
        self._check_thread = threading.Thread(target=self._do_check, daemon=True)
        self._check_thread.start()

    def _do_check(self):
        _mtrace("UpgradeManager._do_check enter")
        with self._lock:
            self.status = self.STATUS_CHECKING
        try:
            current = self.get_current_version()
            _mtrace(f"_do_check: current={current}")
            latest, winner_idx = self.get_latest_version_parallel()
            _mtrace(f"_do_check: latest={latest} winner={winner_idx}")
            with self._lock:
                self.current_version = current
                self.latest_version = latest
                self._winning_mirror = winner_idx
                if latest and current and current != 'unknown' and self._compare_versions(current, latest):
                    self.status = self.STATUS_AVAILABLE
                    _mtrace("_do_check done: AVAILABLE (update found)")
                else:
                    self.status = self.STATUS_NO_UPDATE
                    _mtrace("_do_check done: NO_UPDATE")
        except Exception as e:
            _mtrace(f"_do_check exception: {e}")
            with self._lock:
                if self.status == self.STATUS_CHECKING:
                    self.status = self.STATUS_IDLE

    def get_mirror_name(self):
        """返回版本检查胜出镜像名称（tsinghua / pypi），未检查时返回空字符串。"""
        idx = self._winning_mirror
        return self.PYPI_MIRRORS[idx]["name"] if idx is not None else ""

    def start_upgrade(self):
        """开始升级（后台线程），允许 available 和 failed（重试）状态。"""
        if self.status not in (self.STATUS_AVAILABLE, self.STATUS_FAILED):
            return
        self._cancel_flag.clear()
        self._pip_process = None
        self._upgrade_thread = threading.Thread(target=self._do_upgrade, daemon=True)
        self._upgrade_thread.start()

    def _do_upgrade(self):
        try:
            self._do_upgrade_impl()
        except Exception as e:
            print(f"[coding] upgrade: FATAL exception: {e}", flush=True)
            traceback.print_exc()
            with self._lock:
                self.status = self.STATUS_FAILED
                self.message = ''

    def _do_upgrade_impl(self):
        with self._lock:
            self.status = self.STATUS_UPGRADING
            self.message = ''

        target = self.latest_version
        success = False

        # 优先用版本检查竞速胜出的镜像，其他镜像兜底
        mirror_order = list(range(len(self.PYPI_MIRRORS)))
        if self._winning_mirror is not None:
            mirror_order.remove(self._winning_mirror)
            mirror_order.insert(0, self._winning_mirror)

        for idx in mirror_order:
            # 检查取消标志
            if self._cancel_flag.is_set():
                print("[coding] upgrade: cancelled by user", flush=True)
                with self._lock:
                    self.status = self.STATUS_FAILED
                    self.message = ''
                return

            mirror = self.PYPI_MIRRORS[idx]
            with self._lock:
                self.message = f"{mirror['name']}源"

            cmd = [
                sys.executable, '-m', 'pip', 'install', '--upgrade',
                '--no-cache-dir', '--break-system-packages',
            ]
            if mirror['index']:
                cmd.extend(['-i', mirror['index'], '--trusted-host', mirror['trusted_host']])
            cmd.append(f'xgo-blockly-luwuos=={target}')

            print(f"[coding] upgrade: pip install via {mirror['name']} → {target}", flush=True)
            try:
                self._pip_process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                try:
                    stdout, stderr = self._pip_process.communicate(timeout=600)
                except subprocess.TimeoutExpired:
                    self._pip_process.kill()
                    self._pip_process.communicate()
                    print(f"[coding] upgrade: {mirror['name']} timeout after 600s", flush=True)
                    self._pip_process = None
                    continue

                rc = self._pip_process.returncode
                self._pip_process = None

                if self._cancel_flag.is_set():
                    print("[coding] upgrade: cancelled by user", flush=True)
                    with self._lock:
                        self.status = self.STATUS_FAILED
                        self.message = ''
                    return

                if rc == 0:
                    print(f"[coding] upgrade: {mirror['name']} install OK", flush=True)
                    success = True
                    break
                else:
                    print(f"[coding] upgrade: {mirror['name']} failed rc={rc}", flush=True)
                    if stderr:
                        print(f"[coding] pip stderr: {stderr[-300:]}", flush=True)
            except Exception as e:
                print(f"[coding] upgrade: {mirror['name']} exception: {e}", flush=True)
                self._pip_process = None

        if not success:
            with self._lock:
                self.status = self.STATUS_FAILED
                self.message = ''
            print("[coding] upgrade: all mirrors failed", flush=True)
            return

        # pip install 成功 → 重启服务
        with self._lock:
            self.status = self.STATUS_RESTARTING
            self.message = ''
        print("[coding] upgrade: restarting service...", flush=True)

        try:
            if self._service_manager:
                self._service_manager.stop()
            kill_blockly_service()
        except Exception as e:
            print(f"[coding] upgrade: stop service error: {e}", flush=True)
        time.sleep(0.5)

        if self._service_manager:
            t = threading.Thread(target=self._service_manager.start, daemon=True)
            t.start()

        time.sleep(3)

        new_version = self.get_current_version()
        with self._lock:
            self.current_version = new_version
            self.status = self.STATUS_SUCCESS
        print(f"[coding] upgrade: done, new version={new_version}", flush=True)


# ========================================================================
# 文件列表管理器
# ========================================================================
class FileListManager:
    """管理 Blockly 项目文件列表。"""

    def __init__(self):
        self.files = []
        self.selected_index = 0
        self.scroll_offset = 0
        self.visible_count = 6
        self.refresh()

    def refresh(self):
        """刷新文件列表。"""
        try:
            if not os.path.exists(BLOCKLY_PROJECTS_DIR):
                self.files = []
                return
            all_files = [
                f for f in os.listdir(BLOCKLY_PROJECTS_DIR) if f.endswith(".py")
            ]
            self.files = sorted(all_files)
            self.selected_index = 0
            self.scroll_offset = 0
        except Exception as e:
            print(f"[coding] file list error: {e}", flush=True)
            self.files = []

    def count(self) -> int:
        return len(self.files)

    def selected_filepath(self):
        """返回当前选中文件的完整路径。"""
        if not self.files:
            return None
        return os.path.join(BLOCKLY_PROJECTS_DIR, self.files[self.selected_index])

    def selected_filename(self):
        """返回当前选中文件名。"""
        if not self.files:
            return None
        return self.files[self.selected_index]

    def move_up(self):
        if self.selected_index > 0:
            self.selected_index -= 1
            if self.selected_index < self.scroll_offset:
                self.scroll_offset = self.selected_index

    def move_down(self):
        if self.selected_index < len(self.files) - 1:
            self.selected_index += 1
            if self.selected_index >= self.scroll_offset + self.visible_count:
                self.scroll_offset = self.selected_index - self.visible_count + 1