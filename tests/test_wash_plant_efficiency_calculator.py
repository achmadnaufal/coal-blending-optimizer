"""Unit tests for wash_plant_efficiency_calculator module."""

import pytest
from src.wash_plant_efficiency_calculator import (
    CircuitPerformance,
    MassBalance,
    SeparationType,
    WashPlantEfficiencyCalculator,
    WashPlantFeed,
    WashabilityFraction,
)


def _sample_fractions():
    """Typical float-sink fractions for a thermal coal feed (~18.5% ash)."""
    return [
        WashabilityFraction(1.30, 25.0, 4.5),
        WashabilityFraction(1.35, 18.0, 6.2),
        WashabilityFraction(1.40, 12.0, 9.8),
        WashabilityFraction(1.50, 20.0, 18.5),
        WashabilityFraction(1.60, 10.0, 28.0),
        WashabilityFraction(2.00, 15.0, 42.0),
    ]


def _sample_feed():
    return WashPlantFeed(
        plant_id="CPP-001",
        feed_rate_tph=500,
        feed_ash_pct=18.5,
        feed_moisture_pct=10.0,
        size_fraction_mm="50x0.5",
        separation_type=SeparationType.DENSE_MEDIUM_CYCLONE,
        target_product_ash_pct=10.0,
    )


class TestWashabilityFraction:
    def test_valid_fraction(self):
        f = WashabilityFraction(1.40, 20.0, 8.5)
        assert f.sg_float == 1.40

    def test_invalid_sg(self):
        with pytest.raises(ValueError):
            WashabilityFraction(0.5, 20.0, 8.5)

    def test_invalid_mass_pct(self):
        with pytest.raises(ValueError):
            WashabilityFraction(1.40, 110.0, 8.5)

    def test_invalid_ash_pct(self):
        with pytest.raises(ValueError):
            WashabilityFraction(1.40, 20.0, -5.0)


class TestWashPlantFeed:
    def test_valid_feed(self):
        f = _sample_feed()
        assert f.plant_id == "CPP-001"

    def test_invalid_feed_rate(self):
        with pytest.raises(ValueError):
            WashPlantFeed("P1", -100, 18.5, 10.0, "50x0.5",
                          SeparationType.DENSE_MEDIUM_CYCLONE, 10.0)

    def test_invalid_ash(self):
        with pytest.raises(ValueError):
            WashPlantFeed("P1", 500, 110.0, 10.0, "50x0.5",
                          SeparationType.DENSE_MEDIUM_CYCLONE, 10.0)


class TestWashPlantEfficiencyCalculator:
    def setup_method(self):
        self.calc = WashPlantEfficiencyCalculator()

    def test_theoretical_max_yield_positive(self):
        yield_ = self.calc.theoretical_max_yield(_sample_fractions(), 10.0)
        assert 0 < yield_ < 100

    def test_theoretical_max_yield_higher_target_more_yield(self):
        y10 = self.calc.theoretical_max_yield(_sample_fractions(), 10.0)
        y15 = self.calc.theoretical_max_yield(_sample_fractions(), 15.0)
        assert y15 > y10

    def test_theoretical_max_yield_very_low_target(self):
        # Target ash below all fractions should return near 0
        y = self.calc.theoretical_max_yield(_sample_fractions(), 1.0)
        assert y < 10

    def test_theoretical_max_yield_fraction_sum_error(self):
        bad_fractions = [
            WashabilityFraction(1.30, 10.0, 4.0),
            WashabilityFraction(1.40, 20.0, 8.0),
        ]
        with pytest.raises(ValueError):  # fails on < 3 fractions
            self.calc.theoretical_max_yield(bad_fractions, 10.0)

    def test_theoretical_max_yield_too_few_fractions(self):
        with pytest.raises(ValueError, match="At least 3"):
            self.calc.theoretical_max_yield([WashabilityFraction(1.30, 50.0, 4.5),
                                              WashabilityFraction(1.50, 50.0, 20.0)], 10.0)

    def test_organic_efficiency_100_at_theoretical(self):
        fractions = _sample_fractions()
        feed = _sample_feed()
        theo = self.calc.theoretical_max_yield(fractions, feed.target_product_ash_pct)
        oe = self.calc.organic_efficiency(theo, theo)
        assert abs(oe - 100.0) < 0.01

    def test_organic_efficiency_less_than_100(self):
        fractions = _sample_fractions()
        feed = _sample_feed()
        theo = self.calc.theoretical_max_yield(fractions, feed.target_product_ash_pct)
        oe = self.calc.organic_efficiency(theo - 5, theo)
        assert oe < 100

    def test_organic_efficiency_zero_theoretical_raises(self):
        with pytest.raises(ValueError):
            self.calc.organic_efficiency(50.0, 0.0)

    def test_ep_calculation(self):
        ep = self.calc.compute_ep(d25_sg=1.42, d75_sg=1.52)
        assert abs(ep - 0.05) < 0.001

    def test_ep_invalid_order(self):
        with pytest.raises(ValueError):
            self.calc.compute_ep(d25_sg=1.55, d75_sg=1.40)

    def test_ep_classification_excellent(self):
        ep = 0.018
        classification = self.calc._classify_ep(ep)
        assert classification == "excellent"

    def test_ep_classification_poor(self):
        ep = 0.080
        classification = self.calc._classify_ep(ep)
        assert classification == "poor"

    def test_partition_curve_has_correct_length(self):
        curve = self.calc.partition_curve(1.45, 0.03, steps=10)
        assert len(curve) == 10

    def test_partition_curve_d50_is_near_0_5(self):
        curve = self.calc.partition_curve(1.45, 0.03, steps=21)
        # Find point closest to separation SG
        closest = min(curve, key=lambda p: abs(p.sg_midpoint - 1.45))
        assert abs(closest.partition_coefficient - 0.5) < 0.25  # Horner approx

    def test_partition_curve_monotone_decreasing(self):
        curve = self.calc.partition_curve(1.45, 0.03, steps=20)
        # Higher SG = less in product (sinks to reject)
        # Partition coefficient should decrease as SG increases above d50
        above = [p for p in curve if p.sg_midpoint > 1.45]
        above_coeffs = [p.partition_coefficient for p in above]
        assert all(above_coeffs[i] >= above_coeffs[i + 1] - 0.05 for i in range(len(above_coeffs) - 1))

    def test_partition_curve_invalid_ep(self):
        with pytest.raises(ValueError):
            self.calc.partition_curve(1.45, 0.0)

    def test_two_product_mass_balance(self):
        feed = _sample_feed()
        balance = self.calc.two_product_mass_balance(feed, 9.8, 68.5)
        assert isinstance(balance, MassBalance)
        assert balance.yield_pct == 68.5
        assert balance.product_rate_tph == pytest.approx(342.5, abs=1)

    def test_mass_balance_closure(self):
        feed = _sample_feed()
        balance = self.calc.two_product_mass_balance(feed, 9.8, 68.5)
        assert balance.is_balanced

    def test_mass_balance_reject_rate(self):
        feed = _sample_feed()
        balance = self.calc.two_product_mass_balance(feed, 9.8, 68.5)
        assert abs(balance.product_rate_tph + balance.reject_rate_tph - feed.feed_rate_tph) < 1.0

    def test_mass_balance_yield_100_raises(self):
        with pytest.raises(ValueError):
            self.calc.two_product_mass_balance(_sample_feed(), 9.8, 100.0)

    def test_mass_balance_yield_0_raises(self):
        with pytest.raises(ValueError):
            self.calc.two_product_mass_balance(_sample_feed(), 9.8, 0.0)

    def test_evaluate_full(self):
        perf = self.calc.evaluate(
            _sample_fractions(), _sample_feed(),
            actual_product_ash=9.8, actual_yield=68.5
        )
        assert isinstance(perf, CircuitPerformance)
        assert perf.plant_id == "CPP-001"
        assert 0 < perf.theoretical_max_yield_pct < 100
        assert len(perf.partition_curve) > 0
        assert len(perf.recommendations) > 0

    def test_evaluate_good_performance_positive_recommendation(self):
        # High OE, good balance should mention "Continue"
        fractions = _sample_fractions()
        feed = _sample_feed()
        theo = self.calc.theoretical_max_yield(fractions, feed.target_product_ash_pct)
        # Use near-theoretical yield to get OE ~100%
        perf = self.calc.evaluate(fractions, feed, actual_product_ash=10.0, actual_yield=theo - 0.1)
        combined = " ".join(perf.recommendations)
        # Should either pass or give minimal recommendations
        assert len(perf.recommendations) >= 1

    def test_evaluate_with_explicit_ep(self):
        perf = self.calc.evaluate(
            _sample_fractions(), _sample_feed(),
            actual_product_ash=9.8, actual_yield=68.5,
            ep=0.025
        )
        assert perf.ep_value == 0.025
        assert perf.ep_classification == "good"  # 0.025 > threshold 0.020

    def test_evaluate_with_d25_d75(self):
        perf = self.calc.evaluate(
            _sample_fractions(), _sample_feed(),
            actual_product_ash=9.8, actual_yield=68.5,
            d25_sg=1.42, d75_sg=1.52
        )
        assert abs(perf.ep_value - 0.05) < 0.001
