"""Does C1's price basis actually divergence-shift the live ledger against the backtest?

docs/designs/rp-a-results.md records that true_backtest.veri_cek takes yfinance's
auto_adjust=True while c1_forward.py takes auto_adjust=False plus an explicit
dividend cash credit. The question that matters is not whether the two *price
series* differ — they plainly do — but whether the two C1 *implementations* land
in a different place. They do not, and this file holds that to a declared bound
so it cannot regress silently.

Both bases are read from committed artifacts, so these tests need no network.
"""

import pandas as pd
import pytest

import c1_forward as c1f
import rpa_screen as rpa
from portfolio_simulator import equity_metrics

PROGRAM_START = pd.Timestamp("2024-12-02")   # first month-first session inside the scored window

# Declared before the forward ledger's first row. Observed residual over 436
# sessions is +0.00pp; sub-period rates run 0.000 to 0.020 pp/yr. A divergence
# beyond this bound is a defect to investigate, not the price basis.
MAX_BASIS_DRIFT_PP_PER_YEAR = 0.10


def basis_b_frames(zero_dividends: bool = False) -> dict[str, pd.DataFrame]:
    """c1_forward's basis: split-adjusted prices plus corporate actions."""
    table = pd.read_csv(rpa.BASIS_B_BARS)
    frames = {}
    for symbol in c1f.C1_SYMBOLS:
        rows = table[table.symbol == symbol].sort_values("date", kind="stable")
        frames[symbol] = pd.DataFrame(
            {"Open": rows["open"].values, "Close": rows["close"].values,
             "Dividends": 0.0 if zero_dividends else rows["dividend"].values,
             "Stock Splits": rows["split"].values},
            index=pd.DatetimeIndex(pd.to_datetime(rows["date"].values)),
        )
    return frames


def basis_a_frames(bars) -> dict[str, pd.DataFrame]:
    """true_backtest's basis: auto-adjusted, so dividends are already in the price."""
    return {
        symbol: pd.DataFrame(
            {"Open": bars[symbol]["Open"].values, "Close": bars[symbol]["Close"].values,
             "Dividends": 0.0, "Stock Splits": 0.0},
            index=bars[symbol].index,
        )
        for symbol in c1f.C1_SYMBOLS
    }


@pytest.fixture(scope="module")
def bars():
    return rpa.load_bars()


@pytest.fixture(scope="module")
def calendar(bars):
    return rpa.build_window(bars).in_window


def ledger_equity(tmp_path, frames, calendar, name):
    path = tmp_path / f"{name}.jsonl"
    c1f.advance_ledger(path, PROGRAM_START, calendar, frames)
    records = c1f.read_records(path)
    equity = {r["session"]: r["equity"] for r in records if r["type"] == "EQUITY"}
    series = pd.Series(list(equity.values()),
                       index=pd.DatetimeIndex(pd.to_datetime(list(equity.keys()))))
    dividends = sum(r["cash_amount"] for r in records if r["type"] == "DIVIDEND")
    return series, dividends


@pytest.fixture(scope="module")
def ledgers(tmp_path_factory, bars, calendar):
    tmp = tmp_path_factory.mktemp("basis")
    a, div_a = ledger_equity(tmp, basis_a_frames(bars), calendar, "a")
    b, div_b = ledger_equity(tmp, basis_b_frames(), calendar, "b")
    return a, b, div_a, div_b


def test_the_live_code_on_the_backtest_basis_matches_the_rpa_engine(bars, calendar, ledgers):
    """Control: with the basis held fixed, the two implementations are one rule.

    Without this the basis comparison would be confounded by any semantic fork
    between c1_forward and the RP-A engine.
    """
    a, _, _, _ = ledgers
    sessions = [d for d in calendar if d >= PROGRAM_START]
    engine = rpa.run_engine(sessions, bars, rpa.c1_ew_monthly(sessions, calendar),
                            rpa.EngineConfig(side_cost_bps=10.0))
    assert (a - engine.equity).abs().max() < 1e-9


def test_the_dividend_credit_recovers_the_whole_price_return_shortfall(tmp_path, calendar, ledgers):
    """Dropping the credit is what costs 1.09pp; crediting it gives the pp back."""
    a, b, _, div_b = ledgers
    dropped, _ = ledger_equity(tmp_path, basis_b_frames(zero_dividends=True), calendar, "b0")
    shortfall = equity_metrics(dropped)["total_return_pct"] - equity_metrics(a)["total_return_pct"]
    recovered = equity_metrics(b)["total_return_pct"] - equity_metrics(a)["total_return_pct"]
    assert shortfall < -1.0, "the two price series really do differ by over a point"
    assert abs(recovered) < 0.02, "and the dividend credit gives essentially all of it back"
    assert div_b > 0.0


def test_basis_drift_stays_inside_the_declared_bound(ledgers):
    a, b, _, _ = ledgers
    years = (len(a) - 1) / 252.0
    drift = equity_metrics(b)["total_return_pct"] - equity_metrics(a)["total_return_pct"]
    assert abs(drift) / years <= MAX_BASIS_DRIFT_PP_PER_YEAR, (
        f"price-basis drift {drift / years:+.4f} pp/yr exceeds the bound declared "
        "before the forward ledger's first row; investigate a defect, not the basis"
    )


def test_the_drift_is_not_a_systematic_drag_against_the_live_ledger(ledgers):
    """The report claimed the backtest was optimistic; measured, it is not."""
    a, b, _, _ = ledgers
    assert b.iloc[-1] >= a.iloc[-1] - 1e-6, (
        "the live basis reads at or above the backtest basis on this window"
    )
    relative = abs(float(b.iloc[-1] - a.iloc[-1])) / float(a.iloc[-1])
    assert relative < 5e-4


def test_the_comparison_window_contains_no_split(ledgers):
    """Splits are an untested path for THIS comparison; say so rather than imply coverage."""
    table = pd.read_csv(rpa.BASIS_B_BARS)
    assert (table["split"] == 0).all()
