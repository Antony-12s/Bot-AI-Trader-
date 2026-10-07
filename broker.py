"""The MT5 terminal as the dashboard sees it: connection, account, login, symbol list.

The password goes straight to the terminal (mt5.login) and is never stored, logged or sent
back here; the terminal keeps the account the same way as a login typed in its own window.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import MetaTrader5 as mt5

from config import MT5_DIR, terminal_args
from wizard import gold_like

LOCK = threading.Lock()  # one terminal connection per process, the HTTP server is threaded
# MetaQuotes' own installer, fetched when the user asks: TradeBot does not redistribute MT5 itself.
MT5_SETUP_URL = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
install = {"state": "idle", "error": ""}  # idle | downloading | launched | failed


def installed_terminals():
    """terminal64.exe of every MT5 the Windows uninstall list knows (any broker's build)."""
    try:
        import winreg
    except ImportError:  # not Windows
        return []
    found = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            uninstall = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
        except OSError:
            continue
        for index in range(winreg.QueryInfoKey(uninstall)[0]):
            try:
                entry = winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index))
                location = winreg.QueryValueEx(entry, "InstallLocation")[0]
            except OSError:
                continue
            terminal = Path(location) / "terminal64.exe"
            if location and terminal.exists() and str(terminal) not in found:
                found.append(str(terminal))
    return found


def start_install(opener=urllib.request.urlopen, launch=None):
    """Download the official MT5 installer in the background, then open it for the user to click through."""
    launch = launch or os.startfile  # Windows only, looked up here so the module imports anywhere
    if install["state"] == "downloading":
        return

    def work():
        try:
            target = Path(tempfile.gettempdir(), "mt5setup.exe")
            with opener(MT5_SETUP_URL, timeout=120) as response:
                target.write_bytes(response.read())
            launch(str(target))
            install.update(state="launched", error="")
        except Exception as error:  # shown on the Setup page, never fatal for the dashboard
            install.update(state="failed", error=str(error))

    install.update(state="downloading", error="")
    threading.Thread(target=work, daemon=True).start()


own = {"state": "idle", "error": ""}  # TradeBot's own terminal: idle | copying | done | failed
show_wanted = {"value": False}  # the user pressed Show MT5; otherwise the app keeps it hidden


def own_terminal():
    """TradeBot's own portable terminal64.exe, or None before it is set up."""
    terminal = MT5_DIR / "terminal64.exe"
    return terminal if terminal.exists() else None


def setup_own_terminal(source=None):
    """Copy an installed MT5 into TradeBot's folder as a portable terminal, in the background.

    Only terminal64.exe and Config (the broker server list) come along, about 120 MB: no accounts,
    no passwords (an installed MT5 keeps those in AppData, not in its program folder). Portable mode
    keeps this terminal's logins, logs and charts inside TradeBot\\mt5, apart from the owner's MT5.
    """
    if own["state"] == "copying":
        return
    found = [source] if source else installed_terminals()
    if not found:
        raise ValueError("no MetaTrader 5 on this PC yet: install it first (Setup, step 1)")
    origin = Path(found[0]).parent

    def work():
        try:
            MT5_DIR.mkdir(parents=True, exist_ok=True)
            if (origin / "Config").is_dir():
                shutil.copytree(origin / "Config", MT5_DIR / "Config", dirs_exist_ok=True)
            part = MT5_DIR / "terminal64.exe.part"  # renamed last: terminal_args() never sees half a file
            shutil.copy2(origin / "terminal64.exe", part)
            os.replace(part, MT5_DIR / "terminal64.exe")
            with LOCK:
                mt5.shutdown()  # the next attach starts TradeBot's own terminal instead of the PC's default one
            own.update(state="done", error="")
        except Exception as error:
            own.update(state="failed", error=str(error))

    own.update(state="copying", error="")
    threading.Thread(target=work, daemon=True).start()


def _terminal_windows(terminal=None):
    """Top-level windows of one terminal, matched by its exe path: TradeBot's own unless another is named."""
    terminal = terminal or own_terminal()
    if terminal is None or os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    target, found, names = str(terminal).lower(), [], {}

    def exe_of(pid):
        if pid not in names:
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            names[pid] = path.value.lower() if handle and kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)) else ""
            if handle:
                kernel32.CloseHandle(handle)
        return names[pid]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if user32.GetWindowTextLengthW(hwnd) and exe_of(pid.value) == target:
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def show_terminal(show):
    """Show or hide TradeBot's own MT5 windows; hidden, it keeps running and trading."""
    import ctypes
    show_wanted["value"] = bool(show)
    windows = _terminal_windows()
    for hwnd in windows:
        ctypes.windll.user32.ShowWindow(hwnd, 5 if show else 0)  # SW_SHOW / SW_HIDE
    if show and windows:
        ctypes.windll.user32.SetForegroundWindow(windows[0])
    return len(windows)


def terminal_in_use():
    """The terminal64.exe TradeBot works with: its own, else the one it is attached to, else the first installed."""
    if own_terminal() is not None:
        return own_terminal()
    with LOCK:
        info = mt5.terminal_info()
    if info is not None and Path(info.path, "terminal64.exe").exists():
        return Path(info.path, "terminal64.exe")
    found = installed_terminals()
    return Path(found[0]) if found else None


def open_terminal():
    """The app's "Open MetaTrader 5" button: bring the terminal's window to the front, starting it if closed."""
    import ctypes
    terminal = terminal_in_use()
    if terminal is None:
        raise ValueError("MetaTrader 5 is not installed yet: see Setup, step 1")
    # the main window only: MT5 also owns hidden helper windows with titles ("GDI+ Window") that must stay hidden
    windows = [hwnd for hwnd in _terminal_windows(terminal) if _class_of(hwnd).startswith(MAIN_CLASS)]
    if not windows:
        subprocess.Popen([str(terminal)] + (["/portable"] if terminal == own_terminal() else []), cwd=str(terminal.parent))
        return "started"
    if terminal == own_terminal():
        show_wanted["value"] = True  # the user wants to see it: stop hiding TradeBot's own MT5
    for hwnd in windows:
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE: shown and un-minimised
    ctypes.windll.user32.SetForegroundWindow(windows[0])
    return "shown"


def _class_of(hwnd):
    import ctypes
    kind = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, kind, 64)
    return kind.value


MAIN_CLASS = "MetaQuotes::MetaTrader"  # the terminal's main window class, e.g. MetaQuotes::MetaTrader::5.00
DIALOG_CLASS = "#32770"  # every Windows dialog box ("Open an Account", "Welcome to LiveUpdate", ...)


def keep_hidden():
    """Called every few seconds: MT5 likes to pop its window back up (startup, reconnects).

    While hidden, it also closes MT5's dialog boxes: nobody can see them, and a terminal waiting
    on one ("Open an Account", "Welcome to LiveUpdate" on first start) answers no one, so every
    call from TradeBot timed out. The owner logs in from TradeBot's MetaTrader 5 page instead.
    With Show MT5 on, dialogs are left for the owner.
    """
    if own_terminal() is None or show_wanted["value"]:
        return
    import ctypes
    user32 = ctypes.windll.user32
    for hwnd in _terminal_windows():
        kind = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, kind, 64)
        if kind.value == DIALOG_CLASS:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE, as if Cancel was pressed
        elif user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 0)


def _error(prefix):
    code, text = mt5.last_error()
    return f"{prefix}: {text} ({code})"


ATTACH_TIMEOUT_MS = 15000  # the library waits 60 s by default, and every page poll queued behind it
RETRY_SECONDS = 20  # after a failed attach, answer "not reachable" at once instead of waiting again
last_failure = {"at": 0.0}


def _on_right_terminal(info):
    """Once TradeBot has its own MT5, only that one will do: never the terminal the owner trades in."""
    terminal = own_terminal()
    return terminal is None or Path(info.path).resolve() == terminal.parent.resolve()


def _attach():
    """Attach to TradeBot's terminal, starting it when it is closed. False when that fails."""
    info = mt5.terminal_info()
    if info is not None and _on_right_terminal(info):
        return True
    if info is not None:  # still on the PC's default MT5 from before the own copy existed
        mt5.shutdown()
    if time.time() - last_failure["at"] < RETRY_SECONDS:
        return False
    if mt5.initialize(timeout=ATTACH_TIMEOUT_MS, **terminal_args()):
        if _on_right_terminal(mt5.terminal_info()):
            return True
        mt5.shutdown()  # the library attached to some other running terminal: refuse it
    last_failure["at"] = time.time()
    return False


def status():
    extra = {"installed": bool(installed_terminals()), "install": dict(install),
             "own": own_terminal() is not None, "own_setup": dict(own), "shown": show_wanted["value"]}
    with LOCK:
        if not _attach():
            return dict(extra, connected=False, logged_in=False, error=_error("cannot reach the MT5 terminal"))
        terminal, account = mt5.terminal_info(), mt5.account_info()
    info = dict(extra, connected=True, online=bool(terminal and terminal.connected), logged_in=account is not None,
                terminal=terminal.path if terminal else "")
    if account is not None:
        info.update(
            login=account.login, server=account.server, name=account.name, company=account.company,
            currency=account.currency, balance=account.balance, equity=account.equity,
            leverage=account.leverage, demo=account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
        )
    return info


def login(account, password, server):
    """Log the terminal into a broker account. Raises ValueError with the terminal's reason.

    Returns "demo" or "live": what the broker says the account is, so the caller files it under
    the right mode whatever the user thought it was.
    """
    account, server = str(account).strip(), str(server).strip()
    if not account.isdigit():
        raise ValueError("the account number is digits only")
    if not password or not server:
        raise ValueError("password and server are both needed")
    with LOCK:
        if not _attach():
            raise ValueError(_error("cannot reach the MT5 terminal (is it installed?)"))
        if not mt5.login(int(account), password=password, server=server, timeout=60000):
            raise ValueError(_error("login failed, check the account, password and server"))
        info = mt5.account_info()
    return "demo" if info is not None and info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "live"


def switch(account, server, mode):
    """Log back into a saved account with the password the terminal remembered; it must be a `mode` account."""
    if not str(account).isdigit():
        raise ValueError(f"no {mode} account yet: log in to one on the MetaTrader 5 page first")
    with LOCK:
        if not _attach():
            raise ValueError(_error("cannot reach the MT5 terminal"))
        current = mt5.account_info()
        if current is None or current.login != int(account):
            # no password: MT5 uses the one it saved at the first login
            if not mt5.login(int(account), server=server, timeout=60000):
                raise ValueError(_error(f"could not switch to the {mode} account; log in to it again on the MetaTrader 5 page"))
            current = mt5.account_info()
        is_demo = current is not None and current.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    if mode == "demo" and not is_demo:
        raise ValueError(f"account {account} is not a demo account")


def candles(symbol, timeframe, count):
    """The last `count` candles (the newest still forming) for the chart; times are broker server time."""
    frame = getattr(mt5, "TIMEFRAME_" + timeframe, None)
    if frame is None:
        raise ValueError(f"unknown timeframe {timeframe}")
    with LOCK:
        if not _attach():
            raise ValueError(_error("cannot reach the MT5 terminal"))
        mt5.symbol_select(symbol, True)  # the chart may ask for a symbol not in Market Watch yet
        rates = mt5.copy_rates_from_pos(symbol, frame, 0, count)
    if rates is None or len(rates) == 0:
        raise ValueError(f"no candles for {symbol} {timeframe}: is the symbol name right, and MT5 logged in?")
    return [{"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"])} for r in rates]


def symbols():
    """Every symbol name the broker offers, the plainest gold names first: the dashboard's SYMBOL picker."""
    with LOCK:
        if not _attach():
            return []
        names = [symbol.name for symbol in (mt5.symbols_get() or ())]
    gold = gold_like(names)  # GOLD before GOLD24-7 before BarrickGold
    return gold + sorted(set(names) - set(gold))
