"""Unit tests for BlendOptimizer."""
import pytest
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, "/Users/johndoe/projects/coal-blending-optimizer")
from src.main import BlendOptimizer, DEFAULT_QUALITY_SPECS


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "source_id": ["A", "B", "C", "D"],
        "calorific_value": [6200, 5900, 6050, 5800],
        "total_moisture": [8.5, 11.0, 9.5, 13.0],
        "ash_pct": [5.0, 7.5, 6.0, 8.0],
        "sulfur_pct": [0.4, 0.7, 0.5, 0.8],
        "volume_available_mt": [50000, 80000, 60000, 40000],
        "price_usd_t": [85, 72, 78, 68],
    })


@pytest.fixture
def optimizer():
    return BlendOptimizer()


class TestValidation:
    def test_empty_df_raises(self, optimizer):
        with pytest.raises(ValueError, match="empty"):
            optimizer.validate(pd.DataFrame())

    def test_missing_columns_raises(self, optimizer):
        df = pd.DataFrame({"calorific_value": [6000]})
        with pytest.raises(ValueError, match="Missing required columns"):
            optimizer.validate(df)

    def test_valid_df_passes(self, optimizer, sample_df):
        assert optimizer.validate(sample_df) is True


class TestPreprocess:
    def test_column_names_normalized(self, optimizer):
        df = pd.DataFrame({"Calorific Value": [6000], "ASH PCT": [5.0], "Total Moisture": [10.0], "Sulfur PCT": [0.5]})
        result = optimizer.preprocess(df)
        assert "calorific_value" in result.columns
        assert "ash_pct" in result.columns

    def test_empty_rows_dropped(self, optimizer):
        df = pd.DataFrame({"calorific_value": [6000, None, 5900], "total_moisture": [None, None, None], "ash_pct": [5.0, None, 6.0], "sulfur_pct": [0.5, None, 0.6]})
        result = optimizer.preprocess(df)
        assert len(result) < 3 or result.isnull().all(axis=1).sum() == 0


class TestOptimizeBlend:
    def test_returns_expected_keys(self, optimizer, sample_df):
        result = optimizer.optimize_blend(sample_df)
        assert "blend_ratios" in result
        assert "blended_quality" in result
        assert "quality_check" in result
        assert "feasible" in result

    def test_ratios_sum_to_100(self, optimizer, sample_df):
        result = optimizer.optimize_blend(sample_df)
        total = sum(result["blend_ratios"].values())
        assert abs(total - 100.0) < 0.5

    def test_volume_matches_target(self, optimizer, sample_df):
        target = 100_000
        result = optimizer.optimize_blend(sample_df, target_volume_mt=target)
        total_vol = sum(result["blend_volume_mt"].values())
        assert abs(total_vol - target) < 1.0

    def test_cost_calculated_when_price_present(self, optimizer, sample_df):
        result = optimizer.optimize_blend(sample_df)
        assert "estimated_cost_usd" in result
        assert result["estimated_cost_usd"] > 0

    def test_insufficient_volume_raises(self, optimizer, sample_df):
        with pytest.raises(ValueError, match="Insufficient volume"):
            optimizer.optimize_blend(sample_df, target_volume_mt=999_999_999)

    def test_blended_cv_within_source_range(self, optimizer, sample_df):
        result = optimizer.optimize_blend(sample_df)
        cv = result["blended_quality"]["calorific_value"]
        assert 5800 <= cv <= 6200

    def test_auto_source_ids_assigned(self, optimizer):
        df = pd.DataFrame({
            "calorific_value": [6000, 5900],
            "total_moisture": [10.0, 11.0],
            "ash_pct": [6.0, 7.0],
            "sulfur_pct": [0.5, 0.6],
        })
        result = optimizer.optimize_blend(df)
        assert any("SOURCE" in k for k in result["blend_ratios"])

    def test_quality_check_structure(self, optimizer, sample_df):
        result = optimizer.optimize_blend(sample_df)
        for param, check in result["quality_check"].items():
            assert "value" in check
            assert "pass" in check


class TestSensitivityAnalysis:
    def test_returns_dataframe(self, optimizer, sample_df):
        result = optimizer.sensitivity_analysis(sample_df, param="calorific_value")
        assert isinstance(result, pd.DataFrame)
        assert "delta_pct" in result.columns

    def test_nine_scenarios(self, optimizer, sample_df):
        result = optimizer.sensitivity_analysis(sample_df, delta_pct=5.0)
        assert len(result) == 9


class TestAnalyze:
    def test_analyze_returns_stats(self, optimizer, sample_df):
        result = optimizer.analyze(sample_df)
        assert result["total_records"] == 4
        assert "summary_stats" in result
        assert "means" in result

    def test_to_dataframe_flat(self, optimizer, sample_df):
        result = optimizer.analyze(sample_df)
        df = optimizer.to_dataframe(result)
        assert isinstance(df, pd.DataFrame)
        assert "metric" in df.columns
        assert "value" in df.columns
