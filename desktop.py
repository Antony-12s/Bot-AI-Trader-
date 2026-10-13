"""Desktop extras for the app window: the tray icon, and whether TradeBot starts with Windows.

Starting with Windows is set by the installer (a Startup-folder shortcut the user ticks while
installing), never written by the app itself: an unsigned program that adds itself to Windows
startup on its own looks like malware persistence, and Microsoft Defender quarantines it
(Behavior:Win32/Persistence.A!ml, seen on 2026-10-07).
"""
import os
from pathlib import Path

ICON = Path(__file__).with_name("tradebot.ico")


def startup_shortcut():
    """The shortcut the installer's "Start TradeBot when Windows starts" task creates."""
    return Path(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\TradeBot.lnk")


def run_at_login():
    return startup_shortcut().exists()


def tray(on_open, on_start, on_stop, on_quit, on_stop_and_quit):
    """Tray icon with the app's menu; double-click opens the window. Returns the running icon."""
    import pystray
    from PIL import Image

    def item(text, action, default=False):
        return pystray.MenuItem(text, lambda icon, _: action(), default=default)

    icon = pystray.Icon("TradeBot", Image.open(ICON), "TradeBot", pystray.Menu(
        item("Open TradeBot", on_open, default=True),
        pystray.Menu.SEPARATOR,
        item("Start the bot", on_start),
        item("Stop the bot", on_stop),
        pystray.Menu.SEPARATOR,
        item("Quit (the bot keeps trading)", on_quit),
        item("Stop the bot and quit", on_stop_and_quit),
    ))
    icon.run_detached()
    return icon
