"""Windows 进程 / 版本信息小工具。

只用 ctypes 实现，不依赖任何第三方库，方便打包成单个 exe。
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ver = ctypes.WinDLL("version", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32FirstW.restype = wintypes.BOOL
_k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.TerminateProcess.restype = wintypes.BOOL


@dataclass
class Process:
    pid: int
    name: str
    exe: str


def list_processes() -> list[Process]:
    """枚举当前所有进程（拿不到完整路径的，exe 为空字符串）。"""
    result: list[Process] = []
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (INVALID_HANDLE_VALUE, 0, None):
        return result
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            name = entry.szExeFile
            path = _process_image(pid)
            result.append(Process(pid=pid, name=name, exe=path))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return result


def _process_image(pid: int) -> str:
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(handle)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _norm(path: str | os.PathLike) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path))).rstrip("\\")
    except Exception:
        return os.path.normcase(str(path)).rstrip("\\")


def matching_processes(
    process_names: tuple[str, ...] = (),
    roots: tuple[str, ...] = (),
    exclude_names: tuple[str, ...] = (),
) -> list[Process]:
    """按进程名和/或「可执行文件所在目录」匹配进程。"""
    names = {n.lower() for n in process_names}
    excluded = {n.lower() for n in exclude_names}
    norm_roots = [_norm(r) for r in roots if r]
    found: list[Process] = []
    for p in list_processes():
        if p.pid in (0, 4):
            continue
        if p.name and p.name.lower() in excluded:
            continue
        if p.name and p.name.lower() in names:
            found.append(p)
            continue
        if p.exe and norm_roots:
            exe = _norm(p.exe)
            for root in norm_roots:
                if exe.startswith(root + "\\"):
                    found.append(p)
                    break
    return found


def kill_processes(procs: list[Process]) -> list[str]:
    """强杀进程，返回失败信息。"""
    errors: list[str] = []
    for p in procs:
        handle = _k32.OpenProcess(PROCESS_TERMINATE, False, p.pid)
        if not handle:
            errors.append(f"{p.name}(pid {p.pid}) 拒绝访问")
            continue
        try:
            if not _k32.TerminateProcess(handle, 0):
                errors.append(f"{p.name}(pid {p.pid}) 终止失败")
        finally:
            _k32.CloseHandle(handle)
    return errors


def wait_all_gone(
    process_names: tuple[str, ...] = (),
    roots: tuple[str, ...] = (),
    timeout: float = 8.0,
    exclude_names: tuple[str, ...] = (),
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not matching_processes(process_names, roots, exclude_names):
            return True
        time.sleep(0.15)
    return not matching_processes(process_names, roots, exclude_names)


def file_version(path: str | os.PathLike) -> str:
    """读取 exe/dll 的文件版本号，例如 11.0.1.34700。"""
    try:
        p = str(Path(path))
        size = _ver.GetFileVersionInfoSizeW(p, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not _ver.GetFileVersionInfoW(p, 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not _ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return ""

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return ""
