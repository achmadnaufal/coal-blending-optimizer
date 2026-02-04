"""
Coal Transport Cost Optimizer for mine-to-port logistics planning.

Optimises coal transportation routing and cost allocation across multi-modal
logistics chains (haul road → rail → barge → vessel) from mine gate to
Final Destination port/stockpile.

Transport cost components modelled:
  1. **Mine haul road**: distance, payload capacity, truck cycle time
  2. **Rail**: tonne-km rate, demurrage risk, load/unload handling charges
  3. **Barge/river**: fuel cost/tonne, river-stage-dependent draft restrictions
  4. **Vessel**: freight rate (USD/tonne), port surcharges, laycan penalties

Optimisation objectives:
  - Minimise total landed cost (USD/tonne) at destination port
  - Identify bottleneck segments in the logistics chain
  - Model coal blend routing: split sourcing across multiple mines to target spec

References:
  - Incoterms 2020 (FOB, CFR, CIF cost basis)
  - IEA Coal Market Report 2024 — seaborne freight indices
  - Argus/Platts Newcastle benchmark freight assumptions

Author: github.com/achmadnaufal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class TransportMode(str, Enum):
    """Coal transport mode."""
    HAUL_TRUCK = "haul_truck"
    CONVEYOR = "conveyor"
    RAIL = "rail"
    BARGE = "barge"
    VESSEL = "vessel"


class Incoterm(str, Enum):
    """Trade term defining cost allocation to buyer/seller."""
    FOB = "FOB"    # Free On Board — seller pays to vessel
    CFR = "CFR"    # Cost and Freight — seller pays freight to destination
    CIF = "CIF"    # Cost, Insurance, Freight
    DAP = "DAP"    # Delivered At Place


# Emission factors for transport modes (kgCO2e per tonne-km)
# Source: IPCC 2006 / IMO 4th GHG Study 2020
TRANSPORT_EMISSION_FACTORS: Dict[TransportMode, float] = {
    TransportMode.HAUL_TRUCK: 0.062,  # kg CO2e/t-km (diesel, 60t truck)
    TransportMode.CONVEYOR: 0.008,    # electric conveyor
    TransportMode.RAIL: 0.018,        # diesel loco, Indonesian average
    TransportMode.BARGE: 0.031,       # river/coastal barge
    TransportMode.VESSEL: 0.012,      # Panamax bulk carrier
}


@dataclass
class TransportLeg:
    """A single logistics segment in the coal supply chain.

    Attributes:
        leg_id: Unique identifier.
        mode: Transport mode.
        origin: Origin location name.
        destination: Destination location name.
        distance_km: Distance in kilometres.
        rate_usd_per_tonne_km: Variable cost rate (USD/tonne-km). Use 0 for fixed-cost legs.
        fixed_cost_usd_per_tonne: Fixed handling/loading cost per tonne (USD).
        capacity_tonne_per_month: Maximum throughput capacity (tonnes/month).
        demurrage_rate_usd_per_day: Demurrage rate if applicable (USD/day).
        avg_transit_days: Average transit time (days).
        availability_pct: Operational availability (0–100).
    """

    leg_id: str
    mode: TransportMode
    origin: str
    destination: str
    distance_km: float
    rate_usd_per_tonne_km: float = 0.0
    fixed_cost_usd_per_tonne: float = 0.0
    capacity_tonne_per_month: float = 500_000.0
    demurrage_rate_usd_per_day: float = 0.0
    avg_transit_days: float = 1.0
    availability_pct: float = 95.0

    def __post_init__(self) -> None:
        if self.distance_km < 0:
            raise ValueError("distance_km cannot be negative")
        if self.rate_usd_per_tonne_km < 0:
            raise ValueError("rate_usd_per_tonne_km cannot be negative")
        if self.fixed_cost_usd_per_tonne < 0:
            raise ValueError("fixed_cost_usd_per_tonne cannot be negative")
        if not (0 < self.capacity_tonne_per_month):
            raise ValueError("capacity_tonne_per_month must be positive")
        if not (0 < self.availability_pct <= 100):
            raise ValueError("availability_pct must be 0–100")

    @property
    def variable_cost_usd_per_tonne(self) -> float:
        """Variable transport cost per tonne (rate × distance)."""
        return self.rate_usd_per_tonne_km * self.distance_km

    @property
    def total_cost_usd_per_tonne(self) -> float:
        """Total leg cost per tonne (variable + fixed)."""
        return self.variable_cost_usd_per_tonne + self.fixed_cost_usd_per_tonne

    @property
    def emission_kgco2e_per_tonne(self) -> float:
        """Transport GHG emission per tonne for this leg (kg CO2e)."""
        factor = TRANSPORT_EMISSION_FACTORS.get(self.mode, 0.03)
        return factor * self.distance_km

    @property
    def effective_monthly_capacity(self) -> float:
        """Capacity adjusted for availability (tonnes/month)."""
        return self.capacity_tonne_per_month * (self.availability_pct / 100)


@dataclass
class LogisticsRoute:
    """A complete logistics chain from mine to discharge port.

    Attributes:
        route_id: Unique route identifier.
        route_name: Descriptive name (e.g., 'Berau → Samarinda → Singapore').
        legs: Ordered list of TransportLeg from mine to destination.
        mine_fob_cost_usd_per_tonne: Mine production + FOB stacking cost (USD/t).
        port_charges_usd_per_tonne: Destination port handling charges (USD/t).
        insurance_pct_of_value: Marine insurance as % of cargo value.
        coal_value_usd_per_tonne: Reference coal value for insurance calculation.
    """

    route_id: str
    route_name: str
    legs: List[TransportLeg]
    mine_fob_cost_usd_per_tonne: float = 0.0
    port_charges_usd_per_tonne: float = 3.0
    insurance_pct_of_value: float = 0.1
    coal_value_usd_per_tonne: float = 80.0

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("LogisticsRoute must have at least one leg")
        if self.mine_fob_cost_usd_per_tonne < 0:
            raise ValueError("mine_fob_cost_usd_per_tonne cannot be negative")

    @property
    def total_transport_cost_usd_per_tonne(self) -> float:
        """Sum of all leg costs (USD/tonne)."""
        return sum(leg.total_cost_usd_per_tonne for leg in self.legs)

    @property
    def total_distance_km(self) -> float:
        """Total route distance (km)."""
        return sum(leg.distance_km for leg in self.legs)

    @property
    def total_transit_days(self) -> float:
        """Total estimated transit time (days)."""
        return sum(leg.avg_transit_days for leg in self.legs)

    @property
    def insurance_cost_usd_per_tonne(self) -> float:
        """Marine insurance cost per tonne."""
        return self.coal_value_usd_per_tonne * (self.insurance_pct_of_value / 100)

    @property
    def total_emission_kgco2e_per_tonne(self) -> float:
        """Total transport-related GHG per tonne across all legs."""
        return sum(leg.emission_kgco2e_per_tonne for leg in self.legs)

    @property
    def bottleneck_capacity_tonne_per_month(self) -> float:
        """Minimum effective capacity across all legs (chain bottleneck)."""
        return min(leg.effective_monthly_capacity for leg in self.legs)


@dataclass
class TransportCostResult:
    """Transport cost optimisation result for a route and volume.

    Attributes:
        route_id: Reference route.
        route_name: Display name.
        volume_tonne_per_month: Requested shipment volume.
        is_capacity_feasible: True if route can handle requested volume.
        capacity_utilisation_pct: Volume as % of bottleneck capacity.
        total_landed_cost_usd_per_tonne: Full delivered cost breakdown total.
        cost_breakdown: Itemised cost components (USD/tonne).
        bottleneck_leg: ID of the capacity-constraining leg.
        total_emission_kgco2e_per_tonne: GHG intensity of transport.
        ranked_position: Position in multi-route comparison (1 = cheapest).
    """

    route_id: str
    route_name: str
    volume_tonne_per_month: float
    is_capacity_feasible: bool
    capacity_utilisation_pct: float
    total_landed_cost_usd_per_tonne: float
    cost_breakdown: Dict[str, float]
    bottleneck_leg: Optional[str]
    total_emission_kgco2e_per_tonne: float
    ranked_position: int = 0


class TransportCostOptimizer:
    """Optimises coal logistics costs and identifies least-cost routes.

    Evaluates one or more LogisticsRoute options against volume requirements,
    ranks by landed cost, and surfaces capacity constraints and GHG intensity.

    Example:
        >>> optimizer = TransportCostOptimizer()
        >>> leg1 = TransportLeg("L1", TransportMode.HAUL_TRUCK, "Pit", "ROM Pad", 15, 0.25, 1.5)
        >>> leg2 = TransportLeg("L2", TransportMode.BARGE, "ROM Pad", "Samarinda Port", 85, 0.035, 2.0)
        >>> leg3 = TransportLeg("L3", TransportMode.VESSEL, "Samarinda", "Singapore", 1_450, 0.009, 8.0)
        >>> route = LogisticsRoute("R1", "Pit → Samarinda → Singapore", [leg1, leg2, leg3])
        >>> result = optimizer.evaluate(route, volume_tonne_per_month=250_000)
        >>> print(f"Landed cost: USD {result.total_landed_cost_usd_per_tonne:.2f}/t")
    """

    def __init__(
        self,
        incoterm: Incoterm = Incoterm.CFR,
        include_insurance: bool = True,
    ) -> None:
        """Initialise the optimizer.

        Args:
            incoterm: Trade term for cost boundary (default CFR).
            include_insurance: Whether to include marine insurance in landed cost.
        """
        self.incoterm = incoterm
        self.include_insurance = include_insurance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        route: LogisticsRoute,
        volume_tonne_per_month: float,
    ) -> TransportCostResult:
        """Evaluate a single logistics route for a given monthly volume.

        Args:
            route: LogisticsRoute with ordered transport legs.
            volume_tonne_per_month: Monthly coal volume to transport (tonnes).

        Returns:
            TransportCostResult with cost breakdown and feasibility assessment.
        """
        if not isinstance(route, LogisticsRoute):
            raise TypeError("route must be a LogisticsRoute instance")
        if volume_tonne_per_month <= 0:
            raise ValueError("volume_tonne_per_month must be positive")

        bottleneck_cap = route.bottleneck_capacity_tonne_per_month
        is_feasible = volume_tonne_per_month <= bottleneck_cap
        cap_util = (volume_tonne_per_month / bottleneck_cap) * 100 if bottleneck_cap > 0 else 0

        # Identify bottleneck leg
        bottleneck_leg_id = min(route.legs, key=lambda l: l.effective_monthly_capacity).leg_id

        # Build cost breakdown
        breakdown: Dict[str, float] = {
            "mine_fob": round(route.mine_fob_cost_usd_per_tonne, 3),
            "port_charges": round(route.port_charges_usd_per_tonne, 3),
        }
        for leg in route.legs:
            breakdown[f"leg_{leg.leg_id}_{leg.mode.value}"] = round(leg.total_cost_usd_per_tonne, 3)

        if self.include_insurance and self.incoterm in (Incoterm.CIF, Incoterm.CFR):
            breakdown["insurance"] = round(route.insurance_cost_usd_per_tonne, 3)

        total_landed = sum(breakdown.values())

        return TransportCostResult(
            route_id=route.route_id,
            route_name=route.route_name,
            volume_tonne_per_month=volume_tonne_per_month,
            is_capacity_feasible=is_feasible,
            capacity_utilisation_pct=round(cap_util, 1),
            total_landed_cost_usd_per_tonne=round(total_landed, 2),
            cost_breakdown=breakdown,
            bottleneck_leg=bottleneck_leg_id,
            total_emission_kgco2e_per_tonne=round(route.total_emission_kgco2e_per_tonne, 3),
        )

    def compare_routes(
        self,
        routes: List[LogisticsRoute],
        volume_tonne_per_month: float,
    ) -> List[TransportCostResult]:
        """Evaluate and rank multiple routes by landed cost.

        Args:
            routes: List of LogisticsRoute candidates.
            volume_tonne_per_month: Monthly volume to transport.

        Returns:
            List of TransportCostResult sorted by landed cost ascending (rank 1 = cheapest).

        Raises:
            ValueError: If routes list is empty.
        """
        if not routes:
            raise ValueError("routes list cannot be empty")

        results = [self.evaluate(r, volume_tonne_per_month) for r in routes]
        results.sort(key=lambda x: x.total_landed_cost_usd_per_tonne)
        for i, r in enumerate(results, start=1):
            r.ranked_position = i
        return results

    def sensitivity_analysis(
        self,
        route: LogisticsRoute,
        base_volume: float,
        volume_range_pct: float = 20.0,
        steps: int = 5,
    ) -> List[Dict]:
        """Model landed cost across a range of shipment volumes.

        Args:
            route: Target LogisticsRoute.
            base_volume: Central volume (tonnes/month).
            volume_range_pct: ± % variation from base volume.
            steps: Number of volume steps.

        Returns:
            List of dicts with volume, feasibility, and landed cost per step.
        """
        if steps < 2:
            raise ValueError("steps must be at least 2")
        low = base_volume * (1 - volume_range_pct / 100)
        high = base_volume * (1 + volume_range_pct / 100)
        step_size = (high - low) / (steps - 1)

        output = []
        for i in range(steps):
            vol = low + i * step_size
            result = self.evaluate(route, vol)
            output.append({
                "volume_tonne": round(vol),
                "is_feasible": result.is_capacity_feasible,
                "capacity_utilisation_pct": result.capacity_utilisation_pct,
                "landed_cost_usd_per_tonne": result.total_landed_cost_usd_per_tonne,
            })
        return output
