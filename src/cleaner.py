"""清理引擎：统计大小、备份用户数据、健壮删除、汇总报告。"""
from __future__ import annotations

import os
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from browsers import Browser
from winproc import kill_processes, wait_all_gone

Log = Callable[[str], None]

BACKUP_ROOT = Path(os.path.expandvars(r"%LOCALAPPDATA%\BrowserCleaner\backup"))


def _long(path: Path) -> str:
    """给 Windows API 用的长路径形式，绕开 260 字符限制。"""
    p = os.path.abspath(str(path))
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(_long(path)):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def human(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    n = float(n)
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.1f} {units[i]}" if i else f"{int(n)} {units[i]}"


def _force_writable(func, path, _exc) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _clear_readonly(path: Path) -> None:
    if not path.exists() or path.is_file():
        return
    for root, dirs, files in os.walk(_long(path)):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
        for name in dirs:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass


def remove_path(path: Path) -> tuple[bool, int, str]:
    """删除文件或目录，返回 (是否成功, 释放字节, 错误信息)。"""
    if not path.exists():
        return True, 0, ""
    before = path_size(path)
    target = Path(_long(path))
    try:
        if path.is_dir():
            _clear_readonly(path)
            shutil.rmtree(target, onexc=_force_writable)
        else:
            try:
                os.chmod(target, stat.S_IWRITE)
            except OSError:
                pass
            target.unlink()
    except Exception as exc:  # 文件被占用等
        # 整体改名的兜底方案：改名成功即可认为「已从原地移除」，稍后再试删除
        for _ in range(3):
            time.sleep(0.2)
            try:
                if path.is_dir():
                    shutil.rmtree(target, onexc=_force_writable)
                else:
                    target.unlink()
                break
            except Exception:
                continue
        else:
            try:
                if path.exists():
                    moved = path.with_name(f"{path.name}.pending-del-{os.getpid()}")
                    path.rename(moved)
                    shutil.rmtree(_long(moved), ignore_errors=True) if moved.is_dir() else moved.unlink(missing_ok=True)
            except Exception:
                after = path_size(path)
                return False, max(0, before - after), f"{path} → {exc}"
    after = path_size(path)
    return True, max(0, before - after), ""


def _stamp() -> str:
    """带毫秒的时间戳，避免同一秒内多次备份互相覆盖。"""
    now = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"


def backup_targets(browser: Browser, paths: Iterable[Path], log: Log) -> Path | None:
    dest = BACKUP_ROOT / _stamp() / browser.key
    saved = 0
    for src in paths:
        if not src.exists() or src.is_dir():
            continue
        rel = src.relative_to(browser.data_root) if browser.data_root and _is_under(src, browser.data_root) else Path(src.name)
        target = dest / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_long(src), _long(target))
            saved += 1
        except Exception as exc:
            log(f"  ! 备份失败 {src.name}: {exc}")
    if saved:
        log(f"  已备份 {saved} 个文件到 {dest}")
        return dest
    return None


def _is_under(path: Path, root: Path) -> bool:
    try:
        Path(os.path.normcase(str(path))).relative_to(Path(os.path.normcase(str(root))))
        return True
    except ValueError:
        return False


@dataclass
class BrowserResult:
    key: str
    name: str
    freed: int = 0
    removed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    note: str = ""


@dataclass
class CleanOptions:
    categories: list[str]
    close_first: bool = True
    make_backup: bool = True


def close_browser(browser: Browser, log: Log) -> bool:
    procs = browser.closeable_processes()
    if not procs:
        return True
    log(f"  关闭 {browser.name}（{len(procs)} 个进程）…")
    killed = {p.pid for p in procs}
    errors = kill_processes(procs)
    for e in errors:
        log(f"  ! {e}")
    if not wait_all_gone(
        browser.process_names + browser.helper_names,
        browser.process_roots(),
        timeout=8.0,
        exclude_names=browser.service_names,
    ):
        # 再补一刀
        rest = [p for p in browser.closeable_processes() if p.pid not in killed]
        if rest:
            kill_processes(rest)
        if not wait_all_gone(
            browser.process_names + browser.helper_names,
            browser.process_roots(),
            timeout=4.0,
            exclude_names=browser.service_names,
        ):
            log(f"  ! {browser.name} 仍有进程未退出，部分文件可能无法删除")
            return False
    return True


def clean(
    browsers: list[Browser],
    options: CleanOptions,
    log: Log = print,
    should_stop: Callable[[], bool] | None = None,
) -> list[BrowserResult]:
    results: list[BrowserResult] = []
    for browser in browsers:
        if should_stop and should_stop():
            break
        result = BrowserResult(key=browser.key, name=browser.name)
        log(f"▶ {browser.name} {browser.version}")
        targets = [p for cat in options.categories for p in browser.targets(cat)]
        if not targets:
            result.note = "没有找到可清理的内容"
            log("  没有找到可清理的内容")
            results.append(result)
            continue

        running = browser.running()
        if running:
            if options.close_first:
                if not close_browser(browser, log):
                    result.note = "浏览器未能完全关闭"
            else:
                result.skipped = True
                result.note = "浏览器正在运行，已跳过"
                log("  浏览器正在运行，已跳过（可在设置里允许自动关闭）")
                results.append(result)
                continue

        if options.make_backup and any(c in ("cookies", "history", "form") for c in options.categories):
            data_targets = [
                p
                for c in ("cookies", "history", "form")
                if c in options.categories
                for p in browser.targets(c)
            ]
            backup_targets(browser, data_targets, log)

        for path in targets:
            if should_stop and should_stop():
                break
            ok, freed, err = remove_path(path)
            result.freed += freed
            if ok:
                result.removed += 1
            else:
                result.failed += 1
                if len(result.errors) < 5:
                    result.errors.append(err)
                    log(f"  ! 跳过被占用的项：{path.name}")
        log(f"  释放 {human(result.freed)}，处理 {result.removed} 项" + (f"，{result.failed} 项失败" if result.failed else ""))
        results.append(result)
    return results


def total_freed(results: list[BrowserResult]) -> int:
    return sum(r.freed for r in results)
