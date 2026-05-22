# -*- coding: utf-8 -*-
"""ASR 管理器 - 阿里云 / Deepgram 统一接口"""

import json
import time
import base64
import threading
import websocket

try:
    import pyaudio
except ImportError:
    pyaudio = None


class BaseASR:
    """ASR 基类"""

    # partial 模式: "cumulative"=每次回调完整文本, "delta"=每次回调增量片段
    partial_mode = "cumulative"

    # 外部设置此标志可中断录音循环（线程安全：bool 赋值在 CPython 是原子的）
    _abort = False

    def __init__(self, config):
        self.config = config
        self.on_final = None       # callback(text: str)
        self.on_partial = None     # callback(text: str)

    def start_recording(self, max_duration=30):
        """开始录音并识别，阻塞直到完成，返回识别文本"""
        raise NotImplementedError


class AliyunASR(BaseASR):
    """阿里云 ASR WebSocket 流式识别"""

    partial_mode = "delta"  # 阿里云发送增量 delta

    def __init__(self, config):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "qwen3-asr-flash-realtime")
        self.language = config.get("language", "zh")
        self.vad_threshold = config.get("vad_threshold", 0.5)
        self.silence_ms = config.get("silence_duration_ms", 800)
        self.base_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    def start_recording(self, max_duration=30):
        CHUNK = 3200  # 16000 * 0.1 * 2
        RATE = 16000

        ws_url = f"{self.base_url}?model={self.model}"
        headers = [
            f"Authorization: Bearer {self.api_key}",
            "OpenAI-Beta: realtime=v1"
        ]

        result = {"text": "", "done": False, "session_ready": False}

        def on_open(ws):
            result["connected"] = True
            cfg = {
                "event_id": f"evt_{int(time.time()*1000)}",
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "input_audio_format": "pcm",
                    "sample_rate": RATE,
                    "input_audio_transcription": {"language": self.language},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": self.vad_threshold,
                        "silence_duration_ms": self.silence_ms
                    }
                }
            }
            ws.send(json.dumps(cfg))

        def on_message(ws, message):
            data = json.loads(message)
            evt = data.get("type", "")
            if evt == "session.updated":
                result["session_ready"] = True
            elif evt == "conversation.item.input_audio_transcription.delta":
                delta = data.get("delta", "")
                if delta and self.on_partial:
                    self.on_partial(delta)
            elif evt == "conversation.item.input_audio_transcription.completed":
                result["text"] = data.get("transcript", "")
                result["done"] = True
            elif evt == "session.finished":
                result["text"] = data.get("transcript", result["text"])
                result["done"] = True

        def on_error(ws, error):
            print(f"[AliyunASR] Error: {error}")
            result["done"] = True

        def on_close(ws, code, msg):
            result["done"] = True

        ws = websocket.WebSocketApp(
            ws_url, header=headers,
            on_open=on_open, on_message=on_message,
            on_error=on_error, on_close=on_close
        )
        ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
        ws_thread.start()

        # 等待会话就绪
        t0 = time.time()
        while not result.get("session_ready") and (time.time() - t0) < 5:
            if result["done"]:
                break
            time.sleep(0.05)

        if not result.get("session_ready"):
            print("[AliyunASR] Session not ready, aborting")
            return ""

        # 录音
        try:
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                            input=True, frames_per_buffer=CHUNK)
        except Exception as e:
            print(f"[AliyunASR] Mic open error: {e}")
            return ""

        print("[AliyunASR] Recording...")
        self._abort = False
        t_start = time.time()
        try:
            while not result["done"] and not self._abort and (time.time() - t_start) < max_duration:
                data = stream.read(CHUNK, exception_on_overflow=False)
                if result.get("session_ready") and not result["done"] and not self._abort:
                    encoded = base64.b64encode(data).decode("utf-8")
                    try:
                        ws.send(json.dumps({
                            "event_id": f"evt_{int(time.time()*1000)}",
                            "type": "input_audio_buffer.append",
                            "audio": encoded
                        }))
                    except Exception:
                        break
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

        # 等待最终结果
        if not result["done"]:
            try:
                ws.send(json.dumps({
                    "event_id": f"evt_{int(time.time()*1000)}",
                    "type": "session.finish"
                }))
            except Exception:
                pass
            t_wait = time.time()
            while not result["done"] and (time.time() - t_wait) < 5:
                time.sleep(0.1)

        dur = time.time() - t_start
        print(f"[AliyunASR] Done in {dur:.1f}s: '{result['text']}'")
        return result["text"]


class DeepgramASR(BaseASR):
    """Deepgram Streaming Speech-to-Text (WebSocket)

    partial_mode = "cumulative"  (Deepgram interim results are full transcripts)

    Official docs: https://developers.deepgram.com/docs/stt-streaming

    Low-level WebSocket API with built-in VAD (Voice Activity Detection).
    WebSocket endpoint: wss://api.deepgram.com/v1/listen

    Parameters (via query string):
      - encoding: linear16 (16-bit PCM)
      - sample_rate: 16000
      - language: zh / en / ja etc.
      - model: nova-3 / nova-2 / base / enhanced
      - interim_results: true (partial transcripts)
      - vad_events: true (UtteranceEnd notification)
      - utterance_end_ms: silence duration before VAD cutoff

   cod Refresh: binary audio frames → JSON transcription responses
    """

    def __init__(self, config):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "nova-3")
        self.language = config.get("language", "zh")
        self.vad_silence_ms = config.get("vad_silence_ms", 1000)
        self.base_url = "wss://api.deepgram.com/v1/listen"

    def start_recording(self, max_duration=30):
        CHUNK = 3200  # 16000 * 0.1 * 2
        RATE = 16000

        if not self.api_key:
            print("[DeepgramASR] API Key not configured")
            return ""

        # Build WebSocket URL with query parameters
        params = [
            f"encoding=linear16",
            f"sample_rate={RATE}",
            f"language={self.language}",
            f"model={self.model}",
            f"interim_results=true",
            f"vad_events=true",
            f"utterance_end_ms={self.vad_silence_ms}",
        ]
        ws_url = f"{self.base_url}?{'&'.join(params)}"
        headers = [f"Authorization: Token {self.api_key}"]

        result = {"text": "", "done": False, "final_texts": [], "connected": False}

        def on_open(ws):
            result["connected"] = True
            print("[DeepgramASR] WebSocket connected")

        def on_message(ws, message):
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "Results":
                is_final = data.get("is_final", False)
                speech_final = data.get("speech_final", False)
                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])
                transcript = alternatives[0].get("transcript", "") if alternatives else ""

                if transcript:
                    if is_final:
                        result["final_texts"].append(transcript)
                        if speech_final:
                            result["text"] = " ".join(result["final_texts"])
                            result["done"] = True
                    elif self.on_partial:
                        self.on_partial(transcript)

            elif msg_type == "UtteranceEnd":
                # VAD detected voice activity ended
                if result["final_texts"]:
                    result["text"] = " ".join(result["final_texts"])
                result["done"] = True
                print("[DeepgramASR] UtteranceEnd (VAD triggered)")

            elif msg_type == "Error":
                err_msg = data.get("message", "Unknown error")
                print(f"[DeepgramASR] Server error: {err_msg}")
                result["done"] = True

        def on_error(ws, error):
            print(f"[DeepgramASR] WebSocket error: {error}")
            result["done"] = True

        def on_close(ws, code, msg):
            result["done"] = True
            print(f"[DeepgramASR] WebSocket closed (code={code})")

        ws = websocket.WebSocketApp(
            ws_url, header=headers,
            on_open=on_open, on_message=on_message,
            on_error=on_error, on_close=on_close
        )
        ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
        ws_thread.start()

        # Wait for connection (allow up to 15s for high-latency networks)
        t0 = time.time()
        while not result.get("connected") and (time.time() - t0) < 15:
            if result["done"]:
                break
            time.sleep(0.05)

        if not result.get("connected"):
            print("[DeepgramASR] Connection timeout (>15s, check network to api.deepgram.com)")
            return ""

        # Check if server closed immediately after connect (e.g., auth error / 1011)
        if result["done"]:
            print("[DeepgramASR] Server closed connection immediately (auth error or invalid params)")
            return ""

        # Open microphone and stream audio
        try:
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                            input=True, frames_per_buffer=CHUNK)
        except Exception as e:
            print(f"[DeepgramASR] Mic open error: {e}")
            return ""

        print("[DeepgramASR] Recording...")
        self._abort = False
        t_start = time.time()
        try:
            while not result["done"] and not self._abort and (time.time() - t_start) < max_duration:
                data = stream.read(CHUNK, exception_on_overflow=False)
                if not result["done"] and not self._abort:
                    try:
                        ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                    except Exception:
                        break
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

        # Wait for final result if not already done
        if not result["done"]:
            t_wait = time.time()
            while not result["done"] and (time.time() - t_wait) < 3:
                time.sleep(0.05)

        # Close WebSocket
        try:
            ws.close()
        except Exception:
            pass

        dur = time.time() - t_start
        print(f"[DeepgramASR] Done in {dur:.1f}s: '{result['text']}'")
        return result["text"]


def create_asr(config):
    """根据配置创建 ASR 实例"""
    provider = config.get("provider", "aliyun")
    if provider == "deepgram":
        return DeepgramASR(config.get("deepgram", {}))
    else:
        return AliyunASR(config.get("aliyun", {}))
