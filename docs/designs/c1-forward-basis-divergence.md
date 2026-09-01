# C1 forward vs backtest — price-basis expectation, declared before the first row

Committed 2026-09-01, after the RP-A screen and **before the C1 forward ledger's
first scheduled run** (02:30 Europe/Istanbul, 2026-09-02). `PROGRAM_START` is
2026-09-01 and no forward equity row exists yet.

The point of this file is its timestamp, exactly as with
[`go-no-go.md`](go-no-go.md) and [`candidate-screen-rp-a.md`](candidate-screen-rp-a.md).
A basis expectation written after the ledger starts printing is a rationalisation.

## The concern this file answers

`true_backtest.veri_cek` calls yfinance with the default `auto_adjust=True`, so
its closes are split **and** dividend adjusted: dividends are implicitly
reinvested in the same stock, at the ex-date price, instantly and at no cost.
`c1_forward.py` deliberately calls `auto_adjust=False, actions=True` and credits
each dividend to **cash** on the ex-date, where it sits at 0% until the next
monthly rebalance redeploys it across all 17 names, paying 10 bps on the way in.

`c1_forward` is the correct one. Cash in a cash account does not reinvest itself
into the paying stock for free. The reasonable fear is therefore that the live
ledger will read *systematically below* an auto-adjusted backtest of the same
rule, and that the gap will be mistaken for underperformance.

## What was measured

The live `c1_forward.advance_ledger` code was run over the RP-A window, from
2024-12-02 (the first month-first session inside the scored window) to
2026-08-28 — 436 sessions — on both bases. Not a reimplementation: the same
function that will write tomorrow's row.

| Run | Total return | Sharpe | Final equity |
|---|---|---|---|
| A — auto-adjusted, the backtest basis | +51.250% | 0.853 | $2,268.6881 |
| B — split-adjusted + dividend credit, **the live basis** | +51.250% | 0.853 | $2,268.7184 |
| B′ — split-adjusted, dividend credit **removed** | +50.160% | 0.838 | $2,252.4222 |

- **B′ vs A: −1.090 pp.** This is the number that looks alarming, and it is the
  one an earlier draft of `rp-a-results.md` quoted. It is the gap between total
  return and bare price return.
- **B vs A: +0.000 pp.** The dividend cash credit recovers essentially all of the
  1.090 pp. Dividends credited over the window: $13.64 on $1,500, a 0.53%/yr
  gross yield — this basket is heavy in non-payers (PLTR, MSTR, IBIT, ASTS, TSLA,
  GLD, FXY, USO).
- The reinvestment lag costs almost nothing because the average idle period is
  about half a month against a 0.53%/yr dividend stream, and the redeployment
  rides an existing monthly rebalance rather than adding turnover.

**Control.** With the basis held fixed at A, the live `c1_forward` code and the
RP-A engine agree to **0.000e+00** on final equity. The basis comparison is
therefore not confounded by any semantic fork between the two implementations.

**Sign stability.** Residual measured from six different start dates:

| Start | Sessions | Residual | Rate |
|---|---|---|---|
| 2024-12-02 | 436 | +0.000 pp | +0.000 pp/yr |
| 2025-03-03 | 376 | +0.000 pp | +0.000 pp/yr |
| 2025-06-02 | 313 | +0.000 pp | +0.000 pp/yr |
| 2025-09-02 | 250 | +0.000 pp | +0.000 pp/yr |
| 2025-12-01 | 187 | +0.010 pp | +0.014 pp/yr |
| 2026-03-02 | 126 | +0.010 pp | +0.020 pp/yr |

The residual is not a drag. Where it is non-zero it is marginally **in the live
ledger's favour**, because holding a dividend in cash and redeploying it equally
beats reinvesting it in the paying stock whenever that stock then lags the basket.
The sign is indeterminate and the magnitude is noise.

## The prediction

> The price basis will contribute **no material divergence** between the C1
> forward ledger and an auto-adjusted replication of the same rule over the same
> sessions. Declared bound: **|drift| ≤ 0.10 pp per year**, against a measured
> 0.000–0.020 pp/yr.

`tests/test_c1_basis_equivalence.py` holds this bound, so it cannot regress
quietly. Evidence: `data/rpa_bars.csv` (basis A), `data/c1_basis_b_bars.csv`
(basis B).

## What would falsify it, and what to do then

If the forward ledger diverges from a basis-A replication of its own sessions by
more than 0.10 pp/yr, **the price basis is not the explanation.** Look for a
defect: a missed corporate action, a bar-gap recovery that re-based a position, a
dividend credited against the wrong split basis, or a genuine semantic fork
between the two rebalance paths.

The one thing not to do is reconcile `c1_forward` toward the backtest. That would
be making the correct implementation resemble the idealised one.

## Not covered by this measurement

**Splits.** The RP-A window contains no split in any of the 17 names, so the
split path is untested *by this comparison*. `c1_forward` carries its own split
tests (`tests/test_c1_forward.py`), and a split in the forward period exercises
`RUN_BOUNDARY_BASIS_TRANSITION` logic that has never been compared against an
auto-adjusted replication. If a split occurs, re-run this comparison rather than
assuming the bound still holds.

**Yield drift.** The bound is calibrated to a 0.53%/yr basket yield. A materially
higher-yielding universe would widen the idle-cash effect roughly in proportion.
The universe is frozen, so this is a note for any future universe change, not a
live risk.

**Different periods are not comparable at all.** The forward ledger runs on bars
from 2026-09-01 onward; the backtest ran on 2024-11-22 → 2026-08-28. Their returns
cannot be compared to each other directly under any basis. The only meaningful
comparison — and the one this file's bound applies to — is the forward ledger
against a basis-A replication of *the forward ledger's own sessions*.

## Status of the C1 forward program

Worth restating while this is being written down. `rp-a-results.md` declines to
conclude that C1 is a good strategy: it beat SPY on return and Sharpe but took a
deeper drawdown, and under `go-no-go.md`'s bar it would fail the drawdown leg.

The forward program's value is therefore **not** that it is testing a strategy
believed to work. It is the project's only genuine out-of-sample evidence stream
and the running verification of the ledger machinery — bar-gap recovery, split
handling, dividend crediting, idempotent catch-up. That is a good reason to run
it. It should be named accurately: infrastructure validation on a baseline, not a
strategy trial.
