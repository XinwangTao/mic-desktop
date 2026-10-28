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
import urllib.request
import wave

from .base import STTEngine, register


@register
class WhisperHttpEngine(STTEngine):
    name = "whisper_http"

    def start(self):
        self._q: queue.Queue = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._no = 0
        self._worker = threading.Thread(target=self._loop, daemon=True, name="whisper-http-worker")
        self._worker.start()

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

    def _loop(self):
        url = self.config.get("whisper_http", {}).get("url", "http://127.0.0.1:8390/stt")
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                break
            idx, pcm = item
            try:
                text = self._transcribe(url, pcm)
                if text and self.on_result:
                    self.on_result(text, idx)
            except Exception as e:
                logging.exception("whisper_http utterance %s failed", idx)
                if self.on_result:
                    self.on_result(f"[本地识别错误: {e}]", idx)

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
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read()).get("text", "").strip()
