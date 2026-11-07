"""图形界面：一键清理 + 极速/兼容模式切换。"""
from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import cleaner
import kernel
import litemon
import monitor
import netfix
from browsers import CATEGORIES, Browser, detect_browsers
from winproc import is_admin

APP_TITLE = "浏览器一键清理 · 极速/兼容模式切换"
BG = "#f4f6fb"
CARD = "#ffffff"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
MUTED = "#6b7280"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1010, sw - 80), min(770, sh - 90)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(880, 640)
        self.configure(bg=BG)

        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.browsers: list[Browser] = []
        self.selected: set[str] = set()
        self.cat_vars: dict[str, tk.BooleanVar] = {}
        self.mode_target: Browser | None = None

        self._init_style()
        self._build_header()
        self._build_notebook()
        self._build_statusbar()
        self._build_log()

        self.after(80, self._poll)
        self.log("准备就绪。正在检测已安装的浏览器…")
        self.refresh_browsers()

    # ------------------------------------------------------------ 样式
    def _init_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        self.option_add("*Font", ("Microsoft YaHei UI", 9))
        style.configure(".", font=("Microsoft YaHei UI", 9))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("TLabel", background=BG, foreground="#111827")
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("H1.TLabel", font=("Microsoft YaHei UI", 16, "bold"), background=BG)
        style.configure("H2.TLabel", font=("Microsoft YaHei UI", 10, "bold"), background=BG)
        style.configure("Card.TLabel", background=CARD)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
        style.configure("TLabelframe", background=BG, relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"), foreground="#111827")
        style.configure("Treeview", rowheight=26, fieldbackground=CARD, background=CARD)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TCheckbutton", background=BG)
        style.configure("Card.TCheckbutton", background=CARD)
        style.configure("Horizontal.TProgressbar", background=ACCENT)

    # ------------------------------------------------------------ 顶部
    def _build_header(self) -> None:
        header = tk.Frame(self, bg="#1e3a8a", height=76)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        left = tk.Frame(header, bg="#1e3a8a")
        left.pack(side="left", padx=20, pady=10)
        tk.Label(left, text="浏览器一键清理 · 极速 / 兼容模式切换",
                 font=("Microsoft YaHei UI", 15, "bold"), bg="#1e3a8a", fg="white").pack(anchor="w")
        tk.Label(left, text="支持 Chrome、Edge、搜狗高速浏览器、115 浏览器   |   清理前自动关闭浏览器，安全释放磁盘空间",
                 font=("Microsoft YaHei UI", 8), bg="#1e3a8a", fg="#c7d2fe").pack(anchor="w", pady=(3, 0))

        right = tk.Frame(header, bg="#1e3a8a")
        right.pack(side="right", padx=20)
        tk.Label(right, text="管理员权限" if is_admin() else "普通用户权限",
                 font=("Microsoft YaHei UI", 8), bg="#1e3a8a",
                 fg="#86efac" if is_admin() else "#fca5a5").pack(anchor="e")

    # ------------------------------------------------------------ 主体
    def _build_notebook(self) -> None:
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=14, pady=(12, 6))
        self.tab_clean = ttk.Frame(self.nb, padding=12)
        self.tab_mode = ttk.Frame(self.nb, padding=12)
        self.tab_net = ttk.Frame(self.nb, padding=12)
        self.tab_ping = ttk.Frame(self.nb, padding=12)
        self.tab_lite = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_clean, text="  一键清理  ")
        self.nb.add(self.tab_mode, text="  极速 / 兼容模式  ")
        self.nb.add(self.tab_net, text="  网络修复  ")
        self.nb.add(self.tab_ping, text="  延迟测试  ")
        self.nb.add(self.tab_lite, text="  LiteMonitor  ")
        self._build_clean_tab()
        self._build_mode_tab()
        self._build_net_tab()
        self._build_ping_tab()
        self._build_lite_tab()

    # -------------------------------------------------------- 清理标签页
    def _build_clean_tab(self) -> None:
        root = self.tab_clean

        # 浏览器列表
        box = ttk.LabelFrame(root, text=" 浏览器（点击“选中”列可勾选/取消） ", padding=8)
        box.pack(fill="x")
        cols = ("sel", "name", "ver", "state", "path")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=4, selectmode="none")
        heads = {"sel": ("选中", 52), "name": ("浏览器", 150), "ver": ("版本", 110),
                 "state": ("状态", 80), "path": ("用户数据目录", 420)}
        for c, (text, width) in heads.items():
            self.tree.heading(c, text=text)
            self.tree.column(c, width=width, anchor="center" if c in ("sel", "state") else "w",
                             stretch=(c == "path"))
        self.tree.pack(fill="x", side="left", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._on_tree_click)

        # 清理内容
        box2 = ttk.LabelFrame(root, text=" 清理内容 ", padding=8)
        box2.pack(fill="x", pady=(8, 0))
        for i, (key, title, desc) in enumerate(CATEGORIES):
            var = tk.BooleanVar(value=(key == "cache"))
            self.cat_vars[key] = var
            row, col = divmod(i, 2)
            cell = ttk.Frame(box2)
            cell.grid(row=row, column=col, sticky="w", padx=(0, 24), pady=3)
            ttk.Checkbutton(cell, text=title, variable=var, style="TCheckbutton").pack(anchor="w")
            ttk.Label(cell, text=desc, style="Muted.TLabel", font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        box2.columnconfigure(0, weight=1)
        box2.columnconfigure(1, weight=1)

        # 选项
        box3 = ttk.LabelFrame(root, text=" 选项 ", padding=8)
        box3.pack(fill="x", pady=(8, 0))
        self.opt_close = tk.BooleanVar(value=True)
        self.opt_backup = tk.BooleanVar(value=True)
        self.opt_reopen = tk.BooleanVar(value=False)
        ttk.Checkbutton(box3, text="清理前自动关闭浏览器（缓存文件被占用时必须关闭）",
                        variable=self.opt_close).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(box3, text="清理 Cookie / 历史 / 表单前先备份一份（可回滚）",
                        variable=self.opt_backup).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box3, text="清理完成后重新打开刚才被关闭的浏览器",
                        variable=self.opt_reopen).grid(row=2, column=0, sticky="w")

        # 按钮
        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(10, 0))
        self.btn_hero = tk.Button(
            bar, text="⚡ 一键极速清理", command=self.on_quick_clean,
            font=("Microsoft YaHei UI", 11, "bold"), bg=ACCENT, fg="white",
            activebackground=ACCENT_DARK, activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=22, pady=8,
        )
        self.btn_hero.pack(side="left")
        self.btn_custom = tk.Button(
            bar, text="按勾选清理", command=self.on_custom_clean,
            font=("Microsoft YaHei UI", 9), bg="#e5e7eb", fg="#111827",
            activebackground="#d1d5db", relief="flat", bd=0, cursor="hand2", padx=16, pady=8,
        )
        self.btn_custom.pack(side="left", padx=8)
        ttk.Button(bar, text="刷新列表", command=self.refresh_browsers).pack(side="left")
        ttk.Button(bar, text="打开备份目录", command=self.open_backup_dir).pack(side="left", padx=8)
        ttk.Button(bar, text="打开浏览器缓存目录", command=self.open_data_dir).pack(side="left")

        self.summary_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.summary_var, style="H2.TLabel").pack(side="right")

    # -------------------------------------------------------- 模式标签页
    def _build_mode_tab(self) -> None:
        root = self.tab_mode

        box = ttk.LabelFrame(root, text=" 目标浏览器 ", padding=10)
        box.pack(fill="x")
        self.mode_combo = ttk.Combobox(box, state="readonly", width=34)
        self.mode_combo.pack(side="left")
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_mode_panel())
        self.mode_label = ttk.Label(box, text="", style="H2.TLabel")
        self.mode_label.pack(side="left", padx=14)
        ttk.Button(box, text="刷新状态", command=self._sync_mode_panel).pack(side="right")

        btns = ttk.LabelFrame(root, text=" 切换内核模式 ", padding=10)
        btns.pack(fill="x", pady=(10, 0))

        def big(parent, text, sub, color, cmd):
            f = tk.Frame(parent, bg=color, cursor="hand2", padx=14, pady=10)
            t = tk.Label(f, text=text, font=("Microsoft YaHei UI", 11, "bold"), bg=color, fg="white")
            t.pack(anchor="w")
            d = tk.Label(f, text=sub, font=("Microsoft YaHei UI", 8), bg=color, fg="#e5e7eb")
            d.pack(anchor="w")
            for w in (f, t, d):
                w.bind("<Button-1>", lambda _e, c=cmd: c())
            return f

        big(btns, "极速模式", "Chromium 内核，速度最快，绝大多数网站都用它",
            "#059669", lambda: self.on_set_mode(kernel.MODE_FAST)).pack(side="left", padx=(0, 10))
        big(btns, "兼容模式", "IE 内核，老网站、网银、内部系统打不开时用",
            "#b45309", lambda: self.on_set_mode(kernel.MODE_COMPAT)).pack(side="left", padx=(0, 10))
        big(btns, "智能模式", "由浏览器自己判断，遇 IE-only 网站自动切换",
            "#4b5563", lambda: self.on_set_mode(kernel.MODE_SMART)).pack(side="left")

        launch_bar = ttk.Frame(root)
        launch_bar.pack(fill="x", pady=(12, 0))
        ttk.Button(launch_bar, text="以当前模式启动浏览器", command=self.on_launch).pack(side="left")
        ttk.Button(launch_bar, text="打开浏览器自带的设置页", command=self.on_open_settings).pack(side="left", padx=8)

        info = ttk.LabelFrame(root, text=" 说明 ", padding=10)
        info.pack(fill="both", expand=True, pady=(10, 0))
        self.mode_info = tk.Text(info, wrap="word", relief="flat", bg=CARD, bd=0,
                                 font=("Microsoft YaHei UI", 9), height=10, cursor="arrow")
        self.mode_info.pack(fill="both", expand=True)
        self.mode_info.tag_configure("h", font=("Microsoft YaHei UI", 9, "bold"), foreground="#111827")
        self.mode_info.tag_configure("muted", foreground=MUTED, font=("Microsoft YaHei UI", 8))
        self.mode_info.insert("1.0", "正在检测…")
        self.mode_info.configure(state="disabled")

    # -------------------------------------------------------- 网络修复标签页
    def _build_net_tab(self) -> None:
        root = self.tab_net
        self.net_vars: dict[str, tk.BooleanVar] = {}

        box = ttk.LabelFrame(root, text=" 当前网络状态 ", padding=10)
        box.pack(fill="x")
        self.net_status = tk.Text(box, height=6, wrap="none", relief="flat", bg=CARD, bd=0,
                                  font=("Microsoft YaHei UI", 9), cursor="arrow")
        self.net_status.pack(side="left", fill="both", expand=True)
        self.net_status.insert("1.0", "正在读取…")
        self.net_status.configure(state="disabled")

        row = ttk.Frame(box)
        row.pack(side="right", fill="y", padx=(10, 0))
        ttk.Button(row, text="刷新状态", command=self.refresh_net_status).pack(fill="x")
        ttk.Button(row, text="连通性自检", command=self.on_net_test).pack(fill="x", pady=6)

        box2 = ttk.LabelFrame(root, text=" 修复项目（勾选后点“执行勾选项目”） ", padding=10)
        box2.pack(fill="x", pady=(10, 0))
        for i, act in enumerate(netfix.ACTIONS):
            var = tk.BooleanVar(value=act.key in netfix.QUICK_KEYS)
            self.net_vars[act.key] = var
            cell = ttk.Frame(box2)
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 20), pady=3)
            label = act.title
            if act.needs_admin:
                label += " 🔒"
            if act.needs_reboot:
                label += "（需重启）"
            ttk.Checkbutton(cell, text=label, variable=var).pack(anchor="w")
            ttk.Label(cell, text=act.desc, style="Muted.TLabel",
                      font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        box2.columnconfigure(0, weight=1)
        box2.columnconfigure(1, weight=1)

        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(14, 0))
        self.btn_net_quick = tk.Button(
            bar, text="🧰 一键网络修复", command=self.on_quick_netfix,
            font=("Microsoft YaHei UI", 11, "bold"), bg="#0f766e", fg="white",
            activebackground="#115e59", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=20, pady=8,
        )
        self.btn_net_quick.pack(side="left")
        self.btn_net_custom = tk.Button(
            bar, text="执行勾选项目", command=self.on_custom_netfix,
            font=("Microsoft YaHei UI", 9), bg="#e5e7eb", fg="#111827",
            activebackground="#d1d5db", relief="flat", bd=0, cursor="hand2", padx=16, pady=8,
        )
        self.btn_net_custom.pack(side="left", padx=8)
        ttk.Button(bar, text="以管理员身份重启", command=self.elevate).pack(side="left")
        ttk.Button(bar, text="打开网络连接设置", command=self.open_net_settings).pack(side="left", padx=8)

        ttk.Label(root, text="🔒 = 需要管理员权限。没有权限时工具会提示你先提权重启，不会静默失败。",
                  style="Muted.TLabel", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(10, 0))

    def refresh_net_status(self) -> None:
        def work():
            info = netfix.network_status()
            return info

        def done(info):
            self.net_status.configure(state="normal")
            self.net_status.delete("1.0", "end")
            width = max([len(k) for k in info] + [4])
            for k, v in info.items():
                self.net_status.insert("end", f"{k.ljust(width)}   {v}\n")
            self.net_status.configure(state="disabled")
            self.status_var.set("网络状态已刷新")

        self.run_task(work, done)

    def on_net_test(self) -> None:
        def work():
            return netfix.test_connectivity()

        def done(lines):
            self.net_status.configure(state="normal")
            self.net_status.insert("end", "\n" + "\n".join(lines) + "\n")
            self.net_status.see("end")
            self.net_status.configure(state="disabled")
            self.log("连通性自检：" + " / ".join(lines))

        self.run_task(work, done)

    def on_quick_netfix(self) -> None:
        for k, var in self.net_vars.items():
            var.set(k in netfix.QUICK_KEYS)
        self._start_netfix(list(netfix.QUICK_KEYS), quick=True)

    def on_custom_netfix(self) -> None:
        keys = [k for k, v in self.net_vars.items() if v.get()]
        if not keys:
            messagebox.showinfo("提示", "请至少勾选一个修复项目。")
            return
        reboot_items = [netfix.by_key(k).title for k in keys if netfix.by_key(k) and netfix.by_key(k).needs_reboot]
        msg = "即将执行：\n\n" + "\n".join("· " + netfix.by_key(k).title for k in keys)
        if reboot_items:
            msg += "\n\n其中会重置协议栈，执行后需要重启电脑才能完全生效，期间可能断网。"
        if not messagebox.askyesno("确认执行", msg + "\n\n继续吗？"):
            return
        self._start_netfix(keys, quick=False)

    def _start_netfix(self, keys: list[str], quick: bool) -> None:
        need_admin = [netfix.by_key(k).title for k in keys if netfix.by_key(k) and netfix.by_key(k).needs_admin]
        if need_admin and not is_admin():
            answer = messagebox.askyesno(
                "需要管理员权限",
                "下面这些项目需要管理员权限：\n\n" + "\n".join("· " + t for t in need_admin) +
                "\n\n要以管理员身份重新启动本工具吗？（当前窗口会关闭，重新打开后再点一次即可）",
            )
            if answer:
                self.elevate()
            return

        def work():
            self.log("=" * 52)
            self.log(("一键网络修复" if quick else "自定义网络修复") + f"：{len(keys)} 个项目")
            return netfix.run_many(keys, log=self.log)

        def done(payload):
            ok_count, fail_count, reboot = payload
            self.status_var.set(f"网络修复完成：成功 {ok_count}，失败 {fail_count}")
            self.refresh_net_status()
            if reboot:
                messagebox.showwarning(
                    "需要重启",
                    "Winsock / TCP-IP 已经重置。\n\n请重启电脑后再生效（重启前网络可能仍然异常）。",
                )
            elif fail_count:
                messagebox.showwarning("部分失败", f"有 {fail_count} 项执行失败，详情见下方运行日志。")
            else:
                messagebox.showinfo("完成", f"{ok_count} 项全部执行成功。")

        self.run_task(work, done)

    def elevate(self) -> None:
        exe = sys.executable
        params = "" if getattr(sys, "frozen", False) else f'"{os.path.abspath(sys.argv[0])}"'
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, os.getcwd(), 1)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("提权失败", str(exc))
            return
        if ret <= 32:
            messagebox.showerror("提权失败", "用户取消了管理员授权，或系统拒绝提权。")
            return
        self.destroy()

    def open_net_settings(self) -> None:
        try:
            os.startfile("ms-settings:network")
        except OSError:
            subprocess.Popen(["control.exe", "ncpa.cpl"], close_fds=True)

    # -------------------------------------------------------- 延迟测试标签页
    def _build_ping_tab(self) -> None:
        root = self.tab_ping

        box = ttk.LabelFrame(root, text=" 测试目标（每行一个域名 / IP / 完整接口地址，如 https://api.xxx.com/v1） ", padding=8)
        box.pack(fill="x")
        self.ping_input = tk.Text(box, height=4, wrap="none", relief="solid", bd=1,
                                  font=("Consolas", 9))
        self.ping_input.pack(side="left", fill="both", expand=True)
        self.ping_input.insert("1.0", "\n".join(netfix.default_targets()))
        side = ttk.Frame(box)
        side.pack(side="right", fill="y", padx=(8, 0))
        self.ping_tcp = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="同时测 80/443 端口", variable=self.ping_tcp).pack(anchor="w")
        self.ping_count = tk.IntVar(value=4)
        row = ttk.Frame(side)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="次数").pack(side="left")
        ttk.Spinbox(row, from_=1, to=20, width=5, textvariable=self.ping_count).pack(side="left", padx=4)

        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(8, 0))
        self.btn_ping = tk.Button(
            bar, text="▶ 开始测试", command=self.on_ping_test,
            font=("Microsoft YaHei UI", 10, "bold"), bg="#1d4ed8", fg="white",
            activebackground="#1e40af", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=18, pady=6,
        )
        self.btn_ping.pack(side="left")
        self.btn_monitor = tk.Button(
            bar, text="📊 悬浮监控这个目标", command=self.open_monitor,
            font=("Microsoft YaHei UI", 9), bg="#0f766e", fg="white",
            activebackground="#115e59", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=14, pady=6,
        )
        self.btn_monitor.pack(side="left", padx=8)
        ttk.Button(bar, text="用默认目标", command=self._reset_ping_targets).pack(side="left")
        self.ping_summary = tk.StringVar(value="点「开始测试」逐个域名/IP 测通不通、延迟多少。")
        ttk.Label(bar, textvariable=self.ping_summary, style="Muted.TLabel").pack(side="right")

        cols = ("host", "ip", "loss", "min", "avg", "max", "tcp")
        heads = {"host": ("目标", 250), "ip": ("解析 IP", 120), "loss": ("丢包", 55),
                 "min": ("最快", 55), "avg": ("平均", 55), "max": ("最慢", 55),
                 "tcp": ("端口 / HTTP 结果", 250)}
        wrap = ttk.Frame(root)
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.ping_tree = ttk.Treeview(wrap, columns=cols, show="headings", height=7)
        for c in cols:
            text, width = heads[c]
            self.ping_tree.heading(c, text=text)
            self.ping_tree.column(c, width=width, anchor="w" if c in ("host", "ip", "tcp") else "center")
        self.ping_tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.ping_tree.yview)
        sb.pack(side="right", fill="y")
        self.ping_tree.configure(yscrollcommand=sb.set)
        self.ping_tree.tag_configure("bad", foreground="#b91c1c")
        self.ping_tree.tag_configure("warn", foreground="#b45309")
        self.ping_tree.tag_configure("ok", foreground="#047857")

    def _reset_ping_targets(self) -> None:
        self.ping_input.delete("1.0", "end")
        self.ping_input.insert("1.0", "\n".join(netfix.default_targets()))

    def _ping_targets(self) -> list[str]:
        raw = self.ping_input.get("1.0", "end")
        seen: list[str] = []
        for line in raw.splitlines():
            item = line.strip().strip(",，")
            if item and item not in seen:
                seen.append(item)
        return seen[:20]

    def on_ping_test(self) -> None:
        targets = [netfix.parse_target(t) for t in self._ping_targets()]
        targets = [t for t in targets if t.host]
        if not targets:
            messagebox.showinfo("提示", "先填一个域名、IP 或接口地址。")
            return
        count = max(1, min(20, int(self.ping_count.get() or 4)))
        want_tcp = self.ping_tcp.get()

        def work():
            rows = []
            for target in targets:
                result = netfix.ping_host(target.host, count=count)
                port_text = ""
                http_text = ""
                if target.has_url:
                    # 用户粘进来的是接口地址：直接发一次请求，这才是真正的「通不通」
                    ok, code, cost, err = netfix.http_probe(target)
                    if ok:
                        http_text = f"HTTP {code} · {cost}ms"
                    else:
                        http_text = f"HTTP 失败：{err[:40]}"
                if want_tcp:
                    ports = [target.default_port] if target.has_url else [80, 443]
                    parts = []
                    for port in ports:
                        cost = netfix.tcp_probe(result.ip or target.host, port, timeout=1.2)
                        parts.append(f"{port}:{str(cost) + 'ms' if cost is not None else '失败'}")
                    port_text = " / ".join(parts)
                display = http_text or port_text
                if not display:
                    display = result.error if not result.recv else "-"
                elif not result.recv and result.sent == 0:
                    display = (port_text + "  " + result.error).strip()
                self.log(f"  {target.raw} → {result.ip or '-'} "
                         f"丢包 {'-' if not result.sent else str(result.loss) + '%'} "
                         f"平均 {result.avg_ms if result.avg_ms is not None else '-'}ms  {display}")
                rows.append((target, result, port_text, http_text))
            return rows

        def done(rows):
            self.ping_tree.delete(*self.ping_tree.get_children())
            ok_n = 0
            for target, result, port_text, http_text in rows:
                avg = result.avg_ms
                reachable = bool(result.recv) or bool(http_text and http_text.startswith("HTTP"))
                if reachable and result.recv and result.loss == 0:
                    tag = "ok"
                elif reachable:
                    tag = "warn"
                else:
                    tag = "bad"
                ok_n += 1 if reachable else 0
                if reachable and not getattr(self, "_monitor_target", ""):
                    self._monitor_target = target.host
                if http_text:
                    detail = http_text + (f"  ({port_text})" if port_text else "")
                else:
                    detail = port_text or (result.error if not reachable else "-")
                self.ping_tree.insert("", "end", values=(
                    target.raw,
                    result.ip or "-",
                    f"{result.loss}%" if result.sent else "-",
                    f"{result.min_ms}ms" if result.min_ms is not None else "-",
                    f"{avg}ms" if avg is not None else "-",
                    f"{result.max_ms}ms" if result.max_ms is not None else "-",
                    detail,
                ), tags=(tag,))
            self.ping_summary.set(f"{ok_n}/{len(rows)} 个目标有响应")
            self.status_var.set(f"延迟测试完成：{ok_n}/{len(rows)} 个目标连通")

        self.run_task(work, done)

    def open_monitor(self) -> None:
        targets = [netfix.parse_target(t).host for t in self._ping_targets()]
        targets = [t for t in targets if t]
        preferred = getattr(self, "_monitor_target", "")
        target = preferred or (targets[0] if targets else "223.5.5.5")
        if not hasattr(self, "_monitor") or self._monitor is None or not self._monitor.winfo_exists():
            self._monitor = monitor.MiniMonitor(self, target, on_open_main=self._raise_main)
        else:
            self._monitor.target = target
            self._monitor.title_label.configure(text=target)
        self._monitor.lift()
        self.status_var.set(f"悬浮监控已开启：{target}（拖动可移动，右键关闭）")

    def _raise_main(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    # ----------------------------------------------------- LiteMonitor 标签页
    def _build_lite_tab(self) -> None:
        root = self.tab_lite
        self.lite_host = litemon.LiteMonitorHost()

        top = ttk.Frame(root)
        top.pack(fill="x")
        ok, version = litemon.has_dotnet8()
        self.lite_state = tk.StringVar(
            value=("就绪：已检测到 .NET 桌面运行时 " + version) if ok
            else "缺少 .NET 8 桌面运行时 —— 官方 LiteMonitor 是 .NET 8 程序，没它就起不来"
        )
        ttk.Label(top, textvariable=self.lite_state,
                  foreground="#047857" if ok else "#b91c1c",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")

        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(8, 0))
        self.btn_lite_embed = tk.Button(
            bar, text="📌 内嵌到本页签（实验性）", command=self.on_lite_embed,
            font=("Microsoft YaHei UI", 9), bg="#4b5563", fg="white",
            activebackground="#374151", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=12, pady=6,
            state="normal" if ok else "disabled",
        )
        self.btn_lite_embed.pack(side="left")
        self.btn_lite_launch = tk.Button(
            bar, text="🚀 启动悬浮窗（推荐）", command=self.on_lite_standalone,
            font=("Microsoft YaHei UI", 9, "bold"), bg="#0f766e", fg="white",
            activebackground="#115e59", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=14, pady=6,
        )
        self.btn_lite_launch.pack(side="left", padx=8)
        ttk.Button(bar, text="停止", command=self.on_lite_stop).pack(side="left")
        ttk.Button(bar, text="下载 .NET 8 桌面运行时", command=self.on_download_dotnet).pack(side="left", padx=8)
        ttk.Button(bar, text="去官方下载 LiteMonitor", command=self.on_download_litemon).pack(side="left")
        ttk.Button(bar, text="打开 LiteMonitor 目录", command=self.open_lite_dir).pack(side="left", padx=8)

        holder = ttk.LabelFrame(root, text=" LiteMonitor（内嵌窗口区） ", padding=4)
        holder.pack(fill="both", expand=True, pady=(8, 0))
        self.lite_frame = tk.Frame(holder, bg="#0b1220", highlightthickness=0)
        self.lite_frame.pack(fill="both", expand=True)
        bundled = litemon.find_bundled_exe() is not None
        if bundled:
            hint = (
                "LiteMonitor 已经打包进这个 exe 了，点「🚀 启动悬浮窗」即可，不需要另外下载。\r\n"
                "它是桌面/任务栏悬浮小组件（CPU、GPU、内存、磁盘、网速、FPS），本身就该浮在桌面上。\r\n\r\n"
                "「内嵌到本页签」是实验功能：窗口能被 SetParent 挂进来，但 LiteMonitor 是无边框\r\n"
                "悬浮窗，内嵌后部分区域会渲染成黑块，不推荐日常使用。\r\n\r\n"
                "想要「就在页签里看的监控」，用「延迟测试 → 📊 悬浮监控」那个自研面板更稳。"
            )
        else:
            hint = (
                "公开版不随包分发 LiteMonitor 的二进制（上游未声明许可证）。\r\n"
                "点「去官方下载 LiteMonitor」拿官方 release，解压后放到：\r\n"
                f"{litemon.INSTALL_DIR}\r\n"
                "（保证 LiteMonitor.exe 和 resources 目录同级）然后点「🚀 启动悬浮窗」。\r\n\r\n"
                "不想装第三方？「延迟测试 → 📊 悬浮监控」是自研的，零依赖、能内嵌、也能看 CPU/内存/网速。"
            )
        self.lite_hint = tk.Label(
            self.lite_frame,
            text=hint,
            bg="#0b1220", fg="#9ca3af", font=("Microsoft YaHei UI", 9), justify="left",
        )
        self.lite_hint.pack(expand=True)

        ttk.Label(
            root,
            text=("说明：LiteMonitor 为 Diorser/LiteMonitor v1.3.6 官方发行版（本工具只做打包与启动，未改动其代码）；"
                  "内嵌通过 Win32 SetParent 实现。首次启动若提示缺少 CPU 依赖驱动，按它自己的提示装 PawnIO 即可。"),
            style="Muted.TLabel", font=("Microsoft YaHei UI", 8), wraplength=880, justify="left",
        ).pack(anchor="w", pady=(6, 0))

        self.lite_frame.bind("<Configure>", self._on_lite_configure)

    def _on_lite_configure(self, event) -> None:
        if self.lite_host.embedded and self.lite_host.hwnd:
            litemon.resize_embedded(self.lite_host.hwnd, event.width, event.height)

    def on_lite_embed(self) -> None:
        ok, version = litemon.has_dotnet8()
        if not ok:
            messagebox.showwarning(
                "缺少 .NET 8 桌面运行时",
                "LiteMonitor 是 .NET 8 程序，系统里没有 Microsoft.WindowsDesktop.App 8.x 时它无法启动。\n\n"
                "点「下载 .NET 8 桌面运行时」安装（约 55 MB），装完重启本工具即可内嵌。",
            )
            return

        self.lite_frame.update_idletasks()
        width = max(200, self.lite_frame.winfo_width())
        height = max(150, self.lite_frame.winfo_height())
        parent_id = self.lite_frame.winfo_id()

        def work():
            self.log("正在启动 LiteMonitor 并尝试内嵌…")
            ok2, msg = self.lite_host.embed(parent_id, width, height)
            return ok2, msg

        def done(payload):
            ok2, msg = payload
            self.log(f"  {msg}")
            if ok2:
                self.lite_hint.pack_forget()
                self.lite_state.set("已内嵌（点「停止」可结束进程）")
                self.status_var.set("LiteMonitor 已内嵌")
            else:
                self.lite_state.set(msg)
                messagebox.showwarning(
                    "没能内嵌",
                    msg + "\n\n可以直接用「独立窗口打开」，或者用自研的悬浮监控。",
                )

        self.run_task(work, done)

    def on_lite_standalone(self) -> None:
        def work():
            return self.lite_host.start()

        def done(payload):
            ok, msg = payload
            self.log(f"LiteMonitor: {msg}")
            if not ok:
                messagebox.showerror(
                    "启动失败",
                    msg + "\n\n如果提示缺 .NET，点「下载 .NET 8 桌面运行时」装上再试。",
                )
            else:
                self.lite_state.set("已作为独立窗口运行")

        self.run_task(work, done)

    def on_lite_stop(self) -> None:
        self.lite_host.stop()
        self.lite_hint.pack(expand=True)
        ok, version = litemon.has_dotnet8()
        self.lite_state.set(("就绪：已检测到 .NET 桌面运行时 " + version) if ok
                            else "缺少 .NET 8 桌面运行时 —— 官方 LiteMonitor 是 .NET 8 程序，没它就起不来")
        self.status_var.set("LiteMonitor 已停止")

    def on_download_dotnet(self) -> None:
        if messagebox.askyesno(
            "下载 .NET 8 桌面运行时",
            "将打开官方下载链接（aka.ms，约 55 MB 的安装包）：\n\n" + litemon.DOTNET_URL +
            "\n\n要我直接在浏览器里打开吗？",
        ):
            webbrowser.open(litemon.DOTNET_URL)

    def on_download_litemon(self) -> None:
        if messagebox.askyesno(
            "去官方下载 LiteMonitor",
            "LiteMonitor 由 Diorser 开发，本项目不随包分发它的二进制。\n\n"
            "下载地址（官方 Release）：\n" + litemon.OFFICIAL_RELEASES +
            "\n\n下载解压后，把 LiteMonitor.exe 和 resources 目录放到：\n" +
            str(litemon.INSTALL_DIR) +
            "\n\n要现在打开下载页吗？",
        ):
            webbrowser.open(litemon.OFFICIAL_RELEASES)
            litemon.INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    def open_lite_dir(self) -> None:
        path = litemon.INSTALL_DIR
        if not path.is_dir():
            exe, msg = litemon.ensure_installed()
            if exe is None:
                path.mkdir(parents=True, exist_ok=True)
                if messagebox.askyesno("目录已创建", str(path) + "\n\n要打开这个目录吗？"):
                    os.startfile(str(path))
                return
        os.startfile(str(path))

    # ------------------------------------------------------------ 状态栏 / 日志
    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=14, pady=(0, 4))
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, style="Muted.TLabel").pack(side="left", padx=10)

    def _build_log(self) -> None:
        box = ttk.LabelFrame(self, text=" 运行日志 ", padding=6)
        box.pack(fill="both", padx=14, pady=(0, 12))
        self.log_text = tk.Text(box, height=8, wrap="word", relief="flat", bg="#0f172a",
                                fg="#e5e7eb", insertbackground="#e5e7eb", bd=0,
                                font=("Consolas", 9))
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sb.set, state="disabled")

    # ------------------------------------------------------------ 日志/线程
    def log(self, text: str) -> None:
        self.q.put(("log", text))

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        buttons = [
            (self.btn_hero, "normal"),
            (self.btn_custom, "normal"),
        ]
        if hasattr(self, "btn_net_quick"):
            buttons += [(self.btn_net_quick, "normal"), (self.btn_net_custom, "normal")]
        for button, state in buttons:
            button.configure(state="disabled" if busy else state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
        if status:
            self.status_var.set(status)

    def run_task(self, fn, on_done=None) -> None:
        if self.busy:
            return
        self._set_busy(True, "处理中…")

        def worker() -> None:
            try:
                result = fn()
                self.q.put(("done", result, None, on_done))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("done", None, exc, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "log":
                    self._append_log(rest[0])
                elif kind == "done":
                    result, err, on_done = rest
                    self._set_busy(False, "就绪")
                    if err is not None:
                        self._append_log(f"发生错误：{err}")
                        messagebox.showerror("出错了", str(err))
                    elif on_done:
                        try:
                            on_done(result)
                        except Exception:  # noqa: BLE001
                            import traceback

                            detail = traceback.format_exc()
                            self._append_log("界面更新出错：\n" + detail)
                            messagebox.showerror("界面更新出错", detail[-800:])
        except queue.Empty:
            pass
        self.after(80, self._poll)

    # ------------------------------------------------------------ 浏览器列表
    def refresh_browsers(self) -> None:
        def work():
            self.log("正在检测已安装的浏览器…")
            found = detect_browsers()
            for b in found:
                self.log(f"  发现 {b.name} {b.version}：{b.data_root}")
            if not found:
                self.log("  没有检测到受支持的浏览器")
            return found

        self.run_task(work, self._apply_browsers)

    def _apply_browsers(self, found: list[Browser]) -> None:
        self.browsers = found
        self.selected = {b.key for b in found}
        self.tree.delete(*self.tree.get_children())
        for b in found:
            self.tree.insert("", "end", iid=b.key, values=(
                "☑", b.name, b.version or "-", self._state_text(b), str(b.data_root or ""),
            ))
        names = [f"{b.name}" for b in found]
        self.mode_combo.configure(values=names or ["（未检测到浏览器）"])
        self.mode_combo.current(0 if names else -1)
        self._sync_mode_panel()
        self._update_summary()
        if found:
            self.status_var.set(f"已检测到 {len(found)} 个浏览器")
        else:
            self.status_var.set("没有检测到受支持的浏览器")
        self.refresh_net_status()

    def _state_text(self, b: Browser) -> str:
        return "运行中" if b.running() else "已关闭"

    def _on_tree_click(self, event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid in self.selected:
            self.selected.discard(iid)
            self.tree.set(iid, "sel", "☐")
        else:
            self.selected.add(iid)
            self.tree.set(iid, "sel", "☑")
        self._update_summary()

    def _update_summary(self) -> None:
        chosen = [b for b in self.browsers if b.key in self.selected]
        cats = [k for k, v in self.cat_vars.items() if v.get()]
        if not chosen:
            self.summary_var.set("请在上表中勾选要清理的浏览器")
            return
        self.summary_var.set(
            f"已选 {len(chosen)} 个浏览器 · 清理项：{'、'.join(self._cat_name(c) for c in cats) or '未选择'}"
        )

    @staticmethod
    def _cat_name(key: str) -> str:
        for k, title, _ in CATEGORIES:
            if k == key:
                return title
        return key

    # ------------------------------------------------------------ 清理动作
    def on_quick_clean(self) -> None:
        for k, var in self.cat_vars.items():
            var.set(k == "cache")
        self.selected = {b.key for b in self.browsers}
        for b in self.browsers:
            self.tree.set(b.key, "sel", "☑")
        self._update_summary()
        self._start_clean(categories=["cache"], quick=True)

    def on_custom_clean(self) -> None:
        cats = [k for k, v in self.cat_vars.items() if v.get()]
        if not cats:
            messagebox.showinfo("提示", "请至少勾选一个清理项。")
            return
        risky = [c for c in cats if c != "cache"]
        if risky:
            msg = ("你勾选了会丢失数据的项目：\n\n" + "\n".join(f"· {self._cat_name(c)}" for c in risky) +
                   "\n\n清理后登录状态和历史将无法恢复（备份只能手工还原）。确定继续吗？")
            if not messagebox.askyesno("请再次确认", msg):
                return
        self._start_clean(categories=cats, quick=False)

    def _start_clean(self, categories: list[str], quick: bool) -> None:
        chosen = [b for b in self.browsers if b.key in self.selected]
        if not chosen:
            messagebox.showinfo("提示", "请在上表中勾选要清理的浏览器。")
            return
        options = cleaner.CleanOptions(
            categories=categories,
            close_first=self.opt_close.get(),
            make_backup=self.opt_backup.get(),
        )
        reopen = self.opt_reopen.get()
        running_before = [b for b in chosen if b.running()]
        if running_before and options.close_first:
            names = "、".join(b.name for b in running_before)
            if not messagebox.askyesno("需要关闭浏览器", f"{names} 正在运行，清理缓存前需要先关闭。\n\n现在关闭并继续吗？"):
                return

        def work():
            self.log("=" * 52)
            self.log(f"{'一键极速清理' if quick else '自定义清理'} 开始：{len(chosen)} 个浏览器，"
                     f"{len(categories)} 个清理项")
            results = cleaner.clean(chosen, options, log=self.log)
            return results, chosen, reopen

        self.run_task(work, self._after_clean)

    def _after_clean(self, payload) -> None:
        results, chosen, reopen = payload
        total = cleaner.total_freed(results)
        lines = [f"合计释放 {cleaner.human(total)}"]
        detail = []
        for r in results:
            detail.append(f"  · {r.name}: {cleaner.human(r.freed)}"
                          + (f"（{r.note}）" if r.note else "")
                          + (f"，{r.failed} 项被占用" if r.failed else ""))
        self.log("清理完成，合计释放 " + cleaner.human(total))
        for line in detail:
            self.log(line)
        self.summary_var.set(f"上次清理释放 {cleaner.human(total)} · " + f"{len(results)} 个浏览器")
        self.status_var.set(f"清理完成，释放 {cleaner.human(total)}")
        if reopen:
            closed = [b for b in chosen if b.exe and not b.running()]
            for b in closed:
                kernel.launch(b, None, self.log)
        self._refresh_states()
        messagebox.showinfo("清理完成", "\n".join(lines + detail))

    def _refresh_states(self) -> None:
        for b in self.browsers:
            if self.tree.exists(b.key):
                self.tree.set(b.key, "state", self._state_text(b))

    # ------------------------------------------------------------ 模式切换
    def _sync_mode_panel(self) -> None:
        names = [b.name for b in self.browsers]
        idx = self.mode_combo.current()
        self.mode_target = self.browsers[idx] if 0 <= idx < len(self.browsers) else None
        b = self.mode_target
        if b is None:
            self.mode_label.configure(text="")
            self._set_info("没有检测到受支持的浏览器。")
            return
        self.mode_label.configure(text=f"当前：{kernel.mode_summary(b)}")
        self._set_info(self._mode_info_text(b))

    def _mode_info_text(self, b: Browser) -> str:
        if b.key == "sogou":
            return (
                "搜狗高速浏览器是真正的双内核浏览器，本工具直接修改它的内核策略：\n\n"
                "  · 配置文件：%APPDATA%\\SogouExplorer\\config.xml\n"
                "  · 字段：multicorestrategy（0=智能，1=极速，2=兼容）\n\n"
                "切换前会自动关闭搜狗浏览器，并先把 config.xml 备份一份，切换后立即回读校验。\n"
                "重新打开浏览器后，地址栏右侧的内核图标应该会跟着变化。\n\n"
                "提示：部分网站会记住「本站用兼容模式」，这是站点级设置，不受全局开关影响。"
            )
        return (
            f"{b.name} 使用 Chromium 内核，本身没有 IE 兼容内核，因此不存在真正意义上的"
            "「兼容模式」开关，任何声称能给它换 IE 内核的工具都不靠谱。\n\n"
            "本工具提供的是「以兼容方式启动」：\n"
            "  · 极速模式 = 默认参数启动，GPU 加速全开；\n"
            "  · 兼容模式 = 关闭 GPU / 2D 画布加速启动，老网页、老网银控件、远程桌面环境里\n"
            "    出现的花屏、白屏、黑块通常会消失（代价是滚动和视频会稍慢）。\n\n"
            "点下面的「以当前模式启动浏览器」即可体验。"
        )

    def _set_info(self, text: str) -> None:
        self.mode_info.configure(state="normal")
        self.mode_info.delete("1.0", "end")
        self.mode_info.insert("1.0", text)
        self.mode_info.configure(state="disabled")

    def on_set_mode(self, mode: int) -> None:
        b = self.mode_target
        if b is None:
            messagebox.showinfo("提示", "请先选择一个浏览器。")
            return
        if b.key != "sogou":
            self._launch_chromium_mode(b, mode)
            return

        def work():
            self.log(f"切换 {b.name} → {kernel.MODE_LABELS[mode]}")
            ok, msg = kernel.set_browser_mode(b, mode, self.log)
            return ok, msg

        def done(payload):
            ok, msg = payload
            if ok:
                self.log(f"  切换成功：{msg}")
                self._sync_mode_panel()
                messagebox.showinfo("切换成功", f"已切换为：{msg}\n\n重新打开浏览器即可生效。")
            else:
                self.log(f"  切换失败：{msg}")
                messagebox.showerror("切换失败", msg)

        self.run_task(work, done)

    def on_launch(self) -> None:
        b = self.mode_target
        if b is None:
            return
        if b.key == "sogou":
            ok, msg = kernel.launch(b, None, self.log)
        else:
            ok, msg = kernel.launch(b, kernel.MODE_FAST, self.log)
        self.log("  " + msg)
        if not ok:
            messagebox.showerror("启动失败", msg)

    def _launch_chromium_mode(self, b: Browser, mode: int) -> None:
        """Chromium 系：用启动参数模拟极速/兼容，并明确告诉用户原理。"""
        if mode == kernel.MODE_COMPAT:
            tip = (f"{b.name} 没有 IE 兼容内核，无法真切换。\n\n"
                   f"这里改为「兼容方式启动」：关闭 GPU 加速、2D 画布加速、GPU 光栅化。\n"
                   f"老网页花屏/白屏/黑块时更稳，代价是滚动和视频略慢。\n\n现在启动吗？")
            args = kernel.COMPAT_ARGS
        else:
            tip = (f"{b.name} 没有可切换的内核，默认启动就是极速模式（GPU 加速全开）。\n\n"
                   f"要现在启动它吗？")
            args = []
        if not messagebox.askyesno("以" + ("兼容" if mode == kernel.MODE_COMPAT else "极速") + "方式启动", tip):
            return
        if not b.exe:
            messagebox.showerror("启动失败", "找不到浏览器主程序。")
            return
        ok, msg = kernel.launch_with_args(b, args, self.log)
        self.log("  " + msg)
        if ok:
            self.status_var.set(f"{b.name} 已按{'兼容' if mode == kernel.MODE_COMPAT else '极速'}参数启动")
        else:
            messagebox.showerror("启动失败", msg)

    def on_open_settings(self) -> None:
        b = self.mode_target
        if b is None:
            return
        urls = {
            "chrome": "chrome://settings/system",
            "edge": "edge://settings/system",
            "sogou": "https://ie.sogou.com/",
            "115": "chrome://settings/system",
        }
        url = urls.get(b.key)
        if not url:
            return
        if b.exe and url.startswith(("chrome://", "edge://")):
            subprocess.Popen([str(b.exe), url], close_fds=True)
        else:
            webbrowser.open(url)

    # ------------------------------------------------------------ 其它
    def open_backup_dir(self) -> None:
        path = cleaner.BACKUP_ROOT
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def open_data_dir(self) -> None:
        b = self.mode_target
        if b is None or not b.data_root:
            return
        try:
            os.startfile(str(b.data_root))
        except OSError as exc:
            messagebox.showerror("打不开", str(exc))


def run() -> None:
    app = App()
    app.mainloop()
