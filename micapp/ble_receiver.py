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
QUICK_RETRY_S = 1.5     # 已知设备地址时的重连间隔上限（不走扫描）


class BleReceiver(threading.Thread):
    """后台收包线程；stats() 返回与 UdpReceiver 相同的字段集合。"""

    def __init__(self, on_audio, name_prefix="MicDesktop-", stall_s: float = 0.25,
                 on_control=None):
        super().__init__(daemon=True, name="ble-receiver")
        self.on_audio = on_audio
        # 板载按键等「事件类」控制帧：on_control(事件名)，如 "voice_toggle"
        self.on_control = on_control
        self.name_prefix = name_prefix
        self.stall_s = stall_s
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._loop = None
        self._client = None
        self._stats = {"addr": "-", "packets": 0, "lost": 0, "bad": 0, "reordered": 0,
                       "speech_pkts": 0, "pkt_rate": 0.0, "samples_rate": 0.0}
        self._expect_seq = None
        self._addr = None        # 上次连上的设备地址，断线后按地址直连，省掉扫描
        self._name = None        # 最近一次连上的广播名（向导/界面显示用）
        self._name = None
        self._win_start = None
        self._win_pkts = 0
        self._win_samples = 0
        self.connected = False
        self.status = "未连接"
        self.last_stat = ""
        self.last_ns = ""
        self._pong = {}          # token -> (发出时刻, threading.Event)
        self._pong_lock = threading.Lock()

    # ---------- 对外 ----------
    def run(self):
        try:
            asyncio.run(self._main())
        except Exception as exc:      # bleak 缺失/适配器不可用时不要静默退出
            self.status = "蓝牙不可用：%s" % exc

    def stop(self):
        self._cancel.set()
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
        while not self._cancel.is_set():
            client, name = None, None

            # 快路：连过一次就按地址直连。扫描一次要 5 秒，断线后光等扫描
            # 就白白多 5 秒；无线麦断一下等 15 秒没人能忍。
            if self._addr:
                try:
                    client = BleakClient(self._addr, timeout=4.0)
                    await client.connect()
                    name = self._name or self._addr
                except Exception:
                    client = None
                    self.status = "按地址直连失败，重新扫描"
                    self._addr = None

            if client is None:
                device = await self._find(BleakScanner)
                if device is None:
                    self.status = "未找到 %s*，%.1f 秒后重试" % (self.name_prefix, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(MAX_BACKOFF_S, backoff * 2)
                    continue
                name = device.name
                self.status = "正在连接 %s" % name
                try:
                    client = BleakClient(device)
                    await client.connect()
                except Exception as exc:
                    self.status = "连接失败：%s" % exc
                    client = None

            try:
                if client is not None and client.is_connected:
                    self._addr = client.address
                    self._name = name
                    self._client = client
                    self.connected = True
                    self.status = "已连接 %s" % name
                    with self._lock:
                        self._stats["addr"] = name
                    await client.start_notify(TX_UUID, self._on_notify)
                    backoff = 1.0
                    while client.is_connected and not self._cancel.is_set():
                        await asyncio.sleep(self.stall_s)
                    if not self._cancel.is_set():
                        self.status = "连接断开，准备重连"
            except Exception as exc:
                self.status = "连接失败：%s" % exc
            finally:
                self.connected = False
                self._client = None
                self._expect_seq = None
                # 断链后把设备名和速率清零：留着上一次的值，界面会一直显示
                # 「设备 MicDesktop-XXXX / 50 包每秒」，看起来像还连着。
                with self._lock:
                    self._stats["addr"] = "-"
                    self._stats["pkt_rate"] = 0.0
                    self._stats["samples_rate"] = 0.0
                    self._win_start = None
                    self._win_pkts = 0
                    self._win_samples = 0
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            if not self._cancel.is_set():
                # 知道地址时不用长退避：直连本身几秒内就能重试一次
                await asyncio.sleep(min(backoff, QUICK_RETRY_S) if self._addr else backoff)
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

    @property
    def device_name(self) -> str:
        return self._name or ""

    def _on_notify(self, _sender, data: bytearray):
        raw = bytes(data)
        # 166 字节是音频包；其它长度是控制回包（STAT / PONG），别当音频解码
        if len(raw) != adpcm.HEADER + 160:
            text = raw.decode("utf-8", "replace").strip()
            if text.startswith("PONG"):
                parts = text.split()
                if len(parts) >= 2:
                    with self._pong_lock:
                        entry = self._pong.pop(parts[1], None)
                    if entry is not None:
                        entry[1].set()
            elif text.startswith("STAT"):
                self.last_stat = text
            elif text.startswith("NS "):
                self.last_ns = text
            elif text == "VI":
                # BOOT 键长按：切一次语音输入的开关
                if self.on_control:
                    self.on_control("voice_toggle")
            elif text == "EN":
                # BOOT 键短按：当一次回车
                if self.on_control:
                    self.on_control("enter")
            return

        decoded = adpcm.decode(raw)
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

    # ---------- 下行 ----------
    def send_control(self, text: str) -> bool:
        """发一条下行控制帧（STAT / TONE / NS:on|off），与 UDP 侧同名同义。"""
        client, loop = self._client, self._loop
        if client is None or loop is None or not self.connected:
            return False
        try:
            asyncio.run_coroutine_threadsafe(
                client.write_gatt_char(RX_UUID, text.encode("utf-8"), response=False),
                loop).result(timeout=1.0)
            return True
        except Exception:
            return False

    def send_pcm(self, pcm: bytes, stop_event=None) -> float:
        """把 16k mono PCM 按 20 ms 一帧编成 ADPCM 写进 RX 特征，实时节流。

        返回实际耗时；未连接或写失败提前返回。response=False（写无响应），
        否则每个包都要等一个 ATT 确认，吞吐撑不住。
        """
        client, loop = self._client, self._loop
        if client is None or loop is None or not self.connected:
            return 0.0
        total = len(pcm) // (adpcm.SAMPLES * 2)
        if total <= 0:
            return 0.0
        t0 = time.perf_counter()
        for i in range(total):
            if stop_event is not None and stop_event.is_set():
                break
            chunk = pcm[i * 640:(i + 1) * 640]
            packet = adpcm.encode(chunk, seq=i & 0xFFFF, flags=0)
            fut = asyncio.run_coroutine_threadsafe(
                client.write_gatt_char(RX_UUID, packet, response=False), loop)
            try:
                fut.result(timeout=1.0)
            except Exception:
                break
            delay = t0 + (i + 1) * 0.02 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        return time.perf_counter() - t0

    def ping(self, timeout: float = 2.0):
        """发 PING 等 PONG，返回往返秒数；失败返回 None。

        板子只回显 token，往返时间用本机时钟算，不需要两边时钟同步。
        计时用 perf_counter：Windows 上 time.monotonic() 只有 ~15.6 ms 精度，
        量 BLE 往返会被量化成 0/16/31/47…，看不出真实分布。
        """
        client, loop = self._client, self._loop
        if client is None or loop is None or not self.connected:
            return None
        token = str(int(time.perf_counter() * 1000) & 0xFFFFFF)
        event = threading.Event()
        with self._pong_lock:
            self._pong[token] = (time.perf_counter(), event)
        t0 = time.perf_counter()
        try:
            fut = asyncio.run_coroutine_threadsafe(
                client.write_gatt_char(RX_UUID, ("PING " + token).encode(), response=False), loop)
            fut.result(timeout=1.0)
        except Exception:
            with self._pong_lock:
                self._pong.pop(token, None)
            return None
        ok = event.wait(timeout)
        with self._pong_lock:
            self._pong.pop(token, None)
        return (time.perf_counter() - t0) if ok else None

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
