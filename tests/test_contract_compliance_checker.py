"""Unit tests for ContractComplianceChecker."""

import pytest
from src.contract_compliance_checker import (
    ContractComplianceChecker,
    ContractParameter,
    ConsignmentComplianceReport,
    ParameterComplianceResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compliant_quality():
    """Quality values that fully comply with default GAR5500 contract."""
    return {
        "calorific_value_kcal_adb": 5500.0,  # exactly at typical
        "total_moisture_pct": 20.0,
        "ash_pct": 8.0,
        "total_sulphur_pct": 0.5,
        "volatile_matter_pct": 36.0,
    }


@pytest.fixture
def checker():
    return ContractComplianceChecker(base_price_usd_per_tonne=90.0)


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestInstantiation:
    def test_default_init(self):
        c = ContractComplianceChecker()
        assert c is not None

    def test_custom_base_price(self):
        c = ContractComplianceChecker(base_price_usd_per_tonne=110.0)
        assert c is not None

    def test_negative_base_price_raises(self):
        with pytest.raises(ValueError, match="base_price_usd_per_tonne"):
            ContractComplianceChecker(base_price_usd_per_tonne=-5.0)

    def test_zero_base_price_raises(self):
        with pytest.raises(ValueError):
            ContractComplianceChecker(base_price_usd_per_tonne=0)

    def test_custom_contract_parameters(self):
        params = [
            ContractParameter(
                name="cv", unit="kcal/kg", typical=6000, rejection_min=5500,
                direction="higher_better",
            )
        ]
        c = ContractComplianceChecker(contract_parameters=params)
        assert c is not None


# ---------------------------------------------------------------------------
# check() — acceptance
# ---------------------------------------------------------------------------

class TestCheckAcceptance:
    def test_compliant_quality_accepted(self, checker):
        report = checker.check("SHIP-001", 50_000, _compliant_quality())
        assert report.is_accepted is True

    def test_volume_preserved(self, checker):
        report = checker.check("SHIP-001", 75_000, _compliant_quality())
        assert report.volume_mt == 75_000

    def test_consignment_id_preserved(self, checker):
        report = checker.check("SHIP-XYZ", 50_000, _compliant_quality())
        assert report.consignment_id == "SHIP-XYZ"

    def test_zero_volume_raises(self, checker):
        with pytest.raises(ValueError, match="volume_mt"):
            checker.check("X", 0, _compliant_quality())

    def test_negative_volume_raises(self, checker):
        with pytest.raises(ValueError):
            checker.check("X", -1000, _compliant_quality())

    def test_returns_report_type(self, checker):
        report = checker.check("X", 1000, _compliant_quality())
        assert isinstance(report, ConsignmentComplianceReport)

    def test_missing_parameter_skipped(self, checker):
        quality = {"calorific_value_kcal_adb": 5500.0}
        report = checker.check("X", 1000, quality)
        assert report.is_accepted is True  # no rejection triggered


# ---------------------------------------------------------------------------
# check() — rejection
# ---------------------------------------------------------------------------

class TestCheckRejection:
    def test_cv_below_rejection_min_rejected(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 4800.0  # < 4900 rejection min
        report = checker.check("X", 50_000, quality)
        assert report.is_accepted is False
        assert "calorific_value_kcal_adb" in report.rejection_parameters

    def test_moisture_above_rejection_max_rejected(self, checker):
        quality = _compliant_quality()
        quality["total_moisture_pct"] = 29.0  # > 28 rejection max
        report = checker.check("X", 50_000, quality)
        assert report.is_accepted is False

    def test_sulphur_above_rejection_max_rejected(self, checker):
        quality = _compliant_quality()
        quality["total_sulphur_pct"] = 1.1
        report = checker.check("X", 50_000, quality)
        assert report.is_accepted is False
        assert report.risk_tier == "red"

    def test_at_rejection_boundary_not_rejected(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 4900.0  # exactly at min
        report = checker.check("X", 50_000, quality)
        assert report.is_accepted is True

    def test_multiple_rejections_all_listed(self, checker):
        quality = {
            "calorific_value_kcal_adb": 4800.0,
            "total_moisture_pct": 30.0,
        }
        report = checker.check("X", 50_000, quality)
        assert len(report.rejection_parameters) == 2


# ---------------------------------------------------------------------------
# check() — price adjustments
# ---------------------------------------------------------------------------

class TestPriceAdjustments:
    def test_cv_above_typical_earns_bonus(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 5600.0  # 100 kcal above typical
        report = checker.check("X", 50_000, quality)
        cv_result = next(r for r in report.parameter_results if r.parameter == "calorific_value_kcal_adb")
        assert cv_result.price_adjustment_usd_per_tonne > 0
        assert cv_result.status == "bonus"

    def test_cv_below_typical_triggers_penalty(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 5300.0  # 200 below typical
        report = checker.check("X", 50_000, quality)
        cv_result = next(r for r in report.parameter_results if r.parameter == "calorific_value_kcal_adb")
        assert cv_result.price_adjustment_usd_per_tonne < 0
        assert cv_result.status == "penalty"

    def test_moisture_below_typical_earns_bonus(self, checker):
        quality = _compliant_quality()
        quality["total_moisture_pct"] = 18.0  # 2% below typical
        report = checker.check("X", 50_000, quality)
        moisture_result = next(r for r in report.parameter_results if r.parameter == "total_moisture_pct")
        assert moisture_result.price_adjustment_usd_per_tonne > 0

    def test_ash_above_typical_triggers_penalty(self, checker):
        quality = _compliant_quality()
        quality["ash_pct"] = 10.0  # 2% above typical
        report = checker.check("X", 50_000, quality)
        ash_result = next(r for r in report.parameter_results if r.parameter == "ash_pct")
        assert ash_result.price_adjustment_usd_per_tonne < 0

    def test_bonus_capped_at_max(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 6500.0  # huge bonus but capped
        report = checker.check("X", 50_000, quality)
        cv_result = next(r for r in report.parameter_results if r.parameter == "calorific_value_kcal_adb")
        assert cv_result.price_adjustment_usd_per_tonne <= 3.0  # bonus cap

    def test_total_financial_impact_equals_adj_times_volume(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 5300.0
        report = checker.check("X", 50_000, quality)
        expected = report.total_price_adjustment_usd_per_tonne * 50_000
        assert abs(report.total_financial_impact_usd - expected) < 0.01

    def test_no_deviation_zero_adjustment(self, checker):
        report = checker.check("X", 50_000, _compliant_quality())
        assert report.total_price_adjustment_usd_per_tonne == 0.0

    def test_rejection_no_price_adjustment(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 4800.0  # rejected
        report = checker.check("X", 50_000, quality)
        cv_result = next(r for r in report.parameter_results if r.parameter == "calorific_value_kcal_adb")
        assert cv_result.price_adjustment_usd_per_tonne == 0.0


# ---------------------------------------------------------------------------
# Risk tiers
# ---------------------------------------------------------------------------

class TestRiskTier:
    def test_compliant_is_green(self, checker):
        report = checker.check("X", 50_000, _compliant_quality())
        assert report.risk_tier == "green"

    def test_rejected_is_red(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 4500.0
        report = checker.check("X", 50_000, quality)
        assert report.risk_tier == "red"

    def test_heavy_penalty_is_amber(self, checker):
        quality = _compliant_quality()
        quality["calorific_value_kcal_adb"] = 5100.0  # -400 kcal → -32 USD/t
        report = checker.check("X", 50_000, quality)
        assert report.risk_tier == "amber"


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

class TestBatchOperations:
    def test_check_batch_returns_list(self, checker):
        consignments = [
            {"consignment_id": "C1", "volume_mt": 50_000, "quality": _compliant_quality()},
            {"consignment_id": "C2", "volume_mt": 30_000, "quality": _compliant_quality()},
        ]
        reports = checker.check_batch(consignments)
        assert len(reports) == 2

    def test_batch_summary_counts(self, checker):
        bad_quality = _compliant_quality()
        bad_quality["calorific_value_kcal_adb"] = 4500.0
        consignments = [
            {"consignment_id": "C1", "volume_mt": 50_000, "quality": _compliant_quality()},
            {"consignment_id": "C2", "volume_mt": 30_000, "quality": bad_quality},
        ]
        reports = checker.check_batch(consignments)
        summary = checker.batch_summary(reports)
        assert summary["total_consignments"] == 2
        assert summary["accepted_count"] == 1
        assert summary["rejected_count"] == 1

    def test_batch_summary_volume(self, checker):
        consignments = [
            {"consignment_id": "C1", "volume_mt": 50_000, "quality": _compliant_quality()},
            {"consignment_id": "C2", "volume_mt": 30_000, "quality": _compliant_quality()},
        ]
        reports = checker.check_batch(consignments)
        summary = checker.batch_summary(reports)
        assert summary["total_volume_mt"] == 80_000

    def test_acceptance_rate_100pct(self, checker):
        consignments = [
            {"consignment_id": f"C{i}", "volume_mt": 10_000, "quality": _compliant_quality()}
            for i in range(5)
        ]
        reports = checker.check_batch(consignments)
        summary = checker.batch_summary(reports)
        assert summary["acceptance_rate_pct"] == 100.0
