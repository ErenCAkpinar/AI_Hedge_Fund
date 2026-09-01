# Candidate Screen RP-A — run report

Run 2026-09-01 against the criterion frozen in
[`candidate-screen-rp-a.md`](candidate-screen-rp-a.md), which was committed
2026-08-31, before the engine existed and before any candidate result was known.

**Verdict: FAIL. No candidate passes. The basket-overlay hypothesis class is
closed for this universe on this window.**

Engine: `rpa_screen.py`. Statistics: `rpa_stats.py`. Evidence:
`data/rpa_bars.csv`, `data/rpa_equity.csv`, `data/rpa_benchmarks.csv`,
`data/rpa_results.json`. Reproduce with `.venv-backtest/bin/python rpa_screen.py --run`.

---

## 1. Engine validation — PASS

The spec makes the run VOID unless the engine first reproduces a known-good
independent implementation. Nothing below this line would be reportable otherwise.

| | engine | `true_backtest.buy_hold_curve` | gap | bound |
|---|---|---|---|---|
| Total return | +47.48% | +47.48% | **0.0000 pp** | 0.5 pp |
| Sharpe | 0.740 | 0.740 | **0.0000** | 0.01 |
| Max drawdown | −23.99% | −23.99% | — | — |
| Max abs equity difference, all 441 sessions | — | — | **9.09e-13** | — |

The agreement is to floating point, not to the tolerance. The tests hold the
tight bound as well as the spec's, so a later ledger regression is caught as a
bug rather than absorbed by a tolerance that was sized for a different question.

### One judgement call inside the gate, recorded because it decided VOID vs PASS

The spec names two gate settings — zero costs, no rebalancing — but not an entry
convention. `buy_hold_curve` establishes its book at the anchor's **close**; the
candidates' frozen rule fills at the **next open**. Run with the candidates'
convention the engine misses by −1.12 pp and −0.012 Sharpe, both outside the
bound, because the equal-weight basket gapped **+0.71% overnight** from the close
of 2024-11-22 to the open of 2024-11-25. That outcome turns on one day's gap at
the anchor rather than on any property of the engine.

Gate mode therefore meets `buy_hold_curve` on its own entry convention, which the
operator selected. Candidates are unaffected and keep the frozen next-open fill.
`EngineConfig.fill_at` defaults to `NEXT_OPEN`, and a test asserts it, so no
candidate can inherit the gate's convention.

---

## 2. Metrics across the cost grid

441 scored sessions, 2024-11-22 → 2026-08-28, $1,500, cash long-only, 100% max
gross. All figures from `portfolio_simulator.equity_metrics`, the only function
permitted to compute Sharpe or drawdown, applied to a daily mark-to-market curve
with flat days included.

**Total return**

| Candidate | 0 bps | 5 bps | 10 bps | 20 bps |
|---|---|---|---|---|
| C1 `EW_MONTHLY` | +49.80% | +49.60% | **+49.40%** | +48.99% |
| C2 `TREND_SPY200` | +20.84% | +20.33% | **+19.82%** | +18.81% |
| C3 `XMOM_TOP5` | +16.61% | +15.89% | **+15.18%** | +13.77% |
| C4 `VOLTGT_EW` | +23.08% | +22.90% | **+22.71%** | +22.34% |
| C5 `TREND_XMOM` | −15.17% | −15.87% | **−16.57%** | −17.94% |

**Sharpe**

| Candidate | 0 bps | 5 bps | 10 bps | 20 bps |
|---|---|---|---|---|
| C1 `EW_MONTHLY` | 0.824 | 0.821 | **0.818** | 0.812 |
| C2 `TREND_SPY200` | 0.380 | 0.368 | **0.357** | 0.334 |
| C3 `XMOM_TOP5` | 0.295 | 0.286 | **0.277** | 0.259 |
| C4 `VOLTGT_EW` | 0.498 | 0.493 | **0.488** | 0.477 |
| C5 `TREND_XMOM` | −0.225 | −0.239 | **−0.252** | −0.279 |

**At the 10 bps base case**

| Candidate | Ann. return | Sharpe | Sortino | Max DD | Cost paid | Turnover | Sessions in cash |
|---|---|---|---|---|---|---|---|
| C1 `EW_MONTHLY` | +25.85% | 0.818 | 1.174 | −23.07% | $4.76 (0.32%) | $4,763 | 1 |
| C2 `TREND_SPY200` | +10.91% | 0.357 | 0.465 | −18.06% | $13.02 (0.87%) | $13,018 | 55 |
| C3 `XMOM_TOP5` | +8.43% | 0.277 | 0.404 | −31.93% | $21.29 (1.42%) | $21,287 | 1 |
| C4 `VOLTGT_EW` | +12.44% | 0.488 | 0.676 | −12.60% | $4.94 (0.33%) | $4,938 | 1 |
| C5 `TREND_XMOM` | −9.86% | −0.252 | −0.332 | −31.77% | $22.17 (1.48%) | $22,166 | 55 |

The single cash session shown for C1, C3 and C4 is the anchor itself: every
candidate decides at the anchor close and fills at the next open, so all five are
flat on the first scored session by construction.

---

## 3. The four PASS conditions, candidate by candidate

At the 10 bps base case, against C1. Condition 3 is a one-sided HAC test at lag 5
on the daily difference series `r_Ci − r_C1` (n = 440), Holm-adjusted across the
four candidates at family α = 0.10.

### C2 `TREND_SPY200` — FAIL (1 of 4)

| # | Condition | Value | Required | |
|---|---|---|---|---|
| 1 | Sharpe > C1 | 0.357 | > 0.818 | FAIL |
| 2 | Ann. active return > 0 | −13.73% | > 0 | FAIL |
| 3 | Holm-adjusted p ≤ 0.10 | 1.0000 (raw 0.9305, t = −1.480) | ≤ 0.10 | FAIL |
| 4 | Max DD ≤ 1.25 × C1 | 18.06% | ≤ 28.84% | PASS |

### C3 `XMOM_TOP5` — FAIL (0 of 4)

| # | Condition | Value | Required | |
|---|---|---|---|---|
| 1 | Sharpe > C1 | 0.277 | > 0.818 | FAIL |
| 2 | Ann. active return > 0 | −10.31% | > 0 | FAIL |
| 3 | Holm-adjusted p ≤ 0.10 | 1.0000 (raw 0.7460, t = −0.662) | ≤ 0.10 | FAIL |
| 4 | Max DD ≤ 1.25 × C1 | 31.93% | ≤ 28.84% | FAIL |

### C4 `VOLTGT_EW` — FAIL (1 of 4)

| # | Condition | Value | Required | |
|---|---|---|---|---|
| 1 | Sharpe > C1 | 0.488 | > 0.818 | FAIL |
| 2 | Ann. active return > 0 | −13.30% | > 0 | FAIL |
| 3 | Holm-adjusted p ≤ 0.10 | 1.0000 (raw 0.9676, t = −1.847) | ≤ 0.10 | FAIL |
| 4 | Max DD ≤ 1.25 × C1 | 12.60% | ≤ 28.84% | PASS |

### C5 `TREND_XMOM` — FAIL (0 of 4)

| # | Condition | Value | Required | |
|---|---|---|---|---|
| 1 | Sharpe > C1 | −0.252 | > 0.818 | FAIL |
| 2 | Ann. active return > 0 | −30.39% | > 0 | FAIL |
| 3 | Holm-adjusted p ≤ 0.10 | 1.0000 (raw 0.9643, t = −1.803) | ≤ 0.10 | FAIL |
| 4 | Max DD ≤ 1.25 × C1 | 31.77% | ≤ 28.84% | FAIL |

**The Holm correction never had to do any work.** Every t-statistic is negative,
so every candidate underperformed C1 on the mean daily difference, and no raw
one-sided p is below 0.74 before adjustment. Holm raises all four to 1.0000, but
the screen would have failed identically without it. Raw and adjusted p are both
reported above so the correction is auditable, as the spec asks.

**The result does not depend on costs.** No candidate passes at any point on the
grid, including the frictionless 0 bps diagnostic, where the best any candidate
manages is one condition of four.

### Which HAC path was used

`statsmodels` 0.15.0 installed cleanly into `.venv-backtest`, so
`OLS(...).fit(cov_type="HAC", maxlags=5)` is the reported path. Every standard
error was independently recomputed by a hand-rolled Bartlett kernel that shares
no code with it. **Worst disagreement across all sixteen candidate-by-cost
combinations: 8.8e-16 relative.**

The statsmodels convention was established by probing it, not assumed: for
`cov_type="HAC"` it applies no small-sample correction and reports normal rather
than t inference, so the hand-rolled path is the plain textbook estimator and
needs no correction factor. `run_screen` refuses to write a report at all if the
second path is missing or the two disagree by more than 1e-10.

---

## 4. C1 against SPY — did the basket carry the return?

**Yes, on return and on Sharpe; no, on drawdown.**

| | C1 `EW_MONTHLY` (10 bps) | SPY | |
|---|---|---|---|
| Total return | +49.40% | +31.85% | C1 by 17.6 pp |
| Annualised | +25.85% | +17.16% | C1 |
| Sharpe | 0.818 | 0.717 | C1 |
| Sortino | 1.174 | 0.946 | C1 |
| Max drawdown | −23.07% | −18.76% | **SPY** |

SPY is a frictionless single purchase rebased at the anchor close, built the way
`true_backtest` builds it and scored by the same `equity_metrics`. It pays no
execution cost while C1 pays $4.76, an asymmetry in SPY's favour that C1 clears
anyway.

Two things follow. The 17-symbol universe did outperform the index over this
window, which is consistent with the finding that motivated this screen. But it
did so by taking more drawdown, and under the bar `go-no-go.md` set for V6 —
Sharpe above SPY **and** drawdown shallower than SPY — C1 would pass the first
test and fail the second.

Separately, C1 beat a pure zero-cost buy-and-hold of the same basket by **+2.32 pp**
at 0 bps. The monthly rebalance is the one overlay in this screen that added
anything, and it is the do-nothing baseline rather than a candidate.

---

## 5. Findings

**The frozen 60-session warm-up does not cover two of the candidates.** C4's
`vol60` is exactly available at the anchor, which is what the 60 sessions were
sized for. C2 needs 200 SPY sessions and C3 needs 126. Computed strictly inside
the frozen window, the SPY 200-SMA does not exist until index 199 (2025-06-17),
which would leave C2 and C5 with no trend filter for 139 of the 441 scored
sessions and C3 with no ranking for 66. The operator's resolution was to source
warm-up from bars **before** the window, leaving the traded and scored windows at
exactly 501/441 and changing no frozen parameter. Re-fetching from an earlier
start does not disturb in-window adjusted prices (max relative drift 3e-7,
float32 storage noise). Every symbol has its full lookback at the anchor; the
youngest, IBIT, has 219 pre-anchor sessions against the 127 required, so no
candidate ever ran on a truncated lookback and the scored window was never
shortened.

**The backtest C1 and the live `c1_forward` C1 sit on different price bases, and
it turns out not to matter. (Corrected 2026-09-01 — see below.)**
`true_backtest.veri_cek` takes yfinance's default `auto_adjust=True`, so its
closes are split *and* dividend adjusted. `c1_forward.py` deliberately uses
`auto_adjust=False` plus an explicit dividend cash credit held idle until the next
monthly rebalance. The operator chose the auto-adjusted basis, because the gate is
defined against `true_backtest.buy_hold_curve` and that is what `true_backtest`
feeds it.

> **Correction.** This section first stated that "the two bases differ by 1.04 pp
> of basket total return — twice the gate's tolerance", and treated that as a
> divergence between the two C1 implementations. That was the wrong comparison.
> The 1.04 pp figure is the gap between dividend-adjusted prices and *raw price
> return with no dividend credit at all* — which is not what `c1_forward` does.
> `c1_forward` credits the dividend to cash, and that credit recovers the gap
> almost exactly.
>
> Measured by running the live `c1_forward` code itself over 436 sessions of this
> window on both bases: dropping the dividend credit costs **−1.09 pp**, and
> crediting it returns **+0.00 pp** against the auto-adjusted basis. Residual
> across six sub-period start dates: **0.000 to +0.020 pp/yr**, and marginally in
> the live ledger's favour rather than against it. So the earlier claim that the
> backtest basis is the optimistic one is **not supported**. See
> [`c1-forward-basis-divergence.md`](c1-forward-basis-divergence.md), declared
> before the forward ledger's first row, and `tests/test_c1_basis_equivalence.py`.

The two implementations share the monthly-rebalance and cost-solver semantics —
the engine's solver reduces term for term to `c1_forward._invested_value` at full
investment, and a control test runs the live code on the backtest basis and lands
within 1e-9 of the RP-A engine — and their dividend accounting, though written
differently, is economically equivalent at this basket's yield. No candidate
number in this report is affected: the whole screen ran on one basis.

**Cash earns nothing while Sharpe is measured against a 5% risk-free rate.** C4
holds 34% cash on average and C2/C5 hold 100% cash for 55 sessions, all at 0%,
while `equity_metrics` computes excess return over `risk_free_annual=0.05`. This
penalises de-risking candidates twice. It is faithful to the frozen config, which
specifies a cash account and names `equity_metrics` as the scorer, and it does
**not** drive the result: crediting idle cash at 5% moves C4's Sharpe from 0.488
to 0.591 and C2's from 0.357 to 0.386, against C1's 0.818. Recorded as a
diagnostic; it is not a reported metric and does not change any verdict.

**Why each overlay lost, mechanically.** C2 and C5 were forced to cash for three
runs — 2025-03-11→03-24, 2025-03-27→05-12 and 2026-03-23→04-08 — during which the
equal-weight basket returned **+9.28%, +10.44% and +3.59%**. The 200-SMA filter
exited after the drawdown and re-entered after the rebound, which is the standard
trend-following failure on V-shaped recoveries and accounts for essentially the
whole C1−C2 gap. C3 concentrated into the top five momentum names and held MSTR
through its peak into a −70% decline. C5 compounds both.

**Overlay tracking has a one-session lag that is the frozen fill rule, not a
modelling choice.** An overlay re-entering mid-month buys the base's
previous-close proportions at the next open, inheriting one session of dispersion
— bounded at 3.1e-3, confined to the three re-entries, and reset at the next
monthly rebalance. The tests assert exact equality everywhere else.

---

## 6. What I would not conclude from this

**The window is burned, so this is triage on contaminated data.** The bars existed
before the spec was written, the SPY and equal-weight figures had been seen, and
V6 was archived on this same window. A fail here is cheap in a way a pass would
not have been — but it is still one window, one universe, and one 441-session
sample.

**I would not conclude that trend filters, cross-sectional momentum or volatility
targeting do not work.** What was tested is four specific parameterisations, over
a period containing two sharp V-shaped recoveries that are close to the worst
possible environment for a 200-day trend filter. The failure is a fact about these
overlays on this window, not about the technique class.

**I would not conclude that C1 is a good strategy.** C1 won this screen by being
the thing the overlays failed to improve on. It beat SPY on return and Sharpe
while taking a deeper drawdown, and under `go-no-go.md`'s bar it would fail the
drawdown leg. Its +25.85% annualised is concentrated-basket beta over a strong
market, not demonstrated skill, and this screen contains no out-of-sample
evidence that the universe selection generalises.

**I would not read the statistics as evidence of anything except absence.** Every
t-statistic is negative and no raw p is below 0.74; the correct reading is "no
detectable edge", not "an edge that failed significance". The test had no power
problem to blame — it simply found nothing to find. Equally, C1's margin over the
overlays is not itself a tested claim: the screen tests Ci against C1, not C1
against Ci.

**I would not treat the 20% economic hurdle as engaged.** By the V7 plan's own
`hurdle(C, O) = max(3%, 3 × O / C, $300 / C)`, a $1,500 account needs roughly 20%
annualised net alpha. Nothing here produced alpha of any sign worth measuring, so
capital adequacy was never reached as a question.

**I would not propose a sixth candidate.** Per the spec, total failure closes the
basket-overlay hypothesis class for this universe and window. The next move is a
different hypothesis class, or accepting that index-like exposure is the honest
answer — not a fifth round of parameter search.
