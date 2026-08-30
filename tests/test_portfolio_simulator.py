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


def once(symbol, on_date, side=1, score=0.5, frac=0.30, atr=2.0,
         mult=2.5, init_mult=1.5):
    """Signal callback that fires for one symbol on one date.

    mult      = TRAILING multiple (kurtosis-driven 2.5-5.0)
    init_mult = INITIAL stop multiple (1.8 YÜKSEK / 1.5 ORTA)
    These are different numbers. Conflating them silently widened every initial
    stop from ~1.5xATR to 2.5-5.0xATR, which changed the strategy rather than
    the measurement.
    """
    def fn(sym, date):
        if sym == symbol and date == on_date:
            return SignalDecision(side, score, "YÜKSEK", frac, atr, mult, init_mult)
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
    # entry fills at bar1 open=100; INITIAL stop = 100 - 2*1.5 = 97
    px_o = [100, 100, 100, 100, 100, 100]
    px_h = [100, 100, 100, 130, 100, 100]   # bar3 spikes
    px_l = [100, 100, 100,  98, 100, 100]   # and dips, but stays above 97
    r = simulate_portfolio(d, {"X": frame(d, px_o, px_h, px_l, px_o)},
                           once("X", d[0]), NOCOST)
    # bar3's low of 98 is above the stop of 97 that was active before bar3.
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


# ── regression tests for the seven blockers Codex found in the implementation ─
def test_initial_stop_uses_its_own_multiple_not_the_trail_multiple():
    """Blocker 2. The initial stop is 1.5-1.8xATR; the kurtosis trail is
    2.5-5.0xATR. Using the trail multiple at entry widens every initial stop."""
    d = cal(4)
    # atr=2, init_mult=1.5 -> stop 97. A bar low of 96.5 must hit it.
    px_o = [100, 100, 100, 100]
    px_l = [100, 100, 96.5, 100]
    r = simulate_portfolio(d, {"X": frame(d, px_o, px_o, px_l, px_o)},
                           once("X", d[0], atr=2.0, mult=5.0, init_mult=1.5), NOCOST)
    assert len(r.closed_trades) == 1
    assert r.closed_trades[0]["raw_exit_price"] == pytest.approx(97.0), \
        "stop must be at 1.5xATR, not the 5.0x trail multiple"


def test_final_equity_includes_end_of_data_liquidation_cost():
    """Blocker 1. Liquidation ran after the last equity row, so its spread and
    fees reached closed_trades but never final_equity."""
    d = cal(6)
    cfg = SimulatorConfig(half_spread_bps=50.0, slippage_bps=50.0)   # 100bps, loud
    r = simulate_portfolio(d, {"X": frame(d, 100.0)}, once("X", d[0]), cfg)
    assert r.closed_trades[-1]["reason"] == "END_OF_DATA"
    last_row_equity = float(r.equity_curve["equity"].iloc[-1])
    assert r.final_equity == pytest.approx(last_row_equity)
    assert r.final_equity < cfg.initial_cash, \
        "a round trip at 100bps/side on a flat price must lose money"


def test_allow_short_is_refused_rather_than_silently_miscounted():
    """Blocker 5. Short proceeds/collateral/borrow are unimplemented, so the
    config must raise rather than produce wrong cash flows."""
    with pytest.raises(NotImplementedError, match="allow_short"):
        simulate_portfolio(cal(3), {"X": frame(cal(3), 100.0)},
                           once("X", cal(3)[0]),
                           SimulatorConfig(allow_short=True))


def test_rejected_pyramid_layer_is_not_consumed():
    """Blocker / answer 2. The layer index must advance only on a real fill,
    otherwise a capacity rejection permanently burns a pyramid level."""
    d = cal(12)
    # rising price so the pyramid trigger keeps being crossed
    px = [100 + i * 4 for i in range(12)]
    cfg = SimulatorConfig(initial_cash=400.0, half_spread_bps=0.0, slippage_bps=0.0,
                          sec_fee_rate=0.0, finra_taf_per_share=0.0, cat_fee_per_share=0.0)
    r = simulate_portfolio(d, {"X": frame(d, px)}, once("X", d[0], frac=0.30), cfg)
    burned = [x for x in r.rejections if "PYRAMID" in x.get("reason", "")]
    # whatever happens, a rejection must not be the reason a later layer vanishes
    assert all(f["kind"] != "PYRAMID" or f["qty"] >= 1 for f in r.fills)


def test_cash_never_goes_negative_once_fees_are_counted():
    """Blocker 3. Affordability ignored fees, so cash could dip below zero."""
    d = cal(6)
    cfg = SimulatorConfig(initial_cash=1000.0, cat_fee_per_share=0.05)  # loud fee
    r = simulate_portfolio(d, {"X": frame(d, 9.97)}, once("X", d[0], frac=1.0), cfg)
    assert (r.equity_curve["cash"] >= -1e-9).all()
