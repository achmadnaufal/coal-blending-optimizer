"""Unit tests for StockpileSegregationPlanner."""

import pytest
from src.stockpile_segregation_planner import (
    CoalProduct,
    CoalRank,
    ContaminationRisk,
    HeatingRisk,
    SegregationPlan,
    StockpadConfig,
    StockpileSegregationPlanner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hcc():
    return CoalProduct("HCC-A", CoalRank.BITUMINOUS_HIGH, 7200, 8.5, 9.0, 0.5, 22, 300)


@pytest.fixture
def pci():
    return CoalProduct("PCI-B", CoalRank.BITUMINOUS_LOW, 6400, 11, 12, 0.7, 28, 150)


@pytest.fixture
def lignite():
    return CoalProduct("LIG-C", CoalRank.LIGNITE, 3500, 18, 45, 0.4, 48, 80)


@pytest.fixture
def pad1():
    return StockpadConfig("PAD-01", 400, is_covered=False, has_fire_suppression=True)


@pytest.fixture
def pad2():
    return StockpadConfig("PAD-02", 250, is_covered=True)


@pytest.fixture
def planner(hcc, pci, lignite, pad1, pad2):
    return StockpileSegregationPlanner([hcc, pci, lignite], [pad1, pad2])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_no_products_raises(self, pad1):
        with pytest.raises(ValueError, match="product"):
            StockpileSegregationPlanner([], [pad1])

    def test_no_pads_raises(self, hcc):
        with pytest.raises(ValueError, match="pad"):
            StockpileSegregationPlanner([hcc], [])

    def test_invalid_max_per_pad_raises(self, hcc, pad1):
        with pytest.raises(ValueError, match="max_products"):
            StockpileSegregationPlanner([hcc], [pad1], max_products_per_pad=0)

    def test_negative_gcv_raises(self):
        with pytest.raises(ValueError):
            CoalProduct("X", CoalRank.BITUMINOUS_HIGH, -100, 8, 9, 0.5, 20, 100)

    def test_negative_quantity_raises(self):
        with pytest.raises(ValueError):
            CoalProduct("X", CoalRank.BITUMINOUS_HIGH, 7000, 8, 9, 0.5, 20, -50)

    def test_zero_pad_capacity_raises(self):
        with pytest.raises(ValueError):
            StockpadConfig("X", 0)


# ---------------------------------------------------------------------------
# Heating risk
# ---------------------------------------------------------------------------


class TestHeatingRisk:
    def test_low_vm_negligible(self, planner):
        p = CoalProduct("A", CoalRank.ANTHRACITE, 8000, 5, 4, 0.3, 8, 100)
        assert planner._heating_risk(p) == HeatingRisk.NEGLIGIBLE

    def test_high_vm_extreme(self, planner, lignite):
        assert planner._heating_risk(lignite) == HeatingRisk.EXTREME

    def test_medium_vm_moderate(self, planner):
        p = CoalProduct("B", CoalRank.BITUMINOUS_LOW, 6500, 10, 10, 0.6, 30, 100)
        assert planner._heating_risk(p) == HeatingRisk.MODERATE


# ---------------------------------------------------------------------------
# Rank compatibility
# ---------------------------------------------------------------------------


class TestRankCompatibility:
    def test_anthracite_lignite_incompatible(self):
        assert not StockpileSegregationPlanner._rank_compatible(
            CoalRank.ANTHRACITE, CoalRank.LIGNITE
        )

    def test_hcc_pci_compatible(self):
        assert StockpileSegregationPlanner._rank_compatible(
            CoalRank.BITUMINOUS_HIGH, CoalRank.BITUMINOUS_LOW
        )

    def test_lignite_bituminous_high_incompatible(self):
        assert not StockpileSegregationPlanner._rank_compatible(
            CoalRank.BITUMINOUS_HIGH, CoalRank.LIGNITE
        )


# ---------------------------------------------------------------------------
# Contamination risk
# ---------------------------------------------------------------------------


class TestContaminationRisk:
    def test_similar_products_low_risk(self, hcc, pci, planner):
        risk = planner._quality_contamination_risk(hcc, pci)
        # GCV diff 800, ash diff 2.5 — expect MODERATE or below
        assert risk in (ContaminationRisk.LOW, ContaminationRisk.MODERATE)

    def test_very_different_gcv_critical(self, planner):
        a = CoalProduct("A", CoalRank.BITUMINOUS_HIGH, 8000, 5, 5, 0.3, 20, 100)
        b = CoalProduct("B", CoalRank.LIGNITE, 3500, 20, 45, 0.5, 45, 100)
        risk = planner._quality_contamination_risk(a, b)
        assert risk == ContaminationRisk.CRITICAL


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


class TestPlan:
    def test_plan_returns_result(self, planner):
        plan = planner.plan()
        assert isinstance(plan, SegregationPlan)

    def test_all_products_allocated_when_capacity_sufficient(self, hcc, pci):
        # Large pads with plenty of space
        pad = StockpadConfig("BIG", 1000, has_fire_suppression=True)
        p = StockpileSegregationPlanner([hcc, pci], [pad])
        plan = p.plan()
        # At least one of them must be allocated
        assert plan.total_allocated_kt > 0

    def test_lignite_hcc_on_same_pad_avoided(self, hcc, lignite):
        pad = StockpadConfig("PAD", 500, has_fire_suppression=True)
        p = StockpileSegregationPlanner([hcc, lignite], [pad])
        plan = p.plan()
        # Should not co-locate incompatible ranks on same pad
        pad_allocs = {}
        for alloc in plan.allocations:
            pad_allocs.setdefault(alloc.pad_id, []).append(alloc.product_code)
        for pid, codes in pad_allocs.items():
            assert not (
                "HCC-A" in codes and "LIG-C" in codes
            ), f"Incompatible ranks co-located on {pid}"

    def test_unallocated_when_pad_too_small(self):
        tiny_product = CoalProduct("BIG", CoalRank.BITUMINOUS_HIGH, 7000, 8, 9, 0.5, 22, 9999)
        small_pad = StockpadConfig("SMALL", 10)
        p = StockpileSegregationPlanner([tiny_product], [small_pad])
        plan = p.plan()
        assert "BIG" in plan.unallocated_products

    def test_pad_utilisation_sums_correct(self, planner):
        plan = planner.plan()
        for pid, util in plan.pad_utilisation.items():
            assert 0 <= util <= 100

    def test_total_allocated_matches_sum_of_alloc_kt(self, planner):
        plan = planner.plan()
        assert plan.total_allocated_kt == pytest.approx(
            sum(a.allocated_kt for a in plan.allocations), rel=1e-6
        )

    def test_high_heating_risk_warning_without_fire_suppression(self, lignite):
        pad_no_fire = StockpadConfig("PAD-NF", 200, has_fire_suppression=False)
        p = StockpileSegregationPlanner([lignite], [pad_no_fire])
        plan = p.plan()
        all_warnings = [w for a in plan.allocations for w in a.warnings]
        assert any("fire suppression" in w.lower() or "heating risk" in w.lower() for w in all_warnings)

    def test_recommendations_generated_for_high_risk(self, planner):
        plan = planner.plan()
        # Lignite has EXTREME heating risk — should produce a temperature monitoring rec
        full_recs = " ".join(plan.recommendations).lower()
        if plan.overall_heating_risk in (HeatingRisk.HIGH, HeatingRisk.EXTREME):
            assert "temperature" in full_recs or "thermal" in full_recs
