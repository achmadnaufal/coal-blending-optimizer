"""
Revenue-maximizing coal blend optimizer under index-linked pricing.

Where :mod:`src.lp_blend_optimizer` finds the *cheapest* blend that satisfies a
quality spec, this module finds the *most profitable* blend: the allocation
that maximises margin = realised sales revenue − procurement cost, given an
index-linked price formula that rewards quality (calorific value) and
penalises contaminants (ash, sulfur, moisture) linearly per tonne.

Price formula (per tonne, applied to the weighted-average blend):

    price_per_tonne = base_price
                      + kcal_premium_per_kcal * (cv_blend - reference_cv)
                      - ash_penalty_per_pct   * (ash_blend - reference_ash)
                      - sulfur_penalty_per_pct * (sulfur_blend - reference_sulfur)
                      - moisture_penalty_per_pct * (moisture_blend - reference_moisture)

Each adjustment term is optional (pass ``None`` or omit to disable). This is
the same linear structure that Newcastle / API4 / ICI-3 export contracts use
in practice (calorific-value adjustment at a USD-per-kcal rate, with ash and
sulfur rejection clauses above a cap).

Because the objective decomposes linearly over per-tonne quantities, the whole
problem remains a Linear Program and is solved with
:func:`scipy.optimize.linprog` (HiGHS backend) — no heavy dependencies added.

Problem formulation
-------------------
Decision variables
    ``x_i`` = tonnes drawn from stockpile ``i`` (continuous, ``>= 0``)

Objective (maximise, re-expressed as minimise of negative)::

    max  sum_i  r_i * x_i
    where r_i = base + k_cv*(cv_i - cv_ref) - k_ash*(ash_i - ash_ref) - ...
                                                                 - cost_i

Subject to
    1. ``sum_i x_i == target_tonnage``
    2. ``0 <= x_i <= availability_i``
    3. Optional quality min/max bands (same linearisation as
       :mod:`src.lp_blend_optimizer`).

The per-tonne-revenue coefficient ``r_i`` is exact because the price formula is
linear in quality and the weighted-average quality times total tonnage equals
``sum_i q_i * x_i`` — so revenue = sum of per-tonne contributions.

Edge cases handled
------------------
* Empty stockpile DataFrame (``ValueError``).
* Non-positive ``target_tonnage`` (``ValueError``).
* Insufficient total supply (feasible=False with diagnostic).
* Zero-availability rows (dropped before solve).
* NaN in any column referenced by a price term or constraint (``ValueError``).
* Infeasible quality constraints (feasible=False with solver diagnostic).
* Negative computed margin (allowed — trader still wants the *least-loss* blend;
  the result dataclass exposes ``margin_per_tonne_usd`` so the caller can
  refuse to ship).

Immutability
------------
Accepts a DataFrame, returns a frozen dataclass. Input is never mutated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from .lp_blend_optimizer import (
    BINDING_SLACK_TOL,
    COLUMN_ALIASES,
    DEFAULT_LP_METHOD,
    REQUIRED_COLUMNS,
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexPriceFormula:
    """Immutable index-linked price formula (USD per tonne).

    The formula is evaluated on the *weighted-average blend quality*, which —
    because the underlying pricing is linear — is equivalent to a
    per-stockpile per-tonne contribution. That is what makes this problem an
    LP rather than a nonlinear optimisation.

    Attributes:
        base_price_usd_per_tonne: Baseline sale price at the reference quality
            point (e.g. an index marker such as ICI-3 or API4). Required.
        kcal_premium_usd_per_kcal: USD uplift per kcal/kg above the reference
            CV (typical range: 0.005–0.02 USD/kcal). ``None`` disables the
            CV adjustment. If set, ``reference_cv_kcal_kg`` must also be set.
            The CV column is named ``calorific_value_kcal_kg`` in the
            DataFrame (aliases accepted — see
            :data:`src.lp_blend_optimizer.COLUMN_ALIASES`).
        reference_cv_kcal_kg: Reference CV for the kcal premium. Required when
            ``kcal_premium_usd_per_kcal`` is set.
        ash_penalty_usd_per_pct: USD deducted per percentage-point of ash
            above ``reference_ash_pct`` (typical range: 0.5–3 USD/%).
        reference_ash_pct: Reference ash for the penalty. Required when
            ``ash_penalty_usd_per_pct`` is set.
        sulfur_penalty_usd_per_pct: USD deducted per percentage-point of
            sulphur above ``reference_sulfur_pct`` (typical range:
            5–50 USD/%).
        reference_sulfur_pct: Reference sulphur for the penalty.
        moisture_penalty_usd_per_pct: USD deducted per percentage-point of
            total moisture above ``reference_moisture_pct``.
        reference_moisture_pct: Reference moisture for the penalty.
    """

    base_price_usd_per_tonne: float
    kcal_premium_usd_per_kcal: Optional[float] = None
    reference_cv_kcal_kg: Optional[float] = None
    ash_penalty_usd_per_pct: Optional[float] = None
    reference_ash_pct: Optional[float] = None
    sulfur_penalty_usd_per_pct: Optional[float] = None
    reference_sulfur_pct: Optional[float] = None
    moisture_penalty_usd_per_pct: Optional[float] = None
    reference_moisture_pct: Optional[float] = None

    def validate(self) -> None:
        """Validate the formula at construction time.

        Raises:
            ValueError: If ``base_price_usd_per_tonne`` is not positive, if a
                penalty/premium rate is set without its reference, if any rate
                or reference is negative, or if values are non-numeric.
        """
        if not isinstance(self.base_price_usd_per_tonne, (int, float)):
            raise ValueError("base_price_usd_per_tonne must be numeric.")
        if self.base_price_usd_per_tonne <= 0:
            raise ValueError(
                f"base_price_usd_per_tonne must be positive, got "
                f"{self.base_price_usd_per_tonne!r}."
            )
        _pairs: List[Tuple[str, Optional[float], str, Optional[float]]] = [
            ("kcal_premium_usd_per_kcal", self.kcal_premium_usd_per_kcal,
             "reference_cv_kcal_kg", self.reference_cv_kcal_kg),
            ("ash_penalty_usd_per_pct", self.ash_penalty_usd_per_pct,
             "reference_ash_pct", self.reference_ash_pct),
            ("sulfur_penalty_usd_per_pct", self.sulfur_penalty_usd_per_pct,
             "reference_sulfur_pct", self.reference_sulfur_pct),
            ("moisture_penalty_usd_per_pct", self.moisture_penalty_usd_per_pct,
             "reference_moisture_pct", self.reference_moisture_pct),
        ]
        for rate_name, rate, ref_name, ref in _pairs:
            if rate is None and ref is None:
                continue
            if rate is None or ref is None:
                raise ValueError(
                    f"{rate_name} and {ref_name} must be set together "
                    f"(got rate={rate!r}, ref={ref!r})."
                )
            if not isinstance(rate, (int, float)):
                raise ValueError(f"{rate_name} must be numeric, got {rate!r}.")
            if not isinstance(ref, (int, float)):
                raise ValueError(f"{ref_name} must be numeric, got {ref!r}.")
            if rate < 0:
                raise ValueError(
                    f"{rate_name} must be non-negative (penalties and premia "
                    f"are magnitudes), got {rate!r}."
                )
            if ref < 0:
                raise ValueError(
                    f"{ref_name} must be non-negative, got {ref!r}."
                )


@dataclass(frozen=True)
class RevenueBlendResult:
    """Immutable result of a revenue-maximising blend solve.

    Attributes:
        feasible: True iff a blend satisfying every constraint was found.
        status: Machine-readable solver status (``optimal``,
            ``insufficient_supply``, ``infeasible``, ``no_available_stockpiles``).
        allocation_tonnes: Mapping ``{stockpile_id: tonnes}``.
        allocation_pct: Mapping ``{stockpile_id: percent_of_total}``.
        blended_quality: Weighted-average quality per parameter.
        total_revenue_usd: Total revenue = price_per_tonne * target_tonnage.
        total_cost_usd: Total procurement cost.
        total_margin_usd: ``total_revenue_usd - total_cost_usd``.
        price_per_tonne_usd: Realised sale price per tonne under the formula.
        cost_per_tonne_usd: Weighted-average procurement cost per tonne.
        margin_per_tonne_usd: ``price_per_tonne_usd - cost_per_tonne_usd``.
        binding_constraints: Labels of quality constraints binding at optimum.
        message: Human-readable diagnostic (infeasibility / warnings).
    """

    feasible: bool
    status: str
    allocation_tonnes: Dict[str, float] = field(default_factory=dict)
    allocation_pct: Dict[str, float] = field(default_factory=dict)
    blended_quality: Dict[str, float] = field(default_factory=dict)
    total_revenue_usd: float = 0.0
    total_cost_usd: float = 0.0
    total_margin_usd: float = 0.0
    price_per_tonne_usd: float = 0.0
    cost_per_tonne_usd: float = 0.0
    margin_per_tonne_usd: float = 0.0
    binding_constraints: List[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict representation for JSON serialisation.

        Returns:
            Dict containing all public fields, with nested dicts copied so
            callers cannot mutate the frozen original.
        """
        return {
            "feasible": self.feasible,
            "status": self.status,
            "allocation_tonnes": dict(self.allocation_tonnes),
            "allocation_pct": dict(self.allocation_pct),
            "blended_quality": dict(self.blended_quality),
            "total_revenue_usd": self.total_revenue_usd,
            "total_cost_usd": self.total_cost_usd,
            "total_margin_usd": self.total_margin_usd,
            "price_per_tonne_usd": self.price_per_tonne_usd,
            "cost_per_tonne_usd": self.cost_per_tonne_usd,
            "margin_per_tonne_usd": self.margin_per_tonne_usd,
            "binding_constraints": list(self.binding_constraints),
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Column resolution helpers
# ---------------------------------------------------------------------------


#: Canonical column names used internally by this module. The penalty terms on
#: the :class:`IndexPriceFormula` are keyed to these columns.
CANONICAL_CV_COLUMN: str = "calorific_value_kcal_kg"
CANONICAL_ASH_COLUMN: str = "ash_pct"
CANONICAL_SULFUR_COLUMN: str = "sulphur_pct"
CANONICAL_MOISTURE_COLUMN: str = "moisture_pct"


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with canonicalised column names.

    Uses the same alias table as :mod:`src.lp_blend_optimizer` so that the
    two optimisers accept the same CSVs.
    """
    renamed = {
        col: COLUMN_ALIASES.get(col.lower().strip(), col.lower().strip())
        for col in df.columns
    }
    return df.rename(columns=renamed).copy()


def _normalise_constraint_keys(
    constraints: Mapping[str, Mapping[str, float]],
) -> Dict[str, Mapping[str, float]]:
    """Remap user-supplied constraint keys through :data:`COLUMN_ALIASES`."""
    return {
        COLUMN_ALIASES.get(k.lower().strip(), k.lower().strip()): v
        for k, v in constraints.items()
    }


def _require_column(df: pd.DataFrame, column: str, context: str) -> None:
    """Raise ValueError if ``column`` is missing from ``df``.

    Args:
        df: Normalised stockpile DataFrame.
        column: Canonical column name required.
        context: Short string describing why the column is needed (used in
            the error message).
    """
    if column not in df.columns:
        raise ValueError(
            f"{context} requires column '{column}' in the stockpile "
            f"DataFrame. Available: {sorted(df.columns)}."
        )
    if df[column].isna().any():
        raise ValueError(
            f"Column '{column}' (used by {context}) contains NaN. "
            f"Drop or impute those rows before optimising."
        )


# ---------------------------------------------------------------------------
# Main optimiser
# ---------------------------------------------------------------------------


class RevenueBlendOptimizer:
    """Revenue-maximising blend optimiser with index-linked pricing.

    This optimiser maximises ``revenue - cost`` per realised tonne under a
    linear quality-adjusted price formula (see :class:`IndexPriceFormula`).
    The underlying problem is a Linear Program solved by HiGHS; no new
    dependencies are introduced beyond SciPy (already required).

    Args:
        method: SciPy linprog method. Defaults to ``"highs"``.
        tolerance: Solver primal-feasibility tolerance. Defaults to ``1e-7``.
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
        price_formula: IndexPriceFormula,
        constraints: Optional[Mapping[str, Mapping[str, float]]] = None,
    ) -> RevenueBlendResult:
        """Maximise ``revenue - cost`` for a blend of the supplied stockpiles.

        Args:
            df: Stockpile DataFrame. Must contain ``stockpile_id``,
                ``tonnage``, ``cost_per_tonne_usd``, plus any column
                referenced by ``price_formula`` or ``constraints``. Common
                aliases (``source_id``, ``volume_available_mt``, ``cv``,
                ``ash``, ``sulfur``, etc.) are auto-mapped.
            target_tonnage: Required blend tonnage in metric tonnes. Must be
                strictly positive.
            price_formula: Pricing rule applied to the blended quality.
                Validated at the start of the call.
            constraints: Optional hard quality bands, same shape as
                :meth:`src.lp_blend_optimizer.LPBlendOptimizer.solve`::

                    {"ash_pct": {"max": 10.0}, "calorific_value_kcal_kg": {"min": 5600}}

                Use this to enforce contractual rejection clauses that the
                price formula alone does not capture (hard rejection vs.
                linear penalty).

        Returns:
            :class:`RevenueBlendResult` with the optimal allocation, total
            and per-tonne margin, and a list of binding quality constraints.

        Raises:
            ValueError: On invalid inputs (empty DataFrame, non-positive
                target, invalid price formula, NaN in a referenced column,
                missing required columns, or constraint referencing a
                non-existent column).
        """
        price_formula.validate()

        if target_tonnage is None or target_tonnage <= 0:
            raise ValueError(
                f"target_tonnage must be positive, got {target_tonnage!r}."
            )

        if df is None or df.empty:
            raise ValueError(
                "Stockpile DataFrame is empty — no sources to blend."
            )

        working = _normalise_columns(df)
        self._check_required_columns(working)
        working = self._coerce_numeric(working)

        constraints = _normalise_constraint_keys(constraints or {})
        self._check_constraint_columns(working, constraints)
        self._check_formula_columns(working, price_formula)

        # Drop stockpiles with zero/NaN availability — they cannot contribute.
        working = working[working["tonnage"].fillna(0) > 0].reset_index(drop=True)
        if working.empty:
            return RevenueBlendResult(
                feasible=False,
                status="no_available_stockpiles",
                message="No stockpiles have positive tonnage available.",
            )

        total_available: float = float(working["tonnage"].sum())
        if total_available < target_tonnage:
            return RevenueBlendResult(
                feasible=False,
                status="insufficient_supply",
                message=(
                    f"Total available tonnage {total_available:,.0f} is less "
                    f"than target {target_tonnage:,.0f}."
                ),
            )

        n: int = len(working)
        stockpile_ids: List[str] = working["stockpile_id"].astype(str).tolist()

        # Per-stockpile price coefficients under the linear formula.
        price_per_stockpile: np.ndarray = self._per_stockpile_price(
            working, price_formula
        )
        cost_per_stockpile: np.ndarray = (
            working["cost_per_tonne_usd"].astype(float).values
        )

        # Objective: maximise margin = price - cost (per tonne).
        # linprog minimises, so negate.
        margin_coeffs: np.ndarray = price_per_stockpile - cost_per_stockpile
        c: np.ndarray = -margin_coeffs

        # Equality constraint: sum x_i == target_tonnage.
        A_eq: np.ndarray = np.ones((1, n))
        b_eq: np.ndarray = np.array([float(target_tonnage)])

        # Hard quality bands.
        A_ub_rows, b_ub_rows, labels = self._build_quality_rows(
            working, constraints
        )
        A_ub: Optional[np.ndarray] = (
            np.vstack(A_ub_rows) if A_ub_rows else None
        )
        b_ub: Optional[np.ndarray] = (
            np.asarray(b_ub_rows, dtype=float) if b_ub_rows else None
        )

        bounds: List[Tuple[float, float]] = [
            (0.0, float(cap)) for cap in working["tonnage"].astype(float).values
        ]

        res = linprog(
            c=c,
            A_ub=A_ub,
            b_ub=b_ub,
            A_eq=A_eq,
            b_eq=b_eq,
            bounds=bounds,
            method=self.method,
            options={"primal_feasibility_tolerance": self.tolerance},
        )

        if not res.success:
            return RevenueBlendResult(
                feasible=False,
                status=f"solver_{res.status}",
                message=(
                    f"LP solver failed: {res.message}. "
                    "Problem is likely infeasible given the hard quality "
                    "constraints — relax them or increase tonnage availability."
                ),
            )

        x: np.ndarray = np.asarray(res.x, dtype=float).clip(min=0.0)
        total: float = float(x.sum()) or 1.0

        # Weighted-average quality across every numeric column.
        quality_cols: List[str] = [
            col for col in working.columns
            if col not in ("stockpile_id", "tonnage", "cost_per_tonne_usd")
            and pd.api.types.is_numeric_dtype(working[col])
        ]
        blended_quality: Dict[str, float] = {
            col: float(np.dot(x, working[col].astype(float).values) / total)
            for col in quality_cols
        }

        # Realised revenue and cost.
        total_revenue: float = float(np.dot(price_per_stockpile, x))
        total_cost: float = float(np.dot(cost_per_stockpile, x))
        total_margin: float = total_revenue - total_cost

        # Binding constraints (hard specs only — price-penalty terms are soft).
        binding: List[str] = []
        if A_ub is not None and b_ub is not None:
            slacks: np.ndarray = b_ub - A_ub @ x
            for label, slack in zip(labels, slacks):
                if abs(slack) <= BINDING_SLACK_TOL * max(1.0, total):
                    binding.append(label)

        alloc_tonnes: Dict[str, float] = {
            sid: round(float(v), 2) for sid, v in zip(stockpile_ids, x)
        }
        alloc_pct: Dict[str, float] = {
            sid: round(float(v) / total * 100.0, 3)
            for sid, v in zip(stockpile_ids, x)
        }

        return RevenueBlendResult(
            feasible=True,
            status="optimal",
            allocation_tonnes=alloc_tonnes,
            allocation_pct=alloc_pct,
            blended_quality={k: round(v, 4) for k, v in blended_quality.items()},
            total_revenue_usd=round(total_revenue, 2),
            total_cost_usd=round(total_cost, 2),
            total_margin_usd=round(total_margin, 2),
            price_per_tonne_usd=round(total_revenue / total, 4),
            cost_per_tonne_usd=round(total_cost / total, 4),
            margin_per_tonne_usd=round(total_margin / total, 4),
            binding_constraints=binding,
            message="",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_required_columns(df: pd.DataFrame) -> None:
        """Raise ValueError if any base required column is missing."""
        missing: List[str] = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(
                f"Missing required columns: {missing}. "
                f"Found: {list(df.columns)}."
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
        """Validate hard-constraint columns (presence + NaN-free)."""
        for param in constraints:
            if param not in df.columns:
                raise ValueError(
                    f"Constraint references column '{param}' not found in "
                    f"DataFrame. Available: {list(df.columns)}."
                )
            if df[param].isna().any():
                raise ValueError(
                    f"Column '{param}' contains NaN values and cannot be used "
                    f"as a blending constraint."
                )

    @staticmethod
    def _check_formula_columns(
        df: pd.DataFrame,
        formula: IndexPriceFormula,
    ) -> None:
        """Validate every column the price formula depends on."""
        if formula.kcal_premium_usd_per_kcal is not None:
            _require_column(df, CANONICAL_CV_COLUMN, "kcal premium")
        if formula.ash_penalty_usd_per_pct is not None:
            _require_column(df, CANONICAL_ASH_COLUMN, "ash penalty")
        if formula.sulfur_penalty_usd_per_pct is not None:
            _require_column(df, CANONICAL_SULFUR_COLUMN, "sulfur penalty")
        if formula.moisture_penalty_usd_per_pct is not None:
            _require_column(df, CANONICAL_MOISTURE_COLUMN, "moisture penalty")

    @staticmethod
    def _per_stockpile_price(
        df: pd.DataFrame,
        formula: IndexPriceFormula,
    ) -> np.ndarray:
        """Compute per-tonne index price for every stockpile row.

        The returned vector ``p`` has the property that
        ``sum_i p_i x_i == price_per_tonne(blend) * sum_i x_i`` because every
        term in the formula is linear in quality.
        """
        n: int = len(df)
        price: np.ndarray = np.full(n, float(formula.base_price_usd_per_tonne))

        if formula.kcal_premium_usd_per_kcal is not None:
            cv: np.ndarray = df[CANONICAL_CV_COLUMN].astype(float).values
            price = price + formula.kcal_premium_usd_per_kcal * (
                cv - float(formula.reference_cv_kcal_kg)
            )
        if formula.ash_penalty_usd_per_pct is not None:
            ash: np.ndarray = df[CANONICAL_ASH_COLUMN].astype(float).values
            price = price - formula.ash_penalty_usd_per_pct * (
                ash - float(formula.reference_ash_pct)
            )
        if formula.sulfur_penalty_usd_per_pct is not None:
            sul: np.ndarray = df[CANONICAL_SULFUR_COLUMN].astype(float).values
            price = price - formula.sulfur_penalty_usd_per_pct * (
                sul - float(formula.reference_sulfur_pct)
            )
        if formula.moisture_penalty_usd_per_pct is not None:
            moi: np.ndarray = df[CANONICAL_MOISTURE_COLUMN].astype(float).values
            price = price - formula.moisture_penalty_usd_per_pct * (
                moi - float(formula.reference_moisture_pct)
            )
        return price

    @staticmethod
    def _build_quality_rows(
        df: pd.DataFrame,
        constraints: Mapping[str, Mapping[str, float]],
    ) -> Tuple[List[np.ndarray], List[float], List[str]]:
        """Assemble inequality rows for hard min/max quality bands.

        Returns a tuple ``(A_ub_rows, b_ub_rows, labels)`` where ``labels``
        is used by the caller to report binding constraints.
        """
        rows: List[np.ndarray] = []
        rhs: List[float] = []
        labels: List[str] = []
        for param, spec in constraints.items():
            q_values: np.ndarray = df[param].astype(float).values
            if spec.get("min") is not None:
                q_min = float(spec["min"])
                rows.append(q_min - q_values)
                rhs.append(0.0)
                labels.append(f"{param}>=min({q_min})")
            if spec.get("max") is not None:
                q_max = float(spec["max"])
                rows.append(q_values - q_max)
                rhs.append(0.0)
                labels.append(f"{param}<=max({q_max})")
        return rows, rhs, labels


# ---------------------------------------------------------------------------
# Functional convenience API
# ---------------------------------------------------------------------------


def maximise_blend_revenue(
    df: pd.DataFrame,
    target_tonnage: float,
    price_formula: IndexPriceFormula,
    constraints: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Dict[str, Any]:
    """Functional wrapper around :meth:`RevenueBlendOptimizer.solve`.

    Returns a plain :class:`dict` for callers that want a JSON-ready output.

    Example:
        >>> from src.revenue_blend_optimizer import (
        ...     IndexPriceFormula, maximise_blend_revenue,
        ... )
        >>> formula = IndexPriceFormula(
        ...     base_price_usd_per_tonne=90.0,
        ...     kcal_premium_usd_per_kcal=0.012, reference_cv_kcal_kg=5800,
        ...     ash_penalty_usd_per_pct=1.5, reference_ash_pct=8.0,
        ... )
        >>> out = maximise_blend_revenue(df, 100_000, formula)
        >>> out["feasible"]
        True

    Args:
        df: Stockpile DataFrame.
        target_tonnage: Blend target in tonnes.
        price_formula: Index-linked pricing rule.
        constraints: Optional hard quality bands.

    Returns:
        Plain dict representation of :class:`RevenueBlendResult`.
    """
    return (
        RevenueBlendOptimizer()
        .solve(df, target_tonnage, price_formula, constraints)
        .to_dict()
    )
