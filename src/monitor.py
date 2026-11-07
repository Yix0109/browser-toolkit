"""悬浮监控小窗（自己实现的 LiteMonitor 同类）：实时延迟 / 丢包 + CPU / 内存 / 网速。"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

import netfix

CONF = Path(os.path.expandvars(r"%LOCALAPPDATA%\BrowserCleaner\monitor.json"))

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)

SAMPLE_MAX = 60
BG = "#111827"
FG = "#e5e7eb"
MUTED = "#9ca3af"
GREEN = "#34d399"
ORANGE = "#fbbf24"
RED = "#f87171"


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class MIB_IFROW(ctypes.Structure):
    _fields_ = [
        ("wszName", wintypes.WCHAR * 256),
        ("dwIndex", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("dwMtu", wintypes.DWORD),
        ("dwSpeed", wintypes.DWORD),
        ("dwPhysAddrLen", wintypes.DWORD),
        ("bPhysAddr", ctypes.c_ubyte * 8),
        ("dwAdminStatus", wintypes.DWORD),
        ("dwOperStatus", wintypes.DWORD),
        ("dwLastChange", wintypes.DWORD),
        ("dwInOctets", wintypes.DWORD),
        ("dwInUcastPkts", wintypes.DWORD),
        ("dwInNUcastPkts", wintypes.DWORD),
        ("dwInDiscards", wintypes.DWORD),
        ("dwInErrors", wintypes.DWORD),
        ("dwInUnknownProtos", wintypes.DWORD),
        ("dwOutOctets", wintypes.DWORD),
        ("dwOutUcastPkts", wintypes.DWORD),
        ("dwOutNUcastPkts", wintypes.DWORD),
        ("dwOutDiscards", wintypes.DWORD),
        ("dwOutErrors", wintypes.DWORD),
        ("dwOutQLen", wintypes.DWORD),
        ("dwDescrLen", wintypes.DWORD),
        ("bDescr", ctypes.c_ubyte * 256),
    ]


class MIB_IFTABLE(ctypes.Structure):
    _fields_ = [("dwNumEntries", wintypes.DWORD), ("table", MIB_IFROW * 64)]


def _ft(value: FILETIME) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


class SystemStats:
    """CPU / 内存 / 网速的增量统计。"""

    def __init__(self) -> None:
        self._cpu_prev: tuple[int, int] | None = None
        self._net_prev: tuple[float, int, int] | None = None
        self._wrap = (0, 0)

    def cpu_percent(self) -> float:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return 0.0
        i, k, u = _ft(idle), _ft(kernel), _ft(user)
        total = k + u
        if self._cpu_prev is None:
            self._cpu_prev = (i, total)
            return 0.0
        pi, pt = self._cpu_prev
        self._cpu_prev = (i, total)
        d_total = total - pt
        d_idle = i - pi
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, (d_total - d_idle) * 100.0 / d_total))

    def mem_percent(self) -> float:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not _k32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0.0
        return float(status.dwMemoryLoad)

    def net_rate(self) -> tuple[float, float]:
        """返回 (下载字节/秒, 上传字节/秒)。"""
        size = wintypes.ULONG(0)
        try:
            _iphlp.GetIfTable(None, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            if _iphlp.GetIfTable(buf, ctypes.byref(size)) != 0:
                return 0.0, 0.0
            table = ctypes.cast(buf, ctypes.POINTER(MIB_IFTABLE)).contents
            in_sum = out_sum = 0
            for i in range(table.dwNumEntries):
                row = table.table[i]
                if row.dwOperStatus != 1 or row.dwType == 24:  # 只算已启用、非环回
                    continue
                in_sum += int(row.dwInOctets)
                out_sum += int(row.dwOutOctets)
        except Exception:
            return 0.0, 0.0

        now = time.time()
        # 32 位计数器回绕处理
        if self._net_prev is not None:
            _t, pin, pout = self._net_prev
            if in_sum < pin:
                self._wrap = (self._wrap[0] + 2**32, self._wrap[1])
            if out_sum < pout:
                self._wrap = (self._wrap[0], self._wrap[1] + 2**32)
        in_total = in_sum + self._wrap[0]
        out_total = out_sum + self._wrap[1]
        if self._net_prev is None:
            self._net_prev = (now, in_total, out_total)
            return 0.0, 0.0
        prev_t, prev_in, prev_out = self._net_prev
        self._net_prev = (now, in_total, out_total)
        dt = max(0.05, now - prev_t)
        return (in_total - prev_in) / dt, (out_total - prev_out) / dt


def human_rate(value: float) -> str:
    if value < 1024:
        return f"{value:.0f} B/s"
    if value < 1024 * 1024:
        return f"{value / 1024:.0f} KB/s"
    return f"{value / 1024 / 1024:.1f} MB/s"


class MiniMonitor(tk.Toplevel):
    """无边框、总在最前的悬浮窗。左键拖动，右键菜单，双击打开主界面。"""

    def __init__(self, master: tk.Misc, target: str, on_open_main=None) -> None:
        super().__init__(master)
        self.target = target.strip()
        self.on_open_main = on_open_main
        self.running = True
        self.stats = SystemStats()
        self.samples: list[int | None] = []
        self.last: netfix.PingResult | None = None
        self.q: queue.Queue = queue.Queue()
        self._ip = ""
        self._cpu = 0.0
        self._mem = 0.0
        self._down = 0.0
        self._up = 0.0
        self._loss_total = 0
        self._sent_total = 0

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG)
        self.geometry(self._initial_geometry())
        self._build()
        self._bind_drag()

        threading.Thread(target=self._ping_loop, daemon=True).start()
        self.after(200, self._tick)
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ---------------------------------------------------------- 界面
    def _initial_geometry(self) -> str:
        x, y = 60, 60
        try:
            if CONF.is_file():
                data = json.loads(CONF.read_text(encoding="utf-8"))
                x, y = int(data.get("x", x)), int(data.get("y", y))
        except Exception:
            pass
        return f"236x154+{x}+{y}"

    def _build(self) -> None:
        wrap = tk.Frame(self, bg=BG, highlightthickness=1, highlightbackground="#374151")
        wrap.pack(fill="both", expand=True)

        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="x", padx=9, pady=(7, 0))
        self.dot = tk.Canvas(top, width=9, height=9, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", pady=4)
        self._dot_id = self.dot.create_oval(1, 1, 8, 8, fill=MUTED, outline="")
        self.title_label = tk.Label(top, text=self.target, bg=BG, fg=MUTED,
                                    font=("Microsoft YaHei UI", 8))
        self.title_label.pack(side="left", padx=5)
        close = tk.Label(top, text="✕", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9),
                         cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())

        mid = tk.Frame(wrap, bg=BG)
        mid.pack(fill="x", padx=9, pady=(2, 0))
        self.latency = tk.Label(mid, text="--", bg=BG, fg=FG,
                                font=("Consolas", 20, "bold"))
        self.latency.pack(side="left")
        self.unit = tk.Label(mid, text="ms", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.unit.pack(side="left", padx=(2, 0), pady=(9, 0))
        self.loss = tk.Label(mid, text="丢包 0%", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.loss.pack(side="right", pady=(10, 0))

        self.spark = tk.Canvas(wrap, width=208, height=28, bg=BG, highlightthickness=0)
        self.spark.pack(fill="x", padx=9, pady=(1, 0))

        self.info = tk.Label(wrap, text="CPU --  内存 --", bg=BG, fg=MUTED,
                             font=("Microsoft YaHei UI", 8), anchor="w")
        self.info.pack(fill="x", padx=9)
        self.net = tk.Label(wrap, text="↓ --  ↑ --", bg=BG, fg=MUTED,
                            font=("Microsoft YaHei UI", 8), anchor="w")
        self.net.pack(fill="x", padx=9, pady=(0, 7))

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开主界面", command=self._open_main)
        menu.add_command(label="重新开始统计", command=self._restart)
        menu.add_separator()
        menu.add_command(label="关闭监控", command=self.close)
        for widget in (wrap, top, mid, self.latency, self.info, self.net, self.spark):
            widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        self.bind("<Double-Button-1>", lambda _e: self._open_main())

    def _bind_drag(self) -> None:
        offset = {"x": 0, "y": 0}

        def start(event):
            offset["x"], offset["y"] = event.x, event.y

        def move(event):
            self.geometry(f"+{event.x_root - offset['x']}+{event.y_root - offset['y']}")

        def stop(_event):
            self.save_position()

        for widget in (self, self.spark, self.info, self.net, self.latency):
            widget.bind("<Button-1>", start)
            widget.bind("<B1-Motion>", move)
            widget.bind("<ButtonRelease-1>", stop)

    # ---------------------------------------------------------- 数据
    def _ping_loop(self) -> None:
        pinger = netfix.Pinger()
        try:
            while self.running:
                target = self.target
                try:
                    if not self._ip or target != getattr(self, "_ip_target", None):
                        self._ip = socket.gethostbyname(target)
                        self._ip_target = target
                    status, rtt, text = pinger.ping_once(self._ip, 1200)
                except Exception as exc:  # noqa: BLE001
                    status, rtt, text = -1, 0, str(exc)
                self.q.put((status, rtt, text))
                for _ in range(9):
                    if not self.running:
                        break
                    time.sleep(0.1)
        finally:
            pinger.close()

    def _tick(self) -> None:
        if not self.running:
            return
        try:
            while True:
                status, rtt, text = self.q.get_nowait()
                self._sent_total += 1
                if status == 0:
                    self.samples.append(rtt)
                else:
                    self.samples.append(None)
                    self._loss_total += 1
                if len(self.samples) > SAMPLE_MAX:
                    self.samples.pop(0)
        except queue.Empty:
            pass

        if self.samples:
            recent = self.samples[-30:]
            last = recent[-1] if recent else None
            if last is None:
                self.latency.configure(text="超时", fg=RED)
                self.unit.configure(text="")
            else:
                self.latency.configure(text=str(last), fg=self._color(last))
                self.unit.configure(text="ms")
            window_loss = sum(1 for s in recent if s is None) * 100.0 / len(recent)
            self.loss.configure(text=f"丢包 {window_loss:.0f}%",
                                fg=RED if window_loss else MUTED)
            self.dot.itemconfigure(self._dot_id, fill=GREEN if last is not None else RED)

        self._cpu = self.stats.cpu_percent()
        self._mem = self.stats.mem_percent()
        self._down, self._up = self.stats.net_rate()
        self.info.configure(text=f"CPU {self._cpu:.0f}%   内存 {self._mem:.0f}%")
        self.net.configure(text=f"↓ {human_rate(self._down)}   ↑ {human_rate(self._up)}")
        self._draw_spark()
        self.after(1000, self._tick)

    @staticmethod
    def _color(rtt: int) -> str:
        if rtt < 60:
            return GREEN
        if rtt < 150:
            return ORANGE
        return RED

    def _draw_spark(self) -> None:
        self.spark.delete("all")
        width = max(60, self.spark.winfo_width())
        height = 28
        values = [v for v in self.samples if v is not None]
        top = max(values + [40])
        base = height - 3
        step = width / max(1, SAMPLE_MAX - 1)
        offset = SAMPLE_MAX - len(self.samples)
        prev = None
        for i, value in enumerate(self.samples):
            x = (i + offset) * step
            if value is None:
                self.spark.create_line(x, 2, x, base, fill="#7f1d1d")
                prev = None
                continue
            y = base - (value / top) * (base - 3)
            if prev:
                self.spark.create_line(prev[0], prev[1], x, y, fill=self._color(value), width=1)
            prev = (x, y)

    # ---------------------------------------------------------- 其它
    def save_position(self) -> None:
        try:
            CONF.parent.mkdir(parents=True, exist_ok=True)
            CONF.write_text(
                json.dumps({"x": self.winfo_x(), "y": self.winfo_y()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _open_main(self) -> None:
        if self.on_open_main:
            self.on_open_main()

    def _restart(self) -> None:
        self.samples.clear()
        self._loss_total = self._sent_total = 0

    def close(self) -> None:
        self.running = False
        self.save_position()
        try:
            self.destroy()
        except Exception:
            pass
