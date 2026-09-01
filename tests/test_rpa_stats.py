"""The HAC standard error must be right, and it must be right twice.

Three times this repo has believed a number that was only computed one way. The
central test here is that an independent hand-rolled Bartlett kernel and
statsmodels' HAC agree; the rest pin down the properties that make the agreement
meaningful rather than coincidental.
"""

import math

import numpy as np
import pytest

from rpa_stats import (
    FAMILY_ALPHA,
    HAC_MAXLAGS,
    RpaStatsError,
    bartlett_hac_se,
    hac_mean_test,
    holm,
    statsmodels_hac_se,
)


def ma_series(n, thetas, seed, drift=0.0):
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n + len(thetas))
    out = []
    for i in range(n):
        value = noise[i + len(thetas)]
        for k, theta in enumerate(thetas, start=1):
            value += theta * noise[i + len(thetas) - k]
        out.append(value)
    return np.asarray(out) + drift


@pytest.mark.parametrize("n", [40, 120, 440])
@pytest.mark.parametrize("thetas", [(), (0.6,), (0.6, 0.3, -0.2), (0.9, 0.8, 0.7, 0.6, 0.5)])
def test_hand_rolled_and_statsmodels_hac_agree(n, thetas):
    series = ma_series(n, thetas, seed=hash((n, thetas)) % 2**32, drift=0.03)
    hand = bartlett_hac_se(series, HAC_MAXLAGS)
    library = statsmodels_hac_se(series, HAC_MAXLAGS)
    assert library is not None, "statsmodels is installed for this run"
    assert hand == pytest.approx(library, rel=1e-12, abs=0.0)


def test_reported_agreement_is_tight_and_the_path_is_named():
    result = hac_mean_test(ma_series(440, (0.5, 0.25), seed=11, drift=0.02))
    assert result["se_path"] == "statsmodels"
    assert result["se_agreement_rel"] < 1e-12
    assert result["se_used"] == result["se_statsmodels"]


def test_zero_lag_hac_is_the_plain_iid_standard_error():
    series = ma_series(200, (), seed=3)
    resid = series - series.mean()
    plain = math.sqrt((resid @ resid) / series.size / series.size)
    assert bartlett_hac_se(series, maxlags=0) == pytest.approx(plain, rel=1e-12)


def test_positive_autocorrelation_widens_the_standard_error():
    """If lag-5 correction did nothing, condition 3 would be far too easy to pass."""
    series = ma_series(400, (0.8, 0.7, 0.6, 0.5, 0.4), seed=5)
    assert bartlett_hac_se(series, HAC_MAXLAGS) > bartlett_hac_se(series, maxlags=0)


def test_one_sided_p_follows_the_sign_of_the_mean():
    strong = hac_mean_test(ma_series(400, (0.2,), seed=17, drift=0.5))
    weak = hac_mean_test(ma_series(400, (0.2,), seed=17, drift=-0.5))
    assert strong["p_one_sided"] < 0.01
    assert weak["p_one_sided"] > 0.99
    assert strong["mean"] > 0 > weak["mean"]


def test_hac_rejects_a_series_it_cannot_test():
    with pytest.raises(RpaStatsError):
        bartlett_hac_se([0.1, 0.2, 0.3], maxlags=5)
    with pytest.raises(RpaStatsError):
        hac_mean_test([0.1, float("nan")] * 50)


# ─────────────────────────────────────────────────────────────────────────────
# Holm
# ─────────────────────────────────────────────────────────────────────────────
def test_holm_matches_a_worked_example():
    out = holm({"a": 0.010, "b": 0.040, "c": 0.030, "d": 0.005}, alpha=FAMILY_ALPHA)
    assert out["d"]["p_holm"] == pytest.approx(0.020)   # 4 * 0.005
    assert out["a"]["p_holm"] == pytest.approx(0.030)   # 3 * 0.010
    assert out["c"]["p_holm"] == pytest.approx(0.060)   # 2 * 0.030
    assert out["b"]["p_holm"] == pytest.approx(0.060)   # 1 * 0.040, held up by c


def test_holm_is_monotone_and_never_below_the_raw_p():
    raw = {"a": 0.02, "b": 0.021, "c": 0.5, "d": 0.9}
    out = holm(raw)
    for name, row in out.items():
        assert row["p_holm"] >= row["p_raw"]
    ordered = sorted(out.values(), key=lambda r: r["rank"])
    assert all(x["p_holm"] <= y["p_holm"] for x, y in zip(ordered, ordered[1:]))


def test_holm_is_capped_at_one():
    assert holm({"a": 0.5, "b": 0.6, "c": 0.7, "d": 0.8})["a"]["p_holm"] == 1.0


def test_holm_rejects_only_at_or_below_alpha():
    out = holm({"a": 0.02, "b": 0.30, "c": 0.40, "d": 0.50}, alpha=0.10)
    assert out["a"]["p_holm"] == pytest.approx(0.08) and out["a"]["reject_null"]
    assert not any(out[k]["reject_null"] for k in ("b", "c", "d"))


def test_holm_correction_can_overturn_a_raw_pass():
    """The whole point of condition 3: four candidates, one lucky raw p."""
    raw = {"C2": 0.04, "C3": 0.55, "C4": 0.60, "C5": 0.70}
    out = holm(raw, alpha=0.10)
    assert raw["C2"] < 0.10
    assert out["C2"]["p_holm"] == pytest.approx(0.16)
    assert not out["C2"]["reject_null"]


def test_holm_family_size_is_reported_for_audit():
    out = holm({"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04})
    assert {row["family_size"] for row in out.values()} == {4}


def test_a_zero_difference_series_fails_rather_than_crashing():
    """A candidate that exactly reproduces C1 supplies no evidence, so p = 1."""
    result = hac_mean_test([0.0] * 200)
    assert result["se_path"] == "degenerate"
    assert result["p_one_sided"] == 1.0
    assert result["t_stat"] == 0.0
    assert not holm({"only": result["p_one_sided"]}, alpha=0.10)["only"]["reject_null"]


def test_a_constant_positive_difference_refuses_to_report_significance():
    with pytest.raises(RpaStatsError, match="fabricate|infinite significance"):
        hac_mean_test([0.002] * 200)
