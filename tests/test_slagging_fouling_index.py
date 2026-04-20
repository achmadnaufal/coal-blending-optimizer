"""
Tests for src/slagging_fouling_index.py.

Coverage:
  - AshComposition property correctness: B/A, S/A, Fe/Ca, R_s, R_f
  - Bituminous vs lignitic fouling formula switch
  - Mass-weighted blend arithmetic (oxides averaged first, then ratios)
  - Classification thresholds (low / medium / high / severe)
  - Determinism and frozen-dataclass immutability
  - Single-source evaluate and compare_sources helper
  - Boundary validation: empty, bad fractions, unregistered source,
    negative oxides, oxides > 100, oxide-sum out of range, negative sulfur,
    duplicate source IDs, duplicate blend entries, zero acid oxides
  - Parametrized thresholds for classify_slagging / classify_fouling
"""

from __future__ import annotations

import math
import pytest

from src.slagging_fouling_index import (
    ACID_OXIDES,
    ALKALI_OXIDES,
    BASIC_OXIDES,
    FRACTION_TOLERANCE,
    REQUIRED_OXIDES,
    AshComposition,
    BlendFraction,
    CoalRank,
    SlaggingFoulingIndexCalculator,
    SlaggingFoulingReport,
    classify_fouling,
    classify_slagging,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def low_risk_ash() -> AshComposition:
    # High-silica Australian bituminous: low slagging, low fouling.
    return AshComposition(
        source_id="AUS_HV",
        sio2=58.0,
        al2o3=28.0,
        fe2o3=5.0,
        cao=2.0,
        mgo=1.5,
        na2o=0.3,
        k2o=1.2,
        tio2=1.5,
        sulfur_dry_pct=0.4,
    )


@pytest.fixture()
def high_risk_ash() -> AshComposition:
    # Iron- and calcium-rich Indonesian lignite: severe slagging.
    return AshComposition(
        source_id="IDN_LV",
        sio2=38.0,
        al2o3=15.0,
        fe2o3=20.0,
        cao=12.0,
        mgo=4.0,
        na2o=2.5,
        k2o=1.5,
        tio2=0.8,
        sulfur_dry_pct=3.5,
    )


@pytest.fixture()
def medium_risk_ash() -> AshComposition:
    return AshComposition(
        source_id="MED",
        sio2=50.0,
        al2o3=23.0,
        fe2o3=10.0,
        cao=6.0,
        mgo=2.5,
        na2o=1.0,
        k2o=1.2,
        tio2=1.0,
        sulfur_dry_pct=1.2,
    )


@pytest.fixture()
def calculator(
    low_risk_ash: AshComposition,
    high_risk_ash: AshComposition,
    medium_risk_ash: AshComposition,
) -> SlaggingFoulingIndexCalculator:
    return SlaggingFoulingIndexCalculator(
        [low_risk_ash, high_risk_ash, medium_risk_ash]
    )


# ---------------------------------------------------------------------------
# 1. AshComposition property correctness
# ---------------------------------------------------------------------------


def test_oxide_sum_property(low_risk_ash: AshComposition) -> None:
    assert math.isclose(low_risk_ash.oxide_sum_pct, 97.5, rel_tol=1e-9)


def test_basic_sum_matches_formula(low_risk_ash: AshComposition) -> None:
    expected = 5.0 + 2.0 + 1.5 + 0.3 + 1.2
    assert math.isclose(low_risk_ash.basic_sum, expected, rel_tol=1e-9)


def test_acid_sum_matches_formula(low_risk_ash: AshComposition) -> None:
    expected = 58.0 + 28.0 + 1.5
    assert math.isclose(low_risk_ash.acid_sum, expected, rel_tol=1e-9)


def test_base_acid_ratio(low_risk_ash: AshComposition) -> None:
    expected = (5.0 + 2.0 + 1.5 + 0.3 + 1.2) / (58.0 + 28.0 + 1.5)
    assert math.isclose(low_risk_ash.base_acid_ratio, expected, rel_tol=1e-9)


def test_silica_ratio(low_risk_ash: AshComposition) -> None:
    expected = 58.0 / (58.0 + 5.0 + 2.0 + 1.5)
    assert math.isclose(low_risk_ash.silica_ratio, expected, rel_tol=1e-9)


def test_slagging_index_attig_duzy(low_risk_ash: AshComposition) -> None:
    expected = low_risk_ash.base_acid_ratio * low_risk_ash.sulfur_dry_pct
    assert math.isclose(low_risk_ash.slagging_index, expected, rel_tol=1e-9)


def test_fouling_index_bituminous(low_risk_ash: AshComposition) -> None:
    expected = low_risk_ash.base_acid_ratio * (
        low_risk_ash.na2o + low_risk_ash.k2o
    )
    assert math.isclose(low_risk_ash.fouling_index, expected, rel_tol=1e-9)


def test_fouling_index_lignitic_uses_na2o_only() -> None:
    lig = AshComposition(
        source_id="LIG",
        sio2=40,
        al2o3=18,
        fe2o3=12,
        cao=14,
        mgo=4,
        na2o=2.0,
        k2o=0.5,
        tio2=1.0,
        sulfur_dry_pct=0.9,
        rank=CoalRank.LIGNITIC,
    )
    assert math.isclose(lig.fouling_index, 2.0, rel_tol=1e-9)


def test_iron_calcium_ratio(low_risk_ash: AshComposition) -> None:
    assert math.isclose(low_risk_ash.iron_calcium_ratio, 5.0 / 2.0, rel_tol=1e-9)


def test_iron_calcium_infinite_when_zero_cao() -> None:
    ash = AshComposition(
        source_id="NOCA",
        sio2=60,
        al2o3=28,
        fe2o3=5,
        cao=0,
        mgo=2,
        na2o=0.3,
        k2o=1.2,
        tio2=1.5,
        sulfur_dry_pct=0.4,
    )
    assert ash.iron_calcium_ratio == math.inf


# ---------------------------------------------------------------------------
# 2. Classification thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "r_s, expected",
    [
        (0.0, "low"),
        (0.5, "low"),
        (0.6, "medium"),
        (1.5, "medium"),
        (2.0, "high"),
        (2.5, "high"),
        (2.6, "severe"),
        (10.0, "severe"),
    ],
)
def test_classify_slagging_bands(r_s: float, expected: str) -> None:
    assert classify_slagging(r_s) == expected


@pytest.mark.parametrize(
    "r_f, expected",
    [
        (0.0, "low"),
        (0.19, "low"),
        (0.2, "medium"),
        (0.4, "medium"),
        (0.5, "high"),
        (0.9, "high"),
        (1.0, "severe"),
        (5.0, "severe"),
    ],
)
def test_classify_fouling_bands(r_f: float, expected: str) -> None:
    assert classify_fouling(r_f) == expected


def test_classify_slagging_rejects_negative() -> None:
    with pytest.raises(ValueError, match="slagging"):
        classify_slagging(-0.1)


def test_classify_fouling_rejects_negative() -> None:
    with pytest.raises(ValueError, match="fouling"):
        classify_fouling(-0.01)


def test_classify_slagging_rejects_nan() -> None:
    with pytest.raises(ValueError, match="NaN"):
        classify_slagging(float("nan"))


def test_classify_fouling_rejects_nan() -> None:
    with pytest.raises(ValueError, match="NaN"):
        classify_fouling(float("nan"))


# ---------------------------------------------------------------------------
# 3. Single-source evaluate
# ---------------------------------------------------------------------------


def test_evaluate_source_matches_profile(
    calculator: SlaggingFoulingIndexCalculator,
    low_risk_ash: AshComposition,
) -> None:
    report = calculator.evaluate_source("AUS_HV")
    assert math.isclose(
        report.slagging_index, low_risk_ash.slagging_index, rel_tol=1e-9
    )
    assert math.isclose(
        report.fouling_index, low_risk_ash.fouling_index, rel_tol=1e-9
    )
    assert report.slagging_class == classify_slagging(low_risk_ash.slagging_index)
    assert report.fouling_class == classify_fouling(low_risk_ash.fouling_index)


def test_evaluate_source_unknown_raises(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    with pytest.raises(KeyError, match="GHOST"):
        calculator.evaluate_source("GHOST")


def test_low_risk_classifies_low(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    report = calculator.evaluate_source("AUS_HV")
    assert report.slagging_class == "low"
    assert report.fouling_class == "low"


def test_high_risk_classifies_severe(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    report = calculator.evaluate_source("IDN_LV")
    assert report.slagging_class == "severe"


# ---------------------------------------------------------------------------
# 4. Blend evaluation — weighted arithmetic
# ---------------------------------------------------------------------------


def test_blend_weighted_oxides(
    calculator: SlaggingFoulingIndexCalculator,
    low_risk_ash: AshComposition,
    high_risk_ash: AshComposition,
) -> None:
    report = calculator.evaluate(
        [
            BlendFraction("AUS_HV", 0.7),
            BlendFraction("IDN_LV", 0.3),
        ]
    )
    expected_sio2 = low_risk_ash.sio2 * 0.7 + high_risk_ash.sio2 * 0.3
    assert math.isclose(report.blended_oxides["SiO2"], expected_sio2, rel_tol=1e-9)


def test_blend_weighted_sulfur(
    calculator: SlaggingFoulingIndexCalculator,
    low_risk_ash: AshComposition,
    high_risk_ash: AshComposition,
) -> None:
    report = calculator.evaluate(
        [
            BlendFraction("AUS_HV", 0.5),
            BlendFraction("IDN_LV", 0.5),
        ]
    )
    expected = (low_risk_ash.sulfur_dry_pct + high_risk_ash.sulfur_dry_pct) / 2
    assert math.isclose(
        report.blended_sulfur_dry_pct, expected, rel_tol=1e-9
    )


def test_blend_ba_consistent_with_oxide_sums(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    report = calculator.evaluate(
        [BlendFraction("AUS_HV", 0.6), BlendFraction("IDN_LV", 0.4)]
    )
    basic = sum(report.blended_oxides[o] for o in BASIC_OXIDES)
    acid = sum(report.blended_oxides[o] for o in ACID_OXIDES)
    assert math.isclose(report.base_acid_ratio, basic / acid, rel_tol=1e-9)


def test_blend_100pct_single_source_equivalent(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    standalone = calculator.evaluate_source("AUS_HV")
    blend = calculator.evaluate([BlendFraction("AUS_HV", 1.0)])
    assert math.isclose(
        standalone.slagging_index, blend.slagging_index, rel_tol=1e-9
    )
    assert math.isclose(
        standalone.fouling_index, blend.fouling_index, rel_tol=1e-9
    )


def test_blend_reduces_slagging_for_dilution(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    pure_high = calculator.evaluate([BlendFraction("IDN_LV", 1.0)])
    diluted = calculator.evaluate(
        [BlendFraction("AUS_HV", 0.8), BlendFraction("IDN_LV", 0.2)]
    )
    assert diluted.slagging_index < pure_high.slagging_index


def test_blend_source_indices_populated(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    report = calculator.evaluate(
        [BlendFraction("AUS_HV", 0.5), BlendFraction("MED", 0.5)]
    )
    assert set(report.source_indices.keys()) == {"AUS_HV", "MED"}


# ---------------------------------------------------------------------------
# 5. Determinism and immutability
# ---------------------------------------------------------------------------


def test_evaluate_deterministic(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    blend = [BlendFraction("AUS_HV", 0.6), BlendFraction("IDN_LV", 0.4)]
    r1 = calculator.evaluate(blend)
    r2 = calculator.evaluate(blend)
    assert r1.slagging_index == r2.slagging_index
    assert r1.fouling_index == r2.fouling_index


def test_report_is_frozen(calculator: SlaggingFoulingIndexCalculator) -> None:
    report = calculator.evaluate_source("AUS_HV")
    with pytest.raises((AttributeError, TypeError)):
        report.slagging_index = 0.0  # type: ignore[misc]


def test_ash_composition_is_frozen(low_risk_ash: AshComposition) -> None:
    with pytest.raises((AttributeError, TypeError)):
        low_risk_ash.sio2 = 0.0  # type: ignore[misc]


def test_as_oxide_map_returns_new_dict_each_call(
    low_risk_ash: AshComposition,
) -> None:
    m1 = low_risk_ash.as_oxide_map()
    m2 = low_risk_ash.as_oxide_map()
    assert m1 is not m2
    m1["SiO2"] = -999.0
    assert low_risk_ash.sio2 == 58.0


def test_registered_sources_is_tuple(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    sources = calculator.registered_sources
    assert isinstance(sources, tuple)
    assert set(sources) == {"AUS_HV", "IDN_LV", "MED"}


# ---------------------------------------------------------------------------
# 6. compare_sources helper
# ---------------------------------------------------------------------------


def test_compare_sources_returns_all_registered(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    reports = calculator.compare_sources()
    assert set(reports.keys()) == {"AUS_HV", "IDN_LV", "MED"}
    for report in reports.values():
        assert isinstance(report, SlaggingFoulingReport)


# ---------------------------------------------------------------------------
# 7. Validation — AshComposition construction
# ---------------------------------------------------------------------------


def test_negative_oxide_raises() -> None:
    with pytest.raises(ValueError, match="SiO2"):
        AshComposition(
            source_id="X",
            sio2=-1,
            al2o3=28,
            fe2o3=5,
            cao=2,
            mgo=1.5,
            na2o=0.3,
            k2o=1.2,
            tio2=1.5,
            sulfur_dry_pct=0.4,
        )


def test_oxide_over_100_raises() -> None:
    with pytest.raises(ValueError, match="SiO2"):
        AshComposition(
            source_id="X",
            sio2=101,
            al2o3=0,
            fe2o3=0,
            cao=0,
            mgo=0,
            na2o=0,
            k2o=0,
            tio2=0,
            sulfur_dry_pct=0.4,
        )


def test_oxide_sum_too_low_raises() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        AshComposition(
            source_id="X",
            sio2=10,
            al2o3=10,
            fe2o3=10,
            cao=10,
            mgo=10,
            na2o=5,
            k2o=5,
            tio2=5,
            sulfur_dry_pct=0.4,
        )


def test_oxide_sum_too_high_raises() -> None:
    with pytest.raises(ValueError, match="maximum"):
        AshComposition(
            source_id="X",
            sio2=60,
            al2o3=30,
            fe2o3=10,
            cao=5,
            mgo=3,
            na2o=1,
            k2o=1,
            tio2=1,
            sulfur_dry_pct=0.4,
        )


def test_negative_sulfur_raises() -> None:
    with pytest.raises(ValueError, match="sulfur_dry_pct"):
        AshComposition(
            source_id="X",
            sio2=58,
            al2o3=28,
            fe2o3=5,
            cao=2,
            mgo=1.5,
            na2o=0.3,
            k2o=1.2,
            tio2=1.5,
            sulfur_dry_pct=-0.1,
        )


def test_sulfur_over_limit_raises() -> None:
    with pytest.raises(ValueError, match="physical max"):
        AshComposition(
            source_id="X",
            sio2=58,
            al2o3=28,
            fe2o3=5,
            cao=2,
            mgo=1.5,
            na2o=0.3,
            k2o=1.2,
            tio2=1.5,
            sulfur_dry_pct=20.0,
        )


def test_blank_source_id_raises() -> None:
    with pytest.raises(ValueError, match="source_id"):
        AshComposition(
            source_id="   ",
            sio2=58,
            al2o3=28,
            fe2o3=5,
            cao=2,
            mgo=1.5,
            na2o=0.3,
            k2o=1.2,
            tio2=1.5,
            sulfur_dry_pct=0.4,
        )


# ---------------------------------------------------------------------------
# 8. Validation — calculator / blend
# ---------------------------------------------------------------------------


def test_empty_profiles_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        SlaggingFoulingIndexCalculator([])


def test_duplicate_profile_raises(low_risk_ash: AshComposition) -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        SlaggingFoulingIndexCalculator([low_risk_ash, low_risk_ash])


def test_empty_blend_raises(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    with pytest.raises(ValueError, match="at least one"):
        calculator.evaluate([])


def test_blend_fractions_not_summing_raises(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        calculator.evaluate(
            [BlendFraction("AUS_HV", 0.3), BlendFraction("IDN_LV", 0.3)]
        )


def test_blend_unregistered_source_raises(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    with pytest.raises(ValueError, match="GHOST"):
        calculator.evaluate([BlendFraction("GHOST", 1.0)])


def test_blend_duplicate_source_raises(
    calculator: SlaggingFoulingIndexCalculator,
) -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        calculator.evaluate(
            [BlendFraction("AUS_HV", 0.5), BlendFraction("AUS_HV", 0.5)]
        )


def test_blend_fraction_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="fraction"):
        BlendFraction("X", 1.5)


def test_blend_fraction_zero_raises() -> None:
    with pytest.raises(ValueError, match="fraction"):
        BlendFraction("X", 0.0)


def test_blend_fraction_blank_source_raises() -> None:
    with pytest.raises(ValueError, match="source_id"):
        BlendFraction("", 0.5)


# ---------------------------------------------------------------------------
# 9. Module-level constants sanity
# ---------------------------------------------------------------------------


def test_required_oxides_has_eight_species() -> None:
    assert len(REQUIRED_OXIDES) == 8
    assert set(ACID_OXIDES) | set(BASIC_OXIDES) == set(REQUIRED_OXIDES)


def test_alkali_is_subset_of_basic() -> None:
    assert set(ALKALI_OXIDES).issubset(set(BASIC_OXIDES))


def test_fraction_tolerance_is_small() -> None:
    assert 0 < FRACTION_TOLERANCE < 0.01
