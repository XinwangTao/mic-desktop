"""PySide6 主窗口：无边框深色卡片 + 自定义标题栏 + 圆角气泡。

线程安全：所有后台线程只 emit 信号，界面更新一律在 GUI 线程的槽里做。
"""
import threading
import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizeGrip, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from micapp.assets import APP_TITLE, APP_VERSION, asset_path
from micapp.config import ENGINE_LABELS

BG = "#0E1116"
CARD = "#12151C"
SURFACE = "#181D26"
SURFACE_HI = "#1F2632"
BORDER = "#232A36"
INK = "#E6EAF2"
MUTED = "#8A94A6"
ACCENT = "#4C8DFF"
OK = "#35D07F"
WARN = "#FFB020"

QSS = f"""
QWidget {{ color: {INK}; font-family: "Microsoft YaHei UI","Microsoft YaHei",sans-serif; font-size: 13px; }}
#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
#titlebar {{ background: transparent; }}
#title {{ font-size: 15px; font-weight: 600; }}
#subtitle {{ color: {MUTED}; font-size: 12px; }}
#chip {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 11px;
         padding: 3px 10px; color: {MUTED}; font-size: 12px; }}
#chipHi {{ background: rgba(76,141,255,0.14); border: 1px solid rgba(76,141,255,0.45);
           border-radius: 11px; padding: 3px 10px; color: {ACCENT}; font-size: 12px; }}
#bubble {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
#bubbleMine {{ background: rgba(76,141,255,0.16); border: 1px solid rgba(76,141,255,0.38);
               border-radius: 14px; }}
#systemMsg {{ background: transparent; border: none; }}
#bubbleText {{ font-size: 14px; }}
#stamp {{ color: {MUTED}; font-size: 11px; }}
#hint {{ color: {MUTED}; font-size: 12px; }}
#input {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
          padding: 9px 12px; selection-background-color: {ACCENT}; font-size: 14px; }}
#input:focus {{ border: 1px solid {ACCENT}; background: {SURFACE_HI}; }}
QPushButton#primary {{ background: {ACCENT}; border: none; border-radius: 10px;
                       padding: 9px 20px; color: #0B1220; font-weight: 700; }}
QPushButton#primary:hover {{ background: #6AA0FF; }}
QPushButton#primary:pressed {{ background: #3C79E0; }}
QPushButton#primary:disabled {{ background: #2A3444; color: {MUTED}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {BORDER}; border-radius: 10px;
                     padding: 6px 14px; color: {MUTED}; }}
QPushButton#ghost:hover {{ border-color: {MUTED}; color: {INK}; }}
QPushButton#ghostWarn {{ background: rgba(255,176,32,0.12); border: 1px solid rgba(255,176,32,0.45);
                         border-radius: 10px; padding: 6px 14px; color: {WARN}; }}
QPushButton#ghostWarn:hover {{ border-color: {WARN}; }}
QPushButton#ghostOn {{ background: rgba(76,141,255,0.14); border: 1px solid rgba(76,141,255,0.50);
                       border-radius: 10px; padding: 6px 14px; color: {ACCENT}; font-weight: 600; }}
QPushButton#ghostOn:hover {{ border-color: {ACCENT}; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px;
               color: {MUTED}; font-size: 15px; padding: 2px 9px; }}
QToolButton:hover {{ background: {SURFACE_HI}; color: {INK}; }}
QToolButton#close:hover {{ background: #E0483E; color: #fff; }}
QScrollArea {{ background: transparent; border: none; }}
#chatHolder {{ background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px; }}
QScrollBar::handle:vertical {{ background: #2C3543; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3B4658; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


class LevelMeter(QWidget):
    """麦克风电平：圆角轨道 + 渐变填充。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ratio = 0.0
        self._hot = False
        self.setFixedHeight(10)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_level(self, ratio: float, hot: bool):
        ratio = max(0.0, min(1.0, ratio))
        if abs(ratio - self._ratio) < 0.01 and hot == self._hot:
            return
        self._ratio, self._hot = ratio, hot
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1D2430"))
        p.drawRoundedRect(r, radius, radius)
        if self._ratio > 0.005:
            fill = QRectF(r)
            fill.setWidth(max(r.height(), r.width() * self._ratio))
            grad = QLinearGradient(fill.left(), 0, fill.right(), 0)
            if self._hot:
                grad.setColorAt(0.0, QColor(OK))
                grad.setColorAt(1.0, QColor(WARN))
            else:
                grad.setColorAt(0.0, QColor(OK))
                grad.setColorAt(1.0, QColor("#6BE3A6"))
            p.setBrush(grad)
            p.drawRoundedRect(fill, radius, radius)


class Bubble(QFrame):
    """一条转写/朗读记录。system=True 时不带时间戳，用于提示语。"""

    def __init__(self, text: str, mine: bool = False, system: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("systemMsg" if system else ("bubbleMine" if mine else "bubble"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10 if not system else 4, 14, 8 if not system else 2)
        lay.setSpacing(4)
        body = QLabel(text)
        body.setObjectName("bubbleText")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if system:
            body.setStyleSheet(f"color:{MUTED};")
        lay.addWidget(body)
        if not system:
            stamp = QLabel(("我朗读 · " if mine else "") + time.strftime("%H:%M:%S"))
            stamp.setObjectName("stamp")
            lay.addWidget(stamp)
        self.setMaximumWidth(640)


class MainWindow(QWidget):
    sig_text = Signal(str, int)
    sig_stats = Signal(object)
    sig_level = Signal(float, float, float)   # rms, 当前阈值, 底噪
    sig_status = Signal(str)
    sig_mode = Signal(str, str)
    sig_ns = Signal(bool)                     # 板子自报的降噪状态
    sig_voice = Signal(bool, bool)            # 语音输入：开启?, 已暂停?
    sig_hotkey = Signal()                     # 全局热键（在热键线程触发，转给 GUI 线程）
    sig_hotkey_toggle = Signal()              # Shift+F9：语音输入硬开关
    sig_board = Signal(str)                   # 板子发来的事件帧（BOOT 键等）

    def __init__(self, cfg: dict, receiver=None, engine=None):
        super().__init__()
        self.cfg = cfg
        self.receiver = receiver
        self.engine = engine
        self.voice = None           # 由 main.py 注入的 VoiceInput
        self._drag_from = None
        self._bubbles = 0
        self._ns_on = True          # 设备端降噪当前状态（A/B 开关）
        self._vad_text = ""

        from micapp.tts import Speaker
        self.speaker = Speaker(port=cfg.get("speaker_port", 5001),
                               voice=cfg.get("tts_voice", "Microsoft Huihui Desktop"))

        self.setWindowTitle(f"{APP_TITLE} · ESP32-S3 INMP441")
        self.setWindowIcon(QIcon(asset_path("appicon.png")))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(780, 560)
        self.resize(940, 660)
        self._build()

        self.sig_text.connect(self._on_text)
        self.sig_stats.connect(self._on_stats)
        self.sig_level.connect(self._on_level)
        self.sig_status.connect(self._on_status)
        self.sig_mode.connect(self._on_mode)
        self.sig_ns.connect(self._on_ns_state)
        self.sig_voice.connect(self._on_voice_state)
        self.sig_hotkey.connect(self._on_hotkey_voice)
        self.sig_hotkey_toggle.connect(self._on_hotkey_toggle)
        self.sig_board.connect(self._on_board_event)
        # 句柄在 GUI 线程先取好：语音输入在转写线程里判「前台是不是自己」，
        # winId() 不是线程安全的，那边只读这个缓存值。
        self._hwnd = int(self.winId())

    # ---------- 构建 ----------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        card = QFrame()
        card.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(44)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_titlebar(card))
        root.addWidget(self._build_header(card))

        self.scroll = QScrollArea(card)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        holder.setObjectName("chatHolder")
        self.chat = QVBoxLayout(holder)
        self.chat.setContentsMargins(20, 6, 20, 6)
        self.chat.setSpacing(10)
        self.chat.addStretch(1)
        self.scroll.setWidget(holder)
        self.scroll.viewport().setAutoFillBackground(False)
        holder.setAutoFillBackground(False)
        root.addWidget(self.scroll, 1)

        root.addWidget(self._build_input(card))
        root.addWidget(self._build_footer(card))

        self._append_text("等待语音输入…", muted=True)

    def _build_titlebar(self, parent):
        bar = QWidget(parent)
        bar.setObjectName("titlebar")
        bar.setFixedHeight(62)
        bar.setCursor(Qt.ArrowCursor)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 12, 12, 6)
        lay.setSpacing(12)

        # 标题栏用应用图标当头像：比一个「麦」字更像个正经软件，也和 exe 图标一致
        avatar = QLabel()
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignCenter)
        icon = QPixmap(asset_path("appicon.png"))
        if not icon.isNull():
            avatar.setPixmap(icon.scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            avatar.setText("麦")
            avatar.setStyleSheet(
                f"background:{ACCENT};color:#0B1220;border-radius:10px;font-weight:800;"
                f"font-size:15px;")
        lay.addWidget(avatar)

        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel(APP_TITLE)
        title.setObjectName("title")
        self.lbl_sub = QLabel(f"ESP32-S3 · INMP441 · v{APP_VERSION}")
        self.lbl_sub.setObjectName("subtitle")
        col.addWidget(title)
        col.addWidget(self.lbl_sub)
        lay.addLayout(col)
        lay.addStretch(1)

        btn_min = QToolButton(bar)
        btn_min.setText("–")
        btn_min.clicked.connect(self.showMinimized)
        btn_close = QToolButton(bar)
        btn_close.setObjectName("close")
        btn_close.setText("✕")
        btn_close.clicked.connect(self.close)
        lay.addWidget(btn_min)
        lay.addWidget(btn_close)
        self._titlebar = bar
        return bar

    def _build_header(self, parent):
        wrap = QWidget(parent)
        wrap.setFixedHeight(74)
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(20, 0, 20, 10)
        lay.setSpacing(8)

        self.chip_dev = QLabel("设备 未连接")
        self.chip_dev.setObjectName("chip")
        self.chip_rate = QLabel("0.0 pkt/s")
        self.chip_rate.setObjectName("chip")
        self.chip_lost = QLabel("丢包 0")
        self.chip_lost.setObjectName("chip")
        engine = ENGINE_LABELS.get(self.cfg.get("stt_engine"), self.cfg.get("stt_engine", "-"))
        self.chip_engine = QLabel(f"引擎 {engine}")
        self.chip_engine.setObjectName("chipHi")
        self.chip_mode = QLabel("模式 探测中")
        self.chip_mode.setObjectName("chip")
        for w in (self.chip_dev, self.chip_rate, self.chip_lost, self.chip_mode, self.chip_engine):
            lay.addWidget(w)
        lay.addStretch(1)
        self.lbl_vad = QLabel("底噪 – · 阈值 –")
        self.lbl_vad.setObjectName("hint")
        self.lbl_vad.setToolTip("底噪取最近 10 秒非语音帧的 P20；阈值 = 底噪 × 倍数，夹在上下限之间")
        lay.addWidget(self.lbl_vad)
        lay.addSpacing(6)
        self.meter = LevelMeter(wrap)
        lay.addWidget(self.meter, 0)
        self.meter.setFixedWidth(150)
        return wrap

    def _build_input(self, parent):
        wrap = QWidget(parent)
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(20, 8, 20, 4)
        lay.setSpacing(10)
        self.edit = QLineEdit(wrap)
        self.edit.setObjectName("input")
        self.edit.setPlaceholderText("输入文字，让板子念出来…（回车即朗读）")
        self.edit.returnPressed.connect(self._on_speak)
        self.btn_speak = QPushButton("朗读", wrap)
        self.btn_speak.setObjectName("primary")
        self.btn_speak.setCursor(Qt.PointingHandCursor)
        self.btn_speak.clicked.connect(self._on_speak)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn_speak)
        return wrap

    def _build_footer(self, parent):
        wrap = QWidget(parent)
        wrap.setFixedHeight(46)   # 36 装不下带 padding 的按钮，文字会被上下切掉
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(20, 4, 8, 6)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setObjectName("hint")
        lay.addWidget(self.lbl_status)
        lay.addStretch(1)
        self.btn_vi = QPushButton("语音输入 关", wrap)
        self.btn_vi.setObjectName("ghost")
        self.btn_vi.setCursor(Qt.PointingHandCursor)
        self.btn_vi.setToolTip("开启后，说的话会像打字一样进到当前前台窗口；F9 暂停/恢复。"
                               "开启期间自动回读会被关掉，避免板子念的话又被输入一遍")
        self.btn_vi.clicked.connect(self._toggle_voice_input)
        lay.addWidget(self.btn_vi)
        self.btn_ns = QPushButton("降噪 开", wrap)
        self.btn_ns.setObjectName("ghost")
        self.btn_ns.setCursor(Qt.PointingHandCursor)
        self.btn_ns.setToolTip("A/B 对照：关掉设备端降噪，对比上行底噪与转写结果，用来判断语音是被降噪压了还是被阈值挡了")
        self.btn_ns.clicked.connect(self._toggle_ns)
        lay.addWidget(self.btn_ns)
        btn_clear = QPushButton("清空", wrap)
        btn_clear.setObjectName("ghost")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.clicked.connect(self._clear)
        lay.addWidget(btn_clear)
        grip = QSizeGrip(wrap)
        lay.addWidget(grip, 0, Qt.AlignRight | Qt.AlignBottom)
        return wrap

    # ---------- 线程安全入口（任意线程可调） ----------
    def ui(self, kind, *args):
        if kind == "text":
            self.sig_text.emit(str(args[0]), int(args[1]) if len(args) > 1 else 0)
        elif kind == "level":
            self.sig_level.emit(float(args[0]),
                                float(args[1]) if len(args) > 1 else 0.0,
                                float(args[2]) if len(args) > 2 else 0.0)
        elif kind == "stats":
            self.sig_stats.emit(args[0])
        elif kind == "status":
            self.sig_status.emit(str(args[0]))
        elif kind == "mode":
            self.sig_mode.emit(str(args[0]), str(args[1]) if len(args) > 1 else "")
        elif kind == "voice_state":
            self.sig_voice.emit(bool(args[0]), bool(args[1]) if len(args) > 1 else False)
        elif kind == "hotkey_voice":
            self.sig_hotkey.emit()
        elif kind == "hotkey_toggle":
            self.sig_hotkey_toggle.emit()
        elif kind == "board_event":
            self.sig_board.emit(str(args[0]))

    # ---------- 槽 ----------
    def _on_text(self, text, _idx):
        self._append_text(text)

    def _on_stats(self, s):
        addr = s.get("addr", "-")
        # 蓝牙名固定是 MicDesktop-XXXX，前缀没有信息量；这一行本来就挤，
        # 直接显示全名会被挤到掉字（实测 "MicDesktop-0EB6" 显示成 "MicDesktop-0EE"）。
        prefix = self.cfg.get("ble_name_prefix", "MicDesktop-")
        short = addr[len(prefix):] if addr.startswith(prefix) else addr
        self.chip_dev.setText("设备 " + short if addr != "-" else "设备 未连接")
        self.chip_dev.setToolTip(addr if addr != "-" else "还没收到板子的音频包")
        self.chip_dev.setObjectName("chipHi" if addr != "-" else "chip")
        self.chip_dev.style().unpolish(self.chip_dev)
        self.chip_dev.style().polish(self.chip_dev)
        self.chip_rate.setText("%.1f pkt/s" % s.get("pkt_rate", 0.0))
        self.chip_lost.setText("丢包 %d · 坏帧 %d" % (s.get("lost", 0), s.get("bad", 0)))

    def _on_level(self, rms, threshold, baseline):
        """阈值是自适应的，电平条要按当前阈值归一化，不能再用 config 里的固定值。"""
        thr = float(threshold) or 1.0
        self.meter.set_level(float(rms) / (thr * 3.0), float(rms) >= thr)
        # 50 Hz 回调，只在读数变化时才碰控件，免得 layout 每秒抖 50 次
        txt = ("底噪 %d · 阈值 %d" % (round(baseline), round(thr))
               if baseline > 0 else "底噪 – · 阈值 %d" % round(thr))
        if txt != self._vad_text:
            self._vad_text = txt
            self.lbl_vad.setText(txt)

    def _on_status(self, text):
        self.lbl_status.setText(text)

    def _on_mode(self, label, reason):
        """全双工 / 半双工 + 判定依据，两者都放在看得见的地方。"""
        self.chip_mode.setText("模式 " + label)
        self.chip_mode.setObjectName("chipHi" if label.startswith("全") else "chip")
        self.chip_mode.setToolTip(reason)
        self.chip_mode.style().unpolish(self.chip_mode)
        self.chip_mode.style().polish(self.chip_mode)
        if reason:
            self.lbl_status.setText(reason)

    # ---------- 交互 ----------
    def board_ip(self) -> str:
        """当前可用的板子 IP（最近一次上行来源优先，其次 config）。自动回读要用。"""
        return self._board_ip()

    def _board_ip(self) -> str:
        if self.receiver is not None:
            addr = self.receiver.stats().get("addr", "-")
            # UDP 版是 "ip:port"；BLE 版的 addr 是设备名，没有 IP，别当成主机名去发 UDP
            if addr and addr != "-" and addr.count(":") == 1:
                return addr.split(":")[0]
        return self.cfg.get("board_ip", "") or ""

    def _toggle_ns(self):
        """A/B 对照：下行 NS:on|off 切换设备端降噪。

        目的只有一个——把「语音是被降噪压了」还是「被 VAD 阈值挡了」分开。
        关掉降噪后上行变成 AEC 输出直通，底噪读数会上跳，语音却更完整。
        """
        ip = self._board_ip()
        want = not self._ns_on
        cmd = "NS:on" if want else "NS:off"
        if ip:
            ok = self.speaker.control(ip, cmd)
        else:
            # 蓝牙模式没有 IP，控制帧改走 RX 特征；板子认同一条 "NS:on|off"
            ble = getattr(self.speaker, "ble", None)
            ok = bool(ble is not None and ble.send_control(cmd))
            if not ok:
                self.lbl_status.setText("板子不在线，无法切换降噪")
                return
        if not ok:
            self.lbl_status.setText("降噪切换发送失败（板子 IP 不可达）")
            return
        self._ns_on = want
        self._refresh_ns_button()
        self.lbl_status.setText("设备端降噪已" + ("打开" if want else "关闭（A/B 对照）"))
        self._verify_ns_async(ip)

    def _verify_ns_async(self, ip):
        """后台确认板子真的认了这条控制帧——老固件会静默忽略。"""
        def run():
            if not ip:
                # 蓝牙模式：板子回 "NS on" / "NS off"，等它自己报，不去问 STAT
                ble = getattr(self.speaker, "ble", None)
                if ble is None:
                    return
                was = ble.last_ns
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline and ble.last_ns == was:
                    time.sleep(0.05)
                if ble.last_ns and ble.last_ns != was:
                    on = ble.last_ns.endswith("on")
                    self.sig_ns.emit(on)
                    self.sig_status.emit("板子确认：降噪 " + ("开" if on else "关"))
                else:
                    self.sig_status.emit("降噪切换未见回包（可能是老固件）")
                return

            from micapp.gate import _stat_once
            got = _stat_once((ip, self.speaker.port), 1.0)
            if got is None:
                self.sig_status.emit("降噪切换未见回包（可能是老固件）")
                return
            fields = {}
            for tok in got[1].split():
                if "=" in tok:
                    key, _, val = tok.partition("=")
                    fields[key] = val
            if fields.get("afe") == "on":
                # AFE 前端把 AEC/降噪/VAD 合成一条链，运行期单独关降噪没有意义
                self.sig_status.emit("板子跑的是 AFE 前端，降噪由前端统一管理，A/B 开关不生效")
                return
            if "ns" in fields:
                on = fields["ns"] == "on"
                self.sig_ns.emit(on)              # 以板子自报为准，纠正按钮显示
                self.sig_status.emit("板子确认：降噪 " + ("开" if on else "关"))
                return
            self.sig_status.emit("板子回包没有 ns= 字段（老固件）")

    def _on_ns_state(self, on):
        self._ns_on = bool(on)
        self._refresh_ns_button()

    # ---------- 语音输入 ----------
    def _toggle_voice_input(self):
        if self.voice is None:
            self.lbl_status.setText("语音输入未初始化")
            return
        self.voice.toggle()

    def _on_hotkey_voice(self):
        # F9：暂停/恢复（没开启时按一下直接开启，省得再点界面）
        if self.voice is not None:
            self.voice.toggle_pause()

    def _on_board_event(self, event):
        """板载 BOOT 键：和界面按钮、Shift+F9 同一个动作。"""
        if event == "voice_toggle" and self.voice is not None:
            self.voice.toggle()

    def _on_hotkey_toggle(self):
        # Shift+F9：硬开关，和界面按钮同一个动作，状态照样落盘
        if self.voice is not None:
            self.voice.toggle()

    def _on_voice_state(self, on, paused):
        if on and paused:
            text, obj = "语音输入 暂停", "ghostWarn"
        elif on:
            text, obj = "语音输入 开", "ghostOn"
        else:
            text, obj = "语音输入 关", "ghost"
        self.btn_vi.setText(text)
        self.btn_vi.setObjectName(obj)
        self.btn_vi.style().unpolish(self.btn_vi)
        self.btn_vi.style().polish(self.btn_vi)

    def is_risky_target(self) -> bool:
        """前台是控制台/终端：不注入，避免一句话变成半条命令。"""
        try:
            from micapp.typer import foreground_info, is_risky_window
            _hwnd, cls, title = foreground_info()
            return is_risky_window(cls, title)
        except Exception:
            return False

    def is_self_foreground(self) -> bool:
        """语音输入往「前台窗口」打字；前台就是自己时不能打，否则打进自己的输入框。"""
        try:
            from micapp.typer import foreground_handle
            return foreground_handle() == self._hwnd
        except Exception:
            return False

    def _refresh_ns_button(self):
        self.btn_ns.setText("降噪 开" if self._ns_on else "降噪 关")
        self.btn_ns.setObjectName("ghost" if self._ns_on else "ghostWarn")
        self.btn_ns.style().unpolish(self.btn_ns)
        self.btn_ns.style().polish(self.btn_ns)

    def _on_speak(self):
        text = self.edit.text().strip()
        if not text:
            return
        ip = self._board_ip()
        if not ip and not getattr(getattr(self.speaker, "ble", None), "connected", False):
            self.lbl_status.setText("板子不在线：等蓝牙连上，或在 config.json 填 board_ip")
            return
        if not self.speaker.speak(text, ip,
                                  on_status=self.sig_status.emit,
                                  on_done=lambda ok: self.sig_status.emit(
                                      "朗读完成" if ok else "朗读中断")):
            self.lbl_status.setText("上一段还在播放…")
            return
        self.edit.clear()
        self._append_text(text, mine=True)
        self.btn_speak.setEnabled(False)
        QTimer.singleShot(400, self._reap_speak)

    def _reap_speak(self):
        if self.speaker.busy:
            QTimer.singleShot(300, self._reap_speak)
        else:
            self.btn_speak.setEnabled(True)

    def _append_text(self, text, mine=False, muted=False):
        b = Bubble(text, mine=mine, system=muted)
        self.chat.insertWidget(self.chat.count() - 1, b)
        self._bubbles += 1
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear(self):
        while self.chat.count() > 1:
            item = self.chat.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ---------- 无边框窗口拖动 ----------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._titlebar.geometry().contains(
                self._titlebar.mapFrom(self, ev.position().toPoint())):
            self._drag_from = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._drag_from is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_from)
            ev.accept()

    def mouseReleaseEvent(self, _ev):
        self._drag_from = None

    def closeEvent(self, ev):
        try:
            if self.receiver is not None:
                self.receiver.stop()
            self.speaker.stop()
            if self.engine is not None:
                self.engine.stop()
        finally:
            super().closeEvent(ev)
