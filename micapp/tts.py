"""文字转语音：Windows SAPI 合成 → 16 kHz 单声道 s16LE → UDP 下行推给板子。

SAPI 走 win32com（进程内，快）；没装 pywin32 时退回 PowerShell 调同一套 SAPI。
"""
import audioop
import os
import socket
import struct
import subprocess
import tempfile
import threading
import time
import wave

HEADER = struct.Struct("<4sBBHIIHH")  # 与固件 AUD0 头一致
RATE = 16000
FRAME = 320  # 20 ms
DEFAULT_VOICE = "Microsoft Huihui Desktop"


def _synth_com(text: str, path: str, voice: str) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        sp = win32com.client.Dispatch("SAPI.SpVoice")
        for i in range(sp.GetVoices().Count):
            v = sp.GetVoices().Item(i)
            if v.GetDescription() == voice:
                sp.Voice = v
                break
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(path, 3)  # SSFMCreateForWrite
        sp.AudioOutputStream = stream
        sp.Speak(text)
        stream.Close()
    finally:
        pythoncom.CoUninitialize()


def _synth_powershell(text: str, path: str, voice: str) -> None:
    esc = path.replace("'", "''")
    t = text.replace("'", "''")
    v = voice.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $s.SelectVoice('{v}') }} catch {{}}; "
        f"$s.SetOutputToWaveFile('{esc}'); "
        f"$s.Speak('{t}'); "
        "$s.Dispose();"
    )
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not os.path.exists(path):
        raise RuntimeError("SAPI 合成失败: %s" % (r.stderr or r.stdout))


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """返回 16 kHz / 单声道 / s16LE 的 PCM 字节。"""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="micapp_tts_")
    os.close(fd)
    try:
        try:
            _synth_com(text, path, voice)
        except Exception:
            _synth_powershell(text, path, voice)
        with wave.open(path, "rb") as w:
            ch, width, rate, n = (w.getnchannels(), w.getsampwidth(),
                                  w.getframerate(), w.getnframes())
            raw = w.readframes(n)
        if width != 2:
            raw = audioop.lin2lin(raw, width, 2)
        if ch == 2:
            raw = audioop.tomono(raw, 2, 0.5, 0.5)
        if rate != RATE:
            raw, _ = audioop.ratecv(raw, 2, 1, rate, RATE, None)
        return raw
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def send_pcm(ip: str, port: int, pcm: bytes, stop_event: threading.Event = None) -> float:
    """按 20 ms 一帧实时推送到板子，返回实际耗时。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    total = len(pcm) // (FRAME * 2)
    t0 = time.monotonic()
    try:
        for i in range(total):
            if stop_event is not None and stop_event.is_set():
                break
            chunk = pcm[i * FRAME * 2:(i + 1) * FRAME * 2]
            hdr = HEADER.pack(b"AUD0", 1, 0, 20, i, RATE, FRAME, len(chunk))
            sock.sendto(hdr + chunk, (ip, port))
            delay = t0 + (i + 1) * FRAME / RATE - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    finally:
        sock.close()
    return time.monotonic() - t0


def send_control(ip: str, port: int, text: str) -> bool:
    """发一条下行控制帧（STAT / TONE / NS:on|off），只发不等回包。"""
    if not ip:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(text.encode("utf-8"), (ip, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


class Speaker:
    """后台线程播报，避免阻塞界面。"""

    def __init__(self, port: int = 5001, voice: str = DEFAULT_VOICE):
        self.port = port
        self.voice = voice
        self._stop = threading.Event()
        self._thread = None
        # BLE 模式下行：设成一个带 send_pcm 的 BleReceiver，就不再走 UDP
        self.ble = None

    def control(self, ip: str, text: str) -> bool:
        """下行控制帧：不走播报线程，免得和正在播放的音频抢时序。"""
        return send_control(ip, self.port, text)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def speak(self, text: str, ip: str, on_status=None, on_done=None) -> bool:
        if self.busy:
            return False
        use_ble = self.ble is not None and getattr(self.ble, "connected", False)
        if not use_ble and not ip:
            if on_status:
                on_status("还未收到板子的上行包，无法确定 IP")
            if on_done:
                on_done(False)
            return False

        def run():
            ok = False
            try:
                if on_status:
                    on_status("正在合成语音…")
                pcm = synthesize(text, self.voice)
                if not pcm:
                    if on_status:
                        on_status("合成结果为空")
                    return
                dur = len(pcm) / 2 / RATE
                if on_status:
                    on_status("正在播放 %.1f 秒…" % dur)
                if use_ble:
                    took = self.ble.send_pcm(pcm, self._stop)
                else:
                    took = send_pcm(ip, self.port, pcm, self._stop)
                if took <= 0:
                    if on_status:
                        on_status("下发失败：链路已断开")
                    return
                if on_status:
                    on_status("已播放 %.1f 秒（发送耗时 %.1f 秒）" % (dur, took))
                ok = True
            except Exception as exc:  # 合成/发送失败不影响主流程
                if on_status:
                    on_status("朗读失败: %s" % exc)
            finally:
                if on_done:
                    on_done(ok)

        self._stop.clear()
        self._thread = threading.Thread(target=run, daemon=True, name="tts-speaker")
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
