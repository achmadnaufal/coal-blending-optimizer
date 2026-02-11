"""Unit tests for src.port_inventory_planner.

Covers: CoalProduct validation, InventoryTransaction validation, VesselOrder
validation, StockpileConstraints, PortInventoryPlanner (setup, inventory_at_day,
projection, check_vessel_feasibility, capacity_utilisation, days_of_stock,
export_plan_summary), and edge cases.
"""

import pytest
from src.port_inventory_planner import (
    CoalProduct,
    InventoryTransaction,
    VesselOrder,
    StockpileConstraints,
    PortInventoryPlanner,
    DEFAULT_SAFETY_STOCK_DAYS,
    CONGESTION_ALERT_THRESHOLD_PCT,
)


# ---------------------------------------------------------------------------
# CoalProduct tests
# ---------------------------------------------------------------------------


class TestCoalProduct:
    def test_basic_creation(self):
        p = CoalProduct("GAR5000", 5000.0, 8.0, 18.0, 65.0)
        assert p.product_code == "GAR5000"
        assert p.calorific_value_kcal == 5000.0

    def test_empty_code_raises(self):
        with pytest.raises(ValueError, match="product_code"):
            CoalProduct("", 5000.0, 8.0, 18.0, 65.0)

    def test_out_of_range_gcv_raises(self):
        with pytest.raises(ValueError, match="calorific_value_kcal"):
            CoalProduct("X", 1000.0, 8.0, 18.0, 65.0)

    def test_negative_price_raises(self):
        with pytest.raises(ValueError, match="price_usd_per_tonne"):
            CoalProduct("X", 5000.0, 8.0, 18.0, price_usd_per_tonne=-10.0)

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError, match="storage_category"):
            CoalProduct("X", 5000.0, 8.0, 18.0, storage_category="hazardous")


# ---------------------------------------------------------------------------
# InventoryTransaction tests
# ---------------------------------------------------------------------------


class TestInventoryTransaction:
    def test_basic_creation(self):
        tx = InventoryTransaction("TX001", "GAR5000", day=1, quantity_tonnes=50_000.0)
        assert tx.is_inflow is True

    def test_outflow(self):
        tx = InventoryTransaction("TX002", "GAR5000", day=3, quantity_tonnes=-30_000.0)
        assert tx.is_inflow is False

    def test_zero_quantity_raises(self):
        with pytest.raises(ValueError, match="quantity_tonnes"):
            InventoryTransaction("TX", "X", day=1, quantity_tonnes=0.0)

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="transaction_type"):
            InventoryTransaction("TX", "X", day=1, quantity_tonnes=1000.0,
                                  transaction_type="dispatch")

    def test_negative_day_raises(self):
        with pytest.raises(ValueError, match="day"):
            InventoryTransaction("TX", "X", day=-1, quantity_tonnes=1000.0)


# ---------------------------------------------------------------------------
# VesselOrder tests
# ---------------------------------------------------------------------------


class TestVesselOrder:
    def test_basic_creation(self):
        vo = VesselOrder("MV_STAR", "GAR5000", 75_000.0, loading_day=5)
        assert vo.vessel_id == "MV_STAR"
        assert vo.loading_hours == pytest.approx(25.0)

    def test_min_max_quantity(self):
        vo = VesselOrder("V1", "X", 100_000.0, 3, tolerance_pct=5.0)
        assert vo.min_quantity == pytest.approx(95_000.0)
        assert vo.max_quantity == pytest.approx(105_000.0)

    def test_zero_quantity_raises(self):
        with pytest.raises(ValueError, match="quantity_tonnes"):
            VesselOrder("V1", "X", 0.0, 3)

    def test_negative_day_raises(self):
        with pytest.raises(ValueError, match="loading_day"):
            VesselOrder("V1", "X", 50_000.0, -1)

    def test_excessive_tolerance_raises(self):
        with pytest.raises(ValueError, match="tolerance_pct"):
            VesselOrder("V1", "X", 50_000.0, 3, tolerance_pct=25.0)

    def test_low_loading_rate_raises(self):
        with pytest.raises(ValueError, match="loading_rate_tph"):
            VesselOrder("V1", "X", 50_000.0, 3, loading_rate_tph=100.0)


# ---------------------------------------------------------------------------
# StockpileConstraints tests
# ---------------------------------------------------------------------------


class TestStockpileConstraints:
    def test_basic_creation(self):
        c = StockpileConstraints(max_capacity_tonnes=1_500_000.0)
        assert c.max_capacity_tonnes == 1_500_000.0

    def test_zero_capacity_raises(self):
        with pytest.raises(ValueError, match="max_capacity_tonnes"):
            StockpileConstraints(max_capacity_tonnes=0.0)

    def test_zero_reclaim_rate_raises(self):
        with pytest.raises(ValueError, match="reclaim_rate_tph"):
            StockpileConstraints(max_capacity_tonnes=1_000_000.0, reclaim_rate_tph=0.0)


# ---------------------------------------------------------------------------
# PortInventoryPlanner construction
# ---------------------------------------------------------------------------


class TestPlannerInit:
    def test_valid_creation(self):
        p = PortInventoryPlanner("Test Terminal", 30)
        assert p.terminal_name == "Test Terminal"
        assert p.horizon == 30

    def test_empty_terminal_name_raises(self):
        with pytest.raises(ValueError, match="terminal_name"):
            PortInventoryPlanner("", 30)

    def test_too_short_horizon_raises(self):
        with pytest.raises(ValueError, match="planning_horizon_days"):
            PortInventoryPlanner("T", 5)

    def test_too_long_horizon_raises(self):
        with pytest.raises(ValueError, match="planning_horizon_days"):
            PortInventoryPlanner("T", 100)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def product():
    return CoalProduct("GAR5000", 5000.0, 8.0, 18.0, 65.0)


@pytest.fixture
def planner(product):
    p = PortInventoryPlanner("Muara Jawa Terminal", 30)
    p.register_product(product)
    p.set_opening_stock("GAR5000", 300_000.0)
    constraints = StockpileConstraints(max_capacity_tonnes=1_500_000.0)
    p.set_constraints(constraints)
    return p


# ---------------------------------------------------------------------------
# Inventory tracking tests
# ---------------------------------------------------------------------------


class TestInventoryAtDay:
    def test_opening_stock_at_day_minus_1(self, planner):
        # Before any transactions, stock = opening stock
        stock = planner.inventory_at_day("GAR5000", 0)
        # No transactions on day 0, opening stock = 300k
        assert stock == pytest.approx(300_000.0)

    def test_receipt_adds_to_inventory(self, planner):
        tx = InventoryTransaction("TX01", "GAR5000", day=3, quantity_tonnes=50_000.0,
                                   transaction_type="receipt")
        planner.add_transaction(tx)
        stock = planner.inventory_at_day("GAR5000", 3)
        assert stock == pytest.approx(350_000.0)

    def test_loading_reduces_inventory(self, planner):
        vo = VesselOrder("MV1", "GAR5000", 80_000.0, loading_day=5)
        planner.add_vessel_order(vo)
        stock = planner.inventory_at_day("GAR5000", 5)
        assert stock == pytest.approx(220_000.0)

    def test_stock_floored_at_zero(self, planner):
        # Load more than available
        vo = VesselOrder("MV1", "GAR5000", 500_000.0, loading_day=1)
        planner.add_vessel_order(vo)
        stock = planner.inventory_at_day("GAR5000", 1)
        assert stock == 0.0

    def test_unregistered_product_raises(self, planner):
        with pytest.raises(ValueError, match="not registered"):
            planner.inventory_at_day("UNKNOWN", 5)


# ---------------------------------------------------------------------------
# Projection tests
# ---------------------------------------------------------------------------


class TestProjection:
    def test_projection_length(self, planner):
        proj = planner.projection("GAR5000")
        assert len(proj) == planner.horizon

    def test_projection_structure(self, planner):
        proj = planner.projection("GAR5000")
        for row in proj:
            assert "day" in row
            assert "closing_balance_t" in row
            assert "below_safety_stock" in row

    def test_projection_balance_decreases_with_loading(self, planner):
        planner.add_vessel_order(VesselOrder("MV1", "GAR5000", 100_000.0, loading_day=5))
        proj = planner.projection("GAR5000")
        # Balance on day 5 should be lower than day 4
        assert proj[5]["closing_balance_t"] < proj[4]["closing_balance_t"]

    def test_projection_balance_increases_with_receipt(self, planner):
        planner.add_transaction(InventoryTransaction("TX1", "GAR5000", 10, 80_000.0))
        proj = planner.projection("GAR5000")
        assert proj[10]["closing_balance_t"] > proj[9]["closing_balance_t"]


# ---------------------------------------------------------------------------
# Vessel feasibility tests
# ---------------------------------------------------------------------------


class TestVesselFeasibility:
    def test_feasible_when_stock_sufficient(self, planner):
        vo = VesselOrder("MV1", "GAR5000", 200_000.0, loading_day=5)
        result = planner.check_vessel_feasibility(vo)
        assert result["feasible"] is True
        assert result["shortfall_t"] == 0.0

    def test_infeasible_when_stock_insufficient(self, planner):
        vo = VesselOrder("MV1", "GAR5000", 400_000.0, loading_day=1)
        result = planner.check_vessel_feasibility(vo)
        assert result["feasible"] is False
        assert result["shortfall_t"] > 0

    def test_unregistered_product_infeasible(self, planner):
        vo = VesselOrder("MV1", "UNKNOWN", 50_000.0, loading_day=1)
        result = planner.check_vessel_feasibility(vo)
        assert result["feasible"] is False
        assert len(result["alerts"]) > 0

    def test_loading_hours_in_result(self, planner):
        vo = VesselOrder("MV1", "GAR5000", 75_000.0, loading_day=3)
        result = planner.check_vessel_feasibility(vo)
        assert "estimated_loading_hours" in result
        assert result["estimated_loading_hours"] == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Capacity utilisation tests
# ---------------------------------------------------------------------------


class TestCapacityUtilisation:
    def test_utilisation_computed(self, planner):
        result = planner.capacity_utilisation(day=0)
        assert "utilisation_pct" in result
        assert 0.0 <= result["utilisation_pct"] <= 100.0

    def test_congestion_alert_when_near_full(self):
        p = PortInventoryPlanner("T", 30)
        product = CoalProduct("X", 5000.0, 8.0, 18.0)
        p.register_product(product)
        p.set_opening_stock("X", 1_400_000.0)  # 93% of 1.5M cap
        constraints = StockpileConstraints(max_capacity_tonnes=1_500_000.0)
        p.set_constraints(constraints)
        result = p.capacity_utilisation(0)
        assert result["congestion_alert"] is True

    def test_no_congestion_when_low_utilisation(self, planner):
        result = planner.capacity_utilisation(0)
        # 300k / 1.5M = 20% → no congestion
        assert result["congestion_alert"] is False


# ---------------------------------------------------------------------------
# Days of stock
# ---------------------------------------------------------------------------


class TestDaysOfStock:
    def test_days_of_stock_computed(self, planner):
        planner.add_vessel_order(VesselOrder("MV1", "GAR5000", 60_000.0, loading_day=10))
        result = planner.days_of_stock("GAR5000", day=0)
        assert result is not None
        assert result > 0

    def test_no_vessels_returns_none(self, planner):
        result = planner.days_of_stock("GAR5000", day=0)
        assert result is None


# ---------------------------------------------------------------------------
# Export plan summary
# ---------------------------------------------------------------------------


class TestExportPlanSummary:
    def test_summary_structure(self, planner):
        planner.add_vessel_order(VesselOrder("MV1", "GAR5000", 100_000.0, loading_day=5))
        summary = planner.export_plan_summary()
        assert summary["terminal_name"] == "Muara Jawa Terminal"
        assert "n_vessel_orders" in summary
        assert "all_feasible" in summary

    def test_all_feasible_true_when_stock_sufficient(self, planner):
        planner.add_vessel_order(VesselOrder("MV1", "GAR5000", 100_000.0, loading_day=5))
        summary = planner.export_plan_summary()
        assert summary["all_feasible"] is True

    def test_all_feasible_false_when_shortage(self, planner):
        planner.add_vessel_order(VesselOrder("MV1", "GAR5000", 400_000.0, loading_day=1))
        summary = planner.export_plan_summary()
        assert summary["all_feasible"] is False
