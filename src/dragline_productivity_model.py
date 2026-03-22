"""
Dragline Productivity Model
============================
Models dragline cycle time, productivity, and bench utilisation for open-cut
coal mining operations.

Methodology references:
- Golosinski & Boehm (1987) "Matching of surface mining equipment"
- AS 1742 (Australian Standards for mining equipment productivity)
- Caterpillar / P&H performance handbooks (generalised)

Key metrics computed:
  - Net productivity (BCM/hr) — Bank Cubic Metres per hour
  - Shift utilisation (%)
  - Cumulative strip ratio handling capacity
  - Swing angle penalty factor
  - Walking time per pass (productive vs repositioning)
  - Monthly overburden removal forecast (BCM)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DraglineSpec:
    """
    Physical specification of a dragline.

    Parameters
    ----------
    model_id : str  — machine identifier
    bucket_capacity_m3 : float  — nominal bucket volume (m³)
    boom_length_m : float  — boom arm length (m)
    dump_radius_m : float  — maximum dump radius (m)
    max_dig_depth_m : float  — maximum digging depth (m)
    walking_speed_m_min : float  — machine walking speed (m/min)
    slew_speed_deg_s : float  — slewing speed (degrees/second)
    hoist_speed_m_s : float  — hoist chain speed (m/s)
    drag_speed_m_s : float  — drag chain speed (m/s)
    swing_full_deg : float  — default full swing angle (degrees), typically 90–180
    """
    model_id: str
    bucket_capacity_m3: float
    boom_length_m: float
    dump_radius_m: float
    max_dig_depth_m: float
    walking_speed_m_min: float
    slew_speed_deg_s: float
    hoist_speed_m_s: float
    drag_speed_m_s: float
    swing_full_deg: float = 120.0

    def __post_init__(self):
        for attr in ("bucket_capacity_m3", "boom_length_m", "dump_radius_m",
                     "max_dig_depth_m", "walking_speed_m_min",
                     "slew_speed_deg_s", "hoist_speed_m_s", "drag_speed_m_s"):
            if getattr(self, attr) <= 0:
                raise ValueError(f"{attr} must be positive")
        if not 45 <= self.swing_full_deg <= 360:
            raise ValueError("swing_full_deg must be in [45, 360]")


@dataclass
class BenchConditions:
    """
    Describes the bench geometry and material conditions.

    Parameters
    ----------
    bench_height_m : float  — height of overburden bench (m)
    material_swell_factor : float  — loose/bank volume ratio (> 1)
    fill_factor : float  — actual bucket fill relative to rated capacity (0–1.2)
    actual_swing_deg : float  — mean swing angle for this bench (degrees)
    walk_distance_per_cut_m : float  — dragline advance per cut (m)
    operator_efficiency : float  — operator performance factor (0–1)
    """
    bench_height_m: float
    material_swell_factor: float = 1.25
    fill_factor: float = 0.90
    actual_swing_deg: float = 90.0
    walk_distance_per_cut_m: float = 15.0
    operator_efficiency: float = 0.85

    def __post_init__(self):
        if self.bench_height_m <= 0:
            raise ValueError("bench_height_m must be positive")
        if not 1.0 <= self.material_swell_factor <= 2.0:
            raise ValueError("material_swell_factor must be in [1.0, 2.0]")
        if not 0.5 <= self.fill_factor <= 1.2:
            raise ValueError("fill_factor must be in [0.5, 1.2]")
        if not 0 < self.actual_swing_deg <= 360:
            raise ValueError("actual_swing_deg must be in (0, 360]")
        if not 0 < self.operator_efficiency <= 1.0:
            raise ValueError("operator_efficiency must be in (0, 1]")


@dataclass
class ShiftSchedule:
    """
    Shift schedule and availability parameters.

    Parameters
    ----------
    shift_hours : float  — total shift duration (hrs)
    planned_maintenance_hrs : float  — scheduled maintenance per shift
    unplanned_delays_hrs : float  — average unplanned delays per shift
    meal_break_hrs : float  — statutory meal/rest breaks
    """
    shift_hours: float = 12.0
    planned_maintenance_hrs: float = 1.0
    unplanned_delays_hrs: float = 0.5
    meal_break_hrs: float = 0.5

    def __post_init__(self):
        if self.shift_hours <= 0:
            raise ValueError("shift_hours must be positive")
        total_non_productive = (
            self.planned_maintenance_hrs
            + self.unplanned_delays_hrs
            + self.meal_break_hrs
        )
        if total_non_productive >= self.shift_hours:
            raise ValueError("Total delays/maintenance cannot exceed shift_hours")

    @property
    def productive_hours(self) -> float:
        return (
            self.shift_hours
            - self.planned_maintenance_hrs
            - self.unplanned_delays_hrs
            - self.meal_break_hrs
        )

    @property
    def mechanical_availability(self) -> float:
        """MA = (shift_hrs - planned_maint) / shift_hrs."""
        return (self.shift_hours - self.planned_maintenance_hrs) / self.shift_hours


@dataclass
class DraglineProductivityResult:
    """Container for all productivity model outputs."""
    model_id: str
    cycle_time_s: float
    swing_penalty_factor: float
    bank_productivity_BCM_hr: float
    loose_productivity_LCM_hr: float
    shift_utilisation_pct: float
    bcm_per_shift: float
    monthly_bcm: float  # assumes 2 shifts/day, 25 effective days/month
    pass_count_per_shift: int
    walk_time_per_shift_min: float
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class DraglineProductivityModel:
    """
    Compute dragline productivity for open-cut coal operations.

    Examples
    --------
    >>> from coal_blending_optimizer.src.dragline_productivity_model import (
    ...     DraglineProductivityModel, DraglineSpec, BenchConditions, ShiftSchedule
    ... )
    >>> spec = DraglineSpec(
    ...     model_id="BE1570W",
    ...     bucket_capacity_m3=55.0,
    ...     boom_length_m=96.0,
    ...     dump_radius_m=92.0,
    ...     max_dig_depth_m=52.0,
    ...     walking_speed_m_min=0.26,
    ...     slew_speed_deg_s=0.09,
    ...     hoist_speed_m_s=1.2,
    ...     drag_speed_m_s=1.5,
    ...     swing_full_deg=90.0,
    ... )
    >>> bench = BenchConditions(bench_height_m=20.0, actual_swing_deg=90.0)
    >>> schedule = ShiftSchedule()
    >>> model = DraglineProductivityModel()
    >>> result = model.compute(spec, bench, schedule)
    >>> result.bank_productivity_BCM_hr > 0
    True
    """

    # SWING PENALTY: normalise cycle time by actual vs nominal swing angle
    # Based on empirical swing factor tables from Golosinski & Boehm (1987)
    _SWING_PENALTY_TABLE = [
        (45, 0.80), (60, 0.85), (75, 0.90), (90, 1.00),
        (105, 1.06), (120, 1.12), (135, 1.18), (150, 1.25),
        (165, 1.32), (180, 1.40),
    ]

    def swing_penalty(self, actual_deg: float, nominal_deg: float = 90.0) -> float:
        """
        Return swing penalty multiplier relative to 90° baseline.
        Interpolates from Golosinski & Boehm table.
        """
        # Build interpolation from table
        angles = [row[0] for row in self._SWING_PENALTY_TABLE]
        factors = [row[1] for row in self._SWING_PENALTY_TABLE]

        # Find at-90° factor for denominator
        def interpolate(deg: float) -> float:
            deg = max(angles[0], min(angles[-1], deg))
            for i in range(len(angles) - 1):
                if angles[i] <= deg <= angles[i + 1]:
                    t = (deg - angles[i]) / (angles[i + 1] - angles[i])
                    return factors[i] + t * (factors[i + 1] - factors[i])
            return factors[-1]

        actual_f = interpolate(actual_deg)
        nominal_f = interpolate(nominal_deg)
        return actual_f / nominal_f

    def _cycle_time(self, spec: DraglineSpec, bench: BenchConditions) -> float:
        """
        Compute mean cycle time (seconds) using component method:
          - Drag time: bench_height / drag_speed (simplified)
          - Hoist time: swing radius / hoist_speed
          - Swing time: (swing_angle / slew_speed) × penalty
          - Dump time: fixed 3 s
          - Return time: ~0.7 × swing time
        """
        # Drag distance ≈ half bench height / sin(30°) — simplified
        drag_dist_m = bench.bench_height_m / math.sin(math.radians(30))
        drag_time = drag_dist_m / spec.drag_speed_m_s

        # Hoist: assume height is bench_height + 3 m clearance
        hoist_dist_m = bench.bench_height_m + 3.0
        hoist_time = hoist_dist_m / spec.hoist_speed_m_s

        # Swing time (loaded + return)
        swing_penalty = self.swing_penalty(bench.actual_swing_deg)
        loaded_swing_time = (bench.actual_swing_deg / spec.slew_speed_deg_s) * swing_penalty
        return_swing_time = loaded_swing_time * 0.70  # empty bucket faster

        dump_time = 3.0  # seconds

        total = drag_time + hoist_time + loaded_swing_time + dump_time + return_swing_time
        return round(total, 2)

    def compute(
        self,
        spec: DraglineSpec,
        bench: BenchConditions,
        schedule: ShiftSchedule,
        shifts_per_day: int = 2,
        effective_days_per_month: int = 25,
    ) -> DraglineProductivityResult:
        """
        Compute dragline productivity.

        Parameters
        ----------
        spec : DraglineSpec
        bench : BenchConditions
        schedule : ShiftSchedule
        shifts_per_day : int  — typically 2 for 12-hr shifts
        effective_days_per_month : int  — calendar days minus rain/public holidays

        Returns
        -------
        DraglineProductivityResult
        """
        notes = []
        cycle_time_s = self._cycle_time(spec, bench)
        swing_factor = self.swing_penalty(bench.actual_swing_deg)

        # Payload per cycle (BCM)
        actual_bucket_volume_m3 = spec.bucket_capacity_m3 * bench.fill_factor
        bcm_per_cycle = actual_bucket_volume_m3 / bench.material_swell_factor

        # Gross productivity (cycles/hr × BCM/cycle)
        cycles_per_hr = 3600 / cycle_time_s
        gross_bcm_hr = cycles_per_hr * bcm_per_cycle

        # Apply operator efficiency
        net_bcm_hr = gross_bcm_hr * bench.operator_efficiency

        # Loose cubic metres
        net_lcm_hr = net_bcm_hr * bench.material_swell_factor

        # Shift utilisation
        utilisation = schedule.productive_hours / schedule.shift_hours
        bcm_per_shift = net_bcm_hr * schedule.productive_hours

        # Walk time estimate per shift
        passes_per_shift = int(
            bcm_per_shift / (bcm_per_cycle * bench.walk_distance_per_cut_m)
        ) if bench.walk_distance_per_cut_m > 0 else 0
        walk_time_min = passes_per_shift * (
            bench.walk_distance_per_cut_m / spec.walking_speed_m_min
        )

        # Monthly
        monthly_bcm = bcm_per_shift * shifts_per_day * effective_days_per_month

        # Sanity checks
        if bench.actual_swing_deg > 150:
            notes.append("WARNING: swing angle >150° significantly reduces productivity; "
                         "consider bench repositioning.")
        if bench.operator_efficiency < 0.75:
            notes.append("INFO: operator efficiency below 75%; training may improve output.")
        if bench.fill_factor < 0.80:
            notes.append("INFO: fill factor below 80%; review material flow and bucket selection.")

        return DraglineProductivityResult(
            model_id=spec.model_id,
            cycle_time_s=cycle_time_s,
            swing_penalty_factor=round(swing_factor, 4),
            bank_productivity_BCM_hr=round(net_bcm_hr, 1),
            loose_productivity_LCM_hr=round(net_lcm_hr, 1),
            shift_utilisation_pct=round(utilisation * 100, 1),
            bcm_per_shift=round(bcm_per_shift, 0),
            monthly_bcm=round(monthly_bcm, 0),
            pass_count_per_shift=passes_per_shift,
            walk_time_per_shift_min=round(walk_time_min, 1),
            notes=notes,
        )

    def sensitivity_analysis(
        self,
        spec: DraglineSpec,
        bench: BenchConditions,
        schedule: ShiftSchedule,
        swing_angles: Optional[List[float]] = None,
    ) -> List[Dict]:
        """
        Run productivity at multiple swing angles to support bench planning.

        Returns
        -------
        list of dicts with swing_deg and BCM/hr.
        """
        if swing_angles is None:
            swing_angles = [60, 75, 90, 105, 120, 135, 150]

        results = []
        for angle in swing_angles:
            modified_bench = BenchConditions(
                bench_height_m=bench.bench_height_m,
                material_swell_factor=bench.material_swell_factor,
                fill_factor=bench.fill_factor,
                actual_swing_deg=angle,
                walk_distance_per_cut_m=bench.walk_distance_per_cut_m,
                operator_efficiency=bench.operator_efficiency,
            )
            r = self.compute(spec, modified_bench, schedule)
            results.append({
                "swing_deg": angle,
                "BCM_per_hr": r.bank_productivity_BCM_hr,
                "monthly_BCM": r.monthly_bcm,
                "cycle_time_s": r.cycle_time_s,
            })
        return results
