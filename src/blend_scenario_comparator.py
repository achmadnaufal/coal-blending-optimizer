"""
Blend Scenario Comparator for what-if analysis across coal blend recipes.

Allows planners to define multiple named blend "scenarios" (each a recipe of
source fractions) and compare them side-by-side against a single quality spec
and a single stockpile catalogue. Produces a deterministic, immutable
comparison report with:

  - Weighted-average blend properties (CV, ash, sulfur, moisture)
  - Blended cost per tonne (USD)
  - Quality compliance flag per scenario (PASS/FAIL) with binding parameter
  - Ranking by user-selected objective (cost, calorific value, ash, sulfur)
  - Headroom-to-spec metrics for each compliant scenario

Use cases:
  - Pre-shipment what-if: "What if we swap 10% of SEAM_D for SEAM_G?"
  - Sensitivity: compare a baseline against ±N% perturbations
  - Bid evaluation: compare three customer-proposed recipes vs one spec

This module deliberately does NOT solve an optimisation problem; it only
*evaluates* user-supplied recipes. Use ``BlendOptimizer`` (src/main.py) when
you need to *find* the recipe.

Author: github.com/achmadnaufal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Tolerance for blend fraction sum validation (must be 1.0 ±FRACTION_TOLERANCE).
FRACTION_TOLERANCE: float = 1e-3

#: Quality columns that are weighted-averaged across the blend.
QUALITY_PROPERTIES: Tuple[str, ...] = (
    "cv_kcal",
    "ash_pct",
    "sulfur_pct",
    "total_moisture_pct",
)

#: Cost column name in source records.
COST_COLUMN: str = "cost_per_tonne"

#: Allowed ranking objectives.
RANKING_OBJECTIVES: Tuple[str, ...] = (
    "cost_per_tonne",
    "cv_kcal",
    "ash_pct",
    "sulfur_pct",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioRecipe:
    """A named blend recipe defined as source_id → fraction.

    Attributes:
        name: Human-readable scenario label (e.g. ``"baseline"``).
        fractions: Mapping of source_id to weight fraction in (0, 1].
            All fractions must sum to 1.0 (±FRACTION_TOLERANCE).
    """

    name: str
    fractions: Mapping[str, float]

    def __post_init__(self) -> None:
        """Validate scenario name and fraction structure.

        Raises:
            ValueError: If name is empty, fractions empty, any fraction
                outside (0, 1], or sum not equal to 1.0 (±tolerance).
        """
        if not self.name or not self.name.strip():
            raise ValueError("ScenarioRecipe.name must be a non-empty string.")
        if not self.fractions:
            raise ValueError(
                f"Scenario '{self.name}': fractions mapping must not be empty."
            )
        for source_id, frac in self.fractions.items():
            if not source_id or not source_id.strip():
                raise ValueError(
                    f"Scenario '{self.name}': empty source_id in fractions."
                )
            if not (0 < frac <= 1.0):
                raise ValueError(
                    f"Scenario '{self.name}': fraction for '{source_id}' must be "
                    f"in (0, 1], got {frac}."
                )
        total = sum(self.fractions.values())
        if abs(total - 1.0) > FRACTION_TOLERANCE:
            raise ValueError(
                f"Scenario '{self.name}': fractions must sum to 1.0 "
                f"(±{FRACTION_TOLERANCE}), got {total:.6f}."
            )


@dataclass(frozen=True)
class ScenarioResult:
    """Evaluation result for a single scenario.

    Attributes:
        name: Scenario name.
        blended_quality: Weighted-average quality dict.
        blended_cost_per_tonne: Weighted-average cost (USD/t).
        feasible: True iff every spec parameter passes.
        binding_parameter: Name of the spec parameter that fails (or the
            tightest, when feasible). ``None`` when no specs supplied.
        spec_headroom: Per-parameter headroom dict; positive = within spec,
            negative = violation magnitude.
    """

    name: str
    blended_quality: Mapping[str, float]
    blended_cost_per_tonne: float
    feasible: bool
    binding_parameter: Optional[str]
    spec_headroom: Mapping[str, float]


@dataclass(frozen=True)
class ComparisonReport:
    """Side-by-side comparison report for multiple scenarios.

    Attributes:
        scenarios: Per-scenario results in input order.
        ranking_objective: Objective used to rank scenarios.
        ranked_names: Scenario names ordered best → worst by objective among
            feasible scenarios; infeasible scenarios are appended at the end
            in their original order.
        winner: Name of the top-ranked feasible scenario, or ``None`` when no
            scenario is feasible.
    """

    scenarios: Tuple[ScenarioResult, ...]
    ranking_objective: str
    ranked_names: Tuple[str, ...]
    winner: Optional[str]


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------


class BlendScenarioComparator:
    """Evaluate and rank multiple coal blend scenarios under one spec set.

    Args:
        sources: Sequence of source records. Each record must be a mapping
            with at least ``source_id`` plus all keys in
            :data:`QUALITY_PROPERTIES` and :data:`COST_COLUMN`.
        specs: Optional dict mapping quality property name → ``{"min": x,
            "max": y}`` (either bound optional). When omitted, every scenario
            is reported feasible and ranking is purely by objective.

    Raises:
        ValueError: If sources is empty, contains duplicate source_ids,
            misses required quality keys, has any non-numeric / negative
            value, or specs has inverted bounds.

    Example:
        >>> sources = [
        ...     {"source_id": "A", "cv_kcal": 6200, "ash_pct": 5.0,
        ...      "sulfur_pct": 0.4, "total_moisture_pct": 9.0,
        ...      "cost_per_tonne": 85.0},
        ...     {"source_id": "B", "cv_kcal": 5800, "ash_pct": 8.0,
        ...      "sulfur_pct": 0.7, "total_moisture_pct": 13.0,
        ...      "cost_per_tonne": 65.0},
        ... ]
        >>> specs = {"cv_kcal": {"min": 5900},
        ...          "ash_pct": {"max": 7.5},
        ...          "sulfur_pct": {"max": 0.6}}
        >>> cmp = BlendScenarioComparator(sources, specs=specs)
        >>> scenarios = [
        ...     ScenarioRecipe("rich",  {"A": 0.8, "B": 0.2}),
        ...     ScenarioRecipe("cheap", {"A": 0.3, "B": 0.7}),
        ... ]
        >>> report = cmp.compare(scenarios, ranking_objective="cost_per_tonne")
        >>> report.winner
        'rich'
    """

    def __init__(
        self,
        sources: Sequence[Mapping[str, object]],
        specs: Optional[Mapping[str, Mapping[str, float]]] = None,
    ) -> None:
        if not sources:
            raise ValueError("sources must not be empty.")

        self._sources: Dict[str, Dict[str, float]] = {}
        for record in sources:
            sid = record.get("source_id")
            if not isinstance(sid, str) or not sid.strip():
                raise ValueError("Every source record must have a non-empty 'source_id'.")
            if sid in self._sources:
                raise ValueError(f"Duplicate source_id '{sid}' in sources.")
            cleaned: Dict[str, float] = {}
            for key in (*QUALITY_PROPERTIES, COST_COLUMN):
                if key not in record:
                    raise ValueError(
                        f"Source '{sid}' missing required key '{key}'."
                    )
                try:
                    val = float(record[key])  # type: ignore[arg-type]
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Source '{sid}' value for '{key}' is not numeric: {record[key]!r}"
                    ) from exc
                if val < 0:
                    raise ValueError(
                        f"Source '{sid}' value for '{key}' must be >= 0, got {val}."
                    )
                cleaned = {**cleaned, key: val}
            self._sources = {**self._sources, sid: cleaned}

        self._specs: Dict[str, Dict[str, float]] = {}
        if specs:
            self._specs = self._validate_and_freeze_specs(specs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(
        self,
        scenarios: Sequence[ScenarioRecipe],
        ranking_objective: str = "cost_per_tonne",
    ) -> ComparisonReport:
        """Evaluate every scenario and return a ranked side-by-side report.

        Args:
            scenarios: Recipes to evaluate. Must be non-empty; names must be
                unique. Every source_id referenced must exist in the
                catalogue passed at construction.
            ranking_objective: Property to rank by. Must be one of
                :data:`RANKING_OBJECTIVES`. ``cost_per_tonne``, ``ash_pct``
                and ``sulfur_pct`` rank ascending (lower is better);
                ``cv_kcal`` ranks descending (higher is better).

        Returns:
            A :class:`ComparisonReport` (frozen).

        Raises:
            ValueError: If ``scenarios`` is empty, contains duplicate names,
                references unknown source_ids, or ``ranking_objective`` is
                unsupported.
        """
        if not scenarios:
            raise ValueError("scenarios must not be empty.")
        if ranking_objective not in RANKING_OBJECTIVES:
            raise ValueError(
                f"ranking_objective must be one of {RANKING_OBJECTIVES}, "
                f"got '{ranking_objective}'."
            )
        seen_names: set[str] = set()
        for scenario in scenarios:
            if scenario.name in seen_names:
                raise ValueError(f"Duplicate scenario name '{scenario.name}'.")
            seen_names.add(scenario.name)
            for sid in scenario.fractions:
                if sid not in self._sources:
                    raise ValueError(
                        f"Scenario '{scenario.name}' references unknown source_id '{sid}'."
                    )

        results: Tuple[ScenarioResult, ...] = tuple(
            self._evaluate(scenario) for scenario in scenarios
        )

        ranked_names = self._rank(results, ranking_objective)
        winner: Optional[str] = next(
            (name for name in ranked_names
             if next(r for r in results if r.name == name).feasible),
            None,
        )

        return ComparisonReport(
            scenarios=results,
            ranking_objective=ranking_objective,
            ranked_names=ranked_names,
            winner=winner,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate(self, scenario: ScenarioRecipe) -> ScenarioResult:
        """Compute weighted blend, cost, and compliance for a scenario."""
        quality: Dict[str, float] = {prop: 0.0 for prop in QUALITY_PROPERTIES}
        cost = 0.0
        for sid, frac in scenario.fractions.items():
            record = self._sources[sid]
            for prop in QUALITY_PROPERTIES:
                quality = {**quality, prop: quality[prop] + record[prop] * frac}
            cost += record[COST_COLUMN] * frac

        headroom, binding, feasible = self._compliance(quality)

        return ScenarioResult(
            name=scenario.name,
            blended_quality=quality,
            blended_cost_per_tonne=round(cost, 4),
            feasible=feasible,
            binding_parameter=binding,
            spec_headroom=headroom,
        )

    def _compliance(
        self, quality: Mapping[str, float]
    ) -> Tuple[Dict[str, float], Optional[str], bool]:
        """Return per-parameter headroom, binding parameter, and feasibility."""
        if not self._specs:
            return ({}, None, True)
        headroom: Dict[str, float] = {}
        feasible = True
        worst_param: Optional[str] = None
        worst_headroom = float("inf")
        for param, spec in self._specs.items():
            value = quality.get(param)
            if value is None:
                continue
            lo = spec.get("min")
            hi = spec.get("max")
            distances: List[float] = []
            if lo is not None:
                distances.append(value - lo)  # negative → below min
            if hi is not None:
                distances.append(hi - value)  # negative → above max
            param_headroom = min(distances) if distances else float("inf")
            headroom = {**headroom, param: round(param_headroom, 6)}
            if param_headroom < 0:
                feasible = False
            if param_headroom < worst_headroom:
                worst_headroom = param_headroom
                worst_param = param
        return (headroom, worst_param, feasible)

    @staticmethod
    def _rank(
        results: Sequence[ScenarioResult], objective: str
    ) -> Tuple[str, ...]:
        """Order scenarios best→worst by objective (feasible first)."""
        descending = objective == "cv_kcal"
        feasible = [r for r in results if r.feasible]
        infeasible = [r for r in results if not r.feasible]

        def key_fn(r: ScenarioResult) -> float:
            if objective == "cost_per_tonne":
                return r.blended_cost_per_tonne
            return float(r.blended_quality[objective])

        feasible_sorted = sorted(feasible, key=key_fn, reverse=descending)
        ordered = (*feasible_sorted, *infeasible)
        return tuple(r.name for r in ordered)

    @staticmethod
    def _validate_and_freeze_specs(
        specs: Mapping[str, Mapping[str, float]],
    ) -> Dict[str, Dict[str, float]]:
        """Validate spec bounds and return a defensive deep copy."""
        frozen: Dict[str, Dict[str, float]] = {}
        for param, spec in specs.items():
            lo = spec.get("min")
            hi = spec.get("max")
            if lo is None and hi is None:
                raise ValueError(
                    f"Spec for '{param}' must define at least 'min' or 'max'."
                )
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(
                    f"Spec for '{param}': min ({lo}) cannot exceed max ({hi})."
                )
            frozen = {**frozen, param: {k: float(v) for k, v in spec.items()}}
        return frozen
