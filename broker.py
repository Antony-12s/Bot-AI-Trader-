"""The MT5 terminal as the dashboard sees it: connection, account, login, symbol list.

The password goes straight to the terminal (mt5.login) and is never stored, logged or sent
back here; the terminal keeps the account the same way as a login typed in its own window.
"""
import threading

import MetaTrader5 as mt5

from wizard import gold_like

LOCK = threading.Lock()  # one terminal connection per process, the HTTP server is threaded


def _error(prefix):
    code, text = mt5.last_error()
    return f"{prefix}: {text} ({code})"


def _attach():
    """Attach to the running terminal, starting it when it is closed. False when that fails."""
    return mt5.terminal_info() is not None or mt5.initialize()


def status():
    with LOCK:
        if not _attach():
            return {"connected": False, "error": _error("cannot reach the MT5 terminal (is it installed?)")}
        terminal, account = mt5.terminal_info(), mt5.account_info()
    info = {"connected": True, "online": bool(terminal and terminal.connected), "logged_in": account is not None}
    if account is not None:
        info.update(
            login=account.login, server=account.server, name=account.name, company=account.company,
            currency=account.currency, balance=account.balance, equity=account.equity,
            leverage=account.leverage, demo=account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
        )
    return info


def login(account, password, server):
    """Log the terminal into a broker account. Raises ValueError with the terminal's reason."""
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


def symbols():
    """Every symbol name the broker offers, the plainest gold names first: the dashboard's SYMBOL picker."""
    with LOCK:
        if not _attach():
            return []
        names = [symbol.name for symbol in (mt5.symbols_get() or ())]
    gold = gold_like(names)  # GOLD before GOLD24-7 before BarrickGold
    return gold + sorted(set(names) - set(gold))
