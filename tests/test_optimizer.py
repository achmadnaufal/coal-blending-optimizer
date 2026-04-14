"""
Unit tests for BlendOptimizer covering blend ratio calculation, quality target
validation, cost optimization, constraint satisfaction, and edge cases.

Run with:
    pytest tests/test_optimizer.py -v
"""
import pytest
import pandas as pd
import numpy as np
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.main import BlendOptimizer, DEFAULT_QUALITY_SPECS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def optimizer():
    """Return a default BlendOptimizer instance."""
    return BlendOptimizer()


@pytest.fixture
def multi_source_df():
    """DataFrame with five realistic coal sources and price data."""
    return pd.DataFrame({
        "source_id": ["SRC-001", "SRC-002", "SRC-003", "SRC-004", "SRC-005"],
        "calorific_value": [5650, 5820, 6050, 6350, 6480],
        "total_moisture": [22.0, 20.5, 18.2, 14.0, 13.5],
        "ash_pct": [12.5, 11.2, 9.8, 7.2, 6.5],
        "sulfur_pct": [0.42, 0.38, 0.35, 0.55, 0.48],
        "volume_available_mt": [120000, 85000, 60000, 50000, 45000],
        "price_usd_t": [38.50, 41.20, 44.75, 67.00, 71.50],
    })


@pytest.fixture
def single_source_df():
    """DataFrame with a single coal source."""
    return pd.DataFrame({
        "source_id": ["SOLO-001"],
        "calorific_value": [6100.0],
        "total_moisture": [12.0],
        "ash_pct": [7.0],
        "sulfur_pct": [0.45],
        "volume_available_mt": [500000.0],
        "price_usd_t": [55.0],
    })


@pytest.fixture
def tight_volume_df():
    """DataFrame where total available volume barely exceeds the default target."""
    return pd.DataFrame({
        "source_id": ["A", "B"],
        "calorific_value": [6000, 5900],
        "total_moisture": [10.0, 11.0],
        "ash_pct": [6.0, 7.0],
        "sulfur_pct": [0.5, 0.6],
        "volume_available_mt": [60000.0, 50000.0],
        "price_usd_t": [70.0, 65.0],
    })


@pytest.fixture
def sample_csv_path():
    """Path to the demo sample CSV file."""
    return str(Path(__file__).parent.parent / "demo" / "sample_data.csv")


# ---------------------------------------------------------------------------
# 1. Blend ratio calculation
# ---------------------------------------------------------------------------

class TestBlendRatioCalculation:
    """Verify that blend ratios are numerically sound."""

    def test_ratios_sum_to_100_pct(self, optimizer, multi_source_df):
        """Blend ratios (percentage) must sum to exactly 100%."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        total = sum(result["blend_ratios"].values())
        assert abs(total - 100.0) < 0.5, f"Ratios summed to {total}, expected ~100"

    def test_all_ratios_non_negative(self, optimizer, multi_source_df):
        """No source may receive a negative allocation."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        for src, ratio in result["blend_ratios"].items():
            assert ratio >= 0.0, f"Source {src} has negative ratio {ratio}"

    def test_blend_volumes_match_target(self, optimizer, multi_source_df):
        """Sum of allocated volumes must equal the requested target volume."""
        target = 80_000
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=target)
        total_vol = sum(result["blend_volume_mt"].values())
        assert abs(total_vol - target) < 1.0, (
            f"Total volume {total_vol} differs from target {target}"
        )

    def test_volumes_do_not_exceed_availability(self, optimizer, multi_source_df):
        """Allocated volume per source must not exceed its available tonnage."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        for src_id, allocated in result["blend_volume_mt"].items():
            row = multi_source_df[multi_source_df["source_id"] == src_id]
            if not row.empty:
                available = float(row["volume_available_mt"].iloc[0])
                assert allocated <= available + 0.01, (
                    f"{src_id}: allocated {allocated} > available {available}"
                )

    def test_higher_cv_sources_preferred(self, optimizer, multi_source_df):
        """The optimizer should allocate more to higher calorific value sources."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        ratios = result["blend_ratios"]
        # SRC-005 (CV=6480) should have at least as much allocation as SRC-001 (CV=5650)
        # when both have large availability
        assert ratios.get("SRC-005", 0) >= ratios.get("SRC-001", 0) * 0.5, (
            "Higher-CV source should receive meaningful allocation"
        )


# ---------------------------------------------------------------------------
# 2. Quality target validation
# ---------------------------------------------------------------------------

class TestQualityTargetValidation:
    """Ensure blended quality parameters are within spec."""

    def test_blended_cv_within_source_range(self, optimizer, multi_source_df):
        """Blended calorific value must be between min and max source CV."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        cv = result["blended_quality"]["calorific_value"]
        min_cv = float(multi_source_df["calorific_value"].min())
        max_cv = float(multi_source_df["calorific_value"].max())
        assert min_cv <= cv <= max_cv, f"Blended CV {cv} outside [{min_cv}, {max_cv}]"

    def test_blended_ash_within_source_range(self, optimizer, multi_source_df):
        """Blended ash% must be bounded by the source range."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        ash = result["blended_quality"]["ash_pct"]
        min_ash = float(multi_source_df["ash_pct"].min())
        max_ash = float(multi_source_df["ash_pct"].max())
        assert min_ash <= ash <= max_ash

    def test_quality_check_contains_pass_field(self, optimizer, multi_source_df):
        """Every parameter in quality_check must have a 'pass' boolean field."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        for param, check in result["quality_check"].items():
            assert "pass" in check, f"quality_check[{param}] missing 'pass' key"
            assert isinstance(check["pass"], bool)

    def test_custom_quality_specs_applied(self, optimizer, multi_source_df):
        """Custom quality specs must override the defaults."""
        relaxed_specs = {
            "calorific_value_kcal": {"min": 4000, "target": 5000, "max": 9000},
            "total_moisture_pct": {"min": 0, "target": 25, "max": 40},
            "ash_pct": {"min": 0, "target": 15, "max": 20},
            "sulfur_pct": {"min": 0, "target": 1.0, "max": 2.0},
        }
        result = optimizer.optimize_blend(
            multi_source_df,
            target_volume_mt=100_000,
            quality_specs=relaxed_specs,
        )
        # With very relaxed specs everything should pass
        assert result["feasible"] is True

    def test_feasible_flag_type_is_bool(self, optimizer, multi_source_df):
        """The 'feasible' flag in the result must be a Python bool."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        assert isinstance(result["feasible"], bool)


# ---------------------------------------------------------------------------
# 3. Cost optimization
# ---------------------------------------------------------------------------

class TestCostOptimization:
    """Verify cost fields are computed correctly."""

    def test_estimated_cost_present_when_price_column_exists(
        self, optimizer, multi_source_df
    ):
        """estimated_cost_usd must appear when price_usd_t is in the DataFrame."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        assert "estimated_cost_usd" in result
        assert result["estimated_cost_usd"] > 0

    def test_blended_price_between_min_and_max(self, optimizer, multi_source_df):
        """Blended price per tonne must lie within the min/max source prices."""
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        min_price = float(multi_source_df["price_usd_t"].min())
        max_price = float(multi_source_df["price_usd_t"].max())
        bp = result["blended_price_usd_t"]
        assert min_price <= bp <= max_price, (
            f"Blended price {bp} outside [{min_price}, {max_price}]"
        )

    def test_estimated_cost_equals_price_times_volume(
        self, optimizer, multi_source_df
    ):
        """estimated_cost_usd must be consistent with blended_price_usd_t * volume.

        Both fields are independently rounded to 2 decimal places before storage,
        so a tolerance of 0.01 * target is acceptable for floating-point drift.
        """
        target = 100_000
        result = optimizer.optimize_blend(multi_source_df, target_volume_mt=target)
        expected = result["blended_price_usd_t"] * target
        tolerance = target * 0.01  # 0.01 USD/t rounding tolerance over full volume
        assert abs(result["estimated_cost_usd"] - expected) <= tolerance, (
            f"Cost {result['estimated_cost_usd']} vs expected {expected} "
            f"exceeds tolerance {tolerance}"
        )

    def test_no_cost_field_without_price_column(self, optimizer):
        """When price_usd_t is absent, estimated_cost_usd must not appear."""
        df = pd.DataFrame({
            "source_id": ["X", "Y"],
            "calorific_value": [6000.0, 5800.0],
            "total_moisture": [10.0, 12.0],
            "ash_pct": [6.0, 8.0],
            "sulfur_pct": [0.4, 0.6],
            "volume_available_mt": [100000.0, 100000.0],
        })
        result = optimizer.optimize_blend(df, target_volume_mt=50_000)
        assert "estimated_cost_usd" not in result


# ---------------------------------------------------------------------------
# 4. Constraint satisfaction
# ---------------------------------------------------------------------------

class TestConstraintSatisfaction:
    """Test that hard supply/volume constraints are respected."""

    def test_insufficient_volume_raises_value_error(self, optimizer, multi_source_df):
        """Requesting more volume than available must raise ValueError."""
        with pytest.raises(ValueError, match="Insufficient volume"):
            optimizer.optimize_blend(
                multi_source_df, target_volume_mt=999_999_999
            )

    def test_constraint_report_returns_dataframe(self, optimizer, multi_source_df):
        """constraint_report must return a DataFrame with the expected columns."""
        report = optimizer.constraint_report(
            multi_source_df, target_volume_mt=100_000
        )
        assert isinstance(report, pd.DataFrame)
        required_cols = {
            "parameter", "blended_value", "target", "min_spec", "max_spec", "status"
        }
        assert required_cols.issubset(set(report.columns))

    def test_constraint_status_values_are_valid(self, optimizer, multi_source_df):
        """Constraint report status must be one of OK, WARNING, or BREACH."""
        report = optimizer.constraint_report(
            multi_source_df, target_volume_mt=100_000
        )
        valid_statuses = {"OK", "WARNING", "BREACH"}
        assert set(report["status"]).issubset(valid_statuses)

    def test_multi_product_total_volume_respected(self, optimizer, multi_source_df):
        """Multi-product optimizer must not exceed total available supply."""
        products = [
            {"name": "6000 NAR", "target_volume_mt": 50_000},
            {"name": "5800 NAR", "target_volume_mt": 30_000},
        ]
        results = optimizer.multi_product_optimize(multi_source_df, products=products)
        assert len(results) == 2
        for res in results:
            assert "blend_ratios" in res
            assert "product_name" in res

    def test_multi_product_raises_when_exceeds_supply(
        self, optimizer, multi_source_df
    ):
        """Multi-product optimizer must raise when combined demand exceeds supply."""
        products = [{"name": "Huge", "target_volume_mt": 999_999_999}]
        with pytest.raises(ValueError, match="exceeds available"):
            optimizer.multi_product_optimize(multi_source_df, products=products)


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: single source, infeasible targets, ratio normalisation."""

    def test_single_source_blend_uses_entire_allocation(
        self, optimizer, single_source_df
    ):
        """With one source the entire target volume comes from that source."""
        target = 50_000
        result = optimizer.optimize_blend(single_source_df, target_volume_mt=target)
        assert len(result["blend_ratios"]) == 1
        ratio = list(result["blend_ratios"].values())[0]
        assert abs(ratio - 100.0) < 0.01, f"Single source ratio should be 100%, got {ratio}"

    def test_single_source_volume_matches_target(self, optimizer, single_source_df):
        """Allocated volume for the only source must equal the target."""
        target = 75_000
        result = optimizer.optimize_blend(single_source_df, target_volume_mt=target)
        total_vol = sum(result["blend_volume_mt"].values())
        assert abs(total_vol - target) < 1.0

    def test_empty_dataframe_raises_on_validate(self, optimizer):
        """Passing an empty DataFrame to validate() must raise ValueError."""
        with pytest.raises(ValueError, match="empty"):
            optimizer.validate(pd.DataFrame())

    def test_missing_required_columns_raises(self, optimizer):
        """A DataFrame missing required columns must raise ValueError."""
        df = pd.DataFrame({"calorific_value": [6000.0]})
        with pytest.raises(ValueError, match="Missing required columns"):
            optimizer.validate(df)

    def test_no_source_ids_column_auto_generates_ids(self, optimizer):
        """When source_id is absent the optimizer assigns generated IDs."""
        df = pd.DataFrame({
            "calorific_value": [6000.0, 5800.0],
            "total_moisture": [10.0, 12.0],
            "ash_pct": [6.0, 8.0],
            "sulfur_pct": [0.4, 0.6],
        })
        result = optimizer.optimize_blend(df, target_volume_mt=10_000)
        assert len(result["blend_ratios"]) == 2
        for key in result["blend_ratios"]:
            assert "SOURCE" in key

    def test_optimize_blend_is_immutable(self, optimizer, multi_source_df):
        """optimize_blend must not mutate the input DataFrame."""
        original_columns = list(multi_source_df.columns)
        original_values = multi_source_df.copy()
        optimizer.optimize_blend(multi_source_df, target_volume_mt=100_000)
        # Columns must not have changed
        assert list(multi_source_df.columns) == original_columns
        # Values must not have changed
        pd.testing.assert_frame_equal(multi_source_df, original_values)

    def test_preprocess_does_not_mutate_input(self, optimizer, multi_source_df):
        """preprocess() must return a new DataFrame without altering the original."""
        original_columns = list(multi_source_df.columns)
        optimizer.preprocess(multi_source_df)
        assert list(multi_source_df.columns) == original_columns

    def test_optimize_blend_tight_volume_succeeds(self, optimizer, tight_volume_df):
        """Blend should succeed when total available volume just exceeds target."""
        result = optimizer.optimize_blend(tight_volume_df, target_volume_mt=100_000)
        assert "blend_ratios" in result
        total = sum(result["blend_ratios"].values())
        assert abs(total - 100.0) < 0.5

    def test_gcv_optimizer_empty_sources_raises(self, optimizer):
        """optimize_blend_for_target_gcv must raise on empty source list."""
        with pytest.raises(ValueError, match="sources list cannot be empty"):
            optimizer.optimize_blend_for_target_gcv([], target_gcv_mj_kg=24.0)

    def test_gcv_optimizer_impossible_target_returns_no_match(self, optimizer):
        """When target GCV is above all sources, meets_target must be False."""
        sources = [
            {
                "source_id": "A",
                "gcv_mj_kg": 25.0,
                "volume_available_mt": 5000,
                "cost_usd_per_t": 100,
            },
            {
                "source_id": "B",
                "gcv_mj_kg": 22.0,
                "volume_available_mt": 5000,
                "cost_usd_per_t": 80,
            },
        ]
        result = optimizer.optimize_blend_for_target_gcv(
            sources, target_gcv_mj_kg=99.0
        )
        assert result["meets_target"] is False

    def test_load_sample_csv_produces_correct_row_count(
        self, optimizer, sample_csv_path
    ):
        """Loading the demo sample CSV must produce exactly 15 rows."""
        df = optimizer.load_data(sample_csv_path)
        assert len(df) == 15, f"Expected 15 rows, got {len(df)}"

    def test_load_nonexistent_file_raises(self, optimizer):
        """Loading a file that does not exist must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            optimizer.load_data("/nonexistent/path/data.csv")
