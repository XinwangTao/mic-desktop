"""把「当前这个窗口」的输入法切回中文档，并且是「读状态 -> 动作 -> 验证」的确定性流程。

为什么需要它：中文输入法普遍把「单独按一下 Shift」当中英文切换（豆包输入法
languageSwitchKey=shift）。只要有一次裸 Shift 落进窗口（旧版 MicDesktop 的
Shift+F9 热键被 RegisterHotKey 吞掉功能键后就是这种情况），那个窗口就停在英文档，
打拼音只出英文字母，而且软件关掉也不会自动恢复——状态是按窗口/线程记的。

判定顺序（越靠前越准）：
  1. 普通程序（IMM32）：直接读 IME 上下文里的 conversion status，能读也能写；
  2. 终端这类 TSF 程序：读不到上下文，改读豆包状态条上的「中/英」字形
     （状态条跟随当前焦点窗口，2026-10-31 实测）；读完再切、切完再读一次验证；
  3. 什么都读不到：退回盲发一次裸 Shift，并在提示里说清楚不确定。

用 pythonw 运行：没有黑窗口、不抢焦点。桌面快捷方式「修复中文输入」绑了 Ctrl+Alt+C，
按快捷键启动时前台窗口不变，拿到的就是你要修的那个窗口。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
imm32 = ctypes.WinDLL("imm32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
IME_CMODE_NATIVE = 0x0001
VK_SHIFT = 0x10
VK_CTRL = 0x11
VK_SPACE = 0x20

# L/R Ctrl Shift Alt Win
MODIFIER_VKS = (0xA2, 0xA3, 0xA0, 0xA1, 0xA4, 0xA5, 0x5B, 0x5C)

HERE = os.path.dirname(os.path.abspath(__file__))
REF_PATH = os.path.join(HERE, "ime_statusbar_ref.json")


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetTopWindow.restype = wt.HWND
user32.GetTopWindow.argtypes = [wt.HWND]
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HANDLE
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HANDLE
gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HANDLE, wt.UINT, wt.UINT, ctypes.c_void_p,
                            ctypes.POINTER(_BITMAPINFOHEADER), wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.DeleteDC.argtypes = [wt.HDC]
imm32.ImmGetContext.restype = ctypes.c_void_p
imm32.ImmGetContext.argtypes = [wt.HWND]
imm32.ImmGetConversionStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)]
imm32.ImmSetConversionStatus.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD]
imm32.ImmGetOpenStatus.argtypes = [ctypes.c_void_p]
imm32.ImmSetOpenStatus.argtypes = [ctypes.c_void_p, wt.BOOL]
imm32.ImmReleaseContext.argtypes = [wt.HWND, ctypes.c_void_p]


def _btn(vk, flags):
    ev = _INPUT(type=INPUT_KEYBOARD)
    ev.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    return ev


def release_modifiers() -> None:
    seq = [_btn(vk, KEYEVENTF_KEYUP) for vk in MODIFIER_VKS]
    arr = (_INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), arr, ctypes.sizeof(_INPUT))


def ctrl_space() -> None:
    """Ctrl+空格：Windows 的「开/关输入法」。

    输入法被关闭时，conversion 仍然是中文、状态条却显示英文，打出来的全是字母——
    这种情况只有它能打开（Shift 只会切中英，开不了）。
    """
    seq = (_btn(VK_CTRL, 0), _btn(VK_SPACE, 0),
           _btn(VK_SPACE, KEYEVENTF_KEYUP), _btn(VK_CTRL, KEYEVENTF_KEYUP))
    arr = (_INPUT * 4)(*seq)
    user32.SendInput(4, arr, ctypes.sizeof(_INPUT))


def _pump(ms: int) -> None:
    """抽一小会儿消息。实测：注入 Shift 之后不抽消息，输入法不会真的翻档。"""
    import time
    msg = wt.MSG()
    end = time.time() + ms / 1000.0
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)


def shift_tap() -> None:
    """一次裸 Shift：中文输入法拿它当中英文切换。"""
    seq = (_btn(VK_SHIFT, 0), _btn(VK_SHIFT, KEYEVENTF_KEYUP))
    arr = (_INPUT * 2)(*seq)
    user32.SendInput(2, arr, ctypes.sizeof(_INPUT))


def window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


# 桌面/任务栏本身：点桌面图标时前台会变成它们，不能拿它们当目标
SHELL_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow", "TaskListThumbnailWnd",
})


def _is_shell(hwnd) -> bool:
    return window_class(hwnd) in SHELL_CLASSES


def _top_non_shell_window():
    """按 Z 序从上往下找第一个「像正常窗口」的：可见、有标题、没最小化。

    点桌面图标会把桌面推到前台，Z 序里紧跟着的通常就是用户刚才在用的窗口——
    这是这类小工具唯一能拿到的「上一个活跃窗口」线索。
    """
    GW_HWNDNEXT = 2
    GWL_STYLE = -16
    WS_MINIMIZE = 0x20000000
    hwnd = user32.GetTopWindow(None)
    while hwnd:
        if user32.IsWindowVisible(hwnd) and window_title(hwnd) and not _is_shell(hwnd):
            if not (user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_MINIMIZE):
                return hwnd
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    return None


# ---------------- 豆包状态条：读「中/英」 ----------------

def _grab(x, y, w, h) -> bytes:
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020)
    buf = ctypes.create_string_buffer(w * h * 4)
    bi = _BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    return buf.raw


def _find_status_bar():
    """返回豆包状态条窗口的 (x, y, w, h)；找不到返回 None。

    状态条是 OimeDirectUIWindow 里那个 100~300 × 25~80 的小横条，取最靠右下的那个
    （候选词窗口更大、设置窗口在中间）。
    """
    found = []

    def cb(h, _l):
        if window_class(h) != "OimeDirectUIWindow":
            return True
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        w, hh = r.right - r.left, r.bottom - r.top
        if 100 <= w <= 300 and 25 <= hh <= 80:
            found.append((r.left, r.top, w, hh))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        return None
    return sorted(found, key=lambda t: -(t[0] + t[1]))[0]


def _load_refs():
    try:
        with open(REF_PATH, encoding="utf-8") as f:
            meta = json.load(f)
        ox, oy = meta["offset"]
        cw, ch = meta["size"]
        zh = open(os.path.join(HERE, meta["zh"]), "rb").read()
        en = open(os.path.join(HERE, meta["en"]), "rb").read()
        return ox, oy, cw, ch, zh, en
    except (OSError, ValueError, KeyError):
        return None


def _cell(rect, ox, oy, cw, ch) -> bytes:
    x, y = rect[0] + ox, rect[1] + oy
    return _grab(x, y, cw, ch)


def _dist(a: bytes, b: bytes) -> float:
    n = min(len(a), len(b)) // 4
    if not n:
        return 1e9
    total = 0
    for i in range(n):
        j = i * 4
        total += abs(a[j] - b[j]) + abs(a[j + 1] - b[j + 1]) + abs(a[j + 2] - b[j + 2])
    return total / (n * 3.0)


def read_mode(rect, refs):
    """从状态条读出 '中' / '英'；读不准返回 None。"""
    if not rect or not refs:
        return None
    ox, oy, cw, ch, zh_ref, en_ref = refs
    cur = _cell(rect, ox, oy, cw, ch)
    d_zh = _dist(cur, zh_ref)
    d_en = _dist(cur, en_ref)
    if min(d_zh, d_en) > 40:        # 差太多说明状态条位置/主题变了，不敢下结论
        return None
    return "中" if d_zh <= d_en else "英"


# ---------------- 提权检测与日志 ----------------

def _integrity_rid(pid: int):
    """返回进程完整性级别 RID（0x2000=Medium，0x3000=High/管理员）；打不开返回 None。"""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        wt.DWORD, ctypes.POINTER(wt.DWORD)]
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    tok = wt.HANDLE()
    try:
        if not adv.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        buf = ctypes.create_string_buffer(64)
        need = wt.DWORD()
        if not adv.GetTokenInformation(tok, 25, buf, 64, ctypes.byref(need)):
            return None
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        sub = ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte))[1]
        return ctypes.cast(sid, ctypes.POINTER(ctypes.c_uint))[2 + (sub - 1)]
    finally:
        if tok:
            ctypes.WinDLL("kernel32").CloseHandle(tok)
        ctypes.WinDLL("kernel32").CloseHandle(h)


LOG_PATH = os.path.join(HERE, "fix_cn_input.log")


def _log(line: str) -> None:
    try:
        import time as _t
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (_t.strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass


# ---------------- 三条路 ----------------

def _via_imm(hwnd, my_tid):
    """普通程序：直接读/写 IME 上下文。返回提示文字，读不到返回 None。"""
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = bool(user32.AttachThreadInput(my_tid, tid, True))
    himc = None
    try:
        himc = imm32.ImmGetContext(hwnd)
        if not himc:
            return None
        conv = wt.DWORD()
        sent = wt.DWORD()
        imm32.ImmGetConversionStatus(himc, ctypes.byref(conv), ctypes.byref(sent))
        opened = bool(imm32.ImmGetOpenStatus(himc))
        native = bool(conv.value & IME_CMODE_NATIVE)
        title = window_title(hwnd) or "当前窗口"
        # 注意：输入法「关闭」时 conversion 仍然是中文，只出字母——必须看 OpenStatus
        if opened and native:
            return "已经是中文档：%s" % title
        if not opened:
            imm32.ImmSetOpenStatus(himc, True)
        if not native:
            imm32.ImmSetConversionStatus(himc, conv.value | IME_CMODE_NATIVE, sent.value)
        c2 = wt.DWORD()
        s2 = wt.DWORD()
        imm32.ImmGetConversionStatus(himc, ctypes.byref(c2), ctypes.byref(s2))
        ok = bool(imm32.ImmGetOpenStatus(himc)) and bool(c2.value & IME_CMODE_NATIVE)
        if ok:
            why = "输入法被关闭了（不是英文档）" if not opened else "英文档"
            return "已恢复中文输入（原来是%s）：%s" % (why, title)
        return "读到了状态但写不进去（该窗口不让改），请手动按一下 Shift"
    finally:
        if himc:
            imm32.ImmReleaseContext(hwnd, himc)
        if attached:
            user32.AttachThreadInput(my_tid, tid, False)


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND),
                ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]


user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(_GUITHREADINFO)]
user32.SetFocus.argtypes = [wt.HWND]


def _focused_child(hwnd):
    """目标窗口里当前有键盘焦点的那个子控件（终端里的输入框/编辑控件）。

    只把顶层窗口切到前台不够：焦点还在顶层窗口上，Ctrl+空格 落不到输入框里，
    输入法状态不会变。所以先记下焦点子窗口，切回前台后再把它设回焦点。
    """
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    gti = _GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
    if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)) and gti.hwndFocus:
        return gti.hwndFocus
    return hwnd


def _focus(hwnd, child=None) -> bool:
    """把窗口重新切回前台。

    必须做：追问对话框弹出后焦点到了对话框上，这时候发 Ctrl+空格 会打进对话框，
    目标窗口一点没变。热键本身不抢焦点，但 MessageBox 会。
    """
    if user32.GetForegroundWindow() == hwnd:
        return True
    my = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    ft = user32.GetWindowThreadProcessId(fg, None)
    tt = user32.GetWindowThreadProcessId(hwnd, None)
    a1 = bool(user32.AttachThreadInput(my, ft, True))
    a2 = bool(user32.AttachThreadInput(my, tt, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if child and child != hwnd:
        user32.SetFocus(child)
    if a2:
        user32.AttachThreadInput(my, tt, False)
    if a1:
        user32.AttachThreadInput(my, ft, False)
    _pump(250)
    return user32.GetForegroundWindow() == hwnd


def _via_status_bar(hwnd, refs):
    """终端这类 TSF 程序：读状态条 -> 逐个尝试恢复手段 -> 每步都再读一次验证。

    两种坏法长得像、修法不同：
      · 被切到英文档          -> 裸 Shift
      · 输入法被「关闭」(Ctrl+空格) -> Ctrl+空格 打开
    读不到真实状态，所以按顺序试，读到「中」就停。
    """
    title = window_title(hwnd) or "当前窗口"
    if read_mode(_find_status_bar(), refs) is None:
        return None
    if read_mode(_find_status_bar(), refs) == "中":
        # 状态条是「中」还来修，说明多半是「输入法被关闭」：conversion 仍是中文、
        # 状态条照样显示「中」，但按键全部透传成字母（2026-10-31 复现）。
        # 这种只能靠 Ctrl+空格 打开，Shift 没用。但终端里读不到开/关，
        # 盲按会把本来正常的输入法关掉——所以先问一句，按用户的回答决定。
        if not _ask("这个窗口打拼音时，出来的还是英文字母吗？\n\n"
                    "· 「是」→ 我按一次 Ctrl+空格 把输入法打开（被关闭时它会显示「中」但只出字母）\n"
                    "· 「否」→ 什么都不做"):
            return "没有改动：按你的回答，输入法状态正常。\n\n窗口：%s" % title
        child = _focused_child(hwnd)
        focused = _focus(hwnd, child)   # 对话框抢走了焦点，必须先把目标窗口和输入框切回来
        if not focused:
            return ("没能在发送前把焦点切回该窗口，为了不把 Ctrl+空格 打进别的窗口，"
                    "这里先不动。请在那个窗口里手动按一次 Ctrl+空格。\n\n窗口：%s" % title)
        ctrl_space()
        _pump(400)
        return ("已按一次 Ctrl+空格（输入法被关闭时只有它能打开，Shift 不行）：\n"
                "· 现在能打拼音了 → 完事\n"
                "· 还是只出字母 → 再按一次 Ctrl+空格\n"
                "· 候选出来是英文 → 按一下 Shift\n\n"
                "窗口：%s" % title)
    steps = [("按一下 Shift", shift_tap), ("Ctrl+空格", ctrl_space),
             ("再按一下 Shift", shift_tap), ("再按 Ctrl+空格", ctrl_space)]
    for i, (name, action) in enumerate(steps, 1):
        action()
        _pump(350)
        if read_mode(_find_status_bar(), refs) == "中":
            return "已恢复中文输入（第 %d 步「%s」生效，状态条已验证「中」）：%s" % (i, name, title)
    return ("试了 4 种办法状态条还是「英」：%s\n\n"
            "这属于输入法服务掉线，不是档位问题。退出豆包输入法再打开，或重开一个终端窗口。" % title)


def main(target=None) -> int:
    my_tid = kernel32.GetCurrentThreadId()
    if target is None:
        hwnd = user32.GetForegroundWindow()
        if not hwnd or _is_shell(hwnd):
            hwnd = _top_non_shell_window()
            if not hwnd:
                return _say("没找到要修的窗口", "先把光标点进打不出中文的那个窗口，再按 Ctrl+Alt+C。")
        target_pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
        if target_pid.value == kernel32.GetCurrentProcessId():
            return _say("目标是本工具自己", "请先点进要修复的窗口（终端/编辑器/聊天窗），再按 Ctrl+Alt+C。")
        foreground = user32.GetForegroundWindow()
    else:
        hwnd = target        # 测试用：直接指定窗口
        foreground = hwnd

    release_modifiers()

    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rid = _integrity_rid(pid.value)
    title_txt = window_title(hwnd) or "当前窗口"
    _log("target pid=%d cls=%s title=%r rid=%s" % (
        pid.value, window_class(hwnd), title_txt, ("0x%X" % rid) if rid else "unknown"))

    if rid is not None and rid >= 0x3000:
        msg = ("这个窗口是管理员权限（High），豆包输入法跑在普通权限（Medium），"
               "系统不允许低权限输入法往高权限窗口送字——表现就是「输入法显示中文，"
               "但打拼音只出英文字母」。\n\n"
               "处理办法（二选一）：\n"
               "· 用普通权限重新打开这个程序（最常见）；\n"
               "· 或者在这个窗口里改用微软拼音（Win+空格切，它是系统级输入法，不受限制）。")
        _log("-> elevated target, cannot fix from here")
        return _say("这个窗口打不了中文是因为提权", msg)

    msg = _via_imm(hwnd, my_tid)
    if msg is None and foreground == hwnd:      # 状态条只反映当前焦点窗口，前后台一致才可信
        msg = _via_status_bar(hwnd, _load_refs())
    if msg is None:
        shift_tap()
        _pump(250)
        msg = ("读不到输入法状态，已盲发一次 Shift 切换：%s\n\n"
               "· 现在能打中文 → 完事\n"
               "· 还是不行 → 再按一次 Ctrl+Alt+C，或把窗口标题告诉我" % (window_title(hwnd) or "当前窗口"))

    _log("result: " + msg.replace("\n", " | "))
    title = "修复中文输入"
    if msg.startswith("已经是中文档"):
        title = "不用修"
    return _say(title, msg)


def _say(title: str, text: str) -> int:
    MB_OK = 0x0000
    MB_TOPMOST = 0x00040000
    MB_ICONINFORMATION = 0x00000040
    user32.MessageBoxW(None, text, title, MB_OK | MB_TOPMOST | MB_ICONINFORMATION)
    return 0


def _ask(text: str, title: str = "修复中文输入") -> bool:
    """是/否对话框；测试里可以替换掉。"""
    MB_YESNO = 0x00000004
    MB_TOPMOST = 0x00040000
    MB_ICONQUESTION = 0x00000020
    return user32.MessageBoxW(None, text, title,
                             MB_YESNO | MB_TOPMOST | MB_ICONQUESTION) == 6   # IDYES


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:      # pythonw 下没有控制台，异常必须弹出来
        _say("修复中文输入：出错了", "%s: %s" % (type(exc).__name__, exc))
        raise
