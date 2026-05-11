"""
build/launcher.py
PyInstaller entry point for the DITA Converter single-file exe.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Port detection
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    """
    Ask the OS to assign any available ephemeral port, then release it.

    Binding to port 0 lets the OS choose from its ephemeral range (49152-65535
    on Windows), which is never inside the ranges that Hyper-V / WSL2 reserve
    for themselves.  Those reserved ranges cause WinError 10013 (access denied)
    for every port Streamlit tries in the 8501-8600 window.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Patches
# ---------------------------------------------------------------------------

def _patch_asyncio_run() -> None:
    """
    Force SelectorEventLoop on Windows — tornado's IOLoop needs add_reader/
    add_writer which ProactorEventLoop (the Windows default) does not support.

    Python 3.8-3.11: asyncio.run() respects the global event loop policy.
    Python 3.12+:    asyncio.run() accepts loop_factory (overrides the policy).
    """
    if sys.platform != "win32":
        return

    if sys.version_info >= (3, 12):
        _orig = asyncio.run

        def _run_selector(coro, **kwargs):  # type: ignore[override]
            kwargs.setdefault("loop_factory", asyncio.SelectorEventLoop)
            return _orig(coro, **kwargs)

        asyncio.run = _run_selector  # type: ignore[assignment]
    else:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _patch_signal() -> None:
    import signal
    if not hasattr(signal, "SIGKILL"):
        signal.SIGKILL = 9  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent.parent


def main() -> None:
    _patch_asyncio_run()
    _patch_signal()

    port = _find_free_port()
    app_path = _base() / "ui" / "app.py"

    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        f"--server.port={port}",
        "--global.developmentMode=false",
        "--server.headless=false",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
    ]

    from streamlit.web import cli as stcli
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
