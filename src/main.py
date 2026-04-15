"""
Coal blending optimization to meet quality targets and maximize value.

This module provides tools for optimizing coal blend ratios from multiple
source seams/stockpiles to meet product quality specifications (calorific value,
moisture, ash, sulfur) while minimizing cost or maximizing revenue.

Key classes:
    BlendOptimizer: Main optimizer implementing score-weighted allocation.

Typical usage::

    from src.main import BlendOptimizer

    optimizer = BlendOptimizer()
    df = optimizer.load_data("sample_data/stockpiles.csv")
    result = optimizer.optimize_blend(df, target_volume_mt=100_000)
    print(result["blend_ratios"])

Author: github.com/achmadnaufal
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Quality parameter upper bounds used for sanity validation.
QUALITY_UPPER_BOUNDS: Dict[str, float] = {
    "calorific_value": 9000.0,   # kcal/kg – theoretical hard coal maximum
    "total_moisture": 100.0,     # %
    "ash_pct": 100.0,            # %
    "sulfur_pct": 10.0,          # % – very high-sulfur coals reach ~5 %
    "volatile_matter_pct": 100.0,
}

#: Default quality specification targets/limits (Indonesian export grade).
DEFAULT_QUALITY_SPECS: Dict[str, Dict[str, float]] = {
    "calorific_value_kcal": {"min": 5800, "target": 6000, "max": 6300},
    "total_moisture_pct": {"min": 0, "target": 10, "max": 14},
    "ash_pct": {"min": 0, "target": 6, "max": 8},
    "sulfur_pct": {"min": 0, "target": 0.5, "max": 0.8},
}

#: Required columns that must be present after column normalisation.
REQUIRED_QUALITY_COLUMNS: Tuple[str, ...] = (
    "calorific_value",
    "total_moisture",
    "ash_pct",
    "sulfur_pct",
)


class BlendOptimizer:
    """Coal quality blending optimizer.

    Solves the blend optimization problem: given *N* coal sources with known
    quality parameters and costs, find blend ratios that meet product quality
    specifications at minimum cost.  The allocation strategy is score-based
    weighted proportional assignment with volume-cap enforcement.

    Immutability guarantee:
        All public methods that accept a :class:`pandas.DataFrame` operate on
        an internal copy and **never** mutate the caller's data.

    Args:
        config: Optional configuration mapping with supported keys:

            - ``quality_specs`` (:class:`dict`) – Override default quality
              parameter targets and limits.  Each key maps to a sub-dict with
              optional ``min``, ``max``, and ``target`` float values.

    Raises:
        ValueError: If *config* contains a ``quality_specs`` dict with
            inverted ``min``/``max`` bounds (min > max).

    Example::

        >>> optimizer = BlendOptimizer()
        >>> import pandas as pd
        >>> df = pd.read_csv("sample_data/stockpiles.csv")
        >>> result = optimizer.optimize_blend(df, target_volume_mt=100_000)
        >>> print(result["blend_ratios"])
        {'SEAM_A': 18.42, 'SEAM_B': 12.35, ...}
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = config or {}
        self.quality_specs: Dict[str, Dict[str, float]] = self.config.get(
            "quality_specs", DEFAULT_QUALITY_SPECS
        )
        self._validate_quality_specs(self.quality_specs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_data(self, filepath: str) -> pd.DataFrame:
        """Load coal source data from a CSV or Excel file.

        Args:
            filepath: Path to the data file.  Supported formats: ``.csv``,
                ``.xlsx``, ``.xls``.  Expected columns include
                ``source_id``, ``calorific_value``, ``total_moisture``,
                ``ash_pct``, ``sulfur_pct``, ``volume_available_mt``, and
                ``price_usd_t`` (price is optional).

        Returns:
            :class:`pandas.DataFrame` containing coal source quality and
            availability data exactly as stored in the file (no preprocessing
            is applied at this stage).

        Raises:
            FileNotFoundError: If no file exists at *filepath*.
            ValueError: If *filepath* has an unsupported extension.

        Example::

            >>> optimizer = BlendOptimizer()
            >>> df = optimizer.load_data("sample_data/stockpiles.csv")
            >>> df.shape
            (8, 7)
        """
        p = Path(filepath)
        if not p.exists():
            raise FileNotFoundError(f"Data file not found: {filepath}")
        if p.suffix in (".xlsx", ".xls"):
            return pd.read_excel(filepath)
        if p.suffix in (".csv", ".txt", ""):
            return pd.read_csv(filepath)
        raise ValueError(
            f"Unsupported file format '{p.suffix}'. Use .csv, .xlsx, or .xls."
        )

    def validate(self, df: pd.DataFrame) -> bool:
        """Validate the structure and basic sanity of a coal source DataFrame.

        Performs the following checks in order:

        1. The DataFrame must not be empty (zero rows).
        2. All required quality columns must be present (after name normalisation).
        3. No required quality column may contain negative values — negative
           calorific value, ash content, moisture, or sulfur is physically
           impossible.
        4. Percentage columns (``total_moisture``, ``ash_pct``, ``sulfur_pct``,
           ``volatile_matter_pct``) must not exceed their defined upper bounds
           (e.g. moisture cannot exceed 100 %).
        5. ``volume_available_mt``, if present, must be non-negative.
        6. The DataFrame must contain at least one non-empty coal source row
           (i.e. at least one row where required quality columns are not all
           null).

        Args:
            df: Raw or partially preprocessed coal source DataFrame.

        Returns:
            ``True`` when all validation checks pass.

        Raises:
            ValueError: With a descriptive message for the first failing check.

        Example::

            >>> optimizer = BlendOptimizer()
            >>> optimizer.validate(pd.DataFrame())
            ValueError: Input DataFrame is empty — no coal sources provided.
        """
        if df.empty:
            raise ValueError(
                "Input DataFrame is empty — no coal sources provided."
            )

        # Normalise column names for the check only (immutable — df is not modified)
        normalised_cols = [c.lower().strip().replace(" ", "_") for c in df.columns]
        col_map = dict(zip(normalised_cols, df.columns))

        # 1. Required column presence check
        missing = [c for c in REQUIRED_QUALITY_COLUMNS if c not in normalised_cols]
        if missing:
            raise ValueError(
                f"Missing required columns: {missing}. "
                f"Found columns: {list(df.columns)}"
            )

        # Helper: retrieve the original column name for a normalised name
        def _orig(norm: str) -> str:
            return col_map[norm]

        # 2. Negative value check for quality parameters
        for norm_col in REQUIRED_QUALITY_COLUMNS:
            orig_col = _orig(norm_col)
            col_data = df[orig_col].dropna()
            if (col_data < 0).any():
                bad_vals = col_data[col_data < 0].tolist()
                raise ValueError(
                    f"Column '{orig_col}' contains negative values {bad_vals}. "
                    "Coal quality parameters cannot be negative."
                )

        # 3. Percentage upper-bound check
        for norm_col, upper in QUALITY_UPPER_BOUNDS.items():
            if norm_col not in normalised_cols:
                continue
            orig_col = _orig(norm_col)
            col_data = df[orig_col].dropna()
            if (col_data > upper).any():
                bad_vals = col_data[col_data > upper].tolist()
                raise ValueError(
                    f"Column '{orig_col}' contains values exceeding the physical "
                    f"maximum of {upper}: {bad_vals}."
                )

        # 4. Non-negative volume check
        if "volume_available_mt" in normalised_cols:
            orig_col = _orig("volume_available_mt")
            col_data = df[orig_col].dropna()
            if (col_data < 0).any():
                bad_vals = col_data[col_data < 0].tolist()
                raise ValueError(
                    f"Column '{orig_col}' contains negative volumes {bad_vals}. "
                    "Available tonnage cannot be negative."
                )

        # 5. At least one fully valid source row
        req_orig_cols = [_orig(c) for c in REQUIRED_QUALITY_COLUMNS]
        valid_rows = df[req_orig_cols].dropna(how="all")
        if valid_rows.empty:
            raise ValueError(
                "All rows have missing values for every required quality column. "
                "At least one complete coal source record is required."
            )

        return True

    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean and preprocess raw coal source data.

        Applies the following transformations to a **copy** of the input
        DataFrame (the original is never modified):

        1. Drop rows that are entirely null.
        2. Normalise column names: lower-case, strip whitespace, replace
           spaces with underscores.
        3. Impute missing numeric values with the column median.

        Args:
            df: Raw input :class:`~pandas.DataFrame`.

        Returns:
            A new, cleaned :class:`~pandas.DataFrame` ready for analysis or
            optimisation.  The original *df* is unchanged.

        Example::

            >>> df = pd.DataFrame({"Calorific Value": [6000, None],
            ...                    "ASH PCT": [5.0, 7.0],
            ...                    "Total Moisture": [10.0, None],
            ...                    "Sulfur PCT": [0.5, 0.6]})
            >>> clean = optimizer.preprocess(df)
            >>> list(clean.columns)
            ['calorific_value', 'ash_pct', 'total_moisture', 'sulfur_pct']
        """
        # Work on an isolated copy — immutability guarantee
        working = df.copy()
        working = working.dropna(how="all")
        working = working.rename(
            columns={c: c.lower().strip().replace(" ", "_") for c in working.columns}
        )
        num_cols = working.select_dtypes(include="number").columns
        fill_values = {
            col: working[col].median()
            for col in num_cols
            if working[col].isnull().any()
        }
        if fill_values:
            working = working.fillna(fill_values)
        return working

    def optimize_blend(
        self,
        df: pd.DataFrame,
        target_volume_mt: float = 100_000,
        quality_specs: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> Dict[str, Any]:
        """Find optimal blend ratios using score-based weighted allocation.

        Each coal source is scored by its quality contribution across four
        parameters — calorific value (weight 0.5), ash content (0.2), sulfur
        (0.2), and total moisture (0.1).  Volumes are allocated proportionally
        to these scores while respecting individual source availability caps.
        Any remaining shortfall after the first pass is redistributed among
        uncapped sources.

        The input DataFrame is **not** mutated; a defensive copy is made
        internally before any transformation.

        Args:
            df: Coal source :class:`~pandas.DataFrame` — raw or preprocessed.
                Must contain at minimum the four quality columns:
                ``calorific_value``, ``total_moisture``, ``ash_pct``,
                ``sulfur_pct``.
            target_volume_mt: Total blend volume required, in metric tonnes.
                Must be a positive number.
            quality_specs: Optional mapping that overrides
                :attr:`quality_specs`.  Same schema as
                :data:`DEFAULT_QUALITY_SPECS`.

        Returns:
            Immutable result :class:`dict` with the following keys:

            - ``blend_ratios`` (:class:`dict`) – ``{source_id: ratio_%}``
            - ``blend_volume_mt`` (:class:`dict`) – ``{source_id: volume_mt}``
            - ``blended_quality`` (:class:`dict`) – weighted-average quality
              values keyed by parameter name.
            - ``quality_check`` (:class:`dict`) – per-parameter compliance
              with ``value``, ``min``, ``max``, ``target``, and ``pass`` keys.
            - ``feasible`` (:class:`bool`) – ``True`` if every checked
              quality parameter passes its spec.
            - ``estimated_cost_usd`` (:class:`float`, optional) – total blend
              cost; present only when ``price_usd_t`` column exists.
            - ``blended_price_usd_t`` (:class:`float`, optional) – weighted-
              average price; present only when ``price_usd_t`` column exists.

        Raises:
            ValueError: If *target_volume_mt* is not positive.
            ValueError: If total available supply is less than
                *target_volume_mt*.
            ValueError: If *quality_specs* contains inverted ``min``/``max``
                bounds.

        Example::

            >>> import pandas as pd
            >>> from src.main import BlendOptimizer
            >>> optimizer = BlendOptimizer()
            >>> df = pd.read_csv("sample_data/stockpiles.csv")
            >>> result = optimizer.optimize_blend(df, target_volume_mt=100_000)
            >>> result["feasible"]
            True
        """
        if target_volume_mt <= 0:
            raise ValueError(
                f"target_volume_mt must be positive, got {target_volume_mt}."
            )

        specs = quality_specs or self.quality_specs
        if quality_specs is not None:
            self._validate_quality_specs(quality_specs)

        # Immutable: preprocess works on a copy internally
        working = self.preprocess(df)

        if "source_id" not in working.columns:
            working = working.assign(
                source_id=[f"SOURCE_{i + 1}" for i in range(len(working))]
            )

        if "volume_available_mt" not in working.columns:
            working = working.assign(
                volume_available_mt=target_volume_mt / len(working)
            )

        available: np.ndarray = working["volume_available_mt"].values.clip(0)
        total_available: float = float(available.sum())
        if total_available < target_volume_mt:
            raise ValueError(
                f"Insufficient volume: {total_available:,.0f} MT available, "
                f"{target_volume_mt:,.0f} MT required."
            )

        cv_norm = working["calorific_value"] / (working["calorific_value"].max() + 1e-9)
        ash_norm = 1.0 - (working["ash_pct"] / (working["ash_pct"].max() + 1e-9))
        sulfur_norm = 1.0 - (working["sulfur_pct"] / (working["sulfur_pct"].max() + 1e-9))
        moisture_norm = 1.0 - (
            working["total_moisture"] / (working["total_moisture"].max() + 1e-9)
        )
        scores: pd.Series = (
            cv_norm * 0.5
            + ash_norm * 0.2
            + sulfur_norm * 0.2
            + moisture_norm * 0.1
        )

        raw_alloc = scores / scores.sum() * target_volume_mt
        alloc: np.ndarray = np.minimum(raw_alloc.values, available)
        shortfall: float = target_volume_mt - alloc.sum()
        if shortfall > 0:
            uncapped: np.ndarray = alloc < available
            if uncapped.any():
                extra = scores[uncapped] / scores[uncapped].sum() * shortfall
                alloc[uncapped] += extra.values

        ratios: np.ndarray = alloc / alloc.sum()

        quality_params: Tuple[str, ...] = (
            "calorific_value", "total_moisture", "ash_pct", "sulfur_pct"
        )
        blended_quality: Dict[str, float] = {
            param: float(np.dot(ratios, working[param].values))
            for param in quality_params
            if param in working.columns
        }

        param_spec_map: Dict[str, str] = {
            "calorific_value": "calorific_value_kcal",
            "total_moisture": "total_moisture_pct",
            "ash_pct": "ash_pct",
            "sulfur_pct": "sulfur_pct",
        }
        quality_check: Dict[str, Dict[str, Any]] = {}
        for param, value in blended_quality.items():
            spec_key = param_spec_map.get(param, param)
            if spec_key in specs:
                s = specs[spec_key]
                min_ok = value >= s.get("min", -np.inf)
                max_ok = value <= s.get("max", np.inf)
                quality_check[param] = {
                    "value": round(value, 3),
                    "min": s.get("min"),
                    "max": s.get("max"),
                    "target": s.get("target"),
                    "pass": bool(min_ok and max_ok),
                }

        source_ids: List[str] = list(working["source_id"].astype(str))
        result: Dict[str, Any] = {
            "blend_ratios": dict(zip(source_ids, (ratios * 100).round(2).tolist())),
            "blend_volume_mt": dict(zip(source_ids, alloc.round(1).tolist())),
            "blended_quality": {k: round(v, 3) for k, v in blended_quality.items()},
            "quality_check": quality_check,
            "feasible": all(v["pass"] for v in quality_check.values()),
        }

        if "price_usd_t" in working.columns:
            blended_price = float(np.dot(ratios, working["price_usd_t"].values))
            result = {
                **result,
                "estimated_cost_usd": round(blended_price * target_volume_mt, 2),
                "blended_price_usd_t": round(blended_price, 2),
            }

        return result

    def sensitivity_analysis(
        self,
        df: pd.DataFrame,
        param: str = "calorific_value",
        delta_pct: float = 5.0,
    ) -> pd.DataFrame:
        """Run blend sensitivity analysis by varying a single quality parameter.

        Evaluates nine evenly-spaced scenarios spanning ±*delta_pct* percent
        deviation on the chosen *param* column.  Each scenario re-runs the
        full optimisation and records the resulting blend quality, feasibility,
        and cost.  The input DataFrame is not mutated.

        Args:
            df: Coal source :class:`~pandas.DataFrame`.
            param: Name of the column to perturb.  After preprocessing,
                normalised column names apply (e.g. ``'calorific_value'``,
                ``'ash_pct'``).
            delta_pct: Half-width of the variation range expressed as a
                percentage of the original column values.  Defaults to ``5.0``
                (i.e. a ±5 % sweep).

        Returns:
            :class:`~pandas.DataFrame` with one row per scenario containing:
            ``delta_pct``, ``{param}_mean``, ``blended_cv``, ``blended_ash``,
            ``feasible``, ``cost_usd``.  On failure, a row with an ``error``
            key is inserted instead.

        Raises:
            ValueError: If *delta_pct* is negative.

        Example::

            >>> results = optimizer.sensitivity_analysis(df, param="ash_pct", delta_pct=10)
            >>> results[["delta_pct", "feasible"]]
        """
        if delta_pct < 0:
            raise ValueError(f"delta_pct must be non-negative, got {delta_pct}.")

        base = self.preprocess(df)
        rows: List[Dict[str, Any]] = []
        for delta in np.linspace(-delta_pct, delta_pct, 9):
            # Build modified scenario immutably
            if param in base.columns:
                scenario = base.assign(**{param: base[param] * (1 + delta / 100)})
            else:
                scenario = base.copy()
            try:
                res = self.optimize_blend(scenario)
                rows.append({
                    "delta_pct": round(delta, 1),
                    f"{param}_mean": (
                        round(float(scenario[param].mean()), 2)
                        if param in scenario.columns
                        else None
                    ),
                    "blended_cv": res["blended_quality"].get("calorific_value"),
                    "blended_ash": res["blended_quality"].get("ash_pct"),
                    "feasible": res["feasible"],
                    "cost_usd": res.get("estimated_cost_usd"),
                })
            except Exception as exc:  # noqa: BLE001
                rows.append({"delta_pct": round(delta, 1), "error": str(exc)})
        return pd.DataFrame(rows)

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Run descriptive analysis on coal source data and return summary metrics.

        Preprocesses the input, then computes record count, column names, missing
        value percentages, descriptive statistics (mean, std, min/max/percentiles),
        column totals, and column means for all numeric fields.

        Args:
            df: Raw or preprocessed coal source DataFrame.

        Returns:
            Dict with keys:
                - total_records: Number of rows after preprocessing.
                - columns: List of column names after normalisation.
                - missing_pct: Percentage of null values per column.
                - summary_stats: Descriptive statistics dict (if numeric cols exist).
                - totals: Column sums dict (if numeric cols exist).
                - means: Column means dict (if numeric cols exist).
        """
        df = self.preprocess(df)
        result = {
            "total_records": len(df),
            "columns": list(df.columns),
            "missing_pct": (df.isnull().sum() / len(df) * 100).round(1).to_dict(),
        }
        numeric_df = df.select_dtypes(include="number")
        if not numeric_df.empty:
            result["summary_stats"] = numeric_df.describe().round(3).to_dict()
            result["totals"] = numeric_df.sum().round(2).to_dict()
            result["means"] = numeric_df.mean().round(3).to_dict()
        return result

    def run(self, filepath: str) -> Dict[str, Any]:
        """Run the full analysis pipeline: load data, validate structure, analyze.

        Convenience method that chains load_data(), validate(), and analyze() into
        a single call. Suitable for quick exploratory runs from the command line or
        a Jupyter notebook.

        Args:
            filepath: Path to a CSV or Excel file containing coal source data.

        Returns:
            Analysis result dict as returned by analyze().

        Raises:
            FileNotFoundError: If the file at filepath does not exist.
            ValueError: If the loaded DataFrame fails validation.
        """
        df = self.load_data(filepath)
        self.validate(df)
        return self.analyze(df)

    def to_dataframe(self, result: Dict) -> pd.DataFrame:
        """Convert a result dictionary to a flat two-column DataFrame for export.

        Recursively flattens nested dicts into dotted metric names so the result
        can be written to CSV, Excel, or any tabular format without further
        transformation.

        Args:
            result: Arbitrary result dict (e.g. from optimize_blend or analyze).
                    Nested dicts produce rows with metric names like
                    "quality_check.ash_pct.value".

        Returns:
            DataFrame with columns ["metric", "value"] where each row represents
            one scalar value from the original dict.

        Example:
            >>> result = optimizer.optimize_blend(df)
            >>> flat_df = optimizer.to_dataframe(result)
            >>> flat_df.to_csv("blend_result.csv", index=False)
        """
        rows = []
        for k, v in result.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    rows.append({"metric": f"{k}.{kk}", "value": vv})
            else:
                rows.append({"metric": k, "value": v})
        return pd.DataFrame(rows)


    def constraint_report(self, df: pd.DataFrame, target_volume_mt: float = 100_000) -> pd.DataFrame:
        """
        Generate a constraint report showing which quality parameters are binding.

        For each quality parameter, calculates the weighted-average value,
        distance from target, and margin to min/max limits.

        Args:
            df: Coal source DataFrame.
            target_volume_mt: Total blend volume in metric tonnes.

        Returns:
            DataFrame with parameter, blended_value, target, min, max,
            distance_from_target, headroom_to_max, status (OK/WARNING/BREACH).
        """
        result = self.optimize_blend(df, target_volume_mt=target_volume_mt)
        rows = []
        for param, check in result.get("quality_check", {}).items():
            val = check["value"]
            target = check.get("target")
            mn = check.get("min")
            mx = check.get("max")
            distance = round(val - target, 3) if target is not None else None
            headroom = round(mx - val, 3) if mx is not None else None
            margin_to_min = round(val - mn, 3) if mn is not None else None
            if not check["pass"]:
                status = "BREACH"
            elif headroom is not None and headroom < (mx - mn) * 0.1 if (mx and mn) else False:
                status = "WARNING"
            else:
                status = "OK"
            rows.append({
                "parameter": param,
                "blended_value": val,
                "target": target,
                "min_spec": mn,
                "max_spec": mx,
                "distance_from_target": distance,
                "headroom_to_max": headroom,
                "margin_to_min": margin_to_min,
                "status": status,
            })
        return pd.DataFrame(rows)

    def multi_product_optimize(
        self,
        df: pd.DataFrame,
        products: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Optimise blends for multiple product grades from a shared supply pool.

        Products are processed sequentially in list order.  After each
        product's blend is allocated, the consumed volumes are deducted from
        the remaining supply before the next product is planned.  This greedy
        sequential strategy is appropriate when product grades are ordered by
        priority (highest-priority grade first).

        The input DataFrame is **not** mutated; a working copy is maintained
        internally across iterations.

        Args:
            df: Coal source :class:`~pandas.DataFrame` with a
                ``volume_available_mt`` column.  Sources without this column
                have no supply limit applied.
            products: Non-empty :class:`list` of product specification dicts.
                Each dict supports:

                - ``name`` (:class:`str`) – Product grade label
                  (e.g. ``"6000 NAR"``).
                - ``target_volume_mt`` (:class:`float`) – Required blend
                  volume in metric tonnes.
                - ``quality_specs`` (:class:`dict`, optional) – Per-product
                  quality spec overrides.

        Returns:
            :class:`list` of blend result dicts (one per product), each
            augmented with a ``product_name`` key.

        Raises:
            ValueError: If *products* is empty.
            ValueError: If the sum of all ``target_volume_mt`` values exceeds
                the total available supply.

        Example::

            >>> products = [
            ...     {"name": "6000 NAR", "target_volume_mt": 50_000},
            ...     {"name": "5500 NAR", "target_volume_mt": 30_000},
            ... ]
            >>> results = optimizer.multi_product_optimize(df, products)
            >>> [r["product_name"] for r in results]
            ['6000 NAR', '5500 NAR']
        """
        if not products:
            raise ValueError("products list must contain at least one product.")

        total_required: float = sum(p.get("target_volume_mt", 0) for p in products)
        total_available: float = (
            float(df["volume_available_mt"].sum())
            if "volume_available_mt" in df.columns
            else float("inf")
        )
        if total_required > total_available:
            raise ValueError(
                f"Total required {total_required:,.0f} MT exceeds available "
                f"{total_available:,.0f} MT."
            )

        results: List[Dict[str, Any]] = []
        # Maintain a working copy for sequential supply deduction (immutable pattern)
        remaining = df.copy()
        if "volume_available_mt" in remaining.columns:
            remaining = remaining.assign(
                volume_available_mt=remaining["volume_available_mt"].astype(float)
            )

        for product in products:
            name: str = product.get("name", "Product")
            vol: float = product.get("target_volume_mt", 50_000)
            specs: Optional[Dict[str, Dict[str, float]]] = product.get("quality_specs")

            res = self.optimize_blend(remaining, target_volume_mt=vol, quality_specs=specs)
            results.append({**res, "product_name": name})

            # Deduct consumed volumes for subsequent products
            if "volume_available_mt" in remaining.columns:
                updated_volumes = remaining["volume_available_mt"].copy()
                for src_id, used_vol in res.get("blend_volume_mt", {}).items():
                    mask = remaining["source_id"].astype(str) == str(src_id)
                    updated_volumes = updated_volumes.where(~mask, updated_volumes - float(used_vol))
                remaining = remaining.assign(
                    volume_available_mt=updated_volumes.clip(lower=0)
                )

        return results

    def calculate_blend_environmental_impact(
        self,
        blend_volumes: Dict[str, float],
        source_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Calculate weighted-average environmental impact metrics for a blend.

        Computes the volume-weighted average of SO2 and NOx emissions, ash
        content, sulfur content, and carbon intensity across all sources that
        contributed to the blend.

        Args:
            blend_volumes: Mapping of ``{source_id: volume_mt}`` as returned
                by ``optimize_blend()["blend_volume_mt"]``.  An empty dict
                returns an empty result immediately.
            source_data: List of source emission dicts.  Each dict should
                include ``source_id`` and any subset of:

                - ``so2_emissions_kg_per_mt`` (:class:`float`)
                - ``nox_emissions_kg_per_mt`` (:class:`float`)
                - ``ash_content_percent`` (:class:`float`)
                - ``sulfur_content_percent`` (:class:`float`)
                - ``carbon_intensity_tco2_per_mwh`` (:class:`float`)

                Missing keys default to ``0``.

        Returns:
            :class:`dict` with the following keys:

            - ``so2_emissions_kg_per_mt`` – weighted-average SO2 (kg/MT)
            - ``nox_emissions_kg_per_mt`` – weighted-average NOx (kg/MT)
            - ``ash_content_percent`` – weighted-average ash %
            - ``sulfur_content_percent`` – weighted-average sulfur %
            - ``carbon_intensity_tco2_per_mwh`` – weighted-average carbon
              intensity (tCO2/MWh)
            - ``total_blend_volume_mt`` – total blend volume (int)

            Returns an empty :class:`dict` when *blend_volumes* is empty.
            Returns zero-valued metrics when total volume sums to zero.

        Raises:
            ValueError: If any volume in *blend_volumes* is negative.

        Example::

            >>> blend_vols = {"SRC-001": 30_000, "SRC-002": 70_000}
            >>> emissions = [
            ...     {"source_id": "SRC-001", "so2_emissions_kg_per_mt": 5.2},
            ...     {"source_id": "SRC-002", "so2_emissions_kg_per_mt": 3.8},
            ... ]
            >>> impact = optimizer.calculate_blend_environmental_impact(blend_vols, emissions)
            >>> impact["so2_emissions_kg_per_mt"]
            4.34
        """
        if not blend_volumes:
            return {}

        negative_vols = {k: v for k, v in blend_volumes.items() if v < 0}
        if negative_vols:
            raise ValueError(
                f"blend_volumes contains negative values: {negative_vols}. "
                "All volumes must be non-negative."
            )

        source_lookup: Dict[str, Dict[str, Any]] = {
            str(s.get("source_id", "")): s for s in source_data
        }

        total_volume: float = sum(blend_volumes.values())
        metric_keys: Tuple[str, ...] = (
            "so2_emissions_kg_per_mt",
            "nox_emissions_kg_per_mt",
            "ash_content_percent",
            "sulfur_content_percent",
            "carbon_intensity_tco2_per_mwh",
        )
        zero_metrics: Dict[str, float] = {k: 0.0 for k in metric_keys}

        if total_volume == 0:
            return {**zero_metrics, "total_blend_volume_mt": 0}

        # Accumulate weighted contributions immutably via a running dict
        accum: Dict[str, float] = dict(zero_metrics)
        for source_id, volume in blend_volumes.items():
            source = source_lookup.get(str(source_id), {})
            weight = volume / total_volume
            accum = {
                k: accum[k] + weight * source.get(k, 0.0)
                for k in metric_keys
            }

        return {
            **{k: round(v, 2) for k, v in accum.items()},
            "total_blend_volume_mt": int(total_volume),
        }

    def optimize_blend_for_target_gcv(
        self,
        sources: List[Dict[str, Any]],
        target_gcv_mj_kg: float,
        tolerance: float = 0.5,
    ) -> Dict[str, Any]:
        """Find two-source blend ratios to hit a target gross calorific value.

        Uses a lever-rule solver to identify the pair of sources whose
        available GCV range brackets *target_gcv_mj_kg*.  All valid pairs are
        evaluated and the one with the smallest deviation from the target is
        returned.  Volume constraints are enforced by scaling the ratio when
        the ideal proportion of the high-GCV source would exceed its
        availability.

        Args:
            sources: Non-empty :class:`list` of source dicts.  Each dict must
                include:

                - ``source_id`` (:class:`str`) – Unique identifier.
                - ``gcv_mj_kg`` (:class:`float`) – Gross calorific value in
                  MJ/kg.
                - ``volume_available_mt`` (:class:`float`) – Available tonnage
                  (must be > 0 to be considered).
                - ``cost_usd_per_t`` (:class:`float`, optional) – Unit cost.

            target_gcv_mj_kg: Required blended GCV (MJ/kg).  Must be positive.
            tolerance: Maximum acceptable absolute deviation from the target
                GCV (MJ/kg).  Defaults to ``0.5``.

        Returns:
            On success, a :class:`dict` with:

            - ``blend_ratios`` – ``{source_id: fraction}`` (fractions sum
              to 1.0).
            - ``blended_gcv_mj_kg`` – achieved blended GCV (MJ/kg).
            - ``target_gcv_mj_kg`` – the requested target.
            - ``deviation_mj_kg`` – absolute deviation from target.
            - ``meets_target`` – ``True`` if deviation ≤ *tolerance*.
            - ``total_volume_mt`` – combined volume used.
            - ``blending_cost_usd_per_t`` – weighted-average cost.

            On failure (no valid pair found), returns a dict with
            ``meets_target: False`` and a ``message`` explaining why.

        Raises:
            ValueError: If *sources* is empty.
            ValueError: If *target_gcv_mj_kg* is not positive.
            ValueError: If no source has a non-null ``gcv_mj_kg`` and
                positive ``volume_available_mt``.

        Example::

            >>> sources = [
            ...     {"source_id": "PIT-A", "gcv_mj_kg": 27.0,
            ...      "volume_available_mt": 5000, "cost_usd_per_t": 110},
            ...     {"source_id": "PIT-B", "gcv_mj_kg": 21.0,
            ...      "volume_available_mt": 8000, "cost_usd_per_t": 75},
            ... ]
            >>> result = optimizer.optimize_blend_for_target_gcv(
            ...     sources, target_gcv_mj_kg=24.0
            ... )
            >>> result["blended_gcv_mj_kg"]
            24.0
        """
        if not sources:
            raise ValueError("sources list cannot be empty")
        if target_gcv_mj_kg <= 0:
            raise ValueError(
                f"target_gcv_mj_kg must be positive, got {target_gcv_mj_kg}."
            )

        valid: List[Dict[str, Any]] = [
            s for s in sources
            if s.get("gcv_mj_kg") is not None and s.get("volume_available_mt", 0) > 0
        ]
        if not valid:
            raise ValueError(
                "No valid sources with gcv_mj_kg and positive volume_available_mt."
            )

        valid_sorted: List[Dict[str, Any]] = sorted(
            valid, key=lambda x: x["gcv_mj_kg"], reverse=True
        )

        best_result: Optional[Dict[str, Any]] = None

        for i in range(len(valid_sorted)):
            for j in range(i + 1, len(valid_sorted)):
                high = valid_sorted[i]
                low = valid_sorted[j]

                h_gcv: float = high["gcv_mj_kg"]
                l_gcv: float = low["gcv_mj_kg"]

                if h_gcv == l_gcv:
                    continue

                # Lever-rule: ratio * h_gcv + (1 - ratio) * l_gcv = target
                ratio_high: float = (target_gcv_mj_kg - l_gcv) / (h_gcv - l_gcv)

                if not (0.0 <= ratio_high <= 1.0):
                    continue

                ratio_low: float = 1.0 - ratio_high
                combined_vol: float = (
                    high["volume_available_mt"] + low["volume_available_mt"]
                )
                vol_high_needed: float = ratio_high * combined_vol

                if vol_high_needed > high["volume_available_mt"]:
                    # Scale ratio down to respect high-source availability cap
                    scale: float = high["volume_available_mt"] / vol_high_needed
                    ratio_high = ratio_high * scale
                    ratio_low = 1.0 - ratio_high

                achieved_gcv: float = ratio_high * h_gcv + ratio_low * l_gcv
                deviation: float = abs(achieved_gcv - target_gcv_mj_kg)

                if deviation <= tolerance:
                    total_vol: float = (
                        high["volume_available_mt"] * ratio_high
                        + low["volume_available_mt"] * ratio_low
                    )
                    blended_cost: float = (
                        ratio_high * high.get("cost_usd_per_t", 0.0)
                        + ratio_low * low.get("cost_usd_per_t", 0.0)
                    )
                    candidate: Dict[str, Any] = {
                        "blend_ratios": {
                            high["source_id"]: round(ratio_high, 4),
                            low["source_id"]: round(ratio_low, 4),
                        },
                        "blended_gcv_mj_kg": round(achieved_gcv, 2),
                        "target_gcv_mj_kg": target_gcv_mj_kg,
                        "deviation_mj_kg": round(deviation, 3),
                        "meets_target": deviation <= tolerance,
                        "total_volume_mt": round(total_vol, 0),
                        "blending_cost_usd_per_t": round(blended_cost, 2),
                    }

                    if best_result is None or deviation < best_result["deviation_mj_kg"]:
                        best_result = candidate

        if best_result is None:
            return {
                "blend_ratios": {},
                "blended_gcv_mj_kg": None,
                "target_gcv_mj_kg": target_gcv_mj_kg,
                "meets_target": False,
                "message": "No valid two-source blend found within tolerance.",
            }

        return best_result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_quality_specs(specs: Dict[str, Dict[str, float]]) -> None:
        """Validate that quality specification dicts have sensible bounds.

        Checks every parameter spec for inverted ``min``/``max`` bounds and
        non-negative ``target`` values.

        Args:
            specs: Quality specification mapping as used by
                :meth:`optimize_blend`.

        Raises:
            ValueError: If any parameter has ``min > max`` or a negative
                ``min``, ``max``, or ``target`` for percentage parameters.
        """
        for param, spec in specs.items():
            lo = spec.get("min")
            hi = spec.get("max")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(
                    f"Quality spec for '{param}': min ({lo}) exceeds max ({hi}). "
                    "Infeasible constraint — no blend value can satisfy this."
                )
