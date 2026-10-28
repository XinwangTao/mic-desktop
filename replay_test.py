"""把已录制的 WAV 以 AUD0 UDP 流回放，用于无硬件端到端测试。

用法: python replay_test.py [wav路径] [目标端口]
"""
import socket
import struct
import sys
import time
import wave

HEADER = struct.Struct("<4sBBHIIHH")


def main():
    wav_path = sys.argv[1] if len(sys.argv) > 1 else r"F:\cache\audio_dump.wav"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    wv = wave.open(wav_path, "rb")
    assert wv.getframerate() == 16000 and wv.getnchannels() == 1, "仅支持 16k mono"
    pcm = wv.readframes(wv.getnframes())
    wv.close()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = ("127.0.0.1", port)
    seq, frame_bytes = 0, 640  # 320 samples
    total = len(pcm) // frame_bytes
    t0 = time.monotonic()
    for i in range(total):
        piece = pcm[i * frame_bytes:(i + 1) * frame_bytes]
        pkt = HEADER.pack(b"AUD0", 1, 0, HEADER.size, seq, 16000, 320, len(piece)) + piece
        sock.sendto(pkt, target)
        seq += 1
        # 实时节奏回放
        due = t0 + (i + 1) / 50.0
        delta = due - time.monotonic()
        if delta > 0:
            time.sleep(delta)
    print(f"replayed {total} frames ({total/50:.1f}s) -> 127.0.0.1:{port}")


if __name__ == "__main__":
    main()
