"""The five gate legs: do they pass what should pass, and name what should fail?

docs/designs/c1-forward-decision-gate.md decides whether the ledger is a
trustworthy instrument. A gate that cannot fail is not a gate, so every leg here
is tested against a defect as well as against a clean ledger.

No test in this file touches the network.
"""

import pandas as pd
import pytest

import c1_forward as c1f
import c1_gate as gate

SYMBOLS = list(c1f.C1_SYMBOLS)
PRICE = 100.0
GROSS = PRICE * len(SYMBOLS)          # one share each, equal weight by construction


# ─────────────────────────────────────────────────────────────────────
# Ledger fixtures
# ─────────────────────────────────────────────────────────────────────
def equity(session: str, value: float = 1500.0) -> dict:
    return {"type": "EQUITY", "session": session, "equity": value,
            "cash": 0.0, "gross_market_value": value, "gross_exposure_pct": 1.0}


def decision(session: str) -> dict:
    return {"type": "DECISION", "session": session, "decision_id": f"C1-{session}"}


def fills(session: str, decision_id: str, qty: float = 1.0,
          price: float = PRICE) -> list[dict]:
    return [
        {"type": "FILL", "session": session, "decision_id": decision_id,
         "symbol": symbol, "side": "BUY", "qty": qty, "raw_price": price,
         "fill_price": price, "execution_cost": 0.0}
        for symbol in SYMBOLS
    ]


def execution(session: str, decision_id: str, fill_count: int = len(SYMBOLS),
              gross: float = GROSS) -> dict:
    return {"type": "EXECUTION", "session": session, "decision_id": decision_id,
            "fill_count": fill_count, "pre_trade_equity": gross, "execution_cost": 0.0,
            "cash_after": 0.0, "gross_market_value_after": gross,
            "gross_exposure_pct_after": 1.0}


def clean_ledger() -> list[dict]:
    """Two sessions: decide on the first, fill on the second."""
    return [
        equity("2026-09-01"),
        decision("2026-09-01"),
        *fills("2026-09-02", "C1-2026-09-01"),
        execution("2026-09-02", "C1-2026-09-01"),
        equity("2026-09-02", GROSS),
    ]


def calendar(*dates: str) -> list[pd.Timestamp]:
    return [pd.Timestamp(d).normalize() for d in dates]


# ─────────────────────────────────────────────────────────────────────
# M1 — session completeness
# ─────────────────────────────────────────────────────────────────────
def test_m1_passes_when_the_ledger_matches_the_market_calendar():
    report = gate.check_m1_sessions(clean_ledger(), calendar("2026-09-01", "2026-09-02"))
    assert report["verdict"] == gate.PASS
    assert report["sessions"] == 2


def test_m1_fails_on_a_skipped_session():
    records = [equity("2026-09-01"), equity("2026-09-03")]
    report = gate.check_m1_sessions(records, calendar("2026-09-01", "2026-09-02", "2026-09-03"))
    assert report["verdict"] == gate.FAIL
    assert report["missing_sessions"] == ["2026-09-02"]


def test_m1_fails_on_a_session_the_market_never_had():
    records = [equity("2026-09-01"), equity("2026-09-05")]
    report = gate.check_m1_sessions(records, calendar("2026-09-01", "2026-09-02"))
    assert report["verdict"] == gate.FAIL
    assert report["sessions_not_in_market_calendar"] == ["2026-09-05"]


def test_m1_fails_on_a_duplicated_session():
    records = [equity("2026-09-01"), equity("2026-09-01")]
    report = gate.check_m1_sessions(records, calendar("2026-09-01"))
    assert report["verdict"] == gate.FAIL
    assert report["duplicates"] == ["2026-09-01"]


def test_m1_does_not_punish_a_ledger_that_is_merely_behind():
    """Sessions after the ledger's last row are not gaps; the run has not happened."""
    records = [equity("2026-09-01"), equity("2026-09-02")]
    report = gate.check_m1_sessions(
        records, calendar("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")
    )
    assert report["verdict"] == gate.PASS
    assert report["expected_sessions_in_span"] == 2


# ─────────────────────────────────────────────────────────────────────
# M3 — corporate actions
# ─────────────────────────────────────────────────────────────────────
def held_ledger() -> list[dict]:
    """clean_ledger plus a third session, so a position is actually carried."""
    return clean_ledger() + [equity("2026-09-03", GROSS)]


def test_m3_reports_untested_when_no_action_occurred():
    report = gate.check_m3_actions(clean_ledger(), {s: [] for s in SYMBOLS})
    assert report["verdict"] == gate.UNTESTED
    assert report["split_path_exercised"] is False
    assert "never been compared against an independent basis" in report["note"]


def test_m3_never_awards_itself_a_pass():
    """The gate calls for hand verification; agreement with a shared upstream is not it."""
    records = clean_ledger() + [{
        "type": "DIVIDEND", "session": "2026-09-02", "symbol": "WMT",
        "dividend_per_share": 0.25, "shares_eligible": 1.0, "cash_amount": 0.25,
        "cash_after": 0.25,
    }]
    provider = {s: [] for s in SYMBOLS}
    provider["WMT"] = [{"date": pd.Timestamp("2026-09-02"), "kind": "DIVIDEND", "value": 0.25}]
    report = gate.check_m3_actions(records, provider)
    assert report["verdict"] == gate.NEEDS_HUMAN_CHECK
    assert report["verdict"] != gate.PASS


def test_m3_fails_on_a_dividend_the_ledger_missed_while_holding_the_stock():
    provider = {s: [] for s in SYMBOLS}
    provider["LMT"] = [{"date": pd.Timestamp("2026-09-03"), "kind": "DIVIDEND", "value": 3.3}]
    report = gate.check_m3_actions(held_ledger(), provider)
    assert report["verdict"] == gate.FAIL
    assert ["LMT", "2026-09-03", "DIVIDEND"] in report["in_provider_not_in_ledger"]


def test_m3_does_not_flag_a_dividend_owed_on_a_position_not_yet_held():
    """The real 2026-09-01 LMT ex-date: the book was still 100% cash that session.

    The first live run of this gate reported it as a missed action. It is not one
    — a dividend cannot be earned on shares nobody owns, and the fill lands at the
    next open already ex-dividend. The defect was in this check, not the ledger.
    """
    provider = {s: [] for s in SYMBOLS}
    provider["LMT"] = [{"date": pd.Timestamp("2026-09-01"), "kind": "DIVIDEND", "value": 3.3}]
    report = gate.check_m3_actions(clean_ledger(), provider)
    assert report["verdict"] == gate.UNTESTED
    assert report["in_provider_not_in_ledger"] == []
    assert report["correctly_uncredited"][0]["symbol"] == "LMT"
    assert report["correctly_uncredited"][0]["shares_held_entering_session"] == 0.0
    assert "correctly not credited" in report["note"]


def test_m3_still_flags_a_split_missed_on_a_position_not_yet_held():
    """The zero-position exemption is for dividends only; a split re-bases anyway."""
    provider = {s: [] for s in SYMBOLS}
    provider["NVDA"] = [{"date": pd.Timestamp("2026-09-01"), "kind": "SPLIT", "value": 4.0}]
    report = gate.check_m3_actions(clean_ledger(), provider)
    assert report["verdict"] == gate.FAIL
    assert ["NVDA", "2026-09-01", "SPLIT"] in report["in_provider_not_in_ledger"]


def test_m3_matches_a_split_on_its_ex_date_not_its_recorded_session():
    """_boundary_splits stamps the run boundary; the ex-date lives inside events."""
    records = held_ledger() + [{
        "type": "SPLIT", "session": "2026-09-03", "symbol": "NVDA",
        "split_factor": 4.0, "shares_before": 1.0, "shares_after": 4.0,
        "events": [{"ex_date": "2026-09-02", "ratio": 4.0}],
    }]
    provider = {s: [] for s in SYMBOLS}
    provider["NVDA"] = [{"date": pd.Timestamp("2026-09-02"), "kind": "SPLIT", "value": 4.0}]
    report = gate.check_m3_actions(records, provider)
    assert report["verdict"] == gate.NEEDS_HUMAN_CHECK
    assert report["in_ledger_not_in_provider"] == []
    assert report["split_path_exercised"] is True


def test_m3_fails_on_a_split_the_provider_does_not_confirm():
    records = held_ledger() + [{
        "type": "SPLIT", "session": "2026-09-03", "symbol": "NVDA",
        "split_factor": 4.0, "shares_before": 1.0, "shares_after": 4.0,
        "events": [{"ex_date": "2026-09-03", "ratio": 4.0}],
    }]
    report = gate.check_m3_actions(records, {s: [] for s in SYMBOLS})
    assert report["verdict"] == gate.FAIL
    assert ["NVDA", "2026-09-03", "SPLIT"] in report["in_ledger_not_in_provider"]


def test_m3_ignores_provider_actions_outside_the_ledger_span():
    provider = {s: [] for s in SYMBOLS}
    provider["WMT"] = [{"date": pd.Timestamp("2026-12-15"), "kind": "DIVIDEND", "value": 0.25}]
    report = gate.check_m3_actions(clean_ledger(), provider)
    assert report["verdict"] == gate.UNTESTED
    assert report["provider_actions_in_span"] == []


def test_position_entering_a_session_excludes_that_session_own_fills():
    records = held_ledger()
    assert gate.position_entering_session(records, "LMT", pd.Timestamp("2026-09-02")) == 0.0
    assert gate.position_entering_session(records, "LMT", pd.Timestamp("2026-09-03")) == 1.0


def test_position_entering_a_session_includes_a_split_dated_on_it():
    """Splits are applied BEFORE_NEW_SESSIONS, so the session's own split counts."""
    records = held_ledger() + [{
        "type": "SPLIT", "session": "2026-09-03", "symbol": "NVDA",
        "split_factor": 4.0, "shares_before": 1.0, "shares_after": 4.0,
        "events": [{"ex_date": "2026-09-03", "ratio": 4.0}],
    }]
    assert gate.position_entering_session(records, "NVDA", pd.Timestamp("2026-09-03")) == 4.0


# ─────────────────────────────────────────────────────────────────────
# M4 — the rebalance path
# ─────────────────────────────────────────────────────────────────────
def test_m4_passes_a_complete_equal_weight_rebalance():
    report = gate.check_m4_rebalances(clean_ledger())
    assert report["verdict"] == gate.PASS
    assert report["problems"] == []
    assert report["rebalances"][0]["worst_weight_error"] == 0.0


def test_m4_treats_a_decision_on_the_last_session_as_pending_not_missing():
    records = [equity("2026-09-01"), decision("2026-09-01")]
    report = gate.check_m4_rebalances(records)
    assert report["verdict"] == gate.PASS
    assert report["pending_decision"] == "C1-2026-09-01"


def test_m4_fails_when_a_decision_never_executes():
    records = [equity("2026-09-01"), decision("2026-09-01"), equity("2026-09-02")]
    report = gate.check_m4_rebalances(records)
    assert report["verdict"] == gate.FAIL
    assert "never executed" in report["problems"][0]


def test_m4_fails_when_execution_is_not_on_the_next_session():
    records = [
        equity("2026-09-01"), decision("2026-09-01"), equity("2026-09-02"),
        *fills("2026-09-03", "C1-2026-09-01"),
        execution("2026-09-03", "C1-2026-09-01"), equity("2026-09-03", GROSS),
    ]
    report = gate.check_m4_rebalances(records)
    assert report["verdict"] == gate.FAIL
    assert any("not the next session" in p for p in report["problems"])


def test_m4_fails_when_the_execution_miscounts_its_own_fills():
    records = clean_ledger()
    records[-2] = execution("2026-09-02", "C1-2026-09-01", fill_count=16)
    report = gate.check_m4_rebalances(records)
    assert report["verdict"] == gate.FAIL
    assert any("claims 16 fills" in p for p in report["problems"])


def test_m4_fails_when_the_book_is_not_equal_weight_afterwards():
    """One symbol bought at double size is what a broken solver looks like."""
    records = clean_ledger()
    for record in records:
        if record["type"] == "FILL" and record["symbol"] == "NVDA":
            record["qty"] = 2.0
    report = gate.check_m4_rebalances(records)
    assert report["verdict"] == gate.FAIL
    assert any("post-rebalance weight off" in p for p in report["problems"])


def test_m4_fails_when_a_decision_is_not_on_a_month_first_session():
    records = [
        equity("2026-09-01"), equity("2026-09-02"), decision("2026-09-02"),
        *fills("2026-09-03", "C1-2026-09-02"),
        execution("2026-09-03", "C1-2026-09-02"), equity("2026-09-03", GROSS),
    ]
    report = gate.check_m4_rebalances(records)
    assert report["verdict"] == gate.FAIL
    assert any("month-first" in p for p in report["problems"])


def test_rebuild_positions_applies_a_split_by_its_recorded_result():
    records = [
        equity("2026-09-01"), decision("2026-09-01"),
        *fills("2026-09-02", "C1-2026-09-01"),
        execution("2026-09-02", "C1-2026-09-01"), equity("2026-09-02", GROSS),
        {"type": "SPLIT", "session": "2026-09-03", "symbol": "NVDA",
         "split_factor": 4.0, "events": [], "shares_before": 1.0, "shares_after": 4.0},
        *fills("2026-09-03", "C1-2026-09-03", qty=0.5),
        execution("2026-09-03", "C1-2026-09-03"), equity("2026-09-03", GROSS),
    ]
    snapshots = gate.rebuild_positions(records)
    assert snapshots[pd.Timestamp("2026-09-03")]["NVDA"] == pytest.approx(4.5)
    assert snapshots[pd.Timestamp("2026-09-03")]["WMT"] == pytest.approx(1.5)


# ─────────────────────────────────────────────────────────────────────
# M5 — operational recovery
# ─────────────────────────────────────────────────────────────────────
def runs(*pairs: tuple[str, str]) -> list[dict]:
    return [{"run_date": d, "outcome": o, "error": None} for d, o in pairs]


def test_m5_passes_when_a_failure_is_picked_up_by_a_later_run():
    """The real 2026-09-04 NVDA bar failure, caught up by the 09-07 run."""
    report = gate.check_m5_operations(runs(
        ("2026-09-02", "SUCCESS"), ("2026-09-03", "SUCCESS"),
        ("2026-09-04", "ERROR"), ("2026-09-07", "SUCCESS"), ("2026-09-08", "SUCCESS"),
    ))
    assert report["verdict"] == gate.PASS
    assert report["failures"] == 1
    assert report["unrecovered_failures"] == []


def test_m5_fails_when_the_last_run_errored_and_nothing_followed():
    report = gate.check_m5_operations(runs(
        ("2026-09-03", "SUCCESS"), ("2026-09-04", "ERROR"),
    ))
    assert report["verdict"] == gate.FAIL
    assert report["unrecovered_failures"] == ["2026-09-04"]
    assert "will not catch up on its own" in report["note"]


def test_m5_counts_the_longest_failure_streak():
    report = gate.check_m5_operations(runs(
        ("2026-09-02", "ERROR"), ("2026-09-03", "ERROR"), ("2026-09-04", "ERROR"),
        ("2026-09-07", "SUCCESS"),
    ))
    assert report["verdict"] == gate.PASS
    assert report["longest_consecutive_failure_streak"] == 3


def test_m5_treats_an_unreadable_run_as_a_failure():
    report = gate.check_m5_operations(runs(("2026-09-03", "UNKNOWN")))
    assert report["verdict"] == gate.FAIL


def test_m5_fails_when_there_are_no_logs_at_all():
    assert gate.check_m5_operations([])["verdict"] == gate.FAIL


def test_parse_run_logs_reads_the_real_line_formats(tmp_path):
    (tmp_path / "2026-09-03.log").write_text(
        "[2026-09-02T23:30:08Z] START c1_forward end_exclusive=2026-09-03\n"
        "[2026-09-02T23:30:08Z] SUCCESS c1_forward\n", encoding="utf-8")
    (tmp_path / "2026-09-04.log").write_text(
        "C1ForwardError: 2026-09-03 NVDA: invalid Close\n"
        "[2026-09-04T00:00:07Z] C1_FORWARD_ERROR status=1 log=x\n", encoding="utf-8")
    (tmp_path / "2026-09-01.log").write_text(
        "[2026-08-31T23:30:03Z] START c1_forward\n"
        "[2026-08-31T23:30:05Z] SKIP no completed session\n", encoding="utf-8")
    (tmp_path / "launchd.stderr.log").write_text("ignored\n", encoding="utf-8")

    parsed = gate.parse_run_logs(tmp_path)
    assert [r["run_date"] for r in parsed] == ["2026-09-01", "2026-09-03", "2026-09-04"]
    assert [r["outcome"] for r in parsed] == ["SKIP", "SUCCESS", "ERROR"]
    assert "invalid Close" in parsed[2]["error"]


def test_parse_run_logs_refuses_a_missing_directory(tmp_path):
    with pytest.raises(gate.GateError, match="not found"):
        gate.parse_run_logs(tmp_path / "nope")


# ─────────────────────────────────────────────────────────────────────
# The gate's own arithmetic
# ─────────────────────────────────────────────────────────────────────
def test_the_gate_passes_only_when_every_leg_passes():
    assert gate.overall_verdict({k: {"verdict": gate.PASS} for k in "abcde"}) == gate.PASS


def test_one_failing_leg_fails_the_gate():
    legs = {k: {"verdict": gate.PASS} for k in "abcd"}
    legs["e"] = {"verdict": gate.FAIL}
    assert gate.overall_verdict(legs) == gate.FAIL


def test_an_untested_leg_leaves_the_gate_incomplete_rather_than_passed():
    legs = {k: {"verdict": gate.PASS} for k in "abcd"}
    legs["e"] = {"verdict": gate.UNTESTED}
    assert gate.overall_verdict(legs) == gate.INCOMPLETE


def test_a_leg_awaiting_a_human_does_not_pass_the_gate_by_itself():
    legs = {k: {"verdict": gate.PASS} for k in "abcd"}
    legs["e"] = {"verdict": gate.NEEDS_HUMAN_CHECK}
    assert gate.overall_verdict(legs) == gate.INCOMPLETE


def test_a_failure_outranks_an_untested_leg():
    legs = {"a": {"verdict": gate.UNTESTED}, "b": {"verdict": gate.FAIL}}
    assert gate.overall_verdict(legs) == gate.FAIL
