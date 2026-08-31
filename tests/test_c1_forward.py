"""Network-free tests for the frozen C1 forward-paper path."""

import pandas as pd
import pytest

from c1_forward import (
    C1ForwardError,
    C1_SYMBOLS,
    INITIAL_CASH,
    advance_ledger,
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
             "Close": [float(close_price)] * len(calendar)},
            index=pd.DatetimeIndex(calendar),
        )
        for symbol in C1_SYMBOLS
    }


def by_type(records, record_type):
    return [record for record in records if record["type"] == record_type]


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


@pytest.mark.parametrize("defect", ["missing_symbol", "missing_bar", "nan", "zero"])
def test_bad_market_input_fails_before_creating_ledger(tmp_path, defect):
    ledger = tmp_path / "c1.jsonl"
    market = frames()
    if defect == "missing_symbol":
        market.pop(C1_SYMBOLS[-1])
    elif defect == "missing_bar":
        market[C1_SYMBOLS[-1]] = market[C1_SYMBOLS[-1]].drop(index=sessions()[2])
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
