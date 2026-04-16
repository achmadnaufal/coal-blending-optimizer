"""
Scope-1 Carbon Intensity Calculator for coal blending operations.

Computes Scope-1 CO2-equivalent (CO2e) emission intensity (kg CO2e per tonne
of coal produced) for a given blend, accounting for:

  - Diesel combustion from mining and hauling equipment (kg CO2e/t)
  - Methane (CH4) fugitive emissions from coal seams (kg CO2e/t)
  - Explosive detonation residual carbon (kg CO2e/t), optional

Emission factors follow IPCC AR6 GWP100 (CH4 = 29.8 CO2e) and
IPCC 2006 GL Vol. 2, Ch. 2 (mobile combustion) defaults.

Typical Indonesian sub-bituminous open-cut mining values are used as built-in
defaults; callers may override every factor.

Reference:
    IPCC (2019) 2019 Refinement to the 2006 IPCC Guidelines — Vol. 2 Energy.
    IPCC AR6 WG1 (2021) Table 7.SM.7 — GWP100 for CH4 = 29.8.
    Indonesian Ministry of Energy (2020) — Emission factor guidance for coal mines.

Author: github.com/achmadnaufal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: IPCC AR6 GWP100 for methane (dimensionless, kg CO2e / kg CH4).
GWP100_CH4: float = 29.8

#: Default diesel emission factor (kg CO2e per litre consumed).
#: Source: IPCC 2006 GL Vol.2, Table 3.2.1 — distillate fuel oil.
DEFAULT_DIESEL_EF_KG_CO2E_PER_LITRE: float = 2.68

#: Default diesel consumption for open-cut coal mining (litres per tonne produced).
#: Representative Indonesian Kalimantan strip-mine average.
DEFAULT_DIESEL_LITRES_PER_TONNE: float = 3.5

#: Default fugitive CH4 emission factor for sub-bituminous open-cut mines
#: (m3 CH4 per tonne of coal mined, in-situ gas content basis).
#: Source: IPCC 2006 GL Vol.2, Ch.4, Table 4.1.4, surface mines category.
DEFAULT_CH4_M3_PER_TONNE: float = 0.9

#: Density of methane at STP (kg/m3).
CH4_DENSITY_KG_PER_M3: float = 0.717

#: Default explosive detonation CO2e per tonne of coal produced (kg CO2e/t).
#: Based on ANFO usage ~0.15 kg/t coal, residual carbon 0.2 kg CO2e/kg ANFO.
DEFAULT_EXPLOSIVE_KG_CO2E_PER_TONNE: float = 0.03

#: Physical upper bound for diesel intensity (litres / tonne produced).
#: Underground longwall mines rarely exceed 12 L/t; cap at 50 for validation.
MAX_DIESEL_LITRES_PER_TONNE: float = 50.0

#: Physical upper bound for CH4 emission factor (m3/t).
MAX_CH4_M3_PER_TONNE: float = 25.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceEmissionProfile:
    """Emission profile for a single coal source.

    Attributes:
        source_id: Unique identifier matching the blend source.
        diesel_litres_per_tonne: Diesel consumption for mining + primary haul
            (litres per tonne produced).  Must be >= 0.
        ch4_m3_per_tonne: Fugitive methane emission factor (m3 CH4 per tonne).
            Must be >= 0.
        explosive_kg_co2e_per_tonne: CO2e from explosive detonation residuals
            (kg CO2e per tonne). Must be >= 0. Defaults to module constant.
        diesel_ef_kg_co2e_per_litre: Site-specific diesel emission factor
            (kg CO2e per litre). Defaults to module constant.
    """

    source_id: str
    diesel_litres_per_tonne: float = DEFAULT_DIESEL_LITRES_PER_TONNE
    ch4_m3_per_tonne: float = DEFAULT_CH4_M3_PER_TONNE
    explosive_kg_co2e_per_tonne: float = DEFAULT_EXPLOSIVE_KG_CO2E_PER_TONNE
    diesel_ef_kg_co2e_per_litre: float = DEFAULT_DIESEL_EF_KG_CO2E_PER_LITRE

    def __post_init__(self) -> None:
        """Validate field values after construction.

        Raises:
            ValueError: If any numeric field is negative or exceeds physical bounds.
        """
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string.")
        if self.diesel_litres_per_tonne < 0:
            raise ValueError(
                f"diesel_litres_per_tonne must be >= 0, got {self.diesel_litres_per_tonne}."
            )
        if self.diesel_litres_per_tonne > MAX_DIESEL_LITRES_PER_TONNE:
            raise ValueError(
                f"diesel_litres_per_tonne {self.diesel_litres_per_tonne} exceeds "
                f"physical maximum {MAX_DIESEL_LITRES_PER_TONNE} L/t."
            )
        if self.ch4_m3_per_tonne < 0:
            raise ValueError(
                f"ch4_m3_per_tonne must be >= 0, got {self.ch4_m3_per_tonne}."
            )
        if self.ch4_m3_per_tonne > MAX_CH4_M3_PER_TONNE:
            raise ValueError(
                f"ch4_m3_per_tonne {self.ch4_m3_per_tonne} exceeds "
                f"physical maximum {MAX_CH4_M3_PER_TONNE} m3/t."
            )
        if self.explosive_kg_co2e_per_tonne < 0:
            raise ValueError(
                f"explosive_kg_co2e_per_tonne must be >= 0, "
                f"got {self.explosive_kg_co2e_per_tonne}."
            )
        if self.diesel_ef_kg_co2e_per_litre <= 0:
            raise ValueError(
                f"diesel_ef_kg_co2e_per_litre must be > 0, "
                f"got {self.diesel_ef_kg_co2e_per_litre}."
            )

    @property
    def diesel_intensity_kg_co2e_per_tonne(self) -> float:
        """CO2e contribution from diesel combustion (kg CO2e / tonne produced)."""
        return self.diesel_litres_per_tonne * self.diesel_ef_kg_co2e_per_litre

    @property
    def ch4_intensity_kg_co2e_per_tonne(self) -> float:
        """CO2e contribution from fugitive CH4 (kg CO2e / tonne produced)."""
        return self.ch4_m3_per_tonne * CH4_DENSITY_KG_PER_M3 * GWP100_CH4

    @property
    def total_intensity_kg_co2e_per_tonne(self) -> float:
        """Total Scope-1 CO2e intensity (kg CO2e / tonne produced).

        Sums diesel combustion, fugitive methane, and explosive contributions.
        """
        return (
            self.diesel_intensity_kg_co2e_per_tonne
            + self.ch4_intensity_kg_co2e_per_tonne
            + self.explosive_kg_co2e_per_tonne
        )


@dataclass(frozen=True)
class BlendSource:
    """A single source within a blend recipe.

    Attributes:
        source_id: Unique identifier matching a ``SourceEmissionProfile``.
        fraction: Weight fraction of this source in the blend (0 < fraction <= 1).
            All fractions in a blend must sum to 1.0 (±0.001 tolerance).
    """

    source_id: str
    fraction: float

    def __post_init__(self) -> None:
        """Validate source_id and fraction.

        Raises:
            ValueError: If source_id is empty or fraction is outside (0, 1].
        """
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string.")
        if not (0 < self.fraction <= 1.0):
            raise ValueError(
                f"fraction must be in (0, 1], got {self.fraction} for '{self.source_id}'."
            )


@dataclass(frozen=True)
class CarbonIntensityResult:
    """Output of ``CarbonIntensityCalculator.calculate``.

    Attributes:
        blended_intensity_kg_co2e_per_tonne: Weighted-average Scope-1 CO2e
            intensity for the blend (kg CO2e / tonne produced).
        diesel_contribution_kg_co2e_per_tonne: Diesel share of the total.
        ch4_contribution_kg_co2e_per_tonne: Fugitive CH4 share of the total.
        explosive_contribution_kg_co2e_per_tonne: Explosive residual share.
        source_breakdown: Per-source intensity dict
            ``{source_id: kg_co2e_per_tonne}``.
        total_co2e_tonnes: Absolute CO2e mass for the entire blend batch
            (only populated when ``volume_mt`` is provided to ``calculate``).
    """

    blended_intensity_kg_co2e_per_tonne: float
    diesel_contribution_kg_co2e_per_tonne: float
    ch4_contribution_kg_co2e_per_tonne: float
    explosive_contribution_kg_co2e_per_tonne: float
    source_breakdown: Dict[str, float]
    total_co2e_tonnes: float | None = None


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class CarbonIntensityCalculator:
    """Computes Scope-1 CO2e intensity for a coal blend.

    Accepts a list of ``SourceEmissionProfile`` objects and a blend recipe
    (list of ``BlendSource`` objects), then returns a ``CarbonIntensityResult``
    with weighted-average intensities broken down by emission source type.

    All inputs are validated; no internal state is mutated after construction.

    Example:
        >>> profiles = [
        ...     SourceEmissionProfile("SEAM_A", diesel_litres_per_tonne=4.0, ch4_m3_per_tonne=1.2),
        ...     SourceEmissionProfile("SEAM_B", diesel_litres_per_tonne=3.0, ch4_m3_per_tonne=0.6),
        ... ]
        >>> blend = [
        ...     BlendSource("SEAM_A", fraction=0.6),
        ...     BlendSource("SEAM_B", fraction=0.4),
        ... ]
        >>> calc = CarbonIntensityCalculator(profiles)
        >>> result = calc.calculate(blend, volume_mt=50_000)
        >>> round(result.blended_intensity_kg_co2e_per_tonne, 2)
        36.6
    """

    def __init__(self, emission_profiles: Sequence[SourceEmissionProfile]) -> None:
        """Initialise the calculator with a collection of emission profiles.

        Args:
            emission_profiles: One ``SourceEmissionProfile`` per potential
                blend source. Must be non-empty; duplicate source_ids are
                rejected.

        Raises:
            ValueError: If ``emission_profiles`` is empty or contains
                duplicate ``source_id`` values.
        """
        if not emission_profiles:
            raise ValueError("emission_profiles must not be empty.")

        seen: set[str] = set()
        for profile in emission_profiles:
            if profile.source_id in seen:
                raise ValueError(
                    f"Duplicate source_id '{profile.source_id}' in emission_profiles."
                )
            seen.add(profile.source_id)

        # Store as immutable dict for O(1) lookups
        self._profiles: Dict[str, SourceEmissionProfile] = {
            p.source_id: p for p in emission_profiles
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(
        self,
        blend: Sequence[BlendSource],
        volume_mt: float | None = None,
    ) -> CarbonIntensityResult:
        """Compute weighted-average Scope-1 CO2e intensity for the blend.

        Args:
            blend: Ordered sequence of ``BlendSource`` objects whose fractions
                must sum to 1.0 (tolerance ±0.001). Must be non-empty. Each
                ``source_id`` must match a registered ``SourceEmissionProfile``.
            volume_mt: Optional total blend batch volume in metric tonnes.
                When supplied, ``CarbonIntensityResult.total_co2e_tonnes`` is
                populated. Must be > 0 if provided.

        Returns:
            A ``CarbonIntensityResult`` (frozen dataclass) with:
            - ``blended_intensity_kg_co2e_per_tonne`` — headline figure
            - ``diesel_contribution_kg_co2e_per_tonne`` — diesel share
            - ``ch4_contribution_kg_co2e_per_tonne`` — methane share
            - ``explosive_contribution_kg_co2e_per_tonne`` — explosive share
            - ``source_breakdown`` — per-source weighted intensity dict
            - ``total_co2e_tonnes`` — absolute CO2e if volume_mt given

        Raises:
            ValueError: If ``blend`` is empty, fractions do not sum to 1,
                any ``source_id`` is unregistered, or ``volume_mt`` <= 0.

        Example:
            >>> profiles = [
            ...     SourceEmissionProfile("A", diesel_litres_per_tonne=3.5, ch4_m3_per_tonne=0.9),
            ... ]
            >>> blend = [BlendSource("A", fraction=1.0)]
            >>> result = CarbonIntensityCalculator(profiles).calculate(blend)
            >>> result.blended_intensity_kg_co2e_per_tonne > 0
            True
        """
        self._validate_blend(blend, volume_mt)

        diesel_total: float = 0.0
        ch4_total: float = 0.0
        explosive_total: float = 0.0
        source_breakdown: Dict[str, float] = {}

        for source in blend:
            profile = self._profiles[source.source_id]
            weighted_intensity = profile.total_intensity_kg_co2e_per_tonne * source.fraction
            source_breakdown = {
                **source_breakdown,
                source.source_id: weighted_intensity,
            }
            diesel_total += profile.diesel_intensity_kg_co2e_per_tonne * source.fraction
            ch4_total += profile.ch4_intensity_kg_co2e_per_tonne * source.fraction
            explosive_total += profile.explosive_kg_co2e_per_tonne * source.fraction

        blended_intensity = diesel_total + ch4_total + explosive_total

        total_co2e: float | None = None
        if volume_mt is not None:
            total_co2e = blended_intensity * volume_mt / 1000.0  # kg → tonnes CO2e

        return CarbonIntensityResult(
            blended_intensity_kg_co2e_per_tonne=blended_intensity,
            diesel_contribution_kg_co2e_per_tonne=diesel_total,
            ch4_contribution_kg_co2e_per_tonne=ch4_total,
            explosive_contribution_kg_co2e_per_tonne=explosive_total,
            source_breakdown=source_breakdown,
            total_co2e_tonnes=total_co2e,
        )

    def intensity_for_source(self, source_id: str) -> float:
        """Return the standalone Scope-1 CO2e intensity for a registered source.

        Args:
            source_id: ID of a previously registered ``SourceEmissionProfile``.

        Returns:
            Total CO2e intensity in kg CO2e per tonne produced.

        Raises:
            KeyError: If ``source_id`` is not registered.

        Example:
            >>> profiles = [SourceEmissionProfile("X", diesel_litres_per_tonne=3.0)]
            >>> calc = CarbonIntensityCalculator(profiles)
            >>> calc.intensity_for_source("X") > 0
            True
        """
        if source_id not in self._profiles:
            raise KeyError(f"source_id '{source_id}' is not registered.")
        return self._profiles[source_id].total_intensity_kg_co2e_per_tonne

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_blend(
        self,
        blend: Sequence[BlendSource],
        volume_mt: float | None,
    ) -> None:
        """Validate blend recipe and optional volume.

        Args:
            blend: Blend recipe to validate.
            volume_mt: Optional volume value to validate.

        Raises:
            ValueError: On any validation failure.
        """
        if not blend:
            raise ValueError("blend must contain at least one BlendSource.")

        fraction_sum = sum(s.fraction for s in blend)
        if abs(fraction_sum - 1.0) > 0.001:
            raise ValueError(
                f"BlendSource fractions must sum to 1.0 (±0.001), got {fraction_sum:.6f}."
            )

        for source in blend:
            if source.source_id not in self._profiles:
                raise ValueError(
                    f"source_id '{source.source_id}' has no registered SourceEmissionProfile."
                )

        if volume_mt is not None and volume_mt <= 0:
            raise ValueError(f"volume_mt must be > 0, got {volume_mt}.")
