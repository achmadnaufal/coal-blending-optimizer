"""
Unit tests for BlendComplianceChecker.
"""

import pytest
from src.blend_compliance_checker import (
    BlendComplianceChecker,
    BlendComplianceReport,
    ComplianceStatus,
)


STANDARD_SPECS = {
    "calorific_value_kcal": {"min": 5800, "max": 6300, "target": 6000},
    "total_moisture_pct":   {"max": 14.0},
    "ash_pct":              {"max": 8.0},
    "sulfur_pct":           {"max": 0.8},
}

PASSING_BLEND = {
    "calorific_value_kcal": 6050,
    "total_moisture_pct": 12.5,
    "ash_pct": 6.8,
    "sulfur_pct": 0.55,
}

FAILING_BLEND = {
    "calorific_value_kcal": 5600,  # below min 5800
    "total_moisture_pct": 15.5,   # above max 14.0
    "ash_pct": 6.8,
    "sulfur_pct": 0.55,
}


@pytest.fixture
def checker():
    return BlendComplianceChecker(specs=STANDARD_SPECS)


class TestInit:
    def test_empty_specs_raises(self):
        with pytest.raises(ValueError, match="specs cannot be empty"):
            BlendComplianceChecker(specs={})

    def test_invalid_min_gt_max_raises(self):
        with pytest.raises(ValueError, match="min.*cannot exceed max"):
            BlendComplianceChecker(specs={"gcv": {"min": 7000, "max": 5000}})


class TestCheck:
    def test_returns_report(self, checker):
        report = checker.check("LOT-001", PASSING_BLEND)
        assert isinstance(report, BlendComplianceReport)

    def test_passing_blend_status(self, checker):
        report = checker.check("LOT-001", PASSING_BLEND)
        assert report.overall_status == ComplianceStatus.PASS

    def test_failing_blend_status(self, checker):
        report = checker.check("LOT-002", FAILING_BLEND)
        assert report.overall_status == ComplianceStatus.FAIL

    def test_failed_parameters_listed(self, checker):
        report = checker.check("LOT-002", FAILING_BLEND)
        assert "calorific_value_kcal" in report.failed_parameters
        assert "total_moisture_pct" in report.failed_parameters

    def test_passing_blend_no_failed_params(self, checker):
        report = checker.check("LOT-001", PASSING_BLEND)
        assert len(report.failed_parameters) == 0

    def test_compliance_pct_100_for_pass(self, checker):
        report = checker.check("LOT-001", PASSING_BLEND)
        assert report.compliance_pct == 100.0

    def test_compliance_pct_less_than_100_for_fail(self, checker):
        report = checker.check("LOT-002", FAILING_BLEND)
        assert report.compliance_pct < 100.0

    def test_empty_blend_raises(self, checker):
        with pytest.raises(ValueError, match="blend_quality cannot be empty"):
            checker.check("LOT-003", {})

    def test_missing_parameter_warns(self, checker):
        partial_blend = {"calorific_value_kcal": 6000}  # ash, moisture, sulfur missing
        report = checker.check("LOT-004", partial_blend)
        assert report.overall_status in (ComplianceStatus.WARN, ComplianceStatus.FAIL)

    def test_recommendations_generated_for_failures(self, checker):
        report = checker.check("LOT-002", FAILING_BLEND)
        assert len(report.recommendations) > 0

    def test_warn_band_triggers_warn(self):
        # Custom specs with wide warn band
        specs = {"gcv": {"min": 5000, "max": 7000, "warn_band": 0.20}}
        c = BlendComplianceChecker(specs=specs)
        # 5050 is within 20% of range (400 units) above min 5000 -> WARN
        report = c.check("W-001", {"gcv": 5050})
        assert report.checks["gcv"].status == ComplianceStatus.WARN


class TestBatch:
    def test_returns_dict(self, checker):
        blends = {"LOT-A": PASSING_BLEND, "LOT-B": FAILING_BLEND}
        results = checker.check_batch(blends)
        assert set(results.keys()) == {"LOT-A", "LOT-B"}

    def test_correct_status_per_lot(self, checker):
        blends = {"PASS": PASSING_BLEND, "FAIL": FAILING_BLEND}
        results = checker.check_batch(blends)
        assert results["PASS"].overall_status == ComplianceStatus.PASS
        assert results["FAIL"].overall_status == ComplianceStatus.FAIL


class TestSummaryTable:
    def test_returns_list_of_dicts(self, checker):
        blends = {"LOT-A": PASSING_BLEND}
        reports = checker.check_batch(blends)
        table = checker.summary_table(reports)
        assert isinstance(table, list)
        assert "blend_id" in table[0]
        assert "overall_status" in table[0]
