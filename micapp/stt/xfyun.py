"""讯飞流式听写引擎：每段语音一个 IAT 会话，后台线程串行处理。"""
import base64
import hashlib
import hmac
import json
import logging
import queue
import ssl
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import websocket

from .base import STTEngine, register

FRAME = 1280  # 40ms @16k s16LE mono


def _sslopt() -> dict:
    """优先用 certifi 的 CA 包：打包后 Windows 证书库可能有坏证书，
    默认 load_default_certs 会整库失败（ASN1: NOT_ENOUGH_DATA）。"""
    try:
        import certifi
        return {"ca_certs": certifi.where(), "cert_reqs": ssl.CERT_REQUIRED}
    except Exception:
        return {"cert_reqs": ssl.CERT_NONE}


@register
class XfyunEngine(STTEngine):
    name = "xfyun"

    def start(self):
        self._q: queue.Queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._utterance_no = 0
        self._worker = threading.Thread(target=self._loop, daemon=True, name="xfyun-worker")
        self._worker.start()

    def stop(self):
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    def feed_utterance(self, pcm: bytes):
        self._utterance_no += 1
        try:
            self._q.put_nowait((self._utterance_no, pcm))
        except queue.Full:
            self._drop(self._utterance_no, "队列满")  # 云端跟不上时丢这句，别堵住收包线程

    def _loop(self):
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                break
            idx, pcm = item
            try:
                text = self._transcribe(pcm)
                if text and self.on_result:
                    self.on_result(text, idx)
                else:
                    self._drop(idx, "空结果")
            except Exception as e:
                # 错误文字绝不能走 on_result：语音输入会把「[识别错误: …]」当真话打进前台窗口
                logging.exception("xfyun utterance %s failed", idx)
                self._drop(idx, "转写失败: %s" % e)

    def _make_url(self) -> str:
        host, path = "iat-api.xfyun.cn", "/v2/iat"
        date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        secret = self.config["xfyun"]["api_secret"].encode()
        sig = base64.b64encode(hmac.new(secret, origin.encode(), hashlib.sha256).digest()).decode()
        auth_origin = (f'api_key="{self.config["xfyun"]["api_key"]}", '
                       f'algorithm="hmac-sha256", headers="host date request-line", '
                       f'signature="{sig}"')
        auth = base64.b64encode(auth_origin.encode()).decode()
        return f"wss://{host}{path}?authorization={quote(auth)}&date={quote(date)}&host={quote(host)}"

    def _transcribe(self, pcm: bytes) -> str:
        ws = websocket.create_connection(self._make_url(), timeout=15, sslopt=_sslopt())
        try:
            frames = [pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME)] or [b""]
            n = len(frames)
            lang = self.config.get("language", "zh_cn")
            for i, piece in enumerate(frames):
                last = (i == n - 1)
                body = {"data": {"status": 2 if last else 1,
                                 "format": "audio/L16;rate=16000",
                                 "encoding": "raw",
                                 "audio": base64.b64encode(piece).decode()}}
                if i == 0:
                    body["common"] = {"app_id": self.config["xfyun"]["app_id"]}
                    body["business"] = {"language": lang, "domain": "iat",
                                        "accent": "mandarin", "vad_eos": 10000}
                    if n == 1:
                        body["data"]["status"] = 2
                ws.send(json.dumps(body))
                time.sleep(0.005)
            text = []
            ws.settimeout(60)
            while True:
                msg = json.loads(ws.recv())
                if msg.get("code", 0) != 0:
                    raise RuntimeError(f"讯飞 code={msg.get('code')} {msg.get('message')}")
                d = msg["data"]
                if d.get("result"):
                    for w in d["result"]["ws"]:
                        for cw in w["cw"]:
                            text.append(cw["w"])
                if d.get("status") == 2:
                    return "".join(text)
        finally:
            ws.close()
