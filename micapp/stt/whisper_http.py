"""本地 Whisper 引擎：POST WAV 到 Docker 内的 stt_server，返回 JSON {text}。

服务启动（宿主机）:
  docker run -d --name whisper-local --restart unless-stopped \
    -v <模型目录>:/models:ro -v stt_server.py:/w/stt_server.py:ro \
    -p 8390:8390 whisper-stt python /w/stt_server.py
"""
import io
import json
import logging
import queue
import threading
import time
import urllib.request
import wave

from .base import STTEngine, register

DEFAULT_URL = "http://127.0.0.1:8390/stt"


def _short(exc: Exception) -> str:
    """异常压成一句人话：完整 traceback 进日志，界面只留关键信息。"""
    text = str(exc)
    if "Connection refused" in text or "10061" in text:
        return "连接被拒绝，服务没在运行"
    if "timed out" in text or "timeout" in text.lower():
        return "请求超时"
    return text[:80]


@register
class WhisperHttpEngine(STTEngine):
    name = "whisper_http"

    def start(self):
        self._q: queue.Queue = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._no = 0
        self._worker = threading.Thread(target=self._loop, daemon=True, name="whisper-http-worker")
        self._worker.start()
        # 启动就探一次：服务没起的话，用户开口之前就该知道，
        # 而不是等第一句话变成一条错误气泡。
        threading.Thread(target=self._probe, daemon=True, name="whisper-http-probe").start()

    def stop(self):
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    def feed_utterance(self, pcm: bytes):
        self._no += 1
        try:
            self._q.put_nowait((self._no, pcm))
        except queue.Full:
            pass

    # ---------- 内部 ----------
    def _url(self) -> str:
        return self.config.get("whisper_http", {}).get("url", DEFAULT_URL)

    def _status(self, text: str):
        """给界面一条状态；没接回调就只写日志，不抛。"""
        cb = getattr(self, "status_cb", None)
        if cb is None:
            logging.info("whisper_http: %s", text)
            return
        try:
            cb(text)
        except Exception:
            logging.exception("status_cb 调用失败")

    def _probe(self):
        try:
            t0 = time.monotonic()
            # 0.5 秒静音只探可达性：服务端有 VAD 过滤，静音不会真跑模型（实测 0.05 秒返回）
            self._transcribe(self._url(), b"\x00\x00" * 4000)
            self._status("本地转写服务就绪（%.2f 秒响应）" % (time.monotonic() - t0))
        except Exception as exc:
            logging.exception("whisper_http probe failed")
            self._status("本地转写服务不可用：%s —— 执行 docker start whisper-local"
                         % _short(exc))

    def _loop(self):
        url = self._url()
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                break
            idx, pcm = item
            try:
                text = self._transcribe(url, pcm)
            except Exception:
                # 容器刚起或偶发超时：重试一次，别把一次抖动变成一条错误气泡
                time.sleep(0.8)
                try:
                    text = self._transcribe(url, pcm)
                except Exception as exc:
                    logging.exception("whisper_http utterance %s failed twice", idx)
                    self._status("本地转写失败：%s（这句跳过）" % _short(exc))
                    continue
            if text and self.on_result:
                self.on_result(text, idx)

    @staticmethod
    def _transcribe(url: str, pcm: bytes) -> str:
        buf = io.BytesIO()
        w = wave.open(buf, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
        w.close()
        req = urllib.request.Request(url, data=buf.getvalue(),
                                     headers={"Content-Type": "audio/wav"})
        # 30 秒足够跑完 10 分钟的音频；原来 120 秒会让服务卡死时界面空等两分钟
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("text", "").strip()
