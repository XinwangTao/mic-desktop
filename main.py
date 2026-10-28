"""麦克风接收台入口：装配 UDP 接收 → VAD → STT → UI。"""
import logging
import os
import time
import tkinter as tk


def main():
    from micapp import config
    cfg = config.load()
    log_path = os.path.join(os.path.dirname(config.config_path()), "log.txt")
    logging.basicConfig(filename=log_path, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.info("app start; engine=%s udp_port=%s", cfg["stt_engine"], cfg["udp_port"])

    from micapp.udp_receiver import UdpReceiver
    from micapp.vad import VADSlicer
    from micapp.stt import base as stt_base
    from micapp.stt import xfyun  # noqa: F401 注册引擎
    from micapp.ui import MainWindow

    root = tk.Tk()
    engine = stt_base.create(cfg["stt_engine"], cfg, on_result=None)
    window = MainWindow(root, cfg, receiver=None, engine=engine)

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

    def on_audio(pcm):
        slicer.feed(pcm)

    receiver = UdpReceiver(cfg["udp_port"], on_audio)

    def poll_stats():
        window.ui("stats", receiver.stats())
        root.after(500, poll_stats)

    window.receiver = receiver
    receiver.start()
    poll_stats()
    root.mainloop()
    engine.stop()


if __name__ == "__main__":
    main()
