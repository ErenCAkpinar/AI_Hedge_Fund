# Go / No-Go Criterion — declared BEFORE the corrected simulator exists

Committed 2026-08-30, before `portfolio_simulator.py` was written and before any
corrected result was known. Decisions D10, D13, D14.

The point of this file is its timestamp. If the criterion is chosen after seeing the
number, it is not a criterion, it is a rationalisation.

## Configuration under test

| | |
|---|---|
| Account | cash, no margin |
| Starting capital | $1,500 |
| Max gross exposure | **100%** (D13) — a cash account cannot exceed it |
| Execution cost | **10 bps per side** base case (5 bps half-spread + 5 bps slippage), 0 commission |
| Sensitivity grid | 0 / 5 / 10 / 20 bps per side. **0 bps is a frictionless diagnostic, never the headline** |
| SHORT | disabled (`allow_short=False`) |
| Signal logic | `v6_sinyal` unchanged, including its known HMM look-ahead |
| Window | identical start and end dates for strategy and every benchmark |

## PASS requires BOTH, after costs, at the 10 bps base case

1. **Sharpe(strategy) > Sharpe(SPY)**
2. **MaxDrawdown(strategy) shallower than MaxDrawdown(SPY)**

Both computed from a **daily mark-to-market equity curve** using the identical function
for strategy and benchmark. Flat days stay in the series. No per-trade returns, no
`sqrt(252)` applied to trade counts.

The SPY reference must be **recomputed at run time by that same function**. The figures
from the 2026-08-30 ad-hoc script (SPY Sharpe 0.75, max drawdown -18.8%) are indicative
only and are NOT the comparison values.

## FAIL means archive

If either condition fails, the system is archived as a research artifact. No parameter
tuning, no threshold adjustment, no re-run on a different window in search of a pass.

## Explicitly not a pass

- Beating SPY on raw return while losing on risk-adjusted terms.
- Passing only at 0 bps.
- Passing only on a window other than the declared one.
- Passing after any change to `v6_sinyal` made in response to seeing a failing result.

## Known contamination

The 2024-08-29 → 2026-08-28 window is **already burned as out-of-sample evidence**. Its
results have been seen and the system is being changed in response. A pass here means
"the corrected measurement is not disqualifying", not "the edge is proven". Genuine
out-of-sample evidence requires bars that did not exist when this was written.

## Why this bar

The strategy's one real strength in the flawed run was risk, not return: ~18% average
exposure and a shallower drawdown than SPY. A raw-return bar would ignore that. Requiring
it to beat the index on risk-adjusted terms is the standard an allocator would apply, and
it is the lowest bar that is not self-deception.
