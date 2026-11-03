"""语音输入：把转写结果当作键盘输入，打进当前前台窗口。

常开模式下没有「每次确认」的机会，风险靠三道闸兜：
  1. 话太短（默认 0.6 秒）—— 咳嗽、敲键盘、椅子响
  2. 有效字太少（默认 2 个）—— 噪声幻觉多是一两个字
  3. 连续触发刹车（默认 2 秒内 3 次）—— 误触发或自激，暂停等人工恢复
回读互斥由调用方处理（main.py 在开启时关掉自动回读）。
"""
import json
import logging
import os
import time
from collections import deque

from micapp import typer


# 说这些词时按下回车，而不是把字打出来。只认整句，避免把句子里的
# 「回车键坏了」也变成一次回车。
# 变体是实测来的：whisper 把「回车」听成「回出」两次（huí chū / huí chē 太近），
# 多收几个近音词比事后教用户「再清楚点说」有用。
ENTER_WORDS = frozenset({
    "回车", "换行", "回车键", "换行键",
    "回出", "回撤", "回扯", "回彻", "会车",
})

# 说这些词时把「本次语音输入打进当前窗口的字」全部退格删掉。
# 只删语音打的字，不碰窗口里原有的内容；窗口换过就不删。
CLEAR_WORDS = frozenset({"清除", "清空", "清除全部", "全部清除"})

# 一次最多退格多少下：防止记账出错时把整个文档删光
CLEAR_LIMIT = 4000


def normalize(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


class VoiceInput:
    def __init__(self, on_status, on_state, is_self_foreground, is_playing, state_path,
                 min_seconds: float = 0.6, min_chars: int = 2, brake_count: int = 3,
                 brake_window_s: float = 2.0, dedupe_window: int = 3, clock=time.monotonic,
                 is_risky_target=None, allow_console: bool = False, enter_words=None,
                 clear_words=None):
        self.on_status = on_status or (lambda _s: None)
        self.on_state = on_state or (lambda _on, _paused: None)
        self.is_self_foreground = is_self_foreground or (lambda: False)
        self.is_playing = is_playing or (lambda: False)
        # 前台是终端/控制台时默认不注入：一句话识别错就可能变成半条命令。
        # allow_console=True 是用户显式开的例外，此时文字照打，但「回车」指令仍然禁用。
        self.is_risky_target = is_risky_target or (lambda: False)
        # 终端注入：config 里的 allow_console 是默认值，运行期可热键切换，切换结果落 state.json
        self._console_default = bool(allow_console)
        self.allow_console = self._console_default
        # 用户可以把「自己常被听成什么」写进 config，不用改代码
        self.enter_words = set(ENTER_WORDS) | {normalize(w) for w in (enter_words or [])}
        self.clear_words = set(CLEAR_WORDS) | {normalize(w) for w in (clear_words or [])}
        # 清除只删「语音打进来的字」：记字数 + 记打进了哪个窗口
        self._typed_chars = 0
        self._typed_hwnd = 0
        self.state_path = state_path
        self.min_seconds = min_seconds
        self.min_chars = min_chars
        self.brake_count = brake_count
        self.brake_window_s = brake_window_s
        self.clock = clock
        self._recent = deque(maxlen=max(1, dedupe_window))
        self._accepted_at = deque(maxlen=max(2, brake_count))
        self.enabled = False
        self.paused = False
        self.typed = 0
        self.skipped = 0
        self._load()

    # ---------- 状态 ----------
    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
                self.enabled = bool(data.get("voice_input", False))
                self.allow_console = bool(data.get("allow_console", self._console_default))
        except (OSError, ValueError):
            self.enabled = False
            self.allow_console = self._console_default

    def _save(self):
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"voice_input": self.enabled,
                           "allow_console": self.allow_console}, f)
            os.replace(tmp, self.state_path)
        except OSError:
            logging.exception("保存语音输入状态失败")

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        self.paused = False
        self._accepted_at.clear()
        self._typed_chars = 0      # 重新开启就当新的一段，不清上一次的账
        self._typed_hwnd = 0
        self._save()
        self.on_state(self.enabled, self.paused)
        if self.enabled:
            self.on_status("语音输入已开启：说的话会打进当前前台窗口（F9 暂停）")
        else:
            self.on_status("语音输入已关闭")

    def toggle(self):
        self.set_enabled(not self.enabled)

    def toggle_pause(self):
        if not self.enabled:
            self.set_enabled(True)
            return
        self.paused = not self.paused
        self._accepted_at.clear()
        self.on_state(self.enabled, self.paused)
        self.on_status("语音输入已暂停，按 F9 恢复" if self.paused else "语音输入已恢复")

    def toggle_console(self):
        """热键切换终端注入：开 = 终端里也打字，关 = 终端里不注入（普通窗口不受影响）。"""
        self.allow_console = not self.allow_console
        self._save()
        self.on_status("终端注入已开启" if self.allow_console else "终端注入已关闭")

    # ---------- 主入口 ----------
    def offer(self, text: str, duration_s: float) -> str:
        """一句话转写完成时调用；返回这次的动作，便于日志与测试。"""
        if not self.enabled:
            return "off"
        if self.paused:
            return "paused"
        if duration_s < self.min_seconds:
            self.skipped += 1
            return "too_short"
        norm = normalize(text)
        if len(norm) < self.min_chars:
            self.skipped += 1
            return "too_few"
        # 指令词不参与去重：同一句「回车」连说两次，是真的要按两次回车
        is_cmd = norm in self.enter_words or norm in self.clear_words
        if not is_cmd and norm in self._recent:
            self.skipped += 1
            return "dup"
        # 用户正在手动打字/操作时，别用 Ctrl+V 打断他（语音一开就打不了中文的根源：
        # 注入的粘贴顶掉了输入法正在组词的内容）。停手后再说，就正常上屏。
        if typer.user_is_typing():
            self.skipped += 1
            self.on_status("正在手动打字，这句先不上屏")
            return "typing"
        if self.is_playing():
            # 板子正在念回读：这时候上屏等于把播报打进别人的输入框
            self.skipped += 1
            return "playing"

        now = self.clock()
        self._accepted_at.append(now)
        if (len(self._accepted_at) == self.brake_count
                and now - self._accepted_at[0] < self.brake_window_s):
            self.paused = True
            self.on_state(self.enabled, self.paused)
            self.on_status("语音输入已暂停：%.0f 秒内连续 %d 次触发，疑似误触发（按 F9 恢复）"
                           % (self.brake_window_s, self.brake_count))
            return "brake"

        if self.is_self_foreground():
            self._recent.append(norm)
            self.skipped += 1
            self.on_status("焦点在本应用，这句只显示在聊天区")
            return "own_window"

        if self.is_risky_target() and not self.allow_console:
            self._recent.append(norm)
            self.skipped += 1
            self.on_status("当前窗口是终端，这句没有上屏（免得误识别变成命令）")
            return "risky"

        if norm in self.enter_words:
            # 放行终端时回车同样生效：用户明确要求「终端里也能用回车」。
            # 这也意味着终端里一句误识别的「回车」会直接执行那行命令。
            ok, reason = typer.press_enter()
            if not ok:
                self.on_status("回车没有按下去：%s" % reason)
                return "failed"
            self.typed += 1
            self.on_status("已按回车")
            return "enter"

        if norm in self.clear_words:
            return self._clear_typed()

        ok, reason = typer.type_text(text)
        self._recent.append(norm)
        if not ok:
            if typer.copy_to_clipboard(text):
                self.on_status("%s，文字已复制到剪贴板，手动 Ctrl+V" % reason)
                return "fallback"
            self.on_status("输入失败：%s" % reason)
            return "failed"
        self.typed += 1
        self._typed_chars += len(text)
        self._typed_hwnd = typer.foreground_handle()
        self.on_status("已输入：%s" % text[:24])
        return "typed"

    def _clear_typed(self) -> str:
        """说「清除」时调用：退格删掉本次语音打进当前窗口的字。"""
        if self._typed_chars <= 0:
            self.on_status("没有可清除的内容（这段还没打过字）")
            return "nothing_to_clear"
        if typer.foreground_handle() != self._typed_hwnd:
            self.on_status("当前窗口不是刚才输入的那个，没敢清除")
            return "clear_wrong_window"
        want = min(self._typed_chars, CLEAR_LIMIT)
        done, reason = typer.press_backspace(want)
        self._typed_chars = 0
        if done < want:
            self.on_status("清除中断：%s（已退格 %d 字）" % (reason, done))
            return "clear_failed"
        self.on_status("已清除刚才输入的 %d 个字" % done)
        return "cleared"
