"""
Washability Curve Analyzer for coal float-sink washability analysis.

Computes cumulative float/sink washability curves, determines optimal wash
density cut points, calculates clean coal yield at target ash specifications,
and compares washability performance across multiple coal sources.

Supports coal preparation engineers in Indonesia, Australia, and South Africa
who need to optimise dense medium separation (DMS) cut points for thermal
and metallurgical coal products.

References:
    - ASTM D5114-90 — Float-Sink Testing of Coal
    - IPCC (2006) Vol. 2 Chapter 4 — Coal Washing Yield Estimation
    - Osborne (1988) Coal Preparation Technology, Vol. 1

Author: github.com/achmadnaufal
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore


@dataclass
class CoalSample:
    """A single coal sample with one or more float-sink density fractions.

    Attributes:
        sample_id: Unique identifier for the sample.
        source: Country / region of origin (e.g. "Indonesia", "Australia", "South Africa").
        mine: Mine or concession name within the source region.
        depth_m: Seam depth from which the sample was collected (metres).
        fractions: Ordered list of (fraction_density, weight_pct, ash_pct, sulfur_pct).
            Fractions are ordered from lightest to heaviest density and should
            represent the complete washability data for the sample (sum of
            weight_pct should be approximately 100).
    """

    sample_id: str
    source: str
    mine: str
    depth_m: float
    fractions: List[Tuple[float, float, float, float]]


class WashabilityAnalyzer:
    """Computes float-sink washability curves and wash-point optimisation.

    Typical workflow::

        analyzer = WashabilityAnalyzer()
        curve = analyzer.build_float_sink_curve(fractions=[
            {"density": 1.3, "weight_pct": 20, "ash_pct": 5.5, "sulfur_pct": 0.4},
            {"density": 1.4, "weight_pct": 35, "ash_pct": 9.2, "sulfur_pct": 0.5},
            {"density": 1.5, "weight_pct": 25, "ash_pct": 14.8, "sulfur_pct": 0.7},
            {"density": 1.8, "weight_pct": 20, "ash_pct": 28.5, "sulfur_pct": 1.2},
        ])
        wash_points = analyzer.determine_wash_points(curve, ash_jump_threshold=5.0)
        yield_10 = analyzer.calculate_wash_yield(curve, target_ash_pct=10.0)
        print(f"Yield at 10% ash: {yield_10:.1f}%")
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Core curve building
    # ------------------------------------------------------------------

    def build_float_sink_curve(
        self,
        fractions: List[Dict[str, float]],
    ) -> "pd.DataFrame":
        """Build cumulative float-sink washability curve from density fraction data.

        Each fraction dict must contain: ``density``, ``weight_pct``, ``ash_pct``,
        ``sulfur_pct``. Fractions are sorted by density ascending and then
        cumulative float (light) and sink (heavy) products are computed.

        Args:
            fractions: List of dicts, each with density (g/cm³), weight_pct (%),
                ash_pct (%), sulfur_pct (%).

        Returns:
            DataFrame with columns:
                - ``density``: cut density (g/cm³)
                - ``float_weight_pct``: cumulative weight % of floats at this density
                - ``float_ash_pct``: weighted mean ash % of floats product
                - ``float_sulfur_pct``: weighted mean sulfur % of floats product
                - ``sink_weight_pct``: cumulative weight % of sinks at this density
                - ``sink_ash_pct``: weighted mean ash % of sinks product
                - ``combustible_recovery_pct``: mass of combustible material
                  recovered in floats (ash-free basis) as % of total feed
        """
        if pd is None:
            raise ImportError("pandas is required for washability analysis")  # pragma: no cover

        if not fractions:
            raise ValueError("fractions list must not be empty")

        # Sort by density ascending
        sorted_fracs = sorted(fractions, key=lambda f: f["density"])

        # Validate and normalise weights
        total_weight = sum(f["weight_pct"] for f in sorted_fracs)
        if total_weight == 0:
            raise ValueError("total fraction weight is zero")

        rows = []
        cumulative_float_wt = 0.0
        cumulative_float_ash_mass = 0.0
        cumulative_float_s_mass = 0.0

        for i, frac in enumerate(sorted_fracs):
            d = frac["density"]
            wt = frac["weight_pct"]
            ash = frac["ash_pct"]
            sul = frac["sulfur_pct"]

            # Add all lighter fractions (including current) as "float"
            cumulative_float_wt += wt
            cumulative_float_ash_mass += wt * ash
            cumulative_float_s_mass += wt * sul

            float_ash = cumulative_float_ash_mass / cumulative_float_wt if cumulative_float_wt > 0 else 0.0
            float_sul = cumulative_float_s_mass / cumulative_float_wt if cumulative_float_wt > 0 else 0.0

            # Sink side: weight above this density
            sink_wt = total_weight - cumulative_float_wt
            sink_ash_mass = sum(f["weight_pct"] * f["ash_pct"] for f in sorted_fracs[i + 1 :])
            sink_ash = sink_ash_mass / sink_wt if sink_wt > 0 else 0.0

            # Combustible recovery: fraction of ash-free mass recovered
            total_ash_free_mass = sum(f["weight_pct"] * (100 - f["ash_pct"]) for f in sorted_fracs)
            float_ash_free_mass = sum(
                f["weight_pct"] * (100 - f["ash_pct"])
                for f in sorted_fracs[: i + 1]
            )
            combustible_rec = (float_ash_free_mass / total_ash_free_mass * 100) if total_ash_free_mass > 0 else 0.0

            rows.append(
                {
                    "density": d,
                    "float_weight_pct": round(cumulative_float_wt, 3),
                    "float_ash_pct": round(float_ash, 3),
                    "float_sulfur_pct": round(float_sul, 3),
                    "sink_weight_pct": round(sink_wt, 3),
                    "sink_ash_pct": round(sink_ash, 3),
                    "combustible_recovery_pct": round(combustible_rec, 3),
                }
            )

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Wash point identification
    # ------------------------------------------------------------------

    def determine_wash_points(
        self,
        curve: "pd.DataFrame",
        ash_jump_threshold: float = 5.0,
    ) -> List[Dict]:
        """Identify optimal wash density points from a washability curve.

        A wash point is where the incremental ash increase between two
        successive density cuts exceeds ``ash_jump_threshold`` (%/g/cm³).
        This indicates a natural "break" in the washability — washing below
        this density produces low-ash clean coal, while washing above
        produces high-ash refuse, with little economical intermediate product.

        Args:
            curve: DataFrame from ``build_float_sink_curve``.
            ash_jump_threshold: Ash jump (% ash per g/cm³) to flag as a wash
                boundary. Default 5.0.

        Returns:
            List of dicts, each with ``density``, ``ash_jump``, and ``is_wash_point``
            boolean. ``is_wash_point`` is True when the ash jump going into
            this density exceeds ``ash_jump_threshold``.
        """
        if pd is None:
            raise ImportError("pandas is required for washability analysis")  # pragma: no cover

        if len(curve) < 2:
            return [
                {
                    "density": row["density"],
                    "ash_jump": 0.0,
                    "is_wash_point": False,
                }
                for _, row in curve.iterrows()
            ]

        results = []
        for i, row in curve.iterrows():
            if i == 0:
                results.append({"density": row["density"], "ash_jump": 0.0, "is_wash_point": False})
                continue

            prev_row = curve.iloc[i - 1]
            delta_d = row["density"] - prev_row["density"]
            if delta_d <= 0:
                results.append({"density": row["density"], "ash_jump": 0.0, "is_wash_point": False})
                continue

            delta_ash = row["float_ash_pct"] - prev_row["float_ash_pct"]
            ash_jump = delta_ash / delta_d

            results.append(
                {
                    "density": row["density"],
                    "ash_jump": round(ash_jump, 3),
                    "is_wash_point": ash_jump > ash_jump_threshold,
                }
            )

        return results

    # ------------------------------------------------------------------
    # Yield at target ash
    # ------------------------------------------------------------------

    def calculate_wash_yield(
        self,
        curve: "pd.DataFrame",
        target_ash_pct: float,
    ) -> float:
        """Return clean coal yield % corresponding to a target ash specification.

        Linear interpolation between the two curve points that bracket
        ``target_ash_pct``. If the target is outside the curve range, the
        nearest endpoint is returned.

        Args:
            curve: DataFrame from ``build_float_sink_curve``.
            target_ash_pct: Desired maximum ash content for clean coal product (%).

        Returns:
            Float yield % (weight basis) at the ash cut point.
        """
        if pd is None:
            raise ImportError("pandas is required for washability analysis")  # pragma: no cover

        if len(curve) == 0:
            return 0.0
        if len(curve) == 1:
            ash_1 = curve.iloc[0]["float_ash_pct"]
            return 0.0 if target_ash_pct < ash_1 else curve.iloc[0]["float_weight_pct"]

        # Find where ash crosses target
        ash_vals = curve["float_ash_pct"].values
        wt_vals = curve["float_weight_pct"].values
        d_vals = curve["density"].values

        # Target below all ash values → return 0 (cannot achieve low-ash clean coal)
        if target_ash_pct <= float(min(ash_vals)):
            return wt_vals[0]  # lowest density gives lowest ash but still above target

        # Target above all ash values → include all fractions
        if target_ash_pct >= float(max(ash_vals)):
            return wt_vals[-1]  # highest yield = total float mass

        # Target above all ash values → minimum yield needed
        if target_ash_pct <= min(ash_vals):
            # Return the lowest-density yield (largest mass)
            return wt_vals[0]

        # Linear interpolation between bracketing points
        for i in range(len(curve) - 1):
            ash_lo, ash_hi = ash_vals[i], ash_vals[i + 1]
            if ash_lo <= target_ash_pct <= ash_hi:
                # Interpolate within the interval
                frac = (target_ash_pct - ash_lo) / (ash_hi - ash_lo) if ash_hi != ash_lo else 0.5
                yield_interp = wt_vals[i] + frac * (wt_vals[i + 1] - wt_vals[i])
                return round(yield_interp, 3)

        # Fallback: scan for closest ash above target and interpolate backward
        for i in range(len(curve) - 1, 0, -1):
            if ash_vals[i] <= target_ash_pct:
                frac = (
                    (target_ash_pct - ash_vals[i - 1]) / (ash_vals[i] - ash_vals[i - 1])
                    if ash_vals[i] != ash_vals[i - 1]
                    else 0.0
                )
                return round(wt_vals[i - 1] + frac * (wt_vals[i] - wt_vals[i - 1]), 3)

        return 0.0

    # ------------------------------------------------------------------
    # Multi-source comparison
    # ------------------------------------------------------------------

    def compare_coal_sources(
        self,
        samples: List[CoalSample],
        target_ash_pct: float,
    ) -> "pd.DataFrame":
        """Compare washability of multiple coal sources at a given ash spec.

        Args:
            samples: List of CoalSample objects representing different sources/mines.
            target_ash_pct: Target clean coal ash % for the comparison.

        Returns:
            DataFrame with columns: ``sample_id``, ``mine``, ``source``, ``yield_pct``,
            ``sulfur_pct``, ``combustible_recovery_pct``, ranked by ``yield_pct`` descending.
        """
        if pd is None:
            raise ImportError("pandas is required for washability analysis")  # pragma: no cover

        rows = []
        for sample in samples:
            fractions = [
                {
                    "density": f[0],
                    "weight_pct": f[1],
                    "ash_pct": f[2],
                    "sulfur_pct": f[3],
                }
                for f in sample.fractions
            ]
            curve = self.build_float_sink_curve(fractions)
            wash_points = self.determine_wash_points(curve)
            yield_pct = self.calculate_wash_yield(curve, target_ash_pct)

            # Sulfur at the cut point
            sulfur_pct = self._sulfur_at_yield(curve, yield_pct)
            combustible_rec = self._combustible_at_yield(curve, yield_pct)

            rows.append(
                {
                    "sample_id": sample.sample_id,
                    "mine": sample.mine,
                    "source": sample.source,
                    "yield_pct": round(yield_pct, 2),
                    "sulfur_pct": round(sulfur_pct, 3) if sulfur_pct is not None else None,
                    "combustible_recovery_pct": round(combustible_rec, 2),
                }
            )

        df = pd.DataFrame(rows)
        return df.sort_values("yield_pct", ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Product quality matrix
    # ------------------------------------------------------------------

    def product_quality_matrix(
        self,
        fractions: List[Dict[str, float]],
        density_min: float = 1.30,
        density_max: float = 2.00,
        density_step: float = 0.05,
    ) -> "pd.DataFrame":
        """Compute product ash, yield, and sulfur across a range of wash densities.

        Args:
            fractions: Raw float-sink fraction data (same format as ``build_float_sink_curve``).
            density_min: Lower bound of wash density range (g/cm³). Default 1.30.
            density_max: Upper bound (g/cm³). Default 2.00.
            density_step: Increment between density cuts (g/cm³). Default 0.05.

        Returns:
            DataFrame with columns: ``density``, ``product_ash_pct``, ``yield_pct``,
            ``sulfur_pct``, ``combustible_recovery_pct``.
        """
        if pd is None:
            raise ImportError("pandas is required for washability analysis")  # pragma: no cover

        sorted_fracs = sorted(fractions, key=lambda f: f["density"])
        total_weight = sum(f["weight_pct"] for f in sorted_fracs)

        if total_weight == 0:
            raise ValueError("total fraction weight is zero")

        rows = []
        d = density_min
        while d <= density_max + 1e-9:
            float_wt = 0.0
            float_ash_mass = 0.0
            float_s_mass = 0.0
            float_ash_free = 0.0

            for frac in sorted_fracs:
                if frac["density"] <= d:
                    wt = frac["weight_pct"]
                    float_wt += wt
                    float_ash_mass += wt * frac["ash_pct"]
                    float_s_mass += wt * frac["sulfur_pct"]
                    float_ash_free += wt * (100 - frac["ash_pct"])

            product_ash = float_ash_mass / float_wt if float_wt > 0 else 0.0
            product_sul = float_s_mass / float_wt if float_wt > 0 else 0.0
            combustible_rec = (float_ash_free / sum(f["weight_pct"] * (100 - f["ash_pct"]) for f in sorted_fracs) * 100) if sum(f["weight_pct"] * (100 - f["ash_pct"]) for f in sorted_fracs) > 0 else 0.0

            rows.append(
                {
                    "density": round(d, 2),
                    "product_ash_pct": round(product_ash, 3),
                    "yield_pct": round(float_wt, 3),
                    "sulfur_pct": round(product_sul, 3),
                    "combustible_recovery_pct": round(combustible_rec, 3),
                }
            )
            d = round(d + density_step, 4)

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Critical sulfur cut
    # ------------------------------------------------------------------

    def critically_sulfur_cut(
        self,
        fractions: List[Dict[str, float]],
        min_yield_pct: float = 60.0,
    ) -> Dict:
        """Find the density cut that maximises sulfur removal while maintaining
        at least ``min_yield_pct`` clean coal yield.

        The function sweeps the product quality matrix and selects the
        density cut that maximises sulfur reduction relative to the raw feed
        while the corresponding yield stays above ``min_yield_pct``.

        Args:
            fractions: Raw float-sink fraction data.
            min_yield_pct: Minimum acceptable clean coal yield (%). Default 60.0.

        Returns:
            Dict with ``cut_density``, ``yield_pct``, ``product_ash_pct``,
            ``sulfur_pct``, ``sulfur_reduction_pct`` (relative to raw feed).
            Returns None for ``cut_density`` if no density satisfies the
            minimum yield constraint.
        """
        if pd is None:
            raise ImportError("pandas is required for washability analysis")  # pragma: no cover

        matrix = self.product_quality_matrix(fractions)

        # Raw feed sulfur
        raw_sulfur = sum(f["weight_pct"] * f["sulfur_pct"] for f in fractions) / sum(
            f["weight_pct"] for f in fractions
        )

        feasible = matrix[matrix["yield_pct"] >= min_yield_pct]
        if feasible.empty:
            return {
                "cut_density": None,
                "yield_pct": None,
                "product_ash_pct": None,
                "sulfur_pct": None,
                "sulfur_reduction_pct": None,
                "message": f"No density cut achieves ≥{min_yield_pct}% yield",
            }

        # Pick the one with the lowest sulfur (most sulfur removed)
        best_idx = feasible["sulfur_pct"].idxmin()
        best = feasible.loc[best_idx]

        sulfur_red = ((raw_sulfur - best["sulfur_pct"]) / raw_sulfur * 100) if raw_sulfur > 0 else 0.0

        return {
            "cut_density": best["density"],
            "yield_pct": round(best["yield_pct"], 2),
            "product_ash_pct": round(best["product_ash_pct"], 2),
            "sulfur_pct": round(best["sulfur_pct"], 3),
            "sulfur_reduction_pct": round(sulfur_red, 2),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sulfur_at_yield(
        self,
        curve: "pd.DataFrame",
        yield_pct: float,
    ) -> Optional[float]:
        """Return interpolated sulfur % at a given yield."""
        if len(curve) == 0:
            return None
        if len(curve) == 1:
            return curve.iloc[0]["float_sulfur_pct"]

        wt_vals = curve["float_weight_pct"].values
        sul_vals = curve["float_sulfur_pct"].values

        for i in range(len(curve) - 1):
            if wt_vals[i] <= yield_pct <= wt_vals[i + 1]:
                frac = (yield_pct - wt_vals[i]) / (wt_vals[i + 1] - wt_vals[i]) if wt_vals[i + 1] != wt_vals[i] else 0.0
                return sul_vals[i] + frac * (sul_vals[i + 1] - sul_vals[i])
        return None

    def _combustible_at_yield(
        self,
        curve: "pd.DataFrame",
        yield_pct: float,
    ) -> float:
        """Return interpolated combustible recovery at a given yield."""
        if len(curve) == 0:
            return 0.0
        if len(curve) == 1:
            return curve.iloc[0]["combustible_recovery_pct"]

        wt_vals = curve["float_weight_pct"].values
        comb_vals = curve["combustible_recovery_pct"].values

        for i in range(len(curve) - 1):
            if wt_vals[i] <= yield_pct <= wt_vals[i + 1]:
                frac = (yield_pct - wt_vals[i]) / (wt_vals[i + 1] - wt_vals[i]) if wt_vals[i + 1] != wt_vals[i] else 0.0
                return comb_vals[i] + frac * (comb_vals[i + 1] - comb_vals[i])
        return 0.0
