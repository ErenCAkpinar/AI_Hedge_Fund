"""Newey-West and Holm machinery for the RP-A screen's PASS condition 3.

The spec asks for a one-sided test on the daily difference series ``r_Ci - r_C1``,
Newey-West corrected at lag 5 and Holm-adjusted across the four candidates at
family alpha = 0.10.

Every standard error here is computed **twice**: once through
``statsmodels.OLS(...).fit(cov_type="HAC", maxlags=5)`` and once through a
hand-rolled Bartlett kernel that shares no code with it. This repo has three
times been misled by a number that was only produced one way, so the two are
asserted to agree in ``tests/test_rpa_stats.py`` rather than trusted.

Convention, established empirically against statsmodels 0.15.0 rather than
assumed: for ``cov_type="HAC"`` statsmodels applies **no** small-sample
correction (``use_correction=False``) and reports normal, not t, inference
(``use_t=False``). The hand-rolled path therefore uses the plain textbook
estimator, and the two agree to machine precision with no fudge factor.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

HAC_MAXLAGS = 5
FAMILY_ALPHA = 0.10


class RpaStatsError(RuntimeError):
    """A test statistic cannot be formed from the series given."""


def bartlett_hac_se(diff: Sequence[float], maxlags: int = HAC_MAXLAGS) -> float:
    """Newey-West standard error of the sample mean, written out by hand.

    S = gamma_0 + 2 * sum_{j=1..L} (1 - j/(L+1)) * gamma_j,  Var(mean) = S / n.

    For a regression on a constant the sandwich (X'X)^-1 S_hat (X'X)^-1 collapses
    to exactly S/n, which is why this is comparable to the statsmodels path.
    """
    values = np.asarray(diff, dtype=float)
    n = values.size
    if n <= maxlags + 1:
        raise RpaStatsError(f"need more than {maxlags + 1} observations, got {n}")
    resid = values - values.mean()
    total = float(resid @ resid) / n
    for lag in range(1, maxlags + 1):
        weight = 1.0 - lag / (maxlags + 1.0)
        total += 2.0 * weight * float(resid[lag:] @ resid[:-lag]) / n
    if total < 0:
        raise RpaStatsError("negative HAC variance; the series is degenerate")
    return math.sqrt(total / n)


def statsmodels_hac_se(diff: Sequence[float], maxlags: int = HAC_MAXLAGS) -> float | None:
    """Same quantity via statsmodels, or None when statsmodels is unavailable."""
    try:
        import statsmodels.api as sm
    except ImportError:
        return None
    values = np.asarray(diff, dtype=float)
    fit = sm.OLS(values, np.ones((values.size, 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags}
    )
    return float(np.sqrt(fit.cov_params()[0, 0]))


def hac_mean_test(diff: Sequence[float], maxlags: int = HAC_MAXLAGS) -> dict:
    """One-sided HAC test of H0: mean(diff) <= 0 against H1: mean(diff) > 0."""
    values = np.asarray(diff, dtype=float)
    if not np.isfinite(values).all():
        raise RpaStatsError("difference series contains non-finite values")
    mean_value = float(values.mean())
    # Degeneracy is read off the data, not off a standard error that may land on a
    # denormal: a constant series has no dispersion however it is summed.
    if values.size and values.max() == values.min():
        # A candidate that exactly reproduces C1 is a relabelled C1, and supplies
        # no evidence against the null, so condition 3 fails rather than the run
        # crashing. A constant *positive* difference is pathological rather than
        # degenerate, and reporting p=0 for it would fabricate significance.
        if mean_value > 0:
            raise RpaStatsError(
                "difference series is constant and positive; refusing to "
                "fabricate infinite significance from zero dispersion"
            )
        return {
            "n": int(values.size), "mean": mean_value,
            "se_hand_rolled": 0.0, "se_statsmodels": 0.0, "se_used": 0.0,
            "se_path": "degenerate", "se_agreement_rel": 0.0,
            "t_stat": 0.0, "p_one_sided": 1.0, "maxlags": maxlags,
        }
    hand = bartlett_hac_se(values, maxlags)
    library = statsmodels_hac_se(values, maxlags)
    used, path = (library, "statsmodels") if library is not None else (hand, "hand-rolled")
    if used <= 0:
        raise RpaStatsError("HAC standard error collapsed to zero; refusing to divide by it")
    mean = float(values.mean())
    t_stat = mean / used
    return {
        "n": int(values.size),
        "mean": mean,
        "se_hand_rolled": hand,
        "se_statsmodels": library,
        "se_used": used,
        "se_path": path,
        "se_agreement_rel": (abs(hand - library) / library) if library else None,
        "t_stat": t_stat,
        # use_t=False under statsmodels' HAC, so the reference law is the normal.
        "p_one_sided": float(stats.norm.sf(t_stat)),
        "maxlags": maxlags,
    }


def holm(pvalues: Mapping[str, float], alpha: float = FAMILY_ALPHA) -> dict[str, dict]:
    """Holm step-down adjustment across the family, with the raw p kept alongside.

    Adjusted p for the k-th smallest raw p is max over j <= k of (m - j + 1) * p_(j),
    capped at 1. The running maximum is what enforces monotonicity, and it is the
    reason a candidate cannot be rescued by a later, larger p.
    """
    if not pvalues:
        return {}
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(ordered)
    out: dict[str, dict] = {}
    running = 0.0
    for rank, (name, raw) in enumerate(ordered, start=1):
        running = max(running, min(1.0, (m - rank + 1) * raw))
        out[name] = {
            "p_raw": float(raw),
            "p_holm": float(running),
            "rank": rank,
            "family_size": m,
            "alpha": alpha,
            "reject_null": bool(running <= alpha),
        }
    return out
