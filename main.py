"""麦克风接收台入口：装配 UDP 接收 → VAD → STT → 界面 → TTS 下行。"""
import logging
import os
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from micapp import config
from micapp.stt import base as stt_base
from micapp.stt import xfyun  # noqa: F401 注册引擎
from micapp.stt import whisper_http  # noqa: F401 注册引擎
from micapp.ui import QSS, MainWindow
from micapp.udp_receiver import UdpReceiver
from micapp.vad import VADSlicer


def main() -> int:
    cfg = config.load()
    log_path = os.path.join(os.path.dirname(config.config_path()), "log.txt")
    logging.basicConfig(filename=log_path, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.info("app start; engine=%s udp_port=%s", cfg["stt_engine"], cfg["udp_port"])

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(QSS)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    engine = stt_base.create(cfg["stt_engine"], cfg, on_result=None)
    window = MainWindow(cfg, receiver=None, engine=engine)

    def on_result(text, idx):
        window.ui("text", text, idx)
        try:
            log_dir = os.path.join(os.path.dirname(config.config_path()), "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, time.strftime("transcript_%Y%m%d.txt")),
                      "a", encoding="utf-8") as f:
                f.write(time.strftime("[%H:%M:%S] ") + text + "\n")
        except OSError:
            logging.exception("transcript save failed")

    engine.on_result = on_result
    engine.start()

    slicer = VADSlicer(
        threshold=cfg["vad"]["rms_threshold"],
        start_ms=cfg["vad"]["start_ms"],
        hangover_ms=cfg["vad"]["hangover_ms"],
        on_utterance=engine.feed_utterance,
        on_level=lambda rms: window.ui("level", rms),
    )

    receiver = UdpReceiver(cfg["udp_port"], slicer.feed)
    window.receiver = receiver

    timer = QTimer()
    timer.timeout.connect(lambda: window.ui("stats", receiver.stats()))
    timer.start(500)

    receiver.start()
    window.show()
    try:
        return app.exec()
    finally:
        receiver.stop()
        engine.stop()


if __name__ == "__main__":
    raise SystemExit(main())
