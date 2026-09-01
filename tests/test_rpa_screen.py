"""The five RP-A candidates and the invariants that make their numbers meaningful.

Integration tests run off the committed bar artifact, so they stay network-free.
The look-ahead probes are the important ones: they mutate a price at the decision
session and assert the signal does not move.
"""

import math

import pandas as pd
import pytest

import rpa_screen as rpa
from portfolio_simulator import equity_metrics


@pytest.fixture(scope="module")
def bars():
    return rpa.load_bars()


@pytest.fixture(scope="module")
def window(bars):
    return rpa.build_window(bars)


@pytest.fixture(scope="module")
def base(bars, window):
    """All five candidates at the 10 bps base case."""
    return rpa.build_candidates(bars, window, rpa.EngineConfig(side_cost_bps=rpa.BASE_COST_BPS))


def flat_frames(sessions, price=100.0):
    index = pd.DatetimeIndex(sessions)
    frame = pd.DataFrame({"Open": [price] * len(index), "High": [price] * len(index),
                          "Low": [price] * len(index), "Close": [price] * len(index)},
                         index=index)
    return {s: frame.copy() for s in (rpa.BENCHMARK, *rpa.SYMBOLS)}


# ─────────────────────────────────────────────────────────────────────────────
# Execution contract
# ─────────────────────────────────────────────────────────────────────────────
def test_a_close_decision_fills_at_the_next_open_not_the_same_close():
    sessions = list(pd.to_datetime(["2025-03-03", "2025-03-04", "2025-03-05"]))
    frames = flat_frames(sessions)
    for symbol in rpa.SYMBOLS:                       # the next open is 10% away
        frames[symbol].loc[sessions[1], "Open"] = 110.0
    result = rpa.run_engine(sessions, frames, rpa.buy_and_hold_rule(),
                            rpa.EngineConfig(side_cost_bps=0.0))
    assert result.traded_notional.iloc[0] == 0.0     # nothing fills on the decision close
    assert result.equity.iloc[0] == pytest.approx(rpa.INITIAL_CASH)
    # Bought at 110 and marked back at 100: the entry price is the next open.
    assert result.equity.iloc[1] == pytest.approx(rpa.INITIAL_CASH * 100.0 / 110.0)


def test_every_candidate_is_flat_on_the_first_scored_session(base):
    """A consequence of the frozen fill rule, stated so it is never mistaken for a bug."""
    for name, result in base.items():
        assert result.gross_pct.iloc[0] == 0.0, name
        assert result.equity.iloc[0] == pytest.approx(rpa.INITIAL_CASH), name


def test_flat_prices_cost_exactly_the_declared_entry_charge():
    sessions = list(pd.to_datetime([
        "2025-03-03", "2025-03-04", "2025-03-31", "2025-04-01", "2025-04-02",
    ]))
    frames = flat_frames(sessions)
    result = rpa.run_engine(sessions, frames,
                            rpa.c1_ew_monthly(sessions, sessions),
                            rpa.EngineConfig(side_cost_bps=10.0))
    rate = 10.0 / 10_000.0
    # One entry at 10 bps, then a monthly rebalance that has nothing to trade.
    assert result.equity.iloc[-1] == pytest.approx(rpa.INITIAL_CASH / (1.0 + rate), rel=1e-12)
    assert result.cost_paid.sum() == pytest.approx(
        rpa.INITIAL_CASH - rpa.INITIAL_CASH / (1.0 + rate), rel=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# Look-ahead probes
# ─────────────────────────────────────────────────────────────────────────────
def test_trend_filter_cannot_see_the_decision_session_close(bars, window):
    session = window.traded[200]
    tampered = {k: v.copy() for k, v in bars.items()}
    tampered[rpa.BENCHMARK].loc[session, "Close"] *= 0.5
    before = rpa.trend_exposure(bars, window.full).loc[session]
    after = rpa.trend_exposure(tampered, window.full).loc[session]
    assert before == after
    # ...but it does move the very next session, proving the probe bites.
    nxt = window.traded[201]
    assert rpa.trend_exposure(tampered, window.full).loc[nxt] != \
        rpa.trend_exposure(bars, window.full).loc[nxt]


def test_momentum_score_cannot_see_the_decision_session_close(bars, window):
    session = window.traded[200]
    tampered = {k: v.copy() for k, v in bars.items()}
    tampered[rpa.SYMBOLS[0]].loc[session, "Close"] *= 2.0
    before = rpa.momentum_scores(rpa.close_matrix(bars, window.full)).loc[session]
    after = rpa.momentum_scores(rpa.close_matrix(tampered, window.full)).loc[session]
    pd.testing.assert_series_equal(before, after)


def test_momentum_lookback_is_126_sessions_through_the_previous_close(bars, window):
    closes = rpa.close_matrix(bars, window.full)
    session = window.traded[100]
    position = list(closes.index).index(session)
    symbol = rpa.SYMBOLS[3]
    expected = (closes[symbol].iloc[position - 1]
                / closes[symbol].iloc[position - 1 - rpa.XMOM_LOOKBACK]) - 1.0
    assert rpa.momentum_scores(closes).loc[session, symbol] == pytest.approx(expected, rel=1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# C1
# ─────────────────────────────────────────────────────────────────────────────
def test_c1_rebalances_on_the_anchor_and_each_month_first_session(bars, window):
    flags = rpa._rebalance_days(window.traded, window.full)
    assert flags[0] is True
    months = {(d.year, d.month) for d in window.traded}
    # One decision per month. The anchor month contributes the forced anchor entry
    # instead of its month-first session, which fell before the scored window
    # opened, so the count lands on the number of months rather than one above it.
    assert sum(flags) == len(months)
    assert window.anchor.month == window.traded[0].month
    for session, flag in zip(window.traded, flags):
        if flag and session != window.anchor:
            position = window.full.index(session)
            assert window.full[position - 1].month != session.month


def test_c1_holds_all_seventeen_and_stays_fully_invested(base):
    c1 = base["C1_EW_MONTHLY"]
    invested = c1.gross_pct.iloc[1:]
    assert (invested > 0.999).all()
    assert (c1.weights.iloc[1:] > 0).all().all()


def test_a_rebalance_produces_equal_value_weights_at_the_open(bars, base):
    """Reconstruct the post-fill book exactly rather than bounding its spread.

    The fill is at the open and the mark is at the close, so equal weight at the
    open shows up at the close scaled by each symbol's intraday move. That is an
    identity, not a tolerance, so it is asserted as one.
    """
    c1 = base["C1_EW_MONTHLY"]
    fills = list(c1.traded_notional[c1.traded_notional > 0].index)
    assert len(fills) >= 20
    for session in fills:
        ratios = pd.Series({
            s: float(bars[s].loc[session, "Close"]) / float(bars[s].loc[session, "Open"])
            for s in rpa.SYMBOLS
        })
        expected = ratios / ratios.sum()
        assert (c1.sleeve_weights.loc[session] - expected).abs().max() < 1e-9, session


# ─────────────────────────────────────────────────────────────────────────────
# C3
# ─────────────────────────────────────────────────────────────────────────────
def test_c3_holds_exactly_five_names(base):
    c3 = base["C3_XMOM_TOP5"]
    held = (c3.weights > 1e-12).sum(axis=1).iloc[1:]
    assert set(held.unique()) == {rpa.XMOM_TOP_N}


def test_c3_picks_the_five_highest_momentum_names(bars, window, base):
    closes = rpa.close_matrix(bars, window.full)
    scores = rpa.momentum_scores(closes)
    c3 = base["C3_XMOM_TOP5"]
    flags = rpa._rebalance_days(window.traded, window.full)
    checked = 0
    for index, session in enumerate(window.traded[:-1]):
        if not flags[index]:
            continue
        want = set(rpa.top_momentum_names(scores.loc[session]))
        fill = window.traded[index + 1]
        got = set(c3.weights.columns[c3.weights.loc[fill] > 1e-12])
        assert got == want, session
        checked += 1
    assert checked >= 20


def test_c3_ranking_is_an_independent_recomputation(bars, window):
    """Rank by hand from the committed bars, not through momentum_scores."""
    session = window.traded[150]
    position = window.full.index(session)
    prev, back = window.full[position - 1], window.full[position - 1 - rpa.XMOM_LOOKBACK]
    by_hand = sorted(
        rpa.SYMBOLS,
        key=lambda s: -(float(bars[s].loc[prev, "Close"]) / float(bars[s].loc[back, "Close"])),
    )[:rpa.XMOM_TOP_N]
    closes = rpa.close_matrix(bars, window.full)
    assert set(rpa.top_momentum_names(rpa.momentum_scores(closes).loc[session])) == set(by_hand)


# ─────────────────────────────────────────────────────────────────────────────
# C4
# ─────────────────────────────────────────────────────────────────────────────
def test_c4_target_gross_is_the_volatility_scale(bars, window):
    closes = rpa.close_matrix(bars, window.full)
    scale = rpa.volatility_scale(closes)
    rule = rpa.c4_voltgt_ew(window.traded, window.full, scale)
    flags = rpa._rebalance_days(window.traded, window.full)
    for index, session in enumerate(window.traded):
        target = rule(index, session, None)
        if flags[index]:
            assert sum(target.values()) == pytest.approx(float(scale.loc[session]), rel=1e-12)
        else:
            assert target is None


def test_c4_scale_is_capped_at_one_and_never_levers_up(bars, window, base):
    closes = rpa.close_matrix(bars, window.full)
    assert rpa.volatility_scale(closes).loc[window.traded].max() <= 1.0
    c4, c1 = base["C4_VOLTGT_EW"], base["C1_EW_MONTHLY"]
    assert (c4.gross_pct <= c1.gross_pct + 1e-9).all()


def test_c4_volatility_is_the_annualised_sixty_session_basket_vol(bars, window):
    closes = rpa.close_matrix(bars, window.full)
    session = window.traded[50]
    position = list(closes.index).index(session)
    returns = rpa.basket_returns(closes).iloc[position - rpa.VOL_LOOKBACK + 1: position + 1]
    assert len(returns) == rpa.VOL_LOOKBACK
    expected = min(1.0, rpa.VOL_TARGET / (returns.std(ddof=1) * math.sqrt(rpa.TRADING_DAYS)))
    assert rpa.volatility_scale(closes).loc[session] == pytest.approx(expected, rel=1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Overlays: C2 on C1, C5 on C3
# ─────────────────────────────────────────────────────────────────────────────
OVERLAY_PAIRS = [("C2_TREND_SPY200", "C1_EW_MONTHLY"),
                 ("C5_TREND_XMOM", "C3_XMOM_TOP5")]


def _sync_flags(over, under):
    """Sessions on which the overlay's book was established alongside the base's."""
    under_fills = set(under.traded_notional[under.traded_notional > 0].index)
    over_fills = set(over.traded_notional[over.traded_notional > 0].index)
    synced, out = False, {}
    for session in over.equity.index:
        if over.gross_pct.loc[session] <= 1e-9:
            synced = False                       # in cash, nothing to match
        elif session in under_fills and session in over_fills:
            synced = True                        # both rebalanced into the same book
        elif session in over_fills:
            synced = False                       # re-entered mid-month on lagged weights
        out[session] = synced
    return pd.Series(out)


@pytest.mark.parametrize("overlay,underlying", OVERLAY_PAIRS)
def test_overlay_holds_the_underlying_weights_exactly_when_synced(base, overlay, underlying):
    """"C1 weights" and "C3 holdings" must mean the base's book, not a fresh one."""
    over, under = base[overlay], base[underlying]
    synced = _sync_flags(over, under)
    diff = (over.sleeve_weights[synced] - under.sleeve_weights[synced]).abs().max().max()
    assert diff < 1e-9, f"{overlay} drifted from {underlying} by {diff:.2e}"
    assert synced.sum() > 300


@pytest.mark.parametrize("overlay,underlying", OVERLAY_PAIRS)
def test_overlay_drift_is_only_the_re_entry_lag_and_resets_each_month(base, overlay, underlying):
    """The one place the overlay cannot match the base, and its bound.

    The frozen rule decides at a close and fills at the next open, so an overlay
    re-entering mid-month buys the base's previous-close proportions at the next
    open and inherits one session of dispersion. It is bounded, it is confined to
    sessions the sync flag already excludes, and the next monthly rebalance puts
    both books back on equal weight.
    """
    over, under = base[overlay], base[underlying]
    live = over.gross_pct > 1e-9
    synced = _sync_flags(over, under)
    drift = (over.sleeve_weights[live] - under.sleeve_weights[live]).abs().max(axis=1).dropna()
    assert drift.max() < 1e-2
    assert (drift[drift > 1e-9].index.isin(synced[~synced].index)).all()
    # The very first entry is shared with the base, so it carries no lag at all.
    first_entry = over.traded_notional[over.traded_notional > 0].index[0]
    assert drift.loc[first_entry] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("overlay", ["C2_TREND_SPY200", "C5_TREND_XMOM"])
def test_overlay_is_in_cash_exactly_when_the_filter_was_off_the_session_before(
        bars, window, base, overlay):
    exposure = rpa.trend_exposure(bars, window.full)
    gross = base[overlay].gross_pct
    for index in range(len(window.traded) - 1):
        was_off = exposure.loc[window.traded[index]] == 0.0
        held = gross.iloc[index + 1]
        if was_off:
            assert held == pytest.approx(0.0, abs=1e-12), window.traded[index + 1]
        else:
            assert held > 0.9, window.traded[index + 1]


@pytest.mark.parametrize("overlay", ["C2_TREND_SPY200", "C5_TREND_XMOM"])
def test_overlay_trades_only_on_a_rebalance_or_a_filter_flip(bars, window, base, overlay):
    exposure = [float(rpa.trend_exposure(bars, window.full).loc[d]) for d in window.traded]
    flags = rpa._rebalance_days(window.traded, window.full)
    allowed = {window.traded[i + 1] for i in range(len(window.traded) - 1)
               if flags[i] or (i > 0 and exposure[i] != exposure[i - 1])}
    allowed.add(window.traded[1])
    traded = set(base[overlay].traded_notional[base[overlay].traded_notional > 0].index)
    assert traded <= allowed


def test_trend_filter_actually_switches_off_during_the_window(bars, window):
    """A filter that never fires would make C2 a relabelled C1."""
    exposure = rpa.trend_exposure(bars, window.full).loc[window.traded]
    assert 0 < (exposure == 0.0).sum() < len(exposure)


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio-level invariants across the whole cost grid
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bps", rpa.COST_GRID_BPS)
def test_no_candidate_shorts_levers_or_runs_out_of_cash(bars, window, bps):
    results = rpa.build_candidates(bars, window, rpa.EngineConfig(side_cost_bps=bps))
    assert set(results) == set(rpa.CANDIDATE_NAMES)
    for name, result in results.items():
        assert len(result.equity) == 441, name
        assert (result.gross_pct <= rpa.MAX_GROSS_EXPOSURE + 1e-9).all(), name
        assert (result.cash >= -1e-9).all(), name
        assert (result.weights >= -1e-12).all().all(), name
        assert result.equity.notna().all(), name


def test_higher_costs_never_help_a_candidate(bars, window):
    cheap = rpa.build_candidates(bars, window, rpa.EngineConfig(side_cost_bps=0.0))
    dear = rpa.build_candidates(bars, window, rpa.EngineConfig(side_cost_bps=20.0))
    for name in rpa.CANDIDATE_NAMES:
        assert equity_metrics(dear[name].equity)["total_return_pct"] <= \
            equity_metrics(cheap[name].equity)["total_return_pct"] + 1e-9, name


# ─────────────────────────────────────────────────────────────────────────────
# The four PASS conditions
# ─────────────────────────────────────────────────────────────────────────────
def synthetic_result(equity: pd.Series) -> rpa.EngineResult:
    """evaluate_conditions reads only the equity curve; the rest is scaffolding."""
    empty = pd.Series(0.0, index=equity.index)
    return rpa.EngineResult(
        equity=equity, cash=empty, gross_pct=empty,
        weights=pd.DataFrame(0.0, index=equity.index, columns=list(rpa.SYMBOLS)),
        cost_paid=empty, traded_notional=empty,
    )


def curve_from(returns, index):
    return pd.Series((1.0 + pd.Series(returns, index=index[1:])).cumprod().tolist(),
                     index=index[1:]).reindex(index).fillna(1.0).sort_index() * rpa.INITIAL_CASH


@pytest.fixture
def synthetic_family():
    import numpy as np
    index = pd.bdate_range("2024-11-22", periods=441)
    rng = np.random.default_rng(4)
    base = rng.normal(0.0004, 0.011, len(index) - 1)

    def family(**overrides):
        results = {"C1_EW_MONTHLY": synthetic_result(curve_from(base, index))}
        for name in rpa.CANDIDATE_NAMES[1:]:
            results[name] = synthetic_result(curve_from(overrides.get(name, base), index))
        return results

    return family, base, index


def test_a_candidate_identical_to_c1_fails_conditions_one_and_two(synthetic_family):
    family, _, _ = synthetic_family
    out = rpa.evaluate_conditions(family())
    for name, row in out["candidates"].items():
        conditions = row["conditions"]
        assert not conditions["1_sharpe_beats_c1"]["passed"], name
        assert not conditions["2_positive_active_return"]["passed"], name
        assert conditions["2_positive_active_return"]["value"] == pytest.approx(0.0, abs=1e-9)
        # An identical curve has an identical drawdown, so condition 4 must pass.
        assert conditions["4_drawdown_within_1_25x"]["passed"], name
        assert not row["passed"], name


def test_a_genuinely_better_candidate_clears_all_four(synthetic_family):
    family, base, _ = synthetic_family
    out = rpa.evaluate_conditions(family(C2_TREND_SPY200=base + 0.0015))
    row = out["candidates"]["C2_TREND_SPY200"]
    assert row["conditions"]["1_sharpe_beats_c1"]["passed"]
    assert row["conditions"]["2_positive_active_return"]["passed"]
    assert row["conditions"]["3_holm_adjusted_hac_test"]["passed"]
    assert row["conditions"]["4_drawdown_within_1_25x"]["passed"]
    assert row["passed"]
    # Holm still charges it for the family it was tested in.
    assert row["holm"]["family_size"] == 4
    assert row["holm"]["p_holm"] >= row["holm"]["p_raw"]


def test_the_active_return_condition_follows_its_sign(synthetic_family):
    family, base, _ = synthetic_family
    worse = rpa.evaluate_conditions(family(C3_XMOM_TOP5=base - 0.0015))
    row = worse["candidates"]["C3_XMOM_TOP5"]["conditions"]["2_positive_active_return"]
    assert row["value"] < 0 and not row["passed"]


def test_drawdown_condition_is_a_magnitude_comparison_at_the_boundary():
    """A sign slip here would silently invert condition 4, so pin both sides."""
    index = pd.bdate_range("2024-11-22", periods=40)
    def curve(trough):
        values = [1500.0, 1500.0, trough] + [trough] * (len(index) - 3)
        return pd.Series(values, index=index)
    c1 = curve(1200.0)        # -20%
    inside = curve(1125.0)    # -25%, exactly 1.25x C1
    outside = curve(1100.0)   # -26.67%, outside the bound
    results = {"C1_EW_MONTHLY": synthetic_result(c1)}
    for name, curve in zip(rpa.CANDIDATE_NAMES[1:], [inside, outside, inside, outside]):
        results[name] = synthetic_result(curve)
    out = rpa.evaluate_conditions(results)["candidates"]
    assert out["C2_TREND_SPY200"]["conditions"]["4_drawdown_within_1_25x"]["passed"]
    assert not out["C3_XMOM_TOP5"]["conditions"]["4_drawdown_within_1_25x"]["passed"]
    assert out["C2_TREND_SPY200"]["conditions"]["4_drawdown_within_1_25x"]["reference"] == \
        pytest.approx(25.0)


def test_every_reported_metric_recomputes_from_the_committed_equity_artifact():
    """No number in the report may exist only inside the process that made it."""
    import json
    report = json.loads((rpa.RESULTS_ARTIFACT).read_text())
    curves = pd.read_csv(rpa.EQUITY_ARTIFACT)
    curves["date"] = pd.to_datetime(curves["date"])
    for bps in rpa.COST_GRID_BPS:
        for name in rpa.CANDIDATE_NAMES:
            series = curves[(curves.cost_bps == bps) & (curves.candidate == name)] \
                .set_index("date")["equity"].sort_index()
            assert len(series) == 441
            assert equity_metrics(series) == report["cost_grid"][f"{bps:.0f}"]["metrics"][name]


def test_the_gate_result_in_the_report_is_a_pass_or_nothing_is_reportable():
    import json
    report = json.loads((rpa.RESULTS_ARTIFACT).read_text())
    gate = report["gate"]
    assert gate["passed"] is True
    assert gate["total_return_gap_pp"] <= gate["tolerance_total_return_pp"]
    assert gate["sharpe_gap"] <= gate["tolerance_sharpe"]
