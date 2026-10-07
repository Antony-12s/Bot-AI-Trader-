"""Entry point of the installed TradeBot.exe.

Double-clicked (no arguments) it opens the app. `TradeBot.exe bot.py --flag` runs the bundled
bot module as __main__ with the same arguments, so start.bat, train.bat and the rest work
unchanged once setup.bat points PY at the exe.

The exe is a windowed program (no black console behind the app). Started from a console
(the .bat tools) it borrows that console, so their prints and questions still show.
"""
import runpy
import sys
from pathlib import Path

SCRIPTS = ("bot", "wizard", "replay", "export_history", "report", "ui")


def script_name(argv):
    if len(argv) < 2:
        return "ui"  # double-clicked: open the app
    name = Path(argv[1]).stem
    if name not in SCRIPTS:
        raise SystemExit(f"usage: TradeBot.exe <{'|'.join(name + '.py' for name in SCRIPTS)}> [args]")
    return name


def borrow_console():
    """A windowed exe has no stdout. Attach to the parent's console (a .bat) when there is one."""
    if sys.stdout is not None or sys.platform != "win32":
        return
    import ctypes
    if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        sys.stdout = sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")


if __name__ == "__main__":
    name = script_name(sys.argv)
    sys.argv = sys.argv[1:] or ["ui.py"]
    borrow_console()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:  # None: double-clicked, nothing to print to
            # flush every line so bot.log follows a dashboard-started bot live (the frozen exe ignores PYTHONUNBUFFERED)
            stream.reconfigure(line_buffering=True)
    runpy.run_module(name, run_name="__main__")
