"""Desktop extras for the app window: the tray icon and starting TradeBot with Windows."""
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # per user: no admin rights needed
RUN_NAME = "TradeBot"
ICON = Path(__file__).with_name("tradebot.ico")


def launch_command():
    """How Windows should start TradeBot at login: straight to the tray, no window."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" ui.py --background'
    return f'"{sys.executable}" "{Path(__file__).with_name("ui.py")}" --background'


def run_at_login(enabled=None):
    """Read, or set then read, whether TradeBot starts when this Windows user logs in."""
    try:
        import winreg
    except ImportError:  # not Windows
        return False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        if enabled is True:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launch_command())
        elif enabled is False:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
        try:
            return winreg.QueryValueEx(key, RUN_NAME)[0] == launch_command()
        except FileNotFoundError:
            return False


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
