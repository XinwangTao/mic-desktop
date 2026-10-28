"""无界面端到端测试：UDP 回放录音 -> 接收 -> VAD -> 讯飞 -> 打印转写。"""
import json
import socket
import struct
import sys
import threading
import time
import wave

sys.path.insert(0, r"F:\projects\mic-desktop")
from micapp.udp_receiver import UdpReceiver          # noqa: E402
from micapp.vad import VADSlicer                      # noqa: E402
from micapp.stt import base as stt_base               # noqa: E402
from micapp.stt import xfyun                          # noqa: F401,E402

PORT = 5005
HEADER = struct.Struct("<4sBBHIIHH")

cfg = json.load(open(r"F:\projects\mic-desktop\config.json", encoding="utf-8"))
cfg["udp_port"] = PORT
mapping = {"XF_APPID": "app_id", "XF_APIKEY": "api_key", "XF_APISECRET": "api_secret"}
for line in open(r"F:\cache\xfyun.env", encoding="utf-8"):
    k, v = line.strip().split("=", 1)
    cfg["xfyun"][mapping[k]] = v

results = []
last_result_time = time.time()


def on_result(text, idx):
    global last_result_time
    results.append(text)
    last_result_time = time.time()
    print(f"[STT #{idx}] {text}", flush=True)


engine = stt_base.create("xfyun", cfg, on_result)
engine.start()
slicer = VADSlicer(threshold=cfg["vad"]["rms_threshold"], start_ms=cfg["vad"]["start_ms"],
                   hangover_ms=cfg["vad"]["hangover_ms"], on_utterance=engine.feed_utterance)
receiver = UdpReceiver(PORT, slicer.feed)
receiver.start()
print(f"[receiver listening on {PORT}]", flush=True)

wv = wave.open(r"F:\cache\audio_dump.wav", "rb")
pcm = wv.readframes(wv.getnframes())
wv.close()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
seq = 0
for i in range(0, len(pcm), 640):
    piece = pcm[i:i + 640]
    pkt = HEADER.pack(b"AUD0", 1, 0, HEADER.size, seq, 16000, len(piece) // 2, len(piece)) + piece
    sock.sendto(pkt, ("127.0.0.1", PORT))
    seq += 1
    time.sleep(0.002)  # 约 10 倍速，避免突发丢包
print(f"[replayed {seq} frames]", flush=True)

# 等识别收尾：30 秒无新结果或总 240 秒上限
t0 = time.time()
while time.time() - last_result_time < 30 and time.time() - t0 < 240:
    time.sleep(2)
print(f"[done] utterances recognized: {len([r for r in results if not r.startswith('[识别错误')])}", flush=True)
receiver.stop()
engine.stop()
sys.exit(0 if results else 1)
