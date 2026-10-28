"""tkinter 主窗口。线程安全：所有跨线程更新走 ui_queue + root.after 轮询。"""
import queue
import time
import tkinter as tk
from tkinter import scrolledtext, ttk


class MainWindow:
    def __init__(self, root: tk.Tk, cfg: dict, receiver, engine):
        self.root = root
        self.cfg = cfg
        self.receiver = receiver
        self.engine = engine
        self.ui_q: queue.Queue = queue.Queue()
        root.title("无线麦克风接收台 - ESP32-S3 INMP441")
        root.geometry("820x560")
        self._build()
        root.after(100, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        from micapp.config import ENGINE_LABELS
        engine_label = ENGINE_LABELS.get(self.cfg["stt_engine"], self.cfg["stt_engine"])
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill=tk.X)
        self.lbl_dev = ttk.Label(top, text="设备: 等待中", width=28)
        self.lbl_rate = ttk.Label(top, text="0.0 pkt/s", width=14)
        self.lbl_lost = ttk.Label(top, text="丢包 0", width=12)
        self.lbl_engine = ttk.Label(top, text=f"引擎: {engine_label}", width=20)
        self.lbl_dev.pack(side=tk.LEFT, padx=4)
        self.lbl_rate.pack(side=tk.LEFT, padx=4)
        self.lbl_lost.pack(side=tk.LEFT, padx=4)
        self.lbl_engine.pack(side=tk.LEFT, padx=4)
        self.canvas = tk.Canvas(top, width=160, height=18, bg="#222")
        self.canvas.pack(side=tk.RIGHT, padx=4)
        self.level_bar = self.canvas.create_rectangle(0, 2, 0, 16, fill="#4cd964", width=0)

        self.txt = scrolledtext.ScrolledText(self.root, font=("Microsoft YaHei", 12), wrap=tk.WORD)
        self.txt.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.txt.tag_config("ts", foreground="#888")
        self.txt.insert(tk.END, "等待语音输入…\n")
        self.txt.config(state=tk.DISABLED)

        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="清空", command=self._clear).pack(side=tk.RIGHT)
        creds_ok = all(self.cfg["xfyun"].get(k) for k in ("app_id", "api_key", "api_secret"))
        tip = "" if creds_ok else "  ⚠ 请在 config.json 填入讯飞凭据后重启（语音识别未启用）"
        self.lbl_tip = ttk.Label(bottom, text=tip, foreground="#c00")
        self.lbl_tip.pack(side=tk.RIGHT, padx=8)

    def ui(self, *item):
        self.ui_q.put(item)

    def _poll(self):
        try:
            while True:
                item = self.ui_q.get_nowait()
                kind = item[0]
                if kind == "stats":
                    s = item[1]
                    self.lbl_dev.config(text=f"设备: {s['addr']}")
                    self.lbl_rate.config(text=f"{s['pkt_rate']:.1f} pkt/s")
                    self.lbl_lost.config(text=f"丢包 {s['lost']}  坏帧 {s['bad']}")
                elif kind == "level":
                    self._draw_level(item[1])
                elif kind == "text":
                    _, text, idx = item
                    self.txt.config(state=tk.NORMAL)
                    self.txt.insert(tk.END, time.strftime("[%H:%M:%S] "), "ts")
                    self.txt.insert(tk.END, text + "\n")
                    self.txt.see(tk.END)
                    self.txt.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _draw_level(self, rms: float):
        w = int(self.canvas.winfo_width())
        ratio = min(1.0, rms / 3000.0)
        self.canvas.coords(self.level_bar, 0, 2, w * ratio, 16)
        color = "#4cd964" if rms < self.cfg["vad"]["rms_threshold"] else "#ff9500"
        self.canvas.itemconfig(self.level_bar, fill=color)

    def _clear(self):
        self.txt.config(state=tk.NORMAL)
        self.txt.delete("1.0", tk.END)
        self.txt.config(state=tk.DISABLED)

    def _on_close(self):
        try:
            self.receiver.stop()
            self.engine.stop()
        finally:
            self.root.after(200, self.root.destroy)
