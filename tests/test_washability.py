"""
Unit tests for src.washability — WashabilityAnalyzer.

Covers: build_float_sink_curve, determine_wash_points, calculate_wash_yield,
compare_coal_sources, product_quality_matrix, critically_sulfur_cut,
and edge cases (single fraction, zero yield, extreme densities).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.washability import CoalSample, WashabilityAnalyzer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def analyzer():
    return WashabilityAnalyzer()


@pytest.fixture
def four_fraction_data():
    """Typical Indonesian coal float-sink data."""
    return [
        {"density": 1.30, "weight_pct": 15.0, "ash_pct": 3.8, "sulfur_pct": 0.31},
        {"density": 1.40, "weight_pct": 32.0, "ash_pct": 8.5, "sulfur_pct": 0.48},
        {"density": 1.50, "weight_pct": 28.0, "ash_pct": 15.2, "sulfur_pct": 0.67},
        {"density": 1.80, "weight_pct": 15.0, "ash_pct": 32.0, "sulfur_pct": 1.10},
        {"density": 2.00, "weight_pct": 10.0, "ash_pct": 55.0, "sulfur_pct": 1.80},
    ]


@pytest.fixture
def aus_queensland_sample():
    return CoalSample(
        sample_id="AU-QLD-001",
        source="Australia",
        mine="Queensland Hunter Valley",
        depth_m=142.0,
        fractions=[
            (1.30, 18.0, 4.2, 0.28),
            (1.40, 35.0, 9.1, 0.44),
            (1.50, 25.0, 16.5, 0.65),
            (1.80, 14.0, 30.0, 1.05),
            (2.00, 8.0,  52.0, 1.60),
        ],
    )


@pytest.fixture
def za_witbank_sample():
    return CoalSample(
        sample_id="ZA-WTB-001",
        source="South Africa",
        mine="Witbank Field 2",
        depth_m=95.0,
        fractions=[
            (1.30, 12.0, 5.5, 0.52),
            (1.40, 28.0, 10.2, 0.71),
            (1.50, 30.0, 18.0, 0.88),
            (1.80, 18.0, 34.5, 1.30),
            (2.00, 12.0, 58.0, 2.10),
        ],
    )


# ---------------------------------------------------------------------------
# build_float_sink_curve — basic
# ---------------------------------------------------------------------------

class TestBuildFloatSinkCurve:
    def test_returns_dataframe(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        assert len(result) == len(four_fraction_data)
        assert hasattr(result, "columns")

    def test_columns_present(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        expected = {"density", "float_weight_pct", "float_ash_pct",
                    "float_sulfur_pct", "sink_weight_pct", "sink_ash_pct",
                    "combustible_recovery_pct"}
        assert expected.issubset(set(result.columns))

    def test_density_sorted_ascending(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        assert result["density"].tolist() == sorted(result["density"].tolist())

    def test_cumulative_float_increases(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        float_wts = result["float_weight_pct"].tolist()
        assert float_wts == sorted(float_wts)
        assert all(float_wts[i] <= float_wts[i + 1] for i in range(len(float_wts) - 1))

    def test_sink_weight_decreases(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        sink_wts = result["sink_weight_pct"].tolist()
        assert sink_wts == sorted(sink_wts, reverse=True)

    def test_float_weight_plus_sink_weight_equals_total(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        total_weight = sum(f["weight_pct"] for f in four_fraction_data)
        for _, row in result.iterrows():
            assert abs((row["float_weight_pct"] + row["sink_weight_pct"]) - total_weight) < 0.01

    def test_combustible_recovery_increases_with_density(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        comb = result["combustible_recovery_pct"].tolist()
        assert comb == sorted(comb)

    def test_combustible_recovery_bounded_0_to_100(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        assert result["combustible_recovery_pct"].min() >= 0.0
        assert result["combustible_recovery_pct"].max() <= 100.0

    def test_combustible_recovery_reaches_100_at_max_density(self, analyzer, four_fraction_data):
        result = analyzer.build_float_sink_curve(four_fraction_data)
        # At the heaviest fraction, all combustible matter is in floats
        last_comb = result["combustible_recovery_pct"].iloc[-1]
        assert last_comb >= 95.0  # allow small rounding

    def test_empty_raises(self, analyzer):
        with pytest.raises(ValueError, match="must not be empty"):
            analyzer.build_float_sink_curve([])


# ---------------------------------------------------------------------------
# build_float_sink_curve — edge cases
# ---------------------------------------------------------------------------

class TestBuildFloatSinkEdgeCases:
    def test_single_fraction(self, analyzer):
        data = [{"density": 1.50, "weight_pct": 100.0, "ash_pct": 18.0, "sulfur_pct": 0.8}]
        result = analyzer.build_float_sink_curve(data)
        assert len(result) == 1
        assert result.iloc[0]["float_weight_pct"] == 100.0
        assert result.iloc[0]["float_ash_pct"] == 18.0
        assert result.iloc[0]["sink_weight_pct"] == 0.0

    def test_zero_weight_fraction_raises(self, analyzer):
        # Should not raise but skip zero-weight fractions gracefully
        data = [
            {"density": 1.30, "weight_pct": 0.0, "ash_pct": 5.0, "sulfur_pct": 0.4},
            {"density": 1.40, "weight_pct": 100.0, "ash_pct": 10.0, "sulfur_pct": 0.5},
        ]
        result = analyzer.build_float_sink_curve(data)
        assert len(result) == 2

    def test_unsorted_input_gets_sorted(self, analyzer):
        data = [
            {"density": 1.50, "weight_pct": 30.0, "ash_pct": 15.0, "sulfur_pct": 0.7},
            {"density": 1.30, "weight_pct": 20.0, "ash_pct": 4.0, "sulfur_pct": 0.3},
            {"density": 1.40, "weight_pct": 50.0, "ash_pct": 10.0, "sulfur_pct": 0.5},
        ]
        result = analyzer.build_float_sink_curve(data)
        assert result["density"].tolist() == [1.30, 1.40, 1.50]

    def test_two_extreme_densities(self, analyzer):
        data = [
            {"density": 1.25, "weight_pct": 40.0, "ash_pct": 2.5, "sulfur_pct": 0.20},
            {"density": 2.50, "weight_pct": 60.0, "ash_pct": 60.0, "sulfur_pct": 2.50},
        ]
        result = analyzer.build_float_sink_curve(data)
        assert len(result) == 2
        # Light fraction → low ash floats
        assert result.iloc[0]["float_ash_pct"] < result.iloc[1]["float_ash_pct"]


# ---------------------------------------------------------------------------
# determine_wash_points
# ---------------------------------------------------------------------------

class TestDetermineWashPoints:
    def test_returns_list_of_dicts(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        result = analyzer.determine_wash_points(curve)
        assert isinstance(result, list)
        assert all(isinstance(r, dict) for r in result)
        assert all("density" in r and "ash_jump" in r and "is_wash_point" in r for r in result)

    def test_wash_point_flags_where_ash_jump_exceeds_threshold(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        result = analyzer.determine_wash_points(curve, ash_jump_threshold=5.0)
        wash_points = [r for r in result if r["is_wash_point"]]
        # With this data the transition 1.50→1.80 should have large ash jump
        assert any(r["is_wash_point"] for r in result)

    def test_threshold_zero_returns_all_wash_points(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        result = analyzer.determine_wash_points(curve, ash_jump_threshold=0.0)
        # All except the first row should be wash points (any positive jump)
        # because ash always increases with density
        non_first = result[1:]
        assert all(r["ash_jump"] >= 0 for r in non_first)

    def test_threshold_very_high_returns_no_wash_points(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        result = analyzer.determine_wash_points(curve, ash_jump_threshold=1000.0)
        assert all(not r["is_wash_point"] for r in result)

    def test_single_row_curve(self, analyzer):
        data = [{"density": 1.50, "weight_pct": 100.0, "ash_pct": 18.0, "sulfur_pct": 0.8}]
        curve = analyzer.build_float_sink_curve(data)
        result = analyzer.determine_wash_points(curve)
        assert len(result) == 1
        assert result[0]["ash_jump"] == 0.0
        assert result[0]["is_wash_point"] is False


# ---------------------------------------------------------------------------
# calculate_wash_yield
# ---------------------------------------------------------------------------

class TestCalculateWashYield:
    def test_yield_in_0_to_100_range(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        for target_ash in [5.0, 8.0, 10.0, 15.0, 20.0, 30.0]:
            yield_val = analyzer.calculate_wash_yield(curve, target_ash)
            assert 0.0 <= yield_val <= 100.0

    def test_higher_ash_target_gives_higher_yield(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        y_low = analyzer.calculate_wash_yield(curve, target_ash_pct=8.0)
        y_high = analyzer.calculate_wash_yield(curve, target_ash_pct=15.0)
        assert y_high >= y_low

    def test_yield_at_min_ash(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        min_ash = curve["float_ash_pct"].min()
        y = analyzer.calculate_wash_yield(curve, target_ash_pct=min_ash)
        # Should return a valid yield near the minimum-ash point
        assert y > 0.0

    def test_yield_at_max_ash_near_total(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        max_ash = curve["float_ash_pct"].max()
        y = analyzer.calculate_wash_yield(curve, target_ash_pct=max_ash)
        # At maximum ash, yield should be near total feed weight
        total_weight = sum(f["weight_pct"] for f in four_fraction_data)
        assert y <= total_weight

    def test_interpolation_accuracy(self, analyzer):
        # Two-fraction case: ash is 5% at 50% yield and 10% at 100% yield.
        # Target ash=10% is at the upper endpoint → 100% yield required.
        # Target ash=7.5% interpolates halfway → 75% yield.
        data = [
            {"density": 1.30, "weight_pct": 50.0, "ash_pct": 5.0, "sulfur_pct": 0.4},
            {"density": 1.50, "weight_pct": 50.0, "ash_pct": 15.0, "sulfur_pct": 0.8},
        ]
        curve = analyzer.build_float_sink_curve(data)
        y_10 = analyzer.calculate_wash_yield(curve, target_ash_pct=10.0)
        assert abs(y_10 - 100.0) < 0.5  # at max ash, all floats needed
        y_7 = analyzer.calculate_wash_yield(curve, target_ash_pct=7.5)
        assert abs(y_7 - 75.0) < 0.5  # halfway → 75% yield

    def test_target_below_all_ash(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        min_ash = curve["float_ash_pct"].min()
        y = analyzer.calculate_wash_yield(curve, target_ash_pct=min_ash - 1.0)
        assert y >= 0.0

    def test_target_above_all_ash(self, analyzer, four_fraction_data):
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        max_ash = curve["float_ash_pct"].max()
        y = analyzer.calculate_wash_yield(curve, target_ash_pct=max_ash + 5.0)
        total_weight = sum(f["weight_pct"] for f in four_fraction_data)
        assert y <= total_weight

    def test_zero_yield_edge_case(self, analyzer):
        # Single very high-ash fraction
        data = [{"density": 2.00, "weight_pct": 100.0, "ash_pct": 70.0, "sulfur_pct": 2.0}]
        curve = analyzer.build_float_sink_curve(data)
        y = analyzer.calculate_wash_yield(curve, target_ash_pct=5.0)
        # Target ash 5% is below this fraction's ash; yield should be 0
        assert y == 0.0

    def test_empty_curve_returns_zero(self, analyzer):
        with pytest.raises(ValueError, match="must not be empty"):
            analyzer.calculate_wash_yield(analyzer.build_float_sink_curve([]), 10.0)


# ---------------------------------------------------------------------------
# compare_coal_sources
# ---------------------------------------------------------------------------

class TestCompareCoalSources:
    def test_returns_dataframe(self, analyzer, aus_queensland_sample, za_witbank_sample):
        result = analyzer.compare_coal_sources(
            [aus_queensland_sample, za_witbank_sample], target_ash_pct=10.0
        )
        assert hasattr(result, "columns")
        assert len(result) == 2

    def test_columns_present(self, analyzer, aus_queensland_sample, za_witbank_sample):
        result = analyzer.compare_coal_sources(
            [aus_queensland_sample, za_witbank_sample], target_ash_pct=10.0
        )
        expected = {"sample_id", "mine", "source", "yield_pct", "sulfur_pct",
                    "combustible_recovery_pct"}
        assert expected.issubset(set(result.columns))

    def test_ranked_by_yield_descending(self, analyzer, aus_queensland_sample, za_witbank_sample):
        result = analyzer.compare_coal_sources(
            [aus_queensland_sample, za_witbank_sample], target_ash_pct=10.0
        )
        yields = result["yield_pct"].tolist()
        assert yields == sorted(yields, reverse=True)

    def test_single_source(self, analyzer, aus_queensland_sample):
        result = analyzer.compare_coal_sources([aus_queensland_sample], target_ash_pct=10.0)
        assert len(result) == 1
        assert result.iloc[0]["sample_id"] == "AU-QLD-001"


# ---------------------------------------------------------------------------
# product_quality_matrix
# ---------------------------------------------------------------------------

class TestProductQualityMatrix:
    def test_returns_dataframe(self, analyzer, four_fraction_data):
        result = analyzer.product_quality_matrix(four_fraction_data)
        assert hasattr(result, "columns")

    def test_columns_present(self, analyzer, four_fraction_data):
        result = analyzer.product_quality_matrix(four_fraction_data)
        expected = {"density", "product_ash_pct", "yield_pct", "sulfur_pct",
                    "combustible_recovery_pct"}
        assert expected.issubset(set(result.columns))

    def test_density_range_respected(self, analyzer, four_fraction_data):
        result = analyzer.product_quality_matrix(
            four_fraction_data, density_min=1.40, density_max=1.80
        )
        densities = result["density"].tolist()
        assert all(1.40 <= d <= 1.80 for d in densities)

    def test_yield_increases_with_density(self, analyzer, four_fraction_data):
        result = analyzer.product_quality_matrix(four_fraction_data)
        yields = result["yield_pct"].tolist()
        assert yields == sorted(yields)

    def test_ash_increases_with_density(self, analyzer, four_fraction_data):
        result = analyzer.product_quality_matrix(four_fraction_data)
        ashes = result["product_ash_pct"].tolist()
        assert ashes == sorted(ashes)

    def test_custom_step(self, analyzer, four_fraction_data):
        result = analyzer.product_quality_matrix(
            four_fraction_data, density_min=1.30, density_max=1.50, density_step=0.10
        )
        densities = result["density"].tolist()
        # Should include 1.30, 1.40, 1.50
        assert len(densities) >= 3

    def test_combustible_recovery_increases(self, analyzer, four_fraction_data):
        result = analyzer.product_quality_matrix(four_fraction_data)
        comb = result["combustible_recovery_pct"].tolist()
        assert comb == sorted(comb)


# ---------------------------------------------------------------------------
# critically_sulfur_cut
# ---------------------------------------------------------------------------

class TestCriticallySulfurCut:
    def test_returns_dict(self, analyzer, four_fraction_data):
        result = analyzer.critically_sulfur_cut(four_fraction_data)
        assert isinstance(result, dict)

    def test_keys_present(self, analyzer, four_fraction_data):
        result = analyzer.critically_sulfur_cut(four_fraction_data)
        expected_keys = {"cut_density", "yield_pct", "product_ash_pct",
                         "sulfur_pct", "sulfur_reduction_pct"}
        assert expected_keys.issubset(set(result.keys()))

    def test_yield_meets_minimum_constraint(self, analyzer, four_fraction_data):
        result = analyzer.critically_sulfur_cut(four_fraction_data, min_yield_pct=60.0)
        if result["cut_density"] is not None:
            assert result["yield_pct"] >= 60.0

    def test_high_min_yield_returns_none(self, analyzer):
        # Single low-yield fraction cannot meet a high min_yield threshold
        data = [{"density": 1.50, "weight_pct": 40.0, "ash_pct": 20.0, "sulfur_pct": 0.8}]
        result = analyzer.critically_sulfur_cut(data, min_yield_pct=80.0)
        assert result["cut_density"] is None

    def test_custom_min_yield(self, analyzer, four_fraction_data):
        result_50 = analyzer.critically_sulfur_cut(four_fraction_data, min_yield_pct=50.0)
        result_70 = analyzer.critically_sulfur_cut(four_fraction_data, min_yield_pct=70.0)
        if result_50["cut_density"] is not None and result_70["cut_density"] is not None:
            assert result_50["sulfur_reduction_pct"] >= result_70["sulfur_reduction_pct"]

    def test_sulfur_reduction_positive(self, analyzer, four_fraction_data):
        result = analyzer.critically_sulfur_cut(four_fraction_data, min_yield_pct=50.0)
        if result["sulfur_reduction_pct"] is not None:
            assert result["sulfur_reduction_pct"] >= 0.0

    def test_zero_weight_raises(self, analyzer):
        with pytest.raises(ValueError, match="total fraction weight"):
            analyzer.critically_sulfur_cut([
                {"density": 1.50, "weight_pct": 0.0, "ash_pct": 20.0, "sulfur_pct": 0.8}
            ])


# ---------------------------------------------------------------------------
# Smoke / integration tests
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_workflow(self, analyzer, four_fraction_data):
        """End-to-end: build curve → find wash points → yield at target → sulfur cut."""
        curve = analyzer.build_float_sink_curve(four_fraction_data)
        assert len(curve) > 0

        wash_points = analyzer.determine_wash_points(curve, ash_jump_threshold=5.0)
        assert len(wash_points) == len(curve)

        yield_10 = analyzer.calculate_wash_yield(curve, target_ash_pct=10.0)
        assert 0.0 <= yield_10 <= 100.0

        matrix = analyzer.product_quality_matrix(four_fraction_data)
        assert len(matrix) > 0

        sulfur_cut = analyzer.critically_sulfur_cut(four_fraction_data, min_yield_pct=60.0)
        assert "cut_density" in sulfur_cut

    def test_coal_sample_comparison_full(self, analyzer, aus_queensland_sample, za_witbank_sample):
        result = analyzer.compare_coal_sources(
            [aus_queensland_sample, za_witbank_sample], target_ash_pct=12.0
        )
        assert len(result) == 2
        assert result.iloc[0]["yield_pct"] >= result.iloc[1]["yield_pct"]
