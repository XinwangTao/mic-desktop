"""UDP 音频接收线程：解包 AUD0 帧，回调 PCM 数据与统计。"""
import socket
import struct
import threading
import time

HEADER = struct.Struct("<4sBBHIIHH")  # 20B 小端 packed，与固件 lib.rs 一致
MAGIC = b"AUD0"
VERSION = 1


class UdpReceiver(threading.Thread):
    """后台收包线程。on_audio(pcm: bytes) 在本线程内被调用；
    stats() 返回 {'addr','packets','lost','bad','pkt_rate','samples_rate'}。"""

    def __init__(self, port: int, on_audio):
        super().__init__(daemon=True, name="udp-receiver")
        self.port = port
        self.on_audio = on_audio
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stats = {"addr": "-", "packets": 0, "lost": 0, "bad": 0,
                       "pkt_rate": 0.0, "samples_rate": 0.0}
        self._next_seq = None
        self._win_start = None
        self._win_pkts = 0
        self._win_samples = 0

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
        sock.bind(("0.0.0.0", self.port))
        sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                packet, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            if len(packet) < HEADER.size:
                with self._lock:
                    self._stats["bad"] += 1
                continue
            magic, ver, _flags, hlen, seq, rate, samples, payload_len = HEADER.unpack_from(packet)
            if magic != MAGIC or ver != VERSION or hlen != HEADER.size:
                with self._lock:
                    self._stats["bad"] += 1
                continue
            payload = packet[hlen:]
            if len(payload) != payload_len or payload_len != samples * 2:
                with self._lock:
                    self._stats["bad"] += 1
                continue
            with self._lock:
                if self._next_seq is not None and seq != self._next_seq:
                    self._stats["lost"] += (seq - self._next_seq) & 0xFFFFFFFF
                self._next_seq = (seq + 1) & 0xFFFFFFFF
                self._stats["packets"] += 1
                self._stats["addr"] = f"{addr[0]}:{addr[1]}"
                self._win_pkts += 1
                self._win_samples += samples
            try:
                self.on_audio(payload)
            except Exception:
                pass  # 下游异常不拖垮收包
            self._update_rate()
        sock.close()

    def _update_rate(self):
        now_t = time.monotonic()
        with self._lock:
            if self._win_start is None:
                self._win_start = now_t
                return
            el = now_t - self._win_start
            if el >= 2.0:
                self._stats["pkt_rate"] = self._win_pkts / el
                self._stats["samples_rate"] = self._win_samples / el
                self._win_start = now_t
                self._win_pkts = 0
                self._win_samples = 0

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def stop(self):
        self._stop.set()
