"""M2 harness: does it actually measure the basis gap, and refuse when it can't?

c1_basis_check compares the live forward ledger against a basis-A replication of
its own sessions. The live ledger is five sessions old, so the end-to-end proof
here runs the same code path over the committed historical bars, where the answer
is already known from docs/designs/c1-forward-basis-divergence.md.

No test in this file touches the network.
"""

import numpy as np
import pandas as pd
import pytest

import c1_basis_check as m2
import c1_forward as c1f
import rpa_screen as rpa

HISTORICAL_START = pd.Timestamp("2024-12-02")   # first month-first session in the scored window


def _sessions(count: int, start: str = "2026-09-01") -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.bdate_range(start, periods=count))


def _curve(count: int, total_return_pct: float, start: str = "2026-09-01") -> pd.Series:
    """A smooth curve landing on an exact total return, so drift is controllable."""
    end = 1500.0 * (1.0 + total_return_pct / 100.0)
    return pd.Series(np.linspace(1500.0, end, count), index=_sessions(count, start))


# ─────────────────────────────────────────────────────────────────────
# The bound itself
# ─────────────────────────────────────────────────────────────────────
def test_the_declared_bound_has_not_been_widened():
    """c1-forward-basis-divergence.md's remedy for a breach is never a looser bound."""
    assert m2.MAX_BASIS_DRIFT_PP_PER_YEAR == 0.10


def test_the_verdict_threshold_is_the_gate_session_count():
    """docs/designs/c1-forward-decision-gate.md falls on the 64th session."""
    assert m2.GATE_MIN_SESSIONS == 64


# ─────────────────────────────────────────────────────────────────────
# drift_report
# ─────────────────────────────────────────────────────────────────────
def test_a_verdict_is_withheld_below_the_gate_session_count():
    """Five sessions annualise a rounding error into a false breach."""
    live = _curve(5, 3.06)
    replica = _curve(5, 3.00)
    report = m2.drift_report(live, replica)
    assert report["verdict"] == m2.INSUFFICIENT
    assert report["sessions"] == 5
    # The rate would breach the bound; the point is that it is not called a breach.
    assert abs(report["drift_pp_per_year"]) > m2.MAX_BASIS_DRIFT_PP_PER_YEAR


def test_inside_the_bound_at_gate_length_passes():
    live = _curve(m2.GATE_MIN_SESSIONS, 8.020)
    replica = _curve(m2.GATE_MIN_SESSIONS, 8.000)
    report = m2.drift_report(live, replica)
    assert report["verdict"] == m2.PASS
    assert abs(report["drift_pp_per_year"]) <= m2.MAX_BASIS_DRIFT_PP_PER_YEAR


def test_outside_the_bound_at_gate_length_fails():
    live = _curve(m2.GATE_MIN_SESSIONS, 9.000)
    replica = _curve(m2.GATE_MIN_SESSIONS, 8.000)
    report = m2.drift_report(live, replica)
    assert report["verdict"] == m2.FAIL
    assert "NOT the explanation" in report["interpretation"]


def test_the_annualisation_is_the_one_the_document_declared():
    """rate = drift_pp * 252 / (n - 1), the same form as the historical test."""
    live = _curve(101, 5.500)
    replica = _curve(101, 5.000)
    report = m2.drift_report(live, replica)
    assert report["drift_pp"] == pytest.approx(0.5, abs=1e-6)
    assert report["drift_pp_per_year"] == pytest.approx(0.5 * 252 / 100, rel=1e-6)


def test_mismatched_sessions_refuse_to_compare():
    live = _curve(m2.GATE_MIN_SESSIONS, 5.0)
    replica = _curve(m2.GATE_MIN_SESSIONS, 5.0, start="2026-09-02")
    with pytest.raises(m2.BasisCheckError, match="like for like"):
        m2.drift_report(live, replica)


# ─────────────────────────────────────────────────────────────────────
# Reading a ledger
# ─────────────────────────────────────────────────────────────────────
def _equity_row(session: str, equity: float) -> dict:
    return {"type": "EQUITY", "session": session, "equity": equity}


def test_equity_series_rejects_a_duplicated_session():
    records = [_equity_row("2026-09-01", 1500.0), _equity_row("2026-09-01", 1501.0)]
    with pytest.raises(m2.BasisCheckError, match="duplicate"):
        m2.ledger_equity_series(records)


def test_equity_series_rejects_out_of_order_sessions():
    records = [_equity_row("2026-09-02", 1500.0), _equity_row("2026-09-01", 1501.0)]
    with pytest.raises(m2.BasisCheckError, match="session order"):
        m2.ledger_equity_series(records)


def test_a_ledger_without_a_program_row_is_not_guessed_at():
    with pytest.raises(m2.BasisCheckError, match="program_start"):
        m2.ledger_program_start([_equity_row("2026-09-01", 1500.0)])


def test_program_start_is_read_from_the_ledger_not_the_module():
    """A replay of an older ledger must not silently adopt today's PROGRAM_START."""
    records = [{"type": "PROGRAM", "program_start": "2024-12-02"},
               _equity_row("2024-12-02", 1500.0)]
    assert m2.ledger_program_start(records) == HISTORICAL_START


# ─────────────────────────────────────────────────────────────────────
# End to end, on the committed historical bars
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def historical():
    """A basis-B ledger and the sessions it covers, standing in for the live one."""
    bars = rpa.load_bars()
    calendar = [d for d in rpa.build_window(bars).in_window if d >= HISTORICAL_START]
    table = pd.read_csv(rpa.BASIS_B_BARS)
    frames = {}
    for symbol in c1f.C1_SYMBOLS:
        rows = table[table.symbol == symbol].sort_values("date", kind="stable")
        frames[symbol] = pd.DataFrame(
            {"Open": rows["open"].values, "Close": rows["close"].values,
             "Dividends": rows["dividend"].values, "Stock Splits": rows["split"].values},
            index=pd.DatetimeIndex(pd.to_datetime(rows["date"].values)),
        )
    return bars, calendar, frames


@pytest.fixture(scope="module")
def live_series(tmp_path_factory, historical):
    _, calendar, frames = historical
    path = tmp_path_factory.mktemp("m2") / "live.jsonl"
    c1f.advance_ledger(path, HISTORICAL_START, calendar, frames)
    return m2.ledger_equity_series(c1f.read_records(path))


def test_the_harness_reproduces_the_documented_result(historical, live_series):
    """The whole point: run the real path and land inside the declared bound."""
    bars, _, _ = historical
    replica = m2.replicate_on_basis_a(list(live_series.index), bars, HISTORICAL_START)
    report = m2.drift_report(live_series, replica)

    assert report["sessions"] >= m2.GATE_MIN_SESSIONS
    assert report["verdict"] == m2.PASS
    assert abs(report["drift_pp_per_year"]) <= m2.MAX_BASIS_DRIFT_PP_PER_YEAR
    # c1-forward-basis-divergence.md measured +0.000pp over this window.
    assert abs(report["drift_pp"]) < 0.02


def test_basis_a_frames_strip_the_actions_the_price_already_contains(historical):
    bars, _, _ = historical
    frames = m2.basis_a_frames(bars)
    assert set(frames) == set(c1f.C1_SYMBOLS)
    for frame in frames.values():
        assert (frame["Dividends"] == 0.0).all()
        assert (frame["Stock Splits"] == 0.0).all()


def test_a_missing_symbol_is_refused_rather_than_dropped(historical):
    bars, _, _ = historical
    partial = {s: f for s, f in bars.items() if s != "NVDA"}
    with pytest.raises(m2.BasisCheckError, match="NVDA"):
        m2.basis_a_frames(partial)
