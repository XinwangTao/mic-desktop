"""跨机语音输入——中枢侧：目标状态 + 文字转发。

目标两种状态：local（本机上屏，默认）/ remote（经局域网发给第二台接收端）。
全局热键在两者之间切换。remote 转发失败由 main.py 回本机兜底，不丢字。
目标状态独立存 target.json，不塞进 voice_input 的 state.json（那边只写
voice_input 键，混放会被覆盖）。
"""
import json
import logging
import os
import urllib.error
import urllib.request

DEFAULT_PORT = 8391


class RemoteTarget:
    def __init__(self, state_path, host="", port=DEFAULT_PORT, timeout=2.0,
                 on_status=None):
        self.state_path = state_path
        self.host = host or ""
        self.port = int(port)
        self.timeout = float(timeout)
        self.on_status = on_status or (lambda _s: None)
        self.remote = False
        self._load()

    # ---------- 状态 ----------
    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                self.remote = bool(json.load(f).get("remote", False))
        except (OSError, ValueError):
            self.remote = False

    def _save(self):
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"remote": self.remote}, f)
            os.replace(tmp, self.state_path)
        except OSError:
            logging.exception("保存转发目标状态失败")

    @property
    def is_remote(self) -> bool:
        return self.remote

    @property
    def configured(self) -> bool:
        return bool(self.host)

    def toggle(self):
        self.remote = not self.remote
        self._save()
        if self.remote and not self.host:
            self.on_status("目标已切到第二台，但未配置 IP（config.json 的 remote.host），"
                           "转发会回退本机")
        else:
            self.on_status("语音输入目标：第二台" if self.remote else "语音输入目标：本机")

    # ---------- 转发 ----------
    def forward(self, text: str, dur: float) -> bool:
        """把转写文字 POST 给第二台接收端。成功返回 True，失败（含未配置）返回 False。"""
        if not self.host:
            return False
        payload = json.dumps({"text": text, "dur": dur}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://%s:%d/inject" % (self.host, self.port),
            data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError) as exc:
            logging.info("forward to %s:%d failed: %s", self.host, self.port, exc)
            return False
