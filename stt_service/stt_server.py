"""本地转写 HTTP 服务：跑在 whisper-stt 容器内，POST WAV body 返回 JSON {text}。

环境变量 MODEL_PATH 指定模型目录（默认 /models）。
"""
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from faster_whisper import WhisperModel

MODEL_PATH = os.environ.get("MODEL_PATH", "/models")
# whisper-small 在没有上下文时会输出繁体，而且会自作主张改字（「带上」→「戴上」）。
# 给一句简体中文提示，繁简和用字能同时纠正——实测同一段音频从
# 「明天下午三點開會,記得把方案戴上」变成「明天下午三点开会，记得把方案带上」。
INITIAL_PROMPT = os.environ.get("INITIAL_PROMPT", "以下是普通话的句子。")
# 推理参数全部走环境变量：换设备/换精度/调线程不用改镜像，重建容器即可
DEVICE = os.environ.get("DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("COMPUTE_TYPE", "int8")
CPU_THREADS = int(os.environ.get("CPU_THREADS", "0"))     # 0 = 让 ctranslate2 自己决定
BEAM_SIZE = int(os.environ.get("BEAM_SIZE", "1"))
PORT = int(os.environ.get("PORT", "8390"))
HOST = os.environ.get("HOST", "127.0.0.1")   # 供第二台电脑用时设 0.0.0.0（注意防火墙放行）
print(f"[stt_server] loading model from {MODEL_PATH} ...", flush=True)
model = WhisperModel(MODEL_PATH, device=DEVICE, compute_type=COMPUTE_TYPE,
                     cpu_threads=CPU_THREADS, local_files_only=True)
lock = threading.Lock()
print(f"[stt_server] ready device={DEVICE} compute={COMPUTE_TYPE} threads={CPU_THREADS} "
      f"beam={BEAM_SIZE} prompt={INITIAL_PROMPT!r}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            with lock:
                segments, info = model.transcribe(io.BytesIO(body), language="zh",
                                                  beam_size=BEAM_SIZE, vad_filter=True,
                                                  initial_prompt=INITIAL_PROMPT or None)
                text = "".join(s.text.strip() for s in segments)
            self._reply(200, {"text": text, "lang": info.language})
        except Exception as e:
            self._reply(500, {"error": str(e)})

    def _reply(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"[stt_server] listening on {HOST}:{PORT}", flush=True)
    HTTPServer((HOST, PORT), Handler).serve_forever()
