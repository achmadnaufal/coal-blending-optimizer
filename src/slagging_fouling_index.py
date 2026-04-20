"""
Slagging and Fouling Index Calculator for coal blends.

Computes ash-chemistry-based slagging (R_s) and fouling (R_f) indices used
by utility boiler designers to predict ash deposition severity on furnace
walls (slagging) and convective pass tube banks (fouling). Also supports
base/acid ratio (B/A), silica ratio (S/A), iron/calcium ratio, and the
T250 viscosity proxy commonly used in low-rank coal evaluation.

Formulas:
    B/A     = (Fe2O3 + CaO + MgO + Na2O + K2O) / (SiO2 + Al2O3 + TiO2)
    R_s     = (B/A) * S_dry                        (Attig & Duzy 1969)
    R_f     = (B/A) * (Na2O + K2O)                 (Bryers 1996, bituminous)
    R_f_lig = Na2O (lignitic, Na-dominant alkali)  (Bryers 1996)
    S/A     = SiO2 / (SiO2 + Fe2O3 + CaO + MgO)    (silica ratio)
    T250    = 107*log10(SiO2) - ... (empirical)    (see viscosity helper)

Classification bands (Bryers 1996, Table 2.5):
    Slagging R_s
        R_s < 0.6      : low
        0.6 <= R_s < 2.0 : medium
        2.0 <= R_s < 2.6 : high
        R_s >= 2.6     : severe
    Fouling R_f
        R_f < 0.2      : low
        0.2 <= R_f < 0.5 : medium
        0.5 <= R_f < 1.0 : high
        R_f >= 1.0     : severe

All ash oxides are expressed as weight-% of the ash (dry ash basis). Sulfur
is supplied on a dry coal basis (S_dry, wt-%). All fractions of a blend
must sum to 1.0 (+/- 0.001).

References:
    Attig, R.C. & Duzy, A.F. (1969) "Coal Ash Deposition Studies and
        Application to Boiler Design", Proc. American Power Conf.
    Bryers, R.W. (1996) "Fireside Slagging, Fouling, and High-Temperature
        Corrosion of Heat-Transfer Surfaces due to Impurities in
        Steam-Raising Fuels", Prog. Energy Combust. Sci. 22, pp. 29-120.
    Couch, G. (1994) "Understanding Slagging and Fouling During PF
        Combustion", IEA Coal Research IEACR/72.
    Winegartner, E.C. (1974) "Coal Fouling and Slagging Parameters",
        ASME Research Committee on Corrosion and Deposits.

Author: github.com/achmadnaufal
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Mapping, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Required ash-oxide keys (dry ash basis, wt-%).
REQUIRED_OXIDES: tuple[str, ...] = (
    "SiO2",
    "Al2O3",
    "Fe2O3",
    "CaO",
    "MgO",
    "Na2O",
    "K2O",
    "TiO2",
)

#: Acidic oxides (denominator of the base/acid ratio).
ACID_OXIDES: tuple[str, ...] = ("SiO2", "Al2O3", "TiO2")

#: Basic oxides (numerator of the base/acid ratio).
BASIC_OXIDES: tuple[str, ...] = ("Fe2O3", "CaO", "MgO", "Na2O", "K2O")

#: Alkali oxides used in the bituminous fouling index.
ALKALI_OXIDES: tuple[str, ...] = ("Na2O", "K2O")

#: Maximum permitted oxide sum (wt-%). Ash analyses rarely exceed 102 %
#: after rounding; values above suggest a unit error.
MAX_OXIDE_SUM_PCT: float = 105.0

#: Minimum permitted oxide sum before flagging an incomplete analysis.
MIN_OXIDE_SUM_PCT: float = 70.0

#: Slagging classification thresholds (R_s upper bounds by class).
SLAGGING_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("low", 0.6),
    ("medium", 2.0),
    ("high", 2.6),
    ("severe", math.inf),
)

#: Fouling classification thresholds (R_f upper bounds by class).
FOULING_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("low", 0.2),
    ("medium", 0.5),
    ("high", 1.0),
    ("severe", math.inf),
)

#: Tolerance on blend-fraction sum.
FRACTION_TOLERANCE: float = 1e-3


class CoalRank(str, Enum):
    """Coal rank classes used to select the fouling formula variant.

    - ``BITUMINOUS`` uses ``R_f = (B/A) * (Na2O + K2O)`` (Bryers 1996).
    - ``LIGNITIC`` uses ``R_f = Na2O`` alone, because Na is the dominant
      alkali vector in low-rank ash and K2O is typically very low.
    """

    BITUMINOUS = "bituminous"
    LIGNITIC = "lignitic"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AshComposition:
    """Dry-ash oxide composition for a single coal source.

    All oxides are in weight-% of the ash. The sum should fall in
    [``MIN_OXIDE_SUM_PCT``, ``MAX_OXIDE_SUM_PCT``]; analyses summing to
    exactly 100 % are the nominal case.

    Attributes:
        source_id: Unique identifier for the source / stockpile.
        sio2: SiO2 wt-%.
        al2o3: Al2O3 wt-%.
        fe2o3: Fe2O3 wt-%.
        cao: CaO wt-%.
        mgo: MgO wt-%.
        na2o: Na2O wt-%.
        k2o: K2O wt-%.
        tio2: TiO2 wt-%.
        sulfur_dry_pct: Total sulfur on a dry coal basis (wt-%).
        rank: Coal rank for fouling-formula selection. Defaults to
            ``CoalRank.BITUMINOUS``.
    """

    source_id: str
    sio2: float
    al2o3: float
    fe2o3: float
    cao: float
    mgo: float
    na2o: float
    k2o: float
    tio2: float
    sulfur_dry_pct: float
    rank: CoalRank = CoalRank.BITUMINOUS

    def __post_init__(self) -> None:
        """Validate oxide fields and sulfur.

        Raises:
            ValueError: If any oxide is negative, sulfur is negative,
                source_id is blank, or the oxide sum is outside
                [``MIN_OXIDE_SUM_PCT``, ``MAX_OXIDE_SUM_PCT``].
        """
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string.")

        for name, value in self.as_oxide_map().items():
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}.")
            if value > 100:
                raise ValueError(f"{name} must be <= 100 wt-%, got {value}.")

        if self.sulfur_dry_pct < 0:
            raise ValueError(
                f"sulfur_dry_pct must be >= 0, got {self.sulfur_dry_pct}."
            )
        if self.sulfur_dry_pct > 15:
            raise ValueError(
                f"sulfur_dry_pct {self.sulfur_dry_pct} exceeds physical max 15 wt-%."
            )

        total = self.oxide_sum_pct
        if total < MIN_OXIDE_SUM_PCT:
            raise ValueError(
                f"Oxide sum {total:.2f} wt-% below minimum {MIN_OXIDE_SUM_PCT}; "
                "analysis appears incomplete."
            )
        if total > MAX_OXIDE_SUM_PCT:
            raise ValueError(
                f"Oxide sum {total:.2f} wt-% exceeds maximum {MAX_OXIDE_SUM_PCT}; "
                "check for unit errors."
            )

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def as_oxide_map(self) -> Dict[str, float]:
        """Return a new dict mapping oxide name -> wt-% (immutable copy)."""
        return {
            "SiO2": self.sio2,
            "Al2O3": self.al2o3,
            "Fe2O3": self.fe2o3,
            "CaO": self.cao,
            "MgO": self.mgo,
            "Na2O": self.na2o,
            "K2O": self.k2o,
            "TiO2": self.tio2,
        }

    @property
    def oxide_sum_pct(self) -> float:
        """Sum of all eight reported oxides (wt-%)."""
        return (
            self.sio2
            + self.al2o3
            + self.fe2o3
            + self.cao
            + self.mgo
            + self.na2o
            + self.k2o
            + self.tio2
        )

    @property
    def basic_sum(self) -> float:
        """Sum of basic oxides Fe2O3 + CaO + MgO + Na2O + K2O (wt-%)."""
        return self.fe2o3 + self.cao + self.mgo + self.na2o + self.k2o

    @property
    def acid_sum(self) -> float:
        """Sum of acidic oxides SiO2 + Al2O3 + TiO2 (wt-%)."""
        return self.sio2 + self.al2o3 + self.tio2

    @property
    def base_acid_ratio(self) -> float:
        """Base-to-acid ratio B/A.

        Returns:
            ``basic_sum / acid_sum``. Raises ZeroDivisionError implicitly
            only if all three acidic oxides are zero, which the constructor
            already guards against via the oxide-sum lower bound.
        """
        if self.acid_sum == 0:
            raise ValueError(
                f"acid oxide sum is zero for '{self.source_id}'; cannot compute B/A."
            )
        return self.basic_sum / self.acid_sum

    @property
    def silica_ratio(self) -> float:
        """Silica ratio S/A = SiO2 / (SiO2 + Fe2O3 + CaO + MgO).

        Higher S/A correlates with higher ash fusion temperature and
        lower slagging propensity.
        """
        denom = self.sio2 + self.fe2o3 + self.cao + self.mgo
        if denom == 0:
            raise ValueError(
                f"SiO2+Fe2O3+CaO+MgO sum is zero for '{self.source_id}'."
            )
        return self.sio2 / denom

    @property
    def iron_calcium_ratio(self) -> float:
        """Fe2O3 / CaO ratio; values near 1 correlate with lowest ash
        fluidisation temperature and the highest slagging risk.
        """
        if self.cao == 0:
            return math.inf
        return self.fe2o3 / self.cao

    @property
    def slagging_index(self) -> float:
        """Attig & Duzy (1969) slagging index R_s = (B/A) * S_dry."""
        return self.base_acid_ratio * self.sulfur_dry_pct

    @property
    def fouling_index(self) -> float:
        """Bryers (1996) fouling index R_f.

        - Bituminous: ``R_f = (B/A) * (Na2O + K2O)``
        - Lignitic:   ``R_f = Na2O`` (sodium-dominant alkali)
        """
        if self.rank is CoalRank.LIGNITIC:
            return self.na2o
        return self.base_acid_ratio * (self.na2o + self.k2o)


@dataclass(frozen=True)
class BlendFraction:
    """A single entry of a blend recipe."""

    source_id: str
    fraction: float

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string.")
        if not (0 < self.fraction <= 1.0):
            raise ValueError(
                f"fraction must be in (0, 1], got {self.fraction} "
                f"for '{self.source_id}'."
            )


@dataclass(frozen=True)
class SlaggingFoulingReport:
    """Output of ``SlaggingFoulingIndexCalculator.evaluate``.

    Attributes:
        base_acid_ratio: Blend-weighted B/A (oxides averaged first, then
            ratio computed).
        silica_ratio: Blend-weighted silica ratio S/A.
        iron_calcium_ratio: Blend-weighted Fe2O3/CaO ratio.
        slagging_index: R_s for the blend.
        fouling_index: R_f for the blend.
        slagging_class: One of ``low``, ``medium``, ``high``, ``severe``.
        fouling_class: One of ``low``, ``medium``, ``high``, ``severe``.
        blended_oxides: Weighted-average oxide composition (wt-%).
        blended_sulfur_dry_pct: Weighted-average dry sulfur (wt-%).
        source_indices: Per-source ``{source_id: (R_s, R_f)}`` pairs.
    """

    base_acid_ratio: float
    silica_ratio: float
    iron_calcium_ratio: float
    slagging_index: float
    fouling_index: float
    slagging_class: str
    fouling_class: str
    blended_oxides: Dict[str, float]
    blended_sulfur_dry_pct: float
    source_indices: Dict[str, tuple[float, float]]


# ---------------------------------------------------------------------------
# Classifier helpers
# ---------------------------------------------------------------------------


def classify_slagging(r_s: float) -> str:
    """Return the slagging severity class for a given R_s.

    Args:
        r_s: Slagging index (dimensionless). Must be >= 0.

    Returns:
        One of ``"low"``, ``"medium"``, ``"high"``, ``"severe"``.

    Raises:
        ValueError: If ``r_s`` is negative or NaN.
    """
    if math.isnan(r_s):
        raise ValueError("slagging index is NaN.")
    if r_s < 0:
        raise ValueError(f"slagging index must be >= 0, got {r_s}.")
    for label, upper in SLAGGING_THRESHOLDS:
        if r_s < upper:
            return label
    return "severe"  # pragma: no cover — inf threshold guarantees a match


def classify_fouling(r_f: float) -> str:
    """Return the fouling severity class for a given R_f.

    Args:
        r_f: Fouling index (dimensionless). Must be >= 0.

    Returns:
        One of ``"low"``, ``"medium"``, ``"high"``, ``"severe"``.

    Raises:
        ValueError: If ``r_f`` is negative or NaN.
    """
    if math.isnan(r_f):
        raise ValueError("fouling index is NaN.")
    if r_f < 0:
        raise ValueError(f"fouling index must be >= 0, got {r_f}.")
    for label, upper in FOULING_THRESHOLDS:
        if r_f < upper:
            return label
    return "severe"  # pragma: no cover


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class SlaggingFoulingIndexCalculator:
    """Computes ash-deposition indices for individual coals and blends.

    The calculator is constructed from a collection of ``AshComposition``
    profiles (one per candidate source). Use ``evaluate_source`` for a
    single coal and ``evaluate`` for a blend recipe. All operations are
    pure: the input profiles and any supplied sequences are never mutated.

    Example:
        >>> ashes = [
        ...     AshComposition("PIT_A", sio2=52, al2o3=25, fe2o3=8, cao=4,
        ...                    mgo=2, na2o=0.6, k2o=1.2, tio2=1.0,
        ...                    sulfur_dry_pct=0.7),
        ...     AshComposition("PIT_B", sio2=45, al2o3=22, fe2o3=15, cao=8,
        ...                    mgo=3, na2o=0.4, k2o=1.0, tio2=0.9,
        ...                    sulfur_dry_pct=1.8),
        ... ]
        >>> calc = SlaggingFoulingIndexCalculator(ashes)
        >>> report = calc.evaluate([
        ...     BlendFraction("PIT_A", 0.6),
        ...     BlendFraction("PIT_B", 0.4),
        ... ])
        >>> report.slagging_class in {"low", "medium", "high", "severe"}
        True
    """

    def __init__(self, ash_profiles: Sequence[AshComposition]) -> None:
        """Register ash compositions for later evaluation.

        Args:
            ash_profiles: Non-empty sequence with unique ``source_id``s.

        Raises:
            ValueError: On empty input or duplicate ``source_id``.
        """
        if not ash_profiles:
            raise ValueError("ash_profiles must not be empty.")

        seen: set[str] = set()
        for profile in ash_profiles:
            if profile.source_id in seen:
                raise ValueError(
                    f"Duplicate source_id '{profile.source_id}' in ash_profiles."
                )
            seen.add(profile.source_id)

        # Immutable lookup table keyed by source_id.
        self._profiles: Dict[str, AshComposition] = {
            p.source_id: p for p in ash_profiles
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def registered_sources(self) -> tuple[str, ...]:
        """Return a tuple of registered ``source_id`` values (immutable)."""
        return tuple(self._profiles.keys())

    def evaluate_source(self, source_id: str) -> SlaggingFoulingReport:
        """Evaluate slagging / fouling indices for a single registered source.

        Args:
            source_id: ID of a registered ``AshComposition``.

        Returns:
            A ``SlaggingFoulingReport`` with the single-source ratios and
            classes.

        Raises:
            KeyError: If ``source_id`` is not registered.
        """
        if source_id not in self._profiles:
            raise KeyError(f"source_id '{source_id}' is not registered.")
        profile = self._profiles[source_id]

        r_s = profile.slagging_index
        r_f = profile.fouling_index
        return SlaggingFoulingReport(
            base_acid_ratio=profile.base_acid_ratio,
            silica_ratio=profile.silica_ratio,
            iron_calcium_ratio=profile.iron_calcium_ratio,
            slagging_index=r_s,
            fouling_index=r_f,
            slagging_class=classify_slagging(r_s),
            fouling_class=classify_fouling(r_f),
            blended_oxides=profile.as_oxide_map(),
            blended_sulfur_dry_pct=profile.sulfur_dry_pct,
            source_indices={source_id: (r_s, r_f)},
        )

    def evaluate(
        self,
        blend: Sequence[BlendFraction],
    ) -> SlaggingFoulingReport:
        """Compute blend-weighted slagging and fouling indices.

        The oxide composition is averaged on a mass-weighted basis (all
        oxides + sulfur), then the ratios and indices are computed from
        the blended composition. This is the correct approach for additive
        oxide species; averaging the ratios themselves is a well-known
        error in slagging analysis (Couch 1994).

        Args:
            blend: Non-empty sequence of ``BlendFraction``. Fractions must
                sum to 1.0 +/- ``FRACTION_TOLERANCE``.

        Returns:
            A ``SlaggingFoulingReport`` with blended ratios, indices, and
            per-source indices for traceability.

        Raises:
            ValueError: On empty blend, fractions not summing to 1.0, or
                unregistered ``source_id``.
        """
        self._validate_blend(blend)

        # Mass-weighted oxide average (new dict, not mutated in place).
        blended: Dict[str, float] = {name: 0.0 for name in REQUIRED_OXIDES}
        blended_sulfur: float = 0.0
        source_indices: Dict[str, tuple[float, float]] = {}
        rank = self._profiles[blend[0].source_id].rank

        for entry in blend:
            profile = self._profiles[entry.source_id]
            oxides = profile.as_oxide_map()
            blended = {
                name: blended[name] + oxides[name] * entry.fraction
                for name in REQUIRED_OXIDES
            }
            blended_sulfur += profile.sulfur_dry_pct * entry.fraction
            source_indices = {
                **source_indices,
                entry.source_id: (profile.slagging_index, profile.fouling_index),
            }

        base_acid = _safe_base_acid(blended)
        silica = _safe_silica(blended)
        fe_ca = _safe_fe_ca(blended)
        r_s = base_acid * blended_sulfur
        if rank is CoalRank.LIGNITIC:
            r_f = blended["Na2O"]
        else:
            r_f = base_acid * (blended["Na2O"] + blended["K2O"])

        return SlaggingFoulingReport(
            base_acid_ratio=base_acid,
            silica_ratio=silica,
            iron_calcium_ratio=fe_ca,
            slagging_index=r_s,
            fouling_index=r_f,
            slagging_class=classify_slagging(r_s),
            fouling_class=classify_fouling(r_f),
            blended_oxides=blended,
            blended_sulfur_dry_pct=blended_sulfur,
            source_indices=source_indices,
        )

    def compare_sources(self) -> Dict[str, SlaggingFoulingReport]:
        """Evaluate every registered source individually.

        Returns:
            A new dict mapping ``source_id`` to ``SlaggingFoulingReport``.
            Useful for screening which coals are high-risk before blending.
        """
        return {sid: self.evaluate_source(sid) for sid in self._profiles}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_blend(self, blend: Sequence[BlendFraction]) -> None:
        if not blend:
            raise ValueError("blend must contain at least one BlendFraction.")

        fraction_sum = sum(b.fraction for b in blend)
        if abs(fraction_sum - 1.0) > FRACTION_TOLERANCE:
            raise ValueError(
                f"BlendFraction fractions must sum to 1.0 "
                f"(+/- {FRACTION_TOLERANCE}), got {fraction_sum:.6f}."
            )

        seen: set[str] = set()
        for entry in blend:
            if entry.source_id in seen:
                raise ValueError(
                    f"Duplicate source_id '{entry.source_id}' in blend recipe."
                )
            seen.add(entry.source_id)
            if entry.source_id not in self._profiles:
                raise ValueError(
                    f"source_id '{entry.source_id}' has no registered "
                    "AshComposition."
                )


# ---------------------------------------------------------------------------
# Internal pure helpers (work on plain dicts so tests can reuse them)
# ---------------------------------------------------------------------------


def _safe_base_acid(oxides: Mapping[str, float]) -> float:
    acid = sum(oxides[o] for o in ACID_OXIDES)
    if acid == 0:
        raise ValueError("acid oxide sum is zero; cannot compute B/A.")
    basic = sum(oxides[o] for o in BASIC_OXIDES)
    return basic / acid


def _safe_silica(oxides: Mapping[str, float]) -> float:
    denom = oxides["SiO2"] + oxides["Fe2O3"] + oxides["CaO"] + oxides["MgO"]
    if denom == 0:
        raise ValueError(
            "SiO2+Fe2O3+CaO+MgO sum is zero; cannot compute silica ratio."
        )
    return oxides["SiO2"] / denom


def _safe_fe_ca(oxides: Mapping[str, float]) -> float:
    if oxides["CaO"] == 0:
        return math.inf
    return oxides["Fe2O3"] / oxides["CaO"]
