"""资源路径：源码运行与 PyInstaller onefile 打包后都能找到 assets。"""
import os
import sys

APP_NAME = "MicDesktop"
APP_TITLE = "无线麦克风接收台"
APP_VERSION = "1.2.0"


def asset_path(name: str) -> str:
    """返回 assets 下某个文件的绝对路径。

    onefile 模式下 PyInstaller 把 --add-data 的内容解到 sys._MEIPASS，
    __file__ 指向的临时目录不可靠，所以优先用 _MEIPASS。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "micapp", "assets", name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)
