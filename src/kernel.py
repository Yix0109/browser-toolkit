"""极速模式 / 兼容模式 切换。

- 搜狗高速浏览器：真正改内核策略，写在 %APPDATA%\\SogouExplorer\\config.xml 的
  multicorestrategy 属性里（0=智能，1=极速/Chromium，2=兼容/IE）。
- Chrome / Edge / 115：Chromium 内核本身没有 IE 兼容模式，只能通过启动参数模拟
  「极速」（默认全速渲染）与「兼容」（关闭 GPU 加速、2D 画布加速，老网页/老插件
  更不容易花屏、白屏）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

import cleaner
from browsers import Browser
from winproc import kill_processes, wait_all_gone

Log = Callable[[str], None]

SOGOU_CONFIG = Path(os.path.expandvars(r"%APPDATA%\SogouExplorer\config.xml"))
BACKUP_ROOT = cleaner.BACKUP_ROOT

MODE_SMART = 0
MODE_FAST = 1
MODE_COMPAT = 2

MODE_LABELS = {
    MODE_SMART: "智能模式（自动选择内核）",
    MODE_FAST: "极速模式（Chromium 内核）",
    MODE_COMPAT: "兼容模式（IE 内核）",
}

COMPAT_ARGS = [
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-accelerated-2d-canvas",
    "--disable-gpu-rasterization",
    "--disable-features=CanvasOopRasterization",
]


# --------------------------------------------------------------- 搜狗 config


def read_sogou_mode() -> int | None:
    if not SOGOU_CONFIG.is_file():
        return None
    try:
        text = SOGOU_CONFIG.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r'multicorestrategy="(\d+)"', text)
    return int(m.group(1)) if m else None


def _write_sogou_mode(mode: int) -> tuple[bool, str]:
    try:
        text = SOGOU_CONFIG.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return False, f"读取 config.xml 失败：{exc}"

    if 'multicorestrategy="' in text:
        new_text = re.sub(r'multicorestrategy="\d*"', f'multicorestrategy="{mode}"', text, count=1)
    elif "<Item " in text:
        new_text = text.replace("<Item ", f'<Item multicorestrategy="{mode}" ', 1)
    else:
        return False, "config.xml 结构不认识，未做修改"

    stamp = cleaner._stamp()
    backup_dir = BACKUP_ROOT / stamp / "sogou-config"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(SOGOU_CONFIG, backup_dir / "config.xml")
    except OSError:
        pass

    tmp = SOGOU_CONFIG.with_suffix(".xml.tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, SOGOU_CONFIG)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return False, f"写入 config.xml 失败：{exc}"
    return True, f"已写入（备份在 {backup_dir}）"


def set_browser_mode(browser: Browser, mode: int, log: Log = print) -> tuple[bool, str]:
    """把浏览器设成指定模式。搜狗改配置，其它浏览器只记录不改。"""
    if browser.key != "sogou":
        return False, "该浏览器不支持直接切换内核（Chromium 没有 IE 兼容内核）"
    if not SOGOU_CONFIG.is_file():
        return False, f"找不到搜狗配置文件：{SOGOU_CONFIG}"

    procs = browser.closeable_processes()
    if procs:
        log(f"  先关闭搜狗高速浏览器（{len(procs)} 个进程）…")
        kill_processes(procs)
        if not wait_all_gone(
            browser.process_names + browser.helper_names,
            browser.process_roots(),
            10.0,
            browser.service_names,
        ):
            return False, "搜狗高速浏览器没有完全退出，稍后重试"
        time.sleep(0.6)  # 等它把内存里的配置刷回磁盘

    ok, msg = _write_sogou_mode(mode)
    if not ok:
        return False, msg
    current = read_sogou_mode()
    if current != mode:
        return False, f"写入后校验失败（当前读到 {current}）"
    log(f"  {msg}")
    return True, MODE_LABELS.get(mode, str(mode))


def launch(browser: Browser, mode: int | None = None, log: Log = print) -> tuple[bool, str]:
    """按指定模式启动浏览器。"""
    if not browser.exe:
        return False, "找不到浏览器主程序"
    if browser.key == "sogou":
        if mode in (MODE_SMART, MODE_FAST, MODE_COMPAT):
            ok, msg = set_browser_mode(browser, mode, log)
            if not ok:
                return False, msg
    args = COMPAT_ARGS if (browser.key != "sogou" and mode == MODE_COMPAT) else []
    return launch_with_args(browser, args, log)


def launch_with_args(browser: Browser, args: list[str], log: Log = print) -> tuple[bool, str]:
    """带额外启动参数运行浏览器。"""
    if not browser.exe:
        return False, "找不到浏览器主程序"
    cmd = [str(browser.exe), *args]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(browser.exe.parent),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        suffix = "（兼容参数）" if args else ""
        return True, f"{browser.name} 已启动{suffix}"
    except OSError as exc:
        return False, f"启动失败：{exc}"


def mode_summary(browser: Browser) -> str:
    if browser.key == "sogou":
        m = read_sogou_mode()
        if m is None:
            return "未知（config.xml 未找到或没有该字段）"
        return MODE_LABELS.get(m, f"未知取值 {m}")
    return "Chromium 内核（无 IE 兼容内核，可用启动参数模拟兼容模式）"
