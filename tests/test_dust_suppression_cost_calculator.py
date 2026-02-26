"""
Unit tests for DustSuppressionCostCalculator.
"""

import pytest
from src.dust_suppression_cost_calculator import (
    DustSuppressionCostCalculator,
    DustSuppressionEstimate,
    VALID_METHODS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def standard_calc():
    return DustSuppressionCostCalculator(
        stockpile_area_m2=20_000.0,
        haul_road_length_km=2.0,
        haul_road_width_m=12.0,
        ambient_temperature_c=30.0,
        rainfall_mm_yr=1500.0,
        surface_moisture_pct=9.0,
        dust_generation_rate_kg_m2_yr=0.5,
    )


@pytest.fixture
def dry_climate_calc():
    return DustSuppressionCostCalculator(
        stockpile_area_m2=10_000.0,
        haul_road_length_km=1.5,
        haul_road_width_m=10.0,
        ambient_temperature_c=40.0,
        rainfall_mm_yr=300.0,
        surface_moisture_pct=5.0,
    )


@pytest.fixture
def stockpile_only():
    return DustSuppressionCostCalculator(
        stockpile_area_m2=5_000.0,
        haul_road_length_km=0.0,
    )


# ---------------------------------------------------------------------------
# Instantiation & validation
# ---------------------------------------------------------------------------

class TestInstantiation:
    def test_valid_creation(self, standard_calc):
        assert standard_calc.stockpile_area_m2 == 20_000.0

    def test_negative_stockpile_raises(self):
        with pytest.raises(ValueError, match="stockpile_area_m2"):
            DustSuppressionCostCalculator(stockpile_area_m2=-100.0)

    def test_negative_road_length_raises(self):
        with pytest.raises(ValueError, match="haul_road_length_km"):
            DustSuppressionCostCalculator(1000.0, haul_road_length_km=-1.0)

    def test_zero_road_width_raises(self):
        with pytest.raises(ValueError, match="haul_road_width_m"):
            DustSuppressionCostCalculator(1000.0, haul_road_width_m=0.0)

    def test_extreme_temperature_raises(self):
        with pytest.raises(ValueError, match="ambient_temperature_c"):
            DustSuppressionCostCalculator(1000.0, ambient_temperature_c=70.0)

    def test_negative_rainfall_raises(self):
        with pytest.raises(ValueError, match="rainfall_mm_yr"):
            DustSuppressionCostCalculator(1000.0, rainfall_mm_yr=-10.0)

    def test_invalid_moisture_raises(self):
        with pytest.raises(ValueError, match="surface_moisture_pct"):
            DustSuppressionCostCalculator(1000.0, surface_moisture_pct=120.0)

    def test_negative_dust_rate_raises(self):
        with pytest.raises(ValueError, match="dust_generation_rate"):
            DustSuppressionCostCalculator(1000.0, dust_generation_rate_kg_m2_yr=-1.0)


# ---------------------------------------------------------------------------
# estimate_annual_cost() structure
# ---------------------------------------------------------------------------

class TestEstimateAnnualCost:
    def test_returns_estimate(self, standard_calc):
        est = standard_calc.estimate_annual_cost("polymer_binder")
        assert isinstance(est, DustSuppressionEstimate)

    def test_invalid_method_raises(self, standard_calc):
        with pytest.raises(ValueError, match="method"):
            standard_calc.estimate_annual_cost("foam")  # type: ignore

    def test_all_methods_work(self, standard_calc):
        for m in VALID_METHODS:
            est = standard_calc.estimate_annual_cost(m)  # type: ignore
            assert est.total_annual_cost_usd > 0

    def test_total_cost_sum_of_components(self, standard_calc):
        est = standard_calc.estimate_annual_cost("polymer_binder")
        components = est.chemical_cost_usd_yr + est.labour_cost_usd_yr + est.equipment_cost_usd_yr
        assert abs(est.total_annual_cost_usd - components) < 2.0

    def test_cost_per_m2_consistent(self, standard_calc):
        est = standard_calc.estimate_annual_cost("water_spray")
        expected = est.total_annual_cost_usd / est.total_treated_area_m2
        assert est.cost_per_m2_usd == pytest.approx(expected, rel=0.001)

    def test_effectiveness_in_range(self, standard_calc):
        for m in VALID_METHODS:
            est = standard_calc.estimate_annual_cost(m)  # type: ignore
            assert 0.0 < est.effectiveness_rating <= 1.0

    def test_cost_per_tonne_when_rate_provided(self, standard_calc):
        est = standard_calc.estimate_annual_cost("polymer_binder")
        assert est.cost_per_tonne_suppressed_usd is not None
        assert est.cost_per_tonne_suppressed_usd > 0

    def test_cost_per_tonne_none_without_rate(self, stockpile_only):
        est = stockpile_only.estimate_annual_cost("water_spray")
        assert est.cost_per_tonne_suppressed_usd is None

    def test_to_dict_keys(self, standard_calc):
        d = standard_calc.estimate_annual_cost("polymer_binder").to_dict()
        for k in ("method", "total_annual_cost_usd", "chemical_cost_usd_yr",
                  "labour_cost_usd_yr", "equipment_cost_usd_yr", "effectiveness_rating"):
            assert k in d

    def test_zero_total_area_raises(self):
        c = DustSuppressionCostCalculator(stockpile_area_m2=0.0, haul_road_length_km=0.0)
        with pytest.raises(ValueError, match="Total treated area"):
            c.estimate_annual_cost("water_spray")


# ---------------------------------------------------------------------------
# Climate adjustments
# ---------------------------------------------------------------------------

class TestClimateAdjustment:
    def test_hot_dry_more_applications_than_moderate(self, dry_climate_calc, standard_calc):
        est_dry = dry_climate_calc.estimate_annual_cost("water_spray")
        est_std = standard_calc.estimate_annual_cost("water_spray")
        # Dry climate: higher temp + low rainfall + dry coal → more applications
        # Compare per-m² volume to normalize for area differences
        vol_dry = est_dry.total_product_volume_L_yr / est_dry.total_treated_area_m2
        vol_std = est_std.total_product_volume_L_yr / est_std.total_treated_area_m2
        assert vol_dry > vol_std

    def test_dry_coal_increases_applications(self):
        c_dry = DustSuppressionCostCalculator(5000.0, surface_moisture_pct=5.0)
        c_wet = DustSuppressionCostCalculator(5000.0, surface_moisture_pct=15.0)
        est_dry = c_dry.estimate_annual_cost("water_spray")
        est_wet = c_wet.estimate_annual_cost("water_spray")
        assert est_dry.total_annual_cost_usd > est_wet.total_annual_cost_usd

    def test_high_rainfall_reduces_applications(self):
        c_dry_area = DustSuppressionCostCalculator(5000.0, rainfall_mm_yr=200.0)
        c_wet_area = DustSuppressionCostCalculator(5000.0, rainfall_mm_yr=3000.0)
        est_dry = c_dry_area.estimate_annual_cost("water_spray")
        est_wet = c_wet_area.estimate_annual_cost("water_spray")
        assert est_dry.total_annual_cost_usd > est_wet.total_annual_cost_usd


# ---------------------------------------------------------------------------
# compare_methods()
# ---------------------------------------------------------------------------

class TestCompareMethods:
    def test_returns_all_methods(self, standard_calc):
        results = standard_calc.compare_methods()
        assert len(results) == len(VALID_METHODS)

    def test_sorted_by_cost_effectiveness(self, standard_calc):
        results = standard_calc.compare_methods()
        ratios = [r["cost_effectiveness_ratio"] for r in results]
        assert ratios == sorted(ratios, reverse=True)

    def test_polymer_binder_more_effective_than_water(self, standard_calc):
        results = {r["method"]: r for r in standard_calc.compare_methods()}
        assert results["polymer_binder"]["effectiveness_rating"] > results["water_spray"]["effectiveness_rating"]

    def test_all_have_cost_effectiveness_ratio(self, standard_calc):
        for r in standard_calc.compare_methods():
            assert "cost_effectiveness_ratio" in r
            assert r["cost_effectiveness_ratio"] >= 0


# ---------------------------------------------------------------------------
# Annual water consumption
# ---------------------------------------------------------------------------

class TestWaterConsumption:
    def test_returns_positive_float(self, standard_calc):
        w = standard_calc.annual_water_consumption_m3()
        assert isinstance(w, float)
        assert w > 0

    def test_larger_area_more_water(self):
        c1 = DustSuppressionCostCalculator(10_000.0)
        c2 = DustSuppressionCostCalculator(50_000.0)
        assert c2.annual_water_consumption_m3() > c1.annual_water_consumption_m3()


# ---------------------------------------------------------------------------
# Haul road area
# ---------------------------------------------------------------------------

class TestHaulRoadArea:
    def test_haul_road_area_in_total(self, standard_calc):
        est = standard_calc.estimate_annual_cost("water_spray")
        expected_haul = 2.0 * 1000.0 * 12.0
        assert est.haul_road_area_m2 == pytest.approx(expected_haul, abs=1.0)

    def test_no_haul_road(self, stockpile_only):
        est = stockpile_only.estimate_annual_cost("water_spray")
        assert est.haul_road_area_m2 == 0.0
        assert est.total_treated_area_m2 == pytest.approx(5_000.0, abs=1.0)

    def test_larger_area_proportionally_higher_cost(self):
        c1 = DustSuppressionCostCalculator(10_000.0)
        c2 = DustSuppressionCostCalculator(20_000.0)
        est1 = c1.estimate_annual_cost("polymer_binder")
        est2 = c2.estimate_annual_cost("polymer_binder")
        assert est2.total_annual_cost_usd == pytest.approx(est1.total_annual_cost_usd * 2, rel=0.01)
