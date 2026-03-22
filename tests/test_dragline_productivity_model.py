"""Tests for DraglineProductivityModel."""
import pytest
from src.dragline_productivity_model import (
    DraglineProductivityModel,
    DraglineSpec,
    BenchConditions,
    ShiftSchedule,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def spec():
    return DraglineSpec(
        model_id="BE1570W",
        bucket_capacity_m3=55.0,
        boom_length_m=96.0,
        dump_radius_m=92.0,
        max_dig_depth_m=52.0,
        walking_speed_m_min=0.26,
        slew_speed_deg_s=0.09,
        hoist_speed_m_s=1.2,
        drag_speed_m_s=1.5,
        swing_full_deg=90.0,
    )


@pytest.fixture
def bench():
    return BenchConditions(
        bench_height_m=20.0,
        material_swell_factor=1.25,
        fill_factor=0.90,
        actual_swing_deg=90.0,
        walk_distance_per_cut_m=15.0,
        operator_efficiency=0.85,
    )


@pytest.fixture
def schedule():
    return ShiftSchedule(
        shift_hours=12.0,
        planned_maintenance_hrs=1.0,
        unplanned_delays_hrs=0.5,
        meal_break_hrs=0.5,
    )


@pytest.fixture
def model():
    return DraglineProductivityModel()


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestDraglineSpecValidation:
    def test_negative_bucket_raises(self):
        with pytest.raises(ValueError):
            DraglineSpec("X", -55, 96, 92, 52, 0.26, 0.09, 1.2, 1.5)

    def test_invalid_swing_angle_raises(self):
        with pytest.raises(ValueError, match="swing_full_deg"):
            DraglineSpec("X", 55, 96, 92, 52, 0.26, 0.09, 1.2, 1.5, swing_full_deg=10)

    def test_valid_spec(self, spec):
        assert spec.model_id == "BE1570W"


class TestBenchConditionsValidation:
    def test_zero_height_raises(self):
        with pytest.raises(ValueError, match="bench_height_m"):
            BenchConditions(bench_height_m=0)

    def test_low_swell_raises(self):
        with pytest.raises(ValueError, match="material_swell_factor"):
            BenchConditions(bench_height_m=20, material_swell_factor=0.5)

    def test_high_fill_factor_raises(self):
        with pytest.raises(ValueError, match="fill_factor"):
            BenchConditions(bench_height_m=20, fill_factor=1.5)

    def test_zero_operator_efficiency_raises(self):
        with pytest.raises(ValueError, match="operator_efficiency"):
            BenchConditions(bench_height_m=20, operator_efficiency=0.0)


class TestShiftScheduleValidation:
    def test_delays_exceed_shift_raises(self):
        with pytest.raises(ValueError):
            ShiftSchedule(
                shift_hours=12,
                planned_maintenance_hrs=5,
                unplanned_delays_hrs=5,
                meal_break_hrs=5,
            )

    def test_productive_hours(self, schedule):
        assert schedule.productive_hours == pytest.approx(10.0)

    def test_mechanical_availability(self, schedule):
        expected_ma = (12 - 1) / 12
        assert schedule.mechanical_availability == pytest.approx(expected_ma)


# ---------------------------------------------------------------------------
# Model computation tests
# ---------------------------------------------------------------------------

class TestSwingPenalty:
    def test_90_deg_is_baseline_1(self, model):
        assert model.swing_penalty(90.0) == pytest.approx(1.0)

    def test_wider_swing_increases_penalty(self, model):
        assert model.swing_penalty(120.0) > model.swing_penalty(90.0)

    def test_narrower_swing_reduces_penalty(self, model):
        assert model.swing_penalty(60.0) < model.swing_penalty(90.0)


class TestProductivityComputation:
    def test_positive_productivity(self, model, spec, bench, schedule):
        r = model.compute(spec, bench, schedule)
        assert r.bank_productivity_BCM_hr > 0

    def test_lcm_greater_than_bcm(self, model, spec, bench, schedule):
        """LCM always >= BCM because swell_factor >= 1."""
        r = model.compute(spec, bench, schedule)
        assert r.loose_productivity_LCM_hr >= r.bank_productivity_BCM_hr

    def test_bcm_per_shift_consistent(self, model, spec, bench, schedule):
        r = model.compute(spec, bench, schedule)
        expected = r.bank_productivity_BCM_hr * schedule.productive_hours
        assert r.bcm_per_shift == pytest.approx(expected, rel=0.01)

    def test_monthly_bcm_consistent(self, model, spec, bench, schedule):
        r = model.compute(spec, bench, schedule, shifts_per_day=2, effective_days_per_month=25)
        expected = r.bcm_per_shift * 2 * 25
        assert r.monthly_bcm == pytest.approx(expected, rel=0.01)

    def test_utilisation_in_range(self, model, spec, bench, schedule):
        r = model.compute(spec, bench, schedule)
        assert 0 < r.shift_utilisation_pct <= 100

    def test_wide_swing_warns(self, model, spec, schedule):
        wide_bench = BenchConditions(bench_height_m=20.0, actual_swing_deg=160.0)
        r = model.compute(spec, wide_bench, schedule)
        assert any("swing angle" in n.lower() for n in r.notes)

    def test_low_fill_factor_note(self, model, spec, schedule):
        bench_low_fill = BenchConditions(bench_height_m=20.0, fill_factor=0.70)
        r = model.compute(spec, bench_low_fill, schedule)
        assert any("fill factor" in n.lower() for n in r.notes)

    def test_higher_operator_efficiency_yields_more_bcm(self, model, spec, schedule):
        bench_lo = BenchConditions(bench_height_m=20.0, operator_efficiency=0.70)
        bench_hi = BenchConditions(bench_height_m=20.0, operator_efficiency=0.95)
        r_lo = model.compute(spec, bench_lo, schedule)
        r_hi = model.compute(spec, bench_hi, schedule)
        assert r_hi.bank_productivity_BCM_hr > r_lo.bank_productivity_BCM_hr


class TestSensitivityAnalysis:
    def test_returns_list(self, model, spec, bench, schedule):
        results = model.sensitivity_analysis(spec, bench, schedule)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_keys_present(self, model, spec, bench, schedule):
        results = model.sensitivity_analysis(spec, bench, schedule)
        for row in results:
            assert "swing_deg" in row
            assert "BCM_per_hr" in row

    def test_productivity_decreases_with_wider_swing(self, model, spec, bench, schedule):
        results = model.sensitivity_analysis(spec, bench, schedule, [90, 120, 150])
        bcms = [r["BCM_per_hr"] for r in results]
        assert bcms[0] > bcms[1] > bcms[2]

    def test_custom_angles(self, model, spec, bench, schedule):
        results = model.sensitivity_analysis(spec, bench, schedule, [90, 105])
        assert len(results) == 2
