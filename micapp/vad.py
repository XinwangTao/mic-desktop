"""能量 VAD：把连续 PCM 流切成语音段（utterance）。

用法：VADSlicer.feed(pcm) 逐帧喂入；检测到一段完整语音时
通过 on_utterance(pcm_bytes) 回调整段（s16LE 单声道 16k）；
on_level(rms, threshold, baseline) 持续回调电平供 UI 显示。

阈值默认按环境底噪自适应（NoiseFloor），不再写死一个绝对值：
安静房间底噪低，阈值跟着降，2–3 米的小声说话才不会被漏掉；
噪声大的房间阈值升高，风扇和键盘声才不会被当成一句话送云端。
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


class NoiseFloor:
    """环境底噪估计：滑动窗口取低分位。

    为什么取低分位不取均值：说话帧只占少数，均值会被语音抬上去；
    低分位（默认 P20）在「说话帧占比 < 80%」时仍然落在底噪上。
    为什么只在非语音帧更新：否则连说 10 秒，基线会被自己的声音顶到语音电平，
    阈值跟着涨——那就等于把要修的毛病换个形式复发。
    """

    def __init__(self, window_frames: int = 500, percentile: int = 20,
                 smooth: float = 0.2, min_frames: int = 25):  # 25 帧 = 0.5 s
        self._window = collections.deque(maxlen=max(10, int(window_frames)))
        self._pct = min(90, max(1, int(percentile)))
        self._smooth = min(1.0, max(0.01, float(smooth)))
        self._min_frames = max(5, min(int(min_frames), self._window.maxlen))
        # 卡死兜底：连续这么多帧都没采到样，就无条件采一帧。
        # 没有这个，底噪一旦高于当前阈值，判据会把自己锁死——永远是"语音帧"，永远学不到底噪。
        self._max_stale = max(self._min_frames, int(window_frames) // 5)
        self._boot = 0        # 冷启动无条件采样的计数
        self._stale = 0       # 连续被拒的帧数
        self.value = 0.0      # 平滑后的底噪
        self.ready = False    # 攒够帧数、出过估计值

    def update(self, rms: float, threshold: float) -> float:
        """喂一帧；threshold 是当前生效阈值，用来判断这帧算不算语音。"""
        accept = rms < threshold
        if not accept and self._boot < self._min_frames:
            # 冷启动：前 0.5 s 无条件采样。否则开屏时房间就比 350 吵的话，
            # 每一帧都被判成语音，底噪永远估不出来，阈值也永远降不下去。
            accept = True
        if not accept and self._stale >= self._max_stale:
            accept = True                 # 兜底：每 max_stale 帧强采一帧，防止锁死
        self._boot += 1
        if not accept:
            self._stale += 1
            return self.value
        self._stale = 0
        self._window.append(rms)
        if len(self._window) < self._min_frames:
            return self.value          # 冷启动：样本不够，继续用固定阈值
        ordered = sorted(self._window)
        # P20 落在偏低的位置；用 n*p/100 取下标，窗口很小时也不会越界
        target = ordered[min(len(ordered) - 1, int(len(ordered) * self._pct / 100.0))]
        if not self.ready:
            self.value = target
            self.ready = True
        else:
            self.value += (target - self.value) * self._smooth
        return self.value

    def reset(self) -> None:
        """环境判据变了（例如开始朗读）时清空，避免旧底噪拖着新阈值。"""
        self._window.clear()
        self._boot = 0
        self._stale = 0
        self.value = 0.0
        self.ready = False


class VADSlicer:
    def __init__(self, threshold: float = 350.0, start_ms: int = 200,
                 hangover_ms: int = 800, sample_rate: int = 16000,
                 on_utterance=None, on_level=None,
                 adaptive: bool = True, ratio: float = 3.0,
                 floor: float = 50.0, cap: float = 1200.0,
                 window_frames: int = 500, percentile: int = 20,
                 smooth: float = 0.2, thr_smooth: float = 0.005):
        self.fixed_threshold = float(threshold)   # 回退值，也是冷启动兜底
        self.threshold = float(threshold)         # 当前生效阈值（自适应时会变）
        self.adaptive = bool(adaptive)
        self.ratio = max(1.0, float(ratio))
        self.floor = max(1.0, float(floor))
        self.cap = max(self.floor, float(cap))
        # 阈值本身的平滑系数：50 Hz 下 α=0.005 ≈ 4 s 时间常数。
        # 底噪每帧都在动，直接乘 3 会放大成二十来个单位的跳动，所以阈值要再滤一道。
        self.thr_smooth = min(0.5, max(0.0005, float(thr_smooth)))
        self._thr_primed = False                  # 首次估出底噪时直接跳到位，别慢慢爬
        self.baseline = 0.0                       # 平滑后的环境底噪，界面要显示
        self.start_frames = max(1, start_ms // 20)   # 每帧 20ms
        self.hangover_frames = max(1, hangover_ms // 20)
        self.on_utterance = on_utterance
        self.on_level = on_level
        self._noise = NoiseFloor(window_frames, percentile, smooth)
        self._buf = bytearray()
        self._above = 0
        self._quiet = 0
        self._active = False
        self._pre = collections.deque(maxlen=self.start_frames)  # 起始判定期间的前置帧
        self._hist = collections.deque(maxlen=50)    # 电平条用最近电平
        # 诊断用：50 s 的原始 RMS 和最近 5 s 的阈值，用来回答「阈值到底跳不跳、跳多少」
        self._recent_rms = collections.deque(maxlen=2500)
        self._thr_hist = collections.deque(maxlen=250)
        # 设备端 VAD 提示的使用情况：hint_frames > 0 说明能量阈值已经不再参与切句
        self.hint_frames = 0
        self.hint_speech = 0

    def reset(self):
        """丢掉当前未完成的语音段（例如朗读期间被门控时）。"""
        self._buf = bytearray()
        self._above = 0
        self._quiet = 0
        self._active = False
        self._pre.clear()

    def _retune(self, rms: float):
        """按这一帧更新底噪与阈值。返回前 threshold 已是本帧要用的值。"""
        if not self.adaptive:
            return
        # 注意：这里不能因为"语音段进行中"就跳过。噪声持续高于阈值时判据会一直处于
        # active，跳过更新等于把估计器饿死，阈值再也降不下来（试过，测试第 5 项直接挂）。
        base = self._noise.update(rms, self.threshold)
        if not self._noise.ready:
            return                                  # 还没攒够样本，沿用固定阈值
        self.baseline = base
        target = min(self.cap, max(self.floor, base * self.ratio))
        if not self._thr_primed:
            # 第一次估出底噪：直接到位。否则从固定阈值爬到目标要好几百帧，
            # 启动后头十秒钟都在用错的值。
            self.threshold = target
            self._thr_primed = True
        else:
            self.threshold += (target - self.threshold) * self.thr_smooth

    def stats(self) -> dict:
        """诊断快照：最近 50 s 的真实电平分布 + 阈值最近 5 s 的波动范围。

        用途是把「阈值该定多少」从拍脑袋变成看数据：p10 接近底噪，
        p90 才是说话电平；thr_min/thr_max 之差就是界面上的「跳动」。
        """
        vals = list(self._recent_rms)
        if not vals:
            return {}
        ordered = sorted(vals)

        def pct(p: float) -> float:
            return ordered[min(len(ordered) - 1, int(len(ordered) * p / 100.0))]

        thr = list(self._thr_hist)
        return {
            "n": len(vals),
            "p10": pct(10), "p50": pct(50), "p90": pct(90), "max": ordered[-1],
            "base": self.baseline, "thr": self.threshold,
            "thr_min": min(thr) if thr else 0.0,
            "thr_max": max(thr) if thr else 0.0,
            "over": sum(1 for v in vals if v >= self.threshold),
        }

    def feed(self, pcm: bytes, speech_hint=None):
        """speech_hint：设备端 AFE 给出的逐帧 VAD 结论（True/False）。

        有 hint 就直接用它切句——那是在 AEC + 降噪之后按频谱特征判的，
        比在 PC 端拿 RMS 猜准得多；只有老固件（flags 恒 0，等于没有 hint）才走能量阈值。
        """
        rms = _rms(pcm)
        self._retune(rms)
        self._recent_rms.append(rms)
        self._thr_hist.append(self.threshold)
        if self.on_level:
            self._hist.append(rms)
            self.on_level(rms, self.threshold, self.baseline)
        if speech_hint is None:
            above = rms >= self.threshold
        else:
            above = bool(speech_hint)
            self.hint_frames += 1
            if above:
                self.hint_speech += 1
        if above:
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
