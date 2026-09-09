# Go / No-Go — verdict

Recorded 2026-09-09, against the criterion frozen in [`go-no-go.md`](go-no-go.md)
on 2026-08-30, on numbers produced by `true_backtest.py` on 2026-08-31
(`true_backtest_rapor.json`, `simulator: portfolio-v1`, `versiyon: v6`).

**Verdict: FAIL. The V6 strategy does not clear its own declared bar. Under
`go-no-go.md` the consequence is archive as a research artifact.**

## A defect in the process, recorded first

This verdict is written **nine days after the numbers existed**, and after the
RP-A candidate screen had already been specified, run and reported. The correct
sequence was to write it on 2026-08-31 and let it govern what came next.

Nothing was tuned in the gap — `true_backtest_rapor.json` is untouched since
10:23 on 2026-08-31 and the criterion file has not been edited since it was
committed — so the verdict below is not a rationalisation. But the delay is the
exact failure mode `go-no-go.md` was written to prevent, and a project whose
whole method is "declare the bar before you see the number" does not get to skip
recording the moment it fell short. If the pattern repeats, the criterion files
stop being evidence and become decoration.

## The criterion, and what was measured

PASS required **both**, after costs, at the 10 bps base case. Strategy and both
benchmarks are scored by the same `portfolio_simulator.equity_metrics` over the
same 441 sessions (2024-11-22 → 2026-08-28; the first 60 of the window's 501
sessions are `ISINMA_GUN` warmup and excluded from every series alike).

| Condition | Strategy | SPY buy & hold | Result |
|---|---|---|---|
| 1. Sharpe(strategy) > Sharpe(SPY) | **0.671** | 0.717 | **FAIL** |
| 2. MaxDD(strategy) shallower than SPY | **−9.86%** | −18.76% | PASS |

Condition 1 fails, so the conjunction fails.

Full picture at 10 bps:

| | Ann. return | Total return | Sharpe | Sortino | Max DD |
|---|---|---|---|---|---|
| V6 strategy | — | +23.50% | 0.671 | 0.930 | −9.86% |
| SPY buy & hold | — | +31.85% | 0.717 | 0.946 | −18.76% |
| Watchlist equal-weight | — | +47.48% | 0.740 | 1.084 | −23.99% |

Final equity $1,852.47 on $1,500.

## The 0 bps result, and why it is not a reprieve

| Cost | Sharpe | Max DD | Total return |
|---|---|---|---|
| 0 bps | **0.720** | −9.82% | +24.87% |
| 5 bps | 0.688 | −9.67% | +23.89% |
| **10 bps (base)** | **0.671** | −9.86% | +23.50% |
| 20 bps | 0.545 | −11.52% | +20.35% |

The strategy clears SPY's 0.717 at **0 bps only, by 0.003** — three thousandths
of a Sharpe point, in the run the criterion labels "a frictionless diagnostic,
never the headline". `go-no-go.md` lists "Passing only at 0 bps" by name under
**Explicitly not a pass**. The bar was written on 2026-08-30 in a way that closed
this specific escape route before anyone knew it would be the one available.

## Conformance of the run to the declared configuration

Checked because a verdict against a criterion the run did not actually implement
would be worthless.

| Declared | In `true_backtest_rapor.json` |
|---|---|
| Starting capital $1,500 | `initial_cash: 1500` ✓ |
| Max gross exposure 100% | `max_gross_exposure: 1.0` ✓ |
| 10 bps per side base case | `side_cost_bps: 10.0` ✓ |
| SHORT disabled | `allow_short: false` ✓ |
| `v6_sinyal` unchanged, HMM look-ahead included | `hmm_lookahead_present: true` ✓ |
| Identical window for strategy and benchmarks | 441 sessions, shared anchor ✓ |
| SPY recomputed at run time by the same function | `equity_metrics(spy_curve)` ✓ |

The criterion warned that the ad-hoc 2026-08-30 SPY figures (Sharpe 0.75, MDD
−18.8%) were indicative and **not** the comparison values. The recomputed
figures — 0.717 and −18.76% — are what condition 1 was judged against.

`hmm_lookahead_present: true` deserves emphasis. The criterion deliberately let
the known HMM look-ahead stand rather than fixing it first. So this is a strategy
that lost to SPY on risk-adjusted terms **while holding a look-ahead advantage**.
Removing the look-ahead can only move the number down.

## What actually happened inside the run

| | |
|---|---|
| Trades | 70 (22 winners, **31%**) |
| Exits | SL 34, TIME 21, TRAIL 14, END_OF_DATA 1 |
| Rejected signals | 200 |
| Rejection counters | threshold 4,411 · ADX 1,188 · conflict 540 · VIX risk-off 196 · Hurst 85 |
| Mean gross exposure | **15.1%** (median 11.8%) |
| Sessions fully in cash | 145 of 441 (**33%**) |
| Sessions above 50% exposure | 23 |

This explains condition 2 rather than crediting it. The drawdown is shallow
because the strategy is **absent** — a third of all sessions in cash, 15% average
exposure — not because it manages risk well while invested. `go-no-go.md`
anticipated this ("The strategy's one real strength in the flawed run was risk,
not return") and chose a risk-adjusted bar precisely so that absence alone could
not buy a pass. It did not.

The obvious next thought — that 23.5% on 15% average exposure would look
different scaled up — is a parameter change made in response to a failing result,
which the criterion forbids in the same sentence as everything else.

## Consequence

Per `go-no-go.md`: the system is archived as a research artifact. No parameter
tuning, no threshold adjustment, no re-run on a different window in search of a
pass.

This closes the V6 signal stack as a live-trading candidate. It does not delete
anything: `true_backtest.py`, `portfolio_simulator.py` and the agent stack remain
as the measurement apparatus that produced this result, and `portfolio_simulator`
went on to score the RP-A screen.

## What I would not conclude from this

**Not that the measurement is wrong.** The corrected simulator is the reason
these numbers are lower than the V5-era ones; three display-layer bugs had
previously flattered this system, and this run is the first honest one. A FAIL
from a trustworthy instrument is worth more than a PASS from a broken one.

**Not that SPY is therefore the answer.** SPY won this comparison at 0.717, which
is not itself an impressive Sharpe, and the watchlist equal-weight basket beat
both at 0.740. That basket is the C1 baseline now running forward — where, per
[`c1-forward-basis-divergence.md`](c1-forward-basis-divergence.md), it is being
run as **infrastructure validation, not as a strategy trial**, and where it too
would fail this criterion's drawdown leg at −23.99%.

**Not that this window settles anything.** As `go-no-go.md` states, the
2024-08-29 → 2026-08-28 window is burned as out-of-sample evidence: its results
had been seen and the system was being changed in response. A FAIL here is cheap
in a way a PASS would not have been. The honest reading is "the corrected
measurement is disqualifying on this window", not "the strategy has been proven
worthless in general".

**Not that the agents were useless.** 4,411 threshold rejections and 200 rejected
signals mean the filters were doing something. What is unproven is that what they
did was worth more than being in the index.

## Where this leaves the project

Two hypotheses have now been tested against bars declared before their results
were known, and both failed:

1. **V6 signal stack vs SPY on risk-adjusted terms** — this file. FAIL.
2. **Basket overlays vs the equal-weight baseline** — [`rp-a-results.md`](rp-a-results.md). FAIL, all five candidates, no t-statistic positive.

The one thing still running is the C1 forward ledger, whose declared purpose is
validating the ledger machinery on a baseline, not testing a strategy.

The open question is therefore what `candidate-screen-rp-a.md` already framed: a
different hypothesis class, or accepting that index-like exposure is the honest
answer. That decision is not made in this file, and this file does not prejudge
it — but it should now be made knowing that **both** measured hypotheses lost,
not one.
