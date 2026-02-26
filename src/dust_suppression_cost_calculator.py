"""
Coal Dust Suppression Cost Calculator
=======================================
Estimate chemical dust suppression costs for coal stockpiles and haul roads
based on stockpile area, moisture content, and dust control method.

Dust suppression is a critical operational cost for coal terminals and
open-cut mines, with both direct cost (chemical, water, labour) and
indirect cost (regulatory compliance, health liability) dimensions.

Methods supported
-----------------
- ``water_spray``     : repeated water application (cheapest, least effective long-term)
- ``polymer_binder``  : synthetic polymer emulsion (e.g., Coherex, EnviroBase)
- ``bitumen_emulsion``: dilute bitumen spray (common in Indonesian/Australian mines)
- ``calcium_chloride``: hygroscopic salt (effective in dry climates, corrosive risk)
- ``lignin_sulphonate``: bio-based binder from pulp-mill byproduct (eco-preferred)

References
----------
- ACARP (2018) Dust Suppression on Coal Haul Roads: Technical Review. Report C26063.
- Rio Tinto (2019) Environmental Guidelines: Coal Dust Management. Internal Standard.
- IFC (2007) Environmental, Health and Safety Guidelines for Coal Mining. World Bank Group.
- SNI 5018:2011 Indonesian Coal Mine Environmental Standard — Dust Management.

Example
-------
>>> from src.dust_suppression_cost_calculator import DustSuppressionCostCalculator
>>> calc = DustSuppressionCostCalculator(
...     stockpile_area_m2=25000.0,
...     haul_road_length_km=3.5,
...     haul_road_width_m=12.0,
...     ambient_temperature_c=32.0,
...     rainfall_mm_yr=1800.0,
...     surface_moisture_pct=8.0,
... )
>>> result = calc.estimate_annual_cost("polymer_binder")
>>> print(f"Annual cost: USD {result.total_annual_cost_usd:,.0f}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

SuppressionMethod = Literal[
    "water_spray", "polymer_binder", "bitumen_emulsion",
    "calcium_chloride", "lignin_sulphonate"
]

VALID_METHODS = frozenset([
    "water_spray", "polymer_binder", "bitumen_emulsion",
    "calcium_chloride", "lignin_sulphonate"
])

# ---------------------------------------------------------------------------
# Application rate and cost tables
# ---------------------------------------------------------------------------

# Application rate (L/m²/application)
_APPLICATION_RATE_L_M2: Dict[str, float] = {
    "water_spray": 1.5,
    "polymer_binder": 0.5,
    "bitumen_emulsion": 0.6,
    "calcium_chloride": 0.4,
    "lignin_sulphonate": 0.55,
}

# Applications per year (base; adjusted for climate)
_BASE_APPLICATIONS_PER_YEAR: Dict[str, float] = {
    "water_spray": 52.0,    # weekly
    "polymer_binder": 12.0, # monthly
    "bitumen_emulsion": 8.0,
    "calcium_chloride": 6.0,
    "lignin_sulphonate": 10.0,
}

# Chemical unit cost (USD/L diluted product)
_CHEMICAL_COST_USD_PER_L: Dict[str, float] = {
    "water_spray": 0.002,           # only water cost
    "polymer_binder": 0.45,
    "bitumen_emulsion": 0.22,
    "calcium_chloride": 0.18,
    "lignin_sulphonate": 0.28,
}

# Labour hours per application per 1000 m² area
_LABOUR_HOURS_PER_1000M2: Dict[str, float] = {
    "water_spray": 0.8,
    "polymer_binder": 1.0,
    "bitumen_emulsion": 1.2,
    "calcium_chloride": 0.9,
    "lignin_sulphonate": 1.0,
}

# Equipment operating cost (USD/hr)
_EQUIPMENT_COST_USD_HR: Dict[str, float] = {
    "water_spray": 35.0,      # water truck
    "polymer_binder": 55.0,   # spray truck + pump
    "bitumen_emulsion": 60.0, # bitumen tanker
    "calcium_chloride": 45.0,
    "lignin_sulphonate": 50.0,
}

# Effectiveness rating (0–1): fraction of dust emissions suppressed vs untreated
_EFFECTIVENESS: Dict[str, float] = {
    "water_spray": 0.55,
    "polymer_binder": 0.85,
    "bitumen_emulsion": 0.80,
    "calcium_chloride": 0.78,
    "lignin_sulphonate": 0.82,
}

# Labour cost (USD/hr, Indonesian mine operator rate incl. overhead)
_LABOUR_RATE_USD_HR: float = 8.0


@dataclass
class DustSuppressionEstimate:
    """Annual cost estimate for a dust suppression programme."""
    method: str
    stockpile_area_m2: float
    haul_road_area_m2: float
    total_treated_area_m2: float

    # Volume
    total_applications_per_year: float
    total_product_volume_L_yr: float

    # Cost components (USD/yr)
    chemical_cost_usd_yr: float
    labour_cost_usd_yr: float
    equipment_cost_usd_yr: float
    total_annual_cost_usd: float

    # Efficiency metrics
    cost_per_m2_usd: float
    effectiveness_rating: float    # 0–1 fraction of emissions suppressed
    cost_per_tonne_suppressed_usd: Optional[float]  # if dust_generation_rate known

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "total_treated_area_m2": round(self.total_treated_area_m2, 0),
            "applications_per_year": round(self.total_applications_per_year, 1),
            "product_volume_L_yr": round(self.total_product_volume_L_yr, 0),
            "chemical_cost_usd_yr": round(self.chemical_cost_usd_yr, 0),
            "labour_cost_usd_yr": round(self.labour_cost_usd_yr, 0),
            "equipment_cost_usd_yr": round(self.equipment_cost_usd_yr, 0),
            "total_annual_cost_usd": round(self.total_annual_cost_usd, 0),
            "cost_per_m2_usd": round(self.cost_per_m2_usd, 4),
            "effectiveness_rating": self.effectiveness_rating,
            "cost_per_tonne_suppressed_usd": (
                round(self.cost_per_tonne_suppressed_usd, 2)
                if self.cost_per_tonne_suppressed_usd is not None else None
            ),
        }


class DustSuppressionCostCalculator:
    """
    Calculate annual cost of coal dust suppression for stockpiles and haul roads.

    Parameters
    ----------
    stockpile_area_m2 : float
        Total exposed coal stockpile surface area in m² (must be ≥ 0).
    haul_road_length_km : float
        Total haul road length requiring dust suppression in km (≥ 0).
    haul_road_width_m : float
        Average haul road width in metres (> 0; default 12 m for single-lane).
    ambient_temperature_c : float
        Mean annual ambient temperature (°C). Higher temperatures increase
        water spray application frequency.
    rainfall_mm_yr : float
        Mean annual rainfall in mm. High rainfall reduces application frequency
        for water-based methods.
    surface_moisture_pct : float
        Mean surface moisture content of coal (%). Dry coal (<6%) requires
        more frequent treatment.
    dust_generation_rate_kg_m2_yr : float, optional
        Estimated untreated dust generation (kg/m²/yr). Used to compute
        cost-per-tonne-suppressed metric.
    """

    def __init__(
        self,
        stockpile_area_m2: float,
        haul_road_length_km: float = 0.0,
        haul_road_width_m: float = 12.0,
        ambient_temperature_c: float = 28.0,
        rainfall_mm_yr: float = 1800.0,
        surface_moisture_pct: float = 10.0,
        dust_generation_rate_kg_m2_yr: Optional[float] = None,
    ) -> None:
        if stockpile_area_m2 < 0:
            raise ValueError("stockpile_area_m2 must be >= 0")
        if haul_road_length_km < 0:
            raise ValueError("haul_road_length_km must be >= 0")
        if haul_road_width_m <= 0:
            raise ValueError("haul_road_width_m must be > 0")
        if not (-20.0 <= ambient_temperature_c <= 55.0):
            raise ValueError("ambient_temperature_c must be between -20 and 55")
        if rainfall_mm_yr < 0:
            raise ValueError("rainfall_mm_yr must be >= 0")
        if not (0.0 <= surface_moisture_pct <= 60.0):
            raise ValueError("surface_moisture_pct must be between 0 and 60")
        if dust_generation_rate_kg_m2_yr is not None and dust_generation_rate_kg_m2_yr < 0:
            raise ValueError("dust_generation_rate_kg_m2_yr must be >= 0")

        self.stockpile_area_m2 = stockpile_area_m2
        self.haul_road_length_km = haul_road_length_km
        self.haul_road_width_m = haul_road_width_m
        self.ambient_temperature_c = ambient_temperature_c
        self.rainfall_mm_yr = rainfall_mm_yr
        self.surface_moisture_pct = surface_moisture_pct
        self.dust_generation_rate_kg_m2_yr = dust_generation_rate_kg_m2_yr

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate_annual_cost(
        self,
        method: SuppressionMethod,
    ) -> DustSuppressionEstimate:
        """
        Estimate annual dust suppression cost for a given method.

        Parameters
        ----------
        method : SuppressionMethod
            Dust suppression method to evaluate.

        Returns
        -------
        DustSuppressionEstimate
        """
        if method not in VALID_METHODS:
            raise ValueError(f"method must be one of {sorted(VALID_METHODS)}")

        haul_area = self.haul_road_length_km * 1000.0 * self.haul_road_width_m
        total_area = self.stockpile_area_m2 + haul_area

        if total_area == 0:
            raise ValueError("Total treated area is 0. Provide stockpile_area_m2 or haul_road_length_km.")

        apps_per_yr = self._climate_adjusted_applications(method)
        volume_L = total_area * _APPLICATION_RATE_L_M2[method] * apps_per_yr

        # Chemical cost
        chem_cost = volume_L * _CHEMICAL_COST_USD_PER_L[method]

        # Labour cost
        labour_hrs = (_LABOUR_HOURS_PER_1000M2[method] * total_area / 1000.0) * apps_per_yr
        labour_cost = labour_hrs * _LABOUR_RATE_USD_HR

        # Equipment cost
        equip_hrs = labour_hrs  # 1:1 assumption (operator on machine)
        equip_cost = equip_hrs * _EQUIPMENT_COST_USD_HR[method]

        total_cost = chem_cost + labour_cost + equip_cost
        cost_per_m2 = total_cost / total_area if total_area > 0 else 0.0

        # Cost per tonne suppressed
        cost_per_tonne = None
        if self.dust_generation_rate_kg_m2_yr is not None and self.dust_generation_rate_kg_m2_yr > 0:
            dust_suppressed_tonnes = (
                self.dust_generation_rate_kg_m2_yr
                * total_area
                * _EFFECTIVENESS[method]
                / 1000.0
            )
            cost_per_tonne = total_cost / dust_suppressed_tonnes if dust_suppressed_tonnes > 0 else None

        return DustSuppressionEstimate(
            method=method,
            stockpile_area_m2=self.stockpile_area_m2,
            haul_road_area_m2=round(haul_area, 0),
            total_treated_area_m2=round(total_area, 0),
            total_applications_per_year=round(apps_per_yr, 1),
            total_product_volume_L_yr=round(volume_L, 0),
            chemical_cost_usd_yr=round(chem_cost, 0),
            labour_cost_usd_yr=round(labour_cost, 0),
            equipment_cost_usd_yr=round(equip_cost, 0),
            total_annual_cost_usd=round(total_cost, 0),
            cost_per_m2_usd=round(cost_per_m2, 4),
            effectiveness_rating=_EFFECTIVENESS[method],
            cost_per_tonne_suppressed_usd=cost_per_tonne,
        )

    def compare_methods(self) -> List[dict]:
        """
        Compare all supported dust suppression methods sorted by cost-effectiveness
        (effectiveness / cost ratio, descending).

        Returns
        -------
        list of dicts with method, cost, effectiveness, and ratio
        """
        results = []
        for m in sorted(VALID_METHODS):
            try:
                est = self.estimate_annual_cost(m)  # type: ignore
                ratio = est.effectiveness_rating / est.total_annual_cost_usd if est.total_annual_cost_usd > 0 else 0.0
                results.append({
                    **est.to_dict(),
                    "cost_effectiveness_ratio": round(ratio * 10_000, 4),  # × 10k for readability
                })
            except Exception:
                continue
        return sorted(results, key=lambda x: x["cost_effectiveness_ratio"], reverse=True)

    def annual_water_consumption_m3(self) -> float:
        """
        Return the annual water volume consumed if using water_spray method (m³/yr).
        Useful for water-balance planning.
        """
        est = self.estimate_annual_cost("water_spray")
        return round(est.total_product_volume_L_yr / 1000.0, 1)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _climate_adjusted_applications(self, method: str) -> float:
        """
        Adjust base application frequency for climate conditions.

        Hot+dry conditions → more frequent water spray.
        High rainfall → fewer applications needed.
        Dry coal → more frequent.
        """
        base = _BASE_APPLICATIONS_PER_YEAR[method]
        factor = 1.0

        # Temperature adjustment: +5% per 5°C above 25°C
        if self.ambient_temperature_c > 25.0:
            factor *= 1 + 0.05 * ((self.ambient_temperature_c - 25.0) / 5.0)

        # Rainfall adjustment: -10% per 500mm above 1000mm (wetter = less treatment needed)
        if self.rainfall_mm_yr > 1000.0:
            reduction = 0.10 * ((self.rainfall_mm_yr - 1000.0) / 500.0)
            factor *= max(0.5, 1 - reduction)

        # Moisture adjustment: dry coal (< 8%) needs +20% more applications
        if self.surface_moisture_pct < 8.0:
            factor *= 1.20

        return max(1.0, base * factor)
