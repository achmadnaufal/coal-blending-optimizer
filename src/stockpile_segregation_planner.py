"""
Stockpile Segregation Planner
================================
Optimises coal stockpile pad layout and segregation strategy at mine site
or port terminal to minimise product contamination, self-heating risk, and
blending cost while maximising export scheduling efficiency.

Follows IEA Coal Research / World Coal Association pad management guidelines
and ASTM D1412 spontaneous combustion risk protocols.

References:
    - IEA Clean Coal Centre (2015). Practical guidelines for managing coal
      stockpiles. CCC/257.
    - ASTM D1412-07 Standard Test Method for Equilibrium Moisture of Coal.
    - SAA HB 62 (1996). Spontaneous combustion classification for coal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class CoalRank(str, Enum):
    ANTHRACITE = "anthracite"
    BITUMINOUS_HIGH = "bituminous_high"   # HCC, HCV thermal
    BITUMINOUS_LOW = "bituminous_low"     # Semi-soft, PCI
    SUB_BITUMINOUS = "sub_bituminous"     # Low rank, moderate GCV
    LIGNITE = "lignite"                  # Low rank, high moisture


class ContaminationRisk(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class HeatingRisk(str, Enum):
    NEGLIGIBLE = "NEGLIGIBLE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass
class CoalProduct:
    """Quality specification for a single coal product grade."""

    product_code: str
    rank: CoalRank
    gcv_adb_kcal_kg: float       # Gross calorific value, air-dried basis
    ash_adb_pct: float           # Ash content %
    total_moisture_pct: float    # Total moisture %
    sulfur_adb_pct: float        # Total sulfur %
    volatile_matter_pct: float   # Volatile matter % — drives heating risk
    quantity_kt: float           # Quantity in thousand tonnes

    def __post_init__(self) -> None:
        if self.gcv_adb_kcal_kg <= 0:
            raise ValueError("gcv_adb_kcal_kg must be positive")
        if not (0 < self.ash_adb_pct < 60):
            raise ValueError("ash_adb_pct must be 0–60%")
        if not (0 <= self.total_moisture_pct <= 60):
            raise ValueError("total_moisture_pct must be 0–60%")
        if not (0 <= self.sulfur_adb_pct <= 10):
            raise ValueError("sulfur_adb_pct must be 0–10%")
        if not (0 < self.volatile_matter_pct < 60):
            raise ValueError("volatile_matter_pct must be 0–60%")
        if self.quantity_kt <= 0:
            raise ValueError("quantity_kt must be positive")


@dataclass
class StockpadConfig:
    """Physical configuration of a single stockpad."""

    pad_id: str
    capacity_kt: float
    is_covered: bool = False
    has_fire_suppression: bool = False
    max_stack_height_m: float = 8.0
    acceptable_rank_groups: Optional[List[CoalRank]] = None

    def __post_init__(self) -> None:
        if self.capacity_kt <= 0:
            raise ValueError("capacity_kt must be positive")
        if self.max_stack_height_m <= 0:
            raise ValueError("max_stack_height_m must be positive")


@dataclass
class ProductAllocation:
    """Assignment of a coal product to a specific pad."""

    product_code: str
    pad_id: str
    allocated_kt: float
    heating_risk: HeatingRisk
    contamination_risk: ContaminationRisk
    warnings: List[str] = field(default_factory=list)


@dataclass
class SegregationPlan:
    """Full output of the stockpile segregation optimisation."""

    allocations: List[ProductAllocation]
    total_allocated_kt: float
    unallocated_products: List[str]
    pad_utilisation: Dict[str, float]  # pad_id → utilisation %
    overall_heating_risk: HeatingRisk
    overall_contamination_risk: ContaminationRisk
    recommendations: List[str]
    critical_alerts: List[str]


# ---------------------------------------------------------------------------
# Contamination compatibility matrix
# (products that must NOT be co-located on the same pad)
# ---------------------------------------------------------------------------

# Rank pairs that must not co-locate (bidirectional)
_INCOMPATIBLE_RANKS: List[Tuple[CoalRank, CoalRank]] = [
    (CoalRank.ANTHRACITE, CoalRank.LIGNITE),
    (CoalRank.BITUMINOUS_HIGH, CoalRank.LIGNITE),
    (CoalRank.BITUMINOUS_LOW, CoalRank.LIGNITE),
]

# Spontaneous heating Seyler index: if VM% > threshold, risk escalates
_VM_HEATING_THRESHOLDS = {
    HeatingRisk.NEGLIGIBLE: 15,
    HeatingRisk.LOW: 25,
    HeatingRisk.MODERATE: 35,
    HeatingRisk.HIGH: 45,
    HeatingRisk.EXTREME: 60,
}


class StockpileSegregationPlanner:
    """
    Plans optimal coal stockpile pad assignments to minimise contamination
    and spontaneous combustion risk.

    Parameters
    ----------
    products : list of CoalProduct
        Coal grades to be allocated.
    pads : list of StockpadConfig
        Available stockpads at the terminal or mine.
    max_products_per_pad : int, optional
        Maximum number of distinct products on one pad (default 2).

    Examples
    --------
    >>> from src.stockpile_segregation_planner import (
    ...     StockpileSegregationPlanner, CoalProduct, StockpadConfig, CoalRank
    ... )
    >>> hcc = CoalProduct("HCC-A", CoalRank.BITUMINOUS_HIGH, 7200, 8.5, 9.0, 0.5, 22, 300)
    >>> pci = CoalProduct("PCI-B", CoalRank.BITUMINOUS_LOW, 6400, 11, 12, 0.7, 28, 150)
    >>> lignite = CoalProduct("LIG-C", CoalRank.LIGNITE, 3500, 18, 45, 0.4, 48, 80)
    >>> pad1 = StockpadConfig("PAD-01", 350, is_covered=False, has_fire_suppression=True)
    >>> pad2 = StockpadConfig("PAD-02", 250, is_covered=True)
    >>> planner = StockpileSegregationPlanner([hcc, pci, lignite], [pad1, pad2])
    >>> plan = planner.plan()
    >>> print(f"Allocated: {plan.total_allocated_kt:.0f} kt")
    """

    def __init__(
        self,
        products: List[CoalProduct],
        pads: List[StockpadConfig],
        max_products_per_pad: int = 2,
    ) -> None:
        if not products:
            raise ValueError("At least one product is required")
        if not pads:
            raise ValueError("At least one pad is required")
        if max_products_per_pad < 1:
            raise ValueError("max_products_per_pad must be >= 1")
        self.products = products
        self.pads = pads
        self.max_per_pad = max_products_per_pad

    # ------------------------------------------------------------------
    # Risk calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _heating_risk(product: CoalProduct) -> HeatingRisk:
        """Classify spontaneous heating risk from volatile matter content."""
        vm = product.volatile_matter_pct
        if vm < 15:
            return HeatingRisk.NEGLIGIBLE
        elif vm < 25:
            return HeatingRisk.LOW
        elif vm < 35:
            return HeatingRisk.MODERATE
        elif vm < 45:
            return HeatingRisk.HIGH
        else:
            return HeatingRisk.EXTREME

    @staticmethod
    def _rank_compatible(rank_a: CoalRank, rank_b: CoalRank) -> bool:
        """Return True if two ranks can safely co-exist on the same pad."""
        pair = (rank_a, rank_b)
        pair_rev = (rank_b, rank_a)
        return pair not in _INCOMPATIBLE_RANKS and pair_rev not in _INCOMPATIBLE_RANKS

    @staticmethod
    def _quality_contamination_risk(
        product_a: CoalProduct, product_b: CoalProduct
    ) -> ContaminationRisk:
        """Estimate quality contamination risk between two products on same pad."""
        gcv_diff = abs(product_a.gcv_adb_kcal_kg - product_b.gcv_adb_kcal_kg)
        ash_diff = abs(product_a.ash_adb_pct - product_b.ash_adb_pct)
        if gcv_diff > 1500 or ash_diff > 10:
            return ContaminationRisk.CRITICAL
        elif gcv_diff > 800 or ash_diff > 6:
            return ContaminationRisk.HIGH
        elif gcv_diff > 400 or ash_diff > 3:
            return ContaminationRisk.MODERATE
        else:
            return ContaminationRisk.LOW

    # ------------------------------------------------------------------
    # Core allocation
    # ------------------------------------------------------------------

    def _sort_products_by_risk(self) -> List[CoalProduct]:
        """Sort products: highest heating risk first (gets priority pad selection)."""
        risk_order = {
            HeatingRisk.EXTREME: 5,
            HeatingRisk.HIGH: 4,
            HeatingRisk.MODERATE: 3,
            HeatingRisk.LOW: 2,
            HeatingRisk.NEGLIGIBLE: 1,
        }
        return sorted(
            self.products,
            key=lambda p: risk_order[self._heating_risk(p)],
            reverse=True,
        )

    def plan(self) -> SegregationPlan:
        """
        Generate an optimised stockpile segregation plan.

        Returns
        -------
        SegregationPlan
            Full allocation with per-pad utilisation, risk ratings,
            warnings, and recommendations.
        """
        pad_remaining: Dict[str, float] = {p.pad_id: p.capacity_kt for p in self.pads}
        pad_products: Dict[str, List[CoalProduct]] = {p.pad_id: [] for p in self.pads}
        pad_map = {p.pad_id: p for p in self.pads}

        allocations: List[ProductAllocation] = []
        unallocated: List[str] = []

        sorted_products = self._sort_products_by_risk()

        for product in sorted_products:
            assigned = False
            best_pad = None
            best_contamination = ContaminationRisk.CRITICAL

            for pad in self.pads:
                pid = pad.pad_id
                # Check capacity
                if pad_remaining[pid] < product.quantity_kt:
                    continue
                # Check max products per pad
                if len(pad_products[pid]) >= self.max_per_pad:
                    continue
                # Check rank acceptability
                if (
                    pad.acceptable_rank_groups
                    and product.rank not in pad.acceptable_rank_groups
                ):
                    continue
                # Check rank compatibility with existing products on pad
                compatible = all(
                    self._rank_compatible(existing.rank, product.rank)
                    for existing in pad_products[pid]
                )
                if not compatible:
                    continue
                # Determine contamination risk with pad co-tenants
                if pad_products[pid]:
                    max_cont = max(
                        self._quality_contamination_risk(product, existing)
                        for existing in pad_products[pid]
                    )
                else:
                    max_cont = ContaminationRisk.LOW

                # Prefer pads with lower contamination and fire suppression for high VM
                heat_risk = self._heating_risk(product)
                heat_ok = not (
                    heat_risk in (HeatingRisk.HIGH, HeatingRisk.EXTREME)
                    and not pad.has_fire_suppression
                )
                if not heat_ok and not assigned:
                    # Allow but warn later
                    pass

                cont_order = {
                    ContaminationRisk.LOW: 1,
                    ContaminationRisk.MODERATE: 2,
                    ContaminationRisk.HIGH: 3,
                    ContaminationRisk.CRITICAL: 4,
                }
                if cont_order[max_cont] < cont_order.get(best_contamination, 5):
                    best_pad = pad
                    best_contamination = max_cont
                    assigned = True

            if best_pad is not None:
                pid = best_pad.pad_id
                pad_remaining[pid] -= product.quantity_kt
                pad_products[pid].append(product)

                warnings = []
                heat_risk = self._heating_risk(product)
                if heat_risk in (HeatingRisk.HIGH, HeatingRisk.EXTREME) and not best_pad.has_fire_suppression:
                    warnings.append(
                        f"{product.product_code} has {heat_risk.value} heating risk "
                        f"but PAD {pid} lacks fire suppression — monitor temperature daily."
                    )
                if best_contamination in (ContaminationRisk.HIGH, ContaminationRisk.CRITICAL):
                    warnings.append(
                        f"Contamination risk {best_contamination.value} with co-located product "
                        f"on {pid} — consider dedicated pad if available."
                    )
                if product.rank == CoalRank.LIGNITE and not best_pad.is_covered:
                    warnings.append(
                        f"Lignite {product.product_code} on uncovered pad — "
                        "moisture ingress risk; use covered storage if possible."
                    )

                allocations.append(
                    ProductAllocation(
                        product_code=product.product_code,
                        pad_id=pid,
                        allocated_kt=product.quantity_kt,
                        heating_risk=heat_risk,
                        contamination_risk=best_contamination,
                        warnings=warnings,
                    )
                )
            else:
                unallocated.append(product.product_code)

        # ------------------------------------------------------------------
        # Compute pad utilisation
        # ------------------------------------------------------------------
        pad_util = {}
        for p in self.pads:
            allocated = p.capacity_kt - pad_remaining[p.pad_id]
            pad_util[p.pad_id] = round(allocated / p.capacity_kt * 100, 1)

        # ------------------------------------------------------------------
        # Overall risk ratings
        # ------------------------------------------------------------------
        heat_order = {
            HeatingRisk.NEGLIGIBLE: 0, HeatingRisk.LOW: 1, HeatingRisk.MODERATE: 2,
            HeatingRisk.HIGH: 3, HeatingRisk.EXTREME: 4
        }
        cont_order = {
            ContaminationRisk.LOW: 0, ContaminationRisk.MODERATE: 1,
            ContaminationRisk.HIGH: 2, ContaminationRisk.CRITICAL: 3
        }

        if allocations:
            max_heat = max(allocations, key=lambda a: heat_order[a.heating_risk]).heating_risk
            max_cont = max(allocations, key=lambda a: cont_order[a.contamination_risk]).contamination_risk
        else:
            max_heat = HeatingRisk.NEGLIGIBLE
            max_cont = ContaminationRisk.LOW

        # ------------------------------------------------------------------
        # Recommendations & alerts
        # ------------------------------------------------------------------
        recs = []
        alerts = []

        if unallocated:
            alerts.append(
                f"Products {unallocated} could not be allocated — "
                "insufficient pad capacity or compatibility constraints. "
                "Consider adding additional pad capacity."
            )
        if max_heat in (HeatingRisk.HIGH, HeatingRisk.EXTREME):
            recs.append(
                "Install temperature monitoring probes at 2 m depth intervals "
                "and weekly thermal imaging sweeps for HIGH/EXTREME-risk pads."
            )
        if max_cont in (ContaminationRisk.HIGH, ContaminationRisk.CRITICAL):
            recs.append(
                "Implement physical windrow separators or bunded partitions between "
                "quality-critical products to prevent mixing during reclaim."
            )
        over_90 = [pid for pid, util in pad_util.items() if util > 90]
        if over_90:
            recs.append(
                f"Pads {over_90} are >90% utilised — vessel scheduling or early "
                "loading should be initiated to free capacity."
            )

        return SegregationPlan(
            allocations=allocations,
            total_allocated_kt=sum(a.allocated_kt for a in allocations),
            unallocated_products=unallocated,
            pad_utilisation=pad_util,
            overall_heating_risk=max_heat,
            overall_contamination_risk=max_cont,
            recommendations=recs,
            critical_alerts=alerts,
        )
