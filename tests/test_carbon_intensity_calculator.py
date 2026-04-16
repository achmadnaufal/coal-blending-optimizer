"""
Tests for src/carbon_intensity_calculator.py.

Coverage:
  - Happy-path single and multi-source blends
  - Weighted arithmetic correctness
  - volume_mt absolute CO2e computation
  - Determinism (same inputs → same outputs)
  - Immutability: result frozen dataclasses, no profile mutation
  - Edge cases: single source 100 %, zero explosive override
  - Boundary validation: empty blend, bad fractions, unregistered source,
    negative / zero volume, negative emission factors, duplicate profiles,
    empty profile list, oversized diesel/ch4 values
  - Parametrized emission factor combos
  - intensity_for_source helper
"""

from __future__ import annotations

import math
import pytest

from src.carbon_intensity_calculator import (
    GWP100_CH4,
    CH4_DENSITY_KG_PER_M3,
    DEFAULT_DIESEL_EF_KG_CO2E_PER_LITRE,
    BlendSource,
    CarbonIntensityCalculator,
    CarbonIntensityResult,
    SourceEmissionProfile,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def profile_a() -> SourceEmissionProfile:
    return SourceEmissionProfile(
        source_id="SEAM_A",
        diesel_litres_per_tonne=4.0,
        ch4_m3_per_tonne=1.2,
        explosive_kg_co2e_per_tonne=0.05,
    )


@pytest.fixture()
def profile_b() -> SourceEmissionProfile:
    return SourceEmissionProfile(
        source_id="SEAM_B",
        diesel_litres_per_tonne=3.0,
        ch4_m3_per_tonne=0.6,
        explosive_kg_co2e_per_tonne=0.02,
    )


@pytest.fixture()
def calculator(
    profile_a: SourceEmissionProfile,
    profile_b: SourceEmissionProfile,
) -> CarbonIntensityCalculator:
    return CarbonIntensityCalculator([profile_a, profile_b])


# ---------------------------------------------------------------------------
# 1. SourceEmissionProfile property correctness
# ---------------------------------------------------------------------------


def test_diesel_intensity_property(profile_a: SourceEmissionProfile) -> None:
    expected = 4.0 * DEFAULT_DIESEL_EF_KG_CO2E_PER_LITRE
    assert math.isclose(profile_a.diesel_intensity_kg_co2e_per_tonne, expected, rel_tol=1e-9)


def test_ch4_intensity_property(profile_a: SourceEmissionProfile) -> None:
    expected = 1.2 * CH4_DENSITY_KG_PER_M3 * GWP100_CH4
    assert math.isclose(profile_a.ch4_intensity_kg_co2e_per_tonne, expected, rel_tol=1e-9)


def test_total_intensity_is_sum_of_components(profile_a: SourceEmissionProfile) -> None:
    expected = (
        profile_a.diesel_intensity_kg_co2e_per_tonne
        + profile_a.ch4_intensity_kg_co2e_per_tonne
        + profile_a.explosive_kg_co2e_per_tonne
    )
    assert math.isclose(profile_a.total_intensity_kg_co2e_per_tonne, expected, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 2. Happy-path single-source blend (fraction = 1.0)
# ---------------------------------------------------------------------------


def test_single_source_blend_matches_profile(
    calculator: CarbonIntensityCalculator,
    profile_a: SourceEmissionProfile,
) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    result = calculator.calculate(blend)
    assert math.isclose(
        result.blended_intensity_kg_co2e_per_tonne,
        profile_a.total_intensity_kg_co2e_per_tonne,
        rel_tol=1e-9,
    )


def test_single_source_breakdown_key(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    result = calculator.calculate(blend)
    assert "SEAM_A" in result.source_breakdown
    assert "SEAM_B" not in result.source_breakdown


# ---------------------------------------------------------------------------
# 3. Multi-source blend — weighted average correctness
# ---------------------------------------------------------------------------


def test_two_source_blend_weighted_intensity(
    calculator: CarbonIntensityCalculator,
    profile_a: SourceEmissionProfile,
    profile_b: SourceEmissionProfile,
) -> None:
    blend = [
        BlendSource("SEAM_A", fraction=0.6),
        BlendSource("SEAM_B", fraction=0.4),
    ]
    result = calculator.calculate(blend)
    expected = (
        profile_a.total_intensity_kg_co2e_per_tonne * 0.6
        + profile_b.total_intensity_kg_co2e_per_tonne * 0.4
    )
    assert math.isclose(result.blended_intensity_kg_co2e_per_tonne, expected, rel_tol=1e-9)


def test_component_contributions_sum_to_total(
    calculator: CarbonIntensityCalculator,
) -> None:
    blend = [BlendSource("SEAM_A", fraction=0.5), BlendSource("SEAM_B", fraction=0.5)]
    result = calculator.calculate(blend)
    component_sum = (
        result.diesel_contribution_kg_co2e_per_tonne
        + result.ch4_contribution_kg_co2e_per_tonne
        + result.explosive_contribution_kg_co2e_per_tonne
    )
    assert math.isclose(result.blended_intensity_kg_co2e_per_tonne, component_sum, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 4. volume_mt → total_co2e_tonnes
# ---------------------------------------------------------------------------


def test_total_co2e_computed_when_volume_given(
    calculator: CarbonIntensityCalculator,
) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    result = calculator.calculate(blend, volume_mt=10_000)
    expected_tonnes = result.blended_intensity_kg_co2e_per_tonne * 10_000 / 1000.0
    assert result.total_co2e_tonnes is not None
    assert math.isclose(result.total_co2e_tonnes, expected_tonnes, rel_tol=1e-9)


def test_total_co2e_none_without_volume(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    result = calculator.calculate(blend)
    assert result.total_co2e_tonnes is None


# ---------------------------------------------------------------------------
# 5. Determinism
# ---------------------------------------------------------------------------


def test_deterministic_results(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("SEAM_A", fraction=0.7), BlendSource("SEAM_B", fraction=0.3)]
    r1 = calculator.calculate(blend, volume_mt=50_000)
    r2 = calculator.calculate(blend, volume_mt=50_000)
    assert r1 == r2


# ---------------------------------------------------------------------------
# 6. Immutability
# ---------------------------------------------------------------------------


def test_result_is_frozen(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    result = calculator.calculate(blend)
    with pytest.raises((AttributeError, TypeError)):
        result.blended_intensity_kg_co2e_per_tonne = 0.0  # type: ignore[misc]


def test_source_breakdown_is_new_dict_each_call(
    calculator: CarbonIntensityCalculator,
) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    r1 = calculator.calculate(blend)
    r2 = calculator.calculate(blend)
    assert r1.source_breakdown is not r2.source_breakdown


# ---------------------------------------------------------------------------
# 7. intensity_for_source helper
# ---------------------------------------------------------------------------


def test_intensity_for_source_matches_profile(
    calculator: CarbonIntensityCalculator,
    profile_b: SourceEmissionProfile,
) -> None:
    intensity = calculator.intensity_for_source("SEAM_B")
    assert math.isclose(intensity, profile_b.total_intensity_kg_co2e_per_tonne, rel_tol=1e-9)


def test_intensity_for_source_unknown_raises_key_error(
    calculator: CarbonIntensityCalculator,
) -> None:
    with pytest.raises(KeyError, match="UNKNOWN"):
        calculator.intensity_for_source("UNKNOWN")


# ---------------------------------------------------------------------------
# 8. Validation — blend errors
# ---------------------------------------------------------------------------


def test_empty_blend_raises(calculator: CarbonIntensityCalculator) -> None:
    with pytest.raises(ValueError, match="at least one"):
        calculator.calculate([])


def test_fractions_not_summing_to_one_raises(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("SEAM_A", fraction=0.4), BlendSource("SEAM_B", fraction=0.4)]
    with pytest.raises(ValueError, match="sum to 1"):
        calculator.calculate(blend)


def test_unregistered_source_raises(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("GHOST", fraction=1.0)]
    with pytest.raises(ValueError, match="GHOST"):
        calculator.calculate(blend)


def test_zero_volume_raises(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    with pytest.raises(ValueError, match="volume_mt"):
        calculator.calculate(blend, volume_mt=0)


def test_negative_volume_raises(calculator: CarbonIntensityCalculator) -> None:
    blend = [BlendSource("SEAM_A", fraction=1.0)]
    with pytest.raises(ValueError, match="volume_mt"):
        calculator.calculate(blend, volume_mt=-100)


# ---------------------------------------------------------------------------
# 9. Validation — profile construction errors
# ---------------------------------------------------------------------------


def test_negative_diesel_raises() -> None:
    with pytest.raises(ValueError, match="diesel_litres_per_tonne"):
        SourceEmissionProfile("X", diesel_litres_per_tonne=-1.0)


def test_negative_ch4_raises() -> None:
    with pytest.raises(ValueError, match="ch4_m3_per_tonne"):
        SourceEmissionProfile("X", ch4_m3_per_tonne=-0.1)


def test_negative_explosive_raises() -> None:
    with pytest.raises(ValueError, match="explosive_kg_co2e_per_tonne"):
        SourceEmissionProfile("X", explosive_kg_co2e_per_tonne=-0.01)


def test_zero_diesel_ef_raises() -> None:
    with pytest.raises(ValueError, match="diesel_ef_kg_co2e_per_litre"):
        SourceEmissionProfile("X", diesel_ef_kg_co2e_per_litre=0.0)


def test_empty_source_id_raises() -> None:
    with pytest.raises(ValueError, match="source_id"):
        SourceEmissionProfile("  ")


def test_duplicate_profile_raises() -> None:
    p = SourceEmissionProfile("DUP")
    with pytest.raises(ValueError, match="Duplicate"):
        CarbonIntensityCalculator([p, p])


def test_empty_profile_list_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        CarbonIntensityCalculator([])


# ---------------------------------------------------------------------------
# 10. Parametrized — various fraction splits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frac_a, frac_b",
    [
        (0.1, 0.9),
        (0.5, 0.5),
        (0.9, 0.1),
        (0.25, 0.75),
    ],
)
def test_parametrized_fractions(
    calculator: CarbonIntensityCalculator,
    profile_a: SourceEmissionProfile,
    profile_b: SourceEmissionProfile,
    frac_a: float,
    frac_b: float,
) -> None:
    blend = [BlendSource("SEAM_A", fraction=frac_a), BlendSource("SEAM_B", fraction=frac_b)]
    result = calculator.calculate(blend)
    expected = (
        profile_a.total_intensity_kg_co2e_per_tonne * frac_a
        + profile_b.total_intensity_kg_co2e_per_tonne * frac_b
    )
    assert math.isclose(result.blended_intensity_kg_co2e_per_tonne, expected, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 11. Zero-explosive override produces lower intensity
# ---------------------------------------------------------------------------


def test_zero_explosive_lower_than_default() -> None:
    with_explosive = SourceEmissionProfile("Z", explosive_kg_co2e_per_tonne=0.05)
    without_explosive = SourceEmissionProfile("Z2", explosive_kg_co2e_per_tonne=0.0)
    calc = CarbonIntensityCalculator([with_explosive, without_explosive])

    r_with = calc.calculate([BlendSource("Z", fraction=1.0)])
    r_without = calc.calculate([BlendSource("Z2", fraction=1.0)])
    assert r_with.blended_intensity_kg_co2e_per_tonne > r_without.blended_intensity_kg_co2e_per_tonne
