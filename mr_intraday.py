"""Price-only MR engine. Timestamps are UTC bar-open epoch seconds.

Feed completed M1 bid bars in order. Orders are simulated at the next M1
open; no terminal or API access is performed by this module.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from statistics import median
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    spread: float  # price units, not points or pips


def aggregate(bars, minutes):
    """Discard incomplete or gapped buckets; never synthesize missing prices."""
    groups = {}
    for bar in bars:
        groups.setdefault(bar.time // (minutes * 60), []).append(bar)
    result = []
    for bucket, rows in sorted(groups.items()):
        start = bucket * minutes * 60
        if [r.time for r in rows] != list(range(start, start + minutes * 60, 60)):
            continue
        result.append(Bar(start, rows[0].open, max(r.high for r in rows),
                          min(r.low for r in rows), rows[-1].close, rows[-1].spread))
    return result


def wilder(values, period=14):
    if len(values) < period:
        return []
    out = [sum(values[:period]) / period]
    for value in values[period:]:
        out.append((out[-1] * (period - 1) + value) / period)
    return out


def atr(bars, period=14):
    return wilder([max(b.high - b.low, abs(b.high - a.close),
                       abs(b.low - a.close)) for a, b in zip(bars, bars[1:])], period)


def adx(bars, period=14):
    tr, plus, minus = [], [], []
    for a, b in zip(bars, bars[1:]):
        up, down = b.high - a.high, a.low - b.low
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)
        tr.append(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close)))
    dx = []
    for t, p, m in zip(wilder(tr, period), wilder(plus, period), wilder(minus, period)):
        p, m = (100 * p / t, 100 * m / t) if t else (0, 0)
        dx.append(100 * abs(p - m) / (p + m) if p + m else 0)
    return wilder(dx, period)


def ema20(bars):
    if len(bars) < 20:
        return None
    value = sum(b.close for b in bars[:20]) / 20
    for b in bars[20:]:
        value += 2 / 21 * (b.close - value)
    return value


def lot_size(equity, fraction, distance, contract, step, minimum, maximum,
             open_risk=0, total_fraction=0.01):
    if min(equity, fraction, distance, contract, step, minimum, maximum) <= 0:
        return 0.0
    budget = min(equity * fraction, equity * total_fraction - open_risk)
    if budget <= 0:
        return 0.0
    raw = min(budget / (distance * contract), maximum)
    units = (Decimal(str(raw)) / Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)
    lots = float(units * Decimal(str(step)))
    return lots if lots >= minimum else 0.0


@dataclass
class Position:
    side: int
    entry_time: int
    entry: float
    sl: float
    tp: float
    lots: float
    deadline: int


class Engine:
    def __init__(self, symbol, news, *, contract, equity=10000, risk=0.005,
                 step=0.01, minimum=0.01, maximum=100, k=2, adx_limit=20,
                 time_bars=8, commission=0, slippage=0):
        if symbol not in ('GBPUSD', 'XAUUSD'):
            raise ValueError('symbol must be GBPUSD or XAUUSD')
        if min(contract, equity, risk, step, minimum, maximum, k, adx_limit, time_bars) <= 0:
            raise ValueError('parameters must be positive')
        if risk > .01 or minimum > maximum or min(commission, slippage) < 0:
            raise ValueError('invalid risk, lot limits or costs')
        self.symbol, self.news, self.contract = symbol, news, contract
        self.equity, self.risk = equity, risk
        self.step, self.minimum, self.maximum = step, minimum, maximum
        self.k, self.adx_limit, self.time_bars = k, adx_limit, time_bars
        self.commission, self.slippage = commission, slippage
        self.bars, self.trades = [], []
        self.position = self.window = self.pending = None
        self.reset = {1: True, -1: True}

    def news_times(self):
        currencies = {'USD', 'GBP'} if self.symbol == 'GBPUSD' else {'USD'}
        return [int(n['time']) for n in self.news
                if n['currency'] in currencies and n['impact'].lower() == 'high']

    def spread_baseline(self, time):
        local = datetime.fromtimestamp(time, timezone.utc).astimezone(NY)
        days, date = [], local.date()
        while len(days) < 20:
            date -= timedelta(days=1)
            if date.weekday() < 5:
                days.append(date)
        slot = local.hour * 4 + local.minute // 15
        by_day = {day: [] for day in days}
        for b in self.bars:
            dt = datetime.fromtimestamp(b.time, timezone.utc).astimezone(NY)
            if dt.date() in by_day and dt.hour * 4 + dt.minute // 15 == slot:
                by_day[dt.date()].append(b.spread)
        if any(len(rows) != 15 for rows in by_day.values()):
            return None
        return median([s for rows in by_day.values() for s in rows])

    def forced_exit(self, time):
        local = datetime.fromtimestamp(time, timezone.utc).astimezone(NY)
        if local.hour * 60 + local.minute >= 16 * 60 + 50:
            return 'end_of_day'
        if any(n - 300 <= time <= n for n in self.news_times()):
            return 'news'
        if time >= self.position.deadline:
            return 'time_stop'
        return None

    def close(self, time, price, reason):
        p = self.position
        if reason != 'tp':
            price -= p.side * self.slippage
        pnl = p.side * (price - p.entry) * p.lots * self.contract - self.commission * p.lots
        self.equity += pnl
        self.trades.append(dict(symbol=self.symbol, side=p.side, entry_time=p.entry_time,
                                exit_time=time, entry=p.entry, exit=price, sl=p.sl,
                                tp=p.tp, lots=p.lots, pnl=pnl, reason=reason))
        self.position = None

    def on_bar(self, bar, *, equity=None, open_risk=0, allow_entry=True):
        if bar.time % 60 or bar.spread < 0 or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError('invalid M1 bar')
        if self.bars and bar.time <= self.bars[-1].time:
            raise ValueError('bars must be strictly chronological')
        if self.bars and bar.time != self.bars[-1].time + 60:
            self.window = self.pending = None
        if equity is not None:
            self.equity = equity
        # A trigger from the preceding completed minute executes at this open.
        if self.pending and not self.position:
            side, tp, distance = self.pending
            entry = bar.open + (bar.spread if side == 1 else 0)
            lots = lot_size(self.equity, self.risk, distance, self.contract,
                            self.step, self.minimum, self.maximum, open_risk)
            local = datetime.fromtimestamp(bar.time, timezone.utc).astimezone(NY)
            news_ok = not any(abs(n - bar.time) <= 1800 for n in self.news_times())
            if allow_entry and local.weekday() < 5 and local.hour * 60 + local.minute < 16 * 60 + 50 and news_ok and side * (tp - entry) >= distance and lots:
                # Count the next eight M15 boundaries after the entry.
                deadline = (bar.time // 900 + self.time_bars) * 900
                self.position = Position(side, bar.time, entry, entry - side * distance, tp, lots, deadline)
                self.reset[side] = False
            self.pending = None
        if self.position:
            p = self.position
            offset = bar.spread if p.side == -1 else 0
            op, hi, lo = bar.open + offset, bar.high + offset, bar.low + offset
            if (p.side == 1 and op <= p.sl) or (p.side == -1 and op >= p.sl):
                self.close(bar.time, op, 'sl')
            else:
                forced = self.forced_exit(bar.time)
                if forced:
                    self.close(bar.time, op, forced)
                elif (p.side == 1 and lo <= p.sl) or (p.side == -1 and hi >= p.sl):
                    self.close(bar.time, p.sl, 'sl')
                elif (p.side == 1 and hi >= p.tp) or (p.side == -1 and lo <= p.tp):
                    self.close(bar.time, p.tp, 'tp')
        previous = self.bars[-1] if self.bars else None
        self.bars.append(bar)
        if self.window and not self.position:
            side, start, tp, distance = self.window
            if start <= bar.time < start + 900 and previous:
                trigger = bar.close > previous.high if side == 1 else bar.close < previous.low
                if trigger:
                    self.pending = (side, tp, distance)
                    self.window = None
            if self.window and bar.time + 60 >= start + 900:
                self.window = None
        if (bar.time + 60) % 900 == 0:
            self.setup(bar.time + 60, bar.spread, allow_entry)

    def setup(self, time, spread, allow_entry):
        m15, m30 = aggregate(self.bars, 15), aggregate(self.bars, 30)
        a15, a30, trend = atr(m15), atr(m30), adx(m30)
        anchor = ema20(m15)
        if anchor is None or not a15 or a15[-1] <= 0 or m15[-1].time != time - 900:
            return
        z = (m15[-1].close - anchor) / a15[-1]
        if z > -1:
            self.reset[1] = True
        if z < 1:
            self.reset[-1] = True
        local = datetime.fromtimestamp(time, timezone.utc).astimezone(NY)
        self.window = None
        if not allow_entry or self.position or self.pending or local.weekday() >= 5 or not 180 <= local.hour * 60 + local.minute <= 900:
            return
        if len(a30) < 100 or not trend or trend[-1] >= self.adx_limit:
            return
        # Strict-less rank: tied volatility values share the same rank.
        rank = 100 * sum(v < a30[-1] for v in a30[-100:]) / 100
        baseline = self.spread_baseline(time)
        if rank >= 80 or baseline is None or spread > 1.5 * baseline:
            return
        if any(abs(n - time) <= 1800 for n in self.news_times()):
            return
        side = 1 if z <= -self.k else -1 if z >= self.k else 0
        if side and self.reset[side]:
            self.window = (side, time, anchor, a15[-1])
