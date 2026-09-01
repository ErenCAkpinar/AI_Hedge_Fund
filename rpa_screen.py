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
# c1_forward's basis, kept alongside for the divergence check in
# docs/designs/c1-forward-basis-divergence.md.
BASIS_B_BARS = Path(__file__).parent / "data" / "c1_basis_b_bars.csv"

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



# ─────────────────────────────────────────────────────────────────────────────
# Signals
#
# Every quantity below is read at or before the decision close, so no candidate
# can see a price it would not have had. The lookbacks are the spec's, unchanged:
# C2's 200-session SPY SMA, C3's 126-session total return, C4's 60-session
# realised volatility. Warm-up for all three comes from pre-window bars, which
# are never traded and never scored.
# ─────────────────────────────────────────────────────────────────────────────
CANDIDATE_NAMES = ("C1_EW_MONTHLY", "C2_TREND_SPY200", "C3_XMOM_TOP5",
                   "C4_VOLTGT_EW", "C5_TREND_XMOM")

XMOM_LOOKBACK = 126
XMOM_TOP_N = 5
TREND_SMA = 200
VOL_LOOKBACK = 60
VOL_TARGET = 0.15
TRADING_DAYS = 252


def close_matrix(frames: Mapping[str, pd.DataFrame],
                 calendar: Sequence[pd.Timestamp]) -> pd.DataFrame:
    """Closes for the 17 symbols over the full fetched calendar, warm-up included."""
    index = pd.DatetimeIndex(calendar)
    return pd.DataFrame(
        {s: frames[s]["Close"].reindex(index) for s in SYMBOLS}, index=index
    )


def month_first_flags(sessions: Sequence[pd.Timestamp],
                      calendar: Sequence[pd.Timestamp]) -> list[bool]:
    """True where a traded session is the first market session of its month.

    The predecessor is taken from the full market calendar, not from the traded
    slice, so the anchor is judged against the session that really preceded it.
    """
    order = {d: i for i, d in enumerate(calendar)}
    flags = []
    for session in sessions:
        i = order[session]
        prev = calendar[i - 1] if i else None
        flags.append(prev is None or (prev.year, prev.month) != (session.year, session.month))
    return flags


def trend_exposure(frames: Mapping[str, pd.DataFrame],
                   calendar: Sequence[pd.Timestamp]) -> pd.Series:
    """C2's filter: 1.0 while SPY's previous close is above its trailing 200-SMA.

    Both the close and the average are read as of the previous session, which is
    the literal reading of "SPY's previous close is above its trailing 200-session
    simple moving average", and is one session more conservative than the frozen
    close-decision rule already requires.
    """
    spy = frames[BENCHMARK]["Close"].reindex(pd.DatetimeIndex(calendar))
    above = spy > spy.rolling(TREND_SMA).mean()
    return above.shift(1).map({True: 1.0, False: 0.0}).astype(float)


def momentum_scores(closes: pd.DataFrame) -> pd.DataFrame:
    """C3's rank key: trailing 126-session total return through the previous close."""
    prev = closes.shift(1)
    return prev / prev.shift(XMOM_LOOKBACK) - 1.0


def basket_returns(closes: pd.DataFrame) -> pd.Series:
    """Daily return of the equal-weight basket, i.e. the C1 basket itself.

    Taken from prices rather than from C1's traded equity because vol60 must be
    defined at the anchor, where C1 has no trading history yet, and because a
    price-level basket does not make C4's exposure depend on C1's cost level.
    """
    return closes.pct_change(fill_method=None).mean(axis=1)


def volatility_scale(closes: pd.DataFrame) -> pd.Series:
    """C4's multiplier: min(1, 0.15 / vol60) on the trailing 60-session basket vol."""
    vol = basket_returns(closes).rolling(VOL_LOOKBACK).std(ddof=1) * math.sqrt(TRADING_DAYS)
    return (VOL_TARGET / vol).clip(upper=1.0)


def top_momentum_names(scores: pd.Series) -> list[str]:
    """The C3 basket on one session: highest 126-session return, five names."""
    ranked = scores.dropna()
    if len(ranked) < len(SYMBOLS):
        raise RpaScreenError(
            f"only {len(ranked)}/{len(SYMBOLS)} symbols have a "
            f"{XMOM_LOOKBACK}-session lookback; the ranking would be truncated"
        )
    order = sorted(ranked.index, key=lambda s: (-float(ranked[s]), s))
    chosen, cut = order[:XMOM_TOP_N], order[XMOM_TOP_N - 1:XMOM_TOP_N + 1]
    if len(cut) == 2 and float(ranked[cut[0]]) == float(ranked[cut[1]]):
        raise RpaScreenError(f"tie at the top-{XMOM_TOP_N} boundary: {cut}")
    return chosen


# ─────────────────────────────────────────────────────────────────────────────
# The five candidates, parameterised exactly as the spec froze them
# ─────────────────────────────────────────────────────────────────────────────
def _rebalance_days(sessions, calendar) -> list[bool]:
    """Decision days: each month's first session, plus the anchor.

    The anchor entry is forced because the scored window opens there and every
    candidate must be live from its first scored session; leaving C3/C4/C5 in
    cash until December would be an artefact of the anchor, not a property of the
    rule. The gate requires the same thing of the engine.
    """
    flags = month_first_flags(sessions, calendar)
    flags[0] = True
    return flags


def c1_ew_monthly(sessions, calendar) -> TargetRule:
    rebalance = _rebalance_days(sessions, calendar)
    weight = 1.0 / len(SYMBOLS)

    def rule(index, session, state):
        return {s: weight for s in SYMBOLS} if rebalance[index] else None

    return rule


def c3_xmom_top5(sessions, calendar, scores: pd.DataFrame) -> TargetRule:
    rebalance = _rebalance_days(sessions, calendar)
    weight = 1.0 / XMOM_TOP_N

    def rule(index, session, state):
        if not rebalance[index]:
            return None
        return {s: weight for s in top_momentum_names(scores.loc[session])}

    return rule


def c4_voltgt_ew(sessions, calendar, scale: pd.Series) -> TargetRule:
    rebalance = _rebalance_days(sessions, calendar)

    def rule(index, session, state):
        if not rebalance[index]:
            return None
        k = float(scale.loc[session])
        if not math.isfinite(k):
            raise RpaScreenError(f"{session.date()}: vol60 is undefined")
        return {s: k / len(SYMBOLS) for s in SYMBOLS}

    return rule


def _overlay_rule(sessions, calendar, exposure: pd.Series,
                  base_rule: TargetRule, base_sleeve: pd.DataFrame) -> TargetRule:
    """A trend switch laid over a base candidate: base weights on, cash off.

    Trades only when the base rebalances or the switch flips. Holding the base's
    own drifted proportions in between keeps the overlay's book proportional to
    the base's at all times, so "C1 weights" and "C3 holdings" mean what they say
    rather than a fresh equal weighting every session.
    """
    rebalance = _rebalance_days(sessions, calendar)
    on = [float(exposure.loc[d]) for d in sessions]

    def rule(index, session, state):
        flipped = index == 0 or on[index] != on[index - 1]
        if not (rebalance[index] or flipped):
            return None
        if rebalance[index]:
            base = base_rule(index, session, state) or {}
        else:
            base = base_sleeve.loc[session].to_dict()
        return {s: base.get(s, 0.0) * on[index] for s in SYMBOLS}

    return rule


def build_candidates(frames, window: Window, config: EngineConfig) -> dict[str, EngineResult]:
    """Run all five candidates at one cost level.

    C2 and C5 are overlays on C1 and C3, so their bases are run first and their
    realised weights are fed forward; nothing is recomputed a second way.
    """
    sessions, calendar = window.traded, window.full
    closes = close_matrix(frames, calendar)
    scores = momentum_scores(closes)
    scale = volatility_scale(closes)
    exposure = trend_exposure(frames, calendar)

    for name, series in (("trend filter", exposure.loc[sessions]),
                         ("vol60 scale", scale.loc[sessions])):
        if series.isna().any():
            raise RpaScreenError(f"{name} is undefined on {int(series.isna().sum())} traded sessions")

    c1_rule = c1_ew_monthly(sessions, calendar)
    c3_rule = c3_xmom_top5(sessions, calendar, scores)

    results: dict[str, EngineResult] = {}
    results["C1_EW_MONTHLY"] = run_engine(sessions, frames, c1_rule, config)
    results["C3_XMOM_TOP5"] = run_engine(sessions, frames, c3_rule, config)
    results["C2_TREND_SPY200"] = run_engine(
        sessions, frames,
        _overlay_rule(sessions, calendar, exposure, c1_rule,
                      results["C1_EW_MONTHLY"].sleeve_weights),
        config)
    results["C4_VOLTGT_EW"] = run_engine(sessions, frames,
                                         c4_voltgt_ew(sessions, calendar, scale), config)
    results["C5_TREND_XMOM"] = run_engine(
        sessions, frames,
        _overlay_rule(sessions, calendar, exposure, c3_rule,
                      results["C3_XMOM_TOP5"].sleeve_weights),
        config)
    return {name: results[name] for name in CANDIDATE_NAMES}


def spy_curve(frames, window: Window) -> pd.Series:
    """Frictionless SPY buy-and-hold, rebased at the anchor close.

    Built the same way true_backtest builds its SPY benchmark, and scored by the
    same equity_metrics, as go-no-go.md requires. It pays no execution cost,
    which is an asymmetry in SPY's favour and is stated in the report.
    """
    spy = frames[BENCHMARK]["Close"].reindex(pd.DatetimeIndex(window.traded))
    return spy / float(spy.iloc[0]) * INITIAL_CASH



# ─────────────────────────────────────────────────────────────────────────────
# The screen: gate, cost grid, PASS conditions
# ─────────────────────────────────────────────────────────────────────────────
EQUITY_ARTIFACT = Path(__file__).parent / "data" / "rpa_equity.csv"
BENCHMARK_ARTIFACT = Path(__file__).parent / "data" / "rpa_benchmarks.csv"
RESULTS_ARTIFACT = Path(__file__).parent / "data" / "rpa_results.json"

GATE_RETURN_TOLERANCE_PP = 0.5
GATE_SHARPE_TOLERANCE = 0.01
DRAWDOWN_MULTIPLE = 1.25
# Condition 3 is only trustworthy if the second, independent standard error was
# actually computed. Without statsmodels the HAC path degrades silently to one
# implementation, which is the shape of bug this repo has been caught by before.
HAC_AGREEMENT_TOLERANCE = 1e-10


def daily_returns(equity: pd.Series) -> pd.Series:
    """The return series every statistic is formed from, defined once."""
    return equity.pct_change(fill_method=None).dropna()


def run_gate(frames, window: Window) -> dict:
    """Reproduce true_backtest.buy_hold_curve; the run is VOID unless this passes."""
    from true_backtest import buy_hold_curve
    from portfolio_simulator import equity_metrics

    engine = run_engine(window.traded, frames, buy_and_hold_rule(),
                        EngineConfig(side_cost_bps=0.0, fill_at=DECISION_CLOSE))
    reference = buy_hold_curve(frames, window.in_window, list(SYMBOLS),
                               INITIAL_CASH, anchor=window.anchor)
    got, want = equity_metrics(engine.equity), equity_metrics(reference)
    return_gap = abs(got["total_return_pct"] - want["total_return_pct"])
    sharpe_gap = abs(got["sharpe"] - want["sharpe"])
    return {
        "engine": got,
        "buy_hold_curve": want,
        "total_return_gap_pp": return_gap,
        "sharpe_gap": sharpe_gap,
        "max_abs_equity_difference": float((engine.equity - reference).abs().max()),
        "tolerance_total_return_pp": GATE_RETURN_TOLERANCE_PP,
        "tolerance_sharpe": GATE_SHARPE_TOLERANCE,
        "passed": bool(return_gap <= GATE_RETURN_TOLERANCE_PP
                       and sharpe_gap <= GATE_SHARPE_TOLERANCE),
        "engine_series": engine.equity,
    }


def evaluate_conditions(results: Mapping[str, EngineResult]) -> dict:
    """The four PASS conditions, candidate by candidate, against C1."""
    from portfolio_simulator import equity_metrics
    from rpa_stats import FAMILY_ALPHA, hac_mean_test, holm

    base = results["C1_EW_MONTHLY"]
    base_metrics = equity_metrics(base.equity)
    base_returns = daily_returns(base.equity)

    tests, rows = {}, {}
    for name in CANDIDATE_NAMES[1:]:
        diff = (daily_returns(results[name].equity) - base_returns).dropna()
        tests[name] = hac_mean_test(diff)
        rows[name] = {"metrics": equity_metrics(results[name].equity),
                      "active": tests[name]}

    adjusted = holm({n: t["p_one_sided"] for n, t in tests.items()}, alpha=FAMILY_ALPHA)

    for name, row in rows.items():
        stat, metrics = row["active"], row["metrics"]
        c1_sharpe, ci_sharpe = base_metrics["sharpe"], metrics["sharpe"]
        c1_dd, ci_dd = abs(base_metrics["max_drawdown_pct"]), abs(metrics["max_drawdown_pct"])
        row["holm"] = adjusted[name]
        row["annualised_active_return_pct"] = 252.0 * stat["mean"] * 100.0
        row["conditions"] = {
            "1_sharpe_beats_c1": {
                "value": ci_sharpe, "reference": c1_sharpe,
                "passed": bool(ci_sharpe > c1_sharpe)},
            "2_positive_active_return": {
                "value": 252.0 * stat["mean"] * 100.0, "reference": 0.0,
                "passed": bool(252.0 * stat["mean"] > 0)},
            "3_holm_adjusted_hac_test": {
                "value": adjusted[name]["p_holm"], "reference": FAMILY_ALPHA,
                "p_raw": stat["p_one_sided"], "t_stat": stat["t_stat"],
                "passed": bool(adjusted[name]["reject_null"])},
            # A drawdown "no worse than 1.25x" C1's, compared as magnitudes so the
            # sign convention of max_drawdown_pct cannot invert the test.
            "4_drawdown_within_1_25x": {
                "value": ci_dd, "reference": DRAWDOWN_MULTIPLE * c1_dd,
                "passed": bool(ci_dd <= DRAWDOWN_MULTIPLE * c1_dd)},
        }
        row["passed"] = all(c["passed"] for c in row["conditions"].values())

    return {"c1": base_metrics, "candidates": rows}


def run_screen(frames=None, path: Path = RESULTS_ARTIFACT) -> dict:
    """Run the gate, then the full cost grid, and write the committed artifacts."""
    import json
    from datetime import datetime, timezone

    from portfolio_simulator import equity_metrics

    frames = load_bars() if frames is None else frames
    window = build_window(frames)
    require_complete_bars(frames, window.traded)

    gate = run_gate(frames, window)
    gate_series = gate.pop("engine_series")
    if not gate["passed"]:
        raise RpaScreenError(
            "engine validation FAILED; per the spec the run is VOID and no "
            "candidate number may be quoted"
        )

    spy = spy_curve(frames, window)
    grid, equity_rows = {}, []
    for bps in COST_GRID_BPS:
        results = build_candidates(frames, window, EngineConfig(side_cost_bps=bps))
        for name, result in results.items():
            for session, value in result.equity.items():
                equity_rows.append({"cost_bps": bps, "candidate": name,
                                    "date": session.strftime("%Y-%m-%d"),
                                    "equity": float(value)})
        grid[f"{bps:.0f}"] = {
            "metrics": {n: equity_metrics(r.equity) for n, r in results.items()},
            "cost_paid": {n: float(r.cost_paid.sum()) for n, r in results.items()},
            "traded_notional": {n: float(r.traded_notional.sum()) for n, r in results.items()},
            "sessions_in_cash": {n: int((r.gross_pct <= 1e-9).sum()) for n, r in results.items()},
            "evaluation": evaluate_conditions(results),
        }

    pd.DataFrame(equity_rows).to_csv(EQUITY_ARTIFACT, index=False)
    pd.DataFrame(
        [{"series": "SPY", "date": d.strftime("%Y-%m-%d"), "equity": float(v)}
         for d, v in spy.items()]
        + [{"series": "GATE_BUYHOLD", "date": d.strftime("%Y-%m-%d"), "equity": float(v)}
           for d, v in gate_series.items()]
    ).to_csv(BENCHMARK_ARTIFACT, index=False)

    for bps, level in grid.items():
        for name, row in level["evaluation"]["candidates"].items():
            stat = row["active"]
            if stat["se_statsmodels"] is None:
                raise RpaScreenError(
                    f"{name} at {bps} bps: statsmodels is unavailable, so condition 3's "
                    "standard error was computed only once; install statsmodels rather "
                    "than reporting a number this repo cannot cross-check"
                )
            if stat["se_agreement_rel"] > HAC_AGREEMENT_TOLERANCE:
                raise RpaScreenError(
                    f"{name} at {bps} bps: the two HAC standard errors disagree by "
                    f"{stat['se_agreement_rel']:.3e}; refusing to report condition 3"
                )

    base = grid[f"{BASE_COST_BPS:.0f}"]
    passed = [n for n, row in base["evaluation"]["candidates"].items() if row["passed"]]
    report = {
        "spec": "docs/designs/candidate-screen-rp-a.md",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {
            "fetch_start": WARMUP_FETCH_START,
            "frozen_start": str(window.in_window[0].date()),
            "frozen_end": str(window.in_window[-1].date()),
            "in_window_sessions": len(window.in_window),
            "anchor": str(window.anchor.date()),
            "scored_sessions": len(window.traded),
        },
        "config": {
            "capital": INITIAL_CASH, "symbols": list(SYMBOLS),
            "max_gross_exposure": MAX_GROSS_EXPOSURE,
            "base_cost_bps": BASE_COST_BPS, "cost_grid_bps": list(COST_GRID_BPS),
            "fill": "signal on session close, fill at next open",
            "price_basis": "yfinance auto_adjust=True (split and dividend adjusted)",
        },
        "gate": gate,
        "hac_cross_check": {
            "tolerance_rel": HAC_AGREEMENT_TOLERANCE,
            "worst_disagreement_rel": max(
                row["active"]["se_agreement_rel"]
                for level in grid.values()
                for row in level["evaluation"]["candidates"].values()),
            "paths": sorted({row["active"]["se_path"]
                             for level in grid.values()
                             for row in level["evaluation"]["candidates"].values()}),
        },
        "benchmarks": {"SPY": equity_metrics(spy)},
        "cost_grid": grid,
        "base_case_bps": BASE_COST_BPS,
        "passing_candidates": passed,
        "verdict": "PASS" if passed else "FAIL",
    }
    path.write_text(json.dumps(report, indent=2, default=str))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RP-A screen engine utilities.")
    parser.add_argument("--build-bars", action="store_true",
                        help="download and write the committed bar artifact")
    parser.add_argument("--run", action="store_true",
                        help="run the gate, then the candidates, and write the artifacts")
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
    if args.run:
        report = run_screen()
        gate = report["gate"]
        print(f"gate: {'PASS' if gate['passed'] else 'VOID'}  "
              f"return gap {gate['total_return_gap_pp']:.4f}pp  "
              f"sharpe gap {gate['sharpe_gap']:.4f}  "
              f"max|equity diff| {gate['max_abs_equity_difference']:.2e}")
        print(f"wrote {RESULTS_ARTIFACT.name}, {EQUITY_ARTIFACT.name}, "
              f"{BENCHMARK_ARTIFACT.name}")
        print(f"verdict at {BASE_COST_BPS:.0f} bps: {report['verdict']}  "
              f"{report['passing_candidates']}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
