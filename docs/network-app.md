# Luwu OS 联网功能文档

> 适用版本：Luwu OS v1.0+ | 硬件：树莓派 + SPI LCD 320×240 | 系统：Raspbian (Debian) + systemd

---

## 一、使用指南

### 1.1 如何进入联网 App

主界面选中 **WiFi 卡片** → 按 **D** 键（确认），Launcher 将启动联网 App。

### 1.2 物理按键映射

联网 App 复用机身 4 个物理按键（定义在 luwu-keys.dts 的 gpio-keys）：

| 按键 | GPIO | Qt 键值 | 扫描模式 | 手动选网模式 | 国家地区页面 |
|------|------|---------|----------|-------------|-------------|
| **A** | GPIO17 | KEY_LEFT | 重置网络（连回 XGO2） | 上移 | 上移 |
| **B** | GPIO22 | KEY_RIGHT | 进入国家地区设置 | 下移 | 下移 |
| **C** | GPIO23 | KEY_BACK | 退出 App | 返回 | 返回 |
| **D** | GPIO24 | KEY_ENTER | 进入手动选网 | 选择 | 确认 |

### 1.3 扫码连接（默认模式）

打开 App 后，摄像头自动启动，将手机上的 **WiFi 二维码**（Android / XGO-APP 格式）对准摄像头：

- 标准格式：`WIFI:S:MyWiFi;T:WPA;P:password123;;`
- 检测到二维码自动解析 SSID、密码、加密方式
- 自动连接，成功后显示 **"WiFi连接成功!"** 约 3 秒后自动退出
- 失败显示 **"连接失败,请重试"**，3 秒后回到扫描状态

屏幕上方显示当前已连接的 WiFi，四角提示按键功能。

### 1.4 手动选择网络（D 键）

按 **D** 进入手动选网模式：

1. **WiFi 列表页**：自动扫描附近 WiFi，按信号强度排序
   - A：上移 / B：下移 / C：返回扫码 / D：选择
   - 每条显示信号强度条（▂▄▆█）和 SSID
   - 有密码的 WiFi 末尾显示 `*`
2. **密码键盘页**：选择 SSID 后进入全键盘输入密码
   - A：光标左移 / B：光标右移 / C：退格 / D：按键
   - 支持大小写切换（↑ 键）
   - 输入完毕选中绿色 **确定** 键即可连接

### 1.5 国家/地区设置（B 键）

WiFi 监管域（Regulatory Domain）影响可用信道。中国（CN）支持 2.4GHz 信道 1-13，美国（US）仅支持 1-11。

按 **B** 进入国家选择页面：

1. 列表显示 15 个常用国家/地区
2. A/B 上下移动，当前高亮蓝色底色
3. 顶部显示当前已设置的国家代码（通过 `raspi-config` 读取）
4. 按 **D** 确认选择 → 弹出 **"将重启机器"** 二次确认对话框
5. 再按 **D** 确认 → 3 秒倒计时 → 自动重启设备

**注意**：监管域修改写入 `/boot/firmware/cmdline.txt`（内核启动参数），必须重启设备才能生效。倒计时到 1 时直接执行 `sudo reboot`，页面保留不动（关机本身需要时间）。

### 1.6 语言支持

App 通过 `/home/pi/luwu-os/configs/language.ini` 检测系统语言，自动切换中文 / 英文界面。

### 1.7 典型使用流程

```
主界面 WiFi 卡片 → D → 进入联网 App
  ├─ 扫码：对准二维码 → 自动连接 → 成功退出
  ├─ 手动：D → 选 SSID → D → 输密码 → D 确定 → 连接
  ├─ 重置：A → 重连 XGO2（密码 LuwuDynamics）
  └─ 国家：B → 选国家 → D → D 确认重启 → 重启机器
```

---

## 二、技术文档

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│ Launcher (C++ Qt)                                       │
│   main.cpp → GalleryView (主菜单) / DemoGridView (网格) │
│        │                                                │
│        ├─ 启动时 spawn: python3 preload_app.py          │
│        │     └─ 预加载 PySide6，阻塞在 FIFO 等待 target │
│        │                                                │
│        ├─ 用户选 WiFi 卡片 → D → launchApp()            │
│        │     ├─ Launcher hide()（释放 framebuffer）     │
│        │     ├─ 写 /tmp/luwu_preload.fifo               │
│        │     │    → preload readline → import app       │
│        │     │    → app.main() 运行                     │
│        │     └─ 按键通过 /tmp/luwu_keys.fifo 转发       │
│        │                                                │
│        └─ App 退出 → QProcess::finished 信号            │
│              → Launcher showFullScreen() → 重绘 → 恢复  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 进程模型

| 组件 | 进程 | 技术栈 | 生命周期 |
|------|------|--------|---------|
| Launcher | `luwu-launcher` | C++ Qt5 | 开机即启动（systemd simple service） |
| Preload | `python3 preload_app.py` | Python + PySide6 | Launcher fork，常驻等待 |
| App（联网） | (同上进程) | Python + PySide6 | preload 收到 target 后 import 运行 |

**设计亮点**：preload 常驻进程提前完成 PySide6 导入（~200ms），用户点击卡片后只需 import 目标模块 + main()，启动延迟从完整冷启动 ~800ms 降至 ~50ms。

### 2.3 按键转发机制（FIFO）

```
物理按键 → gpio-keys 驱动 → Linux input 事件
    → Launcher KeyFilter 全局拦截
        → 如果 App 运行中：写 /tmp/luwu_keys.fifo（非阻塞）
            → App QSocketNotifier 监听 FIFO
                → 收到数值 → 构造 QKeyEvent → postEvent 到 App
```

- Launcher 侧：`::open(KEYS_FIFO, O_WRONLY | O_NONBLOCK)` 非阻塞写入防止卡死
- App 侧：`os.open(FIFO, O_RDONLY | O_NONBLOCK)` + `QSocketNotifier` 事件驱动读取
- 退出时：Launcher `unlink(KEYS_FIFO)` 清理

### 2.4 帧处理流程

```
QTimer(66ms, ~15fps)
  │
  ├─ [country_mode] → _draw_country_ui() → PIL 绘制 → numpy → QImage → QPixmap
  ├─ [manual_mode]  → _draw_manual_ui()  → PIL 绘制 → numpy → QImage → QPixmap
  └─ [scanning]     → picamera2 capture_array()
                       → cv2.flip(mirror)
                       → cv2.cvtColor(BGR→GRAY) → pyzbar.decode()
                       → cv2.cvtColor(BGR→RGB) → PIL 叠加中文
                       → numpy → QImage → QPixmap → camera_label
```

- 扫描模式：Picamera2 (libcamera) 捕获 → OpenCV 处理 → PIL 中文叠加 → Qt 显示
- 手动/国家模式：PIL 直接绘制 UI（绕过摄像头，复用 camera_label 作为画布）

### 2.5 状态机

#### 主状态（`_state`）

```
scanning ──(检测到QR)──→ connecting ──(nmcli结果)──→ success → 3s后退出
   ↑                      │                        → failed  → 3s后→ scanning
   └──(3s超时)────────────┘
```

#### 手动模式状态（`_manual_step`）

```
None ──(D键)──→ wifi_list ──(D选择SSID)──→ keyboard ──(确定键)──→ None(连接)
                    ↑                           │
                    └──(C键,密码为空)────────────┘
```

#### 国家模式状态（`_country_step`）

```
None ──(B键)──→ country_list ──(D选中)──→ country_confirm
                    ↑                            │
                    │                      ┌─────┴─────┐
                    │                    C键取消    D键确认
                    │                   返回list   倒计时→重启
                    │                                │
                    └──(C键返回)──────────────────────┘
```

### 2.6 WiFi 连接流程

使用 NetworkManager (`nmcli`) 命令行接口：

```
1. nmcli connection delete luwu-wifi     (清理旧连接)
2. nmcli connection add                  (创建连接)
     type wifi con-name luwu-wifi
     ssid <SSID>
     wifi-sec.key-mgmt wpa-psk          (根据 T: 字段选择加密)
     wifi-sec.psk <password>
3. nmcli device wifi rescan             (强制硬件扫描)
4. sleep 2 → nmcli connection up luwu-wifi  (激活连接)
```

所有需要 `sudo` 的命令统一使用 `sudo -S` + 密码管道（`input="pi\n"`），兼容 systemd 无 TTY 环境。

**加密方式映射**：

| QR T: 字段 | nmcli key-mgmt |
|-----------|----------------|
| WPA / WPA2 / 默认 | `wpa-psk` |
| WEP | `ieee8021x` |
| NOPASS / 空 | 不添加加密参数 |

### 2.7 手动扫描时序

WiFi 扫描有严格的时序依赖：

```
[刚换过国家?] → sleep 4s（监管域切换冷却期）
    ↓
sudo nmcli device wifi rescan    ← 必须 sudo，触发硬件扫描
    ↓
sleep 3s（硬件扫描耗时 1~3s，异步等待完成）
    ↓
nmcli -t -f SSID,SIGNAL,SECURITY device wifi list   ← 读结果
    ↓
解析 → 去重（同 SSID 保留信号最强）→ 按信号排序
```

**注意**：`nmcli device wifi rescan` 是异步的——命令瞬间返回，网卡在后台扫描所有信道。如果不 sleep 直接 list，拿到的是旧缓存。刚换过国家时额外等 4s 让 `cfg80211` 监管域切换完成。

### 2.8 国家/地区监管域设置

#### 为什么需要重启

树莓派 WiFi 监管域有三层写入：

| 操作 | 生效方式 |
|------|---------|
| `raspi-config nonint do_wifi_country XX` | 写 `/boot/firmware/cmdline.txt`（`cfg80211.ieee80211_regdom=XX`），需重启内核模块 |
| `iw reg set XX` | 立即生效，但不持久（重启丢失） |
| `wpa_cli set country XX` | 立即生效，但不持久 |

`raspi-config` 同时做全部三项，但内核启动参数 `cfg80211.ieee80211_regdom` 是真正决定信道过滤的——这必须重启才能加载。

#### 为什么美国看不到 13 信道

```
中国 CN → 2.4GHz 信道 1-13（2472 MHz）
美国 US → 2.4GHz 信道 1-11（2462 MHz）
```

设置美国后 ch13 的 AP（如 XGO2）会被内核过滤，`nmcli list` 中直接不出现。

#### 当前国家读取

使用 `sudo -S raspi-config nonint get_wifi_country` 直接返回标准二字码（CN/US/JP...），比 `iw reg get`（返回内核内部枚举如 "country 98: DFS-FCC"）可靠。

### 2.9 UI 渲染方案

#### Qt Overlay 层（扫描模式）

- 4 个 QLabel corner（A/B/C/D 按键提示）
- 1 个 QLabel status（顶部居中，状态文字）
- 1 个 QLabel hint（底部居中，提示文字）
- 1 个 QLabel wifi_now（顶部居中，当前连接 SSID）
- 底部 camera_label 作为全屏背景承载摄像头画面

#### PIL 绘制层（手动模式、国家模式）

- 纯色背景（`(10, 10, 26)` 深蓝色）
- `ImageDraw.rectangle` / `ImageDraw.text` 绘制所有 UI
- 使用 `msyh.ttc` 微软雅黑字体渲染中文
- 绘制完成后：`np.array(bg)` → `QImage` → `QPixmap` → 复用 `camera_label` 显示
- 四角标签通过 `_draw_corners()` 统一绘制半透明黑底 + 白字

#### resize 布局（`resizeEvent`）

- `camera_label`：全屏填充
- 状态文字 + 提示文字：垂直居中组合排列
- 四角：固定 margin=12px，`adjustSize()` 计算实际尺寸后 `move()` 定位
- WiFi 标签：顶部居中，`adjustSize()` + `_reposition_wifi_label()` 保证不截断

### 2.10 线程模型

| 线程 | 用途 | 实现 |
|------|------|------|
| 主线程（GUI） | Qt 事件循环、PIL 绘制、帧处理 | QTimer(66ms) |
| `_WifiScanWorker` | 扫描附近 WiFi | QThread + Signal(list) |
| `_Worker` | 执行 nmcli 连接 | QThread + Signal(bool) |
| `_ResetWorker` | 重置网络到 XGO2 | QThread（无信号） |

**关键修复**：`_WifiScanWorker` 是方法内部类，`run()` 里不能直接访问 `self._country_changed`（内部类 self 指向 worker 实例，非页面实例）。修复方案：在创建 worker 前将值读到局部变量，利用 Python 闭包机制。

### 2.11 依赖

```
opencv-python  (cv2)          — 帧处理、色彩转换
pyzbar         (pyzbar)       — QR 码解码
picamera2                     — CSI 摄像头捕获
Pillow         (PIL)          — 中文字体渲染、手动 UI 绘制
numpy                         — PIL ↔ QImage 数据桥接
PySide6                       — Qt GUI 框架
NetworkManager (nmcli)        — WiFi 连接管理系统接口
raspi-config                  — WiFi 监管域读写
```

### 2.12 已知问题与约束

| 问题 | 说明 | 状态 |
|------|------|------|
| 🔒 emoji 显示为方块 | msyh.ttc 在 SPI LCD 上渲染 emoji 异常 | 已替换为 `*` |
| `iw reg get` 返回内核枚举 | 显示 `country 98: DFS-FCC` 非标准代码 | 改用 `raspi-config` 读取 |
| systemd 无 TTY | `sudo` 需交互式终端 | 统一 `sudo -S` + 密码管道 |
| 监管域切换需重启 | `cfg80211.ieee80211_regdom` 是内核 boot 参数 | 已实现倒计时自动重启 |
| 扫描需等 3s | `nmcli rescan` 异步，硬件扫描需时间 | 固定 sleep(3s) |
| 闭包引用 bug | 内部类 QThread 访问 self 属性 | 已通过局部变量 + 闭包修复 |
