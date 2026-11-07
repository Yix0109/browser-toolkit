"""把 LiteMonitor（Diorser/LiteMonitor v1.3.6）内嵌到本工具里。

LiteMonitor 是 .NET 8 WPF 程序，因此：
1. 系统必须装有 .NET 8 Desktop Runtime，否则它自己都启动不了；
2. 窗口用 Win32 SetParent 挂到本工具的 Frame 上，实现「在页签里直接用」。

任何一步失败都会回退成「独立窗口启动」，不会让界面卡死。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_POPUP = 0x80000000

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.MoveWindow.argtypes = [
    wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL
]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.GetWindowLongW.restype = ctypes.c_long

DOTNET_URL = "https://aka.ms/dotnet/8.0/windowsdesktop-runtime-win-x64.exe"
OFFICIAL_REPO = "https://github.com/Diorser/LiteMonitor"
OFFICIAL_RELEASES = "https://github.com/Diorser/LiteMonitor/releases/latest"
INSTALL_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%\BrowserCleaner\lite"))


# ----------------------------------------------------------------- 资源定位

def _bundle_dir() -> Path:
    """打包后 PyInstaller 把数据解到 _MEIPASS；源码运行时用 vendor 目录。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for candidate in (base / "litemonitor", base / "vendor" / "litemonitor" / "app"):
        if candidate.is_dir():
            return candidate
    return base / "litemonitor"


def find_bundled_exe() -> Path | None:
    root = _bundle_dir()
    if not root.is_dir():
        return None
    for pattern in ("LiteMonitor.exe", "*/LiteMonitor.exe", "**/LiteMonitor.exe"):
        for hit in root.glob(pattern):
            if hit.is_file():
                return hit
    return None


def local_exe() -> Path:
    return INSTALL_DIR / "LiteMonitor.exe"


def ensure_installed() -> tuple[Path | None, str]:
    """把随包携带的 LiteMonitor 复制到 %LOCALAPPDATA%，返回可用 exe 路径。

    公开版没有随包携带 LiteMonitor（上游未声明许可证），此时返回 None，
    由界面引导用户从官方 release 下载后放进 INSTALL_DIR。
    """
    target = local_exe()
    if target.is_file():
        return target, "已就绪"

    source = find_bundled_exe()
    if source is None:
        return None, (
            "没有找到 LiteMonitor。公开版不随包分发它的二进制"
            f"（上游 {OFFICIAL_REPO} 未声明许可证）。\n\n"
            f"请到官方 release 下载：{OFFICIAL_RELEASES}\n"
            f"解压后放到：{INSTALL_DIR}"
        )
    src_dir = source.parent
    try:
        if INSTALL_DIR.exists():
            shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        INSTALL_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, INSTALL_DIR)
    except Exception as exc:  # noqa: BLE001
        return None, f"解压失败：{exc}"
    return (local_exe() if local_exe().is_file() else None), "已解压到本地"


# --------------------------------------------------------------- .NET 运行时

def dotnet_desktop_version() -> str:
    """返回已安装的 Microsoft.WindowsDesktop.App 版本号（>=8 才算能用）。"""
    bases = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet" / "shared",
        Path(os.environ.get("DOTNET_ROOT", "")) / "shared" if os.environ.get("DOTNET_ROOT") else None,
    ]
    for base in bases:
        if not base:
            continue
        desktop = base / "Microsoft.WindowsDesktop.App"
        if not desktop.is_dir():
            continue
        best: tuple[int, str] = (0, "")
        for child in desktop.iterdir():
            try:
                major = int(child.name.split(".")[0])
            except (ValueError, IndexError):
                continue
            if major >= 8 and major > best[0]:
                best = (major, child.name)
        if best[0]:
            return best[1]
    return ""


def has_dotnet8() -> tuple[bool, str]:
    version = dotnet_desktop_version()
    return bool(version), version


# ----------------------------------------------------------------- 窗口操作

def windows_of_pid(pid: int) -> list[int]:
    found: list[int] = []

    def callback(hwnd, _lparam):
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def wait_for_window(pid: int, timeout: float = 15.0) -> int:
    """等主窗口出现；有弹窗时挑面积最大的那个（避免抓到提示框）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        handles = windows_of_pid(pid)
        if handles:
            best, best_area = 0, 0
            for hwnd in handles:
                w, h = _window_size(hwnd)
                area = w * h
                if area > best_area:
                    best, best_area = hwnd, area
            if best and best_area > 5000:
                return best
        time.sleep(0.2)
    return 0


def _window_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.right - rect.left, rect.bottom - rect.top
    return 0, 0


def embed_window(hwnd: int, parent_hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    style = (style | WS_CHILD | WS_VISIBLE) & ~(WS_CAPTION | WS_THICKFRAME | WS_POPUP)
    user32.SetWindowLongW(hwnd, GWL_STYLE, style)
    ok = bool(user32.SetParent(hwnd, parent_hwnd))
    user32.MoveWindow(hwnd, x, y, width, height, True)
    return ok


def resize_embedded(hwnd: int, width: int, height: int) -> None:
    if hwnd:
        user32.MoveWindow(hwnd, 0, 0, max(50, width), max(50, height), True)


def detach_window(hwnd: int) -> None:
    if hwnd:
        user32.SetParent(hwnd, 0)


# ----------------------------------------------------------------- 启动封装

class LiteMonitorHost:
    """负责启动 / 内嵌 / 停止 LiteMonitor。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.hwnd: int = 0
        self.embedded = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> tuple[bool, str]:
        exe, note = ensure_installed()
        if exe is None:
            return False, note
        if self.running:
            return True, "已经在运行"
        try:
            self.proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
        except OSError as exc:
            return False, f"启动失败：{exc}"
        self.hwnd = wait_for_window(self.proc.pid, 18.0)
        if not self.hwnd:
            code = self.proc.poll()
            self.proc = None
            return False, (
                "LiteMonitor 起不来（退出码 %s）。它需要 .NET 8 桌面运行时，"
                "装好后再试。" % code
            )
        return True, note

    def embed(self, parent_hwnd: int, width: int, height: int) -> tuple[bool, str]:
        if not self.running:
            ok, msg = self.start()
            if not ok:
                return False, msg
        if not self.hwnd:
            return False, "没有拿到 LiteMonitor 的窗口句柄"
        ok = embed_window(self.hwnd, parent_hwnd, 0, 0, width, height)
        self.embedded = ok
        if not ok:
            return False, "嵌入失败，可以用「独立窗口打开」"
        return True, "已内嵌"

    def detach(self) -> None:
        if self.hwnd and self.embedded:
            detach_window(self.hwnd)
        self.embedded = False

    def stop(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass
        self.proc = None
        self.hwnd = 0
        self.embedded = False
