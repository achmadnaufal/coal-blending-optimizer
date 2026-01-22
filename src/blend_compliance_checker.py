"""
Blend Compliance Checker for coal quality specifications.

Validates a proposed coal blend against customer/contract quality specifications
and generates a detailed compliance report. Used to verify optimizer outputs
before shipment nomination and to perform pre-delivery quality checks.

Supported quality parameters (ASTM D / ISO 17246 basis):
  - Calorific Value (GCV/NCV): kcal/kg
  - Total Moisture: %
  - Inherent Moisture: %
  - Ash Content: %
  - Volatile Matter: %
  - Fixed Carbon: %
  - Total Sulfur: %
  - HGI (Hardgrove Grindability Index)

Reference:
    ASTM D5865 — Standard Test Method for Gross Calorific Value of Coal
    ISO 17246:2010 — Coal — Proximate analysis
    Typical Indonesian coal export contract quality bands (Kalimantan thermal coal)

Author: github.com/achmadnaufal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ComplianceStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"  # within tolerance band but close to limit


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParameterCheck:
    """Compliance check result for a single quality parameter.

    Attributes:
        parameter: Parameter name (e.g. 'calorific_value_kcal').
        actual_value: Measured or calculated blend value.
        spec_min: Minimum specification limit (None if no lower bound).
        spec_max: Maximum specification limit (None if no upper bound).
        spec_target: Optional target value.
        status: PASS, WARN, or FAIL.
        deviation: Absolute deviation from the nearest violated limit.
        message: Human-readable result message.
    """

    parameter: str
    actual_value: float
    spec_min: Optional[float]
    spec_max: Optional[float]
    spec_target: Optional[float]
    status: ComplianceStatus
    deviation: float
    message: str


@dataclass
class BlendComplianceReport:
    """Full compliance report for a coal blend.

    Attributes:
        blend_id: Identifier for the blend / shipment lot.
        overall_status: PASS only if all parameters pass; FAIL if any fail.
        checks: Dict of parameter name → ParameterCheck.
        failed_parameters: List of parameter names that failed.
        warned_parameters: List of parameters within tolerance but near limit.
        compliance_pct: Percentage of parameters that passed.
        recommendations: List of corrective action suggestions.
    """

    blend_id: str
    overall_status: ComplianceStatus
    checks: Dict[str, ParameterCheck]
    failed_parameters: List[str]
    warned_parameters: List[str]
    compliance_pct: float
    recommendations: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class BlendComplianceChecker:
    """Check a coal blend quality profile against contract specifications.

    Args:
        specs: Dict mapping parameter name → dict with optional keys:
            ``min``, ``max``, ``target``, ``warn_band`` (fraction of range
            that triggers WARN instead of immediate PASS, default 0.10).

    Example:
        >>> checker = BlendComplianceChecker(specs={
        ...     "calorific_value_kcal": {"min": 5800, "max": 6200, "target": 6000},
        ...     "total_moisture_pct":   {"max": 14.0},
        ...     "ash_pct":              {"max": 8.0},
        ...     "sulfur_pct":           {"max": 0.8},
        ... })
        >>> blend = {
        ...     "calorific_value_kcal": 5950,
        ...     "total_moisture_pct": 13.2,
        ...     "ash_pct": 7.5,
        ...     "sulfur_pct": 0.72,
        ... }
        >>> report = checker.check(blend_id="LOT-2025-001", blend_quality=blend)
        >>> print(f"Status: {report.overall_status}")
        Status: pass
        >>> print(f"Compliance: {report.compliance_pct:.0f}%")
        Compliance: 100%
    """

    DEFAULT_WARN_BAND = 0.10  # 10% of range triggers WARN

    def __init__(self, specs: Dict[str, Dict]):
        if not specs:
            raise ValueError("specs cannot be empty")
        self._validate_specs(specs)
        self.specs = specs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, blend_id: str, blend_quality: Dict[str, float]) -> BlendComplianceReport:
        """Run compliance check on a blend quality profile.

        Args:
            blend_id: Identifier for this blend lot.
            blend_quality: Dict mapping parameter name → measured value.
                Parameters not in specs are ignored.

        Returns:
            :class:`BlendComplianceReport` with full parameter breakdown.

        Raises:
            ValueError: If blend_quality is empty.
        """
        if not blend_quality:
            raise ValueError("blend_quality cannot be empty")

        checks: Dict[str, ParameterCheck] = {}
        for param, spec in self.specs.items():
            actual = blend_quality.get(param)
            if actual is None:
                # Parameter specified but not measured — treat as missing
                checks[param] = ParameterCheck(
                    parameter=param,
                    actual_value=float("nan"),
                    spec_min=spec.get("min"),
                    spec_max=spec.get("max"),
                    spec_target=spec.get("target"),
                    status=ComplianceStatus.WARN,
                    deviation=0.0,
                    message=f"'{param}' not provided in blend quality — cannot verify",
                )
                continue
            check = self._check_parameter(param, float(actual), spec)
            checks[param] = check

        failed = [p for p, c in checks.items() if c.status == ComplianceStatus.FAIL]
        warned = [p for p, c in checks.items() if c.status == ComplianceStatus.WARN]
        n_pass = sum(1 for c in checks.values() if c.status == ComplianceStatus.PASS)
        compliance_pct = n_pass / len(checks) * 100 if checks else 0.0
        overall = ComplianceStatus.FAIL if failed else (
            ComplianceStatus.WARN if warned else ComplianceStatus.PASS
        )
        recs = self._build_recommendations(checks)

        return BlendComplianceReport(
            blend_id=blend_id,
            overall_status=overall,
            checks=checks,
            failed_parameters=failed,
            warned_parameters=warned,
            compliance_pct=round(compliance_pct, 2),
            recommendations=recs,
        )

    def check_batch(
        self, blends: Dict[str, Dict[str, float]]
    ) -> Dict[str, BlendComplianceReport]:
        """Check multiple blends in one call.

        Args:
            blends: Dict mapping blend_id → blend quality dict.

        Returns:
            Dict mapping blend_id → :class:`BlendComplianceReport`.
        """
        return {blend_id: self.check(blend_id, quality) for blend_id, quality in blends.items()}

    def summary_table(self, reports: Dict[str, BlendComplianceReport]) -> List[Dict]:
        """Return a summary table of all blend compliance results.

        Args:
            reports: Output from :meth:`check_batch`.

        Returns:
            List of dicts with keys: blend_id, overall_status, compliance_pct,
            n_failed, n_warned.
        """
        return [
            {
                "blend_id": bid,
                "overall_status": r.overall_status.value,
                "compliance_pct": r.compliance_pct,
                "n_failed": len(r.failed_parameters),
                "n_warned": len(r.warned_parameters),
            }
            for bid, r in reports.items()
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_parameter(
        self, param: str, actual: float, spec: Dict
    ) -> ParameterCheck:
        lo = spec.get("min")
        hi = spec.get("max")
        target = spec.get("target")
        warn_band = spec.get("warn_band", self.DEFAULT_WARN_BAND)

        # Compute range width for warn band
        range_width = None
        if lo is not None and hi is not None:
            range_width = hi - lo

        status = ComplianceStatus.PASS
        deviation = 0.0
        message_parts: List[str] = []

        # Lower bound check
        if lo is not None and actual < lo:
            status = ComplianceStatus.FAIL
            deviation = lo - actual
            message_parts.append(f"below min {lo} by {deviation:.3f}")
        # Upper bound check
        elif hi is not None and actual > hi:
            status = ComplianceStatus.FAIL
            deviation = actual - hi
            message_parts.append(f"above max {hi} by {deviation:.3f}")
        else:
            # Check warn band proximity
            if range_width and range_width > 0:
                band = range_width * warn_band
                if lo is not None and actual < lo + band:
                    status = ComplianceStatus.WARN
                    deviation = actual - lo
                    message_parts.append(f"close to min {lo} (within {warn_band:.0%} band)")
                elif hi is not None and actual > hi - band:
                    status = ComplianceStatus.WARN
                    deviation = hi - actual
                    message_parts.append(f"close to max {hi} (within {warn_band:.0%} band)")

        if not message_parts:
            msg = f"{param}: {actual} ✓ within specification"
        else:
            msg = f"{param}: {actual} — " + "; ".join(message_parts)

        return ParameterCheck(
            parameter=param,
            actual_value=actual,
            spec_min=lo,
            spec_max=hi,
            spec_target=target,
            status=status,
            deviation=round(deviation, 4),
            message=msg,
        )

    @staticmethod
    def _build_recommendations(checks: Dict[str, ParameterCheck]) -> List[str]:
        recs: List[str] = []
        for param, check in checks.items():
            if check.status == ComplianceStatus.FAIL:
                if check.spec_min is not None and check.actual_value < check.spec_min:
                    recs.append(
                        f"Increase {param}: blend more high-{param} component "
                        f"(shortfall {check.deviation:.2f} units)"
                    )
                elif check.spec_max is not None:
                    recs.append(
                        f"Reduce {param}: dilute with lower-{param} component "
                        f"(excess {check.deviation:.2f} units)"
                    )
        return recs

    @staticmethod
    def _validate_specs(specs: Dict[str, Dict]) -> None:
        for param, spec in specs.items():
            lo = spec.get("min")
            hi = spec.get("max")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(
                    f"Spec for '{param}': min ({lo}) cannot exceed max ({hi})"
                )
