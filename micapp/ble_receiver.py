"""BLE 音频接收线程：与 UdpReceiver 同接口，on_audio(pcm, flags)。

板子是 BLE 外设，用 Nordic UART Service 的两个特征：
  RX 6e400002（PC → 板，写）  TX 6e400003（板 → PC，通知）
每包 166 字节 = 6 字节头 + 160 字节 IMA ADPCM，解出 320 采样（20 ms）。

BLE 链路层已保证顺序与重传，所以这里不做重排；seq 只用来统计丢帧。
断线自动重连，退避 1/2/4/8 s，上限 10 s。
"""
import asyncio
import threading
import time

from micapp import adpcm

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

SCAN_TIMEOUT_S = 5.0
MAX_BACKOFF_S = 10.0


class BleReceiver(threading.Thread):
    """后台收包线程；stats() 返回与 UdpReceiver 相同的字段集合。"""

    def __init__(self, on_audio, name_prefix="MicDesktop-", stall_s: float = 0.25):
        super().__init__(daemon=True, name="ble-receiver")
        self.on_audio = on_audio
        self.name_prefix = name_prefix
        self.stall_s = stall_s
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._loop = None
        self._client = None
        self._stats = {"addr": "-", "packets": 0, "lost": 0, "bad": 0, "reordered": 0,
                       "speech_pkts": 0, "pkt_rate": 0.0, "samples_rate": 0.0}
        self._expect_seq = None
        self._win_start = None
        self._win_pkts = 0
        self._win_samples = 0
        self.connected = False
        self.status = "未连接"

    # ---------- 对外 ----------
    def run(self):
        try:
            asyncio.run(self._main())
        except Exception as exc:      # bleak 缺失/适配器不可用时不要静默退出
            self.status = "蓝牙不可用：%s" % exc

    def stop(self):
        self._stop.set()
        client = self._client
        loop = self._loop
        if client is not None and loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
            except Exception:
                pass

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    # ---------- 内部 ----------
    async def _main(self):
        from bleak import BleakClient, BleakScanner

        self._loop = asyncio.get_running_loop()
        backoff = 1.0
        while not self._stop.is_set():
            device = await self._find(BleakScanner)
            if device is None:
                self.status = "未找到 %s*，%.0f 秒后重试" % (self.name_prefix, backoff)
                await asyncio.sleep(backoff)
                backoff = min(MAX_BACKOFF_S, backoff * 2)
                continue

            self.status = "正在连接 %s" % device.name
            try:
                async with BleakClient(device) as client:
                    self._client = client
                    self.connected = True
                    self.status = "已连接 %s" % device.name
                    with self._lock:
                        self._stats["addr"] = device.name
                    await client.start_notify(TX_UUID, self._on_notify)
                    backoff = 1.0
                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(self.stall_s)
                    if not self._stop.is_set():
                        self.status = "连接断开，准备重连"
            except Exception as exc:
                self.status = "连接失败：%s" % exc
            finally:
                self.connected = False
                self._client = None
                self._expect_seq = None
            if not self._stop.is_set():
                await asyncio.sleep(backoff)
                backoff = min(MAX_BACKOFF_S, backoff * 2)

    async def _find(self, BleakScanner):
        try:
            devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_S)
        except Exception as exc:
            self.status = "扫描失败：%s" % exc
            return None
        for d in devices:
            if d.name and d.name.startswith(self.name_prefix):
                return d
        return None

    def _on_notify(self, _sender, data: bytearray):
        decoded = adpcm.decode(bytes(data))
        if decoded is None:
            with self._lock:
                self._stats["bad"] += 1
            return
        seq, flags, pcm = decoded
        with self._lock:
            self._stats["packets"] += 1
            self._win_pkts += 1
            self._win_samples += len(pcm) // 2
            if flags & 0x02:
                if flags & 0x01:
                    self._stats["speech_pkts"] += 1
            if self._expect_seq is not None:
                gap = (seq - self._expect_seq) & 0xFFFF
                if 0 < gap < 0x8000:
                    self._stats["lost"] += gap
        self._expect_seq = (seq + 1) & 0xFFFF
        self.on_audio(pcm, flags)
        self._update_rate()

    def _update_rate(self):
        now = time.monotonic()
        with self._lock:
            if self._win_start is None:
                self._win_start = now
                return
            elapsed = now - self._win_start
            if elapsed >= 2.0:
                self._stats["pkt_rate"] = self._win_pkts / elapsed
                self._stats["samples_rate"] = self._win_samples / elapsed
                self._win_start = now
                self._win_pkts = 0
                self._win_samples = 0
