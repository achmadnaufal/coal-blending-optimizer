"""Tests for :mod:`src.cost_per_gj_calculator`.

Covers: unit auto-detection, kcal/kg and MJ/kg paths, delivered cost stacking,
ranking, blended cost, and edge-case input validation.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.cost_per_gj_calculator import (
    DeliveredCostBreakdown,
    blended_cost_per_gj,
    cost_per_gj,
    delivered_cost_per_gj,
    rank_by_cost_per_gj,
)


def test_cost_per_gj_kcal_basis() -> None:
    # 6000 kcal/kg * 4.184e-3 = 25.104 GJ/tonne; 60 / 25.104 ~= 2.39
    result = cost_per_gj(cost_per_tonne_usd=60.0, calorific_value=6000)
    assert math.isclose(result, 60.0 / (6000 * 4.184e-3), rel_tol=1e-4)


def test_cost_per_gj_mjkg_basis_explicit() -> None:
    # 25 MJ/kg = 25 GJ/tonne; 50 / 25 = 2.0
    result = cost_per_gj(
        cost_per_tonne_usd=50.0, calorific_value=25.0, cv_unit="mj/kg"
    )
    assert math.isclose(result, 2.0, rel_tol=1e-4)


def test_cost_per_gj_auto_detects_unit() -> None:
    # 22.5 MJ/kg is below threshold -> auto-detects as MJ/kg
    result_mj = cost_per_gj(cost_per_tonne_usd=45.0, calorific_value=22.5)
    assert math.isclose(result_mj, 45.0 / 22.5, rel_tol=1e-4)
    # 6000 is above threshold -> kcal/kg
    result_kcal = cost_per_gj(cost_per_tonne_usd=60.0, calorific_value=6000)
    assert math.isclose(result_kcal, 60.0 / (6000 * 4.184e-3), rel_tol=1e-4)


def test_cost_per_gj_moisture_penalty() -> None:
    baseline = cost_per_gj(60.0, 6000)
    with_penalty = cost_per_gj(60.0, 6000, moisture_penalty_pct=10.0)
    # 10% energy loss -> cost/GJ goes up by 1/0.9
    assert math.isclose(with_penalty, baseline / 0.9, rel_tol=1e-4)


def test_cost_per_gj_rejects_negative_cost() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        cost_per_gj(cost_per_tonne_usd=-10.0, calorific_value=6000)


def test_cost_per_gj_rejects_nonpositive_cv() -> None:
    with pytest.raises(ValueError, match="positive"):
        cost_per_gj(cost_per_tonne_usd=50.0, calorific_value=0)
    with pytest.raises(ValueError, match="positive"):
        cost_per_gj(cost_per_tonne_usd=50.0, calorific_value=-100)


def test_cost_per_gj_rejects_bad_moisture_penalty() -> None:
    with pytest.raises(ValueError, match="moisture_penalty"):
        cost_per_gj(50.0, 6000, moisture_penalty_pct=110.0)
    with pytest.raises(ValueError, match="moisture_penalty"):
        cost_per_gj(50.0, 6000, moisture_penalty_pct=-5.0)


def test_cost_per_gj_rejects_nan() -> None:
    with pytest.raises(ValueError):
        cost_per_gj(cost_per_tonne_usd=50.0, calorific_value=float("nan"))
    with pytest.raises(ValueError):
        cost_per_gj(cost_per_tonne_usd=float("nan"), calorific_value=6000)


def test_delivered_cost_stacks_components() -> None:
    bd = delivered_cost_per_gj(
        mine_gate_usd_per_tonne=55.0,
        calorific_value=6000,
        freight_usd_per_tonne=12.0,
        handling_usd_per_tonne=3.0,
    )
    assert isinstance(bd, DeliveredCostBreakdown)
    assert math.isclose(bd.total_usd_per_tonne, 70.0, rel_tol=1e-6)
    assert math.isclose(bd.gj_per_tonne, 6000 * 4.184e-3, rel_tol=1e-6)
    assert math.isclose(
        bd.cost_per_gj_usd, 70.0 / (6000 * 4.184e-3), rel_tol=1e-4
    )


def test_delivered_cost_rejects_negative_freight() -> None:
    with pytest.raises(ValueError, match="freight"):
        delivered_cost_per_gj(55.0, 6000, freight_usd_per_tonne=-1.0)


def test_rank_by_cost_per_gj_sorts_ascending() -> None:
    df = pd.DataFrame([
        {"stockpile_id": "A", "calorific_value_kcal_kg": 6000, "cost_per_tonne_usd": 60.0},
        {"stockpile_id": "B", "calorific_value_kcal_kg": 5000, "cost_per_tonne_usd": 40.0},
        {"stockpile_id": "C", "calorific_value_kcal_kg": 6500, "cost_per_tonne_usd": 55.0},
    ])
    ranked = rank_by_cost_per_gj(df, id_column="stockpile_id")
    # Cost per GJ: A=2.39, B=1.91, C=2.02 -> ranking B, C, A
    assert ranked.iloc[0]["id"] == "B"
    assert ranked.iloc[1]["id"] == "C"
    assert ranked.iloc[2]["id"] == "A"
    assert ranked["cost_per_gj_usd"].is_monotonic_increasing


def test_rank_handles_invalid_rows() -> None:
    df = pd.DataFrame([
        {"stockpile_id": "OK", "calorific_value_kcal_kg": 6000, "cost_per_tonne_usd": 60.0},
        {"stockpile_id": "BAD", "calorific_value_kcal_kg": 0, "cost_per_tonne_usd": 60.0},
        {"stockpile_id": "NAN", "calorific_value_kcal_kg": np.nan, "cost_per_tonne_usd": 60.0},
    ])
    ranked = rank_by_cost_per_gj(df, id_column="stockpile_id")
    # OK row should be first (only valid row); invalid rows come last.
    assert ranked.iloc[0]["id"] == "OK"
    assert pd.isna(ranked.iloc[-1]["cost_per_gj_usd"])


def test_rank_raises_on_empty_or_missing_columns() -> None:
    with pytest.raises(ValueError, match="empty"):
        rank_by_cost_per_gj(pd.DataFrame())
    with pytest.raises(ValueError, match="not found"):
        rank_by_cost_per_gj(
            pd.DataFrame([{"stockpile_id": "A", "cv": 6000}]),
            cost_column="cost_per_tonne_usd",
        )


def test_blended_cost_per_gj_energy_weighted() -> None:
    alloc = {"A": 40_000, "B": 60_000}
    data = {
        "A": {"cost_per_tonne_usd": 55.0, "calorific_value": 6200},
        "B": {"cost_per_tonne_usd": 30.0, "calorific_value": 5000},
    }
    res = blended_cost_per_gj(alloc, data)
    expected_total_cost = 55.0 * 40_000 + 30.0 * 60_000
    expected_total_gj = 6200 * 4.184e-3 * 40_000 + 5000 * 4.184e-3 * 60_000
    assert math.isclose(res["total_cost_usd"], expected_total_cost, rel_tol=1e-4)
    assert math.isclose(
        res["cost_per_gj_usd"], expected_total_cost / expected_total_gj, rel_tol=1e-4
    )
    assert res["total_tonnes"] == 100_000


def test_blended_cost_rejects_missing_stockpile() -> None:
    with pytest.raises(ValueError, match="not in stockpile_data"):
        blended_cost_per_gj(
            {"A": 10_000},
            stockpile_data={},
        )


def test_blended_cost_rejects_empty_allocation() -> None:
    with pytest.raises(ValueError, match="empty"):
        blended_cost_per_gj({}, stockpile_data={})


def test_blended_cost_ignores_zero_tonnes() -> None:
    alloc = {"A": 10_000, "B": 0}
    data = {
        "A": {"cost_per_tonne_usd": 50.0, "calorific_value": 6000},
        "B": {"cost_per_tonne_usd": 30.0, "calorific_value": 5000},
    }
    res = blended_cost_per_gj(alloc, data)
    # Only A should contribute.
    assert res["total_tonnes"] == 10_000
    assert math.isclose(res["cost_per_tonne_usd"], 50.0, rel_tol=1e-6)
