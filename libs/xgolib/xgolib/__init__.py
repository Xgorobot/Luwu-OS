"""
XGO 机器人控制库
统一入口，自动选择设备类型
"""

# 直接导入所有类
from .xgolib_dog import XGO_DOG
from .xgolib_rider import XGO_RIDER

__version__ = '1.1.5'
__all__ = ['XGO', 'XGO_DOG', 'XGO_RIDER']

# 自动扫描的候选串口列表（按优先级排列）
SCAN_PORTS = ["/dev/ttyAMA5", "/dev/ttyAMA0"]

def _scan_port(baud, verbose):
    """自动扫描序列口，返回第一个有效回应的端口和固件号，未找到返回 (None, None)"""
    import os
    for port in SCAN_PORTS:
        if not os.path.exists(port):          # 节点不存在直接跳，不等超时
            if verbose:
                print(f"[scan] {port} not found, skip")
            continue
        try:
            probe = XGO_DOG(port, baud, version="xgomini", verbose=verbose)
            firmware = probe.read_firmware()
            probe.ser.close()
            if firmware and (firmware[:2] == 'MW' or firmware[0] in ['M', 'L', 'W', 'R']):
                if verbose:
                    print(f"[scan] Found device on {port}, firmware={firmware}")
                return port, firmware
        except PermissionError as e:          # 端口被占用，单独提示
            print(f"[scan] {port} is busy: {e}")
        except Exception as e:
            if verbose:
                print(f"[scan] {port} no response: {e}")
    return None, None

def XGO(version="auto", port=None, baud=115200, verbose=False):
    """
    XGO类自动选择对应库的函数
  
    Args:
        version: 设备版本（支持短名和全名）
            "auto"                    - 自动检测
            "mini"   / "xgomini"      - XGO-MINI
            "lite"   / "xgolite"      - XGO-LITE
            "mini3w" / "xgomini2sw"   - XGO-mini2SW
            "rider"  / "xgorider"     - XGO-RIDER
        port: 串口设备路径，不传则自动扫描 SCAN_PORTS
        baud: 波特率
        verbose: 是否显示调试信息
    """
    # 短名 → 内部全名 映射
    _alias = {
        "mini":   "xgomini",
        "lite":   "xgolite",
        "mini3w": "xgomini2sw",
        "rider":  "xgorider",
    }
    version = _alias.get(version, version)  # 短名转全名，已是全名则原样保留
    if version == "auto":
        try:
            # port 未指定时自动扫描；已指定时只读该端口
            if port is None:
                found_port, firmware = _scan_port(baud, verbose)
                if found_port is None:
                    print("Auto scan failed, using default: /dev/ttyAMA0 xgomini")
                    return XGO_DOG("/dev/ttyAMA0", baud, version="xgomini", verbose=verbose)
                port = found_port
            else:
                temp_dog = XGO_DOG(port, baud, version="xgomini", verbose=verbose)
                firmware = temp_dog.read_firmware()
                temp_dog.reset()
                temp_dog.ser.close()

            print(f"Detected firmware: {firmware}")
            
            if firmware and firmware[0] == 'R':
                print("Auto-detected: XGO-RIDER")
                return XGO_RIDER(port, baud, version="xgorider", verbose=verbose)
            elif firmware and (firmware[:2] == 'MW' or firmware[0] in ['M', 'L', 'W']):
                if firmware[:2] == 'MW' or firmware[0] == 'W':
                    detected_version = 'xgomini2sw'
                    label = 'XGO-mini2SW'
                else:
                    version_map = {
                        'M': 'xgomini',
                        'L': 'xgolite',
                    }
                    detected_version = version_map.get(firmware[0], 'xgomini')
                    label = detected_version.upper()
                print(f"Auto-detected: {label}")
                return XGO_DOG(port, baud, version=detected_version, verbose=verbose)
            else:
                print("Auto-detection failed, using default: XGO-MINI")
                return XGO_DOG(port, baud, version="xgomini", verbose=verbose)
                
        except Exception as e:
            print(f"Auto detection failed: {e}, using default: XGO-MINI")
            return XGO_DOG(port, baud, version="xgomini", verbose=verbose)
    
    elif version in ["xgomini", "xgolite", "xgomini2sw", "xgomini3W"]:
        if port is None:
            port, _ = _scan_port(baud, verbose)
            port = port or "/dev/ttyAMA0"
        return XGO_DOG(port, baud, version=version, verbose=verbose)
    
    elif version == "xgorider":
        if port is None:
            port, _ = _scan_port(baud, verbose)
            port = port or "/dev/ttyAMA0"
        return XGO_RIDER(port, baud, version=version, verbose=verbose)
    
    else:
        print(f"Warning: Unknown version '{version}', using 'xgomini' instead")
        if port is None:
            port, _ = _scan_port(baud, verbose)
            port = port or "/dev/ttyAMA0"
        return XGO_DOG(port, baud, version="xgomini", verbose=verbose)