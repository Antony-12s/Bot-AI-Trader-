"""Stand-in for the Windows-only MetaTrader5 package, for running the unit tests on Linux/macOS/CI.

    PYTHONPATH=tests_support python -m unittest

Only the constants the code uses are real; every function raises, and the tests fake what
they need with unittest.mock. On Windows install the real package instead (requirements.txt).
"""
TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
DEAL_TYPE_BUY, DEAL_TYPE_SELL, DEAL_TYPE_BALANCE = 0, 1, 2
TRADE_RETCODE_DONE = 10009
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
TRADE_ACTION_DEAL = 1
ORDER_TIME_GTC = 0

def _unavailable(*args, **kwargs):
    raise RuntimeError("MetaTrader5 is not available on this platform (stub)")

initialize = shutdown = symbol_select = account_info = symbol_info = symbol_info_tick = _unavailable
positions_get = history_deals_get = copy_rates_from_pos = order_send = last_error = _unavailable
DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY = 0, 1, 2, 3
DEAL_REASON_CLIENT, DEAL_REASON_MOBILE, DEAL_REASON_WEB, DEAL_REASON_EXPERT = 0, 1, 2, 3
DEAL_REASON_SL, DEAL_REASON_TP, DEAL_REASON_SO = 4, 5, 6
terminal_info = _unavailable
symbols_get = _unavailable
