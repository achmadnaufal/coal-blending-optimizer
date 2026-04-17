"""
Tests for src/blend_scenario_comparator.py.

Coverage:
  - Happy-path: multiple scenarios, ranking by cost / quality
  - Single-source ("all-same-source") blend
  - Edge cases: zero tonnage rejected (fraction must be > 0), infeasible
    spec produces feasible=False with binding parameter, missing parameter
    in source rejected, duplicate scenario names rejected, unknown source
    referenced rejected, fractions not summing to 1 rejected, inverted spec
    bounds rejected, empty constructor / compare inputs rejected.
  - Determinism + immutability of report and scenarios.
  - Headroom math sign convention.
  - Winner selection skips infeasible scenarios.
"""

from __future__ import annotations

import math

import pytest

from src.blend_scenario_comparator import (
    BlendScenarioComparator,
    ComparisonReport,
    ScenarioRecipe,
    ScenarioResult,
    QUALITY_PROPERTIES,
    RANKING_OBJECTIVES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sources() -> list[dict]:
    return [
        {
            "source_id": "A",
            "cv_kcal": 6300,
            "ash_pct": 4.5,
            "sulfur_pct": 0.35,
            "total_moisture_pct": 8.0,
            "cost_per_tonne": 90.0,
        },
        {
            "source_id": "B",
            "cv_kcal": 5800,
            "ash_pct": 8.0,
            "sulfur_pct": 0.7,
            "total_moisture_pct": 13.0,
            "cost_per_tonne": 65.0,
        },
        {
            "source_id": "C",
            "cv_kcal": 6000,
            "ash_pct": 6.0,
            "sulfur_pct": 0.5,
            "total_moisture_pct": 10.5,
            "cost_per_tonne": 78.0,
        },
    ]


@pytest.fixture()
def specs() -> dict:
    return {
        "cv_kcal": {"min": 5900},
        "ash_pct": {"max": 7.5},
        "sulfur_pct": {"max": 0.6},
    }


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_compare_ranks_by_cost_with_winner(sources, specs):
    cmp = BlendScenarioComparator(sources, specs=specs)
    scenarios = [
        ScenarioRecipe("premium", {"A": 0.7, "C": 0.3}),
        ScenarioRecipe("balanced", {"A": 0.5, "C": 0.5}),
        ScenarioRecipe("dirty", {"B": 1.0}),  # fails cv/ash/sulfur
    ]
    report = cmp.compare(scenarios, ranking_objective="cost_per_tonne")

    assert isinstance(report, ComparisonReport)
    assert len(report.scenarios) == 3
    # "balanced" is cheaper than "premium" and both feasible; dirty fails specs.
    assert report.winner == "balanced"
    # Ranking lists feasible first; infeasible appended at the end.
    feasible_names = [r.name for r in report.scenarios if r.feasible]
    assert report.ranked_names[0] in feasible_names
    assert report.ranked_names[-1] == "dirty"


def test_weighted_quality_arithmetic(sources):
    cmp = BlendScenarioComparator(sources)
    scenarios = [ScenarioRecipe("mix", {"A": 0.5, "B": 0.5})]
    report = cmp.compare(scenarios, ranking_objective="cv_kcal")
    blended = report.scenarios[0].blended_quality
    assert math.isclose(blended["cv_kcal"], (6300 + 5800) / 2)
    assert math.isclose(blended["ash_pct"], (4.5 + 8.0) / 2)
    assert math.isclose(report.scenarios[0].blended_cost_per_tonne, (90.0 + 65.0) / 2)


def test_single_source_blend_passes(sources, specs):
    cmp = BlendScenarioComparator(sources, specs=specs)
    scenarios = [ScenarioRecipe("pure_A", {"A": 1.0})]
    report = cmp.compare(scenarios)
    assert report.scenarios[0].feasible is True
    assert report.winner == "pure_A"
    assert math.isclose(report.scenarios[0].blended_quality["cv_kcal"], 6300.0)


def test_ranking_by_calorific_value_descending(sources):
    cmp = BlendScenarioComparator(sources)
    scenarios = [
        ScenarioRecipe("low_cv", {"B": 1.0}),
        ScenarioRecipe("high_cv", {"A": 1.0}),
        ScenarioRecipe("mid_cv", {"C": 1.0}),
    ]
    report = cmp.compare(scenarios, ranking_objective="cv_kcal")
    assert report.ranked_names == ("high_cv", "mid_cv", "low_cv")


# ---------------------------------------------------------------------------
# Compliance / headroom semantics
# ---------------------------------------------------------------------------


def test_headroom_sign_and_binding_parameter(sources, specs):
    cmp = BlendScenarioComparator(sources, specs=specs)
    scenarios = [ScenarioRecipe("dirty", {"B": 1.0})]  # fails ash & sulfur & cv
    report = cmp.compare(scenarios)
    result = report.scenarios[0]
    assert result.feasible is False
    # cv_kcal=5800 < min 5900 → headroom -100
    assert result.spec_headroom["cv_kcal"] < 0
    # binding param is the worst (most negative) headroom
    assert result.binding_parameter in {"cv_kcal", "ash_pct", "sulfur_pct"}


def test_infeasible_scenario_yields_no_winner(sources, specs):
    cmp = BlendScenarioComparator(sources, specs=specs)
    scenarios = [ScenarioRecipe("dirty_only", {"B": 1.0})]
    report = cmp.compare(scenarios)
    assert report.winner is None
    assert report.scenarios[0].feasible is False


def test_no_specs_means_all_feasible(sources):
    cmp = BlendScenarioComparator(sources)  # no specs supplied
    scenarios = [ScenarioRecipe("anything", {"B": 1.0})]
    report = cmp.compare(scenarios)
    assert report.scenarios[0].feasible is True
    assert report.scenarios[0].binding_parameter is None
    assert report.scenarios[0].spec_headroom == {}


# ---------------------------------------------------------------------------
# Validation: edges
# ---------------------------------------------------------------------------


def test_zero_tonnage_fraction_rejected():
    # fraction == 0 is "zero tonnage" for that source → reject at recipe level
    with pytest.raises(ValueError, match="fraction"):
        ScenarioRecipe("z", {"A": 0.0, "B": 1.0})


def test_fractions_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        ScenarioRecipe("bad_sum", {"A": 0.3, "B": 0.3})


def test_missing_quality_parameter_rejected_at_construction():
    bad = [{"source_id": "X", "cv_kcal": 6000, "ash_pct": 5, "sulfur_pct": 0.5,
            "total_moisture_pct": 10}]  # missing cost_per_tonne
    with pytest.raises(ValueError, match="missing required key 'cost_per_tonne'"):
        BlendScenarioComparator(bad)


def test_negative_quality_value_rejected():
    bad = [{"source_id": "X", "cv_kcal": -1, "ash_pct": 5, "sulfur_pct": 0.5,
            "total_moisture_pct": 10, "cost_per_tonne": 50}]
    with pytest.raises(ValueError, match=">= 0"):
        BlendScenarioComparator(bad)


def test_inverted_spec_bounds_rejected(sources):
    with pytest.raises(ValueError, match="cannot exceed max"):
        BlendScenarioComparator(sources, specs={"ash_pct": {"min": 10, "max": 5}})


def test_empty_sources_rejected():
    with pytest.raises(ValueError, match="sources must not be empty"):
        BlendScenarioComparator([])


def test_empty_scenarios_rejected(sources):
    cmp = BlendScenarioComparator(sources)
    with pytest.raises(ValueError, match="scenarios must not be empty"):
        cmp.compare([])


def test_unknown_source_rejected(sources):
    cmp = BlendScenarioComparator(sources)
    with pytest.raises(ValueError, match="unknown source_id"):
        cmp.compare([ScenarioRecipe("oops", {"Z": 1.0})])


def test_duplicate_scenario_names_rejected(sources):
    cmp = BlendScenarioComparator(sources)
    with pytest.raises(ValueError, match="Duplicate scenario name"):
        cmp.compare([
            ScenarioRecipe("dup", {"A": 1.0}),
            ScenarioRecipe("dup", {"B": 1.0}),
        ])


def test_invalid_ranking_objective_rejected(sources):
    cmp = BlendScenarioComparator(sources)
    with pytest.raises(ValueError, match="ranking_objective"):
        cmp.compare([ScenarioRecipe("x", {"A": 1.0})], ranking_objective="bogus")


def test_duplicate_source_id_rejected():
    bad = [
        {"source_id": "A", "cv_kcal": 6000, "ash_pct": 5, "sulfur_pct": 0.5,
         "total_moisture_pct": 10, "cost_per_tonne": 80},
        {"source_id": "A", "cv_kcal": 6000, "ash_pct": 5, "sulfur_pct": 0.5,
         "total_moisture_pct": 10, "cost_per_tonne": 80},
    ]
    with pytest.raises(ValueError, match="Duplicate source_id"):
        BlendScenarioComparator(bad)


# ---------------------------------------------------------------------------
# Immutability + determinism
# ---------------------------------------------------------------------------


def test_result_is_frozen(sources):
    cmp = BlendScenarioComparator(sources)
    report = cmp.compare([ScenarioRecipe("s", {"A": 1.0})])
    with pytest.raises(Exception):
        report.scenarios[0].blended_cost_per_tonne = 0.0  # type: ignore[misc]
    with pytest.raises(Exception):
        report.winner = "different"  # type: ignore[misc]


def test_compare_is_deterministic(sources, specs):
    cmp = BlendScenarioComparator(sources, specs=specs)
    scenarios = [
        ScenarioRecipe("a", {"A": 0.6, "C": 0.4}),
        ScenarioRecipe("b", {"A": 0.4, "C": 0.6}),
    ]
    r1 = cmp.compare(scenarios)
    r2 = cmp.compare(scenarios)
    assert r1.ranked_names == r2.ranked_names
    assert r1.winner == r2.winner
    assert r1.scenarios[0].blended_cost_per_tonne == r2.scenarios[0].blended_cost_per_tonne


def test_quality_properties_constant_complete():
    # Guards against accidental drift between docstring and constants.
    assert "cv_kcal" in QUALITY_PROPERTIES
    assert "ash_pct" in QUALITY_PROPERTIES
    assert "sulfur_pct" in QUALITY_PROPERTIES
    assert "cost_per_tonne" in RANKING_OBJECTIVES
