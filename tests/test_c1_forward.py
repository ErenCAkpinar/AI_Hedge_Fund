"""Network-free tests for the frozen C1 forward-paper path."""

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from c1_forward import (
    C1ForwardError,
    C1_SYMBOLS,
    INITIAL_CASH,
    advance_ledger,
    download_completed_bars,
    read_records,
)
from portfolio_simulator import equity_metrics


def sessions():
    return list(pd.to_datetime([
        "2026-01-02", "2026-01-05", "2026-01-30",
        "2026-02-02", "2026-02-03", "2026-02-04",
    ]))


def frames(calendar=None, open_price=100.0, close_price=100.0):
    calendar = calendar or sessions()
    return {
        symbol: pd.DataFrame(
            {"Open": [float(open_price)] * len(calendar),
             "Close": [float(close_price)] * len(calendar),
             "Dividends": [0.0] * len(calendar),
             "Stock Splits": [0.0] * len(calendar)},
            index=pd.DatetimeIndex(calendar),
        )
        for symbol in C1_SYMBOLS
    }


def by_type(records, record_type):
    return [record for record in records if record["type"] == record_type]


def split_snapshot(calendar, nvda_price, split_events=(), dividend=None):
    market = frames(calendar)
    market["NVDA"].loc[:, ["Open", "Close"]] = float(nvda_price)
    for ex_date, ratio in split_events:
        if ex_date in calendar:
            market["NVDA"].loc[ex_date, "Stock Splits"] = float(ratio)
    if dividend is not None:
        ex_date, per_share = dividend
        if ex_date in calendar:
            market["NVDA"].loc[ex_date, "Dividends"] = float(per_share)
    return market


def mixed_sessions():
    """Two January sessions then six February ones.

    The prior run stops inside January, so the catch-up window opens on the
    first February session and carries a real monthly decision plus its
    next-open fill, both landing before either split ex-date.
    """
    return list(pd.to_datetime([
        "2026-01-29", "2026-01-30",
        "2026-02-02", "2026-02-03", "2026-02-04",
        "2026-02-05", "2026-02-06", "2026-02-09",
    ]))


def drifting_snapshot(prefix, nvda_price, split_events=()):
    """split_snapshot plus a distinct, split-free path for every other symbol.

    Only NVDA splits here, so every other symbol carries the same price on a
    session whichever run observes it.  Spreading them apart is what gives the
    February rebalance real work: with one flat price the book is already on
    target at the next open and no fill is produced at all.
    """
    market = split_snapshot(prefix, nvda_price, split_events)
    calendar = mixed_sessions()
    for symbol_index, symbol in enumerate(C1_SYMBOLS):
        if symbol == "NVDA":
            continue
        for session in prefix:
            price = 100.0 + symbol_index * 4.0 + calendar.index(session) * 2.0
            market[symbol].loc[session, ["Open", "Close"]] = price
    return market


def final_positions(records):
    positions = {symbol: 0.0 for symbol in C1_SYMBOLS}
    for record in records:
        if record["type"] == "SPLIT":
            positions[record["symbol"]] = record["shares_after"]
        elif record["type"] == "FILL":
            direction = 1.0 if record["side"] == "BUY" else -1.0
            positions[record["symbol"]] += direction * record["qty"]
    return positions


def assert_forward_paths_match(left_ledger, right_ledger):
    left_records = read_records(left_ledger)
    right_records = read_records(right_ledger)
    left_equity = [record["equity"] for record in by_type(left_records, "EQUITY")]
    right_equity = [record["equity"] for record in by_type(right_records, "EQUITY")]
    assert left_equity == pytest.approx(right_equity, rel=0.0, abs=1e-9)
    left_positions = final_positions(left_records)
    right_positions = final_positions(right_records)
    for symbol in C1_SYMBOLS:
        assert left_positions[symbol] == pytest.approx(
            right_positions[symbol], rel=0.0, abs=1e-9
        )


def test_first_session_close_decision_fills_only_at_next_open(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    market[C1_SYMBOLS[0]].loc[sessions()[1], "Open"] = 123.0

    advance_ledger(ledger, "2026-01-02", sessions(), market)
    records = read_records(ledger)
    decisions = by_type(records, "DECISION")
    fills = by_type(records, "FILL")

    assert [event["session"] for event in decisions] == ["2026-01-02", "2026-02-02"]
    assert len([event for event in fills if event["session"] == "2026-01-02"]) == 0
    assert len([event for event in fills if event["session"] == "2026-01-05"]) == 17
    nvda = next(record for record in fills if record["symbol"] == C1_SYMBOLS[0])
    assert nvda["raw_price"] == pytest.approx(123.0)


def test_initial_fill_is_fractional_equal_weight_and_respects_cash_cap(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    advance_ledger(ledger, "2026-01-02", sessions(), frames())
    records = read_records(ledger)
    initial_fills = [
        record for record in by_type(records, "FILL") if record["session"] == "2026-01-05"
    ]
    execution = next(
        record for record in by_type(records, "EXECUTION")
        if record["session"] == "2026-01-05"
    )

    notionals = [record["qty"] * record["raw_price"] for record in initial_fills]
    assert len(initial_fills) == 17
    assert all(record["fractional"] is True for record in initial_fills)
    assert any(not float(record["qty"]).is_integer() for record in initial_fills)
    assert max(notionals) - min(notionals) < 1e-9
    assert execution["cash_after"] >= -1e-9
    assert execution["gross_exposure_pct_after"] <= 1.0 + 1e-9


def test_flat_prices_lose_only_the_declared_initial_execution_cost(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    result = advance_ledger(ledger, "2026-01-02", sessions(), frames())
    records = read_records(ledger)
    executions = by_type(records, "EXECUTION")
    first_cost = executions[0]["execution_cost"]
    later_cost = executions[1]["execution_cost"]

    assert first_cost == pytest.approx(INITIAL_CASH - result["last_equity"], abs=1e-8)
    assert later_cost == pytest.approx(0.0, abs=1e-8)
    assert result["last_equity"] < INITIAL_CASH


def test_ex_date_dividend_is_credited_to_cash_and_daily_equity(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    ex_date = sessions()[2]
    market["WMT"].loc[ex_date, "Dividends"] = 1.25

    advance_ledger(ledger, "2026-01-02", sessions(), market)
    records = read_records(ledger)
    dividends = by_type(records, "DIVIDEND")
    marks = {record["session"]: record for record in by_type(records, "EQUITY")}
    wmt_entry = next(
        record for record in by_type(records, "FILL")
        if record["session"] == "2026-01-05" and record["symbol"] == "WMT"
    )

    assert len(dividends) == 1
    dividend = dividends[0]
    assert dividend["session"] == "2026-01-30"
    assert dividend["symbol"] == "WMT"
    assert dividend["shares_eligible"] == pytest.approx(wmt_entry["qty"])
    assert dividend["cash_amount"] == pytest.approx(wmt_entry["qty"] * 1.25)
    assert marks["2026-01-30"]["equity"] - marks["2026-01-05"]["equity"] \
        == pytest.approx(dividend["cash_amount"])


def test_dividend_is_credited_before_same_open_rebalance(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    ex_date = sessions()[4]
    market["WMT"].loc[ex_date, "Dividends"] = 2.0

    advance_ledger(ledger, "2026-01-02", sessions(), market)
    records = read_records(ledger)
    dividend_index = next(
        index for index, record in enumerate(records)
        if record["type"] == "DIVIDEND" and record["session"] == "2026-02-03"
    )
    first_fill_index = next(
        index for index, record in enumerate(records)
        if record["type"] == "FILL" and record["session"] == "2026-02-03"
    )

    assert dividend_index < first_fill_index


def test_ex_date_open_purchase_does_not_receive_that_dividend(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    market["WMT"].loc[sessions()[1], "Dividends"] = 1.0

    advance_ledger(ledger, "2026-01-02", sessions(), market)
    assert not by_type(read_records(ledger), "DIVIDEND")


def test_fresh_catchup_matches_daily_path_across_ten_for_one_split(tmp_path):
    daily = tmp_path / "daily.jsonl"
    catchup = tmp_path / "catchup.jsonl"
    split_session = sessions()[4]

    advance_ledger(
        daily,
        sessions()[0],
        sessions()[:4],
        split_snapshot(sessions()[:4], 1_000.0),
    )
    current_snapshot = split_snapshot(
        sessions(), 100.0, [(split_session, 10.0)]
    )
    advance_ledger(daily, sessions()[0], sessions(), current_snapshot)
    advance_ledger(catchup, sessions()[0], sessions(), current_snapshot)

    assert_forward_paths_match(daily, catchup)
    daily_split = by_type(read_records(daily), "SPLIT")[0]
    catchup_split = by_type(read_records(catchup), "SPLIT")[0]
    assert daily_split["split_factor"] == 10.0
    assert daily_split["events"] == [{
        "ex_date": split_session.strftime("%Y-%m-%d"), "ratio": 10.0,
    }]
    assert daily_split["last_recorded_session"] == "2026-02-02"
    assert catchup_split["last_recorded_session"] is None
    assert catchup_split["shares_before"] == catchup_split["shares_after"] == 0.0
    program = by_type(read_records(catchup), "PROGRAM")[0]
    assert program["split_policy"] == "RUN_BOUNDARY_BASIS_TRANSITION"
    assert program["split_timing"] == "BEFORE_NEW_SESSIONS"


def test_fresh_catchup_matches_daily_path_across_one_for_ten_reverse_split(tmp_path):
    daily = tmp_path / "daily.jsonl"
    catchup = tmp_path / "catchup.jsonl"
    split_session = sessions()[2]

    advance_ledger(
        daily,
        sessions()[0],
        sessions()[:2],
        split_snapshot(sessions()[:2], 10.0),
    )
    current_snapshot = split_snapshot(
        sessions(), 100.0, [(split_session, 0.1)]
    )
    advance_ledger(daily, sessions()[0], sessions(), current_snapshot)
    advance_ledger(catchup, sessions()[0], sessions(), current_snapshot)

    assert_forward_paths_match(daily, catchup)


def test_mixed_catchup_matches_daily_path_across_two_split_boundaries(tmp_path):
    calendar = mixed_sessions()
    first_split = calendar[4]
    second_split = calendar[6]
    rebalance_open = calendar[3]
    daily = tmp_path / "daily.jsonl"
    catchup = tmp_path / "catchup.jsonl"

    for end in range(1, len(calendar) + 1):
        prefix = calendar[:end]
        if end <= 4:
            snapshot = drifting_snapshot(prefix, 500.0)
        elif end <= 6:
            snapshot = drifting_snapshot(prefix, 50.0, [(first_split, 10.0)])
        else:
            snapshot = drifting_snapshot(
                prefix,
                100.0,
                [(first_split, 10.0), (second_split, 0.5)],
            )
        advance_ledger(daily, calendar[0], prefix, snapshot)

    advance_ledger(
        catchup,
        calendar[0],
        calendar[:2],
        drifting_snapshot(calendar[:2], 500.0),
    )
    before_catchup = len(read_records(catchup))
    advance_ledger(
        catchup,
        calendar[0],
        calendar,
        drifting_snapshot(
            calendar,
            100.0,
            [(first_split, 10.0), (second_split, 0.5)],
        ),
    )

    assert_forward_paths_match(daily, catchup)
    catchup_split = by_type(read_records(catchup), "SPLIT")[0]
    assert catchup_split["session"] == calendar[2].strftime("%Y-%m-%d")
    assert catchup_split["last_recorded_session"] == calendar[1].strftime("%Y-%m-%d")
    assert catchup_split["split_factor"] == 5.0
    assert catchup_split["events"] == [
        {"ex_date": first_split.strftime("%Y-%m-%d"), "ratio": 10.0},
        {"ex_date": second_split.strftime("%Y-%m-%d"), "ratio": 0.5},
    ]

    # The catch-up leg must actually trade inside the window, at a session
    # strictly after the basis transition and strictly before either ex-date.
    # Without this the two paths would only ever be compared while holding.
    catchup_leg = read_records(catchup)[before_catchup:]
    fill_session = rebalance_open.strftime("%Y-%m-%d")
    catchup_fills = [
        record for record in by_type(catchup_leg, "FILL")
        if record["session"] == fill_session
    ]
    daily_fills = [
        record for record in by_type(read_records(daily), "FILL")
        if record["session"] == fill_session
    ]

    assert calendar[2] < rebalance_open < first_split < second_split
    assert [record["session"] for record in by_type(catchup_leg, "DECISION")] == [
        calendar[2].strftime("%Y-%m-%d")
    ]
    assert len(catchup_fills) == len(C1_SYMBOLS)
    assert {record["side"] for record in catchup_fills} == {"BUY", "SELL"}
    assert len(daily_fills) == len(catchup_fills)


def test_catchup_split_and_same_day_dividend_match_daily_path(tmp_path):
    daily = tmp_path / "daily.jsonl"
    catchup = tmp_path / "catchup.jsonl"
    ex_date = sessions()[4]

    advance_ledger(
        daily,
        sessions()[0],
        sessions()[:4],
        split_snapshot(sessions()[:4], 1_000.0),
    )
    current_snapshot = split_snapshot(
        sessions(),
        100.0,
        [(ex_date, 10.0)],
        dividend=(ex_date, 2.0),
    )
    advance_ledger(daily, sessions()[0], sessions(), current_snapshot)
    advance_ledger(catchup, sessions()[0], sessions(), current_snapshot)

    assert_forward_paths_match(daily, catchup)
    daily_dividend = by_type(read_records(daily), "DIVIDEND")[0]
    catchup_records = read_records(catchup)
    catchup_split = by_type(catchup_records, "SPLIT")[0]
    catchup_dividend = by_type(catchup_records, "DIVIDEND")[0]
    assert catchup_records.index(catchup_split) < catchup_records.index(catchup_dividend)
    assert daily_dividend["cash_amount"] == pytest.approx(
        catchup_dividend["cash_amount"], rel=0.0, abs=1e-9
    )


def test_catchup_dividend_before_a_later_split_matches_daily_path(tmp_path):
    """Dividend ex-date strictly before a split ex-date, both in one window.

    The daily run credits the dividend on the pre-split basis, before the split
    is even declared.  The catch-up run rebases shares at the boundary and then
    sees the same historical dividend restated on the current basis.  Both must
    credit identical cash from different per-share amounts; equating those
    amounts instead would multiply the catch-up credit by the split factor.
    """
    daily = tmp_path / "daily.jsonl"
    catchup = tmp_path / "catchup.jsonl"
    dividend_session = sessions()[4]
    split_session = sessions()[5]
    assert dividend_session < split_session

    opening = split_snapshot(sessions()[:4], 1_000.0)
    advance_ledger(daily, sessions()[0], sessions()[:4], opening)
    advance_ledger(catchup, sessions()[0], sessions()[:4], opening)

    advance_ledger(
        daily,
        sessions()[0],
        sessions()[:5],
        split_snapshot(sessions()[:5], 1_000.0, dividend=(dividend_session, 20.0)),
    )
    current_snapshot = split_snapshot(
        sessions(),
        100.0,
        [(split_session, 10.0)],
        dividend=(dividend_session, 2.0),
    )
    advance_ledger(daily, sessions()[0], sessions(), current_snapshot)
    advance_ledger(catchup, sessions()[0], sessions(), current_snapshot)

    assert_forward_paths_match(daily, catchup)
    daily_dividend = by_type(read_records(daily), "DIVIDEND")[0]
    catchup_records = read_records(catchup)
    catchup_dividend = by_type(catchup_records, "DIVIDEND")[0]
    catchup_split = by_type(catchup_records, "SPLIT")[0]

    assert daily_dividend["dividend_per_share"] == pytest.approx(20.0)
    assert catchup_dividend["dividend_per_share"] == pytest.approx(2.0)
    assert catchup_dividend["shares_eligible"] == pytest.approx(
        daily_dividend["shares_eligible"] * 10.0, rel=0.0, abs=1e-9
    )
    assert daily_dividend["cash_amount"] == pytest.approx(
        catchup_dividend["cash_amount"], rel=0.0, abs=1e-9
    )
    assert catchup_split["session"] == dividend_session.strftime("%Y-%m-%d")
    assert catchup_split["events"] == [
        {"ex_date": split_session.strftime("%Y-%m-%d"), "ratio": 10.0},
    ]
    assert catchup_records.index(catchup_split) < catchup_records.index(catchup_dividend)


def test_downloader_requests_split_adjusted_prices_and_corporate_actions(monkeypatch):
    calls = []
    calendar = pd.to_datetime(["2026-01-02", "2026-01-05"])

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            calls.append((self.symbol, kwargs))
            return pd.DataFrame(
                {"Open": [100.0, 100.0], "Close": [100.0, 100.0],
                 "Dividends": [0.0, 0.0], "Stock Splits": [0.0, 0.0]},
                index=calendar,
            )

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=FakeTicker))
    downloaded_calendar, downloaded_frames = download_completed_bars(
        "2026-01-02", "2026-01-06"
    )

    assert downloaded_calendar == list(calendar)
    assert set(downloaded_frames) == set(C1_SYMBOLS)
    assert len(calls) == len(C1_SYMBOLS) + 1
    assert all(call[1]["auto_adjust"] is False for call in calls)
    assert all(call[1]["actions"] is True for call in calls)
    assert all("Stock Splits" in frame.columns for frame in downloaded_frames.values())


def test_monthly_rebalance_can_sell_and_buy_without_short_or_margin(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    rebalance_open = sessions()[4]
    for index, symbol in enumerate(C1_SYMBOLS):
        market[symbol].loc[rebalance_open, "Open"] = 70.0 + index * 5.0
        market[symbol].loc[rebalance_open, "Close"] = 70.0 + index * 5.0

    result = advance_ledger(ledger, "2026-01-02", sessions(), market)
    records = read_records(ledger)
    second_fills = [
        record for record in by_type(records, "FILL") if record["session"] == "2026-02-03"
    ]
    sides = {record["side"] for record in second_fills}
    marks = by_type(records, "EQUITY")

    assert sides == {"BUY", "SELL"}
    assert result["last_equity"] > 0
    assert all(record["cash"] >= -1e-9 for record in marks)
    assert all(record["gross_exposure_pct"] <= 1.0 + 1e-9 for record in marks)


def test_daily_equity_identity_and_metrics_use_shared_function(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    for index, symbol in enumerate(C1_SYMBOLS):
        market[symbol]["Close"] = [100 + index + day for day in range(len(sessions()))]
    result = advance_ledger(ledger, "2026-01-02", sessions(), market)
    marks = by_type(read_records(ledger), "EQUITY")

    for record in marks:
        assert record["equity"] == pytest.approx(
            record["cash"] + record["gross_market_value"]
        )
    series = pd.Series(
        [record["equity"] for record in marks],
        index=pd.to_datetime([record["session"] for record in marks]),
        dtype=float,
    )
    assert result["metrics"] == equity_metrics(series)
    assert result["metrics"]["days"] == len(marks) == len(sessions())


def test_same_complete_history_is_idempotent(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    first = advance_ledger(ledger, "2026-01-02", sessions(), frames())
    before = ledger.read_bytes()
    second = advance_ledger(ledger, "2026-01-02", sessions(), frames())

    assert first["new_sessions"] == len(sessions())
    assert second["new_sessions"] == 0
    assert ledger.read_bytes() == before
    assert second["metrics"] == first["metrics"]


def test_pending_close_decision_survives_until_a_later_daily_run(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    first_day = sessions()[:1]
    first = advance_ledger(ledger, "2026-01-02", first_day, frames(first_day))
    assert first["pending_rebalance"] is True
    assert first["fills"] == 0

    second = advance_ledger(ledger, "2026-01-02", sessions(), frames())
    fills = by_type(read_records(ledger), "FILL")
    assert second["new_sessions"] == len(sessions()) - 1
    assert len([record for record in fills if record["session"] == "2026-01-05"]) == 17


def test_missing_bar_after_a_pending_decision_appends_nothing_and_retries_cleanly(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    first_day = sessions()[:1]
    opening = advance_ledger(ledger, "2026-01-02", first_day, frames(first_day))
    before = ledger.read_bytes()
    assert opening["pending_rebalance"] is True

    incomplete = frames()
    incomplete["WMT"] = incomplete["WMT"].drop(index=sessions()[1])
    with pytest.raises(C1ForwardError, match="missing bars"):
        advance_ledger(ledger, "2026-01-02", sessions(), incomplete)
    assert ledger.read_bytes() == before

    result = advance_ledger(ledger, "2026-01-02", sessions(), frames())
    fills = by_type(read_records(ledger), "FILL")

    assert result["new_sessions"] == len(sessions()) - 1
    assert len([record for record in fills if record["session"] == "2026-01-05"]) == 17
    assert result["equity_sessions"] == result["total_sessions"] == len(sessions())


def test_first_session_gap_creates_no_ledger(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    market["WMT"] = market["WMT"].drop(index=sessions()[0])

    with pytest.raises(C1ForwardError, match="2026-01-02 is missing bars"):
        advance_ledger(ledger, "2026-01-02", sessions(), market)
    assert not ledger.exists()


def test_missing_bars_name_the_session_and_every_missing_symbol(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    for symbol in ("WMT", "QQQ"):
        market[symbol] = market[symbol].drop(index=sessions()[1])

    with pytest.raises(C1ForwardError) as excinfo:
        advance_ledger(ledger, "2026-01-02", sessions(), market)
    message = str(excinfo.value)

    assert "2026-01-05" in message
    assert "QQQ" in message and "WMT" in message
    assert "NVDA" not in message
    assert not ledger.exists()


def test_known_dividend_on_a_failed_session_is_not_partially_credited(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    advance_ledger(ledger, "2026-01-02", sessions()[:2], frames(sessions()[:2]))
    before = ledger.read_bytes()

    market = frames()
    gap_session = sessions()[2]
    market["WMT"] = market["WMT"].drop(index=gap_session)
    market["NVDA"].loc[gap_session, "Dividends"] = 1.0

    with pytest.raises(C1ForwardError, match="missing bars"):
        advance_ledger(ledger, "2026-01-02", sessions(), market)

    assert ledger.read_bytes() == before
    assert not by_type(read_records(ledger), "DIVIDEND")


def test_missing_bar_on_a_split_ex_date_fails_closed_before_any_append(tmp_path):
    """A missing quote row on an ex-date halts instead of advancing.

    Snapshot prices carry the current split basis, so the ratio for that session
    is unreadable from the absent row; the provider may also attach it to the
    preceding session's row, which an earlier run may already have recorded.
    Whether the boundary product sees the ratio would then depend on where the
    run boundary happens to fall, so the run must append nothing at all.
    """
    ledger = tmp_path / "c1.jsonl"
    split_session = sessions()[4]

    advance_ledger(
        ledger,
        sessions()[0],
        sessions()[:4],
        split_snapshot(sessions()[:4], 1_000.0),
    )
    before = ledger.read_bytes()
    equity_before = [record["equity"] for record in by_type(read_records(ledger), "EQUITY")]

    current_snapshot = split_snapshot(sessions(), 100.0, [(split_session, 10.0)])
    current_snapshot["NVDA"] = current_snapshot["NVDA"].drop(index=split_session)

    with pytest.raises(C1ForwardError, match="2026-02-03 is missing bars"):
        advance_ledger(ledger, sessions()[0], sessions(), current_snapshot)

    records = read_records(ledger)
    assert ledger.read_bytes() == before
    assert [record["equity"] for record in by_type(records, "EQUITY")] == equity_before
    assert not any(
        record["session"] == split_session.strftime("%Y-%m-%d") for record in records
    )
    assert not [record for record in by_type(records, "SPLIT")]


@pytest.mark.parametrize(
    "defect",
    ["missing_symbol", "missing_bar", "missing_dividends", "missing_splits", "nan", "zero"],
)
def test_bad_market_input_fails_before_creating_ledger(tmp_path, defect):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    if defect == "missing_symbol":
        market.pop(C1_SYMBOLS[-1])
    elif defect == "missing_bar":
        market[C1_SYMBOLS[-1]] = market[C1_SYMBOLS[-1]].drop(index=sessions()[2])
    elif defect == "missing_dividends":
        market[C1_SYMBOLS[-1]] = market[C1_SYMBOLS[-1]].drop(columns="Dividends")
    elif defect == "missing_splits":
        market[C1_SYMBOLS[-1]] = market[C1_SYMBOLS[-1]].drop(columns="Stock Splits")
    elif defect == "nan":
        market[C1_SYMBOLS[-1]].loc[sessions()[2], "Close"] = float("nan")
    else:
        market[C1_SYMBOLS[-1]].loc[sessions()[2], "Open"] = 0.0

    with pytest.raises(C1ForwardError):
        advance_ledger(ledger, "2026-01-02", sessions(), market)
    assert not ledger.exists()


def test_processed_spy_calendar_prefix_cannot_be_rewritten(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    advance_ledger(ledger, "2026-01-02", sessions(), frames())
    before = ledger.read_bytes()
    altered_calendar = sessions()[:2] + sessions()[3:]
    altered_frames = frames(calendar=altered_calendar)

    with pytest.raises(C1ForwardError, match="calendar does not preserve"):
        advance_ledger(ledger, "2026-01-02", altered_calendar, altered_frames)
    assert ledger.read_bytes() == before


def test_corrupt_ledger_is_rejected(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    advance_ledger(ledger, "2026-01-02", sessions(), frames())
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write('{"broken":')

    with pytest.raises(C1ForwardError, match="partial final record"):
        read_records(ledger)


def test_program_must_start_on_first_session_of_month(tmp_path):
    ledger = tmp_path / "c1.jsonl"
    with pytest.raises(C1ForwardError, match="first market session"):
        advance_ledger(ledger, "2026-01-05", sessions(), frames())
    assert not ledger.exists()
