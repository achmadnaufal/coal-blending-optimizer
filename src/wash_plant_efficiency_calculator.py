"""Wash plant efficiency calculator for coal preparation plants.

Computes Organic Efficiency (OE), Ecart Probable Moyen (EPM/Ep),
partition curves, and mass balance diagnostics for dense medium
cyclone (DMC) and jig-based coal beneficiation circuits.

References:
    Wills & Finch (2016) Wills' Mineral Processing Technology. 8th ed. Elsevier.
    ASTM D4371 (2018) Standard Test Method for Determining the Washability of Coal.
    Luttrell et al. (2007) Advanced gravity concentration of fine particles. Min. Eng.
    King (2001) Modelling and Simulation of Mineral Processing Systems. Butterworth-Heinemann.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class SeparationType(Enum):
    """Coal beneficiation technology type."""
    DENSE_MEDIUM_CYCLONE = "dense_medium_cyclone"
    DENSE_MEDIUM_BATH = "dense_medium_bath"
    JIG = "jig"
    SPIRAL = "spiral"
    FLOTATION = "flotation"
    REFLUX_CLASSIFIER = "reflux_classifier"


@dataclass
class WashabilityFraction:
    """Single specific gravity fraction from a float-sink washability test.

    Args:
        sg_float: Upper SG limit for this fraction (e.g. 1.40 means <1.40 float).
        mass_pct: Mass percentage of raw feed in this SG fraction (0–100).
        ash_pct: Ash content of this fraction on air-dried basis (0–100).
        moisture_pct: Moisture content (0–50).
        cv_mj_kg: Calorific value in MJ/kg (optional, air-dried basis).
    """
    sg_float: float
    mass_pct: float
    ash_pct: float
    moisture_pct: float = 0.0
    cv_mj_kg: Optional[float] = None

    def __post_init__(self) -> None:
        if not 1.0 <= self.sg_float <= 3.0:
            raise ValueError("sg_float must be 1.0–3.0")
        if not 0 <= self.mass_pct <= 100:
            raise ValueError("mass_pct must be 0–100")
        if not 0 <= self.ash_pct <= 100:
            raise ValueError("ash_pct must be 0–100")


@dataclass
class WashPlantFeed:
    """Raw coal feed characteristics for a wash plant circuit.

    Args:
        plant_id: Plant or circuit identifier.
        feed_rate_tph: Feed rate in tonnes per hour.
        feed_ash_pct: Raw feed ash content % (air-dried).
        feed_moisture_pct: Raw feed moisture %.
        size_fraction_mm: Particle size range processed (e.g. "50x0.5").
        separation_type: Beneficiation technology used.
        target_product_ash_pct: Target product ash specification.
        target_yield_pct: Target product mass yield (optional).
    """
    plant_id: str
    feed_rate_tph: float
    feed_ash_pct: float
    feed_moisture_pct: float
    size_fraction_mm: str
    separation_type: SeparationType
    target_product_ash_pct: float
    target_yield_pct: Optional[float] = None

    def __post_init__(self) -> None:
        if self.feed_rate_tph <= 0:
            raise ValueError("feed_rate_tph must be positive")
        if not 0 <= self.feed_ash_pct <= 100:
            raise ValueError("feed_ash_pct must be 0–100")
        if not 0 <= self.target_product_ash_pct <= 100:
            raise ValueError("target_product_ash_pct must be 0–100")


@dataclass
class MassBalance:
    """Two-product mass balance for a wash plant circuit."""
    feed_rate_tph: float
    product_rate_tph: float
    reject_rate_tph: float
    feed_ash_pct: float
    product_ash_pct: float
    reject_ash_pct: float
    yield_pct: float
    organic_efficiency_pct: float
    mass_balance_error_pct: float
    is_balanced: bool  # True if error < 2%


@dataclass
class PartitionCurvePoint:
    """Single point on a partition (Tromp) curve."""
    sg_midpoint: float
    partition_coefficient: float  # 0.0–1.0 (fraction reporting to product)


@dataclass
class CircuitPerformance:
    """Complete wash plant performance assessment."""
    plant_id: str
    separation_sg: float
    ep_value: float          # Ecart Probable Moyen (Ep) — sharpness of separation
    ep_classification: str   # "excellent", "good", "fair", "poor"
    mass_balance: MassBalance
    partition_curve: List[PartitionCurvePoint]
    theoretical_max_yield_pct: float
    actual_yield_pct: float
    yield_recovery_efficiency_pct: float
    recommendations: List[str]


class WashPlantEfficiencyCalculator:
    """Evaluate beneficiation circuit efficiency using float-sink washability data.

    Computes organic efficiency, Ecart Probable Moyen (Ep), partition curve,
    and theoretical vs actual yield comparison for coal wash plant operations.

    Example::

        calc = WashPlantEfficiencyCalculator()
        fractions = [
            WashabilityFraction(1.30, 25.0, 4.5),
            WashabilityFraction(1.35, 18.0, 6.2),
            WashabilityFraction(1.40, 12.0, 9.8),
            WashabilityFraction(1.50, 20.0, 18.5),
            WashabilityFraction(1.60, 10.0, 28.0),
            WashabilityFraction(2.00, 15.0, 42.0),
        ]
        feed = WashPlantFeed(
            plant_id="CPP-001", feed_rate_tph=500, feed_ash_pct=18.5,
            feed_moisture_pct=10.0, size_fraction_mm="50x0.5",
            separation_type=SeparationType.DENSE_MEDIUM_CYCLONE,
            target_product_ash_pct=10.0
        )
        perf = calc.evaluate(fractions, feed, actual_product_ash=9.8, actual_yield=68.5)
    """

    # Ep classification thresholds per Wills & Finch (2016)
    EP_THRESHOLDS = {
        "excellent": 0.020,
        "good": 0.040,
        "fair": 0.060,
    }

    def _validate_fractions(self, fractions: List[WashabilityFraction]) -> None:
        if len(fractions) < 3:
            raise ValueError("At least 3 washability fractions required for meaningful analysis")
        total_mass = sum(f.mass_pct for f in fractions)
        if abs(total_mass - 100.0) > 2.0:
            raise ValueError(
                f"WashabilityFraction mass percentages sum to {total_mass:.1f}%, expected ~100%"
            )

    def _compute_weighted_ash(
        self, fractions: List[WashabilityFraction], cumulative_mass_pct: float
    ) -> float:
        """Compute cumulative weighted ash for fractions up to given mass %."""
        sorted_f = sorted(fractions, key=lambda f: f.sg_float)
        cum_mass = 0.0
        weighted_ash = 0.0
        for f in sorted_f:
            if cum_mass + f.mass_pct <= cumulative_mass_pct:
                weighted_ash += f.ash_pct * f.mass_pct
                cum_mass += f.mass_pct
            else:
                remaining = cumulative_mass_pct - cum_mass
                weighted_ash += f.ash_pct * remaining
                cum_mass += remaining
                break
        return weighted_ash / cumulative_mass_pct if cumulative_mass_pct > 0 else 0.0

    def theoretical_max_yield(
        self,
        fractions: List[WashabilityFraction],
        target_ash_pct: float,
    ) -> float:
        """Determine theoretical maximum yield at the target ash specification.

        Uses float-sink cumulative curve to find the maximum mass recoverable
        at or below the target ash (ideal separation).

        Args:
            fractions: Washability fractions from float-sink analysis.
            target_ash_pct: Target product ash specification.

        Returns:
            Theoretical maximum yield as a percentage.
        """
        self._validate_fractions(fractions)
        sorted_f = sorted(fractions, key=lambda f: f.sg_float)
        cum_mass = 0.0
        weighted_ash = 0.0
        for f in sorted_f:
            new_mass = cum_mass + f.mass_pct
            new_ash = (weighted_ash + f.ash_pct * f.mass_pct) / new_mass
            if new_ash > target_ash_pct:
                # Interpolate to find exact point
                remaining_capacity = (target_ash_pct * cum_mass - weighted_ash) / (f.ash_pct - target_ash_pct)
                cum_mass += max(0.0, remaining_capacity)
                break
            weighted_ash = new_ash * new_mass
            cum_mass = new_mass
        return round(min(cum_mass, 100.0), 2)

    def organic_efficiency(
        self,
        actual_yield_pct: float,
        theoretical_yield_pct: float,
    ) -> float:
        """Compute Organic Efficiency (OE) = actual yield / theoretical max yield × 100.

        OE > 100% indicates the actual product exceeds the theoretical limit
        (data/sampling error). OE < 90% suggests significant misplacement.

        Args:
            actual_yield_pct: Measured product yield from plant operations.
            theoretical_yield_pct: From theoretical_max_yield().

        Returns:
            Organic efficiency percentage.
        """
        if theoretical_yield_pct <= 0:
            raise ValueError("theoretical_yield_pct must be positive")
        return round(actual_yield_pct / theoretical_yield_pct * 100, 2)

    def compute_ep(
        self,
        d25_sg: float,
        d75_sg: float,
    ) -> float:
        """Compute Ecart Probable Moyen (Ep) = (d75 - d25) / 2.

        Ep is the standard sharpness-of-separation metric. Lower = sharper cut.

        Args:
            d25_sg: SG at which 25% of particles report to product (partition coeff = 0.25).
            d75_sg: SG at which 75% of particles report to product (partition coeff = 0.75).

        Returns:
            Ep value.
        """
        if d75_sg < d25_sg:
            raise ValueError("d75_sg must be greater than d25_sg")
        return round((d75_sg - d25_sg) / 2, 4)

    def _classify_ep(self, ep: float) -> str:
        if ep <= self.EP_THRESHOLDS["excellent"]:
            return "excellent"
        elif ep <= self.EP_THRESHOLDS["good"]:
            return "good"
        elif ep <= self.EP_THRESHOLDS["fair"]:
            return "fair"
        else:
            return "poor"

    def partition_curve(
        self,
        separation_sg: float,
        ep: float,
        sg_range: Tuple[float, float] = (1.20, 2.00),
        steps: int = 17,
    ) -> List[PartitionCurvePoint]:
        """Generate a theoretical partition (Tromp) curve using the normal distribution approximation.

        Args:
            separation_sg: Nominal separation SG (d50).
            ep: Ecart Probable Moyen.
            sg_range: SG range to compute partition coefficients for.
            steps: Number of SG points.

        Returns:
            List of PartitionCurvePoints.
        """
        if ep <= 0:
            raise ValueError("ep must be positive")
        points = []
        sg_lo, sg_hi = sg_range
        step = (sg_hi - sg_lo) / (steps - 1)
        for i in range(steps):
            sg = sg_lo + i * step
            # Normal CDF approximation: partition coeff = 1 - Phi((sg - d50) / (Ep * 0.7413))
            z = (sg - separation_sg) / (ep * 0.7413)
            # Rational approximation of normal CDF
            coeff = 1.0 - self._normal_cdf(z)
            points.append(PartitionCurvePoint(
                sg_midpoint=round(sg, 3),
                partition_coefficient=round(max(0.0, min(coeff, 1.0)), 4),
            ))
        return points

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Approximate standard normal CDF using Horner's method (max error < 7.5e-8)."""
        a1, a2, a3, a4, a5 = 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
        p = 0.2316419
        t = 1.0 / (1.0 + p * abs(x))
        poly = t * (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))))
        cdf = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
        return cdf if x >= 0 else 1.0 - cdf

    def two_product_mass_balance(
        self,
        feed: WashPlantFeed,
        product_ash_pct: float,
        yield_pct: float,
    ) -> MassBalance:
        """Compute two-product mass balance and check closure.

        Args:
            feed: WashPlantFeed with raw coal characteristics.
            product_ash_pct: Measured product ash % (air-dried).
            yield_pct: Measured product yield %.

        Returns:
            MassBalance dataclass.
        """
        if not 0 < yield_pct < 100:
            raise ValueError("yield_pct must be between 0 and 100 (exclusive)")
        product_rate = feed.feed_rate_tph * yield_pct / 100
        reject_rate = feed.feed_rate_tph - product_rate
        # Mass balance: feed ash = product yield × product ash + reject yield × reject ash
        reject_yield = 100 - yield_pct
        reject_ash = (
            (feed.feed_ash_pct * 100 - yield_pct * product_ash_pct) / reject_yield
            if reject_yield > 0 else 100.0
        )
        reject_ash = max(0.0, min(reject_ash, 100.0))

        # Check closure: recalculate feed ash from product + reject
        check_feed_ash = (yield_pct * product_ash_pct + reject_yield * reject_ash) / 100
        error_pct = abs(check_feed_ash - feed.feed_ash_pct) / feed.feed_ash_pct * 100

        return MassBalance(
            feed_rate_tph=feed.feed_rate_tph,
            product_rate_tph=round(product_rate, 1),
            reject_rate_tph=round(reject_rate, 1),
            feed_ash_pct=feed.feed_ash_pct,
            product_ash_pct=product_ash_pct,
            reject_ash_pct=round(reject_ash, 2),
            yield_pct=yield_pct,
            organic_efficiency_pct=0.0,  # set in evaluate()
            mass_balance_error_pct=round(error_pct, 3),
            is_balanced=error_pct < 2.0,
        )

    def _generate_recommendations(
        self, ep: str, oe_pct: float, balance: MassBalance
    ) -> List[str]:
        recs = []
        if ep == "poor":
            recs.append(
                "Ep > 0.060: Check DMC medium density control, apex/vortex finder condition,"
                " and feed pressure stability. Consider medium viscosity reduction."
            )
        elif ep == "fair":
            recs.append(
                "Ep 0.040–0.060: Inspect cyclone wear liners and medium pump impeller."
                " Review medium-to-coal ratio (target 3–3.5:1 by volume)."
            )
        if oe_pct < 90:
            recs.append(
                f"Organic Efficiency {oe_pct:.1f}% is below 90%. High near-gravity material"
                " (NGM) misplacement suspected — review separation SG relative to feed density profile."
            )
        if not balance.is_balanced:
            recs.append(
                f"Mass balance error {balance.mass_balance_error_pct:.1f}% exceeds 2% tolerance."
                " Verify belt weightometers, ash analysers calibration, and sampling protocols."
            )
        if balance.reject_ash_pct < 50:
            recs.append(
                f"Reject ash {balance.reject_ash_pct:.1f}% is low — valuable coal may be reporting"
                " to reject. Consider density audit and NGM % measurement."
            )
        if not recs:
            recs.append("Circuit operating within performance targets. Continue routine monitoring.")
        return recs

    def evaluate(
        self,
        fractions: List[WashabilityFraction],
        feed: WashPlantFeed,
        actual_product_ash: float,
        actual_yield: float,
        ep: Optional[float] = None,
        d25_sg: Optional[float] = None,
        d75_sg: Optional[float] = None,
    ) -> CircuitPerformance:
        """Full wash plant performance evaluation.

        Args:
            fractions: Washability test fractions for the feed coal.
            feed: WashPlantFeed configuration.
            actual_product_ash: Measured product ash % from plant.
            actual_yield: Measured product yield % from plant.
            ep: Pre-computed Ep value (if available). If None, estimated from d25/d75.
            d25_sg: SG at 25th percentile partition (for Ep calculation).
            d75_sg: SG at 75th percentile partition (for Ep calculation).

        Returns:
            CircuitPerformance with all metrics and recommendations.
        """
        self._validate_fractions(fractions)

        theo_yield = self.theoretical_max_yield(fractions, feed.target_product_ash_pct)
        oe = self.organic_efficiency(actual_yield, theo_yield)

        # Ep calculation
        if ep is not None:
            ep_val = ep
        elif d25_sg is not None and d75_sg is not None:
            ep_val = self.compute_ep(d25_sg, d75_sg)
        else:
            # Estimate Ep from technology type
            ep_defaults = {
                SeparationType.DENSE_MEDIUM_CYCLONE: 0.025,
                SeparationType.DENSE_MEDIUM_BATH: 0.040,
                SeparationType.JIG: 0.070,
                SeparationType.SPIRAL: 0.080,
                SeparationType.FLOTATION: 0.100,
                SeparationType.REFLUX_CLASSIFIER: 0.035,
            }
            ep_val = ep_defaults.get(feed.separation_type, 0.050)

        ep_class = self._classify_ep(ep_val)

        # Infer separation SG from sorted fractions
        sorted_f = sorted(fractions, key=lambda f: f.sg_float)
        separation_sg = sorted_f[len(sorted_f) // 2].sg_float

        balance = self.two_product_mass_balance(feed, actual_product_ash, actual_yield)
        balance.organic_efficiency_pct = oe

        partition = self.partition_curve(separation_sg, ep_val)
        yield_recovery_eff = round(actual_yield / theo_yield * 100, 2) if theo_yield > 0 else 0.0

        recs = self._generate_recommendations(ep_class, oe, balance)

        return CircuitPerformance(
            plant_id=feed.plant_id,
            separation_sg=separation_sg,
            ep_value=ep_val,
            ep_classification=ep_class,
            mass_balance=balance,
            partition_curve=partition,
            theoretical_max_yield_pct=theo_yield,
            actual_yield_pct=actual_yield,
            yield_recovery_efficiency_pct=yield_recovery_eff,
            recommendations=recs,
        )
