"""
Coal Contract Compliance Checker.

Validates blended coal quality against contractual specifications,
computes rejection risk, penalty triggers, and bonus/discount exposure
for typical coal supply agreements.

Contract structure supported:
- **Guaranteed specification (GS)**: Hard rejection boundary; if breached,
  the entire consignment is rejected.
- **Typical specification (TS)**: Target quality; deviation within ±AR
  (acceptable range) is permitted with no penalty.
- **Rejection specification (RS)**: Outer hard limit; exceeding RS triggers
  contractual rejection and liquidated damages.
- **Bonus/penalty (B/P) band**: Price adjustment per unit deviation from TS.

This is aligned with standard ADB (Air-Dried Basis) coal sales contracts
as used in Indonesian/Australian thermal coal export markets.

References:
- ISO 17246:2010 — Coal: proximate analysis
- Indonesian Coal Quality Reference (ASTM D5865, ASTM D3176)
- Typical PLN/Sumitomo/POSCO coal supply agreement structures

Author: github.com/achmadnaufal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ContractParameter:
    """Specification for a single quality parameter in a coal supply contract.

    Attributes:
        name: Parameter name (e.g. 'calorific_value_kcal_adb').
        unit: Unit string for display (e.g. 'kcal/kg', '%').
        typical: Typical specification value (target).
        rejection_min: Lower rejection limit (None if no lower limit).
        rejection_max: Upper rejection limit (None if no upper limit).
        penalty_per_unit: Price penalty per unit deviation below typical (USD/tonne).
        bonus_per_unit: Price bonus per unit above typical (USD/tonne).
        bonus_cap: Maximum bonus claimable (USD/tonne). Default None = no cap.
        direction: 'higher_better' or 'lower_better' — which direction earns bonus.
    """

    name: str
    unit: str
    typical: float
    rejection_min: Optional[float] = None
    rejection_max: Optional[float] = None
    penalty_per_unit: float = 0.0
    bonus_per_unit: float = 0.0
    bonus_cap: Optional[float] = None
    direction: str = "higher_better"  # 'higher_better' | 'lower_better'


@dataclass
class ParameterComplianceResult:
    """Compliance result for a single quality parameter.

    Attributes:
        parameter: Name of the quality parameter.
        actual_value: Measured or blend-calculated value.
        typical: Contract typical specification.
        rejection_min: Lower rejection limit.
        rejection_max: Upper rejection limit.
        status: 'accepted', 'rejected_low', 'rejected_high', or 'penalty', 'bonus'.
        deviation: Actual - typical (signed).
        price_adjustment_usd_per_tonne: Negative = penalty, positive = bonus.
        rejection_triggered: True if hard rejection limit breached.
        notes: Human-readable commentary.
    """

    parameter: str
    actual_value: float
    typical: float
    rejection_min: Optional[float]
    rejection_max: Optional[float]
    status: str
    deviation: float
    price_adjustment_usd_per_tonne: float
    rejection_triggered: bool
    notes: str = ""


@dataclass
class ConsignmentComplianceReport:
    """Full compliance report for a coal consignment.

    Attributes:
        consignment_id: Identifier for the shipment / lot.
        volume_mt: Consignment volume in metric tonnes.
        parameter_results: List of ParameterComplianceResult per parameter.
        is_accepted: True when no rejection limits are breached.
        total_price_adjustment_usd_per_tonne: Net price adjustment (penalties + bonuses).
        total_financial_impact_usd: Adjustment × volume.
        rejection_parameters: List of parameter names that triggered rejection.
        risk_tier: 'green', 'amber', or 'red' — overall consignment risk level.
    """

    consignment_id: str
    volume_mt: float
    parameter_results: List[ParameterComplianceResult]
    is_accepted: bool
    total_price_adjustment_usd_per_tonne: float
    total_financial_impact_usd: float
    rejection_parameters: List[str]
    risk_tier: str  # 'green' | 'amber' | 'red'


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ContractComplianceChecker:
    """Checks coal blend quality against contractual specifications.

    Args:
        base_price_usd_per_tonne: FOB reference price used for financial
            impact calculations. Defaults to 90.0 USD/tonne.
        contract_parameters: Optional list of ContractParameter objects.
            If not provided, a default Indonesian thermal coal export contract
            template is used (GAR 5500 kcal/kg standard).

    Example::

        checker = ContractComplianceChecker(base_price_usd_per_tonne=90.0)
        report = checker.check(
            consignment_id="BV-2026-001",
            volume_mt=50_000,
            quality={
                "calorific_value_kcal_adb": 5620,
                "total_moisture_pct": 12.5,
                "ash_pct": 7.8,
                "sulfur_pct": 0.72,
                "total_sulphur_pct": 0.72,
            },
        )
        print(report.is_accepted)
        print(f"Net adjustment: USD {report.total_price_adjustment_usd_per_tonne:.2f}/t")
        print(f"Financial impact: USD {report.total_financial_impact_usd:,.0f}")
    """

    def __init__(
        self,
        base_price_usd_per_tonne: float = 90.0,
        contract_parameters: Optional[List[ContractParameter]] = None,
    ) -> None:
        if base_price_usd_per_tonne <= 0:
            raise ValueError(f"base_price_usd_per_tonne must be > 0, got {base_price_usd_per_tonne}")
        self._base_price = base_price_usd_per_tonne
        self._params = contract_parameters or self._default_gar5500_contract()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        consignment_id: str,
        volume_mt: float,
        quality: Dict[str, float],
    ) -> ConsignmentComplianceReport:
        """Check a single consignment's quality against contract specifications.

        Args:
            consignment_id: Shipment/lot identifier.
            volume_mt: Consignment volume in metric tonnes. Must be > 0.
            quality: Dict mapping parameter names to measured/blend values.
                Missing parameters are silently skipped (not checked).

        Returns:
            ConsignmentComplianceReport with full per-parameter breakdown
            and financial impact.

        Raises:
            ValueError: If volume_mt ≤ 0.
        """
        if volume_mt <= 0:
            raise ValueError(f"volume_mt must be > 0, got {volume_mt}")

        results: List[ParameterComplianceResult] = []

        for param in self._params:
            actual = quality.get(param.name)
            if actual is None:
                continue  # parameter not measured — skip

            result = self._check_parameter(param, actual)
            results.append(result)

        rejections = [r.parameter for r in results if r.rejection_triggered]
        net_adj = sum(r.price_adjustment_usd_per_tonne for r in results)
        net_adj = round(net_adj, 4)
        total_impact = round(net_adj * volume_mt, 2)
        is_accepted = len(rejections) == 0

        risk = self._risk_tier(is_accepted, net_adj)

        return ConsignmentComplianceReport(
            consignment_id=consignment_id,
            volume_mt=volume_mt,
            parameter_results=results,
            is_accepted=is_accepted,
            total_price_adjustment_usd_per_tonne=net_adj,
            total_financial_impact_usd=total_impact,
            rejection_parameters=rejections,
            risk_tier=risk,
        )

    def check_batch(
        self, consignments: List[Dict]
    ) -> List[ConsignmentComplianceReport]:
        """Check multiple consignments and return a list of reports.

        Args:
            consignments: List of dicts, each with keys:
                'consignment_id', 'volume_mt', 'quality'.

        Returns:
            List of ConsignmentComplianceReport in input order.
        """
        return [
            self.check(
                consignment_id=c["consignment_id"],
                volume_mt=c["volume_mt"],
                quality=c["quality"],
            )
            for c in consignments
        ]

    def batch_summary(
        self, reports: List[ConsignmentComplianceReport]
    ) -> Dict:
        """Aggregate compliance stats across a batch.

        Args:
            reports: Output of ``check_batch()``.

        Returns:
            Dict with total/accepted/rejected counts, total volume, average
            price adjustment, total financial impact, and at-risk volume.
        """
        total = len(reports)
        accepted = [r for r in reports if r.is_accepted]
        rejected = [r for r in reports if not r.is_accepted]
        total_vol = sum(r.volume_mt for r in reports)
        at_risk_vol = sum(r.volume_mt for r in rejected)
        avg_adj = (
            sum(r.total_price_adjustment_usd_per_tonne for r in reports) / total
            if total > 0 else 0.0
        )
        total_impact = sum(r.total_financial_impact_usd for r in reports)

        return {
            "total_consignments": total,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "acceptance_rate_pct": round(len(accepted) / total * 100, 1) if total else 0.0,
            "total_volume_mt": total_vol,
            "at_risk_volume_mt": at_risk_vol,
            "average_price_adjustment_usd_per_tonne": round(avg_adj, 4),
            "total_financial_impact_usd": round(total_impact, 2),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_parameter(
        self, param: ContractParameter, actual: float
    ) -> ParameterComplianceResult:
        """Evaluate a single parameter's compliance and price adjustment."""
        rejection_triggered = False
        notes_parts = []

        # Rejection check
        if param.rejection_min is not None and actual < param.rejection_min:
            rejection_triggered = True
            notes_parts.append(
                f"REJECTED: {actual} {param.unit} < rejection min {param.rejection_min} {param.unit}"
            )
        elif param.rejection_max is not None and actual > param.rejection_max:
            rejection_triggered = True
            notes_parts.append(
                f"REJECTED: {actual} {param.unit} > rejection max {param.rejection_max} {param.unit}"
            )

        # Deviation from typical
        deviation = round(actual - param.typical, 4)

        # Price adjustment
        price_adj = 0.0
        if not rejection_triggered:
            if param.direction == "higher_better":
                if deviation > 0 and param.bonus_per_unit > 0:
                    bonus = deviation * param.bonus_per_unit
                    if param.bonus_cap is not None:
                        bonus = min(bonus, param.bonus_cap)
                    price_adj = bonus
                    notes_parts.append(f"Bonus +{price_adj:.3f} USD/t (above typical)")
                elif deviation < 0 and param.penalty_per_unit > 0:
                    price_adj = deviation * param.penalty_per_unit  # negative
                    notes_parts.append(f"Penalty {price_adj:.3f} USD/t (below typical)")
            else:  # lower_better (moisture, ash, sulfur)
                if deviation < 0 and param.bonus_per_unit > 0:
                    bonus = abs(deviation) * param.bonus_per_unit
                    if param.bonus_cap is not None:
                        bonus = min(bonus, param.bonus_cap)
                    price_adj = bonus
                    notes_parts.append(f"Bonus +{price_adj:.3f} USD/t (below typical)")
                elif deviation > 0 and param.penalty_per_unit > 0:
                    price_adj = -deviation * param.penalty_per_unit  # negative
                    notes_parts.append(f"Penalty {price_adj:.3f} USD/t (above typical)")

        # Status label
        if rejection_triggered:
            status = "rejected_low" if (param.rejection_min is not None and actual < param.rejection_min) else "rejected_high"
        elif price_adj > 0:
            status = "bonus"
        elif price_adj < 0:
            status = "penalty"
        else:
            status = "accepted"

        return ParameterComplianceResult(
            parameter=param.name,
            actual_value=actual,
            typical=param.typical,
            rejection_min=param.rejection_min,
            rejection_max=param.rejection_max,
            status=status,
            deviation=deviation,
            price_adjustment_usd_per_tonne=round(price_adj, 4),
            rejection_triggered=rejection_triggered,
            notes="; ".join(notes_parts) if notes_parts else "Within specification",
        )

    @staticmethod
    def _risk_tier(is_accepted: bool, net_adj: float) -> str:
        if not is_accepted:
            return "red"
        if net_adj < -1.0:
            return "amber"
        return "green"

    # ------------------------------------------------------------------
    # Default contract template
    # ------------------------------------------------------------------

    @staticmethod
    def _default_gar5500_contract() -> List[ContractParameter]:
        """Indonesian thermal coal export contract template — GAR 5500 kcal/kg basis.

        Typical for PLN / regional power plant supply agreements.
        Price adjustments assume USD 90/t FOB reference price.
        """
        return [
            ContractParameter(
                name="calorific_value_kcal_adb",
                unit="kcal/kg ADB",
                typical=5500.0,
                rejection_min=4900.0,
                rejection_max=None,
                penalty_per_unit=0.08,   # USD/tonne per kcal/kg below typical
                bonus_per_unit=0.06,
                bonus_cap=3.0,
                direction="higher_better",
            ),
            ContractParameter(
                name="total_moisture_pct",
                unit="%",
                typical=20.0,
                rejection_min=None,
                rejection_max=28.0,
                penalty_per_unit=0.30,
                bonus_per_unit=0.20,
                bonus_cap=2.0,
                direction="lower_better",
            ),
            ContractParameter(
                name="ash_pct",
                unit="%",
                typical=8.0,
                rejection_min=None,
                rejection_max=15.0,
                penalty_per_unit=0.40,
                bonus_per_unit=0.25,
                bonus_cap=2.0,
                direction="lower_better",
            ),
            ContractParameter(
                name="total_sulphur_pct",
                unit="%",
                typical=0.5,
                rejection_min=None,
                rejection_max=1.0,
                penalty_per_unit=5.0,
                bonus_per_unit=3.0,
                bonus_cap=1.5,
                direction="lower_better",
            ),
            ContractParameter(
                name="volatile_matter_pct",
                unit="%",
                typical=36.0,
                rejection_min=30.0,
                rejection_max=42.0,
                penalty_per_unit=0.20,
                bonus_per_unit=0.0,
                direction="higher_better",
            ),
        ]
