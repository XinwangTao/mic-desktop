"""跨机语音输入——第二台接收端（无界面）：收中枢转好的文字，打进本机前台窗口。

跑法：python -m micapp.remote_receiver（或仓库根目录 remote_receiver.py）。
只做两件事：起一个 HTTP 端点收 {text,dur}，交给复用 VoiceInput 上屏。
不连板子、不跑 STT、不需要 Qt；注入与护栏复用 voice_input.py / typer.py。

配置：读「本脚本/EXE 所在目录」的 config.json（dev 下即当前工作目录），
合并进 config.DEFAULTS；state.json 也写在同一目录。与 Qt 主程序不同，
接收端是独立脚本，config 位置以运行位置为准，不跟 micapp/config.py 走。
"""
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from micapp import config, typer
from micapp.voice_input import VoiceInput


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def load_config(base: str) -> dict:
    cfg = json.loads(json.dumps(config.DEFAULTS))
    path = os.path.join(base, "config.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except (json.JSONDecodeError, OSError):
            pass  # 坏配置按默认跑
    return cfg


def build_voice(cfg: dict, state_path: str) -> VoiceInput:
    """装配一个「常开、无界面」的 VoiceInput：终端护栏照常，其余回调都退化成空操作。"""

    def is_risky() -> bool:
        _hwnd, cls, title = typer.foreground_info()
        return typer.is_risky_window(cls, title)

    voice = VoiceInput(
        on_status=lambda s: logging.info("remote: %s", s),
        on_state=lambda on, paused: logging.info("remote: voice on=%s paused=%s", on, paused),
        is_self_foreground=lambda: False,   # 没有自己的窗口，谈不上打进自己
        is_playing=lambda: False,           # 不跑 TTS，没有回读互斥
        is_risky_target=is_risky,
        allow_console=bool(cfg.get("voice_input_allow_console", False)),
        enter_words=cfg.get("voice_input_enter_words", []),
        clear_words=cfg.get("voice_input_clear_words", []),
        state_path=state_path,
        min_seconds=float(cfg.get("voice_input_min_seconds", 0.6)),
        min_chars=int(cfg.get("voice_input_min_chars", 2)),
    )
    # 接收端没有自己的开关热键：常开，中枢的语音输入总开关才是主闸。
    if not voice.enabled:
        voice.set_enabled(True)
    return voice


def make_handler(voice: VoiceInput):
    """返回一个绑定 voice 的 Handler 类。锁住 offer：中枢串行发，这里只是加一道保险。"""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                text = str(body.get("text", ""))
                dur = float(body.get("dur", 0.0))
                with lock:
                    action = voice.offer(text, dur)
                self._reply(200, {"action": action})
            except Exception as exc:
                logging.exception("remote inject failed")
                self._reply(500, {"error": str(exc)})

        def do_GET(self):
            self._reply(200, {"ok": True})   # 健康检查，供中枢探测可达性

        def _reply(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    return Handler


def main() -> int:
    base = base_dir()
    cfg = load_config(base)
    remote = cfg.get("remote", {})
    host = str(remote.get("listen", "0.0.0.0"))
    port = int(remote.get("port", 8391))

    voice = build_voice(cfg, os.path.join(base, "state.json"))
    srv = ThreadingHTTPServer((host, port), make_handler(voice))
    logging.info("remote receiver listening on %s:%d (config %s)",
                 host, port, os.path.join(base, "config.json"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
