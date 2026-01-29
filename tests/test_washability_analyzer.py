"""
Unit tests for WashabilityAnalyzer.
"""

import pytest
from src.washability_analyzer import (
    FloatSinkFraction,
    WashabilityAnalyzer,
    WashabilityResult,
)


@pytest.fixture
def typical_fractions():
    """Typical Kalimantan thermal coal float-sink distribution."""
    return [
        FloatSinkFraction(None, 1.30, 12.5, 3.2, sulfur_pct=0.32, gcv_kcal_kg=6850),
        FloatSinkFraction(1.30, 1.35, 18.0, 7.1, sulfur_pct=0.45, gcv_kcal_kg=6600),
        FloatSinkFraction(1.35, 1.40, 22.5, 11.8, sulfur_pct=0.52, gcv_kcal_kg=6250),
        FloatSinkFraction(1.40, 1.50, 19.0, 22.4, sulfur_pct=0.68, gcv_kcal_kg=5800),
        FloatSinkFraction(1.50, 1.60, 13.5, 38.6, sulfur_pct=0.88, gcv_kcal_kg=5100),
        FloatSinkFraction(1.60, None, 14.5, 68.2, sulfur_pct=1.20, gcv_kcal_kg=3900),
    ]


@pytest.fixture
def analyzer(typical_fractions):
    return WashabilityAnalyzer(typical_fractions)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_valid_fractions_accepted(self, typical_fractions):
        a = WashabilityAnalyzer(typical_fractions)
        assert a is not None

    def test_empty_fractions_raises(self):
        with pytest.raises(ValueError, match="empty"):
            WashabilityAnalyzer([])

    def test_weights_not_summing_to_100_raises(self):
        bad = [
            FloatSinkFraction(None, 1.30, 30.0, 5.0),
            FloatSinkFraction(1.30, None, 30.0, 50.0),  # total = 60, not 100
        ]
        with pytest.raises(ValueError, match="sum to 100"):
            WashabilityAnalyzer(bad)

    def test_tolerance_allows_minor_deviation(self):
        fractions = [
            FloatSinkFraction(None, 1.40, 50.2, 8.0),
            FloatSinkFraction(1.40, None, 50.0, 45.0),  # total = 100.2
        ]
        a = WashabilityAnalyzer(fractions, weight_tolerance=0.5)
        assert a is not None


# ---------------------------------------------------------------------------
# analyze_at_density
# ---------------------------------------------------------------------------

class TestAnalyzeAtDensity:
    def test_returns_washability_result(self, analyzer):
        result = analyzer.analyze_at_density(1.40)
        assert isinstance(result, WashabilityResult)

    def test_yield_and_refuse_sum_to_feed(self, analyzer):
        result = analyzer.analyze_at_density(1.40)
        total = result.clean_coal_yield_pct + result.refuse_yield_pct
        assert abs(total - 100.0) < 1.0

    def test_lower_cut_gives_lower_yield(self, analyzer):
        r130 = analyzer.analyze_at_density(1.35)
        r150 = analyzer.analyze_at_density(1.50)
        assert r130.clean_coal_yield_pct < r150.clean_coal_yield_pct

    def test_lower_cut_gives_lower_ash(self, analyzer):
        r135 = analyzer.analyze_at_density(1.35)
        r155 = analyzer.analyze_at_density(1.55)
        assert r135.clean_coal_ash_pct < r155.clean_coal_ash_pct

    def test_sulfur_present_when_data_available(self, analyzer):
        result = analyzer.analyze_at_density(1.40)
        assert result.clean_coal_sulfur_pct is not None

    def test_gcv_present_when_data_available(self, analyzer):
        result = analyzer.analyze_at_density(1.40)
        assert result.clean_coal_gcv_kcal_kg is not None

    def test_separability_index_positive(self, analyzer):
        result = analyzer.analyze_at_density(1.40)
        assert result.separability_index > 0

    def test_near_gravity_material_in_range(self, analyzer):
        result = analyzer.analyze_at_density(1.40)
        assert 0 <= result.near_gravity_material_pct <= 100


# ---------------------------------------------------------------------------
# find_density_for_target_ash
# ---------------------------------------------------------------------------

class TestFindDensityForTargetAsh:
    def test_finds_density_for_achievable_target(self, analyzer):
        sg = analyzer.find_density_for_target_ash(target_ash_pct=10.0)
        assert sg is not None
        result = analyzer.analyze_at_density(sg)
        assert result.clean_coal_ash_pct <= 10.0

    def test_very_low_target_returns_none(self, analyzer):
        # Target ash of 1% is unachievable
        sg = analyzer.find_density_for_target_ash(target_ash_pct=1.0)
        assert sg is None

    def test_high_target_ash_gives_high_yield(self, analyzer):
        sg = analyzer.find_density_for_target_ash(target_ash_pct=20.0)
        assert sg is not None
        result = analyzer.analyze_at_density(sg)
        assert result.clean_coal_yield_pct > 40.0


# ---------------------------------------------------------------------------
# generate_curve
# ---------------------------------------------------------------------------

class TestGenerateCurve:
    def test_returns_list_of_results(self, analyzer):
        curve = analyzer.generate_curve(sg_min=1.30, sg_max=1.60, sg_step=0.05)
        assert len(curve) > 0
        assert all(isinstance(r, WashabilityResult) for r in curve)

    def test_curve_sorted_by_density(self, analyzer):
        curve = analyzer.generate_curve(sg_min=1.30, sg_max=1.60, sg_step=0.05)
        densities = [r.cut_density for r in curve]
        assert densities == sorted(densities)

    def test_yield_increases_with_density(self, analyzer):
        curve = analyzer.generate_curve(sg_min=1.30, sg_max=1.60, sg_step=0.05)
        yields = [r.clean_coal_yield_pct for r in curve]
        for i in range(1, len(yields)):
            assert yields[i] >= yields[i - 1] - 0.5  # allow minor numerical noise


# ---------------------------------------------------------------------------
# raw_coal_characteristics
# ---------------------------------------------------------------------------

class TestRawCoalCharacteristics:
    def test_returns_ash_value(self, analyzer):
        raw = analyzer.raw_coal_characteristics()
        assert "raw_ash_pct" in raw
        assert raw["raw_ash_pct"] > 0

    def test_returns_sulfur_and_gcv_when_available(self, analyzer):
        raw = analyzer.raw_coal_characteristics()
        assert raw["raw_sulfur_pct"] is not None
        assert raw["raw_gcv_kcal_kg"] is not None
