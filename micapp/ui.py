"""PySide6 主窗口：无边框深色卡片 + 自定义标题栏 + 圆角气泡。

线程安全：所有后台线程只 emit 信号，界面更新一律在 GUI 线程的槽里做。
"""
import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizeGrip, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

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
                     padding: 8px 16px; color: {MUTED}; }}
QPushButton#ghost:hover {{ border-color: {MUTED}; color: {INK}; }}
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
    sig_level = Signal(float)
    sig_status = Signal(str)

    def __init__(self, cfg: dict, receiver=None, engine=None):
        super().__init__()
        self.cfg = cfg
        self.receiver = receiver
        self.engine = engine
        self._drag_from = None
        self._bubbles = 0

        from micapp.tts import Speaker
        self.speaker = Speaker(port=cfg.get("speaker_port", 5001),
                               voice=cfg.get("tts_voice", "Microsoft Huihui Desktop"))

        self.setWindowTitle("无线麦克风接收台 - ESP32-S3 INMP441")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(780, 560)
        self.resize(940, 660)
        self._build()

        self.sig_text.connect(self._on_text)
        self.sig_stats.connect(self._on_stats)
        self.sig_level.connect(self._on_level)
        self.sig_status.connect(self._on_status)

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

        avatar = QLabel("麦")
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background:{ACCENT};color:#0B1220;border-radius:10px;font-weight:800;font-size:15px;")
        lay.addWidget(avatar)

        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel("无线麦克风接收台")
        title.setObjectName("title")
        self.lbl_sub = QLabel("ESP32-S3 · INMP441 · UDP")
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
        for w in (self.chip_dev, self.chip_rate, self.chip_lost, self.chip_engine):
            lay.addWidget(w)
        lay.addStretch(1)
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
        wrap.setFixedHeight(36)
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(20, 2, 8, 8)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setObjectName("hint")
        lay.addWidget(self.lbl_status)
        lay.addStretch(1)
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
            self.sig_level.emit(float(args[0]))
        elif kind == "stats":
            self.sig_stats.emit(args[0])
        elif kind == "status":
            self.sig_status.emit(str(args[0]))

    # ---------- 槽 ----------
    def _on_text(self, text, _idx):
        self._append_text(text)

    def _on_stats(self, s):
        addr = s.get("addr", "-")
        self.chip_dev.setText("设备 " + addr if addr != "-" else "设备 未连接")
        self.chip_dev.setObjectName("chipHi" if addr != "-" else "chip")
        self.chip_dev.style().unpolish(self.chip_dev)
        self.chip_dev.style().polish(self.chip_dev)
        self.chip_rate.setText("%.1f pkt/s" % s.get("pkt_rate", 0.0))
        self.chip_lost.setText("丢包 %d · 坏帧 %d" % (s.get("lost", 0), s.get("bad", 0)))

    def _on_level(self, rms):
        thr = float(self.cfg["vad"]["rms_threshold"])
        self.meter.set_level(rms / (thr * 3.0), rms > thr)

    def _on_status(self, text):
        self.lbl_status.setText(text)

    # ---------- 交互 ----------
    def _board_ip(self) -> str:
        if self.receiver is not None:
            addr = self.receiver.stats().get("addr", "-")
            if addr and addr != "-":
                return addr.split(":")[0]
        return self.cfg.get("board_ip", "") or ""

    def _on_speak(self):
        text = self.edit.text().strip()
        if not text:
            return
        ip = self._board_ip()
        if not ip:
            self.lbl_status.setText("还没收到板子的上行包，先在 config.json 填 board_ip")
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
