"""The run report must not drift from the artifact it claims to summarise.

Every display-layer bug this repo has been caught by lived in the gap between a
computed number and a rendered one. docs/designs/rp-a-results.md is rendered by
hand, so it is checked against data/rpa_results.json here rather than trusted.
"""

import json
import pathlib
import re

import pytest

import rpa_screen as rpa

REPORT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "designs" / "rp-a-results.md"

DISPLAY = {
    "C1_EW_MONTHLY": "C1 `EW_MONTHLY`",
    "C2_TREND_SPY200": "C2 `TREND_SPY200`",
    "C3_XMOM_TOP5": "C3 `XMOM_TOP5`",
    "C4_VOLTGT_EW": "C4 `VOLTGT_EW`",
    "C5_TREND_XMOM": "C5 `TREND_XMOM`",
}
CONDITIONS = ["1_sharpe_beats_c1", "2_positive_active_return",
              "3_holm_adjusted_hac_test", "4_drawdown_within_1_25x"]


def number(text: str) -> float:
    """Markdown uses a real minus sign; the JSON does not."""
    return float(text.replace("−", "-").replace("%", "")
                 .replace("+", "").replace("$", "").replace(",", "").strip().strip("*"))


@pytest.fixture(scope="module")
def report():
    return json.loads(rpa.RESULTS_ARTIFACT.read_text())


@pytest.fixture(scope="module")
def markdown():
    return REPORT.read_text()


def row_cells(block: str, label: str) -> list[str]:
    line = next(l for l in block.splitlines() if l.startswith("| " + label))
    return line.split("|")


@pytest.mark.parametrize("metric,heading", [
    ("total_return_pct", "**Total return**"),
    ("sharpe", "**Sharpe**"),
])
def test_cost_grid_tables_match_the_artifact(report, markdown, metric, heading):
    block = markdown.split(heading, 1)[1].split("\n**", 1)[0]
    for name, label in DISPLAY.items():
        cells = row_cells(block, label)[2:6]
        for bps, cell in zip(("0", "5", "10", "20"), cells):
            assert number(cell) == pytest.approx(
                report["cost_grid"][bps]["metrics"][name][metric], abs=0.0051
            ), f"{name} {metric} at {bps} bps"


def test_pass_condition_values_and_verdict_words_match(report, markdown):
    level = report["cost_grid"]["10"]["evaluation"]["candidates"]
    for name, row in level.items():
        section = markdown.split("### " + DISPLAY[name], 1)[1].split("\n### ", 1)[0]
        conditions = row["conditions"]
        assert number(re.search(r"Sharpe > C1 \| ([-−\d.]+)", section).group(1)) == \
            pytest.approx(conditions["1_sharpe_beats_c1"]["value"], abs=0.0051)
        assert number(re.search(r"active return > 0 \| ([-−\d.]+)%", section).group(1)) == \
            pytest.approx(conditions["2_positive_active_return"]["value"], abs=0.0051)
        assert number(re.search(r"\| ([\d.]+) \(raw", section).group(1)) == \
            pytest.approx(conditions["3_holm_adjusted_hac_test"]["value"], abs=1e-4)
        assert number(re.search(r"raw ([\d.]+),", section).group(1)) == \
            pytest.approx(conditions["3_holm_adjusted_hac_test"]["p_raw"], abs=1e-4)
        assert number(re.search(r"1.25 × C1 \| ([\d.]+)%", section).group(1)) == \
            pytest.approx(conditions["4_drawdown_within_1_25x"]["value"], abs=0.0051)
        for position, key in enumerate(CONDITIONS, start=1):
            word = re.search(rf"\| {position} \|.*\| (PASS|FAIL) \|", section).group(1)
            assert (word == "PASS") is conditions[key]["passed"], f"{name} condition {position}"


def test_the_headline_verdict_matches_the_artifact(report, markdown):
    assert report["verdict"] == "FAIL"
    assert report["passing_candidates"] == []
    assert "**Verdict: FAIL" in markdown
    # A report may not claim a pass the artifact does not contain.
    assert "Verdict: PASS" not in markdown


def test_the_report_states_the_gate_outcome_the_artifact_recorded(report, markdown):
    gate = report["gate"]
    assert gate["passed"] is True
    assert "## 1. Engine validation — PASS" in markdown
    assert number(re.search(r"Max abs equity difference.*?\*\*([\d.e-]+)\*\*", markdown).group(1)) \
        == pytest.approx(gate["max_abs_equity_difference"], rel=1e-2)


def test_benchmark_figures_match(report, markdown):
    spy = report["benchmarks"]["SPY"]
    section = markdown.split("## 4. C1 against SPY", 1)[1]
    assert number(row_cells(section, "Total return")[3]) == \
        pytest.approx(spy["total_return_pct"], abs=0.0051)
    assert number(row_cells(section, "Sharpe")[3]) == pytest.approx(spy["sharpe"], abs=0.0051)
    assert number(row_cells(section, "Max drawdown")[3]) == \
        pytest.approx(spy["max_drawdown_pct"], abs=0.0051)


def test_the_hac_cross_check_claim_is_true(report, markdown):
    worst = report["hac_cross_check"]["worst_disagreement_rel"]
    assert worst < report["hac_cross_check"]["tolerance_rel"]
    claimed = number(re.search(r"combinations:\s*([\d.e-]+)\s*relative", markdown).group(1))
    assert claimed == pytest.approx(worst, rel=0.05)
    assert report["hac_cross_check"]["paths"] == ["statsmodels"]
