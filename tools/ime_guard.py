"""输入法急救：常驻一个只有消息循环的小助手，按 Ctrl+Alt+I 修当前窗口。

为什么需要常驻：Windows 的 .lnk「快捷键」要靠 Explorer 注册，实测这台机器上
注册了也不生效（RegisterHotKey 仍返回空闲）；而 Ctrl+Alt+C 又被别的软件占了
（err=1409）。要做到「随时按一下就能修」，只能自己 RegisterHotKey。

它不轮询、不钩键盘：只是阻塞在 GetMessage 上，空闲时 CPU 为 0；收到热键才把
修复逻辑（fix_cn_input.main）跑一遍，跑完继续等。窗口在前台不变——热键不会
抢焦点，所以修的就是你正在用的那个窗口。

启动：pythonw ime_guard.py     （开机自启靠启动文件夹里的快捷方式）
停止：pythonw ime_guard.py --stop
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "ime_guard.log")
TID_FILE = os.path.join(HERE, "ime_guard.tid")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

# 按顺序试，第一个注册成功的就用它（Ctrl+Alt+I 是「输入法」的 I）
CANDIDATES = [("Ctrl+Alt+I", ord("I")), ("Ctrl+Alt+K", ord("K")),
              ("Ctrl+Alt+J", ord("J")), ("Ctrl+Alt+G", ord("G"))]

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]


def log(line: str) -> None:
    try:
        import time
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass


def single_instance() -> bool:
    """命名互斥体：重复启动直接退出，避免两个实例抢热键。"""
    h = kernel32.CreateMutexW(None, False, "Local\\MicDesktopImeGuard")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        return False
    return True


def stop_existing() -> None:
    try:
        with open(TID_FILE, encoding="utf-8") as f:
            tid = int(f.read().strip())
    except (OSError, ValueError):
        return
    user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)


def main() -> int:
    if not single_instance():
        log("another instance already running, exit")
        return 0

    tid = int(kernel32.GetCurrentThreadId())
    with open(TID_FILE, "w", encoding="utf-8") as f:
        f.write(str(tid))

    chosen = None
    hk_id = 1
    for name, vk in CANDIDATES:
        if user32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
            chosen = name
            break
        log("register %s failed err=%d" % (name, ctypes.get_last_error()))
    if not chosen:
        log("no hotkey available, exit")
        return 1
    log("guard started tid=%d hotkey=%s" % (tid, chosen))

    msg = wt.MSG()
    while True:
        got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if got == 0 or got == -1:
            break
        if msg.message == WM_HOTKEY:
            try:
                import fix_cn_input
                fix_cn_input.main()          # 目标 = 当前前台窗口
            except Exception:
                import logging
                logging.exception("repair failed")
                log("repair crashed, see stdout")
    user32.UnregisterHotKey(None, hk_id)
    log("guard stopped")
    try:
        os.remove(TID_FILE)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop_existing()
        sys.exit(0)
    sys.exit(main())
