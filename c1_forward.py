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


SCHEMA_VERSION = 1
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
RECORD_TYPES = {"PROGRAM", "DECISION", "FILL", "EXECUTION", "EQUITY"}


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
        elif kind == "EQUITY":
            if record["session"] in equity_sessions:
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
            state.equities.append(record)
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
    if not {"Open", "Close"}.issubset(frame.columns):
        raise C1ForwardError(f"{symbol}: Open/Close columns are required")
    result = frame.loc[:, ["Open", "Close"]].copy()
    index = pd.DatetimeIndex(pd.to_datetime(result.index))
    if index.tz is not None:
        index = index.tz_localize(None)
    result.index = index.normalize()
    if not result.index.is_unique:
        raise C1ForwardError(f"{symbol}: duplicate sessions")
    return result.sort_index()


def _validated_bars(frames: Mapping[str, pd.DataFrame], session: pd.Timestamp) -> dict:
    bars: dict[str, dict[str, float]] = {}
    for symbol in C1_SYMBOLS:
        frame = frames[symbol]
        if session not in frame.index:
            raise C1ForwardError(f"{session.date()} {symbol}: missing bar")
        open_price = float(frame.loc[session, "Open"])
        close_price = float(frame.loc[session, "Close"])
        if not math.isfinite(open_price) or open_price <= 0:
            raise C1ForwardError(f"{session.date()} {symbol}: invalid Open")
        if not math.isfinite(close_price) or close_price <= 0:
            raise C1ForwardError(f"{session.date()} {symbol}: invalid Close")
        bars[symbol] = {"open": open_price, "close": close_price}
    return bars


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
    """Append every newly completed session, then score all recorded equity."""
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
    bars_by_session = {
        session: _validated_bars(normalised, session) for session in forward_sessions
    }

    existing = read_records(path)
    state = _rebuild(existing, start)
    recorded_sessions = [pd.Timestamp(record["session"]) for record in state.equities]
    if recorded_sessions != forward_sessions[:len(recorded_sessions)]:
        raise C1ForwardError("SPY session calendar does not preserve the ledger prefix")
    new_sessions = forward_sessions[len(recorded_sessions):]
    new_records: list[dict] = []
    if not existing:
        new_records.append(_program_record(start))

    positions = {session: index for index, session in enumerate(sessions)}
    for session in new_sessions:
        bars = bars_by_session[session]
        if state.pending_decision is not None:
            new_records.extend(_execute_rebalance(session, bars, state))
        equity = _equity_record(session, bars, state)
        new_records.append(equity)
        state.equities.append(equity)
        state.last_session = session

        index = positions[session]
        previous = sessions[index - 1] if index else None
        first_session = previous is None or (previous.year, previous.month) != (
            session.year, session.month
        )
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
        "total_sessions": len(final.equities),
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
    """Download adjusted Open/Close bars; ``end_exclusive`` is never processed."""
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
            auto_adjust=True,
            actions=False,
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
