"""麦克风门控：板子朗读期间丢弃上行音频。

扬声器和麦克风在同一块板子上，朗读声会被 INMP441 采回来，
同一句话因此被再次转写。这里在播放期间（以及结束后的一小段混响/功放余量）
不把音频喂给 VAD，并把进行中的语音段作废，避免跨过静音粘成一整段。
"""
import time


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

    def __call__(self, pcm: bytes):
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
        self.feed(pcm)
