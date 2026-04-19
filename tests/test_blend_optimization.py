"""
Comprehensive unit tests for BlendOptimizer covering:
  - Blending optimisation and ratio arithmetic
  - Constraint checking (infeasible specs, inverted bounds)
  - Quality calculations (blended CV, ash, sulfur, moisture)
  - Input validation (negative values, percentages >100, empty sources)
  - Immutability guarantees
  - Environmental impact calculations
  - GCV-target blend optimisation

Run with:
    pytest tests/test_blend_optimization.py -v
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.main import BlendOptimizer, DEFAULT_QUALITY_SPECS, REQUIRED_QUALITY_COLUMNS


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def optimizer() -> BlendOptimizer:
    """Return a default BlendOptimizer instance."""
    return BlendOptimizer()


@pytest.fixture
def standard_df() -> pd.DataFrame:
    """Standard 5-source Indonesian sub-bituminous coal fixture (GAR 4200-6200)."""
    return pd.DataFrame({
        "source_id":          ["KAL-A", "KAL-B", "KAL-C", "SUM-A", "SUM-B"],
        "calorific_value":    [4200.0,  5100.0,  5800.0,  4650.0,  6200.0],
        "total_moisture":     [35.0,    28.0,    20.5,    33.0,    15.0],
        "ash_pct":            [5.5,     7.2,     9.1,     6.0,     8.5],
        "sulfur_pct":         [0.25,    0.31,    0.40,    0.27,    0.52],
        "volume_available_mt":[200000,  130000,  80000,   160000,  55000],
        "price_usd_t":        [21.50,   27.80,   36.50,   23.00,   48.00],
    })


@pytest.fixture
def two_source_df() -> pd.DataFrame:
    """Minimal two-source fixture for ratio arithmetic tests."""
    return pd.DataFrame({
        "source_id":          ["HIGH-CV", "LOW-CV"],
        "calorific_value":    [6400.0,    4200.0],
        "total_moisture":     [12.0,      36.0],
        "ash_pct":            [6.0,       5.5],
        "sulfur_pct":         [0.45,      0.25],
        "volume_available_mt":[100000,    100000],
        "price_usd_t":        [55.0,      20.0],
    })


@pytest.fixture
def demo_csv_path() -> str:
    """Absolute path to the demo sample_data.csv (15 Indonesian sources)."""
    return str(Path(__file__).parent.parent / "demo" / "sample_data.csv")


# ===========================================================================
# 1. Blending optimisation — ratio and volume arithmetic
# ===========================================================================

class TestBlendOptimisation:
    """Verify numeric correctness of the score-weighted allocation."""

    def test_ratios_sum_to_100(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=150_000)
        total = sum(result["blend_ratios"].values())
        assert abs(total - 100.0) < 0.5, f"Ratios summed to {total:.4f}"

    def test_volumes_sum_to_target(self, optimizer, standard_df):
        target = 120_000
        result = optimizer.optimize_blend(standard_df, target_volume_mt=target)
        total_vol = sum(result["blend_volume_mt"].values())
        assert abs(total_vol - target) < 1.0

    def test_all_ratios_non_negative(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        for src, ratio in result["blend_ratios"].items():
            assert ratio >= 0.0, f"{src} has negative ratio {ratio}"

    def test_volumes_respect_availability(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=200_000)
        for src_id, alloc in result["blend_volume_mt"].items():
            row = standard_df[standard_df["source_id"] == src_id]
            if not row.empty:
                available = float(row["volume_available_mt"].iloc[0])
                assert alloc <= available + 0.01, (
                    f"{src_id}: allocated {alloc:.1f} > available {available:.1f}"
                )

    def test_high_cv_source_preferred_over_low(self, optimizer, two_source_df):
        """Score weighting must prefer the high-CV source."""
        result = optimizer.optimize_blend(two_source_df, target_volume_mt=80_000)
        ratios = result["blend_ratios"]
        assert ratios["HIGH-CV"] > ratios["LOW-CV"], (
            "High-CV source should receive greater allocation than low-CV"
        )

    def test_result_contains_required_keys(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        for key in ("blend_ratios", "blend_volume_mt", "blended_quality",
                    "quality_check", "feasible"):
            assert key in result, f"Missing key '{key}' in result"

    def test_feasible_flag_is_bool(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        assert isinstance(result["feasible"], bool)

    def test_single_source_ratio_is_100_pct(self, optimizer):
        df = pd.DataFrame({
            "source_id":          ["SOLO"],
            "calorific_value":    [5500.0],
            "total_moisture":     [25.0],
            "ash_pct":            [7.0],
            "sulfur_pct":         [0.35],
            "volume_available_mt":[500_000],
        })
        result = optimizer.optimize_blend(df, target_volume_mt=50_000)
        ratio = list(result["blend_ratios"].values())[0]
        assert abs(ratio - 100.0) < 0.01

    def test_auto_source_id_generation(self, optimizer):
        """Missing source_id column should auto-generate SOURCE_N identifiers."""
        df = pd.DataFrame({
            "calorific_value":    [5800.0, 5200.0],
            "total_moisture":     [20.0,   27.0],
            "ash_pct":            [7.0,    6.5],
            "sulfur_pct":         [0.35,   0.28],
        })
        result = optimizer.optimize_blend(df, target_volume_mt=10_000)
        assert all("SOURCE" in k for k in result["blend_ratios"]), (
            "Auto-generated IDs must contain 'SOURCE'"
        )

    def test_custom_specs_override_defaults(self, optimizer, standard_df):
        very_relaxed = {
            "calorific_value_kcal": {"min": 1000, "target": 5000, "max": 9000},
            "total_moisture_pct":   {"min": 0,    "target": 30,   "max": 60},
            "ash_pct":              {"min": 0,    "target": 10,   "max": 30},
            "sulfur_pct":           {"min": 0,    "target": 1.0,  "max": 5.0},
        }
        result = optimizer.optimize_blend(
            standard_df, target_volume_mt=100_000, quality_specs=very_relaxed
        )
        assert result["feasible"] is True


# ===========================================================================
# 2. Quality calculations — blended parameter arithmetic
# ===========================================================================

class TestQualityCalculations:
    """Verify that blended quality values are physically correct."""

    def test_blended_cv_within_source_range(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        cv = result["blended_quality"]["calorific_value"]
        assert standard_df["calorific_value"].min() <= cv <= standard_df["calorific_value"].max()

    def test_blended_ash_within_source_range(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        ash = result["blended_quality"]["ash_pct"]
        assert standard_df["ash_pct"].min() <= ash <= standard_df["ash_pct"].max()

    def test_blended_sulfur_within_source_range(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        sulfur = result["blended_quality"]["sulfur_pct"]
        assert standard_df["sulfur_pct"].min() <= sulfur <= standard_df["sulfur_pct"].max()

    def test_blended_moisture_within_source_range(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        moisture = result["blended_quality"]["total_moisture"]
        assert (
            standard_df["total_moisture"].min()
            <= moisture
            <= standard_df["total_moisture"].max()
        )

    def test_quality_check_has_pass_field(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        for param, check in result["quality_check"].items():
            assert "pass" in check, f"quality_check[{param}] missing 'pass'"
            assert isinstance(check["pass"], bool)

    def test_quality_check_value_matches_blended_quality(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        for param, check in result["quality_check"].items():
            blended_val = result["blended_quality"].get(param)
            if blended_val is not None:
                assert abs(check["value"] - blended_val) < 0.001, (
                    f"quality_check[{param}].value {check['value']} != "
                    f"blended_quality[{param}] {blended_val}"
                )

    def test_cost_field_present_with_price_column(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        assert "estimated_cost_usd" in result
        assert result["estimated_cost_usd"] > 0

    def test_cost_absent_without_price_column(self, optimizer):
        df = pd.DataFrame({
            "source_id":       ["A", "B"],
            "calorific_value": [5800.0, 5200.0],
            "total_moisture":  [20.0,   27.0],
            "ash_pct":         [7.0,    6.5],
            "sulfur_pct":      [0.35,   0.28],
            "volume_available_mt": [100000, 100000],
        })
        result = optimizer.optimize_blend(df, target_volume_mt=50_000)
        assert "estimated_cost_usd" not in result

    def test_blended_price_within_source_price_range(self, optimizer, standard_df):
        result = optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        bp = result["blended_price_usd_t"]
        assert standard_df["price_usd_t"].min() <= bp <= standard_df["price_usd_t"].max()

    def test_cost_consistent_with_price_times_volume(self, optimizer, standard_df):
        target = 100_000
        result = optimizer.optimize_blend(standard_df, target_volume_mt=target)
        expected = result["blended_price_usd_t"] * target
        # Allow up to 0.01 USD/t rounding error over full volume
        assert abs(result["estimated_cost_usd"] - expected) <= target * 0.01


# ===========================================================================
# 3. Constraint checking — infeasible specs and inverted bounds
# ===========================================================================

class TestConstraintChecking:
    """Verify that infeasible or inverted quality specs are caught early."""

    def test_inverted_spec_raises_at_construction(self):
        """min > max in quality_specs must raise at BlendOptimizer init."""
        bad_specs = {
            "calorific_value_kcal": {"min": 7000, "max": 5000},  # inverted
        }
        with pytest.raises(ValueError, match="exceeds max"):
            BlendOptimizer(config={"quality_specs": bad_specs})

    def test_inverted_spec_raises_in_optimize_blend(self, optimizer, standard_df):
        """Passing an inverted spec directly to optimize_blend must raise ValueError."""
        bad_specs = {
            "calorific_value_kcal": {"min": 8000, "max": 4000},
        }
        with pytest.raises(ValueError, match="exceeds max"):
            optimizer.optimize_blend(
                standard_df, target_volume_mt=100_000, quality_specs=bad_specs
            )

    def test_insufficient_volume_raises(self, optimizer, standard_df):
        """Requesting more volume than available must raise ValueError."""
        with pytest.raises(ValueError, match="Insufficient volume"):
            optimizer.optimize_blend(standard_df, target_volume_mt=999_999_999)

    def test_zero_target_volume_raises(self, optimizer, standard_df):
        """A target volume of zero must raise ValueError."""
        with pytest.raises(ValueError):
            optimizer.optimize_blend(standard_df, target_volume_mt=0)

    def test_negative_target_volume_raises(self, optimizer, standard_df):
        """A negative target volume must raise ValueError."""
        with pytest.raises(ValueError):
            optimizer.optimize_blend(standard_df, target_volume_mt=-50_000)

    def test_constraint_report_returns_dataframe(self, optimizer, standard_df):
        report = optimizer.constraint_report(standard_df, target_volume_mt=100_000)
        assert isinstance(report, pd.DataFrame)

    def test_constraint_report_columns(self, optimizer, standard_df):
        report = optimizer.constraint_report(standard_df, target_volume_mt=100_000)
        expected_cols = {
            "parameter", "blended_value", "target", "min_spec",
            "max_spec", "status"
        }
        assert expected_cols.issubset(set(report.columns))

    def test_constraint_report_status_valid_values(self, optimizer, standard_df):
        report = optimizer.constraint_report(standard_df, target_volume_mt=100_000)
        assert set(report["status"]).issubset({"OK", "WARNING", "BREACH"})

    def test_multi_product_raises_on_empty_products(self, optimizer, standard_df):
        with pytest.raises(ValueError):
            optimizer.multi_product_optimize(standard_df, products=[])

    def test_multi_product_raises_when_exceeds_supply(self, optimizer, standard_df):
        with pytest.raises(ValueError, match="exceeds available"):
            optimizer.multi_product_optimize(
                standard_df,
                products=[{"name": "Huge", "target_volume_mt": 999_999_999}],
            )

    def test_multi_product_returns_correct_count(self, optimizer, standard_df):
        products = [
            {"name": "Grade A", "target_volume_mt": 50_000},
            {"name": "Grade B", "target_volume_mt": 30_000},
            {"name": "Grade C", "target_volume_mt": 20_000},
        ]
        results = optimizer.multi_product_optimize(standard_df, products=products)
        assert len(results) == 3

    def test_multi_product_names_preserved(self, optimizer, standard_df):
        products = [
            {"name": "Premium Export", "target_volume_mt": 40_000},
            {"name": "Domestic Grade", "target_volume_mt": 20_000},
        ]
        results = optimizer.multi_product_optimize(standard_df, products=products)
        names = [r["product_name"] for r in results]
        assert names == ["Premium Export", "Domestic Grade"]


# ===========================================================================
# 4. Input validation — negative values, percentages >100, empty sources
# ===========================================================================

class TestInputValidation:
    """Validate that the validate() method catches all invalid inputs."""

    def test_empty_dataframe_raises(self, optimizer):
        with pytest.raises(ValueError, match="empty"):
            optimizer.validate(pd.DataFrame())

    def test_missing_required_columns_raises(self, optimizer):
        df = pd.DataFrame({"calorific_value": [5800.0], "ash_pct": [7.0]})
        with pytest.raises(ValueError, match="Missing required columns"):
            optimizer.validate(df)

    def test_negative_calorific_value_raises(self, optimizer):
        df = pd.DataFrame({
            "calorific_value": [-100.0, 5800.0],
            "total_moisture":  [20.0,   20.0],
            "ash_pct":         [7.0,    7.0],
            "sulfur_pct":      [0.3,    0.3],
        })
        with pytest.raises(ValueError, match="negative"):
            optimizer.validate(df)

    def test_negative_ash_raises(self, optimizer):
        df = pd.DataFrame({
            "calorific_value": [5800.0],
            "total_moisture":  [20.0],
            "ash_pct":         [-5.0],   # invalid
            "sulfur_pct":      [0.3],
        })
        with pytest.raises(ValueError, match="negative"):
            optimizer.validate(df)

    def test_negative_sulfur_raises(self, optimizer):
        df = pd.DataFrame({
            "calorific_value": [5800.0],
            "total_moisture":  [20.0],
            "ash_pct":         [7.0],
            "sulfur_pct":      [-0.1],   # invalid
        })
        with pytest.raises(ValueError, match="negative"):
            optimizer.validate(df)

    def test_negative_moisture_raises(self, optimizer):
        df = pd.DataFrame({
            "calorific_value": [5800.0],
            "total_moisture":  [-5.0],   # invalid
            "ash_pct":         [7.0],
            "sulfur_pct":      [0.3],
        })
        with pytest.raises(ValueError, match="negative"):
            optimizer.validate(df)

    def test_moisture_over_100_raises(self, optimizer):
        """Moisture percentage exceeding 100% must be rejected."""
        df = pd.DataFrame({
            "calorific_value": [5800.0],
            "total_moisture":  [120.0],  # impossible
            "ash_pct":         [7.0],
            "sulfur_pct":      [0.3],
        })
        with pytest.raises(ValueError, match="maximum"):
            optimizer.validate(df)

    def test_ash_over_100_raises(self, optimizer):
        df = pd.DataFrame({
            "calorific_value": [5800.0],
            "total_moisture":  [20.0],
            "ash_pct":         [110.0],  # impossible
            "sulfur_pct":      [0.3],
        })
        with pytest.raises(ValueError, match="maximum"):
            optimizer.validate(df)

    def test_negative_volume_raises(self, optimizer):
        df = pd.DataFrame({
            "calorific_value":    [5800.0],
            "total_moisture":     [20.0],
            "ash_pct":            [7.0],
            "sulfur_pct":         [0.3],
            "volume_available_mt":[-1000.0],  # invalid
        })
        with pytest.raises(ValueError, match="negative"):
            optimizer.validate(df)

    def test_valid_data_passes(self, optimizer, standard_df):
        assert optimizer.validate(standard_df) is True

    def test_column_name_case_insensitive_validation(self, optimizer):
        """Validation should normalise column names before checking."""
        df = pd.DataFrame({
            "Calorific Value": [5800.0],
            "Total Moisture":  [20.0],
            "ASH PCT":         [7.0],
            "Sulfur PCT":      [0.3],
        })
        # Should pass — column names are normalised during validation
        assert optimizer.validate(df) is True

    def test_unsupported_file_format_raises(self, optimizer, tmp_path):
        """Loading a .json file must raise ValueError (unsupported format)."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"a": 1}')
        with pytest.raises(ValueError, match="Unsupported file format"):
            optimizer.load_data(str(json_file))

    def test_nonexistent_file_raises(self, optimizer):
        with pytest.raises(FileNotFoundError):
            optimizer.load_data("/no/such/file.csv")


# ===========================================================================
# 5. Immutability guarantees
# ===========================================================================

class TestImmutability:
    """Ensure public methods never mutate the caller's DataFrame."""

    def test_optimize_blend_does_not_mutate(self, optimizer, standard_df):
        snapshot = standard_df.copy(deep=True)
        optimizer.optimize_blend(standard_df, target_volume_mt=100_000)
        pd.testing.assert_frame_equal(standard_df, snapshot)

    def test_preprocess_does_not_mutate(self, optimizer, standard_df):
        snapshot = standard_df.copy(deep=True)
        optimizer.preprocess(standard_df)
        pd.testing.assert_frame_equal(standard_df, snapshot)

    def test_sensitivity_analysis_does_not_mutate(self, optimizer, standard_df):
        snapshot = standard_df.copy(deep=True)
        optimizer.sensitivity_analysis(standard_df, param="calorific_value")
        pd.testing.assert_frame_equal(standard_df, snapshot)

    def test_multi_product_does_not_mutate(self, optimizer, standard_df):
        snapshot = standard_df.copy(deep=True)
        optimizer.multi_product_optimize(
            standard_df,
            products=[{"name": "P1", "target_volume_mt": 50_000}],
        )
        pd.testing.assert_frame_equal(standard_df, snapshot)

    def test_constraint_report_does_not_mutate(self, optimizer, standard_df):
        snapshot = standard_df.copy(deep=True)
        optimizer.constraint_report(standard_df, target_volume_mt=100_000)
        pd.testing.assert_frame_equal(standard_df, snapshot)

    def test_analyze_does_not_mutate(self, optimizer, standard_df):
        snapshot = standard_df.copy(deep=True)
        optimizer.analyze(standard_df)
        pd.testing.assert_frame_equal(standard_df, snapshot)


# ===========================================================================
# 6. Environmental impact calculations
# ===========================================================================

class TestEnvironmentalImpact:
    """Verify weighted-average environmental metrics."""

    def test_empty_blend_volumes_returns_empty(self, optimizer):
        result = optimizer.calculate_blend_environmental_impact({}, [])
        assert result == {}

    def test_zero_total_volume_returns_zero_metrics(self, optimizer):
        blend_vols = {"A": 0.0, "B": 0.0}
        source_data = [
            {"source_id": "A", "so2_emissions_kg_per_mt": 5.0},
            {"source_id": "B", "so2_emissions_kg_per_mt": 3.0},
        ]
        result = optimizer.calculate_blend_environmental_impact(blend_vols, source_data)
        assert result["so2_emissions_kg_per_mt"] == 0.0
        assert result["total_blend_volume_mt"] == 0

    def test_single_source_metrics_equal_source(self, optimizer):
        blend_vols = {"SRC-1": 10_000.0}
        source_data = [
            {
                "source_id": "SRC-1",
                "so2_emissions_kg_per_mt": 4.5,
                "ash_content_percent": 8.0,
                "sulfur_content_percent": 0.35,
                "carbon_intensity_tco2_per_mwh": 0.98,
            }
        ]
        result = optimizer.calculate_blend_environmental_impact(blend_vols, source_data)
        assert result["so2_emissions_kg_per_mt"] == 4.5
        assert result["ash_content_percent"] == 8.0
        assert result["total_blend_volume_mt"] == 10_000

    def test_50_50_blend_gives_average(self, optimizer):
        blend_vols = {"A": 50_000.0, "B": 50_000.0}
        source_data = [
            {"source_id": "A", "so2_emissions_kg_per_mt": 6.0, "ash_content_percent": 9.0},
            {"source_id": "B", "so2_emissions_kg_per_mt": 4.0, "ash_content_percent": 7.0},
        ]
        result = optimizer.calculate_blend_environmental_impact(blend_vols, source_data)
        assert result["so2_emissions_kg_per_mt"] == 5.0
        assert result["ash_content_percent"] == 8.0

    def test_weighted_blend_correct_proportion(self, optimizer):
        """30/70 split should give weighted average closer to 70% source."""
        blend_vols = {"HIGH": 30_000.0, "LOW": 70_000.0}
        source_data = [
            {"source_id": "HIGH", "so2_emissions_kg_per_mt": 10.0},
            {"source_id": "LOW",  "so2_emissions_kg_per_mt": 2.0},
        ]
        result = optimizer.calculate_blend_environmental_impact(blend_vols, source_data)
        expected = 0.3 * 10.0 + 0.7 * 2.0  # = 4.4
        assert abs(result["so2_emissions_kg_per_mt"] - expected) < 0.01

    def test_negative_blend_volume_raises(self, optimizer):
        with pytest.raises(ValueError, match="negative"):
            optimizer.calculate_blend_environmental_impact(
                {"A": -100.0}, [{"source_id": "A"}]
            )

    def test_total_blend_volume_is_int(self, optimizer):
        result = optimizer.calculate_blend_environmental_impact(
            {"X": 25_000.0},
            [{"source_id": "X", "so2_emissions_kg_per_mt": 3.0}],
        )
        assert isinstance(result["total_blend_volume_mt"], int)

    def test_missing_source_defaults_to_zero(self, optimizer):
        """A blend source with no matching source_data entry should contribute 0."""
        blend_vols = {"KNOWN": 50_000.0, "UNKNOWN": 50_000.0}
        source_data = [
            {"source_id": "KNOWN", "so2_emissions_kg_per_mt": 8.0},
        ]
        result = optimizer.calculate_blend_environmental_impact(blend_vols, source_data)
        assert result["so2_emissions_kg_per_mt"] == 4.0  # 0.5*8 + 0.5*0


# ===========================================================================
# 7. GCV-target blend optimisation
# ===========================================================================

class TestGcvTargetBlend:
    """Tests for optimize_blend_for_target_gcv."""

    @pytest.fixture
    def two_gcv_sources(self) -> list:
        return [
            {"source_id": "H", "gcv_mj_kg": 28.0, "volume_available_mt": 10_000, "cost_usd_per_t": 120},
            {"source_id": "L", "gcv_mj_kg": 20.0, "volume_available_mt": 10_000, "cost_usd_per_t": 75},
        ]

    def test_midpoint_target_achieved(self, optimizer, two_gcv_sources):
        result = optimizer.optimize_blend_for_target_gcv(
            two_gcv_sources, target_gcv_mj_kg=24.0, tolerance=0.1
        )
        assert result["meets_target"] is True
        assert abs(result["blended_gcv_mj_kg"] - 24.0) <= 0.1

    def test_ratios_sum_to_one(self, optimizer, two_gcv_sources):
        result = optimizer.optimize_blend_for_target_gcv(
            two_gcv_sources, target_gcv_mj_kg=24.0
        )
        assert result["meets_target"] is True
        total = sum(result["blend_ratios"].values())
        assert abs(total - 1.0) < 0.001

    def test_empty_sources_raises(self, optimizer):
        with pytest.raises(ValueError, match="empty"):
            optimizer.optimize_blend_for_target_gcv([], target_gcv_mj_kg=24.0)

    def test_non_positive_target_raises(self, optimizer, two_gcv_sources):
        with pytest.raises(ValueError):
            optimizer.optimize_blend_for_target_gcv(two_gcv_sources, target_gcv_mj_kg=0)

    def test_impossible_target_above_max_returns_no_match(self, optimizer, two_gcv_sources):
        result = optimizer.optimize_blend_for_target_gcv(
            two_gcv_sources, target_gcv_mj_kg=99.0
        )
        assert result["meets_target"] is False

    def test_impossible_target_below_min_returns_no_match(self, optimizer, two_gcv_sources):
        result = optimizer.optimize_blend_for_target_gcv(
            two_gcv_sources, target_gcv_mj_kg=5.0
        )
        assert result["meets_target"] is False

    def test_cost_calculation_correct(self, optimizer, two_gcv_sources):
        result = optimizer.optimize_blend_for_target_gcv(
            two_gcv_sources, target_gcv_mj_kg=24.0
        )
        assert result["meets_target"] is True
        r = result["blend_ratios"]
        h_ratio = r.get("H", 0)
        l_ratio = r.get("L", 0)
        expected_cost = h_ratio * 120 + l_ratio * 75
        assert abs(result["blending_cost_usd_per_t"] - expected_cost) < 0.5

    def test_no_valid_sources_raises(self, optimizer):
        """Sources with zero volume must be excluded and raise if none remain."""
        sources = [
            {"source_id": "A", "gcv_mj_kg": 26.0, "volume_available_mt": 0},
            {"source_id": "B", "gcv_mj_kg": 22.0, "volume_available_mt": 0},
        ]
        with pytest.raises(ValueError, match="No valid sources"):
            optimizer.optimize_blend_for_target_gcv(sources, target_gcv_mj_kg=24.0)


# ===========================================================================
# 8. Demo sample data file
# ===========================================================================

class TestDemoSampleData:
    """Smoke tests for the demo/sample_data.csv file."""

    def test_csv_loads_15_rows(self, optimizer, demo_csv_path):
        df = optimizer.load_data(demo_csv_path)
        assert len(df) == 15, f"Expected 15 rows, got {len(df)}"

    def test_csv_has_required_columns(self, optimizer, demo_csv_path):
        df = optimizer.load_data(demo_csv_path)
        required = {
            "stockpile_id", "mine_site", "tonnage_available", "cv_kcal_kg",
            "ash_pct", "sulfur_pct", "moisture_pct", "cost_per_tonne_usd",
        }
        assert required.issubset(set(df.columns)), (
            f"Missing columns: {required - set(df.columns)}"
        )

    def test_cv_values_in_indonesian_range(self, optimizer, demo_csv_path):
        """All calorific values should be in a realistic Indonesian coal range."""
        df = optimizer.load_data(demo_csv_path)
        assert (df["cv_kcal_kg"] >= 3800).all(), "CV below 3800 kcal/kg"
        assert (df["cv_kcal_kg"] <= 7000).all(), "CV above 7000 kcal/kg"

    def test_no_negative_values_in_quality_columns(self, optimizer, demo_csv_path):
        df = optimizer.load_data(demo_csv_path)
        quality_cols = [
            "cv_kcal_kg", "moisture_pct", "ash_pct", "sulfur_pct",
        ]
        for col in quality_cols:
            assert (df[col] >= 0).all(), f"Negative values found in '{col}'"

    def test_all_available_tonnes_positive(self, optimizer, demo_csv_path):
        df = optimizer.load_data(demo_csv_path)
        assert (df["tonnage_available"] > 0).all()

    def test_source_ids_are_unique(self, optimizer, demo_csv_path):
        df = optimizer.load_data(demo_csv_path)
        assert df["stockpile_id"].nunique() == 15, "stockpile_id values must be unique"


# ===========================================================================
# 9. Sensitivity analysis
# ===========================================================================

class TestSensitivityAnalysis:
    """Verify sensitivity analysis output shape and semantics."""

    def test_returns_dataframe(self, optimizer, standard_df):
        result = optimizer.sensitivity_analysis(standard_df, param="calorific_value")
        assert isinstance(result, pd.DataFrame)

    def test_nine_scenario_rows(self, optimizer, standard_df):
        result = optimizer.sensitivity_analysis(standard_df, delta_pct=5.0)
        assert len(result) == 9

    def test_delta_column_present(self, optimizer, standard_df):
        result = optimizer.sensitivity_analysis(standard_df)
        assert "delta_pct" in result.columns

    def test_negative_delta_pct_raises(self, optimizer, standard_df):
        with pytest.raises(ValueError, match="non-negative"):
            optimizer.sensitivity_analysis(standard_df, delta_pct=-5.0)

    def test_delta_range_symmetric(self, optimizer, standard_df):
        result = optimizer.sensitivity_analysis(
            standard_df, param="calorific_value", delta_pct=10.0
        )
        deltas = result["delta_pct"].tolist()
        assert deltas[0] == pytest.approx(-10.0, abs=0.1)
        assert deltas[-1] == pytest.approx(10.0, abs=0.1)
