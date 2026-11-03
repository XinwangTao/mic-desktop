"""应用配置读写。config.json 与主程序/EXE 同目录，便携式。"""
import json
import os
import sys

DEFAULTS = {
    "udp_port": 5000,
    "speaker_port": 5001,
    "board_ip": "",
    # 音频上行来源："udp"（Wi-Fi）或 "ble"。固件侧二选一，两边要一致。
    "input_mode": "udp",
    "ble_name_prefix": "MicDesktop-",
    "stt_engine": "xfyun",
    "language": "zh_cn",
    "xfyun": {"app_id": "", "api_key": "", "api_secret": ""},
    "whisper_http": {"url": "http://127.0.0.1:8390/stt"},
    "vad": {
        # 固定阈值：adaptive=false 时直接用；adaptive=true 时只在底噪估计就绪前兜底
        "rms_threshold": 350,
        "start_ms": 200,
        "hangover_ms": 800,
        # 自适应阈值：按环境底噪算阈值，代替写死的 350。
        # 阈值 = clamp(底噪 × ratio, floor, cap)；ratio=3 ≈ +9.5 dB 信噪比
        "adaptive": True,
        "ratio": 3.0,
        # 下限/上限：2026-10-29 实测本机底噪 p50≈22、p90≈30，用户语音整句 RMS 110~125。
        # floor 原本定 120，结果阈值正好压在语音线上，句子擦着过——降到 50，实际算出来是 57。
        "floor": 50,
        "cap": 1200,    # 上限：噪声再大也不能把阈值抬到说话够不着
        "window_frames": 500,  # 底噪窗口 500 帧 × 20 ms = 10 s
        "percentile": 20,      # 取 P20：说话帧占少数，均值会被语音抬高
        "smooth": 0.2,         # 底噪指数平滑系数（50 Hz 下 τ≈0.1 s，只去毛刺）
        # 阈值本身的平滑：50 Hz 下 0.005 ≈ τ 4 s。底噪每帧都在动，不滤这一道
        # 界面上的阈值会跟着飘二十来个单位（实测 55~76）。
        "thr_smooth": 0.005,
        # 诊断：每 5 s 往 log.txt 写一行电平分布与阈值波动。调完阈值记得关掉
        "log_stats": False,
    },
    "tts_voice": "Microsoft Huihui Desktop",
    # "auto" = 朗读期间静音麦克风（半双工，老固件只能这样）；"off" = 依赖设备端 AEC，真全双工
    "mic_gate": "auto",
    "mic_mute_tail_s": 0.35,
    # 自动回读：转写出一句就自动念出来。默认关——会把误转写也念出来，需要时在 config.json 打开
    "auto_speak": False,
    "auto_speak_min_chars": 2,
    # 跨机语音输入：中枢侧把转写好的文字转发给第二台接收端
    "remote": {
        "host": "",           # 第二台接收端 IP（中枢侧填；空 = 未配置，转发自动回退本机）
        "port": 8391,         # 中枢目标端口 / 接收端监听端口
        "timeout": 2.0,       # 中枢转发超时（秒）
        "listen": "0.0.0.0",  # 接收端监听地址
    },
}

ENGINE_LABELS = {"xfyun": "讯飞云端", "whisper_http": "Whisper 本地"}


def config_path() -> str:
    base = os.path.dirname(os.path.abspath(sys.argv[0])) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "config.json")


def load() -> dict:
    path = config_path()
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except (json.JSONDecodeError, OSError):
            pass  # 坏配置按默认跑，UI 上仍可改
    return cfg


def save(cfg: dict) -> None:
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
