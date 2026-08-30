"""Tests for portfolio_simulator.

Every test uses synthetic frames. The simulator imports nothing from this
project, so none of this needs network, yfinance, or the signal layer.

Test list agreed with Codex (gpt-5.6-sol) on 2026-08-30; see
docs/designs/backtest-live-parity.md.
"""



import pandas as pd
import pytest

from portfolio_simulator import (
    SignalDecision,
    SimulatorConfig,
    adverse_price,
    simulate_portfolio,
)


def cal(n, start="2025-01-02"):
    return list(pd.bdate_range(start, periods=n))


def frame(dates, o=None, h=None, l=None, c=None, flat=100.0):
    """Build an OHLC frame. Scalars broadcast, lists are used as-is."""
    def col(v):
        if v is None:
            return [flat] * len(dates)
        if isinstance(v, (int, float)):
            return [float(v)] * len(dates)
        return [float(x) for x in v]
    return pd.DataFrame(
        {"Open": col(o), "High": col(h if h is not None else o),
         "Low": col(l if l is not None else o), "Close": col(c if c is not None else o)},
        index=pd.DatetimeIndex(dates))


def once(symbol, on_date, side=1, score=0.5, frac=0.30, atr=2.0, mult=2.5):
    """Signal callback that fires for one symbol on one date."""
    def fn(sym, date):
        if sym == symbol and date == on_date:
            return SignalDecision(side, score, "YÜKSEK", frac, atr, mult)
        return None
    return fn


NOCOST = SimulatorConfig(half_spread_bps=0.0, slippage_bps=0.0,
                         sec_fee_rate=0.0, finra_taf_per_share=0.0,
                         cat_fee_per_share=0.0)


# ── the ordering rule: today's high cannot create today's stop hit ───────────
def test_todays_favourable_extreme_cannot_trigger_todays_stop():
    """The defect this simulator exists to fix.

    Bar 3 spikes high then collapses. The old code raised the trail from that
    high and tested the same bar's low against it, producing a same-bar exit
    that assumes the high came first. Only the stop known before the bar may
    fire during it.
    """
    d = cal(6)
    # entry fills at bar1 open=100, stop = 100 - 2*2.5 = 95
    px_o = [100, 100, 100, 100, 100, 100]
    px_h = [100, 100, 100, 130, 100, 100]   # bar3 spikes
    px_l = [100, 100, 100,  96, 100, 100]   # and dips, but stays above 95
    r = simulate_portfolio(d, {"X": frame(d, px_o, px_h, px_l, px_o)},
                           once("X", d[0]), NOCOST)
    # bar3's low of 96 is above the stop of 95 that was active before bar3.
    # If the trail had been raised from bar3's high of 130 first, the stop
    # would be 125 and the position would have exited on bar3.
    exits_on_bar3 = [t for t in r.closed_trades if t["exit_date"] == str(d[3])[:10]]
    assert not exits_on_bar3, "same-bar high must not create a same-bar stop hit"


def test_stop_raised_on_a_bar_becomes_active_the_next_bar():
    d = cal(6)
    # bar3 high 130 -> stop advances to 130-5 = 125. bar4 low 120 must hit it.
    px_o = [100, 100, 100, 100, 126, 126]
    px_h = [100, 100, 100, 130, 126, 126]
    px_l = [100, 100, 100, 100, 120, 126]
    r = simulate_portfolio(d, {"X": frame(d, px_o, px_h, px_l, px_o)},
                           once("X", d[0]), NOCOST)
    assert len(r.closed_trades) == 1
    t = r.closed_trades[0]
    assert t["exit_date"] == str(d[4])[:10]
    assert t["reason"] == "TRAIL"          # the stop had advanced
    assert t["stop_had_advanced"] is True
    assert t["raw_exit_price"] == pytest.approx(125.0)


# ── exit reason integrity ────────────────────────────────────────────────────
def test_stop_that_never_advanced_is_labelled_SL_not_TRAIL():
    """The original bug: "SL" was never assigned, so the reported SL rate was
    structurally 0.0%. A stop hit before the watermark ever moved is an SL."""
    d = cal(4)
    px = [100, 100, 90, 90]                # bar2 collapses straight through 95
    r = simulate_portfolio(d, {"X": frame(d, px)}, once("X", d[0]), NOCOST)
    assert len(r.closed_trades) == 1
    assert r.closed_trades[0]["reason"] == "SL"
    assert r.closed_trades[0]["stop_had_advanced"] is False


def test_gap_through_fills_at_the_open_not_at_the_stop():
    """Old code booked the exit at the stop price even when the bar gapped
    far below it. A stop order becomes a market order."""
    d = cal(4)
    o = [100, 100, 80, 80]                 # bar2 OPENS at 80, stop was 95
    r = simulate_portfolio(d, {"X": frame(d, o)}, once("X", d[0]), NOCOST)
    t = r.closed_trades[0]
    assert t["raw_exit_price"] == pytest.approx(80.0), "must fill at the gapped open"
    assert t["reason"] == "SL"


# ── capital constraints ──────────────────────────────────────────────────────
def test_overlapping_large_targets_cannot_breach_cash_or_gross_cap():
    d = cal(5)
    frames = {"A": frame(d, 100.0), "B": frame(d, 100.0)}

    def both(sym, date):
        if date == d[0]:
            return SignalDecision(1, 0.9 if sym == "A" else 0.8, "YÜKSEK", 0.70, 2.0, 2.5)
        return None

    r = simulate_portfolio(d, frames, both, NOCOST)
    assert (r.equity_curve["cash"] >= -1e-9).all(), "cash must never go negative"
    assert (r.equity_curve["gross_exposure_pct"] <= 1.0 + 1e-9).all(), \
        "gross exposure must never exceed the 100% cap"


def test_symbol_order_does_not_change_the_result():
    """All same-open candidates size from one shared equity snapshot, so
    WATCHLIST ordering cannot change sizing."""
    d = cal(5)
    frames = {"A": frame(d, 100.0), "B": frame(d, 100.0)}

    def both(sym, date):
        if date == d[0]:
            return SignalDecision(1, 0.9 if sym == "A" else 0.8, "YÜKSEK", 0.70, 2.0, 2.5)
        return None

    fwd = simulate_portfolio(d, frames, both, NOCOST, symbols=["A", "B"])
    rev = simulate_portfolio(d, frames, both, NOCOST, symbols=["B", "A"])
    assert fwd.final_equity == pytest.approx(rev.final_equity)
    assert sorted(f["symbol"] for f in fwd.fills) == sorted(f["symbol"] for f in rev.fills)


def test_unaffordable_order_is_rejected_not_forced_to_one_share():
    d = cal(4)
    cfg = SimulatorConfig(initial_cash=50.0, half_spread_bps=0.0, slippage_bps=0.0,
                          sec_fee_rate=0.0, finra_taf_per_share=0.0, cat_fee_per_share=0.0)
    r = simulate_portfolio(d, {"X": frame(d, 500.0)}, once("X", d[0]), cfg)
    assert not r.fills, "a $500 share cannot be bought with $50"
    assert any(x["reason"] in ("INSUFFICIENT_CAPACITY", "BELOW_ONE_SHARE")
               for x in r.rejections)
    assert r.final_equity == pytest.approx(50.0)


# ── timing ───────────────────────────────────────────────────────────────────
def test_signal_at_close_fills_at_the_next_open():
    d = cal(4)
    o = [100, 111, 111, 111]
    r = simulate_portfolio(d, {"X": frame(d, o)}, once("X", d[0]), NOCOST)
    assert r.fills[0]["date"] == str(d[1])[:10]
    assert r.fills[0]["raw"] == pytest.approx(111.0), "fills at the NEXT open"


def test_holding_limit_is_exactly_max_holding_sessions():
    d = cal(30)
    cfg = SimulatorConfig(max_holding_sessions=15, half_spread_bps=0.0, slippage_bps=0.0,
                          sec_fee_rate=0.0, finra_taf_per_share=0.0, cat_fee_per_share=0.0)
    r = simulate_portfolio(d, {"X": frame(d, 100.0)}, once("X", d[0]), cfg)
    t = r.closed_trades[0]
    assert t["reason"] == "TIME"
    assert t["bars_held"] == 15


# ── accounting identities ────────────────────────────────────────────────────
def test_equity_identity_holds_every_day():
    d = cal(12)
    c = [100, 102, 104, 103, 108, 110, 109, 112, 115, 113, 118, 120]
    r = simulate_portfolio(d, {"X": frame(d, c)}, once("X", d[0]), NOCOST)
    ec = r.equity_curve
    identity = ec["cash"] + ec["long_market_value"] - ec["short_market_value"]
    assert (identity - ec["equity"]).abs().max() < 1e-9


def test_execution_cost_is_charged_once_per_side():
    d = cal(4)
    cfg = SimulatorConfig(half_spread_bps=5.0, slippage_bps=5.0,
                          sec_fee_rate=0.0, finra_taf_per_share=0.0, cat_fee_per_share=0.0)
    r = simulate_portfolio(d, {"X": frame(d, 100.0)}, once("X", d[0], frac=0.30), cfg)
    entry = [f for f in r.fills if f["kind"] == "ENTRY"][0]
    assert entry["fill"] == pytest.approx(100.0 * 1.0010)   # +10bps, once
    assert adverse_price(100.0, 1, opening=False, cfg=cfg) == pytest.approx(99.90)


def test_end_of_data_liquidation_closes_everything():
    d = cal(8)
    r = simulate_portfolio(d, {"X": frame(d, 100.0)}, once("X", d[0]), NOCOST)
    assert r.closed_trades, "the open position must be liquidated at the last close"
    assert r.closed_trades[-1]["reason"] == "END_OF_DATA"


# ── fail-closed behaviour ────────────────────────────────────────────────────
def test_short_is_rejected_when_allow_short_is_false():
    d = cal(5)
    r = simulate_portfolio(d, {"X": frame(d, 100.0)},
                           once("X", d[0], side=-1), NOCOST)
    assert not r.fills
    assert any(x["reason"] == "SHORT_DISABLED" for x in r.rejections)


def test_missing_bar_for_a_held_position_fails_loudly():
    """Carrying a stale price forward would hide an unobservable stop and
    fabricate equity, so it must raise instead."""
    d = cal(6)
    df = frame(d, 100.0).drop(index=d[3])          # bar3 vanishes mid-hold
    with pytest.raises(RuntimeError, match="missing bar"):
        simulate_portfolio(d, {"X": df}, once("X", d[0]), NOCOST)


def test_gross_exposure_never_exceeds_configured_cap():
    d = cal(20)
    frames = {s: frame(d, 100.0) for s in "ABCDE"}

    def always(sym, date):
        return SignalDecision(1, 0.5, "ORTA", 0.40, 2.0, 2.5)

    r = simulate_portfolio(d, frames, always, NOCOST)
    assert (r.equity_curve["gross_exposure_pct"] <= 1.0 + 1e-9).all()
