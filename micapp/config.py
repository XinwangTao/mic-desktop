"""应用配置读写。config.json 与主程序/EXE 同目录，便携式。"""
import json
import os
import sys

DEFAULTS = {
    "udp_port": 5000,
    "stt_engine": "xfyun",
    "language": "zh_cn",
    "xfyun": {"app_id": "", "api_key": "", "api_secret": ""},
    "whisper_http": {"url": "http://127.0.0.1:8390/stt"},
    "vad": {"rms_threshold": 350, "start_ms": 200, "hangover_ms": 800},
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
