"""全局热键：RegisterHotKey + 自己线程里的消息循环，不装全局钩子库。

RegisterHotKey 的 hWnd 传 None 时，热键消息投递到「注册它的那个线程」的队列，
所以必须和 GetMessage 在同一个线程里，并且退出时要 PostThreadMessage(WM_QUIT)。
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

VK_F9 = 0x78

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GlobalHotkey(threading.Thread):
    """按键按下时回调；注册失败不抛异常，把原因放在 error 里。"""

    def __init__(self, vk: int, on_press, name: str = "hotkey", mods: int = 0, hk_id: int = 1):
        super().__init__(daemon=True, name=name)
        self.vk = vk
        self.mods = mods
        self.hk_id = hk_id
        self.on_press = on_press
        self.ok = False
        self.error = ""
        # 不能叫 _stop：那是 threading.Thread 内部方法，覆盖后 is_alive()/join() 会炸
        self._cancel = threading.Event()
        self._thread_id = 0

    def run(self):
        self._thread_id = int(_kernel32.GetCurrentThreadId())
        # 同一进程内 id 必须互不相同，否则第二个注册覆盖/失败
        if not _user32.RegisterHotKey(None, self.hk_id, self.mods | MOD_NOREPEAT, self.vk):
            err = ctypes.get_last_error()
            self.error = "热键注册失败（err=%d，可能被别的程序占用）" % err
            logging.warning("RegisterHotKey vk=0x%02X failed err=%d", self.vk, err)
            return
        self.ok = True
        logging.info("热键已注册 id=%d vk=0x%02X mods=0x%02X", self.hk_id, self.vk, self.mods)
        msg = wt.MSG()
        while not self._cancel.is_set():
            got = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got == 0 or got == -1:
                break
            if msg.message == WM_HOTKEY:
                try:
                    self.on_press()
                except Exception:
                    logging.exception("热键回调失败")
        _user32.UnregisterHotKey(None, self.hk_id)
        self.ok = False

    def stop(self):
        self._cancel.set()
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
