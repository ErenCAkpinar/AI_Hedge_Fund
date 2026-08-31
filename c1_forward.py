"""Minimum forward-paper path for the frozen RP-A C1 baseline.

C1 holds the existing 17-symbol universe at equal weight.  It decides on the
first market session's close and fills at the next session's open.  The module
writes decisions, fills and daily close equity to one append-only JSONL ledger.
It has no agent, LLM or broker import and cannot place a real order.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from portfolio_simulator import equity_metrics


SCHEMA_VERSION = 5
STRATEGY_ID = "C1_EW_MONTHLY_FORWARD_V1"
PROGRAM_START = "2026-09-01"
C1_SYMBOLS = (
    "NVDA", "AVGO", "SOXX",
    "PLTR", "MSTR", "IBIT",
    "ASTS", "VST",
    "LMT", "LLY", "TSLA",
    "GLD", "FXY", "META",
    "USO", "WMT", "QQQ",
)
INITIAL_CASH = 1_500.0
SIDE_COST_BPS = 10.0
MAX_GROSS_EXPOSURE = 1.0
TARGET_WEIGHT_DENOMINATOR = len(C1_SYMBOLS)
RECORD_TYPES = {
    "PROGRAM", "DECISION", "SPLIT", "DIVIDEND", "FILL", "EXECUTION", "EQUITY",
}


class C1ForwardError(RuntimeError):
    """Advancing would make the C1 paper ledger invalid."""


@dataclass
class LedgerState:
    cash: float = INITIAL_CASH
    positions: dict[str, float] = field(
        default_factory=lambda: {symbol: 0.0 for symbol in C1_SYMBOLS}
    )
    pending_decision: str | None = None
    last_session: pd.Timestamp | None = None
    processed_sessions: list[pd.Timestamp] = field(default_factory=list)
    equities: list[dict] = field(default_factory=list)


def _record(record_type: str, session: pd.Timestamp, **payload: object) -> dict:
    if record_type not in RECORD_TYPES:
        raise C1ForwardError(f"unsupported record type: {record_type}")
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "type": record_type,
        "session": session.strftime("%Y-%m-%d"),
        **payload,
    }


def _program_record(program_start: pd.Timestamp) -> dict:
    return _record(
        "PROGRAM",
        program_start,
        program_start=program_start.strftime("%Y-%m-%d"),
        initial_cash=INITIAL_CASH,
        side_cost_bps=SIDE_COST_BPS,
        max_gross_exposure=MAX_GROSS_EXPOSURE,
        long_only=True,
        fractional_shares=True,
        symbols=list(C1_SYMBOLS),
        target_weight={"numerator": 1, "denominator": TARGET_WEIGHT_DENOMINATOR},
        decision_time="FIRST_SESSION_CLOSE",
        fill_time="NEXT_SESSION_OPEN",
        price_basis="SPLIT_ADJUSTED_NOT_DIVIDEND_ADJUSTED",
        split_policy="RUN_BOUNDARY_BASIS_TRANSITION",
        split_timing="BEFORE_NEW_SESSIONS",
        dividend_timing="EX_DATE_BEFORE_OPEN",
        bar_gap_policy="FAIL_CLOSED",
        metrics_function="portfolio_simulator.equity_metrics",
    )


def read_records(ledger_path: str | Path) -> list[dict]:
    """Read the JSONL ledger and reject partial or malformed records."""
    path = Path(ledger_path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise C1ForwardError("ledger has a partial final record")
    records: list[dict] = []
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            raise C1ForwardError(f"ledger line {line_number}: blank record")
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise C1ForwardError(f"ledger line {line_number}: invalid JSON") from exc
        if record.get("schema_version") != SCHEMA_VERSION:
            raise C1ForwardError(f"ledger line {line_number}: unsupported schema")
        if record.get("strategy_id") != STRATEGY_ID:
            raise C1ForwardError(f"ledger line {line_number}: strategy drift")
        if record.get("type") not in RECORD_TYPES:
            raise C1ForwardError(f"ledger line {line_number}: invalid record type")
        try:
            pd.Timestamp(record["session"])
        except (KeyError, TypeError, ValueError) as exc:
            raise C1ForwardError(f"ledger line {line_number}: invalid session") from exc
        records.append(record)
    return records


def _append_records(path: Path, records: Sequence[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        for record in records
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(fd, encoded[offset:])
            if written <= 0:
                raise C1ForwardError("ledger append failed")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)


def _rebuild(records: Sequence[dict], program_start: pd.Timestamp) -> LedgerState:
    state = LedgerState()
    if not records:
        return state
    if records[0] != _program_record(program_start):
        raise C1ForwardError("ledger frozen configuration does not match this run")

    program_count = 0
    decision_ids: set[str] = set()
    split_keys: set[tuple[str, str]] = set()
    dividend_keys: set[tuple[str, str]] = set()
    opening_trade_sessions: set[str] = set()
    processed_session_texts: set[str] = set()
    equity_sessions: set[str] = set()
    for index, record in enumerate(records):
        kind = record["type"]
        session = pd.Timestamp(record["session"])
        if kind == "PROGRAM":
            program_count += 1
            if index != 0 or program_count != 1:
                raise C1ForwardError("PROGRAM must occur exactly once")
        elif kind == "DECISION":
            decision_id = record.get("decision_id")
            if not decision_id or decision_id in decision_ids:
                raise C1ForwardError("duplicate or missing decision_id")
            if state.pending_decision is not None:
                raise C1ForwardError("overlapping rebalance decisions")
            if state.last_session != session:
                raise C1ForwardError("decision must follow that session's equity mark")
            decision_ids.add(decision_id)
            state.pending_decision = decision_id
        elif kind == "SPLIT":
            symbol = record.get("symbol")
            if symbol not in state.positions:
                raise C1ForwardError("split contains a symbol outside C1")
            key = (record["session"], symbol)
            if key in split_keys:
                raise C1ForwardError("duplicate split record")
            if key in dividend_keys:
                raise C1ForwardError("split must precede the same-session dividend")
            if record["session"] in opening_trade_sessions:
                raise C1ForwardError("split must precede opening trades")
            if state.last_session is not None and session <= state.last_session:
                raise C1ForwardError("split must precede that session's equity mark")
            if record.get("timing") != "BEFORE_NEW_SESSIONS":
                raise C1ForwardError("split has an invalid timing policy")
            expected_last_recorded = (
                state.last_session.strftime("%Y-%m-%d")
                if state.last_session is not None else None
            )
            if record.get("last_recorded_session") != expected_last_recorded:
                raise C1ForwardError("split misstates the last recorded session")
            factor = float(record.get("split_factor", math.nan))
            shares_before = float(record.get("shares_before", math.nan))
            shares_after = float(record.get("shares_after", math.nan))
            if not all(math.isfinite(value) for value in (factor, shares_before, shares_after)):
                raise C1ForwardError("split contains a non-finite value")
            if factor <= 0 or shares_before < 0 or shares_after < 0:
                raise C1ForwardError("split factor must be positive and shares non-negative")
            events = record.get("events")
            if not isinstance(events, list) or not events:
                raise C1ForwardError("split requires at least one ex-date event")
            product = 1.0
            previous_ex_date: pd.Timestamp | None = None
            for event in events:
                ex_date = event.get("ex_date") if isinstance(event, dict) else None
                ratio = float(event.get("ratio", math.nan)) if isinstance(event, dict) else math.nan
                if not isinstance(ex_date, str):
                    raise C1ForwardError("split event has an invalid ex_date")
                try:
                    ex_session = pd.Timestamp(ex_date)
                except ValueError as exc:
                    raise C1ForwardError("split event has an invalid ex_date") from exc
                if ex_session < session:
                    raise C1ForwardError("split event precedes its basis transition")
                if previous_ex_date is not None and ex_session < previous_ex_date:
                    raise C1ForwardError("split events must be in ex-date order")
                if not math.isfinite(ratio) or ratio <= 0:
                    raise C1ForwardError("split event ratio must be positive")
                previous_ex_date = ex_session
                product *= ratio
            if not math.isclose(product, factor, rel_tol=1e-12, abs_tol=1e-12):
                raise C1ForwardError("split factor does not match its ex-date events")
            if not math.isclose(
                shares_before, state.positions[symbol], rel_tol=1e-12, abs_tol=1e-10
            ):
                raise C1ForwardError("split shares_before does not match the ledger")
            if not math.isclose(
                shares_after, shares_before * factor, rel_tol=1e-12, abs_tol=1e-10
            ):
                raise C1ForwardError("split shares_after does not match the factor")
            state.positions[symbol] = shares_after
            split_keys.add(key)
        elif kind == "DIVIDEND":
            symbol = record.get("symbol")
            if symbol not in state.positions:
                raise C1ForwardError("dividend contains a symbol outside C1")
            key = (record["session"], symbol)
            if key in dividend_keys:
                raise C1ForwardError("duplicate dividend record")
            if record["session"] in opening_trade_sessions:
                raise C1ForwardError("dividend must precede opening trades")
            if state.last_session is not None and session <= state.last_session:
                raise C1ForwardError("dividend must precede that session's equity mark")
            shares = float(record.get("shares_eligible", math.nan))
            per_share = float(record.get("dividend_per_share", math.nan))
            amount = float(record.get("cash_amount", math.nan))
            cash_after = float(record.get("cash_after", math.nan))
            if not all(math.isfinite(value) for value in (shares, per_share, amount, cash_after)):
                raise C1ForwardError("dividend contains a non-finite value")
            if shares <= 0 or per_share <= 0:
                raise C1ForwardError("dividend shares and per-share amount must be positive")
            if abs(shares - state.positions[symbol]) > 1e-10:
                raise C1ForwardError("dividend eligible shares do not match the ledger")
            if abs(amount - shares * per_share) > 1e-7:
                raise C1ForwardError("dividend cash does not match shares times per-share amount")
            state.cash += amount
            if abs(state.cash - cash_after) > 1e-7:
                raise C1ForwardError("dividend cash_after does not reconcile")
            state.cash = cash_after
            dividend_keys.add(key)
        elif kind == "FILL":
            if record.get("decision_id") != state.pending_decision:
                raise C1ForwardError("fill does not match the pending decision")
            symbol = record.get("symbol")
            if symbol not in state.positions:
                raise C1ForwardError("fill contains a symbol outside C1")
            qty = float(record.get("qty", 0.0))
            fill_price = float(record.get("fill_price", 0.0))
            if not math.isfinite(qty) or qty <= 0:
                raise C1ForwardError("fill quantity must be positive")
            if not math.isfinite(fill_price) or fill_price <= 0:
                raise C1ForwardError("fill price must be positive")
            if record.get("side") == "SELL":
                if qty > state.positions[symbol] + 1e-10:
                    raise C1ForwardError("fill would create a short position")
                state.positions[symbol] -= qty
                state.cash += qty * fill_price
            elif record.get("side") == "BUY":
                state.positions[symbol] += qty
                state.cash -= qty * fill_price
            else:
                raise C1ForwardError("fill side must be BUY or SELL")
            opening_trade_sessions.add(record["session"])
        elif kind == "EXECUTION":
            if record.get("decision_id") != state.pending_decision:
                raise C1ForwardError("execution does not match the pending decision")
            recorded_cash = float(record.get("cash_after", math.nan))
            if not math.isfinite(recorded_cash) or abs(state.cash - recorded_cash) > 1e-7:
                raise C1ForwardError("execution cash does not reconcile to fills")
            state.cash = recorded_cash
            if state.cash < -1e-9 or any(qty < -1e-10 for qty in state.positions.values()):
                raise C1ForwardError("ledger violates cash or long-only constraints")
            state.pending_decision = None
            opening_trade_sessions.add(record["session"])
        elif kind == "EQUITY":
            if record["session"] in processed_session_texts:
                raise C1ForwardError("duplicate daily equity")
            if state.last_session is not None and session <= state.last_session:
                raise C1ForwardError("equity sessions must be strictly increasing")
            cash = float(record.get("cash", math.nan))
            gross = float(record.get("gross_market_value", math.nan))
            equity = float(record.get("equity", math.nan))
            if not all(math.isfinite(value) for value in (cash, gross, equity)):
                raise C1ForwardError("daily equity contains a non-finite value")
            if abs(cash - state.cash) > 1e-7 or abs(equity - cash - gross) > 1e-7:
                raise C1ForwardError("daily equity does not reconcile")
            gross_pct = gross / equity if equity > 0 else math.inf
            if gross_pct > MAX_GROSS_EXPOSURE + 1e-9:
                raise C1ForwardError("daily gross exposure exceeds 100%")
            state.last_session = session
            state.processed_sessions.append(session)
            state.equities.append(record)
            processed_session_texts.add(record["session"])
            equity_sessions.add(record["session"])
    return state


def _normalise_calendar(calendar: Iterable[pd.Timestamp]) -> list[pd.Timestamp]:
    sessions: list[pd.Timestamp] = []
    for item in calendar:
        session = pd.Timestamp(item)
        if session.tzinfo is not None:
            session = session.tz_localize(None)
        sessions.append(session.normalize())
    if sessions != sorted(sessions) or len(sessions) != len(set(sessions)):
        raise C1ForwardError("session calendar must be unique and ascending")
    return sessions


def _normalise_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    required = {"Open", "Close", "Dividends", "Stock Splits"}
    if not required.issubset(frame.columns):
        raise C1ForwardError(
            f"{symbol}: Open/Close/Dividends/Stock Splits columns are required"
        )
    result = frame.loc[:, ["Open", "Close", "Dividends", "Stock Splits"]].copy()
    index = pd.DatetimeIndex(pd.to_datetime(result.index))
    if index.tz is not None:
        index = index.tz_localize(None)
    result.index = index.normalize()
    if not result.index.is_unique:
        raise C1ForwardError(f"{symbol}: duplicate sessions")
    return result.sort_index()


def _bars_and_missing(
    frames: Mapping[str, pd.DataFrame], session: pd.Timestamp,
) -> tuple[dict, list[str]]:
    bars: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    for symbol in C1_SYMBOLS:
        frame = frames[symbol]
        if session not in frame.index:
            missing.append(symbol)
            continue
        open_price = float(frame.loc[session, "Open"])
        close_price = float(frame.loc[session, "Close"])
        dividend = float(frame.loc[session, "Dividends"])
        split_ratio = float(frame.loc[session, "Stock Splits"])
        if not math.isfinite(open_price) or open_price <= 0:
            raise C1ForwardError(f"{session.date()} {symbol}: invalid Open")
        if not math.isfinite(close_price) or close_price <= 0:
            raise C1ForwardError(f"{session.date()} {symbol}: invalid Close")
        if not math.isfinite(dividend) or dividend < 0:
            raise C1ForwardError(f"{session.date()} {symbol}: invalid Dividends")
        if not math.isfinite(split_ratio) or split_ratio < 0:
            raise C1ForwardError(f"{session.date()} {symbol}: invalid Stock Splits")
        bars[symbol] = {
            "open": open_price,
            "close": close_price,
            "dividend": dividend,
            "split_ratio": split_ratio,
        }
    return bars, missing


def _boundary_splits(
    new_sessions: Sequence[pd.Timestamp],
    bars_by_session: Mapping[pd.Timestamp, dict],
    state: LedgerState,
) -> list[dict]:
    """Rebase the prior ledger's shares onto the snapshot's current split basis.

    Provider bars are always presented on the snapshot's own split basis, so any
    session priced in this run already reflects every pending ex-date.  Only the
    shares carried over from earlier runs are stale: they are multiplied once, by
    the product of the pending ratios, before the first new session is processed.
    The new sessions then apply no split of their own.
    """
    if not new_sessions:
        return []
    boundary = new_sessions[0]
    last_recorded = (
        state.last_session.strftime("%Y-%m-%d") if state.last_session is not None else None
    )
    events: dict[str, list[dict]] = {}
    for session in new_sessions:
        bars = bars_by_session[session]
        for symbol in C1_SYMBOLS:
            if symbol not in bars or bars[symbol]["split_ratio"] <= 0:
                continue
            events.setdefault(symbol, []).append({
                "ex_date": session.strftime("%Y-%m-%d"),
                "ratio": bars[symbol]["split_ratio"],
            })
    records: list[dict] = []
    for symbol in C1_SYMBOLS:
        if symbol not in events:
            continue
        factor = 1.0
        for event in events[symbol]:
            factor *= event["ratio"]
        shares_before = state.positions[symbol]
        shares_after = shares_before * factor
        if not math.isfinite(factor) or not math.isfinite(shares_after):
            raise C1ForwardError(f"{symbol}: split basis transition overflow")
        state.positions[symbol] = shares_after
        records.append(_record(
            "SPLIT", boundary,
            symbol=symbol,
            split_factor=factor,
            events=events[symbol],
            shares_before=shares_before,
            shares_after=shares_after,
            last_recorded_session=last_recorded,
            timing="BEFORE_NEW_SESSIONS",
        ))
    return records


def _credit_dividends(session: pd.Timestamp, bars: dict, state: LedgerState) -> list[dict]:
    """Credit ex-date cash to shares held before any opening rebalance.

    Downloaded dividends share the snapshot's current split basis, so a
    per-share amount is quoted against the same basis as the shares that
    ``_boundary_splits`` has already rebased.
    """
    records: list[dict] = []
    for symbol in C1_SYMBOLS:
        if symbol not in bars:
            continue
        per_share = bars[symbol]["dividend"]
        shares = state.positions[symbol]
        if per_share <= 0 or shares <= 0:
            continue
        amount = shares * per_share
        state.cash += amount
        records.append(_record(
            "DIVIDEND", session,
            symbol=symbol,
            shares_eligible=shares,
            dividend_per_share=per_share,
            cash_amount=amount,
            cash_after=state.cash,
            timing="EX_DATE_BEFORE_OPEN",
        ))
    return records


def _invested_value(equity: float, current_values: Sequence[float]) -> float:
    """Solve V + 10bps * turnover(equal-weight V) = pre-trade equity."""
    if not math.isfinite(equity) or equity <= 0:
        raise C1ForwardError("cannot rebalance non-positive equity")
    cost_rate = SIDE_COST_BPS / 10_000.0
    low, high = 0.0, equity
    for _ in range(100):
        candidate = (low + high) / 2.0
        target = candidate / TARGET_WEIGHT_DENOMINATOR
        cost = cost_rate * sum(abs(target - value) for value in current_values)
        if candidate + cost <= equity:
            low = candidate
        else:
            high = candidate
    return low


def _execute_rebalance(session: pd.Timestamp, bars: dict, state: LedgerState) -> list[dict]:
    decision_id = state.pending_decision
    if decision_id is None:
        return []
    current_values = [state.positions[s] * bars[s]["open"] for s in C1_SYMBOLS]
    pre_trade_equity = state.cash + sum(current_values)
    invested = _invested_value(pre_trade_equity, current_values)
    target_value = invested / TARGET_WEIGHT_DENOMINATOR
    deltas = {
        symbol: target_value / bars[symbol]["open"] - state.positions[symbol]
        for symbol in C1_SYMBOLS
    }
    cost_rate = SIDE_COST_BPS / 10_000.0
    records: list[dict] = []
    total_cost = 0.0
    for side in ("SELL", "BUY"):
        for symbol in C1_SYMBOLS:
            delta = deltas[symbol]
            if (side == "SELL" and delta >= -1e-12) or (side == "BUY" and delta <= 1e-12):
                continue
            qty = abs(delta)
            raw_price = bars[symbol]["open"]
            fill_price = raw_price * (1.0 - cost_rate if side == "SELL" else 1.0 + cost_rate)
            cost = qty * raw_price * cost_rate
            if side == "SELL":
                state.positions[symbol] -= qty
                state.cash += qty * fill_price
            else:
                state.positions[symbol] += qty
                state.cash -= qty * fill_price
            total_cost += cost
            records.append(_record(
                "FILL", session,
                decision_id=decision_id,
                symbol=symbol,
                side=side,
                qty=qty,
                raw_price=raw_price,
                fill_price=fill_price,
                execution_cost=cost,
                fractional=True,
            ))
    if abs(state.cash) < 1e-9:
        state.cash = 0.0
    gross = sum(state.positions[s] * bars[s]["open"] for s in C1_SYMBOLS)
    equity = state.cash + gross
    gross_pct = gross / equity if equity > 0 else math.inf
    if state.cash < -1e-9 or any(qty < -1e-10 for qty in state.positions.values()):
        raise C1ForwardError("rebalance would violate cash or long-only constraints")
    if gross_pct > MAX_GROSS_EXPOSURE + 1e-9:
        raise C1ForwardError("rebalance would exceed 100% gross exposure")
    records.append(_record(
        "EXECUTION", session,
        decision_id=decision_id,
        fill_count=sum(record["type"] == "FILL" for record in records),
        pre_trade_equity=pre_trade_equity,
        execution_cost=total_cost,
        cash_after=state.cash,
        gross_market_value_after=gross,
        gross_exposure_pct_after=gross_pct,
    ))
    state.pending_decision = None
    return records


def _equity_record(session: pd.Timestamp, bars: dict, state: LedgerState) -> dict:
    gross = sum(state.positions[s] * bars[s]["close"] for s in C1_SYMBOLS)
    equity = state.cash + gross
    gross_pct = gross / equity if equity > 0 else math.inf
    if gross_pct > MAX_GROSS_EXPOSURE + 1e-9:
        raise C1ForwardError("close mark exceeds 100% gross exposure")
    return _record(
        "EQUITY", session,
        cash=state.cash,
        gross_market_value=gross,
        gross_exposure_pct=gross_pct,
        equity=equity,
    )


def _decision_record(session: pd.Timestamp) -> dict:
    return _record(
        "DECISION", session,
        decision_id=f"C1-{session.strftime('%Y-%m-%d')}",
        decision_at="SESSION_CLOSE",
        execute_at="NEXT_SESSION_OPEN",
        symbols=list(C1_SYMBOLS),
        target_weight={"numerator": 1, "denominator": TARGET_WEIGHT_DENOMINATOR},
        gross_target=MAX_GROSS_EXPOSURE,
        long_only=True,
        fractional_shares=True,
    )


def _metrics(equities: Sequence[dict]) -> dict:
    series = pd.Series(
        [float(record["equity"]) for record in equities],
        index=pd.DatetimeIndex([pd.Timestamp(record["session"]) for record in equities]),
        dtype=float,
    )
    return equity_metrics(series)


def advance_ledger(
    ledger_path: str | Path,
    program_start: str | pd.Timestamp,
    calendar: Iterable[pd.Timestamp],
    frames: Mapping[str, pd.DataFrame],
) -> dict:
    """Append every newly completed session, then score all recorded equity.

    A session is only recorded when every C1 symbol has a bar for it.  A single
    missing bar aborts the whole run before anything is appended: the absent row
    cannot be proven free of a corporate action, so recording the session would
    bake an unverifiable basis into the equity path.
    """
    path = Path(ledger_path)
    start = pd.Timestamp(program_start).normalize()
    sessions = _normalise_calendar(calendar)
    if not sessions or start not in sessions:
        raise C1ForwardError("program_start must be present in the SPY session calendar")
    if any(
        value < start and (value.year, value.month) == (start.year, start.month)
        for value in sessions
    ):
        raise C1ForwardError("program_start must be the month's first market session")
    if set(frames) != set(C1_SYMBOLS):
        missing = sorted(set(C1_SYMBOLS) - set(frames))
        extra = sorted(set(frames) - set(C1_SYMBOLS))
        raise C1ForwardError(f"frozen universe mismatch; missing={missing}, extra={extra}")
    normalised = {symbol: _normalise_frame(frames[symbol], symbol) for symbol in C1_SYMBOLS}
    forward_sessions = [session for session in sessions if session >= start]
    existing = read_records(path)
    state = _rebuild(existing, start)
    recorded_sessions = state.processed_sessions
    if recorded_sessions != forward_sessions[:len(recorded_sessions)]:
        raise C1ForwardError("SPY session calendar does not preserve the ledger prefix")
    new_sessions = forward_sessions[len(recorded_sessions):]

    bars_by_session: dict[pd.Timestamp, dict] = {}
    for session in new_sessions:
        bars, missing = _bars_and_missing(normalised, session)
        if missing:
            raise C1ForwardError(
                f"{session.date()} is missing bars for {sorted(missing)}; "
                "C1 records only complete sessions and will not advance"
            )
        bars_by_session[session] = bars

    new_records: list[dict] = []
    if not existing:
        new_records.append(_program_record(start))
    new_records.extend(_boundary_splits(new_sessions, bars_by_session, state))

    positions = {session: index for index, session in enumerate(sessions)}
    for session in new_sessions:
        bars = bars_by_session[session]
        new_records.extend(_credit_dividends(session, bars, state))
        index = positions[session]
        previous = sessions[index - 1] if index else None
        first_session = previous is None or (previous.year, previous.month) != (
            session.year, session.month
        )

        if state.pending_decision is not None:
            new_records.extend(_execute_rebalance(session, bars, state))
        equity = _equity_record(session, bars, state)
        new_records.append(equity)
        state.equities.append(equity)
        state.last_session = session
        state.processed_sessions.append(session)
        if first_session:
            decision = _decision_record(session)
            new_records.append(decision)
            state.pending_decision = decision["decision_id"]

    _append_records(path, new_records)
    records = existing + new_records
    final = _rebuild(records, start)
    decisions = [record for record in records if record["type"] == "DECISION"]
    fills = [record for record in records if record["type"] == "FILL"]
    last_equity = float(final.equities[-1]["equity"]) if final.equities else INITIAL_CASH
    return {
        "strategy_id": STRATEGY_ID,
        "ledger": str(path.resolve()),
        "new_sessions": len(new_sessions),
        "total_sessions": len(final.processed_sessions),
        "equity_sessions": len(final.equities),
        "decisions": len(decisions),
        "fills": len(fills),
        "last_session": (
            final.last_session.strftime("%Y-%m-%d") if final.last_session is not None else None
        ),
        "last_equity": last_equity,
        "pending_rebalance": final.pending_decision is not None,
        "metrics": _metrics(final.equities),
    }


def download_completed_bars(
    program_start: str | pd.Timestamp,
    end_exclusive: str | pd.Timestamp,
) -> tuple[list[pd.Timestamp], dict[str, pd.DataFrame]]:
    """Download split-adjusted Open/Close plus actions; do not process the end date."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise C1ForwardError("yfinance is required only for the download CLI") from exc
    start = pd.Timestamp(program_start).normalize()
    end = pd.Timestamp(end_exclusive).normalize()
    if end <= start:
        raise C1ForwardError("end_exclusive must be after program_start")
    context_start = start.replace(day=1)

    def fetch(symbol: str) -> tuple[str, pd.DataFrame]:
        frame = yf.Ticker(symbol).history(
            start=context_start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=False,
            actions=True,
        )
        if frame.empty:
            raise C1ForwardError(f"{symbol}: no completed bars downloaded")
        return symbol, _normalise_frame(frame, symbol)

    downloaded: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, symbol) for symbol in (*C1_SYMBOLS, "SPY")]
        for future in as_completed(futures):
            symbol, frame = future.result()
            downloaded[symbol] = frame
    calendar = list(downloaded.pop("SPY").index)
    frames = {
        symbol: downloaded[symbol].loc[downloaded[symbol].index.isin(calendar)]
        for symbol in C1_SYMBOLS
    }
    return calendar, frames


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advance the frozen C1 monthly equal-weight forward-paper ledger."
    )
    parser.add_argument("--ledger", required=True, help="Append-only JSONL ledger path")
    parser.add_argument(
        "--end-exclusive",
        default=date.today().isoformat(),
        help="Download boundary (default: today; only earlier sessions are used)",
    )
    args = parser.parse_args(argv)
    if pd.Timestamp(args.end_exclusive).date() > date.today():
        raise C1ForwardError("end_exclusive cannot be in the future")
    calendar, frames = download_completed_bars(PROGRAM_START, args.end_exclusive)
    result = advance_ledger(args.ledger, PROGRAM_START, calendar, frames)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
