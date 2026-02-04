"""Unit tests for TransportCostOptimizer."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transport_cost_optimizer import (
    TransportCostOptimizer,
    TransportLeg,
    LogisticsRoute,
    TransportMode,
    Incoterm,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_leg(leg_id="L1", mode=TransportMode.HAUL_TRUCK, distance=15, rate=0.25, fixed=1.5, capacity=500_000) -> TransportLeg:
    return TransportLeg(
        leg_id=leg_id,
        mode=mode,
        origin="Mine",
        destination="Port",
        distance_km=distance,
        rate_usd_per_tonne_km=rate,
        fixed_cost_usd_per_tonne=fixed,
        capacity_tonne_per_month=capacity,
    )


def make_route(route_id="R1", legs=None) -> LogisticsRoute:
    if legs is None:
        legs = [
            make_leg("L1", TransportMode.HAUL_TRUCK, 15, 0.25, 1.5),
            make_leg("L2", TransportMode.BARGE, 85, 0.035, 2.0),
            make_leg("L3", TransportMode.VESSEL, 1450, 0.009, 3.0),
        ]
    return LogisticsRoute(
        route_id=route_id,
        route_name=f"Route {route_id}",
        legs=legs,
        mine_fob_cost_usd_per_tonne=35.0,
        port_charges_usd_per_tonne=3.0,
    )


# ---------------------------------------------------------------------------
# TransportLeg tests
# ---------------------------------------------------------------------------

class TestTransportLeg:
    def test_variable_cost(self):
        leg = make_leg(distance=100, rate=0.05)
        assert abs(leg.variable_cost_usd_per_tonne - 5.0) < 0.01

    def test_total_cost(self):
        leg = make_leg(distance=100, rate=0.05, fixed=2.0)
        assert abs(leg.total_cost_usd_per_tonne - 7.0) < 0.01

    def test_emission_factor(self):
        leg = make_leg(mode=TransportMode.VESSEL, distance=1000, rate=0.009, fixed=0)
        assert abs(leg.emission_kgco2e_per_tonne - 12.0) < 0.1  # 0.012 * 1000

    def test_effective_capacity(self):
        leg = make_leg(capacity=1_000_000)
        leg.availability_pct = 90
        assert abs(leg.effective_monthly_capacity - 900_000) < 1

    def test_negative_distance_raises(self):
        with pytest.raises(ValueError):
            make_leg(distance=-1)

    def test_invalid_availability_raises(self):
        with pytest.raises(ValueError):
            TransportLeg("L1", TransportMode.RAIL, "A", "B", 100, availability_pct=0)


# ---------------------------------------------------------------------------
# LogisticsRoute tests
# ---------------------------------------------------------------------------

class TestLogisticsRoute:
    def test_total_distance(self):
        route = make_route()
        assert abs(route.total_distance_km - (15 + 85 + 1450)) < 0.01

    def test_total_transport_cost(self):
        route = make_route()
        expected = sum(leg.total_cost_usd_per_tonne for leg in route.legs)
        assert abs(route.total_transport_cost_usd_per_tonne - expected) < 0.01

    def test_bottleneck_capacity(self):
        legs = [
            make_leg("L1", capacity=1_000_000),
            make_leg("L2", capacity=300_000),   # bottleneck
            make_leg("L3", capacity=800_000),
        ]
        route = LogisticsRoute("R1", "Test", legs)
        assert route.bottleneck_capacity_tonne_per_month == 300_000 * (95 / 100)  # adjusted for availability

    def test_empty_legs_raises(self):
        with pytest.raises(ValueError):
            LogisticsRoute("R1", "Empty", [])


# ---------------------------------------------------------------------------
# TransportCostOptimizer tests
# ---------------------------------------------------------------------------

class TestOptimizer:
    def setup_method(self):
        self.optimizer = TransportCostOptimizer()

    def test_evaluate_returns_result(self):
        route = make_route()
        result = self.optimizer.evaluate(route, 200_000)
        assert result.total_landed_cost_usd_per_tonne > 0
        assert "mine_fob" in result.cost_breakdown

    def test_capacity_feasible(self):
        route = make_route()
        result = self.optimizer.evaluate(route, 100_000)
        assert result.is_capacity_feasible is True

    def test_capacity_infeasible(self):
        legs = [make_leg("L1", capacity=50_000)]
        route = LogisticsRoute("R1", "Small Cap", legs)
        result = self.optimizer.evaluate(route, 200_000)
        assert result.is_capacity_feasible is False

    def test_compare_routes_sorted(self):
        cheap_legs = [make_leg("L1", distance=10, rate=0.01, fixed=1.0)]
        expensive_legs = [make_leg("L1", distance=200, rate=0.1, fixed=10.0)]
        routes = [
            LogisticsRoute("R1", "Expensive", expensive_legs, mine_fob_cost_usd_per_tonne=50),
            LogisticsRoute("R2", "Cheap", cheap_legs, mine_fob_cost_usd_per_tonne=20),
        ]
        results = self.optimizer.compare_routes(routes, 100_000)
        assert results[0].ranked_position == 1
        assert results[0].total_landed_cost_usd_per_tonne <= results[1].total_landed_cost_usd_per_tonne

    def test_compare_routes_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            self.optimizer.compare_routes([], 100_000)

    def test_sensitivity_analysis_steps(self):
        route = make_route()
        results = self.optimizer.sensitivity_analysis(route, 200_000, volume_range_pct=10, steps=5)
        assert len(results) == 5

    def test_sensitivity_min_steps_raises(self):
        route = make_route()
        with pytest.raises(ValueError):
            self.optimizer.sensitivity_analysis(route, 200_000, steps=1)

    def test_invalid_route_type_raises(self):
        with pytest.raises(TypeError):
            self.optimizer.evaluate({"route_id": "bad"}, 100_000)

    def test_zero_volume_raises(self):
        route = make_route()
        with pytest.raises(ValueError):
            self.optimizer.evaluate(route, 0)
