"""首启向导：新电脑第一次跑 MicDesktop，把基础功能的两个前置条件配完——
蓝牙连上板子、转写引擎可用。对应图 mic-desktop-first-run-setup。

约束：
- 未配置才弹（needs_setup）；已配置机器永不打扰（老机器静默迁移）
- 「试说一句」真转写成功才允许完成；失败回对应步骤，已填内容不丢
- 只写 config.json；主界面其余功能不动
- BLE 单连接：板子同时只能伺候一台电脑
"""
import ipaddress
import asyncio
import json
import logging
import socket
import threading
import time
import urllib.request
import wave
import io as _io

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPlainTextEdit, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout,
)

from micapp import config

SILENCE_1S = b"\x00\x00" * 16000
SCAN_SECONDS = 6.0
CONNECT_TIMEOUT_S = 15.0
CAPTURE_SECONDS = 4.0
PROBE_TIMEOUT_S = 25.0


# ---------- 纯逻辑（单测覆盖） ----------

def needs_setup(cfg: dict) -> bool:
    """未完成向导 且 引擎看起来也没配过 → 需要向导。"""
    if cfg.get("setup_done"):
        return False
    x = cfg.get("xfyun", {})
    if x.get("app_id") and x.get("api_key") and x.get("api_secret"):
        return False          # 云端凭据齐全：老机器静默迁移
    if cfg.get("stt_engine") == "whisper_http" and cfg.get("whisper_http", {}).get("url"):
        return False          # 本地服务地址在：老机器静默迁移
    return True


def looks_configured(cfg: dict) -> bool:
    """老机器迁移判断：引擎看起来能用就补上 setup_done，不弹窗。"""
    return not needs_setup(cfg)


def local_ip() -> str:
    """本机在局域网里的 IP（UDP connect 不发包也能拿到路由出口）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def sweep_lan(port: int = 8390, timeout: float = 0.25, workers: int = 60) -> list:
    """扫本机所在 /24 里开了 port 的主机，返回 IP 列表（不含本机回环）。"""
    ip = local_ip()
    if ip == "127.0.0.1":
        return []
    net = ipaddress.ip_network(ip + "/24", strict=False)
    hosts = [str(h) for h in net.hosts() if str(h) != ip]
    found = []
    lock = threading.Lock()

    def check(h):
        try:
            s = socket.create_connection((h, port), timeout=timeout)
            s.close()
            with lock:
                found.append(h)
        except OSError:
            pass

    threads = []
    queue = list(hosts)
    def worker():
        while True:
            try:
                h = queue.pop()
            except IndexError:
                return
            check(h)
    for _ in range(min(workers, len(queue) or 1)):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout + 1.0)
    return sorted(found)


def probe_whisper(url: str, timeout: float = 5.0) -> tuple:
    """POST 1 秒静音：HTTP 通 + 返回 JSON 就算服务在。返回 (ok, 说明)。"""
    buf = _io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(SILENCE_1S); w.close()
    try:
        req = urllib.request.Request(url, data=buf.getvalue(),
                                     headers={"Content-Type": "audio/wav"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        if "text" in data:
            return True, "服务就绪"
        return False, "返回格式不对：%s" % str(data)[:60]
    except Exception as exc:
        return False, str(exc)[:80]


# ---------- 向导界面 ----------


class SetupWizard(QDialog):
    """三步向导：连板子 → 选引擎 → 试说一句。可随时跳过。"""

    def __init__(self, cfg: dict, board_connected_name: str = "", parent=None):
        super().__init__(parent)
        self.cfg = json.loads(json.dumps(cfg))     # 深拷贝，取消不改原值
        self.board_connected_name = board_connected_name
        self.setWindowTitle("MicDesktop 初始设置")
        self.setModal(True)
        self.setMinimumSize(560, 420)

        self._scan_thread = None
        self._scan_result = []
        self._probe_thread = None
        self._probe_state = {}                     # {done, ok, text}
        self._receiver = None
        self._pcm = []

        root = QVBoxLayout(self)
        self._title = QLabel()
        self._title.setStyleSheet("font-size:15px;font-weight:650;")
        root.addWidget(self._title)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self._page0 = self._page_welcome()
        self._page1 = self._page_bluetooth()
        self._page2 = self._page_engine()
        self._page3 = self._page_probe()
        for p in (self._page0, self._page1, self._page2, self._page3):
            self.pages.addWidget(p)

        btns = QHBoxLayout()
        self.btn_skip = QPushButton("跳过向导")
        self.btn_skip.setObjectName("ghost")
        self.btn_back = QPushButton("上一步")
        self.btn_back.setObjectName("ghost")
        self.btn_next = QPushButton("下一步")
        btns.addWidget(self.btn_skip)
        btns.addStretch(1)
        btns.addWidget(self.btn_back)
        btns.addWidget(self.btn_next)
        root.addLayout(btns)

        self.btn_skip.clicked.connect(self._skip)
        self.btn_back.clicked.connect(self._back)
        self.btn_next.clicked.connect(self._next)
        self._show(0)

    # ---------- 页面构建 ----------
    def _page_welcome(self):
        w = QLabel(
            "把无线麦克风接到这台电脑，只差两步：\n\n"
            "  ① 用蓝牙连上板子（MicDesktop-XXXX）\n"
            "  ② 选一个转写引擎（说什么语言都行，本向导默认中文）\n\n"
            "最后说一句话验证，字能上屏就全部搞定。\n"
            "语音输入、回读、快捷键都随程序自带，不需要另外安装。\n\n"
            "提示：板子同一时间只能连一台电脑，如果它正连在别的机器上，请先在那边断开。")
        w.setWordWrap(True)
        w.setAlignment(Qt.AlignTop)
        return w

    def _page_bluetooth(self):
        w, lay = _vpage()
        tip = QLabel("板子已上电的话，下面直接扫描。扫不到时检查：\n"
                     "· 板子没电\n· 板子正被别的电脑连着（BLE 单连接）\n· 距离太远")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        row = QHBoxLayout()
        self.btn_scan = QPushButton("扫描板子")
        self.btn_scan.clicked.connect(self._scan)
        row.addWidget(self.btn_scan)
        self.lbl_bt = QLabel("")
        row.addWidget(self.lbl_bt, 1)
        lay.addLayout(row)
        self.device_list = QListWidget()
        lay.addWidget(self.device_list, 1)
        if self.board_connected_name:
            self.lbl_bt.setText("板子已连接：%s（不用重新扫描）" % self.board_connected_name)
        return w

    def _page_engine(self):
        w, lay = _vpage()
        self.rb_local = QRadioButton("本地 / 局域网服务（推荐，无需安装任何环境）")
        self.rb_xfyun = QRadioButton("讯飞云端（填凭据，走外网）")
        self.rb_local.setChecked(True)
        lay.addWidget(self.rb_local)
        row = QHBoxLayout()
        row.addWidget(QLabel("服务地址"))
        self.edit_url = QLineEdit(self.cfg.get("whisper_http", {}).get("url",
                                  "http://127.0.0.1:8390/stt"))
        row.addWidget(self.edit_url, 1)
        self.btn_detect = QPushButton("自动探测")
        self.btn_detect.clicked.connect(self._detect_lan)
        row.addWidget(self.btn_detect)
        lay.addLayout(row)
        self.lbl_detect = QLabel("探测会找本机与局域网里开着 8390 端口的转写服务")
        self.lbl_detect.setStyleSheet("color:#8a99a8;font-size:12px;")
        lay.addWidget(self.lbl_detect)
        lay.addSpacing(10)
        lay.addWidget(self.rb_xfyun)
        form = QHBoxLayout()
        self.ed_app = QLineEdit(self.cfg.get("xfyun", {}).get("app_id", ""))
        self.ed_key = QLineEdit(self.cfg.get("xfyun", {}).get("api_key", ""))
        self.ed_sec = QLineEdit(self.cfg.get("xfyun", {}).get("api_secret", ""))
        self.ed_app.setPlaceholderText("APPID"); self.ed_key.setPlaceholderText("APIKey")
        self.ed_sec.setPlaceholderText("APISecret"); self.ed_sec.setEchoMode(QLineEdit.Password)
        for e in (self.ed_app, self.ed_key, self.ed_sec):
            form.addWidget(e)
        lay.addLayout(form)
        lay.addStretch(1)
        return w

    def _page_probe(self):
        w, lay = _vpage()
        lay.addWidget(QLabel("最后一步：对着板子说一句话（任意内容），确认整条链路通。"))
        self.btn_probe = QPushButton("开始测试（连板子 + 录 4 秒）")
        self.btn_probe.clicked.connect(self._start_probe)
        lay.addWidget(self.btn_probe)
        self.probe_out = QPlainTextEdit()
        self.probe_out.setReadOnly(True)
        self.probe_out.setPlaceholderText("结果会显示在这里……")
        lay.addWidget(self.probe_out, 1)
        return w

    # ---------- 导航 ----------
    def _show(self, idx):
        self.pages.setCurrentIndex(idx)
        titles = ["欢迎", "① 连接板子", "② 转写引擎", "③ 试说一句"]
        self._title.setText(titles[idx])
        self.btn_back.setVisible(idx in (1, 2))
        self.btn_skip.setVisible(idx < 3)
        last = idx == 3
        self.btn_next.setText("完成" if last else "下一步")
        if idx == 1 and not self.device_list.count() and not self.board_connected_name:
            self._scan()

    def _back(self):
        self._show(max(0, self.pages.currentIndex() - 1))

    def _next(self):
        idx = self.pages.currentIndex()
        if idx == 1 and not self._bt_ok():
            return
        if idx == 2 and not self._engine_ok():
            return
        if idx == 3:
            if not self._probe_state.get("ok"):
                self.probe_out.setPlainText("先完成测试再点完成（或上一步检查配置）。")
                return
            self._save_and_close()
            return
        self._show(idx + 1)

    def _skip(self):
        # 跳过：默认值落盘但不标记完成；主界面挂黄条提醒
        self.cfg["setup_done"] = False
        config.save(self.cfg)
        self.done(0)

    def _save_and_close(self):
        self.cfg["setup_done"] = True
        self.cfg["input_mode"] = "ble"
        self.cfg["stt_engine"] = "xfyun" if self.rb_xfyun.isChecked() else "whisper_http"
        self.cfg["whisper_http"] = {"url": self.edit_url.text().strip()}
        self.cfg["xfyun"] = {"app_id": self.ed_app.text().strip(),
                             "api_key": self.ed_key.text().strip(),
                             "api_secret": self.ed_sec.text().strip()}
        config.save(self.cfg)
        self.done(1)

    # ---------- 蓝牙页 ----------
    def _bt_ok(self) -> bool:
        if self.board_connected_name:
            return True
        item = self.device_list.currentItem()
        if item is None:
            self.lbl_bt.setText("先扫描并选中一块板子")
            return False
        self.cfg["_bt_name"] = item.text()
        return True

    def _scan(self):
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self.btn_scan.setEnabled(False)
        self.lbl_bt.setText("扫描中…")
        self.device_list.clear()
        from bleak import BleakScanner

        def work():
            try:
                devs = asyncio.run(BleakScanner.discover(timeout=SCAN_SECONDS))
                names = sorted({d.name for d in devs
                                if d.name and d.name.startswith("MicDesktop-")})
            except Exception as exc:
                names = []
                self._scan_error = str(exc)[:80]
            else:
                self._scan_error = ""
            self._scan_result = names
        self._scan_thread = threading.Thread(target=work, daemon=True)
        self._scan_thread.start()

        def poll():
            if self._scan_thread.is_alive():
                return
            self.btn_scan.setEnabled(True)
            timer.stop()
            if self._scan_error:
                self.lbl_bt.setText("蓝牙扫描失败：%s（检查适配器是否开启）" % self._scan_error)
                return
            for n in self._scan_result:
                self.device_list.addItem(n)
            self.lbl_bt.setText("找到 %d 块板子" % len(self._scan_result)
                                if self._scan_result else "没扫到——看上面的三个原因")
        timer = QTimer(self)
        timer.timeout.connect(poll)
        timer.start(400)

    # ---------- 引擎页 ----------
    def _engine_ok(self) -> bool:
        if self.rb_local.isChecked():
            url = self.edit_url.text().strip()
            if not url.startswith("http"):
                self.lbl_detect.setText("地址要以 http 开头")
                return False
            return True
        if not (self.ed_app.text().strip() and self.ed_key.text().strip()
                and self.ed_sec.text().strip()):
            self.lbl_detect.setText("讯飞三个凭据都要填")
            return False
        return True

    def _detect_lan(self):
        self.btn_detect.setEnabled(False)
        self.lbl_detect.setText("探测中（约 2~3 秒）…")
        result = {}

        def work():
            hits = []
            if probe_whisper("http://127.0.0.1:8390/stt")[0]:
                hits.append("127.0.0.1")
            result["local"] = bool(hits)
            result["lan"] = sweep_lan()
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if "local" not in result:
                return
            timer.stop()
            self.btn_detect.setEnabled(True)
            tips = []
            if result["local"]:
                self.edit_url.setText("http://127.0.0.1:8390/stt")
                tips.append("本机服务已填好")
            for ip in result["lan"]:
                tips.append("局域网发现：%s" % ip)
            if not tips:
                tips.append("本机和局域网都没找到 8390 服务——可手填地址，或改选讯飞云端")
            self.lbl_detect.setText("；".join(tips))
        timer = QTimer(self)
        timer.timeout.connect(poll)
        timer.start(300)

    # ---------- 试说一句 ----------
    def _start_probe(self):
        if self._probe_thread and self._probe_thread.is_alive():
            return
        self.btn_probe.setEnabled(False)
        self.probe_out.setPlainText("连接板子并录音中（%d 秒）……请对着板子说话"
                                    % int(CAPTURE_SECONDS))
        self._probe_state = {"done": False, "ok": False, "text": ""}
        self._pcm = []

        engine_cfg = json.loads(json.dumps(self.cfg))
        use_xfyun = self.rb_xfyun.isChecked()
        engine_cfg["stt_engine"] = "xfyun" if use_xfyun else "whisper_http"
        url = self.edit_url.text().strip()

        def work():
            from micapp.ble_receiver import BleReceiver
            rec = BleReceiver(lambda pcm, flags: self._pcm.append(pcm))
            rec.start()
            t0 = time.monotonic()
            while not rec.connected and time.monotonic() - t0 < CONNECT_TIMEOUT_S:
                time.sleep(0.1)
            if not rec.connected:
                self._probe_state.update(done=True, ok=False,
                                         text="连不上板子：回上一步重新扫描（检查是否被别的电脑占用）")
                rec.stop()
                return
            time.sleep(CAPTURE_SECONDS)
            rec.stop()
            pcm = b"".join(self._pcm)
            if len(pcm) < 32000:
                self._probe_state.update(done=True, ok=False,
                                         text="没录到声音：说话时靠近板子再试一次")
                return
            try:
                if use_xfyun:
                    text = self._probe_xfyun(engine_cfg, pcm)
                else:
                    text = self._probe_whisper(url, pcm)
                self._probe_state.update(done=True, ok=bool(text), text=text or "(空结果，但链路是通的)")
            except Exception as exc:
                self._probe_state.update(done=True, ok=False, text="转写失败：%s" % str(exc)[:100])

        self._probe_thread = threading.Thread(target=work, daemon=True)
        self._probe_thread.start()

        def poll():
            st = self._probe_state
            if not st.get("done"):
                return
            timer.stop()
            self.btn_probe.setEnabled(True)
            self.probe_out.setPlainText(
                ("✅ 链路通，转写结果：「%s」\n\n可以点「完成」了。" % st["text"])
                if st["ok"] else ("❌ %s" % st["text"]))
        timer = QTimer(self)
        timer.timeout.connect(poll)
        timer.start(400)

    def _probe_whisper(self, url, pcm):
        buf = _io.BytesIO()
        w = wave.open(buf, "wb")
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm); w.close()
        req = urllib.request.Request(url, data=buf.getvalue(),
                                     headers={"Content-Type": "audio/wav"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as r:
            return json.loads(r.read()).get("text", "").strip()

    def _probe_xfyun(self, engine_cfg, pcm):
        from micapp.stt import base as stt_base
        from micapp.stt import xfyun  # noqa: F401 注册
        got = {"text": "", "done": False}
        eng = stt_base.create("xfyun", engine_cfg, lambda t, i: got.update(text=t, done=True))
        eng.on_drop = lambda i: got.update(done=True)
        eng.start()
        eng.feed_utterance(pcm)
        deadline = time.monotonic() + PROBE_TIMEOUT_S
        while not got["done"] and time.monotonic() < deadline:
            time.sleep(0.2)
        eng.stop()
        return got["text"].strip()


def _vpage():
    """返回 (widget, layout)：向导页统一竖排布局。"""
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setAlignment(Qt.AlignTop)
    return w, lay


def run_wizard(cfg: dict, board_connected_name: str = "") -> dict:
    """模态跑一遍向导。返回更新后的 cfg；用户跳过时返回带 setup_done=False 的 cfg。"""
    wiz = SetupWizard(cfg, board_connected_name=board_connected_name)
    wiz.exec()
    return wiz.cfg
