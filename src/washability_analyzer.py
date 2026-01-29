"""
Washability Analyzer for coal dense medium separation (DMS) optimization.

Models theoretical yield-vs-ash curves from raw float-sink washability data
to determine the optimum cut density for a target clean coal ash content or
yield. Supports dense medium cyclone (DMC) and static bath configurations.

Dense media separation is the primary coal beneficiation process used in
Indonesia, Australia, and South Africa to produce low-ash thermal and
metallurgical coal products.

Methodology references:
- ASTM D4371 — Standard Test Method for Float-and-Sink Analysis of Coal
- Osborne (1988) Coal Preparation Technology, Vol. 1, Graham & Trotman
- Sanders & Schapman (1999) Washability Analysis in Coal Preparation
- ICCP (2011) International Handbook of Coal Petrography

Author: github.com/achmadnaufal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class FloatSinkFraction:
    """A single float-sink fraction from washability analysis.

    Attributes:
        specific_gravity_lower: Lower bound of SG interval (None for lightest fraction).
        specific_gravity_upper: Upper bound of SG interval (None for heaviest fraction).
        weight_pct: Weight percentage of this fraction in the raw coal (0–100).
        ash_pct: Ash content of this fraction on air-dried basis (%).
        sulfur_pct: Total sulfur content of this fraction (%). Optional.
        gcv_kcal_kg: Gross calorific value of this fraction (kcal/kg). Optional.
    """

    specific_gravity_lower: Optional[float]
    specific_gravity_upper: Optional[float]
    weight_pct: float
    ash_pct: float
    sulfur_pct: Optional[float] = None
    gcv_kcal_kg: Optional[float] = None


@dataclass
class WashabilityResult:
    """Output of a washability analysis at a given cut density.

    Attributes:
        cut_density: Dense medium separation specific gravity cutpoint.
        clean_coal_yield_pct: Theoretical yield of floats product (%).
        clean_coal_ash_pct: Weighted mean ash of floats product (%).
        clean_coal_sulfur_pct: Weighted mean sulfur of floats product (%). None if data unavailable.
        clean_coal_gcv_kcal_kg: Weighted mean GCV of floats product. None if unavailable.
        refuse_yield_pct: Theoretical yield of sinks product (%).
        refuse_ash_pct: Weighted mean ash of sinks product (%).
        near_gravity_material_pct: Fraction of feed ±0.1 SG around cut point (NGC index).
        separability_index: Clean/refuse ash ratio (higher = easier separation).
    """

    cut_density: float
    clean_coal_yield_pct: float
    clean_coal_ash_pct: float
    clean_coal_sulfur_pct: Optional[float]
    clean_coal_gcv_kcal_kg: Optional[float]
    refuse_yield_pct: float
    refuse_ash_pct: float
    near_gravity_material_pct: float
    separability_index: float


class WashabilityAnalyzer:
    """Analyzes float-sink data to model DMS yield–ash washability curves.

    Typical workflow:
    1. Instantiate with a list of FloatSinkFraction objects.
    2. Call ``analyze_at_density()`` to inspect a specific cut point.
    3. Call ``find_density_for_target_ash()`` to back-calculate the cut density
       needed to achieve a target clean coal ash specification.
    4. Call ``generate_curve()`` to sweep a SG range and produce a full
       yield-ash curve table.

    Args:
        fractions: Ordered list of FloatSinkFraction objects from lightest to
            heaviest SG. Weights must sum to 100 (validated on construction).
        weight_tolerance: Allowed deviation from 100% sum (default 0.5%).

    Raises:
        ValueError: If fractions list is empty or weights deviate from 100
            by more than ``weight_tolerance``.

    Example::

        fractions = [
            FloatSinkFraction(None, 1.30, 12.5, 3.2, sulfur_pct=0.32, gcv_kcal_kg=6850),
            FloatSinkFraction(1.30, 1.35, 18.0, 7.1, sulfur_pct=0.45, gcv_kcal_kg=6600),
            FloatSinkFraction(1.35, 1.40, 22.5, 11.8, sulfur_pct=0.52, gcv_kcal_kg=6250),
            FloatSinkFraction(1.40, 1.50, 19.0, 22.4, sulfur_pct=0.68, gcv_kcal_kg=5800),
            FloatSinkFraction(1.50, 1.60, 13.5, 38.6, sulfur_pct=0.88, gcv_kcal_kg=5100),
            FloatSinkFraction(1.60, None, 14.5, 68.2, sulfur_pct=1.20, gcv_kcal_kg=3900),
        ]
        analyzer = WashabilityAnalyzer(fractions)
        result = analyzer.analyze_at_density(1.40)
        print(f"Yield at SG 1.40: {result.clean_coal_yield_pct:.1f}%")
        print(f"Clean coal ash: {result.clean_coal_ash_pct:.1f}%")
    """

    def __init__(
        self,
        fractions: List[FloatSinkFraction],
        weight_tolerance: float = 0.5,
    ) -> None:
        if not fractions:
            raise ValueError("fractions list must not be empty.")

        total_weight = sum(f.weight_pct for f in fractions)
        if abs(total_weight - 100.0) > weight_tolerance:
            raise ValueError(
                f"Fraction weights must sum to 100%. Got {total_weight:.2f}%. "
                f"Tolerance: ±{weight_tolerance}%."
            )

        self._fractions = fractions
        self._total_weight = total_weight

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_at_density(self, cut_density: float) -> WashabilityResult:
        """Calculate theoretical DMS product characteristics at a specific cut density.

        Fractions with ``specific_gravity_upper <= cut_density`` (floats) become
        clean coal product; fractions above become refuse.  The uppermost
        boundary fraction is split proportionally when the cut falls within it.

        Args:
            cut_density: Specific gravity cutpoint (e.g., 1.40).

        Returns:
            WashabilityResult with yield, ash, and optional sulfur/GCV for
            both clean coal (floats) and refuse (sinks) products.

        Raises:
            ValueError: If cut_density is outside the SG range of the fractions.
        """
        self._validate_cut_density(cut_density)

        floats: List[Tuple[float, float, Optional[float], Optional[float]]] = []
        sinks: List[Tuple[float, float, Optional[float], Optional[float]]] = []

        for frac in self._fractions:
            sg_upper = frac.specific_gravity_upper
            sg_lower = frac.specific_gravity_lower

            # Fully below cut → goes to clean coal
            if sg_upper is not None and sg_upper <= cut_density:
                floats.append((frac.weight_pct, frac.ash_pct, frac.sulfur_pct, frac.gcv_kcal_kg))
            # Fully above cut → goes to refuse
            elif sg_lower is not None and sg_lower >= cut_density:
                sinks.append((frac.weight_pct, frac.ash_pct, frac.sulfur_pct, frac.gcv_kcal_kg))
            else:
                # Boundary fraction — split by SG position (linear approximation)
                if sg_lower is None:
                    # Lightest fraction: entirely float unless cut is below even this
                    floats.append((frac.weight_pct, frac.ash_pct, frac.sulfur_pct, frac.gcv_kcal_kg))
                elif sg_upper is None:
                    # Heaviest fraction: entirely sink
                    sinks.append((frac.weight_pct, frac.ash_pct, frac.sulfur_pct, frac.gcv_kcal_kg))
                else:
                    # Split fraction
                    split_ratio = (cut_density - sg_lower) / (sg_upper - sg_lower)
                    float_wt = frac.weight_pct * split_ratio
                    sink_wt = frac.weight_pct * (1 - split_ratio)
                    floats.append((float_wt, frac.ash_pct, frac.sulfur_pct, frac.gcv_kcal_kg))
                    sinks.append((sink_wt, frac.ash_pct, frac.sulfur_pct, frac.gcv_kcal_kg))

        clean_yield, clean_ash, clean_sulfur, clean_gcv = self._weighted_averages(floats)
        refuse_yield, refuse_ash, _, _ = self._weighted_averages(sinks)

        ngm = self._near_gravity_material(cut_density, delta=0.1)

        sep_index = (
            round(refuse_ash / clean_ash, 2) if clean_ash > 0 else 0.0
        )

        return WashabilityResult(
            cut_density=cut_density,
            clean_coal_yield_pct=round(clean_yield, 2),
            clean_coal_ash_pct=round(clean_ash, 2),
            clean_coal_sulfur_pct=round(clean_sulfur, 3) if clean_sulfur is not None else None,
            clean_coal_gcv_kcal_kg=round(clean_gcv, 0) if clean_gcv is not None else None,
            refuse_yield_pct=round(refuse_yield, 2),
            refuse_ash_pct=round(refuse_ash, 2),
            near_gravity_material_pct=round(ngm, 2),
            separability_index=sep_index,
        )

    def find_density_for_target_ash(
        self,
        target_ash_pct: float,
        sg_step: float = 0.01,
        sg_min: float = 1.25,
        sg_max: float = 1.80,
    ) -> Optional[float]:
        """Back-calculate the cut density needed to achieve a target clean coal ash.

        Sweeps the SG range in increments of ``sg_step`` and returns the density
        where clean coal ash first meets or falls below the target.

        Args:
            target_ash_pct: Target maximum ash content for clean coal (%).
            sg_step: Step size for SG sweep. Smaller values = finer resolution.
            sg_min: Lower bound of SG search range. Default 1.25.
            sg_max: Upper bound of SG search range. Default 1.80.

        Returns:
            Cut density (float) that meets the target, or None if the target
            is unachievable within the search range.
        """
        sg = sg_min
        while sg <= sg_max:
            try:
                result = self.analyze_at_density(round(sg, 4))
                if result.clean_coal_ash_pct <= target_ash_pct:
                    return round(sg, 3)
            except ValueError:
                pass
            sg += sg_step
        return None

    def generate_curve(
        self,
        sg_min: float = 1.25,
        sg_max: float = 1.70,
        sg_step: float = 0.05,
    ) -> List[WashabilityResult]:
        """Generate a yield–ash curve by sweeping cut densities.

        Args:
            sg_min: Minimum specific gravity to evaluate.
            sg_max: Maximum specific gravity to evaluate.
            sg_step: Interval between evaluated cut densities.

        Returns:
            List of WashabilityResult objects, one per SG step.
        """
        results = []
        sg = sg_min
        while sg <= sg_max + 1e-9:
            try:
                results.append(self.analyze_at_density(round(sg, 4)))
            except ValueError:
                pass
            sg = round(sg + sg_step, 4)
        return results

    def raw_coal_characteristics(self) -> Dict:
        """Return weighted mean characteristics of the raw (as-received) coal.

        Returns:
            Dict with ``raw_ash_pct``, ``raw_sulfur_pct`` (or None),
            ``raw_gcv_kcal_kg`` (or None).
        """
        data = [
            (f.weight_pct, f.ash_pct, f.sulfur_pct, f.gcv_kcal_kg)
            for f in self._fractions
        ]
        _, ash, sulfur, gcv = self._weighted_averages(data)
        return {
            "raw_ash_pct": round(ash, 2),
            "raw_sulfur_pct": round(sulfur, 3) if sulfur is not None else None,
            "raw_gcv_kcal_kg": round(gcv, 0) if gcv is not None else None,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_cut_density(self, sg: float) -> None:
        sg_values = [
            f.specific_gravity_lower for f in self._fractions if f.specific_gravity_lower is not None
        ] + [
            f.specific_gravity_upper for f in self._fractions if f.specific_gravity_upper is not None
        ]
        if not sg_values:
            return
        min_sg = min(sg_values)
        max_sg = max(sg_values)
        if sg < min_sg or sg > max_sg:
            raise ValueError(
                f"cut_density {sg} is outside the SG range [{min_sg}, {max_sg}] "
                "defined by the float-sink fractions."
            )

    @staticmethod
    def _weighted_averages(
        items: List[Tuple[float, float, Optional[float], Optional[float]]]
    ) -> Tuple[float, float, Optional[float], Optional[float]]:
        """Return total weight and weighted-mean ash, sulfur, GCV."""
        if not items:
            return 0.0, 0.0, None, None

        total_wt = sum(w for w, *_ in items)
        if total_wt == 0:
            return 0.0, 0.0, None, None

        weighted_ash = sum(w * a for w, a, *_ in items) / total_wt

        sulfur_data = [(w, s) for w, _, s, _ in items if s is not None]
        weighted_sulfur = (
            sum(w * s for w, s in sulfur_data) / total_wt
            if sulfur_data else None
        )

        gcv_data = [(w, g) for w, _, _, g in items if g is not None]
        weighted_gcv = (
            sum(w * g for w, g in gcv_data) / total_wt
            if gcv_data else None
        )

        return total_wt, weighted_ash, weighted_sulfur, weighted_gcv

    def _near_gravity_material(self, cut_density: float, delta: float = 0.1) -> float:
        """Calculate % of feed within ±delta SG of the cut point (NGC index)."""
        ngm_weight = 0.0
        for frac in self._fractions:
            sg_lo = frac.specific_gravity_lower or 0.0
            sg_hi = frac.specific_gravity_upper or float("inf")
            # Check overlap between fraction SG range and [cut-delta, cut+delta]
            band_lo = cut_density - delta
            band_hi = cut_density + delta
            overlap_lo = max(sg_lo, band_lo)
            overlap_hi = min(sg_hi, band_hi)
            if overlap_hi > overlap_lo:
                frac_range = sg_hi - sg_lo if sg_hi != float("inf") else delta * 2
                if frac_range > 0:
                    overlap_ratio = (overlap_hi - overlap_lo) / frac_range
                    ngm_weight += frac.weight_pct * min(1.0, overlap_ratio)
        return ngm_weight
