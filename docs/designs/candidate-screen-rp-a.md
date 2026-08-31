# Candidate Screen RP-A — criterion declared BEFORE any candidate is run

Committed 2026-08-31, after V6 was archived and **before** the weight engine exists,
before any candidate has been implemented, and before any candidate result is known.

The point of this file is its timestamp, exactly as with `go-no-go.md`. V6 failed a
criterion that had been frozen in advance. This screen inherits that discipline.

## Why this screen exists

The corrected V6 measurement produced one finding that is more useful than the failure
itself: **equal-weight buy-and-hold of the same 17-symbol universe beat every version of
the strategy**, on return and on risk-adjusted return. The universe selection was not the
problem. The signal, sizing and exit machinery layered on top of it subtracted value.

So the question this screen asks is deliberately narrow:

> Holding the same basket, does any simple exposure overlay beat just holding it?

## Frozen configuration

| | |
|---|---|
| Window | 2024-08-29 → 2026-08-28, identical for every series |
| Sessions | 501 total, 441 scored (first 60 are warm-up, excluded from metrics) |
| Universe | the existing 17-symbol `WATCHLIST`, unchanged |
| Account | cash, long-only, no margin, no shorting |
| Max gross exposure | 100% |
| Capital | $1,500 |
| Execution cost | **10 bps per side** base case; grid 0 / 5 / 10 / 20 reported |
| Fills | signal on a session close, fill at the NEXT open |
| Metrics | `portfolio_simulator.equity_metrics`, daily mark-to-market, flat days included |

## Candidates — parameters frozen here, not tunable later

- **C1 `EW_MONTHLY`** — all 17 symbols at equal weight, rebalanced to equal weight on the
  first session of each month, always 100% invested. Pays costs.
- **C2 `TREND_SPY200`** — C1 weights while SPY's previous close is above its trailing
  200-session simple moving average; otherwise 100% cash. Evaluated every session close.
- **C3 `XMOM_TOP5`** — monthly, rank the 17 by trailing 126-session total return through
  the previous close; hold the top 5 at equal weight, fully invested.
- **C4 `VOLTGT_EW`** — C1 weights scaled by `min(1.0, 0.15 / vol60)`, where `vol60` is the
  trailing 60-session annualised realised volatility of the C1 basket. Rebalanced monthly.
  Remainder held as cash.
- **C5 `TREND_XMOM`** — C3 holdings, forced to 100% cash whenever the C2 trend filter is off.

No candidate may be added, removed, or reparameterised after this commit. No sixth
candidate may be introduced because the first five disappoint.

## Comparators

- **C2–C5 are measured against C1**, not against SPY. C1 is the honest do-nothing
  alternative: same basket, same costs, no cleverness.
- **C1 is measured against SPY**, to record whether the basket itself carried the return.

## PASS requires ALL FOUR, at the 10 bps base case

For candidate `Ci`, `i ∈ {2,3,4,5}`, against C1:

1. `Sharpe(Ci) > Sharpe(C1)`
2. Annualised active return `252 × mean(r_Ci − r_C1) > 0`
3. One-sided t-test on the daily difference series `r_Ci − r_C1`, Newey-West corrected
   (lag 5), **Holm-adjusted across the four candidates at family α = 0.10**
4. `MaxDrawdown(Ci)` no worse than `1.25 × MaxDrawdown(C1)`

Condition 3 is the one that makes this a screen rather than a leaderboard. Four candidates
on a burned window will produce a numerical winner whether or not any edge exists.

## Engine validation — the run is VOID if this fails

The weight engine is new code, and this project has been misled three times by
display-layer bugs. Before any candidate result is reported, the engine must reproduce a
known-good independent implementation:

> Running the engine with **zero costs and no rebalancing** must reproduce
> `true_backtest.buy_hold_curve` on the same universe and anchor to within
> **0.5 percentage points of total return and 0.01 of Sharpe.**

If it does not, the run is reported as VOID and no candidate number is quoted.

## What a PASS means, and what it does not

A pass means: **carry that candidate forward to genuine out-of-sample evidence.** It does
not mean the edge is proven, and it does not authorise capital.

The 2024-08-29 → 2026-08-28 window is already burned. Its bars existed before this file
was written, its SPY and equal-weight figures have been seen, and V6 was archived on it.
A pass here is a triage result on contaminated data. Real evidence requires bars that did
not exist when this file was committed.

## What a total FAIL means

If no candidate passes, the finding is recorded and the basket-overlay hypothesis class is
closed for this universe on this window. The next move is a different hypothesis class, or
accepting that index-like exposure is the honest answer. It is **not** a fifth round of
parameter search.

## Stated economic limit, separate from the research question

By the V7 plan's own hurdle, `hurdle(C, O) = max(3%, 3 × O / C, $300 / C)`, a $1,500
account requires roughly **20% annualised net alpha** to be economically relevant. None of
these candidates is expected to clear that bar at that capital. This screen answers
"is there measurable edge", not "is $1,500 worth deploying". Capital adequacy is a
separate decision and must not be smuggled into a pass.

## Explicitly not a pass

- Beating C1 on raw return while losing on risk-adjusted terms.
- Passing only at 0 bps.
- Passing only before the Holm correction.
- Passing after any parameter change made in response to seeing a result.
- Any number produced by an engine that failed the validation above.
