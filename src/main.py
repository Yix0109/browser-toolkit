"""程序入口。"""
from __future__ import annotations

import ctypes
import sys


def _setup_windows() -> None:
    """高 DPI 适配 + 任务栏独立图标。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BrowserCleaner.SogouChrome.1")
    except Exception:
        pass


def main() -> int:
    _setup_windows()
    from ui import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
