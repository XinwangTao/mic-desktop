"""跨机语音输入的针对性自检：RemoteTarget（目标切换/持久化/转发）+ 接收端 HTTP 往返。

不碰 Qt、不碰真实注入，只验中枢转发与第二台收包这两段新逻辑。
跑法：python test_cross_pc.py（全过退出码 0）。
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, r"F:\projects\mic-desktop")

from micapp.remote_receiver import make_handler  # noqa: E402
from micapp.remote_target import RemoteTarget  # noqa: E402
from micapp.voice_input import VoiceInput  # noqa: E402


class _Echo(BaseHTTPRequestHandler):
    """记录收到的 body，回 200，供 forward() 打一个真服务器。"""
    seen = []
    lock = threading.Lock()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        with self.lock:
            _Echo.seen.append(self.rfile.read(n))
        data = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class _FakeVoice:
    """替身：只记录 offer，不真注入。"""
    def __init__(self):
        self.calls = []
        self.enabled = True

    def offer(self, text, dur):
        self.calls.append((text, dur))
        return "typed"


def test_target():
    checks = []
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "target.json")
        status = []
        t = RemoteTarget(state, host="", on_status=status.append)
        checks.append(("默认是本机", t.is_remote is False))
        checks.append(("未配置 host 时 configured=False", t.configured is False))
        checks.append(("未配置 host 时 forward 直接 False", t.forward("你好", 1.0) is False))

        t.toggle()
        checks.append(("toggle 后是第二台", t.is_remote is True))
        checks.append(("切到第二台但没 IP 会提示", any("未配置" in s for s in status)))

        # 持久化：重新读文件
        t2 = RemoteTarget(state, host="")
        checks.append(("状态落盘并恢复", t2.is_remote is True))

    # 转发成功 / 失败
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t3 = RemoteTarget(os.path.join(tempfile.gettempdir(), "_crosspc_%d.json" % os.getpid()),
                          host="127.0.0.1", port=port, timeout=2.0)
        checks.append(("转发成功返回 True", t3.forward("你好，第二台", 1.2) is True))
        checks.append(("服务器收到 {text,dur}", len(_Echo.seen) == 1 and
                       json.loads(_Echo.seen[0]) == {"text": "你好，第二台", "dur": 1.2}))
    finally:
        srv.shutdown()

    dead = RemoteTarget(os.path.join(tempfile.gettempdir(), "_crosspc2_%d.json" % os.getpid()),
                        host="127.0.0.1", port=1, timeout=0.5)
    checks.append(("转发到死端口返回 False", dead.forward("x", 1.0) is False))

    return checks


def test_receiver():
    checks = []
    fake = _FakeVoice()
    handler = make_handler(fake)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        payload = json.dumps({"text": "今天开会", "dur": 2.5}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:%d/inject" % port, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            body = json.loads(resp.read())
        checks.append(("接收端回 200 且 action=typed", body.get("action") == "typed"))
        checks.append(("接收端把 {text,dur} 交给 voice.offer",
                       fake.calls == [("今天开会", 2.5)]))
        # 健康检查
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2.0) as resp:
            hb = json.loads(resp.read())
        checks.append(("GET 健康检查回 ok", hb.get("ok") is True))
    finally:
        srv.shutdown()

    return checks


def _mk_voice(state_path, allow_console=False):
    return VoiceInput(
        on_status=lambda s: None,
        on_state=lambda a, b: None,
        is_self_foreground=lambda: False,
        is_playing=lambda: False,
        state_path=state_path,
        allow_console=allow_console,
    )


def test_console_toggle():
    checks = []
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "state.json")
        v = _mk_voice(sp, allow_console=False)
        checks.append(("默认终端注入关", v.allow_console is False))

        v.toggle_console()
        checks.append(("toggle 后终端注入开", v.allow_console is True))

        # 重新加载：持久化应恢复「开」
        v2 = _mk_voice(sp, allow_console=False)
        checks.append(("终端注入状态落盘并恢复", v2.allow_console is True))
    return checks


def test_typing_guard():
    """用户在手动打字时，offer 应返回 typing 而不注入（就是「语音一开打不了中文」的解）。"""
    import micapp.voice_input as vi
    checks = []
    with tempfile.TemporaryDirectory() as d:
        v = _mk_voice(os.path.join(d, "state.json"))
        v.set_enabled(True)
        orig = vi.typer.user_is_typing
        try:
            vi.typer.user_is_typing = lambda *a, **k: True
            checks.append(("正在打字时 offer 返回 typing", v.offer("你好世界", 1.0) == "typing"))
        finally:
            vi.typer.user_is_typing = orig
    return checks


def main():
    checks = test_target() + test_receiver() + test_console_toggle() + test_typing_guard()
    failed = [c for c in checks if not c[1]]
    for name, ok in checks:
        print(("  ok   " if ok else "  FAIL ") + name)
    print("通过 %d/%d" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
