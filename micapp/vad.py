"""能量 VAD：把连续 PCM 流切成语音段（utterance）。

用法：VADSlicer.feed(pcm) 逐帧喂入；检测到一段完整语音时
通过 on_utterance(pcm_bytes) 回调整段（s16LE 单声道 16k）；
on_level(rms) 持续回调电平供 UI 显示。
"""
import collections

try:
    import audioop

    def _rms(pcm: bytes) -> float:
        return audioop.rms(pcm, 2)
except ImportError:  # Python 3.13+ 移除了 audioop
    import struct as _struct

    def _rms(pcm: bytes) -> float:
        n = len(pcm) // 2
        if not n:
            return 0.0
        vals = _struct.unpack("<%dh" % n, pcm[: n * 2])
        return (sum(v * v for v in vals) / n) ** 0.5


class VADSlicer:
    def __init__(self, threshold: float = 350.0, start_ms: int = 200,
                 hangover_ms: int = 800, sample_rate: int = 16000,
                 on_utterance=None, on_level=None):
        self.threshold = threshold
        self.start_frames = max(1, start_ms // 20)   # 每帧 20ms
        self.hangover_frames = max(1, hangover_ms // 20)
        self.on_utterance = on_utterance
        self.on_level = on_level
        self._buf = bytearray()
        self._above = 0
        self._quiet = 0
        self._active = False
        self._pre = collections.deque(maxlen=self.start_frames)  # 起始判定期间的前置帧
        self._hist = collections.deque(maxlen=50)    # 电平条用最近电平

    def reset(self):
        """丢掉当前未完成的语音段（例如朗读期间被门控时）。"""
        self._buf = bytearray()
        self._above = 0
        self._quiet = 0
        self._active = False
        self._pre.clear()

    def feed(self, pcm: bytes):
        rms = _rms(pcm)
        if self.on_level:
            self._hist.append(rms)
            self.on_level(rms)
        if rms >= self.threshold:
            self._above += 1
            self._quiet = 0
            if not self._active and self._above >= self.start_frames:
                self._active = True
                # 补回判定期间的 start_ms 帧，否则每句话开头会丢一整个音节
                self._buf = bytearray(b"".join(self._pre))
            if not self._active:
                self._pre.append(pcm)
        else:
            self._above = 0
            self._pre.clear()
            if self._active:
                self._quiet += 1
        if self._active:
            self._buf.extend(pcm)
            if self._quiet >= self.hangover_frames:
                self._active = False
                data = bytes(self._buf)
                self._buf = bytearray()
                if len(data) >= 3200 and self.on_utterance:  # <100ms 丢弃
                    self.on_utterance(data)
