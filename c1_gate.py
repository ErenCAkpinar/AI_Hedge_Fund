"""The C1 forward decision gate, run as one command.

`docs/designs/c1-forward-decision-gate.md` was declared at session 5 of an
expected 64 and asks five questions of the ledger on 2026-12-01.  This module
answers them.  It decides whether the LEDGER is a trustworthy instrument, not
whether C1 is a good strategy: no leg here reads a return figure, and the gate
lists return of any value under "explicitly not a pass".

    M1  every NYSE session present exactly once
    M2  the declared price-basis bound holds forward   (c1_basis_check)
    M3  corporate actions, against a second source     (never auto-PASS)
    M4  the monthly rebalance path is complete
    M5  every failed scheduled run self-recovered

Each leg returns its own verdict and the evidence behind it, so a FAIL names the
defect rather than just lowering a flag.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

import c1_basis_check as m2mod
import c1_forward as c1f


PASS = "PASS"
FAIL = "FAIL"
UNTESTED = "UNTESTED"
NEEDS_HUMAN_CHECK = "NEEDS_HUMAN_CHECK"
INCOMPLETE = "INCOMPLETE"

GATE_DATE = "2026-12-01"
GATE_EXPECTED_SESSIONS = 64
WEIGHT_TOLERANCE = 1e-6          # post-rebalance value share against 1/17

DEFAULT_LEDGER = m2mod.DEFAULT_LEDGER
DEFAULT_LOG_DIR = os.path.expanduser("~/Library/Logs/AI_Hedge_Fund/c1-forward")

_LOG_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")


class GateError(RuntimeError):
    """The gate cannot be evaluated honestly, so it is not evaluated at all."""


# ─────────────────────────────────────────────────────────────────────────────
# Shared ledger reading
# ─────────────────────────────────────────────────────────────────────────────
def _by_type(records: Sequence[Mapping], record_type: str) -> list[Mapping]:
    return [r for r in records if r["type"] == record_type]


def _session(record: Mapping) -> pd.Timestamp:
    return pd.Timestamp(record["session"]).normalize()


def ledger_sessions(records: Sequence[Mapping]) -> list[pd.Timestamp]:
    return [_session(r) for r in _by_type(records, "EQUITY")]


def rebuild_positions(records: Sequence[Mapping]) -> dict[pd.Timestamp, dict[str, float]]:
    """Share count per symbol immediately after each execution session.

    Walks FILL and SPLIT rows in ledger order. SPLIT carries shares_after
    directly, so the split path is trusted where it states its own result rather
    than being recomputed from a ratio the ledger already applied.
    """
    positions: dict[str, float] = {symbol: 0.0 for symbol in c1f.C1_SYMBOLS}
    snapshots: dict[pd.Timestamp, dict[str, float]] = {}
    for record in records:
        kind = record["type"]
        if kind == "SPLIT":
            positions[record["symbol"]] = float(record["shares_after"])
        elif kind == "FILL":
            delta = float(record["qty"])
            positions[record["symbol"]] += delta if record["side"] == "BUY" else -delta
        elif kind == "EXECUTION":
            snapshots[_session(record)] = dict(positions)
    return snapshots


# ─────────────────────────────────────────────────────────────────────────────
# M1 — session completeness
# ─────────────────────────────────────────────────────────────────────────────
def check_m1_sessions(
    records: Sequence[Mapping],
    market_calendar: Sequence[pd.Timestamp],
) -> dict:
    """Every market session in the ledger's own span, exactly once, no extras.

    Compared against an external calendar (SPY's own bars, the same source
    c1_forward uses). Checking the ledger against itself would only prove it is
    self-consistent, which a ledger that silently skipped a session also is.
    """
    sessions = ledger_sessions(records)
    if not sessions:
        return {"verdict": FAIL, "reason": "ledger holds no EQUITY rows", "sessions": 0}

    duplicates = sorted({s.strftime("%Y-%m-%d") for s in sessions if sessions.count(s) > 1})
    out_of_order = sessions != sorted(sessions)
    span_start, span_end = sessions[0], sessions[-1]
    expected = [
        pd.Timestamp(d).normalize() for d in market_calendar
        if span_start <= pd.Timestamp(d).normalize() <= span_end
    ]
    missing = sorted(set(expected) - set(sessions))
    unexpected = sorted(set(sessions) - set(expected))

    ok = not duplicates and not out_of_order and not missing and not unexpected
    return {
        "verdict": PASS if ok else FAIL,
        "sessions": len(sessions),
        "expected_sessions_in_span": len(expected),
        "first_session": span_start.strftime("%Y-%m-%d"),
        "last_session": span_end.strftime("%Y-%m-%d"),
        "duplicates": duplicates,
        "out_of_order": out_of_order,
        "missing_sessions": [d.strftime("%Y-%m-%d") for d in missing],
        "sessions_not_in_market_calendar": [d.strftime("%Y-%m-%d") for d in unexpected],
        "gate_expected_sessions": GATE_EXPECTED_SESSIONS,
        "at_gate_length": len(sessions) >= GATE_EXPECTED_SESSIONS,
    }


# ─────────────────────────────────────────────────────────────────────────────
# M3 — corporate actions
# ─────────────────────────────────────────────────────────────────────────────
def position_entering_session(
    records: Sequence[Mapping], symbol: str, session: pd.Timestamp
) -> float:
    """Shares held in `symbol` as the session opens, before that session's fills.

    Dividends are credited EX_DATE_BEFORE_OPEN against exactly this quantity, and
    splits are applied BEFORE_NEW_SESSIONS, so a split dated on the session counts
    and a fill dated on the session does not.
    """
    position = 0.0
    for record in records:
        if record.get("symbol") != symbol:
            continue
        when = _session(record)
        if record["type"] == "SPLIT" and when <= session:
            position = float(record["shares_after"])
        elif record["type"] == "FILL" and when < session:
            delta = float(record["qty"])
            position += delta if record["side"] == "BUY" else -delta
    return position


def check_m3_actions(
    records: Sequence[Mapping],
    provider_actions: Mapping[str, Sequence[Mapping]],
) -> dict:
    """Ledger dividends and splits against a freshly downloaded second reading.

    This is deliberately NOT an automatic PASS. The gate calls for hand
    verification against the issuer's actual action, and a fresh yfinance pull
    shares an upstream with the rows that produced the ledger: it catches an
    action the ledger dropped or mis-stored, not an action the provider itself
    has wrong. So the strongest verdict available here is NEEDS_HUMAN_CHECK, and
    where nothing happened at all the verdict is UNTESTED rather than PASS.

    Two shapes of the ledger are honoured rather than flagged:

    * A dividend whose ex-date falls on a session where the position is zero is
      correctly not credited — you cannot earn a dividend on shares you do not
      hold — so it is listed as uncredited rather than counted as missed. The
      program's own first session is exactly this case: the book is cash until
      the next open.
    * A SPLIT row is stamped with the run boundary, not the ex-date, because
      ``_boundary_splits`` rebases carried shares once before the first new
      session. Splits are therefore matched on the ex-dates inside ``events``.
    """
    ledger_divs = [
        {"symbol": r["symbol"], "session": r["session"],
         "per_share": round(float(r["dividend_per_share"]), 10),
         "cash_amount": round(float(r["cash_amount"]), 10)}
        for r in _by_type(records, "DIVIDEND")
    ]
    ledger_splits = [
        {"symbol": r["symbol"], "recorded_session": r["session"],
         "factor": round(float(r["split_factor"]), 10),
         "ex_dates": [e["ex_date"] for e in r.get("events", [])],
         "shares_before": float(r["shares_before"]),
         "shares_after": float(r["shares_after"])}
        for r in _by_type(records, "SPLIT")
    ]

    sessions = ledger_sessions(records)
    span = (sessions[0], sessions[-1]) if sessions else None
    provider: list[dict] = []
    for symbol, events in sorted(provider_actions.items()):
        for event in events:
            when = pd.Timestamp(event["date"]).normalize()
            if span and not (span[0] <= when <= span[1]):
                continue
            provider.append({
                "symbol": symbol, "session": when.strftime("%Y-%m-%d"),
                "kind": event["kind"], "value": round(float(event["value"]), 10),
            })

    ledger_keys = {(d["symbol"], d["session"], "DIVIDEND") for d in ledger_divs}
    for split in ledger_splits:
        for ex_date in split["ex_dates"]:
            ledger_keys.add((split["symbol"], ex_date, "SPLIT"))
    provider_keys = {(p["symbol"], p["session"], p["kind"]) for p in provider}

    uncredited: list[dict] = []
    missed: list[tuple] = []
    for key in sorted(provider_keys - ledger_keys):
        symbol, session, kind = key
        held = position_entering_session(records, symbol, pd.Timestamp(session))
        if kind == "DIVIDEND" and held <= 0.0:
            uncredited.append({
                "symbol": symbol, "session": session,
                "shares_held_entering_session": held,
                "why": "no position on the ex-date, so no dividend was due",
            })
        else:
            missed.append(key)
    only_ledger = sorted(ledger_keys - provider_keys)

    if missed or only_ledger:
        verdict = FAIL
    elif not ledger_keys:
        verdict = UNTESTED
    else:
        verdict = NEEDS_HUMAN_CHECK

    if verdict == UNTESTED:
        note = (
            "No corporate action was credited in the window. The dividend and split "
            "paths are UNTESTED by this gate, not validated by it — the RP-A window "
            "contained no split in any of these 17 names either, so the split path has "
            "still never been compared against an independent basis."
        )
        if uncredited:
            note += (
                f" {len(uncredited)} ex-date(s) fell on a session where the position was "
                "zero and were correctly not credited; that branch was exercised, the "
                "crediting branch was not."
            )
    elif verdict == NEEDS_HUMAN_CHECK:
        note = (
            "Every credited action matches a second reading. A human must now confirm "
            "each one against the issuer's actual announcement before this leg can be "
            "called passed; the provider is a shared upstream, not an independent witness."
        )
    else:
        note = (
            "Ledger and provider disagree about which corporate actions occurred. "
            "Investigate before anything else: a missed or mis-dated action re-bases "
            "positions and silently corrupts every later equity row."
        )

    return {
        "verdict": verdict,
        "ledger_dividends": ledger_divs,
        "ledger_splits": ledger_splits,
        "provider_actions_in_span": provider,
        "in_provider_not_in_ledger": [list(k) for k in missed],
        "in_ledger_not_in_provider": [list(k) for k in only_ledger],
        "correctly_uncredited": uncredited,
        "split_path_exercised": bool(ledger_splits),
        "note": note,
    }


# ─────────────────────────────────────────────────────────────────────────────
# M4 — the monthly rebalance path
# ─────────────────────────────────────────────────────────────────────────────
def check_m4_rebalances(records: Sequence[Mapping]) -> dict:
    """Each decision reaches an execution on the next session at equal weight."""
    sessions = ledger_sessions(records)
    index = {session: i for i, session in enumerate(sessions)}
    decisions = _by_type(records, "DECISION")
    executions = {r["decision_id"]: r for r in _by_type(records, "EXECUTION")}
    fills_by_id: dict[str, list[Mapping]] = defaultdict(list)
    for fill in _by_type(records, "FILL"):
        fills_by_id[fill["decision_id"]].append(fill)
    snapshots = rebuild_positions(records)

    denominator = float(len(c1f.C1_SYMBOLS))
    checked: list[dict] = []
    problems: list[str] = []

    # A decision made on the final recorded session has not had its execution
    # session yet; that is the ledger working, not a hole.
    pending_id = None
    if decisions and _session(decisions[-1]) == sessions[-1] and \
            decisions[-1]["decision_id"] not in executions:
        pending_id = decisions[-1]["decision_id"]

    for decision in decisions:
        decision_id = decision["decision_id"]
        if decision_id == pending_id:
            continue
        entry: dict = {"decision_id": decision_id, "decision_session": decision["session"]}
        execution = executions.get(decision_id)
        if execution is None:
            problems.append(f"{decision_id}: decision never executed")
            entry["verdict"] = FAIL
            checked.append(entry)
            continue

        exec_session = _session(execution)
        entry["execution_session"] = execution["session"]
        expected_index = index[_session(decision)] + 1
        if expected_index >= len(sessions) or sessions[expected_index] != exec_session:
            problems.append(
                f"{decision_id}: executed {execution['session']}, not the next session"
            )
            entry["verdict"] = FAIL

        fills = fills_by_id.get(decision_id, [])
        entry["fill_count"] = len(fills)
        if int(execution["fill_count"]) != len(fills):
            problems.append(
                f"{decision_id}: EXECUTION claims {execution['fill_count']} fills, "
                f"ledger holds {len(fills)}"
            )
            entry["verdict"] = FAIL
        if any(_session(f) != exec_session for f in fills):
            problems.append(f"{decision_id}: fills straddle more than one session")
            entry["verdict"] = FAIL

        # Equal weight after the trade, measured at the prices actually filled.
        prices = {f["symbol"]: float(f["raw_price"]) for f in fills}
        positions = snapshots.get(exec_session, {})
        gross_after = float(execution["gross_market_value_after"])
        target_share = 1.0 / denominator
        worst = 0.0
        covered = 0
        for symbol, price in prices.items():
            value = positions.get(symbol, 0.0) * price
            share = value / gross_after if gross_after > 0 else 0.0
            worst = max(worst, abs(share - target_share))
            covered += 1
        entry["symbols_priced_by_a_fill"] = covered
        entry["worst_weight_error"] = round(worst, 12)
        if covered and worst > WEIGHT_TOLERANCE:
            problems.append(
                f"{decision_id}: post-rebalance weight off by {worst:.2e}, "
                f"tolerance {WEIGHT_TOLERANCE:.0e}"
            )
            entry["verdict"] = FAIL
        entry.setdefault("verdict", PASS)
        checked.append(entry)

    # Decisions are made on the first ledger session of each month.
    month_firsts = []
    seen_months = set()
    for session in sessions:
        key = (session.year, session.month)
        if key not in seen_months:
            seen_months.add(key)
            month_firsts.append(session.strftime("%Y-%m-%d"))
    decision_sessions = [d["session"] for d in decisions]
    if decision_sessions != month_firsts:
        problems.append(
            f"decision sessions {decision_sessions} are not the ledger's month-first "
            f"sessions {month_firsts}"
        )

    return {
        "verdict": FAIL if problems else PASS,
        "decisions": len(decisions),
        "executions_checked": len(checked),
        "pending_decision": pending_id,
        "month_first_sessions": month_firsts,
        "rebalances": checked,
        "problems": problems,
        "weight_tolerance": WEIGHT_TOLERANCE,
    }


# ─────────────────────────────────────────────────────────────────────────────
# M5 — operational recovery
# ─────────────────────────────────────────────────────────────────────────────
def parse_run_logs(log_dir: str | Path) -> list[dict]:
    """One entry per dated scheduler log, with the outcome it recorded."""
    directory = Path(log_dir)
    if not directory.is_dir():
        raise GateError(f"{directory}: scheduler log directory not found")
    runs: list[dict] = []
    for path in sorted(directory.iterdir()):
        match = _LOG_NAME.match(path.name)
        if not match:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "C1_FORWARD_ERROR" in text:
            outcome = "ERROR"
        elif "SUCCESS c1_forward" in text:
            outcome = "SUCCESS"
        elif "SKIP" in text:
            outcome = "SKIP"
        else:
            outcome = "UNKNOWN"
        error_line = next(
            (line.strip() for line in text.splitlines() if "C1ForwardError" in line), None
        )
        runs.append({"run_date": match.group(1), "outcome": outcome, "error": error_line})
    return runs


def check_m5_operations(runs: Sequence[Mapping]) -> dict:
    """Every failed run is followed by a later run that succeeded, hands-free."""
    if not runs:
        return {"verdict": FAIL, "reason": "no scheduler logs found", "runs": 0}

    outcomes = [r["outcome"] for r in runs]
    failures = [r for r in runs if r["outcome"] in ("ERROR", "UNKNOWN")]
    unrecovered: list[str] = []
    for position, run in enumerate(runs):
        if run["outcome"] not in ("ERROR", "UNKNOWN"):
            continue
        if not any(o == "SUCCESS" for o in outcomes[position + 1:]):
            unrecovered.append(run["run_date"])

    longest_streak = 0
    streak = 0
    for outcome in outcomes:
        streak = streak + 1 if outcome in ("ERROR", "UNKNOWN") else 0
        longest_streak = max(longest_streak, streak)

    return {
        "verdict": FAIL if unrecovered else PASS,
        "runs": len(runs),
        "successes": outcomes.count("SUCCESS"),
        "skips": outcomes.count("SKIP"),
        "failures": len(failures),
        "longest_consecutive_failure_streak": longest_streak,
        "unrecovered_failures": unrecovered,
        "failure_detail": [
            {"run_date": r["run_date"], "error": r["error"]} for r in failures
        ],
        "note": (
            "Every failed run was picked up by a later scheduled run with no manual "
            "intervention, which is the fail-closed policy behaving as designed."
            if not unrecovered else
            "A failure was never followed by a successful run. The ledger is behind and "
            "will not catch up on its own; this is an M5 failure regardless of cause."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────────────────────
def download_market_calendar(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """SPY's own sessions — the calendar c1_forward itself uses."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise GateError("yfinance is required for the gate's network legs") from exc
    frame = yf.Ticker("SPY").history(
        start=pd.Timestamp(start).strftime("%Y-%m-%d"),
        end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        interval="1d", auto_adjust=False, actions=False,
    )
    if frame.empty:
        raise GateError("SPY: no session calendar downloaded")
    return list(pd.DatetimeIndex(frame.index).tz_localize(None).normalize())


def download_actions(
    start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, list[dict]]:
    """A second reading of dividends and splits for the frozen universe."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise GateError("yfinance is required for the gate's network legs") from exc

    first = pd.Timestamp(start).strftime("%Y-%m-%d")
    last = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    def fetch(symbol: str) -> tuple[str, list[dict]]:
        frame = yf.Ticker(symbol).history(
            start=first, end=last, interval="1d", auto_adjust=False, actions=True,
        )
        events: list[dict] = []
        if frame.empty:
            return symbol, events
        index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        for column, kind in (("Dividends", "DIVIDEND"), ("Stock Splits", "SPLIT")):
            if column not in frame:
                continue
            values = frame[column].values
            for when, value in zip(index, values):
                if float(value) > 0:
                    events.append({"date": when, "kind": kind, "value": float(value)})
        return symbol, events

    actions: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, symbol) for symbol in c1f.C1_SYMBOLS]
        for future in as_completed(futures):
            symbol, events = future.result()
            actions[symbol] = events
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────
def overall_verdict(legs: Mapping[str, Mapping]) -> str:
    """PASS needs all five. UNTESTED and NEEDS_HUMAN_CHECK are not passes.

    Resolved in docs/designs/c1-forward-decision-gate.md on 2026-09-09, while
    92% of the evidence was still in the future: a leg that never had the chance
    to be exercised leaves the gate INCOMPLETE, which is a decision for a human,
    not a pass the code may award itself.
    """
    verdicts = [leg.get("verdict") for leg in legs.values()]
    if any(v == FAIL for v in verdicts):
        return FAIL
    if all(v == PASS for v in verdicts):
        return PASS
    return INCOMPLETE


def run(ledger_path: str | Path, log_dir: str | Path) -> dict:
    records = c1f.read_records(ledger_path)
    if not records:
        raise GateError(f"{ledger_path}: ledger is empty or absent")
    sessions = ledger_sessions(records)
    if not sessions:
        raise GateError(f"{ledger_path}: ledger holds no EQUITY rows")
    span_start, span_end = sessions[0], sessions[-1]

    legs = {
        "M1_session_completeness": check_m1_sessions(
            records, download_market_calendar(span_start, span_end)
        ),
        "M2_basis_bound": m2mod.run(ledger_path),
        "M3_corporate_actions": check_m3_actions(
            records, download_actions(span_start, span_end)
        ),
        "M4_rebalance_path": check_m4_rebalances(records),
        "M5_operational_recovery": check_m5_operations(parse_run_logs(log_dir)),
    }
    return {
        "gate": "C1 forward decision gate",
        "declared_in": "docs/designs/c1-forward-decision-gate.md",
        "gate_date": GATE_DATE,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ledger": str(Path(ledger_path).resolve()),
        "sessions": len(sessions),
        "verdict": overall_verdict(legs),
        "legs": legs,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the C1 forward decision gate.")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    parser.add_argument("--out", help="Write the report JSON here as well as stdout")
    args = parser.parse_args(argv)

    report = run(args.ledger, args.log_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0 if report["verdict"] != FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
