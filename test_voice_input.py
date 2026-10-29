"""语音输入的闸门链自检：真注入到一个 QLineEdit 窗口，逐条验行为。

覆盖：状态落盘/恢复、正常上屏、重复、字数太少、时长太短、刹车、暂停恢复、
焦点在本应用时不注入。跑法：python test_voice_input.py（全过退出码 0）。

注意：SendInput 注入的是窗口消息，必须让 Qt 事件循环转一圈才会进控件。
所以每一步用一个定时器隔开，不能在一次回调里连着做完。
"""
import ctypes
import os
import sys
import tempfile

sys.path.insert(0, r"F:\projects\mic-desktop")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget  # noqa: E402

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
    edit = QLineEdit()
    lay.addWidget(edit)
    w.resize(560, 80)
    w.show()
    w.raise_()
    w.activateWindow()

    tmpdir = tempfile.mkdtemp(prefix="vi-test-")
    state_path = os.path.join(tmpdir, "state.json")
    checks = []
    states = []
    flags = {"playing": False, "self_fg": False}
    t = [1000.0]                        # 假时钟，方便把「刹车」造出来
    vi = VoiceInput(
        on_status=lambda s: None,
        on_state=lambda on, paused: states.append((on, paused)),
        is_self_foreground=lambda: flags["self_fg"],
        is_playing=lambda: flags["playing"],
        state_path=state_path,
        clock=lambda: t[0],
    )

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    def grab():
        check("抢到前台焦点", force_foreground(int(w.winId())))

    def clear():
        edit.clear()

    def reload_enabled():
        return VoiceInput(lambda s: None, lambda a, b: None, lambda: False,
                          lambda: False, state_path=state_path).enabled

    steps = [
        grab,
        lambda: check("初始为关", vi.enabled is False),
        lambda: vi.set_enabled(True),
        lambda: check("开启后落盘", os.path.exists(state_path)),
        lambda: check("开启回调", states[-1] == (True, False), repr(states[-1])),
        clear,
        lambda: check("正常上屏 -> typed", vi.offer(TEXT, 2.0) == "typed"),
        lambda: check("窗口内容一致", edit.text() == TEXT, repr(edit.text())),
        lambda: check("同一句重复 -> dup", vi.offer(TEXT, 2.0) == "dup"),
        lambda: check("字数太少 -> too_few", vi.offer("嗯", 2.0) == "too_few"),
        lambda: check("时长太短 -> too_short", vi.offer("这是一句很长的话", 0.3) == "too_short"),
        lambda: check("回读播放中 -> playing", vi.offer("板子念的时候说的话", 2.0) == "playing"),
        clear,
        lambda: check("焦点在本应用 -> own_window",
                      vi.offer("焦点在应用自己身上", 2.0) == "own_window"),
        lambda: check("own_window 时不注入", edit.text() == "", repr(edit.text())),
        lambda: vi.set_enabled(True),                       # 清空计数，重造刹车条件
        lambda: brake_offer("第零次不同的话"),
        lambda: brake_offer("第一次不同的话"),
        lambda: brake_offer("第二次不同的话"),
        lambda: check("刹车前两次放行", offer_log[:2] == ["typed", "typed"], repr(offer_log)),
        lambda: check("第三次触发刹车", offer_log[2:] == ["brake"], repr(offer_log)),
        lambda: check("刹车后进入暂停", vi.paused is True),
        lambda: check("暂停后不再上屏", vi.offer("暂停期间的话", 2.0) == "paused"),
        lambda: vi.toggle_pause(),
        lambda: check("F9 恢复", vi.paused is False and vi.enabled is True),
        clear,
        lambda: check("恢复后能再上屏", vi.offer(TEXT2, 2.0) == "typed"),
        lambda: check("恢复后窗口内容", edit.text() == TEXT2, repr(edit.text())),
        lambda: check("重启后状态恢复", reload_enabled() is True),
        lambda: vi.set_enabled(False),
        lambda: check("关闭后重启仍为关", reload_enabled() is False),
    ]
    offer_log = []

    def brake_offer(text):
        t[0] += 0.5                      # 假时钟推开 0.5 秒，落在刹车窗口内
        offer_log.append(vi.offer(text, 2.0))

    state = {"i": 0}

    def tick():
        i = state["i"]
        if i < len(steps):
            # 有些步骤需要在「带标志」的情况下调用，统一在这里前后清标志
            flags["playing"] = i == 11
            flags["self_fg"] = i == 13
            steps[i]()
            flags["playing"] = flags["self_fg"] = False
            state["i"] = i + 1
            return
        timer.stop()
        app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(150)                    # 每步 150 ms，留足注入消息被处理的时间
    QTimer.singleShot(15000, app.quit)  # 兜底，别挂死
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
