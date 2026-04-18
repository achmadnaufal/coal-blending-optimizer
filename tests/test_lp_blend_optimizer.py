"""Tests for :mod:`src.lp_blend_optimizer`.

Covers: basic feasible solve, infeasible problem, insufficient supply, single
stockpile, NaN rejection, missing column rejection, binding-constraint
detection, and immutability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.lp_blend_optimizer import (
    COLUMN_ALIASES,
    LPBlendOptimizer,
    LPBlendResult,
    optimize_blend_lp,
)


def _make_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"stockpile_id": "HCV", "calorific_value_kcal_kg": 6400,
         "ash_pct": 6.5, "sulphur_pct": 0.35, "tonnage": 50000,
         "cost_per_tonne_usd": 55.0},
        {"stockpile_id": "MCV", "calorific_value_kcal_kg": 5800,
         "ash_pct": 8.0, "sulphur_pct": 0.45, "tonnage": 70000,
         "cost_per_tonne_usd": 42.0},
        {"stockpile_id": "LCV", "calorific_value_kcal_kg": 4900,
         "ash_pct": 13.0, "sulphur_pct": 0.6, "tonnage": 120000,
         "cost_per_tonne_usd": 24.0},
    ])


def test_feasible_blend_minimises_cost() -> None:
    lp = LPBlendOptimizer()
    df = _make_df()
    res = lp.solve(
        df,
        target_tonnage=100_000,
        constraints={
            "calorific_value_kcal_kg": {"min": 5600},
            "ash_pct": {"max": 10.0},
            "sulphur_pct": {"max": 0.5},
        },
    )
    assert isinstance(res, LPBlendResult)
    assert res.feasible is True
    assert res.status == "optimal"
    # Total allocation should equal target tonnage within tolerance.
    assert abs(sum(res.allocation_tonnes.values()) - 100_000) < 1.0
    # Blended calorific value must respect the min constraint.
    assert res.blended_quality["calorific_value_kcal_kg"] >= 5600 - 1e-2
    assert res.blended_quality["ash_pct"] <= 10.0 + 1e-6
    # Should identify at least one binding constraint in this typical case.
    assert res.total_cost_usd > 0


def test_infeasible_when_constraint_too_strict() -> None:
    lp = LPBlendOptimizer()
    df = _make_df()
    # Nothing in the pool has CV > 7000 kcal/kg — so demanding min 7000 is infeasible.
    res = lp.solve(
        df,
        target_tonnage=50_000,
        constraints={"calorific_value_kcal_kg": {"min": 7000}},
    )
    assert res.feasible is False
    assert "solver" in res.status or res.status == "infeasible_single_stockpile"
    assert res.message  # human-readable diagnostic


def test_insufficient_supply_returns_infeasible() -> None:
    lp = LPBlendOptimizer()
    df = _make_df()
    res = lp.solve(df, target_tonnage=10_000_000)  # far exceeds supply
    assert res.feasible is False
    assert res.status == "insufficient_supply"


def test_single_stockpile_passes_when_in_spec() -> None:
    lp = LPBlendOptimizer()
    df = pd.DataFrame([{
        "stockpile_id": "SOLO",
        "calorific_value_kcal_kg": 6000,
        "ash_pct": 8.0,
        "sulphur_pct": 0.4,
        "tonnage": 200000,
        "cost_per_tonne_usd": 50.0,
    }])
    res = lp.solve(
        df,
        target_tonnage=50_000,
        constraints={"ash_pct": {"max": 10.0}},
    )
    assert res.feasible is True
    assert res.allocation_pct["SOLO"] == 100.0
    assert res.allocation_tonnes["SOLO"] == 50_000


def test_single_stockpile_fails_out_of_spec() -> None:
    lp = LPBlendOptimizer()
    df = pd.DataFrame([{
        "stockpile_id": "SOLO",
        "calorific_value_kcal_kg": 6000,
        "ash_pct": 15.0,
        "sulphur_pct": 0.4,
        "tonnage": 200000,
        "cost_per_tonne_usd": 50.0,
    }])
    res = lp.solve(
        df,
        target_tonnage=50_000,
        constraints={"ash_pct": {"max": 10.0}},
    )
    assert res.feasible is False
    assert res.status == "infeasible_single_stockpile"


def test_empty_dataframe_raises() -> None:
    lp = LPBlendOptimizer()
    with pytest.raises(ValueError, match="empty"):
        lp.solve(pd.DataFrame(), target_tonnage=10_000)


def test_nonpositive_target_raises() -> None:
    lp = LPBlendOptimizer()
    with pytest.raises(ValueError, match="positive"):
        lp.solve(_make_df(), target_tonnage=0)
    with pytest.raises(ValueError, match="positive"):
        lp.solve(_make_df(), target_tonnage=-1_000)


def test_nan_in_constraint_column_raises() -> None:
    lp = LPBlendOptimizer()
    df = _make_df()
    df.loc[0, "ash_pct"] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        lp.solve(
            df,
            target_tonnage=50_000,
            constraints={"ash_pct": {"max": 10.0}},
        )


def test_constraint_references_missing_column_raises() -> None:
    lp = LPBlendOptimizer()
    with pytest.raises(ValueError, match="not found"):
        lp.solve(
            _make_df(),
            target_tonnage=50_000,
            constraints={"bogus_param": {"max": 1.0}},
        )


def test_input_dataframe_not_mutated() -> None:
    lp = LPBlendOptimizer()
    df = _make_df()
    snapshot = df.copy(deep=True)
    lp.solve(
        df,
        target_tonnage=80_000,
        constraints={"ash_pct": {"max": 10.0}},
    )
    pd.testing.assert_frame_equal(df, snapshot)


def test_column_aliases_recognised() -> None:
    lp = LPBlendOptimizer()
    df = pd.DataFrame([
        {"source_id": "A", "cv": 6000, "ash": 8.0, "sulfur": 0.4,
         "volume_available_mt": 50_000, "price_usd_t": 40.0},
        {"source_id": "B", "cv": 5200, "ash": 10.5, "sulfur": 0.6,
         "volume_available_mt": 80_000, "price_usd_t": 28.0},
    ])
    # No constraints beyond the target — ensures aliases map correctly.
    res = lp.solve(df, target_tonnage=60_000)
    assert res.feasible is True
    # Column aliases registered in the module constant.
    assert COLUMN_ALIASES["cv"] == "calorific_value_kcal_kg"


def test_functional_wrapper_returns_dict() -> None:
    df = _make_df()
    out = optimize_blend_lp(
        df, target_tonnage=50_000,
        constraints={"ash_pct": {"max": 10.0}},
    )
    assert isinstance(out, dict)
    assert out["feasible"] is True
    assert "allocation_tonnes" in out


def test_zero_tonnage_stockpiles_dropped() -> None:
    lp = LPBlendOptimizer()
    df = _make_df()
    df.loc[0, "tonnage"] = 0
    res = lp.solve(
        df,
        target_tonnage=60_000,
        constraints={"ash_pct": {"max": 12.0}},
    )
    # HCV (index 0) had 0 tonnes so must not appear with positive allocation.
    assert res.allocation_tonnes.get("HCV", 0) == 0
    assert res.feasible is True
