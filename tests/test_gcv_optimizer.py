"""
Unit tests for optimize_blend_for_target_gcv.
"""
import pytest
from src.main import CoalBlendingOptimizer


@pytest.fixture
def optimizer():
    return CoalBlendingOptimizer()


@pytest.fixture
def two_sources():
    return [
        {"source_id": "PIT-A", "gcv_mj_kg": 27.0, "volume_available_mt": 5000, "cost_usd_per_t": 110},
        {"source_id": "PIT-B", "gcv_mj_kg": 21.0, "volume_available_mt": 8000, "cost_usd_per_t": 75},
    ]


class TestOptimizeBlendForTargetGCV:

    def test_midpoint_blend(self, optimizer, two_sources):
        """Target at midpoint of two sources → 50/50 blend."""
        result = optimizer.optimize_blend_for_target_gcv(two_sources, target_gcv_mj_kg=24.0)
        assert result["meets_target"] is True
        assert abs(result["blended_gcv_mj_kg"] - 24.0) <= 0.5

    def test_blend_ratios_sum_to_one(self, optimizer, two_sources):
        result = optimizer.optimize_blend_for_target_gcv(two_sources, target_gcv_mj_kg=25.0)
        if result["meets_target"]:
            total = sum(result["blend_ratios"].values())
            assert abs(total - 1.0) < 0.01

    def test_empty_sources_raises(self, optimizer):
        with pytest.raises(ValueError, match="sources list cannot be empty"):
            optimizer.optimize_blend_for_target_gcv([], target_gcv_mj_kg=24.0)

    def test_invalid_target_gcv_raises(self, optimizer, two_sources):
        with pytest.raises(ValueError, match="target_gcv_mj_kg must be positive"):
            optimizer.optimize_blend_for_target_gcv(two_sources, target_gcv_mj_kg=0)

    def test_unreachable_target_returns_no_match(self, optimizer, two_sources):
        """Target higher than all sources cannot be met."""
        result = optimizer.optimize_blend_for_target_gcv(two_sources, target_gcv_mj_kg=35.0)
        assert result["meets_target"] is False

    def test_blending_cost_calculated(self, optimizer, two_sources):
        result = optimizer.optimize_blend_for_target_gcv(two_sources, target_gcv_mj_kg=24.0)
        if result["meets_target"]:
            assert "blending_cost_usd_per_t" in result
            assert result["blending_cost_usd_per_t"] > 0

    def test_three_source_blend_selects_best_pair(self, optimizer):
        sources = [
            {"source_id": "A", "gcv_mj_kg": 28.0, "volume_available_mt": 3000, "cost_usd_per_t": 120},
            {"source_id": "B", "gcv_mj_kg": 22.0, "volume_available_mt": 5000, "cost_usd_per_t": 80},
            {"source_id": "C", "gcv_mj_kg": 19.0, "volume_available_mt": 6000, "cost_usd_per_t": 60},
        ]
        result = optimizer.optimize_blend_for_target_gcv(sources, target_gcv_mj_kg=25.0)
        assert result["meets_target"] is True
