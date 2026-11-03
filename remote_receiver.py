"""第二台接收端启动脚本（无界面）：python remote_receiver.py

在第二台电脑上跑，收中枢转好的文字打进本机前台窗口。见 micapp/remote_receiver.py。
"""
from micapp.remote_receiver import main

if __name__ == "__main__":
    raise SystemExit(main())
