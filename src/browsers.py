"""浏览器识别：找到 exe、安装目录、用户数据目录以及各类可清理目标。"""
from __future__ import annotations

import os
import re
import winreg
from dataclasses import dataclass, field
from pathlib import Path

from winproc import file_version, matching_processes

# ---------------------------------------------------------------- 清理类别

CATEGORIES: list[tuple[str, str, str]] = [
    ("cache", "缓存文件", "网页缓存、图片、脚本、GPU/着色器缓存。安全，推荐每次都清。"),
    ("cookies", "Cookie / 登录状态", "清除后所有网站都需要重新登录。"),
    ("history", "历史记录 / 下载记录", "网址历史、地址栏联想、最近关闭的标签页。"),
    ("form", "表单 / 自动填充", "输入框历史、自动填充过的地址和账号框。"),
]

# ------------------------------------------------------------ Chromium 路径

CHROMIUM_CACHE = [
    "Cache",
    "Code Cache",
    "GPUCache",
    "ShaderCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "Media Cache",
    "Service Worker\\CacheStorage",
    "Service Worker\\ScriptCache",
    "Application Cache",
    "CacheStorage",
]

CHROMIUM_COOKIES = [
    "Cookies",
    "Cookies-journal",
    "Network\\Cookies",
    "Network\\Cookies-journal",
]

CHROMIUM_HISTORY = [
    "History",
    "History-journal",
    "HistoryEx",  # 115 浏览器把历史库改名成 HistoryEx
    "HistoryEx-journal",
    "Visited Links",
    "Top Sites",
    "Top Sites-journal",
    "Favicons",
    "Favicons-journal",
    "Current Session",
    "Current Tabs",
    "Last Session",
    "Last Tabs",
    "Sessions",
    "Network Action Predictor",
    "Network Action Predictor-journal",
]

CHROMIUM_FORM = [
    "Web Data",
    "Web Data-journal",
]


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(p))


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


def _scan_process_dirs() -> list[tuple[str, str]]:
    """从注册表卸载项里反复掏「安装路径」，返回 (显示名, 安装根目录)。"""
    out: list[tuple[str, str]] = []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in keys:
        try:
            root = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        with root:
            try:
                count = winreg.QueryInfoKey(root)[0]
            except OSError:
                continue
            for i in range(count):
                try:
                    name = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, name) as item:
                        def val(field_name: str) -> str:
                            try:
                                return str(winreg.QueryValueEx(item, field_name)[0])
                            except OSError:
                                return ""

                        display = val("DisplayName")
                        if not display:
                            continue
                        loc = val("InstallLocation").strip('" ')
                        unins = val("UninstallString").strip('" ')
                        icon = val("DisplayIcon").split(",")[0].strip('" ')
                        for candidate in (loc, unins, icon):
                            if candidate:
                                p = Path(candidate)
                                out.append((display, str(p.parent if p.suffix else p)))
                except OSError:
                    continue
    return out


def _app_path(exe_name: str) -> Path | None:
    tail = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for prefix in ("", "WOW6432Node\\"):
            try:
                with winreg.OpenKey(hive, prefix + tail) as k:
                    value = str(winreg.QueryValueEx(k, "")[0]).strip('" ')
                    if value and Path(value).is_file():
                        return Path(value)
            except OSError:
                continue
    return None


def _locate_exe(exe_names: list[str], hint_pattern: str) -> Path | None:
    """按 App Paths、常见路径、注册表卸载项的顺序定位可执行文件。"""
    for exe_name in exe_names:
        p = _app_path(exe_name)
        if p:
            return p
    # 常见安装位置
    roots = [
        _expand(r"%ProgramFiles%"),
        _expand(r"%ProgramFiles(x86)%"),
        _expand(r"%LOCALAPPDATA%"),
    ]
    for exe_name in exe_names:
        for root in roots:
            for guess in (
                root / exe_name,
                root / "Google" / "Chrome" / "Application" / exe_name,
                root / "Microsoft" / "Edge" / "Application" / exe_name,
            ):
                if guess.is_file():
                    return guess
    # 注册表卸载项里反查
    for display, folder in _scan_process_dirs():
        if not re.search(hint_pattern, display, re.I):
            continue
        base = Path(folder)
        for exe_name in exe_names:
            for cand in (base / exe_name, base / "Application" / exe_name, base.parent / exe_name):
                if cand.is_file():
                    return cand
            try:
                for found in base.rglob(exe_name):
                    if found.is_file():
                        return found
            except OSError:
                pass
    return None


# ---------------------------------------------------------------- 浏览器模型


@dataclass
class Browser:
    key: str
    name: str
    engine: str  # "chromium" | "sogou"
    exe: Path | None = None
    data_root: Path | None = None
    process_names: tuple[str, ...] = ()
    helper_names: tuple[str, ...] = ()
    service_names: tuple[str, ...] = ()
    notes: str = ""
    version: str = ""
    _cache_extra: list[Path] = field(default_factory=list)

    # -- 基本信息 -------------------------------------------------
    @property
    def installed(self) -> bool:
        return self.exe is not None and self.data_root is not None

    @property
    def install_dir(self) -> Path | None:
        return self.exe.parent if self.exe else None

    @property
    def profiles(self) -> list[Path]:
        """用户配置文件目录列表（Chrome 的 Default / Profile 1 ...）。"""
        root = self.data_root
        if not root or not root.is_dir():
            return []
        if self.engine == "sogou":
            candidate = root / "Webkit" / "Default"
            return [candidate] if candidate.is_dir() else []
        result: list[Path] = []
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                if child.name == "Default" or child.name.startswith("Profile "):
                    result.append(child)
        except OSError:
            pass
        return result

    # -- 清理目标 -------------------------------------------------
    def targets(self, category: str) -> list[Path]:
        out: list[Path] = []
        if self.engine == "sogou":
            out.extend(self._sogou_targets(category))
        else:
            out.extend(self._chromium_targets(category))
        seen: set[str] = set()
        unique: list[Path] = []
        for p in out:
            key = os.path.normcase(str(p))
            if key not in seen and p.exists():
                seen.add(key)
                unique.append(p)
        return unique

    def _chromium_targets(self, category: str) -> list[Path]:
        table = {
            "cache": CHROMIUM_CACHE,
            "cookies": CHROMIUM_COOKIES,
            "history": CHROMIUM_HISTORY,
            "form": CHROMIUM_FORM,
        }
        rels = table.get(category, [])
        out: list[Path] = []
        for profile in self.profiles:
            for rel in rels:
                out.append(profile / rel)
        if category == "cache":
            out.extend(self._cache_extra)
        return out

    def _sogou_targets(self, category: str) -> list[Path]:
        root = self.data_root
        if not root:
            return []
        out: list[Path] = []
        ie_root = root / "Webkit" / "SEIE"  # 兼容模式（IE 内核）的历史缓存/WinINet 目录
        if category == "cache":
            for profile in self.profiles:
                for rel in CHROMIUM_CACHE:
                    out.append(profile / rel)
            out.extend(
                [
                    root / "Webkit" / "ShaderCache",
                    root / "Webkit" / "GrShaderCache",
                    root / "Temp",
                    root / "Thumbnails",
                    root / "FavIcon",
                    ie_root / "Cache",
                ]
            )
            # IE 内核的 WinINet 缓存放在 SEIE\5204\0 这类编号目录里
            try:
                if ie_root.is_dir():
                    for child in ie_root.iterdir():
                        if child.is_dir() and re.fullmatch(r"\d{3,6}", child.name):
                            out.append(child)
            except OSError:
                pass
        elif category == "cookies":
            for profile in self.profiles:
                out.extend([profile / "Cookies", profile / "Cookies-journal"])
            out.append(ie_root / "Cookies")
        elif category == "history":
            out.extend(
                [
                    root / "HistoryUrl3.db",
                    root / "uhistory3.db",
                    root / "MCPattern3.db",
                    ie_root / "History",
                ]
            )
            for profile in self.profiles:
                out.extend([profile / "Top Sites", profile / "Visited Links"])
        elif category == "form":
            out.extend([root / "FormData3.dat"])
        return out

    def process_roots(self) -> tuple[str, ...]:
        return (str(self.install_dir),) if self.install_dir else ()

    def closeable_processes(self) -> list:
        """需要关闭的进程（含渲染/助手进程，排除安全中心等常驻服务）。"""
        return matching_processes(
            self.process_names + self.helper_names,
            self.process_roots(),
            self.service_names,
        )

    def running(self) -> bool:
        """浏览器主界面是否开着（用于界面显示状态）。"""
        return bool(matching_processes(self.process_names, (), self.service_names))


# ---------------------------------------------------------------- 探测入口


def detect_browsers() -> list[Browser]:
    browsers = [
        _detect_chrome(),
        _detect_edge(),
        _detect_sogou(),
        _detect_115(),
    ]
    return [b for b in browsers if b is not None]


def _detect_chrome() -> Browser | None:
    exe = _locate_exe(
        ["chrome.exe"],
        r"Google Chrome",
    )
    data = _expand(r"%LOCALAPPDATA%\Google\Chrome\User Data")
    b = Browser(
        key="chrome",
        name="Google Chrome",
        engine="chromium",
        exe=exe,
        data_root=data if data.is_dir() else None,
        process_names=("chrome.exe",),
        service_names=("chrome_crashpad_handler.exe",),
    )
    if not b.installed:
        return None
    b.version = file_version(exe) if exe else ""
    return b


def _detect_edge() -> Browser | None:
    exe = _locate_exe(["msedge.exe"], r"Microsoft Edge")
    data = _expand(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
    b = Browser(
        key="edge",
        name="Microsoft Edge",
        engine="chromium",
        exe=exe,
        data_root=data if data.is_dir() else None,
        process_names=("msedge.exe",),
        service_names=("msedgewebview2.exe",),
    )
    if not b.installed:
        return None
    b.version = file_version(exe) if exe else ""
    return b


def _detect_sogou() -> Browser | None:
    exe = None
    for display, folder in _scan_process_dirs():
        if not re.search(r"搜狗|Sogou", display, re.I):
            continue
        base = Path(folder)
        for cand in (
            base / "SogouExplorer.exe",
            base.parent / "SogouExplorer.exe",
            base / "SCMainFrame.exe",
        ):
            if cand.is_file():
                exe = cand
                break
        if exe is None:
            try:
                hits = [p for p in base.rglob("SogouExplorer.exe")][:1]
                if hits:
                    exe = hits[0]
            except OSError:
                pass
        if exe:
            break
    if exe is None:
        for guess in (
            _expand(r"%ProgramFiles(x86)%\SogouExplorer\SogouExplorer.exe"),
            _expand(r"%ProgramFiles%\SogouExplorer\SogouExplorer.exe"),
        ):
            if guess.is_file():
                exe = guess
                break

    data = _expand(r"%APPDATA%\SogouExplorer")
    b = Browser(
        key="sogou",
        name="搜狗高速浏览器",
        engine="sogou",
        exe=exe,
        data_root=data if data.is_dir() else None,
        process_names=("SogouExplorer.exe",),
        helper_names=("SCMainFrame.exe", "SogouInprocBrowser.exe", "SogouWebkit.exe"),
        service_names=("SESecurityCenter.exe", "SGRepairTool.exe", "SogouExplorerUp.exe"),
        notes="支持真正的 极速 / 兼容 内核切换（config.xml 的 multicorestrategy）。",
    )
    if not b.installed:
        return None
    b.version = file_version(exe) if exe else ""
    return b


def _detect_115() -> Browser | None:
    exe = _locate_exe(["115chrome.exe", "115Chrome.exe"], r"115")
    data = _expand(r"%LOCALAPPDATA%\115Chrome\User Data")
    b = Browser(
        key="115",
        name="115 浏览器",
        engine="chromium",
        exe=exe,
        data_root=data if data.is_dir() else None,
        process_names=("115chrome.exe",),
        notes="Chromium 内核，极速/兼容通过启动参数模拟。",
    )
    if not b.installed:
        return None
    b.version = file_version(exe) if exe else ""
    return b
