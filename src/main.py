"""
Coal blending optimization to meet quality targets and maximize value.

This module provides tools for optimizing coal blend ratios from multiple
source seams/stockpiles to meet product quality specifications (calorific value,
moisture, ash, sulfur) while minimizing cost or maximizing revenue.

Author: github.com/achmadnaufal
"""
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple


DEFAULT_QUALITY_SPECS = {
    "calorific_value_kcal": {"min": 5800, "target": 6000, "max": 6300},
    "total_moisture_pct": {"min": 0, "target": 10, "max": 14},
    "ash_pct": {"min": 0, "target": 6, "max": 8},
    "sulfur_pct": {"min": 0, "target": 0.5, "max": 0.8},
}


class BlendOptimizer:
    """
    Coal quality blending optimizer.

    Solves the blend optimization problem: given N coal sources with known
    quality parameters and costs, find blend ratios that meet product
    quality specifications at minimum cost.

    Args:
        config: Optional configuration dict with keys:
            - quality_specs: Dict of quality parameter targets/limits

    Example:
        >>> optimizer = BlendOptimizer()
        >>> import pandas as pd
        >>> df = pd.read_csv("data/stockpiles.csv")
        >>> result = optimizer.optimize_blend(df, target_volume_mt=100_000)
        >>> print(result["blend_ratios"])
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.quality_specs = self.config.get("quality_specs", DEFAULT_QUALITY_SPECS)

    def load_data(self, filepath: str) -> pd.DataFrame:
        """
        Load coal source data from CSV or Excel file.

        Args:
            filepath: Path to file. Expected columns: source_id, calorific_value,
                      total_moisture, ash_pct, sulfur_pct, volume_available_mt, price_usd_t

        Returns:
            DataFrame with coal source quality and availability data.

        Raises:
            FileNotFoundError: If file does not exist.
        """
        p = Path(filepath)
        if not p.exists():
            raise FileNotFoundError(f"Data file not found: {filepath}")
        if p.suffix in (".xlsx", ".xls"):
            return pd.read_excel(filepath)
        return pd.read_csv(filepath)

    def validate(self, df: pd.DataFrame) -> bool:
        """
        Validate input DataFrame structure.

        Args:
            df: DataFrame to validate.

        Returns:
            True if validation passes.

        Raises:
            ValueError: If DataFrame is empty or missing required columns.
        """
        if df.empty:
            raise ValueError("Input DataFrame is empty")
        required_cols = ["calorific_value", "total_moisture", "ash_pct", "sulfur_pct"]
        df_cols = [c.lower().strip().replace(" ", "_") for c in df.columns]
        missing = [c for c in required_cols if c not in df_cols]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        return True

    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and preprocess input data.

        Standardizes column names, drops fully empty rows, fills missing
        numeric values with column medians.

        Args:
            df: Raw input DataFrame.

        Returns:
            Cleaned DataFrame ready for analysis.
        """
        df = df.copy()
        df.dropna(how="all", inplace=True)
        df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]
        num_cols = df.select_dtypes(include="number").columns
        for col in num_cols:
            if df[col].isnull().any():
                df[col].fillna(df[col].median(), inplace=True)
        return df

    def optimize_blend(
        self,
        df: pd.DataFrame,
        target_volume_mt: float = 100_000,
        quality_specs: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Find optimal blend ratios using score-based weighted allocation.

        Sources are scored by quality contribution (CV, ash, sulfur, moisture),
        then volumes are allocated proportionally with availability constraints.

        Args:
            df: DataFrame with coal source parameters (preprocessed or raw).
            target_volume_mt: Total blend volume required in metric tonnes.
            quality_specs: Override default quality specification targets.

        Returns:
            Dict with:
                - blend_ratios: {source_id: ratio_pct}
                - blend_volume_mt: {source_id: volume_mt}
                - blended_quality: weighted-average quality values
                - quality_check: pass/fail per quality parameter
                - feasible: True if all quality targets met
                - estimated_cost_usd: total cost (if price_usd_t present)

        Raises:
            ValueError: If total available volume < target_volume_mt.
        """
        df = self.preprocess(df)
        specs = quality_specs or self.quality_specs

        if "source_id" not in df.columns:
            df["source_id"] = [f"SOURCE_{i+1}" for i in range(len(df))]

        if "volume_available_mt" not in df.columns:
            df["volume_available_mt"] = target_volume_mt / len(df)

        available = df["volume_available_mt"].values.clip(0)
        total_available = available.sum()
        if total_available < target_volume_mt:
            raise ValueError(
                f"Insufficient volume: {total_available:,.0f} MT available, "
                f"{target_volume_mt:,.0f} MT required"
            )

        cv_norm = df["calorific_value"] / (df["calorific_value"].max() + 1e-9)
        ash_norm = 1 - (df["ash_pct"] / (df["ash_pct"].max() + 1e-9))
        sulfur_norm = 1 - (df["sulfur_pct"] / (df["sulfur_pct"].max() + 1e-9))
        moisture_norm = 1 - (df["total_moisture"] / (df["total_moisture"].max() + 1e-9))
        scores = (cv_norm * 0.5 + ash_norm * 0.2 + sulfur_norm * 0.2 + moisture_norm * 0.1)

        raw_alloc = scores / scores.sum() * target_volume_mt
        alloc = np.minimum(raw_alloc.values, available)
        shortfall = target_volume_mt - alloc.sum()
        if shortfall > 0:
            uncapped = alloc < available
            if uncapped.any():
                extra = scores[uncapped] / scores[uncapped].sum() * shortfall
                alloc[uncapped] += extra.values

        ratios = alloc / alloc.sum()

        quality_params = ["calorific_value", "total_moisture", "ash_pct", "sulfur_pct"]
        blended_quality = {}
        for param in quality_params:
            if param in df.columns:
                blended_quality[param] = float(np.dot(ratios, df[param].values))

        param_spec_map = {
            "calorific_value": "calorific_value_kcal",
            "total_moisture": "total_moisture_pct",
            "ash_pct": "ash_pct",
            "sulfur_pct": "sulfur_pct",
        }
        quality_check = {}
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

        result = {
            "blend_ratios": dict(zip(df["source_id"].astype(str), (ratios * 100).round(2))),
            "blend_volume_mt": dict(zip(df["source_id"].astype(str), alloc.round(1))),
            "blended_quality": {k: round(v, 3) for k, v in blended_quality.items()},
            "quality_check": quality_check,
            "feasible": all(v["pass"] for v in quality_check.values()),
        }

        if "price_usd_t" in df.columns:
            result["estimated_cost_usd"] = round(
                float(np.dot(ratios, df["price_usd_t"].values) * target_volume_mt), 2
            )
            result["blended_price_usd_t"] = round(
                float(np.dot(ratios, df["price_usd_t"].values)), 2
            )

        return result

    def sensitivity_analysis(
        self, df: pd.DataFrame, param: str = "calorific_value", delta_pct: float = 5.0
    ) -> pd.DataFrame:
        """
        Run blend sensitivity analysis by varying a quality parameter.

        Args:
            df: Coal source DataFrame.
            param: Column to vary (e.g. 'calorific_value', 'ash_pct').
            delta_pct: Variation range ±delta_pct percent.

        Returns:
            DataFrame with scenario results across the variation range.
        """
        df = self.preprocess(df)
        rows = []
        for delta in np.linspace(-delta_pct, delta_pct, 9):
            df_mod = df.copy()
            if param in df_mod.columns:
                df_mod[param] = df_mod[param] * (1 + delta / 100)
            try:
                res = self.optimize_blend(df_mod)
                rows.append({
                    "delta_pct": round(delta, 1),
                    f"{param}_mean": round(df_mod[param].mean(), 2) if param in df_mod.columns else None,
                    "blended_cv": res["blended_quality"].get("calorific_value"),
                    "blended_ash": res["blended_quality"].get("ash_pct"),
                    "feasible": res["feasible"],
                    "cost_usd": res.get("estimated_cost_usd"),
                })
            except Exception as e:
                rows.append({"delta_pct": round(delta, 1), "error": str(e)})
        return pd.DataFrame(rows)

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Run descriptive analysis and return summary metrics."""
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
        """Full pipeline: load → validate → analyze."""
        df = self.load_data(filepath)
        self.validate(df)
        return self.analyze(df)

    def to_dataframe(self, result: Dict) -> pd.DataFrame:
        """Convert result dict to flat DataFrame for export."""
        rows = []
        for k, v in result.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    rows.append({"metric": f"{k}.{kk}", "value": vv})
            else:
                rows.append({"metric": k, "value": v})
        return pd.DataFrame(rows)
