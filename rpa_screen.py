"""RP-A candidate screen — bar cache and weight engine.

Implements the execution engine required by ``docs/designs/candidate-screen-rp-a.md``.
That document was committed before this module existed and freezes the window,
universe, capital, cost grid and candidate rules.  Nothing here may reparameterise
them, so the frozen values are imported from ``true_backtest`` rather than restated.

The candidates themselves are deliberately absent from this commit: the spec makes
the run VOID unless the engine first reproduces a known-good independent
implementation, so the gate is written and passed before any candidate exists.

Two points where the spec is silent were resolved by the operator, not by this
module, and are recorded in ``docs/designs/rp-a-results.md``:

  * **Indicator warm-up** is sourced from bars *before* the frozen window.  C2
    needs 200 SPY sessions and C3 needs 126, but the frozen in-window warm-up is
    60.  The traded and scored windows stay exactly 501/441 sessions; pre-window
    bars are never traded and never scored.  Re-fetching with an earlier start
    does not disturb in-window adjusted prices (verified: max relative drift
    3e-7, float32 storage noise).
  * **Price basis** is yfinance auto-adjusted (split *and* dividend adjusted),
    matching ``true_backtest.veri_cek``, which is what the gate's
    ``true_backtest.buy_hold_curve`` is fed.  ``c1_forward.py`` deliberately uses
    the opposite basis (split-adjusted plus an explicit dividend cash credit).
    The two bases differ by ~1.04pp of basket total return over this window; the
    divergence is documented rather than quietly reconciled.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

import pandas as pd

# Frozen parameters are imported, never restated, so that this module cannot
# drift from the values true_backtest already runs on.
from true_backtest import (
    BACKTEST_END,
    BACKTEST_START,
    BASLANGIC_SERMAYE,
    ISINMA_GUN,
    WATCHLIST,
)

SYMBOLS: tuple[str, ...] = tuple(WATCHLIST)
BENCHMARK = "SPY"

# Bars before the frozen window exist only to warm up C2's 200-session SPY SMA
# and C3's 126-session lookback.  They are never traded and never scored.
WARMUP_FETCH_START = "2023-08-01"

INITIAL_CASH = float(BASLANGIC_SERMAYE)
MAX_GROSS_EXPOSURE = 1.00
BASE_COST_BPS = 10.0
COST_GRID_BPS: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0)

BAR_CACHE = Path(__file__).parent / "data" / "rpa_bars.csv"

_EPS = 1e-9


class RpaScreenError(RuntimeError):
    """The engine cannot proceed without producing a number that is not real."""


# ─────────────────────────────────────────────────────────────────────────────
# Bars
# ─────────────────────────────────────────────────────────────────────────────
def build_bar_cache(path: Path | str = BAR_CACHE) -> Path:
    """Download the universe plus SPY and write the committed bar artifact.

    Uses the same yfinance call shape as ``true_backtest.veri_cek`` — notably its
    default ``auto_adjust=True`` — so the gate compares engine output against
    ``buy_hold_curve`` on exactly the price basis true_backtest itself uses.
    """
    import yfinance as yf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for symbol in (BENCHMARK, *SYMBOLS):
        frame = yf.Ticker(symbol).history(
            start=WARMUP_FETCH_START, end=BACKTEST_END, interval="1d",
        )
        if frame.empty:
            raise RpaScreenError(f"{symbol}: no bars downloaded")
        frame = frame.copy()
        frame.index = pd.DatetimeIndex([ts.date() for ts in frame.index])
        for session, bar in frame.iterrows():
            rows.append({
                "date": session.strftime("%Y-%m-%d"),
                "symbol": symbol,
                "open": float(bar["Open"]),
                "high": float(bar["High"]),
                "low": float(bar["Low"]),
                "close": float(bar["Close"]),
            })

    table = pd.DataFrame(rows).sort_values(["symbol", "date"], kind="stable")
    table.to_csv(path, index=False)
    return path


def load_bars(path: Path | str = BAR_CACHE) -> dict[str, pd.DataFrame]:
    """Load the committed bar artifact into per-symbol frames."""
    path = Path(path)
    if not path.exists():
        raise RpaScreenError(
            f"{path} is missing; run `python rpa_screen.py --build-bars` first"
        )
    table = pd.read_csv(path)
    frames: dict[str, pd.DataFrame] = {}
    for symbol, group in table.groupby("symbol", sort=False):
        frame = group.sort_values("date", kind="stable").set_index(
            pd.DatetimeIndex(pd.to_datetime(group.sort_values("date", kind="stable")["date"]))
        )
        frames[str(symbol)] = frame[["open", "high", "low", "close"]].rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}
        )
    missing = [s for s in (BENCHMARK, *SYMBOLS) if s not in frames]
    if missing:
        raise RpaScreenError(f"bar cache is missing {missing}")
    return frames


@dataclass(frozen=True)
class Window:
    """The frozen calendar, split into what is fetched, traded and scored."""

    full: list[pd.Timestamp]      # every session in the cache, warm-up included
    in_window: list[pd.Timestamp]  # the 501 frozen sessions
    traded: list[pd.Timestamp]     # the 441 scored sessions, from the anchor

    @property
    def anchor(self) -> pd.Timestamp:
        return self.traded[0]


def build_window(frames: Mapping[str, pd.DataFrame]) -> Window:
    """Derive the frozen calendar from SPY, exactly as true_backtest does.

    ``true_backtest`` takes its calendar from SPY's bars over the frozen window
    and anchors scoring at ``calendar[ISINMA_GUN]``.  Both are reproduced here so
    the engine and the gate reference share one definition of the window.
    """
    spy = frames[BENCHMARK]
    full = list(spy.index)
    start, end = pd.Timestamp(BACKTEST_START), pd.Timestamp(BACKTEST_END)
    in_window = [d for d in full if start <= d < end]
    if len(in_window) <= ISINMA_GUN:
        raise RpaScreenError(f"only {len(in_window)} in-window sessions")
    return Window(full=full, in_window=in_window, traded=in_window[ISINMA_GUN:])


def require_complete_bars(frames: Mapping[str, pd.DataFrame], sessions: Sequence[pd.Timestamp]) -> None:
    """Fail loudly if any traded session is missing a bar for any symbol."""
    for symbol in SYMBOLS:
        have = frames[symbol].index
        gaps = [d for d in sessions if d not in have]
        if gaps:
            raise RpaScreenError(
                f"{symbol} is missing {len(gaps)} bars in the traded window, "
                f"first {gaps[0].date()}; the engine will not fabricate them"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────
NEXT_OPEN = "NEXT_OPEN"
DECISION_CLOSE = "DECISION_CLOSE"


@dataclass(frozen=True)
class EngineConfig:
    initial_cash: float = INITIAL_CASH
    side_cost_bps: float = BASE_COST_BPS
    max_gross_exposure: float = MAX_GROSS_EXPOSURE
    # NEXT_OPEN is the frozen candidate contract. DECISION_CLOSE exists only so the
    # validation gate can meet true_backtest.buy_hold_curve on its own entry
    # convention; no candidate may use it.
    fill_at: str = NEXT_OPEN


@dataclass
class EngineResult:
    equity: pd.Series
    cash: pd.Series
    gross_pct: pd.Series
    weights: pd.DataFrame          # close-mark weight of each symbol, fraction of equity
    cost_paid: pd.Series
    traded_notional: pd.Series
    fills: list[dict] = field(default_factory=list)

    @property
    def sleeve_weights(self) -> pd.DataFrame:
        """Weights renormalised within the invested sleeve; zero rows stay zero."""
        gross = self.weights.sum(axis=1)
        safe = gross.where(gross > _EPS, other=1.0)
        out = self.weights.div(safe, axis=0)
        out.loc[gross <= _EPS, :] = 0.0
        return out


def solve_invested_notional(
    equity: float,
    current_values: Mapping[str, float],
    sleeve_fracs: Mapping[str, float],
    cost_rate: float,
    gross_target: float,
) -> float:
    """Total notional ``V`` to hold after trading, net of the cost of getting there.

    Solves ``V = gross_target * (equity - cost(V))`` where
    ``cost(V) = cost_rate * sum |V * frac_i - current_i|``.  ``cost`` is convex and
    not monotone, but ``V + gross_target * cost(V)`` has derivative bounded below
    by ``1 - gross_target * cost_rate > 0``, so bisection is valid.

    At ``gross_target = 1`` this reduces term for term to ``c1_forward._invested_value``,
    which is the live C1 ledger's rule; the equivalence is asserted in the tests.
    """
    if not math.isfinite(equity) or equity <= 0:
        raise RpaScreenError("cannot rebalance non-positive equity")
    if gross_target <= 0:
        return 0.0

    def cost(candidate: float) -> float:
        return cost_rate * sum(
            abs(candidate * sleeve_fracs.get(s, 0.0) - current_values.get(s, 0.0))
            for s in set(sleeve_fracs) | set(current_values)
        )

    low, high = 0.0, equity
    for _ in range(100):
        mid = (low + high) / 2.0
        if mid + gross_target * cost(mid) <= gross_target * equity:
            low = mid
        else:
            high = mid
    return low


TargetRule = Callable[[int, pd.Timestamp, "EngineState"], Mapping[str, float] | None]


@dataclass
class EngineState:
    """Read-only view handed to a rule at a session close."""

    cash: float
    positions: dict[str, float]
    close_values: dict[str, float]
    equity: float


def run_engine(
    sessions: Sequence[pd.Timestamp],
    frames: Mapping[str, pd.DataFrame],
    rule: TargetRule,
    config: EngineConfig,
) -> EngineResult:
    """Run one candidate over ``sessions``.

    The frozen execution contract: ``rule`` is evaluated at a session **close** and
    returns target weights as fractions of post-trade equity (summing to at most
    ``max_gross_exposure``), or ``None`` to hold and let the book drift.  A returned
    target fills at the **next** session's open, paying ``side_cost_bps`` per side on
    traded notional.  The final session's decision therefore never fills, which is
    correct: there is no next open inside the window.
    """
    cost_rate = config.side_cost_bps / 10_000.0
    if config.fill_at not in (NEXT_OPEN, DECISION_CLOSE):
        raise RpaScreenError(f"unknown fill convention {config.fill_at!r}")
    cash = float(config.initial_cash)
    positions: dict[str, float] = {s: 0.0 for s in SYMBOLS}
    pending: Mapping[str, float] | None = None

    eq_rows, cash_rows, gross_rows, w_rows, cost_rows, notional_rows = [], [], [], [], [], []
    fills: list[dict] = []

    def execute(session: pd.Timestamp, target: Mapping[str, float], column: str):
        nonlocal cash
        prices = {s: float(frames[s].loc[session, column]) for s in SYMBOLS}
        current = {s: positions[s] * prices[s] for s in SYMBOLS}
        equity_pre = cash + sum(current.values())
        gross_target = float(sum(target.values()))
        if gross_target > config.max_gross_exposure + _EPS:
            raise RpaScreenError(
                f"{session.date()}: target gross {gross_target:.6f} exceeds "
                f"the {config.max_gross_exposure:.0%} cap"
            )
        if any(w < -_EPS for w in target.values()):
            raise RpaScreenError(f"{session.date()}: long-only violated by target")

        fracs = (
            {s: target.get(s, 0.0) / gross_target for s in SYMBOLS}
            if gross_target > _EPS else {s: 0.0 for s in SYMBOLS}
        )
        invested = solve_invested_notional(
            equity_pre, current, fracs, cost_rate, gross_target
        )
        deltas = {s: (invested * fracs[s]) / prices[s] - positions[s] for s in SYMBOLS}
        spent, moved = 0.0, 0.0
        # Sell before buy, matching c1_forward's fill ordering.
        for side in ("SELL", "BUY"):
            for symbol in SYMBOLS:
                delta = deltas[symbol]
                if (side == "SELL" and delta >= -1e-12) or (side == "BUY" and delta <= 1e-12):
                    continue
                qty = abs(delta)
                raw = prices[symbol]
                fill = raw * (1.0 - cost_rate if side == "SELL" else 1.0 + cost_rate)
                charge = qty * raw * cost_rate
                if side == "SELL":
                    positions[symbol] -= qty
                    cash += qty * fill
                else:
                    positions[symbol] += qty
                    cash -= qty * fill
                spent += charge
                moved += qty * raw
                fills.append({
                    "session": session.strftime("%Y-%m-%d"), "symbol": symbol,
                    "side": side, "qty": qty, "raw_price": raw,
                    "fill_price": fill, "execution_cost": charge,
                })
        if abs(cash) < _EPS:
            cash = 0.0
        if cash < -_EPS or any(q < -1e-10 for q in positions.values()):
            raise RpaScreenError(
                f"{session.date()}: fill would violate cash or long-only constraints"
            )
        return spent, moved

    def mark(session: pd.Timestamp):
        closes = {s: float(frames[s].loc[session, "Close"]) for s in SYMBOLS}
        values = {s: positions[s] * closes[s] for s in SYMBOLS}
        gross = sum(values.values())
        equity = cash + gross
        if equity <= 0:
            raise RpaScreenError(f"{session.date()}: equity went non-positive")
        gross_pct = gross / equity
        if gross_pct > config.max_gross_exposure + 1e-9:
            raise RpaScreenError(
                f"{session.date()}: close mark gross {gross_pct:.6f} exceeds the cap"
            )
        return values, gross_pct, equity

    for index, session in enumerate(sessions):
        session_cost = 0.0
        session_notional = 0.0

        if config.fill_at == NEXT_OPEN:
            if pending is not None:
                session_cost, session_notional = execute(session, pending, "Open")
                pending = None
            values, gross_pct, equity = mark(session)
            state = EngineState(cash=cash, positions=dict(positions),
                                close_values=values, equity=equity)
            target = rule(index, session, state)
            if target is not None:
                pending = dict(target)
        else:
            values, gross_pct, equity = mark(session)
            state = EngineState(cash=cash, positions=dict(positions),
                                close_values=values, equity=equity)
            target = rule(index, session, state)
            if target is not None:
                session_cost, session_notional = execute(session, target, "Close")
            values, gross_pct, equity = mark(session)

        eq_rows.append(equity)
        cash_rows.append(cash)
        gross_rows.append(gross_pct)
        w_rows.append({s: values[s] / equity for s in SYMBOLS})
        cost_rows.append(session_cost)
        notional_rows.append(session_notional)

    calendar = pd.DatetimeIndex(sessions)
    return EngineResult(
        equity=pd.Series(eq_rows, index=calendar, name="equity"),
        cash=pd.Series(cash_rows, index=calendar, name="cash"),
        gross_pct=pd.Series(gross_rows, index=calendar, name="gross_pct"),
        weights=pd.DataFrame(w_rows, index=calendar, columns=list(SYMBOLS)),
        cost_paid=pd.Series(cost_rows, index=calendar, name="cost_paid"),
        traded_notional=pd.Series(notional_rows, index=calendar, name="traded_notional"),
        fills=fills,
    )


def buy_and_hold_rule(symbols: Sequence[str] = SYMBOLS) -> TargetRule:
    """Enter equal weight once, at the first session close, then never trade again.

    This is the configuration the spec's engine-validation clause names: "zero
    costs and no rebalancing".  It is not a candidate.
    """
    weight = 1.0 / len(symbols)

    def rule(index: int, session: pd.Timestamp, state: EngineState):
        if index == 0:
            return {s: weight for s in symbols}
        return None

    return rule


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RP-A screen engine utilities.")
    parser.add_argument("--build-bars", action="store_true",
                        help="download and write the committed bar artifact")
    args = parser.parse_args(argv)
    if args.build_bars:
        path = build_bar_cache()
        frames = load_bars(path)
        window = build_window(frames)
        require_complete_bars(frames, window.traded)
        print(f"wrote {path}")
        print(f"  fetched sessions : {len(window.full)}  from {window.full[0].date()}")
        print(f"  frozen window    : {len(window.in_window)} sessions "
              f"{window.in_window[0].date()} → {window.in_window[-1].date()}")
        print(f"  traded / scored  : {len(window.traded)} sessions from {window.anchor.date()}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
