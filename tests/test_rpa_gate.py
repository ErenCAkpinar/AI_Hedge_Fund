"""Engine validation gate for the RP-A screen.

``docs/designs/candidate-screen-rp-a.md`` makes the whole run VOID unless the new
weight engine first reproduces a known-good independent implementation:

    Running the engine with zero costs and no rebalancing must reproduce
    true_backtest.buy_hold_curve on the same universe and anchor to within
    0.5 percentage points of total return and 0.01 of Sharpe.

This file is committed before any candidate exists, so the tolerance can never be
chosen in response to a candidate result.  It fails loudly rather than printing.
"""

import pandas as pd
import pytest

import rpa_screen as rpa
from c1_forward import _invested_value
from portfolio_simulator import equity_metrics
from true_backtest import buy_hold_curve

# The frozen tolerance, quoted from the spec.  Never widen this.
MAX_TOTAL_RETURN_GAP_PP = 0.5
MAX_SHARPE_GAP = 0.01


@pytest.fixture(scope="module")
def bars():
    return rpa.load_bars()


@pytest.fixture(scope="module")
def window(bars):
    return rpa.build_window(bars)


def test_frozen_window_is_501_sessions_with_441_scored(bars, window):
    assert len(window.in_window) == 501
    assert len(window.traded) == 441
    assert str(window.in_window[0].date()) == "2024-08-29"
    assert str(window.in_window[-1].date()) == "2026-08-28"
    assert window.anchor == window.in_window[60]


def test_every_traded_session_has_a_bar_for_every_symbol(bars, window):
    rpa.require_complete_bars(bars, window.traded)
    assert len(rpa.SYMBOLS) == 17


def test_warmup_lookbacks_are_available_at_the_anchor(bars, window):
    """C2 needs 200 SPY sessions and C3 needs 126; neither may be truncated."""
    spy_before = [d for d in bars[rpa.BENCHMARK].index if d < window.anchor]
    assert len(spy_before) - 1 >= 200
    for symbol in rpa.SYMBOLS:
        before = [d for d in bars[symbol].index if d < window.anchor]
        assert len(before) >= 127, f"{symbol} cannot supply a 126-session lookback"


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────
def test_candidates_default_to_the_frozen_next_open_fill():
    """DECISION_CLOSE exists for the gate alone; nothing may default to it."""
    assert rpa.EngineConfig().fill_at == rpa.NEXT_OPEN


@pytest.fixture(scope="module")
def gate(bars, window):
    # The spec names two gate settings, zero costs and no rebalancing, and asks
    # the engine to meet buy_hold_curve "on the same universe and anchor".
    # buy_hold_curve establishes its book at the anchor's close, so gate mode
    # does too; candidates are unaffected and keep the frozen next-open fill.
    engine = rpa.run_engine(
        window.traded, bars, rpa.buy_and_hold_rule(),
        rpa.EngineConfig(side_cost_bps=0.0, fill_at=rpa.DECISION_CLOSE),
    )
    reference = buy_hold_curve(
        bars, window.in_window, list(rpa.SYMBOLS),
        rpa.INITIAL_CASH, anchor=window.anchor,
    )
    return engine, reference


def test_gate_series_are_the_same_length_and_start_at_the_same_capital(gate):
    engine, reference = gate
    assert len(engine.equity) == len(reference) == 441
    assert engine.equity.index.equals(reference.index)
    assert engine.equity.iloc[0] == pytest.approx(rpa.INITIAL_CASH, rel=0, abs=1e-9)
    assert float(reference.iloc[0]) == pytest.approx(rpa.INITIAL_CASH, rel=1e-12)


def test_engine_reproduces_buy_hold_curve_total_return(gate):
    engine, reference = gate
    got = equity_metrics(engine.equity)["total_return_pct"]
    want = equity_metrics(reference)["total_return_pct"]
    assert abs(got - want) <= MAX_TOTAL_RETURN_GAP_PP, (
        f"engine total return {got:.2f}% vs buy_hold_curve {want:.2f}% — "
        f"gap {abs(got - want):.3f}pp exceeds the frozen {MAX_TOTAL_RETURN_GAP_PP}pp; "
        "the run is VOID, do not loosen this bound"
    )


def test_engine_reproduces_buy_hold_curve_sharpe(gate):
    engine, reference = gate
    got = equity_metrics(engine.equity)["sharpe"]
    want = equity_metrics(reference)["sharpe"]
    assert abs(got - want) <= MAX_SHARPE_GAP, (
        f"engine Sharpe {got} vs buy_hold_curve {want} — gap {abs(got - want):.4f} "
        f"exceeds the frozen {MAX_SHARPE_GAP}; the run is VOID"
    )


def test_zero_cost_buy_and_hold_pays_nothing_after_its_single_entry(gate):
    engine, _ = gate
    assert engine.cost_paid.sum() == pytest.approx(0.0, abs=1e-12)
    # One entry, at the anchor, and never traded again.
    assert (engine.traded_notional > 0).sum() == 1
    assert engine.traded_notional.index[engine.traded_notional > 0][0] == engine.equity.index[0]
    assert len(engine.fills) == len(rpa.SYMBOLS)


def test_engine_tracks_buy_hold_curve_session_by_session(gate):
    """Stricter than the spec bound, so a ledger regression cannot hide inside it.

    The spec allows 0.5pp; the engine actually agrees to floating point. Holding
    the tight bound here means any future drift is caught as a bug rather than
    absorbed by a tolerance that was sized for a different question.
    """
    engine, reference = gate
    assert (engine.equity - reference).abs().max() < 1e-9


def test_engine_stays_long_only_and_inside_the_gross_cap(gate):
    engine, _ = gate
    assert (engine.gross_pct <= rpa.MAX_GROSS_EXPOSURE + 1e-9).all()
    assert (engine.cash >= -1e-9).all()
    assert (engine.weights >= -1e-12).all().all()


# ─────────────────────────────────────────────────────────────────────────────
# The cost solver must be the live C1 ledger's rule, not a second opinion
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("equity", [1_500.0, 987.65, 4_321.0])
def test_full_investment_solver_matches_c1_forward_exactly(equity):
    """At gross_target=1 the engine must reduce to c1_forward._invested_value."""
    current = {s: v for s, v in zip(rpa.SYMBOLS, [
        0.0, 10.0, 25.5, 100.0, 3.25, 0.0, 88.8, 12.0,
        40.0, 61.5, 7.0, 200.0, 0.0, 33.3, 19.9, 5.5, 150.0,
    ])}
    fracs = {s: 1.0 / len(rpa.SYMBOLS) for s in rpa.SYMBOLS}
    cost_rate = 10.0 / 10_000.0
    mine = rpa.solve_invested_notional(equity, current, fracs, cost_rate, 1.0)
    theirs = _invested_value(equity, [current[s] for s in rpa.SYMBOLS])
    assert mine == pytest.approx(theirs, rel=1e-12, abs=1e-9)


@pytest.mark.parametrize("gross_target", [0.25, 0.5, 0.75, 1.0])
def test_partial_investment_solver_satisfies_its_defining_equation(gross_target):
    current = {s: 20.0 for s in rpa.SYMBOLS}
    fracs = {s: 1.0 / len(rpa.SYMBOLS) for s in rpa.SYMBOLS}
    cost_rate = 20.0 / 10_000.0
    equity = 1_500.0
    invested = rpa.solve_invested_notional(equity, current, fracs, cost_rate, gross_target)
    cost = cost_rate * sum(
        abs(invested * fracs[s] - current[s]) for s in rpa.SYMBOLS
    )
    # V == gross_target * (equity - cost(V))
    assert invested == pytest.approx(gross_target * (equity - cost), rel=1e-9, abs=1e-9)


def test_zero_gross_target_liquidates_to_cash():
    current = {s: 20.0 for s in rpa.SYMBOLS}
    fracs = {s: 0.0 for s in rpa.SYMBOLS}
    assert rpa.solve_invested_notional(1_500.0, current, fracs, 0.001, 0.0) == 0.0


def test_engine_refuses_a_target_above_the_gross_cap(bars, window):
    def greedy(index, session, state):
        return {s: 0.2 for s in rpa.SYMBOLS} if index == 0 else None

    with pytest.raises(rpa.RpaScreenError, match="exceeds the"):
        rpa.run_engine(window.traded[:5], bars, greedy, rpa.EngineConfig())


def test_engine_refuses_a_negative_target_weight(bars, window):
    def shorting(index, session, state):
        if index != 0:
            return None
        target = {s: 1.0 / len(rpa.SYMBOLS) for s in rpa.SYMBOLS}
        target[rpa.SYMBOLS[0]] = -0.1
        return target

    with pytest.raises(rpa.RpaScreenError, match="long-only"):
        rpa.run_engine(window.traded[:5], bars, shorting, rpa.EngineConfig())


def test_missing_bar_is_reported_rather_than_filled_in(bars, window):
    broken = dict(bars)
    victim = rpa.SYMBOLS[0]
    broken[victim] = bars[victim].drop(index=[window.traded[10]])
    with pytest.raises(rpa.RpaScreenError, match="missing 1 bars"):
        rpa.require_complete_bars(broken, window.traded)
