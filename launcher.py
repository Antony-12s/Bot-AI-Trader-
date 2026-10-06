"""Entry point of the installed TradeBot.exe: it stands in for python.exe in the .bat files.

`TradeBot.exe bot.py --flag` runs the bundled bot module as __main__ with the same arguments,
so start.bat, train.bat and the rest work unchanged once setup.bat points PY at the exe.
"""
import runpy
import sys
from pathlib import Path

SCRIPTS = ("bot", "wizard", "replay", "export_history", "report", "ui")


def script_name(argv):
    name = Path(argv[1]).stem if len(argv) > 1 else ""
    if name not in SCRIPTS:
        raise SystemExit(f"usage: TradeBot.exe <{'|'.join(name + '.py' for name in SCRIPTS)}> [args]")
    return name


if __name__ == "__main__":
    name = script_name(sys.argv)
    sys.argv = sys.argv[1:]
    runpy.run_module(name, run_name="__main__")
