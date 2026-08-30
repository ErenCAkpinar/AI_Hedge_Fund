"""
portfolio_simulator.py — cash-and-positions ledger for backtesting.

Replaces the per-trade simulation in true_backtest.py, which summed independent
trades that could overlap without limit (measured: 66.5% mean / 217.5% max
concurrent gross exposure against a $1,500 cash account).

Design agreed 2026-08-30 between Claude and Codex (gpt-5.6-sol). See
docs/designs/backtest-live-parity.md and docs/designs/go-no-go.md.

DELIBERATELY IMPORTS NOTHING FROM THIS PROJECT.
The signal layer is injected as a callback so this engine stays deterministic
and testable against synthetic frames.

──────────────────────────────────────────────────────────────────────────────
EVENT ORDER WITHIN ONE SESSION  (order is load-bearing, do not reorder)

  previous close ── pending ENTRY / PYRAMID intents ──┐
                                                      ▼
  ┌─ TODAY ─────────────────────────────────────────────────────────────────┐
  │ 1. gap-through stops        open beyond the stop that was already active │
  │ 2. fill pending intents     at today's open, one shared equity snapshot  │
  │ 3. intraday stops           low/high vs the stop active BEFORE this bar  │
  │ 4. time exits               at close, exactly max_holding_sessions       │
  │ 5. mark to market           survivors at close -> equity row             │
  │ 6. advance watermark/stop   takes effect NEXT session, never this one    │
  │ 7. emit intents             close signals + at most one pyramid layer    │
  └──────────────────────────────────────────────────────────────────────────┘

THE ORDERING RULE (why exit reasons are trustworthy now):
  Only the stop known BEFORE the bar may fire during that bar. A daily bar
  cannot establish whether the high preceded the low, so raising the stop from
  today's high and then testing today's low against it — as the old code did at
  true_backtest.py:415-423 — assumes the favourable sequence. Step 6 runs after
  step 3 for exactly this reason.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

import pandas as pd

__all__ = [
    "SimulatorConfig",
    "SignalDecision",
    "Position",
    "SimulationResult",
    "simulate_portfolio",
]


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SimulatorConfig:
    initial_cash: float = 1_500.0

    # D13: a cash account cannot exceed 100% gross. Kept explicit rather than
    # implied by "cash >= 0" because it is an auditable invariant and it starts
    # mattering the moment SHORT or margin is introduced.
    max_gross_exposure: float = 1.00

    max_holding_sessions: int = 15
    pyramid_fraction: float = 0.50
    max_pyramids: int = 2

    # Execution cost, per side, in basis points. Alpaca is commission-free on US
    # equities, so spread and slippage are the real cost, not commission.
    half_spread_bps: float = 5.0
    slippage_bps: float = 5.0

    # Pass-through regulatory fees, kept separate so they are never mistaken for
    # commission. Schedule as of 2026-07-20.
    sec_fee_rate: float = 0.0000206        # sell side only, on notional
    finra_taf_per_share: float = 0.000195  # sell side only
    finra_taf_cap: float = 9.79
    cat_fee_per_share: float = 0.000003    # both sides

    integer_shares: bool = True
    allow_short: bool = False              # D12: fail closed until broker tests exist

    @property
    def side_cost_bps(self) -> float:
        return self.half_spread_bps + self.slippage_bps


@dataclass(frozen=True)
class SignalDecision:
    """What the injected signal callback returns for a symbol on a session close."""
    side: int                  # +1 LONG, -1 SHORT
    score: float
    confidence: str
    target_fraction: float     # fraction of equity to deploy
    atr: float                 # frozen on the SIGNAL date, never the entry bar
    trail_multiple: float


@dataclass
class Position:
    trade_id: int
    symbol: str
    side: int

    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    qty: int
    base_qty: int
    avg_entry_price: float     # actual adverse execution price, cost included

    score: float
    confidence: str
    target_fraction: float

    atr_at_signal: float
    trail_multiple: float
    watermark: float
    active_stop: float
    stop_has_advanced: bool = False

    pyramid_levels: tuple = ()
    next_pyramid_index: int = 0
    pyramid_fill_count: int = 0

    bars_held: int = 0
    accumulated_execution_cost: float = 0.0
    accumulated_fees: float = 0.0
    realized_cash_in: float = 0.0   # total cash paid in (long) across all layers


@dataclass
class SimulationResult:
    equity_curve: pd.DataFrame
    closed_trades: list
    fills: list
    rejections: list
    config: SimulatorConfig
    final_equity: float
    total_execution_cost: float
    total_fees: float


# ─────────────────────────────────────────────────────────────────────────────
# Cost model
# ─────────────────────────────────────────────────────────────────────────────
def adverse_price(raw: float, side: int, opening: bool, cfg: SimulatorConfig) -> float:
    """Price actually paid/received, moved against us by half-spread + slippage.

    opening=True  -> we are increasing exposure (buy for a long)
    opening=False -> we are reducing exposure  (sell for a long)
    """
    bps = cfg.side_cost_bps / 10_000.0
    buying = (side > 0) == opening
    return raw * (1 + bps) if buying else raw * (1 - bps)


def regulatory_fees(qty: int, notional: float, selling: bool, cfg: SimulatorConfig) -> float:
    fee = qty * cfg.cat_fee_per_share
    if selling:
        fee += notional * cfg.sec_fee_rate
        fee += min(qty * cfg.finra_taf_per_share, cfg.finra_taf_cap)
    return fee


# ─────────────────────────────────────────────────────────────────────────────
# Simulator
# ─────────────────────────────────────────────────────────────────────────────
def simulate_portfolio(
    calendar: Sequence[pd.Timestamp],
    frames: dict,
    signal_fn: Callable[[str, pd.Timestamp], Optional[SignalDecision]],
    cfg: SimulatorConfig = SimulatorConfig(),
    symbols: Optional[Iterable[str]] = None,
) -> SimulationResult:
    """Run one chronological cash-and-positions ledger over `calendar`.

    calendar  : canonical sessions (SPY's), ascending
    frames    : symbol -> DataFrame indexed by date with Open/High/Low/Close
    signal_fn : (symbol, date) -> SignalDecision | None, called only on a close
                for symbols flat for the whole session
    """
    if cfg.max_gross_exposure <= 0:
        raise ValueError("max_gross_exposure must be positive")

    calendar = list(calendar)
    # A signal on a close fills at the NEXT open. Filling at the same close is
    # lookahead. Intents created on the final session simply never fill.
    next_session = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}

    symbols = list(symbols if symbols is not None else frames.keys())
    cash = float(cfg.initial_cash)
    positions: dict = {}
    pending: list = []
    fills: list = []
    closed: list = []
    rejections: list = []
    daily: list = []

    trade_seq = 0
    cum_cost = 0.0
    cum_fees = 0.0
    peak_equity = cfg.initial_cash

    def bar(sym, date):
        df = frames.get(sym)
        if df is None or date not in df.index:
            return None
        return df.loc[date]

    def close_position(pos: Position, date, raw_exit: float, reason: str):
        nonlocal cash, cum_cost, cum_fees
        px = adverse_price(raw_exit, pos.side, opening=False, cfg=cfg)
        gross = px * pos.qty
        cost = abs(px - raw_exit) * pos.qty
        fee = regulatory_fees(pos.qty, gross, selling=pos.side > 0, cfg=cfg)
        cash += gross - fee if pos.side > 0 else -gross - fee
        cum_cost += cost
        cum_fees += fee
        pnl = (gross - fee) - pos.realized_cash_in if pos.side > 0 else None
        closed.append({
            "trade_id": pos.trade_id, "symbol": pos.symbol,
            "side": "LONG" if pos.side > 0 else "SHORT",
            "signal_date": str(pos.signal_date)[:10],
            "entry_date": str(pos.entry_date)[:10],
            "exit_date": str(date)[:10],
            "qty": pos.qty, "base_qty": pos.base_qty,
            "avg_entry_price": round(pos.avg_entry_price, 4),
            "exit_price": round(px, 4), "raw_exit_price": round(raw_exit, 4),
            "reason": reason,
            "pnl_dollar": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl / pos.realized_cash_in * 100, 3)
                       if pnl is not None and pos.realized_cash_in else None,
            "bars_held": pos.bars_held,
            "pyramid_fills": pos.pyramid_fill_count,
            "execution_cost": round(pos.accumulated_execution_cost + cost, 4),
            "fees": round(pos.accumulated_fees + fee, 4),
            "stop_had_advanced": pos.stop_has_advanced,
            "score": pos.score, "confidence": pos.confidence,
        })
        fills.append({"date": str(date)[:10], "symbol": pos.symbol, "kind": "EXIT",
                      "qty": pos.qty, "raw": round(raw_exit, 4), "fill": round(px, 4),
                      "reason": reason})
        positions.pop(pos.symbol, None)

    for date in calendar:
        exited_today = set()

        # ── 1. gap-through stops on positions that already existed ───────────
        for sym in list(positions):
            pos = positions[sym]
            b = bar(sym, date)
            if b is None:
                raise RuntimeError(
                    f"missing bar for held position {sym} on {date}; refusing to "
                    f"carry forward (would fabricate equity and hide a stop)")
            o = float(b["Open"])
            gapped = (pos.side > 0 and o <= pos.active_stop) or \
                     (pos.side < 0 and o >= pos.active_stop)
            if gapped:
                reason = "TRAIL" if pos.stop_has_advanced else "SL"
                close_position(pos, date, o, reason)
                exited_today.add(sym)

        # ── 2. fill pending intents at today's open, one shared snapshot ─────
        due = [i for i in pending if i["execute_at"] == date]
        pending = [i for i in pending if i["execute_at"] != date]
        if due:
            gross_mv = 0.0
            for sym, pos in positions.items():
                b = bar(sym, date)
                if b is not None:
                    gross_mv += abs(pos.qty) * float(b["Open"])
            e0 = cash + gross_mv          # equity snapshot shared by every candidate
            cap_room = cfg.max_gross_exposure * e0 - gross_mv
            avail = cash

            # deterministic: score desc, then symbol. WATCHLIST order cannot matter.
            due.sort(key=lambda i: (-i["score"], i["symbol"]))
            for intent in due:
                sym = intent["symbol"]
                if sym in exited_today:
                    rejections.append({"date": str(date)[:10], "symbol": sym,
                                       "reason": "EXITED_SAME_SESSION"})
                    continue
                b = bar(sym, date)
                if b is None:
                    rejections.append({"date": str(date)[:10], "symbol": sym,
                                       "reason": "NO_BAR"})
                    continue
                raw = float(b["Open"])
                px = adverse_price(raw, intent["side"], opening=True, cfg=cfg)
                if px <= 0:
                    continue

                if intent["kind"] == "ENTRY":
                    want = int(math.floor((e0 * intent["target_fraction"]) / px))
                else:
                    want = int(math.floor(intent["requested_qty"]))
                if want < 1:
                    rejections.append({"date": str(date)[:10], "symbol": sym,
                                       "reason": "BELOW_ONE_SHARE",
                                       "kind": intent["kind"]})
                    continue

                afford = int(math.floor(avail / px))
                room = int(math.floor(max(cap_room, 0.0) / px))
                qty = min(want, afford, room)
                if qty < 1:
                    rejections.append({
                        "date": str(date)[:10], "symbol": sym, "kind": intent["kind"],
                        "reason": "INSUFFICIENT_CAPACITY", "requested_qty": want,
                        "filled_qty": 0, "available_cash": round(avail, 2),
                        "one_share_cost": round(px, 2),
                        "gross_room": round(max(cap_room, 0.0), 2)})
                    continue

                notional = px * qty
                fee = regulatory_fees(qty, notional, selling=False, cfg=cfg)
                cash -= notional + fee
                avail = cash
                cap_room -= notional
                cum_cost += abs(px - raw) * qty
                cum_fees += fee
                fills.append({"date": str(date)[:10], "symbol": sym,
                              "kind": intent["kind"], "qty": qty,
                              "raw": round(raw, 4), "fill": round(px, 4),
                              "requested_qty": want})

                if intent["kind"] == "ENTRY":
                    trade_seq += 1
                    atr, mult = intent["atr"], intent["trail_multiple"]
                    stop = px - atr * mult if intent["side"] > 0 else px + atr * mult
                    levels = tuple(
                        px + intent["side"] * atr * 1.5 * (n + 1)
                        for n in range(cfg.max_pyramids)) if atr > 0 else ()
                    positions[sym] = Position(
                        trade_id=trade_seq, symbol=sym, side=intent["side"],
                        signal_date=intent["created_at"], entry_date=date,
                        qty=qty, base_qty=qty, avg_entry_price=px,
                        score=intent["score"], confidence=intent["confidence"],
                        target_fraction=intent["target_fraction"],
                        atr_at_signal=atr, trail_multiple=mult,
                        watermark=px, active_stop=stop,
                        pyramid_levels=levels,
                        accumulated_execution_cost=abs(px - raw) * qty,
                        accumulated_fees=fee,
                        realized_cash_in=notional + fee)
                else:
                    pos = positions.get(sym)
                    if pos is None:
                        continue
                    pos.avg_entry_price = (
                        (pos.avg_entry_price * pos.qty + px * qty) / (pos.qty + qty))
                    pos.qty += qty
                    pos.pyramid_fill_count += 1
                    pos.accumulated_execution_cost += abs(px - raw) * qty
                    pos.accumulated_fees += fee
                    pos.realized_cash_in += notional + fee

        # ── 3. intraday stops vs the stop active BEFORE this bar ─────────────
        for sym in list(positions):
            pos = positions[sym]
            b = bar(sym, date)
            if b is None:
                continue
            hit = (pos.side > 0 and float(b["Low"]) <= pos.active_stop) or \
                  (pos.side < 0 and float(b["High"]) >= pos.active_stop)
            if hit:
                reason = "TRAIL" if pos.stop_has_advanced else "SL"
                close_position(pos, date, pos.active_stop, reason)
                exited_today.add(sym)

        # ── 4. time exits at close ───────────────────────────────────────────
        for sym in list(positions):
            pos = positions[sym]
            pos.bars_held += 1
            if pos.bars_held >= cfg.max_holding_sessions:
                b = bar(sym, date)
                if b is not None:
                    close_position(pos, date, float(b["Close"]), "TIME")
                    exited_today.add(sym)

        # ── 5. mark to market ────────────────────────────────────────────────
        long_mv = short_mv = 0.0
        for sym, pos in positions.items():
            b = bar(sym, date)
            mv = abs(pos.qty) * float(b["Close"]) if b is not None else 0.0
            if pos.side > 0:
                long_mv += mv
            else:
                short_mv += mv
        net_mv = long_mv - short_mv
        equity = cash + net_mv
        peak_equity = max(peak_equity, equity)
        daily.append({
            "date": date, "cash": cash,
            "long_market_value": long_mv, "short_market_value": short_mv,
            "gross_exposure": long_mv + short_mv, "net_exposure": net_mv,
            "gross_exposure_pct": (long_mv + short_mv) / equity if equity else 0.0,
            "equity": equity,
            "drawdown_pct": equity / peak_equity - 1.0,
            "execution_cost_cum": cum_cost, "fees_cum": cum_fees,
            "open_positions": len(positions),
        })

        # ── 6. advance watermark/stop — takes effect NEXT session ────────────
        for pos in positions.values():
            b = bar(pos.symbol, date)
            if b is None or pos.atr_at_signal <= 0:
                continue
            if pos.side > 0:
                pos.watermark = max(pos.watermark, float(b["High"]))
                new_stop = pos.watermark - pos.atr_at_signal * pos.trail_multiple
                if new_stop > pos.active_stop:
                    pos.active_stop = new_stop
                    pos.stop_has_advanced = True
            else:
                pos.watermark = min(pos.watermark, float(b["Low"]))
                new_stop = pos.watermark + pos.atr_at_signal * pos.trail_multiple
                if new_stop < pos.active_stop:
                    pos.active_stop = new_stop
                    pos.stop_has_advanced = True

        # ── 7a. at most one pyramid layer per position per session ───────────
        for pos in positions.values():
            if pos.next_pyramid_index >= len(pos.pyramid_levels):
                continue
            b = bar(pos.symbol, date)
            if b is None:
                continue
            c = float(b["Close"])
            trigger = pos.pyramid_levels[pos.next_pyramid_index]
            crossed = (pos.side > 0 and c >= trigger) or (pos.side < 0 and c <= trigger)
            if not crossed:
                continue
            add = int(math.floor(pos.base_qty * cfg.pyramid_fraction))
            pos.next_pyramid_index += 1
            if add < 1:
                rejections.append({"date": str(date)[:10], "symbol": pos.symbol,
                                   "reason": "PYRAMID_BELOW_ONE_SHARE"})
                continue
            nxt = next_session.get(date)
            if nxt is None:
                continue
            pending.append({"kind": "PYRAMID", "symbol": pos.symbol, "side": pos.side,
                            "created_at": date, "execute_at": nxt,
                            "score": pos.score, "confidence": pos.confidence,
                            "requested_qty": add, "target_fraction": 0.0,
                            "atr": pos.atr_at_signal,
                            "trail_multiple": pos.trail_multiple})

        # ── 7b. entry signals for symbols flat all session ───────────────────
        for sym in symbols:
            if sym in positions or sym in exited_today:
                continue
            if any(i["symbol"] == sym for i in pending):
                continue
            b = bar(sym, date)
            if b is None:
                continue
            d = signal_fn(sym, date)
            if d is None or d.side == 0:
                continue
            if d.side < 0 and not cfg.allow_short:
                rejections.append({"date": str(date)[:10], "symbol": sym,
                                   "reason": "SHORT_DISABLED"})
                continue
            nxt = next_session.get(date)
            if nxt is None:
                continue
            pending.append({"kind": "ENTRY", "symbol": sym, "side": d.side,
                            "created_at": date, "execute_at": nxt,
                            "score": d.score, "confidence": d.confidence,
                            "target_fraction": d.target_fraction,
                            "requested_qty": None,
                            "atr": d.atr, "trail_multiple": d.trail_multiple})

    # ── forced liquidation at the final close ────────────────────────────────
    if calendar and positions:
        last = calendar[-1]
        for sym in list(positions):
            b = bar(sym, last)
            if b is not None:
                close_position(positions[sym], last, float(b["Close"]), "END_OF_DATA")

    curve = pd.DataFrame(daily).set_index("date") if daily else pd.DataFrame()
    if not curve.empty:
        curve["daily_return"] = curve["equity"].pct_change(fill_method=None)

    final_equity = float(curve["equity"].iloc[-1]) if not curve.empty else cfg.initial_cash
    return SimulationResult(
        equity_curve=curve, closed_trades=closed, fills=fills, rejections=rejections,
        config=cfg, final_equity=final_equity,
        total_execution_cost=cum_cost, total_fees=cum_fees)
