"""
Cost-per-gigajoule (GJ) calculator for thermal coal.

Thermal coal is traded on tonnage but *burned* on energy content. The most
economically relevant price metric is therefore the **cost per unit of energy
delivered** — typically USD per GJ (or USD per million BTU, equivalently) —
which lets buyers compare coals of different calorific values on an equal
energy basis.

This module provides utilities to:

* Convert a cost-per-tonne quote into cost-per-GJ given a calorific value.
* Rank a list of stockpiles by their cost-per-GJ (cheapest energy first).
* Compute the blended cost-per-GJ of a coal blend (weighted-average energy
  basis, *not* weighted-average price basis — these differ in general).
* Calculate delivered / loaded / as-fired cost-per-GJ by stacking freight,
  handling, and moisture penalties onto the mine-gate price.

Unit conventions
----------------
* **Calorific value** is accepted in either kcal/kg or MJ/kg (auto-detected by
  magnitude: values >= 500 are treated as kcal/kg). You can also specify the
  unit explicitly via the ``cv_unit`` parameter.
* **Output** is always USD/GJ unless otherwise noted.

Conversion factors
------------------
* 1 kcal = 4.184e-3 MJ = 4.184e-6 GJ
* 1 MJ/kg = 1 GJ/tonne  (because 1 tonne = 1000 kg)
* Therefore:  GJ/tonne = (kcal/kg) * 4.184e-3

Immutability
------------
All functions return fresh objects; no inputs are mutated.

Example
-------
>>> from src.cost_per_gj_calculator import cost_per_gj
>>> cost_per_gj(cost_per_tonne_usd=60.0, calorific_value=6000)  # kcal/kg
2.39
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Conversion factor: 1 kcal = 4.184e-3 MJ.
KCAL_TO_MJ: float = 4.184e-3

#: Conversion factor: 1 MJ/kg to GJ/tonne (they're numerically identical).
MJ_PER_KG_TO_GJ_PER_TONNE: float = 1.0

#: Threshold for auto-detecting the CV unit.
#: Values >= this are treated as kcal/kg; below are treated as MJ/kg.
#: Reasoning: thermal coal is 12-32 MJ/kg or 2800-7600 kcal/kg, and the two
#: ranges do not overlap.
CV_UNIT_AUTODETECT_THRESHOLD: float = 500.0

#: Supported CV units.
CVUnit = Literal["kcal/kg", "mj/kg", "auto"]


# ---------------------------------------------------------------------------
# Helper: unit conversion
# ---------------------------------------------------------------------------


def _cv_to_gj_per_tonne(
    calorific_value: float,
    cv_unit: CVUnit = "auto",
) -> float:
    """Convert a calorific value to GJ/tonne.

    Args:
        calorific_value: Numeric CV in kcal/kg or MJ/kg.
        cv_unit: ``"kcal/kg"``, ``"mj/kg"``, or ``"auto"`` (default).

    Returns:
        Energy content in GJ per tonne.

    Raises:
        ValueError: If *calorific_value* is not positive or if
            *cv_unit* is not recognised.
    """
    if calorific_value is None or not pd.notna(calorific_value):
        raise ValueError(f"calorific_value must be a finite number, got {calorific_value!r}.")
    if calorific_value <= 0:
        raise ValueError(
            f"calorific_value must be positive, got {calorific_value}."
        )

    unit = cv_unit.lower() if isinstance(cv_unit, str) else "auto"
    if unit not in ("kcal/kg", "mj/kg", "auto"):
        raise ValueError(
            f"Unrecognised cv_unit {cv_unit!r}. Use 'kcal/kg', 'mj/kg', or 'auto'."
        )

    if unit == "auto":
        unit = "kcal/kg" if calorific_value >= CV_UNIT_AUTODETECT_THRESHOLD else "mj/kg"

    if unit == "kcal/kg":
        # (kcal/kg) * (4.184e-3 MJ/kcal) * (1000 kg/tonne) / (1000 MJ/GJ)
        # = (kcal/kg) * 4.184e-3 GJ/tonne
        return float(calorific_value) * KCAL_TO_MJ
    # MJ/kg is numerically identical to GJ/tonne.
    return float(calorific_value) * MJ_PER_KG_TO_GJ_PER_TONNE


# ---------------------------------------------------------------------------
# Single-coal calculation
# ---------------------------------------------------------------------------


def cost_per_gj(
    cost_per_tonne_usd: float,
    calorific_value: float,
    cv_unit: CVUnit = "auto",
    moisture_penalty_pct: float = 0.0,
) -> float:
    """Compute cost per GJ of energy for a single coal grade.

    Args:
        cost_per_tonne_usd: Cost per wet tonne in USD. Must be non-negative.
        calorific_value: Calorific value (as-received basis) in kcal/kg or
            MJ/kg. See :data:`CV_UNIT_AUTODETECT_THRESHOLD` for auto-detection.
        cv_unit: Unit of *calorific_value*. Defaults to ``"auto"``.
        moisture_penalty_pct: Optional energy discount applied to the CV to
            account for as-fired moisture losses. ``5.0`` means "subtract 5%
            of the CV before computing cost/GJ". Must be in ``[0, 100)``.

    Returns:
        Cost per gigajoule in USD/GJ, rounded to 4 decimal places.

    Raises:
        ValueError: If *cost_per_tonne_usd* is negative, if
            *calorific_value* is not positive, or if
            *moisture_penalty_pct* is outside ``[0, 100)``.

    Example::

        >>> cost_per_gj(cost_per_tonne_usd=60.0, calorific_value=6000)
        2.3899
        >>> cost_per_gj(cost_per_tonne_usd=45.0, calorific_value=22.5, cv_unit="mj/kg")
        2.0
    """
    if cost_per_tonne_usd is None or not pd.notna(cost_per_tonne_usd):
        raise ValueError(
            f"cost_per_tonne_usd must be a finite number, got {cost_per_tonne_usd!r}."
        )
    if cost_per_tonne_usd < 0:
        raise ValueError(
            f"cost_per_tonne_usd must be non-negative, got {cost_per_tonne_usd}."
        )
    if not (0.0 <= moisture_penalty_pct < 100.0):
        raise ValueError(
            f"moisture_penalty_pct must be in [0, 100), got {moisture_penalty_pct}."
        )

    gj_per_tonne: float = _cv_to_gj_per_tonne(calorific_value, cv_unit)
    adjusted_gj: float = gj_per_tonne * (1.0 - moisture_penalty_pct / 100.0)
    if adjusted_gj <= 0:
        raise ValueError(
            "Effective energy content is non-positive after moisture penalty — "
            "check calorific_value and moisture_penalty_pct."
        )
    return round(cost_per_tonne_usd / adjusted_gj, 4)


# ---------------------------------------------------------------------------
# Delivered cost calculation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliveredCostBreakdown:
    """Immutable breakdown of a delivered cost-per-GJ calculation."""

    mine_gate_usd_per_tonne: float
    freight_usd_per_tonne: float
    handling_usd_per_tonne: float
    total_usd_per_tonne: float
    gj_per_tonne: float
    cost_per_gj_usd: float

    def to_dict(self) -> Dict[str, float]:
        """Return a plain dict for JSON serialisation."""
        return {
            "mine_gate_usd_per_tonne": self.mine_gate_usd_per_tonne,
            "freight_usd_per_tonne": self.freight_usd_per_tonne,
            "handling_usd_per_tonne": self.handling_usd_per_tonne,
            "total_usd_per_tonne": self.total_usd_per_tonne,
            "gj_per_tonne": self.gj_per_tonne,
            "cost_per_gj_usd": self.cost_per_gj_usd,
        }


def delivered_cost_per_gj(
    mine_gate_usd_per_tonne: float,
    calorific_value: float,
    freight_usd_per_tonne: float = 0.0,
    handling_usd_per_tonne: float = 0.0,
    cv_unit: CVUnit = "auto",
    moisture_penalty_pct: float = 0.0,
) -> DeliveredCostBreakdown:
    """Compute delivered (CIF) cost-per-GJ with freight and handling stacked in.

    Args:
        mine_gate_usd_per_tonne: FOB / mine-gate price per tonne (USD).
        calorific_value: Calorific value (kcal/kg or MJ/kg).
        freight_usd_per_tonne: Mine-to-destination shipping cost per tonne.
            Defaults to ``0.0``. Must be non-negative.
        handling_usd_per_tonne: Port / stockyard handling, demurrage, insurance
            per tonne. Defaults to ``0.0``. Must be non-negative.
        cv_unit: ``"kcal/kg"``, ``"mj/kg"``, or ``"auto"``.
        moisture_penalty_pct: Optional as-fired energy discount in percent.

    Returns:
        :class:`DeliveredCostBreakdown` with per-component costs and the final
        cost-per-GJ.

    Raises:
        ValueError: If any cost component is negative or CV/penalty values
            are out of range (re-raised from helpers).

    Example::

        >>> bd = delivered_cost_per_gj(
        ...     mine_gate_usd_per_tonne=55.0,
        ...     calorific_value=6000,
        ...     freight_usd_per_tonne=12.0,
        ...     handling_usd_per_tonne=3.0,
        ... )
        >>> bd.total_usd_per_tonne
        70.0
    """
    for name, val in (
        ("mine_gate_usd_per_tonne", mine_gate_usd_per_tonne),
        ("freight_usd_per_tonne", freight_usd_per_tonne),
        ("handling_usd_per_tonne", handling_usd_per_tonne),
    ):
        if val is None or not pd.notna(val):
            raise ValueError(f"{name} must be a finite number, got {val!r}.")
        if val < 0:
            raise ValueError(f"{name} must be non-negative, got {val}.")

    total_per_tonne: float = (
        float(mine_gate_usd_per_tonne)
        + float(freight_usd_per_tonne)
        + float(handling_usd_per_tonne)
    )
    gj_per_tonne: float = _cv_to_gj_per_tonne(calorific_value, cv_unit)
    adjusted_gj: float = gj_per_tonne * (1.0 - moisture_penalty_pct / 100.0)
    if adjusted_gj <= 0:
        raise ValueError(
            "Effective energy content is non-positive after moisture penalty."
        )

    return DeliveredCostBreakdown(
        mine_gate_usd_per_tonne=round(float(mine_gate_usd_per_tonne), 4),
        freight_usd_per_tonne=round(float(freight_usd_per_tonne), 4),
        handling_usd_per_tonne=round(float(handling_usd_per_tonne), 4),
        total_usd_per_tonne=round(total_per_tonne, 4),
        gj_per_tonne=round(gj_per_tonne, 4),
        cost_per_gj_usd=round(total_per_tonne / adjusted_gj, 4),
    )


# ---------------------------------------------------------------------------
# Ranked comparison
# ---------------------------------------------------------------------------


def rank_by_cost_per_gj(
    df: pd.DataFrame,
    cost_column: str = "cost_per_tonne_usd",
    cv_column: str = "calorific_value_kcal_kg",
    cv_unit: CVUnit = "auto",
    id_column: Optional[str] = None,
) -> pd.DataFrame:
    """Rank stockpiles by cost-per-GJ (cheapest energy first).

    Args:
        df: Stockpile DataFrame.
        cost_column: Name of the cost-per-tonne column.
        cv_column: Name of the calorific-value column.
        cv_unit: Unit of *cv_column* values.
        id_column: Identifier column to include in the output. If ``None``,
            the DataFrame index is used.

    Returns:
        New DataFrame sorted ascending by ``cost_per_gj_usd``. Rows with
        invalid inputs (NaN, zero CV, negative cost) get ``cost_per_gj_usd``
        set to ``NaN`` and are sorted to the bottom.

    Raises:
        ValueError: If *cost_column* or *cv_column* is missing from *df*.
        ValueError: If *df* is empty.

    Example::

        >>> ranked = rank_by_cost_per_gj(df, id_column="stockpile_id")
        >>> ranked.head()
    """
    if df is None or df.empty:
        raise ValueError("DataFrame is empty — nothing to rank.")
    for col in (cost_column, cv_column):
        if col not in df.columns:
            raise ValueError(
                f"Column '{col}' not found in DataFrame. "
                f"Available columns: {list(df.columns)}."
            )

    rows: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        identifier: Any = row[id_column] if id_column and id_column in df.columns else idx
        try:
            c_per_gj: Optional[float] = cost_per_gj(
                cost_per_tonne_usd=float(row[cost_column]),
                calorific_value=float(row[cv_column]),
                cv_unit=cv_unit,
            )
        except (ValueError, TypeError):
            c_per_gj = None

        rows.append({
            "id": identifier,
            cost_column: row.get(cost_column),
            cv_column: row.get(cv_column),
            "cost_per_gj_usd": c_per_gj,
        })

    out = pd.DataFrame(rows)
    # NaN sorts to the end so the cheapest valid grade is always row 0.
    return out.sort_values("cost_per_gj_usd", na_position="last").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Blended cost
# ---------------------------------------------------------------------------


def blended_cost_per_gj(
    allocation_tonnes: Dict[str, float],
    stockpile_data: Dict[str, Dict[str, float]],
    cv_unit: CVUnit = "auto",
) -> Dict[str, float]:
    """Compute the blended cost per GJ of a blend allocation.

    The blended cost per GJ is:

        sum(cost_i * tonnes_i)  /  sum(gj_i * tonnes_i)

    where ``gj_i`` is the GJ-per-tonne of stockpile *i*. This is *energy-
    weighted*, not tonnage-weighted — a coal with twice the CV contributes
    twice as many GJ per tonne to the denominator.

    Args:
        allocation_tonnes: ``{stockpile_id: tonnes}``. Zero- or negative-tonnage
            entries are ignored.
        stockpile_data: ``{stockpile_id: {"cost_per_tonne_usd": ...,
            "calorific_value": ...}}``. Any stockpile referenced in
            *allocation_tonnes* must appear here with both fields.
        cv_unit: Unit of calorific values in *stockpile_data*.

    Returns:
        Dict with keys ``total_tonnes``, ``total_cost_usd``, ``total_gj``,
        ``cost_per_tonne_usd`` (tonnage-weighted average), and
        ``cost_per_gj_usd`` (the energy-weighted blended cost).

    Raises:
        ValueError: If *allocation_tonnes* is empty, if a referenced
            stockpile is missing required fields, or if the total positive
            tonnage is zero.

    Example::

        >>> alloc = {"A": 40000, "B": 60000}
        >>> data = {
        ...     "A": {"cost_per_tonne_usd": 55, "calorific_value": 6200},
        ...     "B": {"cost_per_tonne_usd": 30, "calorific_value": 5000},
        ... }
        >>> res = blended_cost_per_gj(alloc, data)
        >>> res["cost_per_gj_usd"]
        1.6755
    """
    if not allocation_tonnes:
        raise ValueError("allocation_tonnes is empty — cannot compute blended cost.")

    total_tonnes: float = 0.0
    total_cost: float = 0.0
    total_gj: float = 0.0

    for sid, tonnes in allocation_tonnes.items():
        if tonnes is None or not pd.notna(tonnes) or tonnes <= 0:
            continue
        if sid not in stockpile_data:
            raise ValueError(
                f"Stockpile '{sid}' referenced in allocation but not in stockpile_data."
            )
        sd = stockpile_data[sid]
        for required in ("cost_per_tonne_usd", "calorific_value"):
            if required not in sd:
                raise ValueError(
                    f"Stockpile '{sid}' is missing required field '{required}'."
                )
        cost_t = float(sd["cost_per_tonne_usd"])
        gj_t = _cv_to_gj_per_tonne(float(sd["calorific_value"]), cv_unit)
        total_tonnes += float(tonnes)
        total_cost += cost_t * float(tonnes)
        total_gj += gj_t * float(tonnes)

    if total_tonnes <= 0:
        raise ValueError(
            "No positive-tonnage stockpiles in allocation — cannot compute cost."
        )

    return {
        "total_tonnes": round(total_tonnes, 2),
        "total_cost_usd": round(total_cost, 2),
        "total_gj": round(total_gj, 2),
        "cost_per_tonne_usd": round(total_cost / total_tonnes, 4),
        "cost_per_gj_usd": round(total_cost / total_gj, 4),
    }
