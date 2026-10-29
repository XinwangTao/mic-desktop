"""UDP 音频接收线程：解包 AUD0 帧，重排乱序包，回调 PCM 数据与统计。"""
import socket
import struct
import threading
import time

HEADER = struct.Struct("<4sBBHIIHH")  # 20B 小端 packed，与固件 lib.rs 一致
MAGIC = b"AUD0"
VERSION = 1
FLAG_SPEECH = 0x01   # 固件 AFE 判为语音时置位；老固件恒 0

HOLD_S = 0.06   # 缺口最多等 60ms，等不到就当作丢包放行


class UdpReceiver(threading.Thread):
    """后台收包线程。on_audio(pcm: bytes) 在本线程内按包序号顺序被调用；
    stats() 返回 {'addr','packets','lost','bad','reordered','pkt_rate','samples_rate'}。"""

    def __init__(self, port: int, on_audio, reorder_hold: int = 3):
        super().__init__(daemon=True, name="udp-receiver")
        self.port = port
        self.on_audio = on_audio                     # on_audio(pcm, flags)
        self.reorder_hold = max(0, reorder_hold)   # 乱序缓存帧数，1 帧 = 20ms
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._stats = {"addr": "-", "packets": 0, "lost": 0, "bad": 0, "reordered": 0,
                       "speech_pkts": 0, "pkt_rate": 0.0, "samples_rate": 0.0}
        self._play_seq = None          # 下一个该播的包序号
        self._pending = {}             # 早到的包：seq -> pcm
        self._pending_at = 0.0
        self._win_start = None
        self._win_pkts = 0
        self._win_samples = 0

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
        sock.bind(("0.0.0.0", self.port))
        sock.settimeout(0.02)
        while not self._cancel.is_set():
            try:
                packet, addr = sock.recvfrom(65535)
            except socket.timeout:
                # 缓冲区里压着的包等不到缺口了，按现有顺序放行
                if self._pending and time.monotonic() - self._pending_at > HOLD_S:
                    self._flush_pending()
                continue
            if len(packet) < HEADER.size:
                with self._lock:
                    self._stats["bad"] += 1
                continue
            magic, ver, flags, hlen, seq, rate, samples, payload_len = HEADER.unpack_from(packet)
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
                self._stats["packets"] += 1
                self._stats["addr"] = f"{addr[0]}:{addr[1]}"
                self._win_pkts += 1
                self._win_samples += samples
                if flags & FLAG_SPEECH:
                    self._stats["speech_pkts"] += 1
            try:
                self._deliver(seq, payload, flags)
            except Exception:
                pass  # 下游异常不拖垮收包
            self._update_rate()
        sock.close()

    # ---------- 乱序重排 ----------
    def _deliver(self, seq: int, payload: bytes, flags: int = 0):
        """按 seq 顺序投递；乱序包最多缓存 reorder_hold 帧，超时/超限就按现有顺序放行。"""
        if self._play_seq is None:
            self._play_seq = (seq + 1) & 0xFFFFFFFF
            self.on_audio(payload, flags)
            return
        gap = (seq - self._play_seq) & 0xFFFFFFFF
        if gap == 0:
            self.on_audio(payload, flags)
            self._play_seq = (seq + 1) & 0xFFFFFFFF
            self._drain()
        elif gap <= self.reorder_hold:
            self._pending[seq] = (payload, flags)
            self._pending_at = time.monotonic()
            with self._lock:
                self._stats["reordered"] += 1
            if len(self._pending) > self.reorder_hold:
                self._flush_pending()
        elif gap > 0x80000000:
            pass  # 迟到的旧包，已经播过去了，直接丢
        else:
            # 缺口比缓存窗口还大：认定中间确实丢了，记丢包，先放行手里的再播当前包
            with self._lock:
                self._stats["lost"] += gap
            self._flush_pending()
            self.on_audio(payload, flags)
            self._play_seq = (seq + 1) & 0xFFFFFFFF

    def _drain(self):
        while self._play_seq in self._pending:
            pcm, flags = self._pending.pop(self._play_seq)
            self.on_audio(pcm, flags)
            self._play_seq = (self._play_seq + 1) & 0xFFFFFFFF

    def _flush_pending(self):
        if not self._pending:
            return
        seqs = sorted(self._pending)
        first = seqs[0]
        missing = (first - self._play_seq) & 0xFFFFFFFF
        if 0 < missing <= 0x80000000:
            with self._lock:
                self._stats["lost"] += missing
        for seq in seqs:
            pcm, flags = self._pending.pop(seq)
            self.on_audio(pcm, flags)
            self._play_seq = (seq + 1) & 0xFFFFFFFF

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
        self._cancel.set()
