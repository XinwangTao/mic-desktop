"""麦克风门控：板子朗读期间丢弃上行音频。

扬声器和麦克风在同一块板子上，朗读声会被 INMP441 采回来，
同一句话因此被再次转写。这里在播放期间（以及结束后的一小段混响/功放余量）
不把音频喂给 VAD，并把进行中的语音段作废，避免跨过静音粘成一整段。

固件上了设备端 AEC 之后就不需要这层门控了，但老固件还得有。
所以启动时先问板子一句（STAT 回包里的 aec=on|off），按它的能力决定装不装。
"""
import socket
import time

STAT_TIMEOUT_S = 1.0   # 单次等回包
STAT_ATTEMPTS = 3      # 最多问几次；全超时就按半双工走（安全默认）


class MicGate:
    """包在 VADSlicer.feed 外面的一层开关，可直接当 on_audio 用。"""

    def __init__(self, feed, speaker, tail_s: float = 0.35, slicer=None):
        self.feed = feed
        self.speaker = speaker
        self.tail_s = tail_s
        self.slicer = slicer
        self.dropped = 0
        self._muted = False
        self._resume_at = 0.0

    @property
    def muted(self) -> bool:
        return self._muted

    def __call__(self, pcm: bytes, flags: int = 0):
        now = time.monotonic()
        if getattr(self.speaker, "busy", False):
            self._resume_at = now + self.tail_s
            if not self._muted:
                self._muted = True
                if self.slicer is not None:
                    self.slicer.reset()   # 作废朗读前的半句话
        elif self._muted and now >= self._resume_at:
            self._muted = False
        if self._muted:
            self.dropped += 1
            return
        self.feed(pcm, flags)


def _stat_once(target, timeout_s: float):
    """发一个 STAT，等回包；返回 (来源 IP, 回包文本)，超时返回 None。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(b"STAT", target)
        deadline = time.monotonic() + timeout_s
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return None
            sock.settimeout(remain)
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                return None
            text = data.decode("utf-8", "replace")
            if not text.startswith("STAT"):   # 串台的别的包，忽略
                continue
            return addr[0], text
    except OSError:
        return None
    finally:
        sock.close()


def _parse_aec(text: str) -> bool:
    """回包里 aec=on → True；aec=off 或老固件没这个字段 → False。"""
    for token in text.split():
        if token.startswith("aec="):
            return token[4:].lower() == "on"
    return False


def learn_board_ip(port: int = 5000, timeout_s: float = 1.0):
    """在接收线程启动前短暂占用上行端口，从上行包的来源地址拿到板子 IP。

    为什么不直接广播：板子开着 Wi-Fi 省电（modem sleep），广播包落在休眠窗口里就丢，
    实测广播 STAT 3 次全超时而单播 0.06 s 就回。上行是持续的单播流，靠它学最可靠。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(0.05)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return ""
            if len(data) >= 4 and data[:4] == b"AUD0":
                return addr[0]
    except OSError:
        return ""
    finally:
        sock.close()
    return ""


def probe_aec(board_ip: str = "", port: int = 5001,
              timeout_s: float = STAT_TIMEOUT_S, attempts: int = STAT_ATTEMPTS):
    """问板子有没有 AEC。返回 (state, ip)：

    state=True   板子回 aec=on
    state=False  板子回 aec=off，或是没有 aec= 字段的老固件
    state=None   attempts 次都没回包（没上电 / 不同网段）

    board_ip 为空时才退回广播——广播不可靠（板子省电会丢），只当兜底。
    """
    target = (board_ip, port) if board_ip else ("255.255.255.255", port)
    for _ in range(max(1, attempts)):
        got = _stat_once(target, timeout_s)
        if got is not None:
            ip, text = got
            return _parse_aec(text), ip
    return None, ""


def build_gate(feed, speaker, cfg: dict, slicer=None, prober=None, learner=None):
    """按配置 + 板子能力决定装不装门控；返回 (gate 或 None, 判定说明)。

    mic_gate="off"  显式全双工，不探测
    mic_gate="on"   显式半双工，不探测
    mic_gate="auto" 先拿板子 IP（config → 从上行包学），再单播 STAT 问 aec=on|off；
                    探测不到按半双工（安全默认）
    """
    mode = str(cfg.get("mic_gate", "auto")).strip().lower()
    if mode == "off":
        return None, "配置 mic_gate=off：全双工，不装门控"
    if mode == "on":
        return _make_gate(feed, speaker, cfg, slicer), "配置 mic_gate=on：半双工"

    ip = str(cfg.get("board_ip", "") or "").strip()
    if not ip:
        learn = learner or learn_board_ip
        ip = learn(cfg.get("udp_port", 5000)) or ""

    probe = prober or probe_aec
    state, _ip = probe(ip, cfg.get("speaker_port", 5001))
    if state is None:
        return _make_gate(feed, speaker, cfg, slicer), "未探测到板子，先按半双工"
    if state:
        return None, "板子上报 aec=on：全双工，不装门控"
    return _make_gate(feed, speaker, cfg, slicer), "板子无 AEC（aec=off 或老固件）：半双工"


def _make_gate(feed, speaker, cfg: dict, slicer):
    return MicGate(feed, speaker, tail_s=cfg.get("mic_mute_tail_s", 0.35), slicer=slicer)
