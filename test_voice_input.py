"""语音输入的闸门链自检：真注入到一个 QPlainTextEdit 窗口，逐条验行为。

覆盖：状态落盘/恢复、正常上屏、重复、字数太少、时长太短、刹车、暂停恢复、
焦点在本应用时不注入、前台是终端时不注入、说「回车」真的按回车。
跑法：python test_voice_input.py（全过退出码 0）。

注意：SendInput 注入的是窗口消息，必须让 Qt 事件循环转一圈才会进控件。
所以每一步用一个定时器隔开，不能在一次回调里连着做完。
"""
import ctypes
import os
import sys
import tempfile

sys.path.insert(0, r"F:\projects\mic-desktop")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QVBoxLayout, QWidget  # noqa: E402

from micapp import typer  # noqa: E402
from micapp.voice_input import VoiceInput  # noqa: E402

TEXT = "你好，语音输入测试：第七车间 26 度。"
TEXT2 = "恢复之后的一句话。"


def force_foreground(hwnd):
    """破 Windows 前台锁：临时把本线程输入队列嫁接到当前前台窗口线程。"""
    u = ctypes.WinDLL("user32")
    k = ctypes.WinDLL("kernel32")
    fg = typer.foreground_handle()
    fg_thread = u.GetWindowThreadProcessId(fg, None) if fg else 0
    my_thread = k.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(u.AttachThreadInput(my_thread, fg_thread, True))
    u.BringWindowToTop(hwnd)
    u.SetForegroundWindow(hwnd)
    u.SetFocus(hwnd)
    if attached:
        u.AttachThreadInput(my_thread, fg_thread, False)
    return typer.foreground_handle() == hwnd


def main():
    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.setWindowTitle("voice input probe")
    lay = QVBoxLayout(w)
    edit = QPlainTextEdit()          # 纯文本控件：回车才能真的插一个换行
    lay.addWidget(edit)
    w.resize(560, 160)
    w.show()
    w.raise_()
    w.activateWindow()

    tmpdir = tempfile.mkdtemp(prefix="vi-test-")
    state_path = os.path.join(tmpdir, "state.json")
    checks = []
    states = []
    flags = {"playing": False, "self_fg": False, "risky": False}
    t = [1000.0]                     # 假时钟，方便把「刹车」造出来
    vi = VoiceInput(
        on_status=lambda s: None,
        on_state=lambda on, paused: states.append((on, paused)),
        is_self_foreground=lambda: flags["self_fg"],
        is_playing=lambda: flags["playing"],
        is_risky_target=lambda: flags["risky"],
        state_path=state_path,
        clock=lambda: t[0],
    )

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    def text():
        return edit.toPlainText()

    def reload_enabled():
        return VoiceInput(lambda s: None, lambda a, b: None, lambda: False,
                          lambda: False, state_path=state_path).enabled

    # 放行模式实例：同一套闸门，但 allow_console=True
    vi_console = VoiceInput(
        on_status=lambda s: None,
        on_state=lambda on, paused: None,
        is_self_foreground=lambda: False,
        is_playing=lambda: False,
        is_risky_target=lambda: True,
        state_path=os.path.join(tmpdir, "state2.json"),
        clock=lambda: t[0],
        allow_console=True,
    )
    vi_console.set_enabled(True)

    offer_log = []
    brake_base = [None]

    def brake_offer(sentence):
        # 刹车专测：三句挤在 2 秒内。基准时间第一次才算，避免被每步 +3 秒冲掉
        if brake_base[0] is None:
            brake_base[0] = t[0] + 5.0
        brake_base[0] += 0.5
        t[0] = brake_base[0]
        offer_log.append(vi.offer(sentence, 2.0))

    # 每一步是 (假前提, 动作)；假前提在跑这一步之前置位，跑完统一清掉
    steps = [
        (None, lambda: check("抢到前台焦点", force_foreground(int(w.winId())))),
        (None, lambda: check("初始为关", vi.enabled is False)),
        (None, lambda: vi.set_enabled(True)),
        (None, lambda: check("开启后落盘", os.path.exists(state_path))),
        (None, lambda: check("开启回调", states[-1] == (True, False), repr(states[-1]))),
        (None, lambda: edit.clear()),
        (None, lambda: check("正常上屏 -> typed", vi.offer(TEXT, 2.0) == "typed")),
        (None, lambda: check("窗口内容一致", text() == TEXT, repr(text()))),
        (None, lambda: check("同一句重复 -> dup", vi.offer(TEXT, 2.0) == "dup")),
        (None, lambda: check("字数太少 -> too_few", vi.offer("嗯", 2.0) == "too_few")),
        (None, lambda: check("时长太短 -> too_short", vi.offer("这是一句很长的话", 0.3) == "too_short")),
        ("playing", lambda: check("回读播放中 -> playing",
                                  vi.offer("板子念的时候说的话", 2.0) == "playing")),
        (None, lambda: edit.clear()),
        ("self_fg", lambda: check("焦点在本应用 -> own_window",
                                  vi.offer("焦点在应用自己身上", 2.0) == "own_window")),
        (None, lambda: check("own_window 时不注入", text() == "", repr(text()))),
        ("risky", lambda: check("前台是终端 -> risky",
                                vi.offer("后台服务重启一下", 2.0) == "risky")),
        (None, lambda: check("终端里一个字都不打", text() == "", repr(text()))),
        ("risky", lambda: check("放行模式：终端里也能打字",
                                vi_console.offer("放进终端的这句话", 2.0) == "typed")),
        (None, lambda: check("放行模式：文字确实进去了",
                             text().endswith("放进终端的这句话"), repr(text()))),
        ("risky", lambda: check("放行模式：终端里回车也生效",
                                vi_console.offer("回车", 2.0) == "enter")),
        (None, lambda: check("说「回车」-> enter", vi.offer("回车", 2.0) == "enter")),
        (None, lambda: check("真的插了一个换行", text().endswith("\n"), repr(text()))),
        (None, lambda: check("句子里的「回车」不当命令", vi.offer("回车键不太好使", 2.0) == "typed")),
        (None, lambda: vi.set_enabled(True)),            # 清空计数，重造刹车条件
        (None, lambda: brake_offer("第零次不同的话")),
        (None, lambda: brake_offer("第一次不同的话")),
        (None, lambda: brake_offer("第二次不同的话")),
        (None, lambda: check("刹车前两次放行", offer_log[:2] == ["typed", "typed"], repr(offer_log))),
        (None, lambda: check("第三次触发刹车", offer_log[2:] == ["brake"], repr(offer_log))),
        (None, lambda: check("刹车后进入暂停", vi.paused is True)),
        (None, lambda: check("暂停后不再上屏", vi.offer("暂停期间的话", 2.0) == "paused")),
        (None, lambda: vi.toggle_pause()),
        (None, lambda: check("F9 恢复", vi.paused is False and vi.enabled is True)),
        (None, lambda: edit.clear()),
        (None, lambda: check("恢复后能再上屏", vi.offer(TEXT2, 2.0) == "typed")),
        (None, lambda: check("恢复后窗口内容", text() == TEXT2, repr(text()))),
        (None, lambda: check("重启后状态恢复", reload_enabled() is True)),
        (None, lambda: vi.set_enabled(False)),
        (None, lambda: check("关闭后重启仍为关", reload_enabled() is False)),
        # 顺带验类名判定的纯函数，省得只有真终端才能测
        (None, lambda: check("类名判定：conhost", typer.is_risky_window("ConsoleWindowClass"))),
        (None, lambda: check("类名判定：Windows Terminal",
                             typer.is_risky_window("CASCADIA_HOSTING_WINDOW_CLASS"))),
        (None, lambda: check("类名判定：普通窗口",
                             not typer.is_risky_window("Chrome_WidgetWin_1", "记事本"))),
        (None, lambda: check("标题带「管理员」的普通窗口不算终端",
                             not typer.is_risky_window("Chrome_WidgetWin_1", "管理员: 群聊"))),
        (None, lambda: check("提权控制台仍按类名拦",
                             typer.is_risky_window("ConsoleWindowClass", "管理员: Windows PowerShell"))),
    ]

    state = {"i": 0}

    def tick():
        i = state["i"]
        if i >= len(steps):
            timer.stop()
            app.quit()
            return
        flag, action = steps[i]
        t[0] += 3.0                  # 默认每步相隔 3 秒，正常步骤不该触发刹车
        if flag:
            flags[flag] = True
        action()
        if flag:
            flags[flag] = False
        state["i"] = i + 1

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(150)                    # 每步 150 ms，留足注入消息被处理的时间
    QTimer.singleShot(20000, app.quit)  # 兜底，别挂死
    app.exec()

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("  ok   " if ok else "  FAIL ") + name
              + (("  <- " + detail) if detail and not ok else ""))
    print("通过 %d/%d" % (len(checks) - len(failed), len(checks)))
    try:
        os.remove(state_path)
        os.rmdir(tmpdir)
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
