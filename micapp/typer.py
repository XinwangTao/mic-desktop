"""把文字打进当前前台窗口：SendInput 逐字送 Unicode，不激活窗口、不经过输入法。

为什么不用 keyboard / pynput：那是全局钩子，安全软件会拦，也会拖慢全系统输入。
这里只需要「注入」和「热键」两件事，user32 自带 SendInput / RegisterHotKey 就够。
"""
import ctypes
import ctypes.wintypes as wt
import logging
import time

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
ERROR_ACCESS_DENIED = 5

# use_last_error=True 才能用 ctypes.get_last_error() 读到真正的 GetLastError；
# 用默认的 ctypes.windll.user32 拿到的是过期的错误码。
_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _units(text: str):
    """按 UTF-16 码元切：BMP 外的字符（emoji）会拆成代理对，SendInput 认这个。"""
    data = text.encode("utf-16-le", "surrogatepass")
    for i in range(0, len(data), 2):
        yield data[i] | (data[i + 1] << 8)


def _events(unit: int, up: bool):
    ev = _INPUT(type=INPUT_KEYBOARD)
    ev.ki = _KEYBDINPUT(wVk=0, wScan=unit,
                        dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
                        time=0, dwExtraInfo=0)
    return ev


def type_text(text: str, gap_s: float = 0.004):
    """把 text 逐字打进前台窗口，返回 (成功?, 原因)。

    一次 SendInput 只送一个字的「按下+抬起」。实测（typing_probe.py）：
    把 16 个字的 32 个事件放进同一次调用，目标窗口每个字都变成了这一批的第一个字
    （"你好，语音输入…" 读回来是 "你你你你…"）；逐字调用则完全正确。
    所以别为了「快」再合并批，代价是每次多一次系统调用（微秒级）。
    """
    if not text:
        return False, "文字为空"
    user32 = _user32
    for unit in _units(text):
        pair = (_INPUT * 2)(_events(unit, False), _events(unit, True))
        sent = user32.SendInput(2, ctypes.byref(pair), ctypes.sizeof(_INPUT))
        if sent != 2:
            err = ctypes.get_last_error()
            logging.warning("SendInput 只送进 %d/2，err=%d", sent, err)
            if err == ERROR_ACCESS_DENIED:
                return False, "目标程序权限更高（UAC 隔离）"
            return False, "目标窗口没有接收键盘输入"
        time.sleep(gap_s)
    return True, ""


def copy_to_clipboard(text: str) -> bool:
    """兜底：SendInput 打不进去时，至少让用户能 Ctrl+V。"""
    try:
        import win32clipboard
        import win32con
    except Exception:
        logging.exception("剪贴板依赖不可用")
        return False
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        return True
    except Exception:
        logging.exception("写剪贴板失败")
        return False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def foreground_handle() -> int:
    return int(_user32.GetForegroundWindow() or 0)
