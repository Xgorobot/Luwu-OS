'''
xgo图形化python库  edu库（LuwU OS 新镜像版）
'''
import cv2
import numpy as np
import math
import os, sys, time, json, base64
from PIL import Image
import json
import threading
import subprocess
# from xgolib import XGO
# from keras.preprocessing import image
# import _thread  使用_thread会报错，坑！


__versinon__ = '2.0.0'
__last_modified__ = '2026/5/11'

camera_still = False

'''
人脸检测
'''
def getFaceBox(net, frame, conf_threshold=0.7):
    frameOpencvDnn = frame.copy()
    frameHeight = frameOpencvDnn.shape[0]
    frameWidth = frameOpencvDnn.shape[1]
    blob = cv2.dnn.blobFromImage(frameOpencvDnn, 1.0, (300, 300), [104, 117, 123], True, False)
    net.setInput(blob)
    detections = net.forward()
    bboxes = []
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > conf_threshold:
            x1 = int(detections[0, 0, i, 3] * frameWidth)
            y1 = int(detections[0, 0, i, 4] * frameHeight)
            x2 = int(detections[0, 0, i, 5] * frameWidth)
            y2 = int(detections[0, 0, i, 6] * frameHeight)
            bboxes.append([x1, y1, x2, y2])
            cv2.rectangle(frameOpencvDnn, (x1, y1), (x2, y2), (0, 255, 0), int(round(frameHeight / 150)),8)  
    return frameOpencvDnn, bboxes

'''
手势识别函数
'''
def hand_pos(angle):
    """
    手势识别函数 - 优化版本
    根据手指角度判断手势，增加容错性和准确性
    """
    if not angle or len(angle) != 5:
        return None
        
    pos = None
    # 手指角度阈值优化 - 增加容错范围
    thumb_threshold = 55   # 大拇指阈值调整
    finger_threshold = 55  # 其他手指阈值调整
    
    # 手指角度
    f1 = angle[0]  # 大拇指角度
    f2 = angle[1]  # 食指角度  
    f3 = angle[2]  # 中指角度
    f4 = angle[3]  # 无名指角度
    f5 = angle[4]  # 小拇指角度
    
    # 手指状态判断（优化后的阈值）
    thumb_up = f1 < thumb_threshold
    index_up = f2 < finger_threshold
    middle_up = f3 < finger_threshold  
    ring_up = f4 < finger_threshold
    pinky_up = f5 < finger_threshold
    
    thumb_down = f1 >= thumb_threshold
    index_down = f2 >= finger_threshold
    middle_down = f3 >= finger_threshold
    ring_down = f4 >= finger_threshold  
    pinky_down = f5 >= finger_threshold
    
    # 手势识别逻辑优化（按优先级排序）
    
    # 1. 五指张开 - 最容易识别
    if thumb_up and index_up and middle_up and ring_up and pinky_up:
        pos = '5'
    
    # 2. 拳头 - 全部手指弯曲
    elif thumb_down and index_down and middle_down and ring_down and pinky_down:
        pos = 'Stone'
    
    # 3. 特殊手势 - 按特征优先级
    elif thumb_up and index_down and middle_down and ring_down and pinky_down:
        pos = 'Good'  # 站起大拇指
    
    elif thumb_up and index_down and middle_up and ring_up and pinky_down:
        pos = 'Rock'  # 摇滚手势
    
    elif thumb_up and index_down and middle_up and ring_up and pinky_up:
        pos = 'Ok'    # OK手势（近似）
    
    # 4. 数字手势 - 按伸出手指数量
    elif thumb_down and index_up and middle_down and ring_down and pinky_down:
        pos = '1'     # 一根手指
    
    elif thumb_down and index_up and middle_up and ring_down and pinky_down:
        pos = '2'     # 两根手指
    
    elif thumb_down and index_up and middle_up and ring_up and pinky_down:
        pos = '3'     # 三根手指
    
    elif thumb_down and index_up and middle_up and ring_up and pinky_up:
        pos = '4'     # 四根手指
    
    # 5. 有歧义的情况 - 添加容错判断
    # 如果上述所有条件都不符合，尝试放宽条件
    elif not pos:
        # 放宽阈值重新判断
        relaxed_threshold = 70
        
        # 重新计算手指状态
        t_up = f1 < relaxed_threshold
        i_up = f2 < relaxed_threshold
        m_up = f3 < relaxed_threshold
        r_up = f4 < relaxed_threshold  
        p_up = f5 < relaxed_threshold
        
        if t_up and not i_up and not m_up and not r_up and not p_up:
            pos = 'Good'
        elif not t_up and i_up and not m_up and not r_up and not p_up:
            pos = '1'
        elif not t_up and i_up and m_up and not r_up and not p_up:
            pos = '2'
        elif t_up and i_up and m_up and r_up and p_up:
            pos = '5'
        elif not t_up and not i_up and not m_up and not r_up and not p_up:
            pos = 'Stone'
    
    return pos

# 手部关键点连接（用于绘制骨架），基于 MediaPipe 21 点模型
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),       # 拇指
    (0, 5), (5, 6), (6, 7), (7, 8),       # 食指
    (0, 9), (9, 10), (10, 11), (11, 12),  # 中指
    (0, 13), (13, 14), (14, 15), (15, 16),# 无名指
    (0, 17), (17, 18), (18, 19), (19, 20),# 小指
    (5, 9), (9, 13), (13, 17),             # 指根横向
]

def draw_hand_landmarks(img, pts, color=(0, 255, 0), thickness=2):
    """在手部图像上绘制 21 个关键点和连接线。"""
    h, w = img.shape[:2]
    for x, y in pts:
        if 0 <= x < w and 0 <= y < h:
            cv2.circle(img, (x, y), 3, color, -1)
    for i, j in HAND_CONNECTIONS:
        if i < len(pts) and j < len(pts):
            x1, y1 = pts[i]
            x2, y2 = pts[j]
            if 0 <= x1 < w and 0 <= y1 < h and 0 <= x2 < w and 0 <= y2 < h:
                cv2.line(img, (x1, y1), (x2, y2), color, thickness)

def color(value):
  digit = list(map(str, range(10))) + list("ABCDEF")
  value = value.upper()
  if isinstance(value, tuple):
    string = '#'
    for i in value:
      a1 = i // 16
      a2 = i % 16
      string += digit[a1] + digit[a2]
    return string
  elif isinstance(value, str):
    a1 = digit.index(value[1]) * 16 + digit.index(value[2])
    a2 = digit.index(value[3]) * 16 + digit.index(value[4])
    a3 = digit.index(value[5]) * 16 + digit.index(value[6])
    return (a3, a2, a1)



class XGOEDU():
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if XGOEDU._initialized:
            return
        XGOEDU._initialized = True
        print("[XGOEDU] 首次初始化硬件资源...")

        # PySide6 显示层初始化
        from PySide6.QtWidgets import QApplication, QLabel
        from PySide6.QtGui import QPixmap, QColor, QFont, QFontDatabase
        from PySide6.QtCore import Qt

        self._app = QApplication.instance() or QApplication(sys.argv)

        self._canvas = QPixmap(320, 240)
        self._canvas.fill(QColor("black"))

        self._label = QLabel()
        self._label.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self._label.resize(320, 240)
        self._label.move(0, 0)
        self._label.setPixmap(self._canvas)
        self._label.show()

        font_id = QFontDatabase.addApplicationFont("/home/pi/luwu-os/model/msyh.ttc")
        families = QFontDatabase.applicationFontFamilies(font_id)
        self._font_family = families[0] if families else ""

        self.cap = None
        self.hand = None
        self.yolo = None
        self.face = None
        self.face_classifier = None
        self.classifier = None
        self.agesexmark = None
        self.camera_still = False
        self.picam2 = None
        self.camera_config = None

        # ── gpio-keys 按键设备初始化 ──────────────────────────
        import evdev
        self._key_dev = None
        self._key_states = {"a": False, "b": False, "c": False, "d": False}
        self._key_map = {
            evdev.ecodes.KEY_LEFT: "a",
            evdev.ecodes.KEY_RIGHT: "b",
            evdev.ecodes.KEY_BACK: "c",
            evdev.ecodes.KEY_ENTER: "d",
        }
        for candidate in (
            "/dev/input/by-path/platform-luwu-keys-event",
            "/dev/input/event1",
        ):
            try:
                self._key_dev = evdev.InputDevice(candidate)
                print(f"[XGOEDU] 按键设备: {candidate}")
                break
            except Exception:
                pass

    # ── 内部辅助方法 ──────────────────────────────────────

    def _qcolor(self, color):
        from PySide6.QtGui import QColor
        _map = {
            'WHITE': 'white', 'BLACK': 'black', 'RED': 'red',
            'GREEN': 'green', 'BLUE': 'blue', 'YELLOW': 'yellow',
            'CYAN': 'cyan', 'MAGENTA': 'magenta', 'GRAY': 'gray',
        }
        if isinstance(color, tuple):
            return QColor(*color[:3])
        elif isinstance(color, str):
            return QColor(_map.get(color.upper(), color))
        return QColor('white')

    def _flush(self):
        self._label.setPixmap(self._canvas)
        self._app.processEvents()

    def _show_pil(self, img):
        """PIL Image → QPixmap 显示"""
        import numpy as np
        from PySide6.QtGui import QImage, QPixmap
        arr = np.array(img.convert("RGB"))
        h, w, c = arr.shape
        qimg = QImage(arr.data.tobytes(), w, h, w * c,
                      QImage.Format.Format_RGB888)
        self._label.setPixmap(QPixmap.fromImage(qimg))
        self._app.processEvents()

    def _clear_display(self):
        from PySide6.QtGui import QColor
        self._canvas.fill(QColor("black"))
        self._flush()
        
    def open_camera(self):
        if self.picam2 is None:
            from picamera2 import Picamera2
            from libcamera import Transform
            self.picam2 = Picamera2()
            self.camera_config = self.picam2.create_preview_configuration(
                main={"size": (320, 240), "format": "RGB888"},  # 强制指定RGB格式
                transform=Transform(hflip=1, vflip=0)
            )
            self.picam2.configure(self.camera_config)
            self.picam2.start()
            time.sleep(1)

    def close_camera(self):
        """释放摄像头资源"""
        if self.picam2 is not None:
            try:
                self.picam2.stop()
                self.picam2.close()
                print("[XGOEDU] 摄像头已释放")
            except Exception as e:
                print(f"[XGOEDU] 释放摄像头异常: {e}")
            finally:
                self.picam2 = None

    def cleanup(self):
        """统一释放所有硬件资源（摄像头、GPIO等），程序退出时调用"""
        print("[XGOEDU] 正在释放所有硬件资源...")
        self.camera_still = False
        self.close_camera()
        try:
            GPIO.cleanup()
            print("[XGOEDU] GPIO 已释放")
        except Exception as e:
            print(f"[XGOEDU] GPIO 释放异常: {e}")
        # 重置单例状态，允许下次重新初始化
        XGOEDU._initialized = False
        XGOEDU._instance = None
        print("[XGOEDU] 所有资源已释放")

    def fetch_token(self):
        from urllib.request import urlopen
        from urllib.request import Request
        from urllib.error import URLError
        from urllib.parse import urlencode
        API_KEY = 'Q4ZgU8bfnhA8HQFnNucBO2ut'
        SECRET_KEY = 'MqFrVgdwoM8ZuGIp0NIFF7qfYti4mjP6'
        TOKEN_URL = 'http://aip.baidubce.com/oauth/2.0/token'
        params = {'grant_type': 'client_credentials',
                'client_id': API_KEY,
                'client_secret': SECRET_KEY}
        post_data = urlencode(params)
        post_data = post_data.encode( 'utf-8')
        req = Request(TOKEN_URL, post_data)
        try:
            f = urlopen(req)
            result_str = f.read()
        except URLError as err:
            print('token http response http code : ' + str(err.code))
            result_str = err.read()
        result_str =  result_str.decode()

        #print(result_str)
        result = json.loads(result_str)
        #print(result)
        SCOPE=False
        if ('access_token' in result.keys() and 'scope' in result.keys()):
            #print(SCOPE)
            if SCOPE and (not SCOPE in result['scope'].split(' ')):  # SCOPE = False 忽略检查
                raise DemoError('scope is not correct')
            #print('SUCCESS WITH TOKEN: %s  EXPIRES IN SECONDS: %s' % (result['access_token'], result['expires_in']))
            return result['access_token']
        else:
            raise DemoError('MAYBE API_KEY or SECRET_KEY not correct: access_token or scope not found in token response')



    #绘画直线
    '''
    x1,y1为初始点坐标,x2,y2为终止点坐标
    '''
    def lcd_line(self, x1, y1, x2, y2, color="WHITE", width=2):
        from PySide6.QtGui import QPainter, QPen
        p = QPainter(self._canvas)
        pen = QPen(self._qcolor(color))
        pen.setWidth(width)
        p.setPen(pen)
        p.drawLine(x1, y1, x2, y2)
        p.end()
        self._flush()
    #绘画圆
    '''
    x1,y1,x2,y2为定义给定边框的两个点,angle0为初始角度,angle1为终止角度
    '''
    def lcd_circle(self, x1, y1, x2, y2, angle0, angle1,
                   color="WHITE", width=2):
        from PySide6.QtGui import QPainter, QPen
        from PySide6.QtCore import QRect
        p = QPainter(self._canvas)
        pen = QPen(self._qcolor(color))
        pen.setWidth(width)
        p.setPen(pen)
        p.drawArc(QRect(x1, y1, x2 - x1, y2 - y1),
                  int(-angle0 * 16), int(-(angle1 - angle0) * 16))
        p.end()
        self._flush()

    #绘画圆弧
    '''
    x1,y1,x2,y2为定义边界框的两个点
    angle0为初始角度，三点钟方向为起始点，顺时针增加
    angle1为终止角度
    color为圆弧颜色，默认为白色
    width为圆弧宽度，默认为2
    '''
    def lcd_arc(self, x1, y1, x2, y2, angle0, angle1,
                color=(255, 255, 255), width=2):
        self.lcd_circle(x1, y1, x2, y2, angle0, angle1,
                        color=color, width=width)

    #绘画圆:  根据圆形点和半径画圆
    '''
    center_x, center_y 圆心点坐标
    radius 圆半径长度 mm
    
    '''
    def lcd_round(self, center_x, center_y, radius, color, width=2):
        x1 = center_x - radius
        y1 = center_y - radius
        x2 = center_x + radius
        y2 = center_y + radius
        self.lcd_circle(x1, y1, x2, y2, 0, 360, color=color, width=width)
  

    
    #绘画矩形
    '''
    x1,y1为初始点坐标,x2,y2为对角线终止点坐标
    '''
    def lcd_rectangle(self, x1, y1, x2, y2, fill=None,
                      outline="WHITE", width=2):
        from PySide6.QtGui import QPainter, QPen
        from PySide6.QtCore import QRect, Qt
        p = QPainter(self._canvas)
        if fill:
            p.fillRect(QRect(x1, y1, x2 - x1, y2 - y1),
                       self._qcolor(fill))
        pen = QPen(self._qcolor(outline))
        pen.setWidth(width)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRect(x1, y1, x2 - x1, y2 - y1))
        p.end()
        self._flush()
    #清除屏幕
    def lcd_clear(self):
        from PySide6.QtGui import QColor
        self._canvas.fill(QColor("black"))
        self._flush()
    #显示图片
    '''
    图片的大小为320*240,jpg格式
    '''
    def lcd_picture(self, filename, x=0, y=0):
        from PySide6.QtGui import QPainter
        from PySide6.QtGui import QPixmap as QP
        img_px = QP("/home/pi/xgoPictures/" + filename)
        p = QPainter(self._canvas)
        p.drawPixmap(x, y, img_px)
        p.end()
        self._flush()
    #显示文字
    '''
    x1,y1为初始点坐标,content为内容
    '''
    def lcd_text(self, x, y, content, color="WHITE", fontsize=15):
        from PySide6.QtGui import QPainter, QFont
        p = QPainter(self._canvas)
        p.setPen(self._qcolor(color))
        font = QFont(self._font_family)
        font.setPixelSize(fontsize)
        p.setFont(font)
        p.drawText(x, y + fontsize, str(content))
        p.end()
        self._flush()
    #流式显示所有文字
    '''
    x1,y1为初始点坐标,content为内容
    遇到回车符自动换行，遇到边缘换行，一页满了自动清屏，2,2开始继续显示
    '''
    def display_text_on_screen(self, content, color, start_x=2, start_y=2, font_size=20, screen_width=320, screen_height=240):
        # 计算每行可显示字符的数量和行数
        char_width = font_size +1  #// 2
        chars_per_line = screen_width // char_width
        lines = screen_height // char_width
    
        # 拆分内容为逐个字符的列表
        chars = list(content)
     
        # 处理换行符
        line_break_indices = [i for i, char in enumerate(chars) if char == '\n']
    
    
        # 计算总行数和页数
        total_lines = len(chars) // chars_per_line + 1
        total_pages = (total_lines - 1+len(line_break_indices)) // lines + 1
    
        # 清屏
        self._clear_display()
    
        # 逐行显示文字
        current_page = 1
        current_line = 1
        current_char = 0
    
        while current_page <= total_pages or  current_char < len(chars) :
            self._clear_display()
            # 计算当前页要显示的行数
            if current_page < total_pages or  current_char < len(chars) :
                lines_to_display = lines
            else:
                lines_to_display = (total_lines - 1) % lines + 1
    
            current_line = 1
            # 显示当前页的内容
            for line in range(lines_to_display):
                current_x = start_x
                current_y = start_y + current_line * char_width # font_size
                current_line +=1
                if current_line >= lines:
                    break
    
                # 显示当前行的文字
                for _ in range(chars_per_line):
                    # 检查是否所有字符都已显示完毕
                    if current_char >= len(chars):
                        break
    
                    char = chars[current_char]
                    if char == '\n':
                        current_x = start_x
                        current_y = start_y + current_line * char_width # font_size
                        current_line +=1
                       
                        self.lcd_text(current_x, current_y, char, color, font_size)
                        current_char += 1
                        break  # continue
    
                    self.lcd_text(current_x, current_y, char, color, font_size)
                    current_x += char_width
                    current_char += 1
    
                # 检查是否所有字符都已显示完毕
                if current_char >= len(chars):
                    break
    
            # 更新当前页和当前行
            current_page += 1
            current_line += lines_to_display
    
            # 等待显示时间或手动触发翻页
            # 这里可以根据需要添加适当的延时代码或触发翻页的机制
    
        # 如果内容超过一屏幕，则清屏
        # if total_lines > lines:
        if current_page < total_pages:
            self._clear_display()
    
    #key_value
    '''
    a左上按键 (KEY_LEFT,  GPIO17)
    b右上按键 (KEY_RIGHT, GPIO22)
    c左下按键 (KEY_BACK,  GPIO23)
    d右下按键 (KEY_ENTER, GPIO24)
    通过 gpio-keys 内核驱动读取 /dev/input/eventX
    返回值 False未按下, True按下
    '''
    def xgoButton(self, button):
        from evdev import ecodes
        if self._key_dev is None:
            return False
        try:
            while True:
                event = self._key_dev.read_one()
                if event is None:
                    break
                if event.type == ecodes.EV_KEY:
                    btn = self._key_map.get(event.code)
                    if btn is not None:
                        self._key_states[btn] = (event.value == 1)
        except Exception:
            pass
        return self._key_states.get(button, False)
    #speaker
    '''
    filename 文件名 字符串
    通过 aplay 非阻塞播放（ALSA dmix 混音）
    '''
    def xgoSpeaker(self,filename):
        path="/home/pi/xgoMusic/"
        subprocess.Popen(["aplay", path + filename],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    def xgoVideoAudio(self,filename):
        path="/home/pi/xgoVideos/"
        time.sleep(0.2)  #音画速度同步了 但是时间轴可能不同步 这里调试一下
        subprocess.Popen(["mplayer", path + filename, "-novideo"],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    def xgoVideo(self,filename):
        path="/home/pi/xgoVideos/"
        x=threading.Thread(target=self.xgoVideoAudio,args=(filename,))
        x.start()
        global counter
        video=cv2.VideoCapture(path+filename)
        print(path+filename)
        fps = video.get(cv2.CAP_PROP_FPS) 
        print(fps)
        init_time=time.time()
        counter=0
        try:
            while True:
                grabbed, dst = video.read()
                try:
                    b,g,r = cv2.split(dst)
                    dst = cv2.merge((r,g,b))
                except:
                    pass
                try:
                    imgok = Image.fromarray(dst)
                except:
                    break
                self._show_pil(imgok)
                #强制卡帧数 实测帧数不要超过20贞 否则显示跟不上 但是20贞转换经常有问题 所以建议直接15贞
                counter += 1
                ctime=time.time()- init_time
                if ctime != 0:
                    qtime=counter/fps-ctime
                    #print(qtime)
                    if qtime>0:
                        time.sleep(qtime)
                if not grabbed:
                    break
        finally:
            video.release()
        
    #audio_record
    '''
    filename 文件名 字符串
    seconds 录制时间S 字符串
    '''
    def xgoAudioRecord(self,filename="record",seconds=5):
        path="/home/pi/xgoMusic/"
        # 如果文件夹不存在则创建
        if not os.path.exists(path):
            os.makedirs(path)
        command1 = "arecord -d"
        command2 = "-f S32_LE -r 8000 -c 1 -t wav"
        cmd=command1+" "+str(seconds)+" "+command2+" "+path+filename
        print(cmd)
        subprocess.run(cmd, shell=True)

    def xgoCamera(self,switch):
        global camera_still
        if switch:
            self.open_camera()
            self.camera_still=True
            t = threading.Thread(target=self.camera_mode)  
            t.start() 
        else:
            self.camera_still=False
            time.sleep(0.5)
            splash = Image.new("RGB",(320,240),"black")
            self._show_pil(splash)

    def camera_mode(self):
        self.camera_still = True
        while self.camera_still:
            # 使用Picamera2捕获帧
            image = self.picam2.capture_array()
            # 转换颜色空间 BGR -> RGB
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            # 转换为PIL Image并显示
            imgok = Image.fromarray(image)
            self._show_pil(imgok)
            time.sleep(0.033)  # 约30fps
  #这里的seconds基本上相当于视频的两倍时长
    def xgoVideoRecord(self, filename="record", seconds=5):
        path = "/home/pi/xgoVideos/"
        # 如果文件夹不存在则创建
        if not os.path.exists(path):
            os.makedirs(path)
        self.camera_still = False
        time.sleep(0.6)
        
        if self.picam2 is None:
            self.open_camera()
        
        # 创建视频写入器
        FPS = 10
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        video_path = path + filename
        video_writer = cv2.VideoWriter(video_path, fourcc, FPS, (320, 240))
        
        start_time = time.time()
        while time.time() - start_time < seconds:
            print('recording...')
            # 捕获帧
            image = self.picam2.capture_array()
            # 写入视频
            video_writer.write(image)
            # 显示预览
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            imgok = Image.fromarray(image)
            self._show_pil(imgok)
        
        print('recording done')
        video_writer.release()

    def xgoTakePhoto(self, filename="photo"):
        path = "/home/pi/xgoPictures/"
        self.camera_still = False
        time.sleep(0.6)
        
        if self.picam2 is None:
            self.open_camera()
        
        # 使用Picamera2捕获图像
        image = self.picam2.capture_array()
        if image is None:
            print('xgoTakePhoto: capture failed, image is None')
            return
        # 镜像翻转（自拍模式）
        image = cv2.flip(image, 1)
        # 保存为JPEG
        cv2.imwrite(path + filename , image)
        
        # 显示预览
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        imgok = Image.fromarray(image)
        self._show_pil(imgok)
        print('photo writed!')
        time.sleep(0.7)

    def xgoTakePhotoHD(self, filename="photo", width=1920, height=1080):
        """
        高分辨率拍照 - 使用 Picamera2 进行高分辨率拍摄
        
        参数:
            filename: 文件名（不含扩展名和路径）
            width: 图片宽度，默认1920
            height: 图片高度，默认1080
        
        返回:
            str: 保存的照片完整路径，失败返回None
        """
        print(f'[xgoTakePhotoHD] 开始拍照: filename={filename}, size={width}x{height}')
        
        path = "/home/pi/xgoPictures/"
        # 如果文件夹不存在则创建
        if not os.path.exists(path):
            os.makedirs(path)
            print(f'[xgoTakePhotoHD] 创建目录: {path}')
        
        # 确保文件名有.jpg后缀
        if not filename.endswith('.jpg'):
            filename = filename + '.jpg'
        photo_path = path + filename
        
        # 在屏幕上显示拍照状态
        try:
            self.lcd_clear()
            self.lcd_text(80, 100, "正在拍照...", "YELLOW", 20)
        except:
            pass
        
        self.camera_still = False
        hd_picam = None
        
        try:
            # 先停止并关闭已有的 self.picam2（需要用不同分辨率重新配置）
            if self.picam2 is not None:
                print(f'[xgoTakePhotoHD] 释放已有摄像头...')
                try:
                    self.picam2.stop()
                    self.picam2.close()
                    print(f'[xgoTakePhotoHD] 摄像头释放成功')
                except Exception as e:
                    print(f'[xgoTakePhotoHD] 摄像头释放异常: {e}')
                finally:
                    self.picam2 = None
            
            time.sleep(0.5)
            
            # 创建新的 Picamera2 实例用于高分辨率拍摄
            print(f'[xgoTakePhotoHD] 创建高分辨率摄像头实例...')
            hd_picam = Picamera2()
            
            # 使用 still_configuration 配置高分辨率
            hd_config = hd_picam.create_still_configuration(
                main={"size": (width, height), "format": "RGB888"},
                transform=Transform(hflip=0, vflip=0)  # 不在配置中翻转，后面手动翻转
            )
            hd_picam.configure(hd_config)
            hd_picam.start()
            print(f'[xgoTakePhotoHD] 高分辨率摄像头已启动')
            
            # 等待摄像头稳定并丢弃前几帧
            print(f'[xgoTakePhotoHD] 预热摄像头...')
            time.sleep(1)
            for i in range(5):
                _ = hd_picam.capture_array()
                time.sleep(0.1)
            
            # 正式拍照
            print(f'[xgoTakePhotoHD] 正式拍照...')
            image = hd_picam.capture_array()
            
            if image is not None:
                print(f'[xgoTakePhotoHD] 拍照成功, 图像尺寸: {image.shape}')
                
                # Picamera2 使用 RGB888 格式，hflip=0 时原始图像已经是正确方向，无需翻转
                # 保存时需要转换为 BGR
                image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                cv2.imwrite(photo_path, image_bgr)
                print(f'[xgoTakePhotoHD] 照片已保存: {photo_path}')
                
                # 缩放后显示预览（image 已经是 RGB 格式）
                preview = cv2.resize(image, (320, 240))
                imgok = Image.fromarray(preview)
                self._show_pil(imgok)
                
                return photo_path
            else:
                print(f'[xgoTakePhotoHD] 拍照失败! image is None')
                try:
                    self.lcd_clear()
                    self.lcd_text(80, 100, "拍照失败", "RED", 20)
                except:
                    pass
                return None
                
        except Exception as e:
            print(f'[xgoTakePhotoHD] 异常: {e}')
            try:
                self.lcd_clear()
                self.lcd_text(80, 100, "拍照失败", "RED", 20)
            except:
                pass
            return None
            
        finally:
            # 确保高分辨率摄像头资源正确释放
            if hd_picam is not None:
                try:
                    hd_picam.stop()
                    hd_picam.close()
                    print(f'[xgoTakePhotoHD] 高分辨率摄像头已释放')
                except Exception as e:
                    print(f'[xgoTakePhotoHD] 释放高分辨率摄像头异常: {e}')
            
            # 将 self.picam2 设为 None，让后续方法按需重新初始化
            self.picam2 = None


    '''
    开启摄像头  A键拍照 B键录像 C键退出
    '''
    def camera(self, filename="camera"):
        import time
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        
        # 1. 初始化配置
        font = ImageFont.truetype("/home/pi/luwu-os/model/msyh.ttc", 20)
        video_fps = 15
        preview_size = (320, 240)
        photo_path = f"/home/pi/xgoPictures/{filename}.jpg"
        video_path = f"/home/pi/xgoVideos/{filename}.mp4"
        
        # 2. 确保之前相机已关闭
        def safe_camera_shutdown():
            if hasattr(self, 'picam2') and self.picam2 is not None:
                try:
                    if hasattr(self.picam2, '_preview'):
                        self.picam2.stop_preview()
                    self.picam2.stop()
                    self.picam2.close()
                except:
                    pass
                finally:
                    self.picam2 = None
        
        safe_camera_shutdown()
        time.sleep(1)  #

        try:
            from picamera2 import Picamera2
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(
                main={"size": preview_size, "format": "RGB888"},
                buffer_count=4)
            self.picam2.configure(config)
            self.picam2.start()
            time.sleep(2)  
            
    
            recording = False
            video_writer = None
            last_button_time = 0
            
            while True:
                current_time = time.time()
                
                try:
                   
                    frame = self.picam2.capture_array("main")
                    if frame is None or frame.size == 0:
                        continue
                        
                    # 6. 标准化图像
                    frame = frame.astype(np.uint8)
                    if len(frame.shape) == 2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
                    else:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    
                 
                    img = Image.fromarray(frame)
                    draw = ImageDraw.Draw(img)
                    status = "录像中" if recording else "就绪"
                    draw.text((5, 5), f"A:拍照 B:{'停止' if recording else '开始'} C:退出 | {status}", 
                             fill=(255,255,0), font=font)
                    self._show_pil(img)
                    
    
                    if recording and video_writer is not None:
                        video_writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                    

                    if current_time - last_button_time > 0.1:
                        if XGOEDU.xgoButton(self, "a"):  # 拍照
                            cv2.imwrite(photo_path, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                            # 显示拍照反馈
                            feedback = Image.new("RGB", preview_size, (0,0,0))
                            draw = ImageDraw.Draw(feedback)
                            draw.text((50, 100), "照片已保存!", fill=(0,255,0), font=font)
                            self._show_pil(feedback)
                            time.sleep(1)
                            last_button_time = current_time
                            
                        elif XGOEDU.xgoButton(self, "b"):  # 录像控制
                            recording = not recording
                            if recording:
                                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                                video_writer = cv2.VideoWriter(video_path, fourcc, video_fps, preview_size)
                            elif video_writer is not None:
                                video_writer.release()
                                video_writer = None
                                # 显示录像反馈
                                feedback = Image.new("RGB", preview_size, (0,0,0))
                                draw = ImageDraw.Draw(feedback)
                                draw.text((40, 100), "视频已保存!", fill=(0,255,0), font=font)
                                self._show_pil(feedback)
                                time.sleep(1)
                            last_button_time = current_time
                            
                        elif XGOEDU.xgoButton(self, "c"):  
                            break
                            
                except Exception as e:
                    print(f"帧处理异常: {str(e)}")
                    time.sleep(0.1)
                    
        except Exception as e:
            print(f"相机初始化失败: {str(e)}")
        finally:
            # 10. 安全释放资源
            try:
                if video_writer is not None:
                    video_writer.release()
            except:
                pass
                
            safe_camera_shutdown()
            XGOEDU.lcd_clear(self)
            print("相机应用已安全退出")
    '''
    骨骼识别
    '''
    def posenetRecognition(self, target="camera"):
        '''骨骼识别 - 使用 cv2.dnn + MediaPipe ONNX 模型替代 mediapipe Python API'''
        import sys
        sys.path.insert(0, '/home/pi/luwu-os/model')
        try:
            from mp_persondet import MPPersonDet
            from mp_pose import MPPose
        except ImportError as e:
            print(f'[posenetRecognition] 缺少辅助脚本: {e}')
            return None

        if not hasattr(self, '_person_det') or self._person_det is None:
            pdet_model = '/home/pi/luwu-os/model/person_detection_mediapipe_2023mar.onnx'
            pose_model = '/home/pi/luwu-os/model/pose_estimation_mediapipe_2023mar.onnx'
            if not os.path.exists(pdet_model) or not os.path.exists(pose_model):
                print('[posenetRecognition] 缺少模型文件，请确认 /home/pi/luwu-os/model/ 目录')
                return None
            self._person_det = MPPersonDet(pdet_model, scoreThreshold=0.5)
            self._pose_est   = MPPose(pose_model,     confThreshold=0.5)

        # 图像采集
        if target == "camera":
            self.open_camera()
            image = self.picam2.capture_array()
            if image is None:
                print('posenetRecognition: capture failed, image is None')
                return None
            image_bgr = image  # picam2 输出 BGR
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            image_rgb = np.array(Image.open(target).convert('RGB'))
            image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)

        # 检测人体
        persons = self._person_det.infer(image_bgr)
        if persons is None or len(persons) == 0:
            self._show_pil(Image.fromarray(image_rgb))
            return None

        result = self._pose_est.infer(image_bgr, persons[0])
        if result is None:
            self._show_pil(Image.fromarray(image_rgb))
            return None

        # result: [bbox(2,2), landmarks(39,5), world_lm(39,3), mask, heatmap, conf]
        landmarks = result[1]  # shape (39, 5): x, y, z, visibility, presence

        # 绘制关键点
        for i in range(33):
            x, y = int(landmarks[i, 0]), int(landmarks[i, 1])
            vis = landmarks[i, 3]
            if vis > 0.5 and 0 <= x < image_rgb.shape[1] and 0 <= y < image_rgb.shape[0]:
                cv2.circle(image_rgb, (x, y), 3, (255, 255, 255), -1)

        # 计算关节角度（与原 mediapipe 版本相同的关节组合）
        joint_list = [[24, 26, 28], [23, 25, 27], [14, 12, 24], [13, 11, 23]]
        h_img, w_img = image_rgb.shape[:2]
        angellist = []
        for joint in joint_list:
            a = np.array([landmarks[joint[0], 0] / w_img, landmarks[joint[0], 1] / h_img])
            b = np.array([landmarks[joint[1], 0] / w_img, landmarks[joint[1], 1] / h_img])
            c = np.array([landmarks[joint[2], 0] / w_img, landmarks[joint[2], 1] / h_img])
            radians = np.arctan2(c[1]-b[1], c[0]-b[0]) - np.arctan2(a[1]-b[1], a[0]-b[0])
            angle = np.abs(radians * 180.0 / np.pi)
            angellist.append(angle if angle <= 180 else 360 - angle)

        image_rgb = cv2.flip(image_rgb, 1)
        if angellist:
            ges = '|'.join(str(int(a)) for a in angellist[:4])
            cv2.putText(image_rgb, ges, (10, 220),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2, cv2.LINE_AA)

        self._show_pil(Image.fromarray(image_rgb))
        return angellist if angellist else None
    '''
    手势识别
    '''
    def gestureRecognition(self, target="camera"):
        ges = ''
        center = (0, 0)
        if self.hand is None:
            self.hand = hands(1, 2, 0.7, 0.7)

        if target == "camera":
            self.open_camera()
            time.sleep(0.3)
            image_bgr = self.picam2.capture_array()
            if image_bgr is None:
                return None
        else:
            path = "/home/pi/xgoPictures/" if not target.startswith('/') else ""
            image_bgr = cv2.imread(path + target)
            if image_bgr is None:
                return None

        # 水平镜像
        image_bgr = cv2.flip(image_bgr, 1)

        # 单次 ONNX 推理（hands.run 需要 BGR 输入）
        datas = self.hand.run(image_bgr)

        # 转为 RGB 用于显示
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

        if datas:
            for data in datas:
                pts = data['dlandmark']
                center = data['center']
                # 绘制手部骨架（21个关键点 + 连接线）
                draw_hand_landmarks(image_rgb, pts, color=(0, 255, 0), thickness=2)
                # 手势识别
                g = hand_pos(data['hand_angle'])
                if g:
                    ges = g

        # 绘制手势名称（与 main.py 一致的显示风格）
        if ges:
            cv2.putText(image_rgb, ges, (10, 40),
                        cv2.FONT_HERSHEY_COMPLEX, 1.2, (0, 255, 0), 2)

        imgok = Image.fromarray(image_rgb)
        self._show_pil(imgok)

        return (ges, center) if ges else None
    '''
    yolo
    '''
    def yoloFast(self,target="camera"):
        ret=''
        self.open_camera()
        if self.yolo==None:
            self.yolo = yoloXgo('/home/pi/luwu-os/model/yolo_coco.onnx',
            ['person','bicycle','car','motorbike','aeroplane','bus','train','truck','boat','traffic light','fire hydrant','stop sign','parking meter','bench','bird','cat','dog','horse','sheep','cow','elephant','bear','zebra','giraffe','backpack','umbrella','handbag','tie','suitcase','frisbee','skis','snowboard','sports ball','kite','baseball bat','baseball glove','skateboard','surfboard','tennis racket','bottle','wine glass','cup','fork','knife','spoon','bowl','banana','apple','sandwich','orange','broccoli','carrot','hot dog','pizza','donut','cake','chair','sofa','pottedplant','bed','diningtable','toilet','tvmonitor','laptop','mouse','remote','keyboard','cell phone','microwave','oven','toaster','sink','refrigerator','book','clock','vase','scissors','teddy bear','hair drier','toothbrush'],
            [352,352],0.66)
        if target=="camera":
            self.open_camera()
            image = self.picam2.capture_array()
            if image is None:
                print("摄像头读取帧失败")
                return None
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)  # 如果需要RGB格式
        else:
            image=np.array(Image.open(target))
        datas = self.yolo.run(image)
        b,g,r = cv2.split(image)
        image = cv2.merge((r,g,b))
        image = cv2.flip(image,1)
        if datas:
            for data in datas:
                XGOEDU.rectangle(self,image,data['xywh'],"#33cc00",2)
                xy= (data['xywh'][0], data['xywh'][1])
                XGOEDU.text(self,image,data['classes'],xy,1,"#ff0000",2)
                value_yolo = data['classes']
                ret=(value_yolo,xy)
        imgok = Image.fromarray(image)
        self._show_pil(imgok)
        if ret=='':
            return None
        else:
            return ret

    '''
    人脸坐标点检测
    '''
    def face_detect(self,target="camera"):
        ret=''
        if self.face==None:
            self.face = face_detection(0.7)
        if target=="camera":
            self.open_camera()
            image = self.picam2.capture_array()
            if image is None:
                print("摄像头读取帧失败")
                return None
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)  # 如果需要RGB格式    
        else:
            image=np.array(Image.open(target))
        image = cv2.flip(image,1)
        datas = self.face.run(image)
        for data in datas:
            lefteye = str(data['left_eye'])
            righteye = str(data['right_eye'])
            nose = str(data['nose'])
            mouth = str(data['mouth'])
            leftear = str(data['left_ear'])
            rightear = str(data['right_ear'])
            cv2.putText(image,'lefteye',(10,30),cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,0,0),2)
            cv2.putText(image,lefteye,(100,30),cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,0,0),2)
            cv2.putText(image,'righteye',(10,50),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,255,0),2)
            cv2.putText(image,righteye,(100,50),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,255,0),2)
            cv2.putText(image,'nose',(10,70),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,0,255),2)
            cv2.putText(image,nose,(100,70),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,0,255),2)
            cv2.putText(image,'leftear',(10,90),cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,255,0),2)
            cv2.putText(image,leftear,(100,90),cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,255,0),2)
            cv2.putText(image,'rightear',(10,110),cv2.FONT_HERSHEY_SIMPLEX,0.7,(200,0,200),2)
            cv2.putText(image,rightear,(100,110),cv2.FONT_HERSHEY_SIMPLEX,0.7,(200,0,200),2)
            XGOEDU.rectangle(self,image,data['rect'],"#33cc00",2)
            ret=data['rect']
        imgok = Image.fromarray(image)
        self._show_pil(imgok)
        if ret=='':
            return None
        else:
            return ret

    '''
    情绪识别
    '''
    def emotion(self, target="camera"):
        '''情绪识别 - 使用 onnxruntime + FER+ 替代 TensorFlow/Keras'''
        import onnxruntime as ort
        EMOTION_MODEL = '/home/pi/luwu-os/model/emotion-ferplus-8.onnx'
        # FER+ 输出 8 个情绪类别
        FERPLUS_LABELS = ['Neutral', 'Happiness', 'Surprise', 'Sadness',
                          'Anger', 'Disgust', 'Fear', 'Contempt']
        # 映射到原五类别接口
        LABEL_MAP = {
            'Neutral': 'Neutral', 'Happiness': 'Happy',  'Surprise': 'Surprise',
            'Sadness': 'Sad',     'Anger': 'Angry',       'Disgust': 'Angry',
            'Fear': 'Neutral',    'Contempt': 'Neutral',
        }
        ret = ''
        if self.classifier is None:
            if not os.path.exists(EMOTION_MODEL):
                print(f'[emotion] 缺少情绪模型文件: {EMOTION_MODEL}')
                return None
            # 查找 haar cascade 文件
            haar_candidates = [
                '/home/pi/luwu-os/model/haarcascade_frontalface_default.xml',
                '/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml',
                cv2.data.haarcascades + 'haarcascade_frontalface_default.xml',
            ]
            haar_file = next((p for p in haar_candidates if os.path.exists(p)), None)
            if haar_file is None:
                print('[emotion] 找不到 haarcascade_frontalface_default.xml')
                return None
            self.face_classifier = cv2.CascadeClassifier(haar_file)
            self.classifier = ort.InferenceSession(EMOTION_MODEL)
            self._emo_input = self.classifier.get_inputs()[0].name

        if target == "camera":
            self.open_camera()
            image = self.picam2.capture_array()
            if image is None:
                print("摄像头读取帧失败")
                return None
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            image = np.array(Image.open(target))

        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        faces = self.face_classifier.detectMultiScale(gray, 1.3, 5)
        label = ''

        for (x, y, w, h) in faces:
            cv2.rectangle(image, (x, y), (x+w, y+h), (255, 0, 0), 2)
            roi_gray = gray[y:y+h, x:x+w]
            roi_gray = cv2.resize(roi_gray, (64, 64), interpolation=cv2.INTER_AREA)

            if np.sum([roi_gray]) != 0:
                # FER+ 期望输入：[1, 1, 64, 64] float32
                roi_f = roi_gray.astype(np.float32) / 255.0
                roi_f = roi_f[np.newaxis, np.newaxis, :, :]
                preds = self.classifier.run(None, {self._emo_input: roi_f})[0][0]
                ferplus_label = FERPLUS_LABELS[int(np.argmax(preds))]
                label = LABEL_MAP.get(ferplus_label, 'Neutral')
                label_position = (x, y)
                ret = (label, (x, y))
            else:
                label = 'No Face Found'

            try:
                cv2.putText(image, label, label_position,
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
            except:
                pass

        image = cv2.flip(image, 1)
        imgok = Image.fromarray(image)
        self._show_pil(imgok)

        if ret == '':
            return None
        else:
            return ret
    '''
    年纪及性别检测 - 使用 onnxruntime + gender_age.onnx + YuNet 人脸检测
    '''
    def agesex(self, target="camera"):
        import onnxruntime as ort
        AGESEX_MODEL = '/home/pi/luwu-os/model/gender_age.onnx'
        ageList = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
        genderList = ['Male', 'Female']
        padding = 20
        ret = ''

        if self.agesexmark is None:
            if not os.path.exists(AGESEX_MODEL):
                print(f'[agesex] 缺少模型文件: {AGESEX_MODEL}')
                return None
            self.face_detector = face_detection(min_detection_confidence=0.7)
            self.agesex_session = ort.InferenceSession(AGESEX_MODEL)
            self._agesex_input = self.agesex_session.get_inputs()[0].name
            self.agesexmark = True

        if target == "camera":
            self.open_camera()
            image = self.picam2.capture_array()
            if image is None:
                print("摄像头读取帧失败")
                return None
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            image = np.array(Image.open(target))

        # YuNet 人脸检测 (需要 BGR 输入)
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        faces = self.face_detector.run(image_bgr)
        image_disp = image_bgr.copy()

        gender = ''
        age = ''

        for face in faces:
            x, y, w, h = face['rect']
            # 裁剪人脸区域（带 padding）
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = min(image.shape[1], x + w + padding)
            y2 = min(image.shape[0], y + h + padding)
            face_img = image[y1:y2, x1:x2]

            # 预处理: resize 到 62x62, 归一化 [0,1]
            face_input = cv2.resize(face_img, (62, 62))
            face_input = face_input.astype(np.float32) / 255.0
            face_input = face_input[np.newaxis, :, :, :]  # [1, 62, 62, 3]

            outputs = self.agesex_session.run(None, {self._agesex_input: face_input})
            gender_preds = outputs[0]   # [1, 1, 1, 2]
            age_pred = outputs[1]        # [1, 1, 1, 1]

            gender_idx = int(np.argmax(gender_preds[0][0][0]))
            gender = genderList[gender_idx]

            age_val = float(age_pred[0][0][0][0]) * 100  # 模型输出归一化值(0~1)，乘以100得到实际年龄
            # 将年龄回归值映射到最近的年龄段
            age_centers = [1, 5, 10, 17.5, 28.5, 40.5, 50.5, 80]
            closest_idx = min(range(len(age_centers)), key=lambda i: abs(age_centers[i] - age_val))
            age = ageList[closest_idx]

            label = "{},{}".format(gender, age)
            cv2.putText(image_disp, label, (x, y - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.rectangle(image_disp, (x, y), (x + w, y + h), (0, 255, 0), 2)
            ret = (gender, age, (x, y))

        image_disp = cv2.flip(image_disp, 1)
        imgok = Image.fromarray(cv2.cvtColor(image_disp, cv2.COLOR_BGR2RGB))
        self._show_pil(imgok)

        if ret == '':
            return None
        else:
            return ret

    
    def rectangle(self,frame,z,colors,size):
        frame=cv2.rectangle(frame,(int(z[0]),int(z[1])),(int(z[0]+z[2]),int(z[1]+z[3])),color(colors),size)
        return frame
        
    def circle(self,frame,xy,rad,colors,tk):
        frame=cv2.circle(frame,xy,rad,color(colors),tk)
        return frame
    
    def text(self,frame,text,xy,font_size,colors,size):
        frame=cv2.putText(frame,text,xy,cv2.FONT_HERSHEY_SIMPLEX,font_size,color(colors),size)
        return frame   

    def SpeechRecognition(self,seconds=3):
        self.xgoAudioRecord(filename="recog.wav",seconds=seconds)
        from urllib.request import urlopen
        from urllib.request import Request
        from urllib.error import URLError
        from urllib.parse import urlencode
        timer = time.perf_counter
        AUDIO_FILE = 'recog.wav' 
        FORMAT = AUDIO_FILE[-3:]  
        CUID = '123456PYTHON'
        RATE = 16000
        DEV_PID = 1537  
        ASR_URL = 'http://vop.baidu.com/server_api'
        SCOPE = 'audio_voice_assistant_get' 

        token = self.fetch_token()

        speech_data = []
        path="/home/pi/xgoMusic/"
        with open(path+AUDIO_FILE, 'rb') as speech_file:
            speech_data = speech_file.read()

        length = len(speech_data)
        if length == 0:
            raise DemoError('file %s length read 0 bytes' % AUDIO_FILE)
        speech = base64.b64encode(speech_data)
        speech = str(speech, 'utf-8')
        params = {'dev_pid': DEV_PID,
                'format': FORMAT,
                'rate': RATE,
                'token': token,
                'cuid': CUID,
                'channel': 1,
                'speech': speech,
                'len': length
                }
        post_data = json.dumps(params, sort_keys=False)
        req = Request(ASR_URL, post_data.encode('utf-8'))
        req.add_header('Content-Type', 'application/json')
        try:
            begin = timer()
            f = urlopen(req)
            result_str = f.read()
            print ("Request time cost %f" % (timer() - begin))
        except URLError as err:
            print('asr http response http code : ' + str(err.code))
            result_str = err.read()
        try:
            result_str = str(result_str, 'utf-8')
            re=json.loads(result_str)
            text=re['result'][0]
        except:
            text='error!'
        return text

    def SpeechSynthesis(self,texts):
        from urllib.request import urlopen
        from urllib.request import Request
        from urllib.error import URLError
        from urllib.parse import urlencode
        from urllib.parse import quote_plus

        TEXT = texts
        PER = 0
        SPD = 5
        PIT = 5
        VOL = 5
        AUE = 6
        FORMATS = {3: "mp3", 4: "pcm", 5: "pcm", 6: "wav"}
        FORMAT = FORMATS[AUE]
        CUID = "123456PYTHON"
        TTS_URL = 'http://tsn.baidu.com/text2audio'

        SCOPE = 'audio_tts_post' 

        token = self.fetch_token()
        tex = quote_plus(TEXT) 
        print(tex)
        params = {'tok': token, 'tex': tex, 'per': PER, 'spd': SPD, 'pit': PIT, 'vol': VOL, 'aue': AUE, 'cuid': CUID,
                'lan': 'zh', 'ctp': 1}  

        data = urlencode(params)
        print('test on Web Browser' + TTS_URL + '?' + data)

        req = Request(TTS_URL, data.encode('utf-8'))
        has_error = False
        try:
            f = urlopen(req)
            result_str = f.read()

            headers = dict((name.lower(), value) for name, value in f.headers.items())

            has_error = ('content-type' not in headers.keys() or headers['content-type'].find('audio/') < 0)
        except  URLError as err:
            print('asr http response http code : ' + str(err.code))
            result_str = err.read()
            has_error = True

        path="/home/pi/xgoMusic/"
        save_file = "error.txt" if has_error else 'result.' + FORMAT
        with open(path+save_file, 'wb') as of:
            of.write(result_str)

        if has_error:
            result_str = str(result_str, 'utf-8')
            print("tts api  error:" + result_str)

        print("result saved as :" + save_file)

        self.xgoSpeaker("result.wav")

    def cv2AddChineseText(self,img, text, position, textColor=(0, 255, 0), textSize=30):
        if (isinstance(img, np.ndarray)):  
            img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img)
        fontStyle = ImageFont.truetype(
            "/home/pi/luwu-os/model/msyh.ttc", textSize, encoding="utf-8")
        draw.text(position, text, textColor, font=fontStyle)
        return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    
    def AprilTagRecognition(self, target="camera"):
        """
        AprilTag码识别（使用OpenCV aruco模块）
        返回: 识别到的第一个Tag ID，没有则返回None
        """
        if target == "camera":
            self.open_camera()
            image = self.picam2.capture_array()
            if image is None:
                print("摄像头读取帧失败")
                return None
            # open_camera 使用 hflip=1，AprilTag 需要原始方向，翻转回来
            image = cv2.flip(image, 1)
        else:
            path = "/home/pi/xgoPictures/"
            image = np.array(Image.open(path + target).convert('RGB'))
        
        # 转为灰度图
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        
        # 支持多种AprilTag字典
        apriltag_dicts = [
            cv2.aruco.DICT_APRILTAG_36h11,
            cv2.aruco.DICT_APRILTAG_25h9,
            cv2.aruco.DICT_APRILTAG_16h5,
            cv2.aruco.DICT_APRILTAG_36h10
        ]
        
        # 创建检测参数
        parameters = cv2.aruco.DetectorParameters()
        
        result = None
        for dict_type in apriltag_dicts:
            aruco_dict = cv2.aruco.getPredefinedDictionary(dict_type)
            corners, ids, rejected = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=parameters)
            
            if ids is not None:
                for i, corner in enumerate(corners):
                    tag_id = ids[i][0]
                    pts = corner[0].astype(int)
                    
                    # 绘制边框
                    for j in range(4):
                        pt1 = tuple(pts[j])
                        pt2 = tuple(pts[(j + 1) % 4])
                        cv2.line(image, pt1, pt2, (0, 255, 0), 2)
                    
                    # 绘制中心点
                    center = pts.mean(axis=0).astype(int)
                    cv2.circle(image, tuple(center), 5, (255, 0, 0), -1)
                    cv2.putText(image, f"ID:{tag_id}", (center[0] - 20, center[1] - 20),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
                    
                    if result is None:
                        result = tag_id
                break  # 找到就退出
        
        # 显示（cv2.flip 已恢复原始方向）
        imgok = Image.fromarray(image)
        self._show_pil(imgok)
        
        return result

    def AprilTagDetection(self, marker_length=5, target="camera"):
        """
        AprilTag位姿检测（使用OpenCV aruco模块 + solvePnP）
        
        Args:
            marker_length: 标记实际边长，单位：厘米
            target: "camera" 使用摄像头实时采集，否则从文件加载
        
        Returns:
            dict: 检测到时返回位姿信息字典，未检测到返回 None
        """
        try:
            from .camera_calibration import load_calibration
        except ImportError:
            from camera_calibration import load_calibration

        if target == "camera":
            self.open_camera()
            image = self.picam2.capture_array()
            if image is None:
                return None
            # open_camera 使用 hflip=1，AprilTag 需要原始方向，翻转回来
            image = cv2.flip(image, 1)
        else:
            path = "/home/pi/xgoPictures/"
            image = np.array(Image.open(path + target).convert('RGB'))

        print(f'[AprilTagDetection] image shape: {image.shape}, dtype: {image.dtype}')

        # image 已是正确显示方向（软件 flip 过），直接用于检测和显示
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

        # AprilTag 字典及其名称映射
        apriltag_dicts = [
            (cv2.aruco.DICT_APRILTAG_36h11, "tag36h11"),
            (cv2.aruco.DICT_APRILTAG_25h9,  "tag25h9"),
            (cv2.aruco.DICT_APRILTAG_16h5,  "tag16h5"),
            (cv2.aruco.DICT_APRILTAG_36h10, "tag36h10"),
        ]

        parameters = cv2.aruco.DetectorParameters()

        # 加载标定参数
        camera_matrix, dist_coeffs = load_calibration()

        # marker_length 从厘米转为米
        marker_length_meters = marker_length / 100.0

        result = None
        for dict_type, family_name in apriltag_dicts:
            aruco_dict = cv2.aruco.getPredefinedDictionary(dict_type)
            corners, ids, rejected = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=parameters)

            if ids is not None:
                for i, corner in enumerate(corners):
                    tag_id = int(ids[i][0])
                    pts = corner[0].astype(int)

                    # 绘制边框
                    for j in range(4):
                        pt1 = tuple(pts[j])
                        pt2 = tuple(pts[(j + 1) % 4])
                        cv2.line(image, pt1, pt2, (0, 255, 0), 2)

                    # 绘制中心点
                    center = pts.mean(axis=0).astype(int)
                    cv2.circle(image, tuple(center), 5, (255, 0, 0), -1)

                    # 位姿估计：使用 solvePnP 替代已废弃的 estimatePoseSingleMarkers
                    half = marker_length_meters / 2.0
                    obj_points = np.array([
                        [-half,  half, 0],
                        [ half,  half, 0],
                        [ half, -half, 0],
                        [-half, -half, 0]
                    ], dtype=np.float32)
                    success, rvec, tvec = cv2.solvePnP(
                        obj_points, corner[0], camera_matrix, dist_coeffs
                    )
                    if not success:
                        continue
                    rvec = rvec.flatten()
                    tvec = tvec.flatten()

                    # 旋转向量 → 旋转矩阵 → 欧拉角
                    R, _ = cv2.Rodrigues(rvec)
                    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
                    singular = sy < 1e-6
                    if not singular:
                        x_rot = math.atan2(R[2, 1], R[2, 2])
                        y_rot = math.atan2(-R[2, 0], sy)
                        z_rot = math.atan2(R[1, 0], R[0, 0])
                    else:
                        x_rot = math.atan2(-R[1, 2], R[1, 1])
                        y_rot = math.atan2(-R[2, 0], sy)
                        z_rot = 0

                    # 弧度转角度
                    x_deg = round(x_rot * 180.0 / math.pi, 2)
                    y_deg = round(y_rot * 180.0 / math.pi, 2)
                    z_deg = round(z_rot * 180.0 / math.pi, 2)

                    # 米转厘米
                    x_cm = round(tvec[0] * 100.0, 2)
                    y_cm = round(tvec[1] * 100.0, 2)
                    z_cm = round(tvec[2] * 100.0, 2)

                    # 在图像上显示位姿信息
                    info_text = f"ID:{tag_id} X:{x_cm} Y:{y_cm} Z:{z_cm}cm"
                    cv2.putText(image, info_text,
                                (center[0] - 60, center[1] - 20),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)

                    if result is None:
                        result = {
                            "tag_id": tag_id,
                            "tag_family": family_name,
                            "x_translation": x_cm,
                            "y_translation": y_cm,
                            "z_translation": z_cm,
                            "x_rotation": x_deg,
                            "y_rotation": y_deg,
                            "z_rotation": z_deg,
                        }
                break  # 找到就退出

        # 显示（已是 320x240，无需缩放）
        imgok = Image.fromarray(image)
        self._show_pil(imgok)

        return result

    def QRRecognition(self, target="camera"):
        import pyzbar.pyzbar as pyzbar
        
        # 图像采集
        if target == "camera":
            self.open_camera()
            # 多帧重试提升识别率（单帧可能模糊/曝光不佳）
            barcodes = []
            image = None
            for _ in range(5):
                image = self.picam2.capture_array()
                if image is None:
                    continue
                # open_camera 使用 hflip=1，QR码需要原始方向才能正确解码
                image = cv2.flip(image, 1)
                # 灰度化提升 pyzbar 识别率
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
                barcodes = pyzbar.decode(gray)
                if barcodes:
                    break
            if image is None:
                print("摄像头读取帧失败")
                return None
        else:
            path = "/home/pi/xgoPictures/"
            image = np.array(Image.open(path + target))
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            barcodes = pyzbar.decode(gray)
        
        # 结果处理
        result = []
        for barcode in barcodes:
            barcodeData = barcode.data.decode("utf-8")
            barcodeType = barcode.type
            result.append(barcodeData)
            text = "{} ({})".format(barcodeData, barcodeType)
            image = self.cv2AddChineseText(image, text, (10, 30), (0, 255, 0), 30)
        
        # 显示处理（保持RGB格式）
        imgok = Image.fromarray(image)
        self._show_pil(imgok)
        
        return result if result else []

    def ColorRecognition(self, target="camera", mode='R'):
        color_x = 0
        color_y = 0
        color_radius = 0
        threshold = []
        # 颜色阈值设置
        if mode == 'R':  # red
            color_lower = np.array([170, 70, 70])
            color_upper = np.array([180, 255, 255])
            color_lower1 = np.array([0, 70, 70])
            color_upper2 = np.array([10, 255, 255])
            threshold = [[color_lower, color_upper], [color_lower1, color_upper2]]
        elif mode == 'G':  # green
            color_lower = np.array([40, 70, 70])
            color_upper = np.array([85, 255, 255])
            threshold = [[color_lower, color_upper]]
        elif mode == 'B':  # blue
            color_lower = np.array([90, 100, 100])
            color_upper = np.array([124, 255, 255])
            threshold = [[color_lower, color_upper]]
        elif mode == 'Y':  # yellow
            color_lower = np.array([26, 100, 100])
            color_upper = np.array([34, 255, 255])
            threshold = [[color_lower, color_upper]]
    
        # 图像采集（统一使用frame变量）
        if target == "camera":
            self.open_camera()
            frame = self.picam2.capture_array()
            if frame is None:
                print("摄像头读取帧失败")
                return None
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # 转换为RGB
        else:
            path = "/home/pi/xgoPictures/"
            frame = np.array(Image.open(path + target).convert('RGB'))
    
        # 图像处理
        # frame_ = cv2.GaussianBlur(frame, (5,5), 0)
        hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)  # 注意是RGB2HSV
        mask = None
        for th in threshold:
            if mask is None:
                mask = cv2.inRange(hsv, th[0], th[1])
            else:
                mask = cv2.bitwise_or(mask, cv2.inRange(hsv, th[0], th[1]))
            # mask = cv2.inRange(hsv, th[0], th[1])
        # mask = cv2.inRange(hsv, color_lower, color_upper)
        mask = cv2.erode(mask, None, iterations=2)
        mask = cv2.dilate(mask, None, iterations=2)
        mask = cv2.GaussianBlur(mask, (3,3), 0)
        cnts = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        
        # 目标检测
        if len(cnts) > 0:
            cnt = max(cnts, key=cv2.contourArea)
            (color_x, color_y), color_radius = cv2.minEnclosingCircle(cnt)
            cv2.circle(frame, (int(color_x), int(color_y)), int(color_radius), (255,0,255), 2)
        
        # 显示坐标
        cv2.putText(frame, f"X:{int(color_x)}, Y:{int(color_y)}", 
                   (40,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,0), 2)
    
        # 显示处理（直接使用RGB格式）
        imgok = Image.fromarray(frame)
        self._show_pil(imgok)
    
        return ((color_x, color_y), color_radius)

    def ColorBlockDetect(self, target_x=160, target_y=120, color_ranges=[], min_radius=10, target="camera"):
        """
        色块识别函数 - 识别指定颜色范围的色块，优先返回距离目标坐标最近的色块

        参数:
            target_x: 目标x坐标 (0-320，默认160为画面中心)
            target_y: 目标y坐标 (0-240，默认120为画面中心)
            color_ranges: 色域范围列表，格式为 [[H_min, H_max, S_min, S_max, V_min, V_max], ...]
            min_radius: 目标最小半径
            target: 图像来源

        返回:
            [与x坐标偏差, 与y坐标偏差, 色块半径]
            未检测到返回 [0, 0, 0]
        """
        # 第一步：图像采集
        if target == "camera":
            self.open_camera()
            frame = self.picam2.capture_array()
            if frame is None:
                print("摄像头读取帧失败")
                return None
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            path = "/home/pi/xgoPictures/"
            frame = np.array(Image.open(path + target).convert('RGB'))

        # 第二步：转换到 HSV 颜色空间
        hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)

        # 第三步：根据色域范围列表生成掩码
        mask = None
        for cr in color_ranges:
            if len(cr) == 6:
                # 格式: [H_min, H_max, S_min, S_max, V_min, V_max]
                lower = np.array([cr[0], cr[2], cr[4]])  # [H_min, S_min, V_min]
                upper = np.array([cr[1], cr[3], cr[5]])  # [H_max, S_max, V_max]
            elif len(cr) == 2:
                # 兼容 [[lower], [upper]] 格式
                lower = np.array(cr[0])
                upper = np.array(cr[1])
            else:
                continue

            current_mask = cv2.inRange(hsv, lower, upper)
            if mask is None:
                mask = current_mask
            else:
                mask = cv2.bitwise_or(mask, current_mask)

        # 如果没有有效的颜色范围，返回默认值
        if mask is None:
            imgok = Image.fromarray(frame)
            self._show_pil(imgok)
            return [0, 0, 0]

        # 第四步：形态学处理
        mask = cv2.erode(mask, None, iterations=2)
        mask = cv2.dilate(mask, None, iterations=2)
        mask = cv2.GaussianBlur(mask, (3, 3), 0)

        # 第五步：轮廓检测
        cnts = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

        # 第六步：筛选并找到最近的色块
        best_block = None
        best_distance = float('inf')
        box = None

        for cnt in cnts:
            # (cx, cy), radius = cv2.minEnclosingCircle(cnt)
            # # 过滤半径小于最小半径的色块
            # if radius < min_radius:
            #     continue
            if len(cnt) < min_radius:  # 点太少无法拟合圆
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            cx = x + w / 2
            cy = y + h / 2
            # 计算与目标坐标的距离
            distance = math.sqrt((cx - target_x) ** 2 + (cy - target_y) ** 2)
            if distance < best_distance:
                best_distance = distance
                best_block = (cx, cy, len(cnt))
                box = (x, y, w, h)

        # 第七步：绘制结果并显示
        if best_block is not None:
            bx, by, br = best_block
            # 画目标点（十字标记）
            cv2.drawMarker(frame, (int(target_x), int(target_y)), (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
            # 画检测到的色块圆
            # cv2.circle(frame, (int(bx), int(by)), int(br), (255, 0, 255), 2)
            cv2.rectangle(frame, (box[0], box[1]), (box[0]+box[2], box[1]+box[3]), (255, 0, 255), 2)
            # 画色块中心
            cv2.circle(frame, (int(bx), int(by)), 3, (255, 0, 255), -1)
            # 画目标点到色块的连线
            cv2.line(frame, (int(target_x), int(target_y)), (int(bx), int(by)), (255, 255, 0), 1)
            # 显示偏差信息
            offset_x = int(bx - target_x)
            offset_y = int(target_y - by)
            cv2.putText(frame, f"dX:{offset_x} dY:{offset_y} R:{int(br)}",
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

            result = [offset_x, offset_y, int(br)]
        else:
            # 未检测到，画目标点
            cv2.drawMarker(frame, (int(target_x), int(target_y)), (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
            cv2.putText(frame, "No block detected", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            result = [0, 0, 0]

        # 显示结果
        imgok = Image.fromarray(frame)
        self._show_pil(imgok)

        return result

    def LineRecognition(self, target="camera", mode='K'):
        """
        巡线识别函数 - 文章五步法版本
        
        核心算法：灰度化 → 动态二值化 → 开运算去噪 → 轮廓提取 → 逐列扫描+多项式拟合
        
        参数:
            target: 图像来源，"camera"表示摄像头，其他为图片文件名
            mode: 颜色模式，'K'(黑色), 'W'(白色), 'R'(红), 'G'(绿), 'B'(蓝), 'Y'(黄)
        
        返回:
            {
                'x': 线的x坐标 (0-320, 160为中心; -1表示未检测到),
                'angle': 线的方向角度(度数，-90到90，0表示竖直，正值向右倾斜)
            }
        """
        SCREEN_WIDTH = 320
        SCREEN_HEIGHT = 240
        
        # ========== 第零步：图像采集 ==========
        if target == "camera":
            self.open_camera()
            frame = self.picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            path = "/home/pi/xgoPictures/"
            frame = np.array(Image.open(path + target).convert('RGB'))
        
        orig_height, orig_width = frame.shape[:2]
        result = {'x': -1, 'angle': 0}
        
        # ROI区域：底部80-120行（文章推荐），这里取底部1/3
        roi_top = int(orig_height * 2 / 3)
        roi = frame[roi_top:, :]
        roi_height, roi_width = roi.shape[:2]
        
        # ========== 第一步：灰度化 ==========
        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        
        # ========== 第二步：二值化（动态阈值） ==========
        # 使用自适应阈值，适应光照变化
        gray_blur = cv2.GaussianBlur(gray, (5, 5), 0)
        
        if mode == 'K':  # 黑线
            # 自适应阈值：局部区域均值法
            binary = cv2.adaptiveThreshold(
                gray_blur, 255, 
                cv2.ADAPTIVE_THRESH_MEAN_C,  # 局部均值
                cv2.THRESH_BINARY_INV,       # 黑线变白
                blockSize=25,                # 局部区域大小
                C=10                         # 常数偏移
            )
        elif mode == 'W':  # 白线
            binary = cv2.adaptiveThreshold(
                gray_blur, 255,
                cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY,
                blockSize=25, C=10
            )
        else:
            # 彩色线使用HSV
            hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
            if mode == 'R':
                mask1 = cv2.inRange(hsv, np.array([0, 100, 50]), np.array([10, 255, 255]))
                mask2 = cv2.inRange(hsv, np.array([170, 100, 50]), np.array([180, 255, 255]))
                binary = cv2.bitwise_or(mask1, mask2)
            elif mode == 'G':
                binary = cv2.inRange(hsv, np.array([35, 100, 50]), np.array([85, 255, 255]))
            elif mode == 'B':
                binary = cv2.inRange(hsv, np.array([100, 100, 50]), np.array([130, 255, 255]))
            elif mode == 'Y':
                binary = cv2.inRange(hsv, np.array([20, 100, 50]), np.array([35, 255, 255]))
            else:
                binary = cv2.adaptiveThreshold(
                    gray_blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                    cv2.THRESH_BINARY_INV, 25, 10
                )
        
        # ========== 第三步：形态学处理（开运算去噪） ==========
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)  # 先腐蚀后膨胀，去噪
        
        # ========== 第四步：轮廓提取（筛选最大轮廓） ==========
        cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # 筛选面积最大的轮廓
        valid_cnts = [c for c in cnts if cv2.contourArea(c) > 100]
        if not valid_cnts:
            # 没有检测到有效轮廓
            display_frame = frame.copy()
            cv2.line(display_frame, (0, roi_top), (orig_width, roi_top), (100, 100, 100), 1)
            cv2.putText(display_frame, "No line detected", (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            imgok = Image.fromarray(display_frame)
            self._show_pil(imgok)
            return result
        
        # 取最大轮廓
        best_cnt = max(valid_cnts, key=cv2.contourArea)
        
        # 创建只包含最大轮廓的掩码
        line_mask = np.zeros_like(binary)
        cv2.drawContours(line_mask, [best_cnt], -1, 255, -1)
        
        # ========== 第五步：逐列扫描 + 中心线拟合 ==========
        centroid_x = []  # 列坐标
        centroid_y = []  # 该列黑色像素的垂直中点
        
        # 逐列扫描
        for x in range(roi_width):
            col_pixels = np.where(line_mask[:, x] > 0)[0]  # 该列的白色像素（即黑线）
            if len(col_pixels) > 0:
                # 计算该列像素的垂直中点
                y_center = int(np.mean(col_pixels))
                centroid_x.append(x)
                centroid_y.append(y_center)
        
        if len(centroid_x) < 5:
            # 采样点太少，无法拟合
            display_frame = frame.copy()
            cv2.line(display_frame, (0, roi_top), (orig_width, roi_top), (100, 100, 100), 1)
            cv2.putText(display_frame, "Too few points", (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 165, 0), 2)
            imgok = Image.fromarray(display_frame)
            self._show_pil(imgok)
            return result
        
        centroid_x = np.array(centroid_x)
        centroid_y = np.array(centroid_y)
        
        # 用顶部20%和底部20%区域的平均x来计算角度（更稳定）
        y_min, y_max = centroid_y.min(), centroid_y.max()
        y_range = y_max - y_min
        
        if y_range > 5:
            # 取顶部20%的点的平均x坐标
            top_threshold = y_min + y_range * 0.2
            top_mask = centroid_y <= top_threshold
            top_x = centroid_x[top_mask].mean() if top_mask.any() else centroid_x.mean()
            
            # 取底部20%的点的平均x坐标
            bottom_threshold = y_max - y_range * 0.2
            bottom_mask = centroid_y >= bottom_threshold
            bottom_x = centroid_x[bottom_mask].mean() if bottom_mask.any() else centroid_x.mean()
            
            # 计算角度
            dx = top_x - bottom_x
            line_angle = math.degrees(math.atan2(dx, y_range))
        else:
            # 线太短，无法计算角度
            line_angle = 0
            bottom_x = centroid_x.mean()
        
        # 角度归一化到 [-90, 90]
        if line_angle > 90:
            line_angle -= 180
        elif line_angle < -90:
            line_angle += 180
        
        result['x'] = int(bottom_x * SCREEN_WIDTH / orig_width)
        result['angle'] = int(line_angle)
        
        # 保存底部点y坐标用于绘制
        bottom_y_draw = int(y_max)
        
        # ========== 绘制结果 ==========
        display_frame = frame.copy()
        
        # 绘制ROI分界线
        cv2.line(display_frame, (0, roi_top), (orig_width, roi_top), (100, 100, 100), 1)
        
        # 绘制轮廓
        shifted_cnt = best_cnt.copy()
        shifted_cnt[:, 0, 1] += roi_top
        cv2.drawContours(display_frame, [shifted_cnt], -1, (0, 255, 0), 2)
        
        # 绘制拟合曲线上的点
        for i in range(0, len(centroid_x), 3):  # 每3个点画一个
            px = centroid_x[i]
            py = centroid_y[i] + roi_top
            cv2.circle(display_frame, (int(px), int(py)), 3, (255, 255, 0), -1)
        
        # 绘制底部检测点
        draw_cx = int(bottom_x)
        draw_cy = bottom_y_draw + roi_top
        cv2.circle(display_frame, (draw_cx, draw_cy), 8, (255, 0, 255), -1)
        
        # 绘制方向线
        angle_rad = math.radians(result['angle'])
        line_len = 40
        dx = int(line_len * math.sin(angle_rad))
        dy = int(line_len * math.cos(angle_rad))
        cv2.line(display_frame, (draw_cx, draw_cy), (draw_cx + dx, draw_cy - dy), (255, 0, 0), 3)
        
        # 显示信息
        offset = result['x'] - SCREEN_WIDTH // 2
        info_text = f"X:{result['x']} Off:{offset} Ang:{result['angle']}"
        cv2.putText(display_frame, info_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.putText(display_frame, f"Pts:{len(centroid_x)}", (200, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        
        imgok = Image.fromarray(display_frame)
        self._show_pil(imgok)
        
        return result

    def cap_color_mask(self, position=None, scale=25, h_error=20, s_limit=[90, 255], v_limit=[90, 230]):
        if position is None:
            position = [160, 100]
        count = 0
        self.open_camera()
        
        while True:
            if self.xgoButton("c"):   
                break
                
            # 图像采集（统一使用image变量）
            image = self.picam2.capture_array()  # Picamera2默认输出BGR
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)  # 转为RGB用于显示
            
            # 颜色空间处理（保持BGR用于HSV转换）
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            h, s, v = cv2.split(hsv)
            
            # 颜色采样
            color = np.mean(h[position[1]:position[1] + scale, position[0]:position[0] + scale])
            
            if self.xgoButton("b") and count == 0:
                count += 1
                color_lower = [max(color - h_error, 0), s_limit[0], v_limit[0]]
                color_upper = [min(color + h_error, 255), s_limit[1], v_limit[1]]
                return [color_lower, color_upper]
    
            # 绘制界面（使用RGB图像）
            if count == 0:
                cv2.rectangle(image_rgb, 
                             (position[0], position[1]), 
                             (position[0] + scale, position[1] + scale),
                             (255, 255, 255), 2)
                cv2.putText(image_rgb, 'press button B', 
                           (40, 40), cv2.FONT_HERSHEY_SIMPLEX, 
                           0.7, (255, 0, 0), 2)  # RGB格式的红色
            
            # 显示（直接使用RGB）
            imgok = Image.fromarray(image_rgb)
            self._show_pil(imgok)
    
    def filter_img(self,frame,color):
        b,g,r = cv2.split(frame)
        frame_bgr = cv2.merge((r,g,b))
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        if isinstance(color, list):
            color_lower = np.array(color[0])
            color_upper = np.array(color[1])
        else:
            color_upper, color_lower = get_color_mask(color)
        mask = cv2.inRange(hsv, color_lower, color_upper)
        img_mask = cv2.bitwise_and(frame, frame, mask=mask)
        return img_mask

    def BallRecognition(self,color_mask,target="camera",p1=36, p2=15, minR=6, maxR=35):
        x=y=ra=0
        if target=="camera":
            self.open_camera()
            image = self.picam2.capture_array()
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)  # 如果需要RGB格式
        else:
            path="/home/pi/xgoPictures/"
            image=np.array(Image.open(path+target))

        frame_mask=self.filter_img(image, color_mask)
        
        img = cv2.medianBlur(frame_mask, 5)
        img = cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
        
        circles = cv2.HoughCircles(img, cv2.HOUGH_GRADIENT, 1, 20, param1=p1, param2=p2, minRadius=minR,maxRadius=maxR)
        b,g,r = cv2.split(image)
        image = cv2.merge((r,g,b))
        if circles is not None and len(circles[0]) == 1:
            param = circles[0][0]
            x, y, ra = int(param[0]), int(param[1]), int(param[2])
            cv2.circle(image, (x, y), ra, (255, 255, 255), 2)
            cv2.circle(image, (x, y), 2, (255, 255, 255), 2)
        imgok = Image.fromarray(image)
        self._show_pil(imgok)
        return x,y,ra





class DemoError(Exception):
    pass

class hands():
    """手势识别 - 使用 cv2.dnn + MediaPipe ONNX 模型替代 mediapipe Python API"""
    _PALM_MODEL = '/home/pi/luwu-os/model/palm_detection_mediapipe_2023feb.onnx'
    _HAND_MODEL = '/home/pi/luwu-os/model/handpose_estimation_mediapipe_2023feb.onnx'

    def __init__(self, model_complexity, max_num_hands, min_detection_confidence, min_tracking_confidence):
        import sys
        sys.path.insert(0, '/home/pi/luwu-os/model')
        try:
            from mp_palmdet import MPPalmDet
            from mp_handpose import MPHandPose
        except ImportError as e:
            raise ImportError(f'缺少辅助脚本: {e}，请确认 /home/pi/luwu-os/model/ 中有 mp_palmdet.py / mp_handpose.py')
        if not os.path.exists(self._PALM_MODEL) or not os.path.exists(self._HAND_MODEL):
            raise FileNotFoundError('缺少手势识别模型文件，请确认 /home/pi/luwu-os/model/ 目录')
        self.max_num_hands = max_num_hands
        self.min_detection_confidence = min_detection_confidence
        self._palm_det  = MPPalmDet(self._PALM_MODEL,  scoreThreshold=min_detection_confidence)
        self._hand_pose = MPHandPose(self._HAND_MODEL, confThreshold=min_detection_confidence)

    def run(self, cv_img):
        """输入 BGR 图像，返回与原 mediapipe hands 兼容的格式"""
        palms = self._palm_det.infer(cv_img)
        hf = []
        if palms is None:
            return hf
        for palm in palms[:self.max_num_hands]:
            hand = self._hand_pose.infer(cv_img, palm)
            if hand is None:
                continue
            # hand 格式 (132 floats):
            # [0:4]    bbox [x1,y1,x2,y2]
            # [4:67]   screen landmarks 21*3 (x,y,z)
            # [67:130] world landmarks 21*3
            # [130]    handedness (0=left, 1=right)
            # [131]    conf
            x1, y1, x2, y2 = int(hand[0]), int(hand[1]), int(hand[2]), int(hand[3])
            lm = hand[4:67].reshape(21, 3)
            pts = [(int(lm[i, 0]), int(lm[i, 1])) for i in range(21)]
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            rect = [x1, y1, x2 - x1, y2 - y1]
            right_left = 'R' if float(hand[130]) > 0.5 else 'L'
            hf.append({
                'center': (cx, cy),
                'rect': rect,
                'dlandmark': pts,
                'hand_angle': self.hand_angle(pts),
                'right_left': right_left,
            })
        return hf

    def vector_2d_angle(self, v1, v2):
        v1_x = v1[0]
        v1_y = v1[1]
        v2_x = v2[0]
        v2_y = v2[1]
        try:
            angle_= math.degrees(math.acos((v1_x*v2_x+v1_y*v2_y)/(((v1_x**2+v1_y**2)**0.5)*((v2_x**2+v2_y**2)**0.5))))
        except:
            angle_ = 180
        return angle_

    def hand_angle(self,hand_):
        angle_list = []
        # thumb 大拇指角度
        angle_ = self.vector_2d_angle(
            ((int(hand_[0][0])- int(hand_[2][0])),(int(hand_[0][1])-int(hand_[2][1]))),
            ((int(hand_[3][0])- int(hand_[4][0])),(int(hand_[3][1])- int(hand_[4][1])))
            )
        angle_list.append(angle_)
        # index 食指角度
        angle_ = self.vector_2d_angle(
            ((int(hand_[0][0])-int(hand_[6][0])),(int(hand_[0][1])- int(hand_[6][1]))),
            ((int(hand_[7][0])- int(hand_[8][0])),(int(hand_[7][1])- int(hand_[8][1])))
            )
        angle_list.append(angle_)
        # middle 中指角度
        angle_ = self.vector_2d_angle(
            ((int(hand_[0][0])- int(hand_[10][0])),(int(hand_[0][1])- int(hand_[10][1]))),
            ((int(hand_[11][0])- int(hand_[12][0])),(int(hand_[11][1])- int(hand_[12][1])))
            )
        angle_list.append(angle_)
        # ring 無名指角度
        angle_ = self.vector_2d_angle(
            ((int(hand_[0][0])- int(hand_[14][0])),(int(hand_[0][1])- int(hand_[14][1]))),
            ((int(hand_[15][0])- int(hand_[16][0])),(int(hand_[15][1])- int(hand_[16][1])))
            )
        angle_list.append(angle_)
        # pink 小拇指角度
        angle_ = self.vector_2d_angle(
            ((int(hand_[0][0])- int(hand_[18][0])),(int(hand_[0][1])- int(hand_[18][1]))),
            ((int(hand_[19][0])- int(hand_[20][0])),(int(hand_[19][1])- int(hand_[20][1])))
            )
        angle_list.append(angle_)
        return angle_list
    
class yoloXgo():
    def __init__(self,model,classes,inputwh,thresh):
        import onnxruntime 
        self.session = onnxruntime.InferenceSession(model)
        self.input_width=inputwh[0]
        self.input_height=inputwh[1]
        self.thresh=thresh
        self.classes=classes
        
    def sigmoid(self,x):
        return 1. / (1 + np.exp(-x))

    # tanh函数
    def tanh(self,x):
        return 2. / (1 + np.exp(-2 * x)) - 1

    # 数据预处理
    def preprocess(self,src_img, size):
        output = cv2.resize(src_img,(size[0], size[1]),interpolation=cv2.INTER_AREA)
        output = output.transpose(2,0,1)
        output = output.reshape((1, 3, size[1], size[0])) / 255
        return output.astype('float32') 

    # nms算法
    def nms(self,dets,thresh=0.45):
        # dets:N*M,N是bbox的个数，M的前4位是对应的（x1,y1,x2,y2），第5位是对应的分数
        # #thresh:0.3,0.5....
        x1 = dets[:, 0]
        y1 = dets[:, 1]
        x2 = dets[:, 2]
        y2 = dets[:, 3]
        scores = dets[:, 4]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)  # 求每个bbox的面积
        order = scores.argsort()[::-1]  # 对分数进行倒排序
        keep = []  # 用来保存最后留下来的bboxx下标

        while order.size > 0:
            i = order[0]  # 无条件保留每次迭代中置信度最高的bbox
            keep.append(i)

            # 计算置信度最高的bbox和其他剩下bbox之间的交叉区域
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            # 计算置信度高的bbox和其他剩下bbox之间交叉区域的面积
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h

            # 求交叉区域的面积占两者（置信度高的bbox和其他bbox）面积和的必烈
            ovr = inter / (areas[i] + areas[order[1:]] - inter)

            # 保留ovr小于thresh的bbox，进入下一次迭代。
            inds = np.where(ovr <= thresh)[0]

            # 因为ovr中的索引不包括order[0]所以要向后移动一位
            order = order[inds + 1]
        
        output = []
        for i in keep:
            output.append(dets[i].tolist())

        return output

    def run(self, img,):
        pred = []

        # 输入图像的原始宽高
        H, W, _ = img.shape

        # 数据预处理: resize, 1/255
        data = self.preprocess(img, [self.input_width, self.input_height])

        # 模型推理
        input_name = self.session.get_inputs()[0].name
        feature_map = self.session.run([], {input_name: data})[0][0]

        # 输出特征图转置: CHW, HWC
        feature_map = feature_map.transpose(1, 2, 0)
        # 输出特征图的宽高
        feature_map_height = feature_map.shape[0]
        feature_map_width = feature_map.shape[1]

        # 特征图后处理
        for h in range(feature_map_height):
            for w in range(feature_map_width):
                data = feature_map[h][w]

                # 解析检测框置信度
                obj_score, cls_score = data[0], data[5:].max()
                score = (obj_score ** 0.6) * (cls_score ** 0.4)

                # 阈值筛选
                if score > self.thresh:
                    # 检测框类别
                    cls_index = np.argmax(data[5:])
                    # 检测框中心点偏移
                    x_offset, y_offset = self.tanh(data[1]), self.tanh(data[2])
                    # 检测框归一化后的宽高
                    box_width, box_height = self.sigmoid(data[3]), self.sigmoid(data[4])
                    # 检测框归一化后中心点
                    box_cx = (w + x_offset) / feature_map_width
                    box_cy = (h + y_offset) / feature_map_height
                    
                    # cx,cy,w,h => x1, y1, x2, y2
                    x1, y1 = box_cx - 0.5 * box_width, box_cy - 0.5 * box_height
                    x2, y2 = box_cx + 0.5 * box_width, box_cy + 0.5 * box_height
                    x1, y1, x2, y2 = int(x1 * W), int(y1 * H), int(x2 * W), int(y2 * H)

                    pred.append([x1, y1, x2, y2, score, cls_index])
        datas=np.array(pred)
        data=[]
        if len(datas)>0:
            boxes=self.nms(datas)
            for b in boxes:
                obj_score, cls_index = b[4], int(b[5])
                x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                s={'classes':self.classes[cls_index],'score':'%.2f' % obj_score,'xywh':[x1,y1,x2-x1,y2-y1],}
                data.append(s)
            return data
        else:
            return False

class face_detection():
    """人脸检测 - 使用 cv2.FaceDetectorYN (YuNet ONNX) 替代 MediaPipe"""
    _MODEL_PATH = '/home/pi/luwu-os/model/face_detection_yunet_2023mar.onnx'

    def __init__(self, min_detection_confidence=0.7):
        self.min_detection_confidence = min_detection_confidence
        if not os.path.exists(self._MODEL_PATH):
            raise FileNotFoundError(
                f'缺少人脸检测模型: {self._MODEL_PATH}，请运行 install.sh 下载')
        self._detector = cv2.FaceDetectorYN.create(
            self._MODEL_PATH, '', (320, 240),
            score_threshold=self.min_detection_confidence
        )

    def run(self, cv_img):
        """输入 BGR 图像，返回与原 mediapipe face_detection 兼容的格式"""
        h, w = cv_img.shape[:2]
        self._detector.setInputSize((w, h))
        _, faces = self._detector.detect(cv_img)
        result = []
        if faces is None:
            return result
        for face in faces:
            # YuNet 输出: [x1,y1,w,h, re_x,re_y, le_x,le_y,
            #              nt_x,nt_y, rcm_x,rcm_y, lcm_x,lcm_y, score]
            x1, y1, fw, fh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
            data = {
                'id': 0,
                'score': round(float(face[14]), 3),
                'rect': [x1, y1, fw, fh],
                'right_eye': (int(face[4]),  int(face[5])),
                'left_eye':  (int(face[6]),  int(face[7])),
                'nose':      (int(face[8]),  int(face[9])),
                'mouth':     (int(face[10]), int(face[11])),
                'right_ear': (0, 0),   # YuNet 不检测耳朵，保持接口兼容
                'left_ear':  (0, 0),
            }
            result.append(data)
        return result
