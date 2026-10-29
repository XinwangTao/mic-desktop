"""自动回读：转写出一句就自动念出来。

只挂在 STT 的 on_result 一个入口上，手动「朗读」按钮那条路完全不碰。
四道闸门，任何一道拦下都只跳过这一句，不影响转写与落盘：
  1. 开关没开          → off
  2. 有效字符太少      → too_short（噪声被判成一两个字时不念）
  3. 最近念过（去重）  → dup（AEC 残留把刚念的话又转写回来时挡住自激）
  4. 刹车：连续 N 次且间隔很短 → 暂停，需手动恢复
另外朗读中来了新句子不打断，最多排队 1 条，再多的丢最新的。
"""
import threading
import time
from collections import deque

DEFAULT_MIN_CHARS = 2
DEFAULT_DEDUPE_WINDOW = 5
DEFAULT_BRAKE_COUNT = 3
DEFAULT_BRAKE_WINDOW_S = 2.0


def normalize(text: str) -> str:
    """去空白与标点，只留字母数字（中文算字母），用于去重比较。"""
    return "".join(ch for ch in text if ch.isalnum())


class Readback:
    def __init__(self, speaker, ip_getter, enabled: bool = False,
                 min_chars: int = DEFAULT_MIN_CHARS,
                 dedupe_window: int = DEFAULT_DEDUPE_WINDOW,
                 brake_count: int = DEFAULT_BRAKE_COUNT,
                 brake_window_s: float = DEFAULT_BRAKE_WINDOW_S,
                 on_status=None, clock=time.monotonic):
        self.speaker = speaker
        self.ip_getter = ip_getter
        self.enabled = enabled
        self.min_chars = min_chars
        self.dedupe_window = dedupe_window
        self.brake_count = brake_count
        self.brake_window_s = brake_window_s
        self.on_status = on_status or (lambda _s: None)
        self.clock = clock
        self._recent = deque(maxlen=max(1, dedupe_window))
        self._speak_at = deque(maxlen=max(2, brake_count))
        self._pending = None
        self.paused = False
        self.spoken = 0
        self.skipped = 0

    # ---------- 对外 ----------
    def offer(self, text: str) -> str:
        """转写结果进来；返回这次的动作，便于日志与测试。"""
        if not self.enabled:
            return "off"
        if self.paused:
            return "paused"
        norm = normalize(text)
        if len(norm) < self.min_chars:
            self.skipped += 1
            return "too_short"
        if norm in self._recent:
            self.skipped += 1
            return "dup"
        if getattr(self.speaker, "busy", False):
            self._pending = text          # 只留最新一条
            return "queued"
        return self._speak(text)

    def resume(self) -> None:
        """刹车之后手动恢复。"""
        self.paused = False
        self._speak_at.clear()

    # ---------- 内部 ----------
    def _speak(self, text: str) -> str:
        if self.paused:
            return "paused"
        ip = self.ip_getter()
        if not ip:
            self.skipped += 1
            self.on_status("未回读：板子不在线")
            return "no_ip"

        now = self.clock()
        self._speak_at.append(now)
        if (len(self._speak_at) == self.brake_count
                and now - self._speak_at[0] < self.brake_window_s):
            self.paused = True
            self.on_status("回读已暂停：疑似自激循环（%d 次 / %.1f s）"
                           % (self.brake_count, self.brake_window_s))
            return "brake"

        if not self.speaker.speak(text, ip, on_status=self.on_status, on_done=self._on_done):
            self._pending = text
            return "queued"
        self._recent.append(normalize(text))
        self.spoken += 1
        return "spoken"

    def _on_done(self, _ok):
        # on_done 在播放线程里触发，此刻 speaker.busy 仍为真；挪到线程退出后再续播
        if self._pending is None or self.paused:
            return
        text, self._pending = self._pending, None
        threading.Timer(0.05, lambda: self._speak(text)).start()
