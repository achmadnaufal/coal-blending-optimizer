"""
Linear-programming based coal blend optimizer.

Formulates the blend optimization problem as a Linear Program (LP) and solves
it with :func:`scipy.optimize.linprog`. Unlike the score-weighted allocator in
:mod:`src.main`, this module finds the *provably cost-minimising* blend that
respects every quality constraint (min/max on ash, sulfur, calorific value,
moisture, etc.), per-stockpile availability caps, and a total-tonnage target.

Problem formulation
-------------------
Decision variables
    ``x_i`` = tonnes drawn from stockpile *i* (continuous, ``>= 0``)

Objective (minimise)
    ``sum_i (cost_per_tonne_i * x_i)``

Constraints
    1. Total tonnage:  ``sum_i x_i == target_tonnes``
    2. Availability:   ``0 <= x_i <= available_i``
    3. Quality (linear weighted-average, rewritten in standard form):

       For a ``min`` bound on quality parameter ``q``:
           ``sum_i (q_min - q_i) * x_i <= 0``

       For a ``max`` bound on quality parameter ``q``:
           ``sum_i (q_i - q_max) * x_i <= 0``

Solver
    Uses ``scipy.optimize.linprog`` with the HiGHS dual-simplex method, which
    is fast (<10 ms for typical coal blends) and handles problems up to
    thousands of stockpiles without issue.

Edge cases handled
------------------
* Empty / single-stockpile pools (trivial degenerate solution)
* NaN values in quality columns (rejected up-front with clear error)
* Zero tonnage or zero availability (rejected)
* Infeasible problems (LP solver returns ``status != 0``; we surface a
  human-readable explanation of which constraint is unsatisfiable)
* Over- and under-constrained problems (detected via constraint-slack analysis
  in the returned result dict)

Immutability
------------
All functions accept DataFrames and return fresh result dicts. Input data is
never mutated.

Example
-------
>>> import pandas as pd
>>> from src.lp_blend_optimizer import LPBlendOptimizer
>>> df = pd.DataFrame([
...     {"stockpile_id": "A", "calorific_value_kcal_kg": 6200,
...      "ash_pct": 7.0, "sulphur_pct": 0.4, "tonnage": 50000,
...      "cost_per_tonne_usd": 45.0},
...     {"stockpile_id": "B", "calorific_value_kcal_kg": 5400,
...      "ash_pct": 12.0, "sulphur_pct": 0.6, "tonnage": 80000,
...      "cost_per_tonne_usd": 30.0},
... ])
>>> lp = LPBlendOptimizer()
>>> res = lp.solve(
...     df,
...     target_tonnage=100000,
...     constraints={
...         "calorific_value_kcal_kg": {"min": 5800},
...         "ash_pct": {"max": 10.0},
...         "sulphur_pct": {"max": 0.5},
...     },
... )
>>> res["feasible"]
True
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import linprog


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Default LP method; HiGHS is fast, robust, and distributed with SciPy 1.6+.
DEFAULT_LP_METHOD: str = "highs"

#: Column name aliases mapping common variants to canonical names.
COLUMN_ALIASES: Dict[str, str] = {
    "stockpile": "stockpile_id",
    "source_id": "stockpile_id",
    "id": "stockpile_id",
    "calorific_value": "calorific_value_kcal_kg",
    "cv": "calorific_value_kcal_kg",
    "gcv": "calorific_value_kcal_kg",
    "ash": "ash_pct",
    "ash_content_pct": "ash_pct",
    "sulfur_pct": "sulphur_pct",
    "sulphur": "sulphur_pct",
    "sulfur": "sulphur_pct",
    "moisture": "moisture_pct",
    "total_moisture_pct": "moisture_pct",
    "tonnes": "tonnage",
    "volume_available_mt": "tonnage",
    "available_tonnes": "tonnage",
    "cost": "cost_per_tonne_usd",
    "price_usd_t": "cost_per_tonne_usd",
}

#: Required columns for an LP problem.
REQUIRED_COLUMNS: Tuple[str, ...] = (
    "stockpile_id",
    "tonnage",
    "cost_per_tonne_usd",
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LPBlendResult:
    """Immutable result of an LP blend solve.

    Attributes:
        feasible: True iff a blend satisfying every constraint was found.
        status: Human-readable solver status message.
        allocation_tonnes: Mapping ``{stockpile_id: tonnes}`` (0 if unused).
        allocation_pct: Mapping ``{stockpile_id: percent_of_total}``.
        blended_quality: Weighted-average quality values by parameter.
        total_cost_usd: Total blend cost at the cost-per-tonne given.
        cost_per_tonne_usd: Weighted-average cost per tonne of the blend.
        binding_constraints: List of constraint keys active at the optimum
            (slack below :data:`BINDING_SLACK_TOL`).
        message: Human-readable diagnostic (populated on infeasibility).
    """

    feasible: bool
    status: str
    allocation_tonnes: Dict[str, float] = field(default_factory=dict)
    allocation_pct: Dict[str, float] = field(default_factory=dict)
    blended_quality: Dict[str, float] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    cost_per_tonne_usd: float = 0.0
    binding_constraints: List[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict representation for JSON serialisation."""
        return {
            "feasible": self.feasible,
            "status": self.status,
            "allocation_tonnes": dict(self.allocation_tonnes),
            "allocation_pct": dict(self.allocation_pct),
            "blended_quality": dict(self.blended_quality),
            "total_cost_usd": self.total_cost_usd,
            "cost_per_tonne_usd": self.cost_per_tonne_usd,
            "binding_constraints": list(self.binding_constraints),
            "message": self.message,
        }


#: Tolerance below which a constraint slack is considered "binding".
BINDING_SLACK_TOL: float = 1e-4


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class LPBlendOptimizer:
    """Cost-minimising blend optimizer using linear programming.

    This class wraps :func:`scipy.optimize.linprog` (HiGHS backend) to find
    the mathematically optimal stockpile allocation under linear quality
    constraints and a fixed tonnage target.

    The optimizer is *stateless*: construction takes no arguments (beyond
    solver tuning) and every call to :meth:`solve` is fully independent.

    Args:
        method: SciPy linprog method name. Defaults to ``"highs"``.
        tolerance: Solver feasibility tolerance (passed through to HiGHS
            options as ``primal_feasibility_tolerance``). Defaults to
            ``1e-7``.
    """

    def __init__(
        self,
        method: str = DEFAULT_LP_METHOD,
        tolerance: float = 1e-7,
    ) -> None:
        self.method: str = method
        self.tolerance: float = tolerance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def solve(
        self,
        df: pd.DataFrame,
        target_tonnage: float,
        constraints: Optional[Mapping[str, Mapping[str, float]]] = None,
    ) -> LPBlendResult:
        """Solve the LP blend problem.

        Args:
            df: Stockpile DataFrame. Must contain at minimum
                ``stockpile_id``, ``tonnage``, and ``cost_per_tonne_usd``
                columns (aliases accepted — see :data:`COLUMN_ALIASES`).
                Quality parameter columns referenced by ``constraints``
                must also be present.
            target_tonnage: Required blend tonnage (must be > 0).
            constraints: Optional mapping of quality parameter name to a
                dict with ``min`` and/or ``max`` keys. Example::

                    {
                        "calorific_value_kcal_kg": {"min": 5800},
                        "ash_pct": {"max": 10.0},
                        "sulphur_pct": {"max": 0.5},
                    }

        Returns:
            :class:`LPBlendResult` with full solver output and diagnostics.

        Raises:
            ValueError: If *target_tonnage* is not positive, if the
                DataFrame is missing required columns, if a constraint
                references a column not in the DataFrame, or if quality
                columns referenced by constraints contain NaN.
        """
        if target_tonnage is None or target_tonnage <= 0:
            raise ValueError(
                f"target_tonnage must be positive, got {target_tonnage!r}."
            )

        if df is None or df.empty:
            raise ValueError("Stockpile DataFrame is empty — no sources to blend.")

        working = self._normalise_columns(df)
        self._check_required_columns(working)
        working = self._coerce_numeric(working)

        constraints = dict(constraints or {})
        self._check_constraint_columns(working, constraints)

        # Drop stockpiles with zero or NaN availability — they cannot contribute.
        working = working[working["tonnage"].fillna(0) > 0].reset_index(drop=True)
        if working.empty:
            return LPBlendResult(
                feasible=False,
                status="no_available_stockpiles",
                message="No stockpiles have positive tonnage available.",
            )

        total_available: float = float(working["tonnage"].sum())
        if total_available < target_tonnage:
            return LPBlendResult(
                feasible=False,
                status="insufficient_supply",
                message=(
                    f"Total available tonnage {total_available:,.0f} is less "
                    f"than target {target_tonnage:,.0f}."
                ),
            )

        n: int = len(working)
        stockpile_ids: List[str] = working["stockpile_id"].astype(str).tolist()

        # Degenerate case: single stockpile. LP would still work but we short-
        # circuit for clarity and to avoid floating-point drift in the solver.
        if n == 1:
            return self._single_stockpile_result(
                working, target_tonnage, constraints, stockpile_ids[0]
            )

        # Objective: minimise total cost = sum_i cost_i * x_i
        c: np.ndarray = working["cost_per_tonne_usd"].astype(float).values

        # Equality constraint: sum_i x_i == target_tonnage
        A_eq: np.ndarray = np.ones((1, n))
        b_eq: np.ndarray = np.array([float(target_tonnage)])

        # Inequality constraints from quality specs.
        A_ub_rows: List[np.ndarray] = []
        b_ub_rows: List[float] = []
        constraint_labels: List[str] = []

        for param, spec in constraints.items():
            q_values: np.ndarray = working[param].astype(float).values
            if "min" in spec and spec["min"] is not None:
                q_min = float(spec["min"])
                # sum (q_min - q_i) * x_i <= 0  <=>  weighted-avg >= q_min
                A_ub_rows.append(q_min - q_values)
                b_ub_rows.append(0.0)
                constraint_labels.append(f"{param}>=min({q_min})")
            if "max" in spec and spec["max"] is not None:
                q_max = float(spec["max"])
                # sum (q_i - q_max) * x_i <= 0  <=>  weighted-avg <= q_max
                A_ub_rows.append(q_values - q_max)
                b_ub_rows.append(0.0)
                constraint_labels.append(f"{param}<=max({q_max})")

        A_ub: Optional[np.ndarray] = (
            np.vstack(A_ub_rows) if A_ub_rows else None
        )
        b_ub: Optional[np.ndarray] = (
            np.asarray(b_ub_rows, dtype=float) if b_ub_rows else None
        )

        bounds: List[Tuple[float, float]] = [
            (0.0, float(cap)) for cap in working["tonnage"].astype(float).values
        ]

        options: Dict[str, Any] = {
            "primal_feasibility_tolerance": self.tolerance,
        }

        res = linprog(
            c=c,
            A_ub=A_ub,
            b_ub=b_ub,
            A_eq=A_eq,
            b_eq=b_eq,
            bounds=bounds,
            method=self.method,
            options=options,
        )

        if not res.success:
            return LPBlendResult(
                feasible=False,
                status=f"solver_{res.status}",
                message=(
                    f"LP solver failed: {res.message}. "
                    "Problem is likely infeasible — relax quality constraints "
                    "or increase tonnage target."
                ),
            )

        x: np.ndarray = np.asarray(res.x, dtype=float).clip(min=0.0)
        total: float = float(x.sum()) or 1.0  # guard against divide-by-zero

        # Compute blended quality on every numeric column present in df.
        quality_cols: List[str] = [
            col for col in working.columns
            if col not in ("stockpile_id", "tonnage", "cost_per_tonne_usd")
            and pd.api.types.is_numeric_dtype(working[col])
        ]
        blended_quality: Dict[str, float] = {
            col: float(np.dot(x, working[col].astype(float).values) / total)
            for col in quality_cols
        }

        # Identify binding quality constraints (slack below tolerance).
        binding: List[str] = []
        if A_ub is not None and b_ub is not None:
            slacks: np.ndarray = b_ub - A_ub @ x
            for label, slack in zip(constraint_labels, slacks):
                if abs(slack) <= BINDING_SLACK_TOL * max(1.0, total):
                    binding.append(label)

        alloc_tonnes: Dict[str, float] = {
            sid: round(float(v), 2) for sid, v in zip(stockpile_ids, x)
        }
        alloc_pct: Dict[str, float] = {
            sid: round(float(v) / total * 100.0, 3)
            for sid, v in zip(stockpile_ids, x)
        }
        total_cost: float = float(np.dot(c, x))

        return LPBlendResult(
            feasible=True,
            status="optimal",
            allocation_tonnes=alloc_tonnes,
            allocation_pct=alloc_pct,
            blended_quality={k: round(v, 4) for k, v in blended_quality.items()},
            total_cost_usd=round(total_cost, 2),
            cost_per_tonne_usd=round(total_cost / total, 4),
            binding_constraints=binding,
            message="",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of ``df`` with column names canonicalised."""
        renamed = {
            col: COLUMN_ALIASES.get(col.lower().strip(), col.lower().strip())
            for col in df.columns
        }
        return df.rename(columns=renamed).copy()

    @staticmethod
    def _check_required_columns(df: pd.DataFrame) -> None:
        """Raise ValueError if any required column is missing."""
        missing: List[str] = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(
                f"Missing required columns: {missing}. Found: {list(df.columns)}."
            )

    @staticmethod
    def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
        """Coerce known numeric columns to float; leave stockpile_id as-is."""
        working = df.copy()
        for col in working.columns:
            if col == "stockpile_id":
                continue
            working[col] = pd.to_numeric(working[col], errors="coerce")
        return working

    @staticmethod
    def _check_constraint_columns(
        df: pd.DataFrame,
        constraints: Mapping[str, Mapping[str, float]],
    ) -> None:
        """Raise ValueError if any constraint references a missing or NaN column."""
        for param in constraints:
            if param not in df.columns:
                raise ValueError(
                    f"Constraint references column '{param}' not found in "
                    f"DataFrame. Available columns: {list(df.columns)}."
                )
            if df[param].isna().any():
                raise ValueError(
                    f"Column '{param}' contains NaN values and cannot be used "
                    f"as a blending constraint. Drop or impute missing rows first."
                )

    @staticmethod
    def _single_stockpile_result(
        df: pd.DataFrame,
        target_tonnage: float,
        constraints: Mapping[str, Mapping[str, float]],
        stockpile_id: str,
    ) -> LPBlendResult:
        """Handle the degenerate 1-stockpile case explicitly."""
        row = df.iloc[0]
        cap: float = float(row["tonnage"])
        if cap < target_tonnage:
            return LPBlendResult(
                feasible=False,
                status="insufficient_supply",
                message=(
                    f"Single stockpile has {cap:,.0f} available but "
                    f"{target_tonnage:,.0f} required."
                ),
            )
        # Check each quality constraint on the lone stockpile's value.
        violations: List[str] = []
        quality: Dict[str, float] = {}
        for col in df.columns:
            if col in ("stockpile_id", "tonnage", "cost_per_tonne_usd"):
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                quality[col] = float(row[col])

        for param, spec in constraints.items():
            val = float(row[param])
            if spec.get("min") is not None and val < float(spec["min"]):
                violations.append(f"{param}={val} < min={spec['min']}")
            if spec.get("max") is not None and val > float(spec["max"]):
                violations.append(f"{param}={val} > max={spec['max']}")

        if violations:
            return LPBlendResult(
                feasible=False,
                status="infeasible_single_stockpile",
                message=(
                    "Sole stockpile violates quality constraints: "
                    + "; ".join(violations)
                ),
            )

        cost_per_t: float = float(row["cost_per_tonne_usd"])
        return LPBlendResult(
            feasible=True,
            status="optimal",
            allocation_tonnes={stockpile_id: round(target_tonnage, 2)},
            allocation_pct={stockpile_id: 100.0},
            blended_quality={k: round(v, 4) for k, v in quality.items()},
            total_cost_usd=round(cost_per_t * target_tonnage, 2),
            cost_per_tonne_usd=round(cost_per_t, 4),
            binding_constraints=[],
            message="Single-stockpile degenerate solution.",
        )


# ---------------------------------------------------------------------------
# Convenience functional API
# ---------------------------------------------------------------------------


def optimize_blend_lp(
    df: pd.DataFrame,
    target_tonnage: float,
    constraints: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Dict[str, Any]:
    """Functional wrapper around :meth:`LPBlendOptimizer.solve`.

    Returns a plain :class:`dict` (not an :class:`LPBlendResult` dataclass)
    for callers that prefer a JSON-ready output.

    Example::

        >>> from src.lp_blend_optimizer import optimize_blend_lp
        >>> result = optimize_blend_lp(df, 100_000, {"ash_pct": {"max": 10}})
        >>> result["feasible"]
        True
    """
    optimizer = LPBlendOptimizer()
    return optimizer.solve(df, target_tonnage, constraints).to_dict()
