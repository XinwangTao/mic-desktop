"""麦克风接收台入口：装配 UDP 接收 → VAD → STT → 界面 → TTS 下行。"""
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from micapp import config
from micapp.assets import APP_NAME, asset_path
from micapp.stt import base as stt_base
from micapp.stt import xfyun  # noqa: F401 注册引擎
from micapp.stt import whisper_http  # noqa: F401 注册引擎
from micapp.gate import build_gate
from micapp.readback import Readback
from micapp.ui import QSS, MainWindow
from micapp.udp_receiver import UdpReceiver
from micapp.vad import VADSlicer, _rms


def main() -> int:
    cfg = config.load()
    log_path = os.path.join(os.path.dirname(config.config_path()), "log.txt")
    # 滚动日志：诊断模式每 5 s 写一行，不设上限会一直涨（实测一天几十 MB）
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=2,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    logging.info("app start; engine=%s udp_port=%s", cfg["stt_engine"], cfg["udp_port"])

    # 让任务栏用我们自己的图标，而不是 python.exe 的（Windows 认 AppUserModelID）
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MicDesktop.ESP32S3.WirelessMic")
        except Exception:
            logging.exception("set AppUserModelID failed")

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(asset_path("appicon.png")))
    app.setStyleSheet(QSS)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    engine = stt_base.create(cfg["stt_engine"], cfg, on_result=None)
    window = MainWindow(cfg, receiver=None, engine=engine)
    # 引擎自报状态（本地服务不可用、转写失败等）显示在状态行，
    # 别让用户只看到一句抛异常的英文。
    engine.status_cb = lambda s: window.ui("status", s)

    # 自动回读：说完一句就自动念出来。默认关，config.json 里 auto_speak=true 才生效。
    # 去重 / 刹车 / 排队都在 Readback 里，任何一道拦下都只跳过这一句。
    readback = Readback(
        window.speaker,
        window.board_ip,
        enabled=bool(cfg.get("auto_speak", False)),
        min_chars=int(cfg.get("auto_speak_min_chars", 2)),
        on_status=lambda s: window.ui("status", s),
    )

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
        action = readback.offer(text)
        if action not in ("spoken", "queued", "off"):
            logging.info("readback skip (%s): %s", action, text)

    engine.on_result = on_result
    engine.start()

    def on_utterance(pcm):
        # 每句话的真实电平：阈值定得对不对，看这一行就知道（rms 应当明显高于 thr）
        logging.info("utterance %.2f s rms=%.0f thr=%.0f", len(pcm) / 2 / 16000, _rms(pcm), slicer.threshold)
        engine.feed_utterance(pcm)

    vad_cfg = cfg["vad"]
    slicer = VADSlicer(
        threshold=vad_cfg["rms_threshold"],
        start_ms=vad_cfg["start_ms"],
        hangover_ms=vad_cfg["hangover_ms"],
        on_utterance=on_utterance,
        on_level=lambda rms, thr, base: window.ui("level", rms, thr, base),
        adaptive=bool(vad_cfg.get("adaptive", True)),
        ratio=float(vad_cfg.get("ratio", 3.0)),
        floor=float(vad_cfg.get("floor", 120)),
        cap=float(vad_cfg.get("cap", 1200)),
        window_frames=int(vad_cfg.get("window_frames", 500)),
        percentile=int(vad_cfg.get("percentile", 20)),
        smooth=float(vad_cfg.get("smooth", 0.2)),
        thr_smooth=float(vad_cfg.get("thr_smooth", 0.005)),
    )
    logging.info("vad adaptive=%s ratio=%.1f floor=%.0f cap=%.0f fixed=%.0f",
                 slicer.adaptive, slicer.ratio, slicer.floor, slicer.cap,
                 slicer.fixed_threshold)

    # 朗读期间静音麦克风（半双工），避免板子自己的声音被采回来重复转写。
    # 启动时问一次板子（STAT → aec=on|off）：有 AEC 就真全双工，没有才装门控。
    # config.json 里写死 on/off 可跳过探测，保留人工覆盖。
    # 蓝牙模式下板子不开 Wi-Fi，所有 UDP 探测（AEC 握手、STAT）都不适用；
    # 音频和回读都走已建立的 BLE 链路，按全双工走、不装门控。
    if str(cfg.get("input_mode", "udp")).lower() == "ble":
        mic_gate, gate_reason = None, "蓝牙模式：音频走 BLE，不做 UDP 探测，按全双工"
    else:
        mic_gate, gate_reason = build_gate(slicer.feed, window.speaker, cfg, slicer)
    gate = mic_gate if mic_gate is not None else slicer.feed
    window.gate = mic_gate
    logging.info("mic gate: %s", gate_reason)
    window.ui("mode", "半双工" if mic_gate is not None else "全双工", gate_reason)

    # 上行 flags：bit1 = 本帧带设备端 VAD 结论，bit0 = 该结论是「语音」。
    # 老固件两位恒 0 → 传 None，让 VADSlicer 走能量阈值兜底，
    # 不能把「没有提示」当成「判为静音」，否则老固件一句都切不出来。
    FLAG_SPEECH, FLAG_VAD_VALID = 0x01, 0x02

    def on_audio(pcm, flags=0):
        hint = bool(flags & FLAG_SPEECH) if (flags & FLAG_VAD_VALID) else None
        gate(pcm, hint)

    if str(cfg.get("input_mode", "udp")).lower() == "ble":
        from micapp.ble_receiver import BleReceiver
        receiver = BleReceiver(on_audio, name_prefix=cfg.get("ble_name_prefix", "MicDesktop-"))
        window.speaker.ble = receiver   # 下行 TTS 改走 BLE 的 RX 特征
        logging.info("input mode: ble (prefix=%s)", cfg.get("ble_name_prefix", "MicDesktop-"))
    else:
        receiver = UdpReceiver(cfg["udp_port"], on_audio)
    window.receiver = receiver

    timer = QTimer()
    timer.timeout.connect(lambda: window.ui("stats", receiver.stats()))
    timer.start(500)

    # 诊断：每 5 s 记一次真实电平分布与阈值波动，用来定阈值而不是靠猜。
    # vad.log_stats=true 才开，长期跑会把日志撑大。
    if vad_cfg.get("log_stats", False):
        diag = QTimer(window)

        def log_vad_stats():
            s = slicer.stats()
            if s:
                logging.info("vad n=%d p10=%.0f p50=%.0f p90=%.0f max=%.0f "
                             "base=%.1f thr=%.0f (5s %.0f~%.0f) over=%d hint=%d/%d",
                             s["n"], s["p10"], s["p50"], s["p90"], s["max"],
                             s["base"], s["thr"], s["thr_min"], s["thr_max"], s["over"],
                             slicer.hint_speech, slicer.hint_frames)

        diag.timeout.connect(log_vad_stats)
        diag.start(5000)

    receiver.start()
    window.show()
    try:
        return app.exec()
    finally:
        receiver.stop()
        engine.stop()


if __name__ == "__main__":
    raise SystemExit(main())
