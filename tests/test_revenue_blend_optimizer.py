"""Tests for :mod:`src.revenue_blend_optimizer`.

Covers:
* Weighted-average blend math for 2- and 3-stockpile cases.
* Optimizer finds the maximum-margin allocation for simple feasible cases.
* Optimizer reports infeasibility gracefully when hard constraints exclude
  every possible blend.
* Edge cases: empty DataFrame, zero-weight stockpiles, NaN in referenced
  columns, insufficient supply, non-positive target, invalid price formulas.
* Immutability: frozen dataclasses, input DataFrame not mutated.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.revenue_blend_optimizer import (
    IndexPriceFormula,
    RevenueBlendOptimizer,
    RevenueBlendResult,
    maximise_blend_revenue,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _three_pile_df() -> pd.DataFrame:
    """Three stockpiles spanning the quality range."""
    return pd.DataFrame([
        {"stockpile_id": "HCV", "calorific_value_kcal_kg": 6400,
         "ash_pct": 6.5, "sulphur_pct": 0.35, "moisture_pct": 14.0,
         "tonnage": 50_000, "cost_per_tonne_usd": 55.0},
        {"stockpile_id": "MCV", "calorific_value_kcal_kg": 5800,
         "ash_pct": 8.0, "sulphur_pct": 0.45, "moisture_pct": 18.0,
         "tonnage": 70_000, "cost_per_tonne_usd": 42.0},
        {"stockpile_id": "LCV", "calorific_value_kcal_kg": 4900,
         "ash_pct": 12.0, "sulphur_pct": 0.6, "moisture_pct": 30.0,
         "tonnage": 120_000, "cost_per_tonne_usd": 24.0},
    ])


def _simple_formula(
    base: float = 70.0,
    kcal_rate: float = 0.012,
    ash_rate: float = 1.5,
    sulfur_rate: float = 30.0,
) -> IndexPriceFormula:
    return IndexPriceFormula(
        base_price_usd_per_tonne=base,
        kcal_premium_usd_per_kcal=kcal_rate,
        reference_cv_kcal_kg=5800,
        ash_penalty_usd_per_pct=ash_rate,
        reference_ash_pct=8.0,
        sulfur_penalty_usd_per_pct=sulfur_rate,
        reference_sulfur_pct=0.45,
    )


# ---------------------------------------------------------------------------
# Weighted-average blend math (the core correctness property)
# ---------------------------------------------------------------------------


class TestWeightedAverageMath:
    """Verify blended quality is a correct weighted average of inputs."""

    def test_two_pile_50_50_blend_simple_math(self) -> None:
        df = pd.DataFrame([
            {"stockpile_id": "A", "calorific_value_kcal_kg": 6000,
             "ash_pct": 6.0, "sulphur_pct": 0.4, "moisture_pct": 15.0,
             "tonnage": 100_000, "cost_per_tonne_usd": 40.0},
            {"stockpile_id": "B", "calorific_value_kcal_kg": 6000,
             "ash_pct": 6.0, "sulphur_pct": 0.4, "moisture_pct": 15.0,
             "tonnage": 100_000, "cost_per_tonne_usd": 40.0},
        ])
        # Identical sources — allocation ambiguous, but blended quality fixed.
        formula = IndexPriceFormula(base_price_usd_per_tonne=50.0)
        res = RevenueBlendOptimizer().solve(df, 100_000, formula)
        assert res.feasible
        assert math.isclose(res.blended_quality["calorific_value_kcal_kg"], 6000.0)
        assert math.isclose(res.blended_quality["ash_pct"], 6.0)
        assert math.isclose(res.blended_quality["sulphur_pct"], 0.4)

    def test_forced_blend_weighted_averages(self) -> None:
        """With per-stockpile cap = 50 kt and target = 100 kt, we *must* take
        50 kt of each — verify the weighted-average formula."""
        df = pd.DataFrame([
            {"stockpile_id": "A", "calorific_value_kcal_kg": 6000,
             "ash_pct": 5.0, "sulphur_pct": 0.3,
             "tonnage": 50_000, "cost_per_tonne_usd": 50.0},
            {"stockpile_id": "B", "calorific_value_kcal_kg": 5200,
             "ash_pct": 9.0, "sulphur_pct": 0.5,
             "tonnage": 50_000, "cost_per_tonne_usd": 30.0},
        ])
        formula = IndexPriceFormula(base_price_usd_per_tonne=60.0)
        res = RevenueBlendOptimizer().solve(df, 100_000, formula)
        assert res.feasible
        # Both piles maxed at 50 kt.
        assert math.isclose(res.allocation_tonnes["A"], 50_000, abs_tol=1.0)
        assert math.isclose(res.allocation_tonnes["B"], 50_000, abs_tol=1.0)
        # Weighted averages: (50*6000 + 50*5200)/100 = 5600, etc.
        assert math.isclose(res.blended_quality["calorific_value_kcal_kg"],
                            5600.0, abs_tol=0.5)
        assert math.isclose(res.blended_quality["ash_pct"], 7.0, abs_tol=0.01)
        assert math.isclose(res.blended_quality["sulphur_pct"], 0.4,
                            abs_tol=0.001)
        # Cost per tonne: (50*50 + 50*30)/100 = 40.
        assert math.isclose(res.cost_per_tonne_usd, 40.0, abs_tol=0.01)

    def test_three_pile_blend_proportions_preserve_mass(self) -> None:
        df = _three_pile_df()
        formula = _simple_formula()
        res = RevenueBlendOptimizer().solve(df, 80_000, formula)
        assert res.feasible
        # Allocation sum equals target.
        assert math.isclose(sum(res.allocation_tonnes.values()), 80_000,
                            abs_tol=1.0)
        # Percentage sum = 100 within rounding.
        assert math.isclose(sum(res.allocation_pct.values()), 100.0,
                            abs_tol=0.01)


# ---------------------------------------------------------------------------
# Optimiser finds the correct optimum
# ---------------------------------------------------------------------------


class TestOptimumFound:
    """Verify the LP picks the margin-maximising allocation."""

    def test_prefers_high_margin_pile_when_unconstrained(self) -> None:
        """Pile A has the best margin; with enough supply the solver takes it
        entirely at the target."""
        # Revenue per tonne for each pile under the formula below:
        #   A: 70 + 0.012*(6400-5800) - 1.5*(6.5-8) = 70 + 7.2 + 2.25 = 79.45
        #      margin = 79.45 - 55 = 24.45
        #   B: 70 + 0.012*(5800-5800) - 1.5*(8-8) = 70,  margin = 70 - 42 = 28.0
        #   C: 70 + 0.012*(4900-5800) - 1.5*(12-8) = 70 - 10.8 - 6 = 53.2
        #      margin = 53.2 - 24 = 29.2
        # So LCV actually has the highest margin -> solver should pick it.
        df = _three_pile_df()
        formula = IndexPriceFormula(
            base_price_usd_per_tonne=70.0,
            kcal_premium_usd_per_kcal=0.012,
            reference_cv_kcal_kg=5800,
            ash_penalty_usd_per_pct=1.5,
            reference_ash_pct=8.0,
        )
        res = RevenueBlendOptimizer().solve(df, 50_000, formula)
        assert res.feasible
        # LCV has 120 kt available — solver should take all 50 kt from LCV.
        assert res.allocation_tonnes["LCV"] == pytest.approx(50_000, abs=1.0)
        assert res.allocation_tonnes.get("HCV", 0) == pytest.approx(0, abs=1.0)
        # Per-tonne margin matches the analytic 29.2.
        assert res.margin_per_tonne_usd == pytest.approx(29.2, abs=0.01)

    def test_constraint_forces_blend_against_pure_choice(self) -> None:
        """Forcing a minimum CV prevents the 100%-LCV optimum and creates a
        binding constraint; the solver should still maximise what remains."""
        df = _three_pile_df()
        formula = IndexPriceFormula(
            base_price_usd_per_tonne=70.0,
            kcal_premium_usd_per_kcal=0.012,
            reference_cv_kcal_kg=5800,
            ash_penalty_usd_per_pct=1.5,
            reference_ash_pct=8.0,
        )
        res = RevenueBlendOptimizer().solve(
            df, 100_000, formula,
            constraints={"calorific_value_kcal_kg": {"min": 5800}},
        )
        assert res.feasible
        # Blended CV must respect the floor.
        assert res.blended_quality["calorific_value_kcal_kg"] >= 5800 - 1e-2
        # At least one binding constraint surfaces.
        assert any("calorific_value_kcal_kg>=min" in c
                   for c in res.binding_constraints)
        # Total margin = total_margin_usd; verify consistency with per-tonne.
        assert math.isclose(
            res.total_margin_usd, res.margin_per_tonne_usd * 100_000,
            rel_tol=1e-6,
        )

    def test_total_margin_equals_revenue_minus_cost(self) -> None:
        df = _three_pile_df()
        formula = _simple_formula()
        res = RevenueBlendOptimizer().solve(df, 100_000, formula)
        assert res.feasible
        assert math.isclose(
            res.total_margin_usd,
            res.total_revenue_usd - res.total_cost_usd,
            rel_tol=1e-9, abs_tol=1e-2,
        )


# ---------------------------------------------------------------------------
# Infeasibility handling
# ---------------------------------------------------------------------------


class TestInfeasibility:

    def test_infeasible_when_constraints_exclude_all_blends(self) -> None:
        df = _three_pile_df()
        formula = _simple_formula()
        # No pile has CV > 7000 — demanding min 7000 is provably infeasible.
        res = RevenueBlendOptimizer().solve(
            df, 50_000, formula,
            constraints={"calorific_value_kcal_kg": {"min": 7000}},
        )
        assert res.feasible is False
        assert res.status.startswith("solver_") or res.status == "infeasible"
        assert res.message  # diagnostic present

    def test_insufficient_supply(self) -> None:
        df = _three_pile_df()
        formula = _simple_formula()
        res = RevenueBlendOptimizer().solve(df, 10_000_000, formula)
        assert res.feasible is False
        assert res.status == "insufficient_supply"
        assert "available" in res.message.lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_empty_dataframe_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            RevenueBlendOptimizer().solve(
                pd.DataFrame(), 10_000, _simple_formula()
            )

    def test_nonpositive_target_raises(self) -> None:
        df = _three_pile_df()
        formula = _simple_formula()
        with pytest.raises(ValueError, match="positive"):
            RevenueBlendOptimizer().solve(df, 0, formula)
        with pytest.raises(ValueError, match="positive"):
            RevenueBlendOptimizer().solve(df, -100, formula)

    def test_zero_weight_stockpiles_dropped(self) -> None:
        df = _three_pile_df()
        df.loc[0, "tonnage"] = 0  # HCV unavailable
        res = RevenueBlendOptimizer().solve(df, 60_000, _simple_formula())
        assert res.feasible
        assert res.allocation_tonnes.get("HCV", 0) == 0

    def test_all_zero_availability_returns_infeasible(self) -> None:
        df = _three_pile_df()
        df["tonnage"] = 0
        res = RevenueBlendOptimizer().solve(df, 10_000, _simple_formula())
        assert res.feasible is False
        assert res.status == "no_available_stockpiles"

    def test_nan_in_constraint_column_raises(self) -> None:
        df = _three_pile_df()
        df.loc[0, "ash_pct"] = np.nan
        with pytest.raises(ValueError, match="NaN"):
            RevenueBlendOptimizer().solve(
                df, 50_000, _simple_formula(),
                constraints={"ash_pct": {"max": 10.0}},
            )

    def test_nan_in_formula_column_raises(self) -> None:
        df = _three_pile_df()
        df.loc[1, "calorific_value_kcal_kg"] = np.nan
        with pytest.raises(ValueError, match="NaN"):
            RevenueBlendOptimizer().solve(df, 50_000, _simple_formula())

    def test_missing_cv_column_when_kcal_premium_set(self) -> None:
        df = _three_pile_df().drop(columns=["calorific_value_kcal_kg"])
        with pytest.raises(ValueError, match="calorific_value_kcal_kg"):
            RevenueBlendOptimizer().solve(df, 50_000, _simple_formula())

    def test_input_dataframe_not_mutated(self) -> None:
        df = _three_pile_df()
        snapshot = df.copy(deep=True)
        RevenueBlendOptimizer().solve(df, 80_000, _simple_formula())
        pd.testing.assert_frame_equal(df, snapshot)

    def test_constraint_column_missing_raises(self) -> None:
        df = _three_pile_df()
        with pytest.raises(ValueError, match="not found"):
            RevenueBlendOptimizer().solve(
                df, 50_000, _simple_formula(),
                constraints={"bogus_param": {"max": 1.0}},
            )


# ---------------------------------------------------------------------------
# IndexPriceFormula validation
# ---------------------------------------------------------------------------


class TestIndexPriceFormula:

    def test_nonpositive_base_price_raises(self) -> None:
        with pytest.raises(ValueError, match="base_price"):
            IndexPriceFormula(base_price_usd_per_tonne=0).validate()
        with pytest.raises(ValueError, match="base_price"):
            IndexPriceFormula(base_price_usd_per_tonne=-10).validate()

    def test_orphan_rate_without_reference_raises(self) -> None:
        with pytest.raises(ValueError, match="together"):
            IndexPriceFormula(
                base_price_usd_per_tonne=70.0,
                kcal_premium_usd_per_kcal=0.01,  # missing reference_cv
            ).validate()
        with pytest.raises(ValueError, match="together"):
            IndexPriceFormula(
                base_price_usd_per_tonne=70.0,
                reference_ash_pct=8.0,  # missing ash_penalty
            ).validate()

    def test_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            IndexPriceFormula(
                base_price_usd_per_tonne=70.0,
                ash_penalty_usd_per_pct=-1.0,
                reference_ash_pct=8.0,
            ).validate()

    def test_frozen_dataclass_is_immutable(self) -> None:
        f = IndexPriceFormula(base_price_usd_per_tonne=60.0)
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            f.base_price_usd_per_tonne = 100.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Result object and functional wrapper
# ---------------------------------------------------------------------------


class TestResultAndWrapper:

    def test_result_is_frozen(self) -> None:
        res = RevenueBlendOptimizer().solve(
            _three_pile_df(), 50_000, _simple_formula()
        )
        assert isinstance(res, RevenueBlendResult)
        with pytest.raises(Exception):  # FrozenInstanceError
            res.feasible = False  # type: ignore[misc]

    def test_to_dict_round_trip(self) -> None:
        res = RevenueBlendOptimizer().solve(
            _three_pile_df(), 50_000, _simple_formula()
        )
        d = res.to_dict()
        assert d["feasible"] is True
        assert "allocation_tonnes" in d
        assert "total_margin_usd" in d
        # Mutating the dict must not affect the frozen result.
        d["allocation_tonnes"]["SABOTAGE"] = 99.0
        assert "SABOTAGE" not in res.allocation_tonnes

    def test_functional_wrapper_returns_dict(self) -> None:
        out = maximise_blend_revenue(
            _three_pile_df(), 50_000, _simple_formula()
        )
        assert isinstance(out, dict)
        assert out["feasible"] is True
        assert "total_margin_usd" in out

    def test_column_aliases_accepted(self) -> None:
        """Caller can use the ergonomic column names from the demo CSV."""
        df = pd.DataFrame([
            {"source_id": "A", "cv": 6200, "ash": 7.0, "sulfur": 0.4,
             "moisture": 16.0, "volume_available_mt": 40_000,
             "price_usd_t": 45.0},
            {"source_id": "B", "cv": 5400, "ash": 10.0, "sulfur": 0.5,
             "moisture": 22.0, "volume_available_mt": 80_000,
             "price_usd_t": 28.0},
        ])
        formula = _simple_formula()
        res = RevenueBlendOptimizer().solve(df, 60_000, formula)
        assert res.feasible
        assert "A" in res.allocation_tonnes or "B" in res.allocation_tonnes
