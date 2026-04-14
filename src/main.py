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
        df = df.dropna(how="all")
        df = df.rename(columns={c: c.lower().strip().replace(" ", "_") for c in df.columns})
        num_cols = df.select_dtypes(include="number").columns
        fill_values = {
            col: df[col].median()
            for col in num_cols
            if df[col].isnull().any()
        }
        if fill_values:
            df = df.fillna(fill_values)
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
        self, df: pd.DataFrame, products: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Optimize blends for multiple product grades simultaneously.

        Args:
            df: Coal source DataFrame with volume_available_mt.
            products: List of dicts, each with keys:
                - name: Product grade name (e.g. "6000 NAR")
                - target_volume_mt: Volume required
                - quality_specs: (optional) Override specs for this product

        Returns:
            List of blend result dicts, one per product, with 'product_name' added.

        Raises:
            ValueError: If total required volume exceeds available supply.
        """
        total_required = sum(p.get("target_volume_mt", 0) for p in products)
        total_available = df["volume_available_mt"].sum() if "volume_available_mt" in df.columns else float("inf")
        if total_required > total_available:
            raise ValueError(
                f"Total required {total_required:,.0f} MT exceeds available {total_available:,.0f} MT"
            )
        results = []
        remaining_df = df.copy()
        if "volume_available_mt" in remaining_df.columns:
            remaining_df["volume_available_mt"] = remaining_df["volume_available_mt"].astype(float)
        for product in products:
            name = product.get("name", "Product")
            vol = product.get("target_volume_mt", 50_000)
            specs = product.get("quality_specs")
            res = self.optimize_blend(remaining_df, target_volume_mt=vol, quality_specs=specs)
            res["product_name"] = name
            results.append(res)
            # Reduce available volume for subsequent products
            if "volume_available_mt" in remaining_df.columns:
                for src_id, used_vol in res.get("blend_volume_mt", {}).items():
                    mask = remaining_df["source_id"].astype(str) == str(src_id)
                    remaining_df.loc[mask, "volume_available_mt"] -= float(used_vol)
                remaining_df["volume_available_mt"] = remaining_df["volume_available_mt"].clip(0)
        return results

    def calculate_blend_environmental_impact(self, blend_volumes: dict, source_data: list) -> dict:
        """
        Calculate environmental impact metrics of blended coal product.
        
        Computes blended SO2/NOx emissions, ash content, and carbon intensity
        based on constituent coal sources.
        
        Args:
            blend_volumes: Dict of {source_id: volume_mt}
            source_data: List of dicts with source emissions and ash data
            
        Returns:
            Dict with blended environmental metrics
        """
        if not blend_volumes:
            return {}
        
        # Create lookup for source data
        source_dict = {str(s.get("source_id", "")): s for s in source_data}
        
        total_volume = sum(blend_volumes.values())
        blended_metrics = {
            "so2_emissions_kg_per_mt": 0,
            "nox_emissions_kg_per_mt": 0,
            "ash_content_percent": 0,
            "sulfur_content_percent": 0,
            "carbon_intensity_tco2_per_mwh": 0,
        }
        
        if total_volume == 0:
            return blended_metrics
        
        # Calculate weighted average emissions
        for source_id, volume in blend_volumes.items():
            source = source_dict.get(str(source_id), {})
            weight = volume / total_volume
            
            blended_metrics["so2_emissions_kg_per_mt"] += weight * source.get("so2_emissions_kg_per_mt", 0)
            blended_metrics["nox_emissions_kg_per_mt"] += weight * source.get("nox_emissions_kg_per_mt", 0)
            blended_metrics["ash_content_percent"] += weight * source.get("ash_content_percent", 0)
            blended_metrics["sulfur_content_percent"] += weight * source.get("sulfur_content_percent", 0)
            blended_metrics["carbon_intensity_tco2_per_mwh"] += weight * source.get("carbon_intensity_tco2_per_mwh", 0)
        
        # Round to appropriate precision
        blended_metrics = {k: round(v, 2) for k, v in blended_metrics.items()}
        blended_metrics["total_blend_volume_mt"] = int(total_volume)
        
        return blended_metrics

    def optimize_blend_for_target_gcv(
        self,
        sources: list,
        target_gcv_mj_kg: float,
        tolerance: float = 0.5,
    ) -> dict:
        """
        Determine blending ratios to hit a target GCV specification.

        Uses a weighted-average solver to find the simplest two-source blend
        that meets the target GCV within tolerance.

        Args:
            sources: List of dicts, each with keys:
                - source_id (str)
                - gcv_mj_kg (float)
                - volume_available_mt (float)
                - cost_usd_per_t (float, optional)
            target_gcv_mj_kg: Required GCV of blended product (MJ/kg)
            tolerance: Acceptable deviation from target (MJ/kg), default ±0.5

        Returns:
            Dict with blend_ratios (source_id → fraction), blended_gcv,
            meets_target, total_volume_mt, and blending_cost_usd_per_t

        Raises:
            ValueError: If sources list is empty or target_gcv_mj_kg <= 0

        Example:
            >>> sources = [
            ...     {"source_id": "PIT-A", "gcv_mj_kg": 27.0, "volume_available_mt": 5000, "cost_usd_per_t": 110},
            ...     {"source_id": "PIT-B", "gcv_mj_kg": 21.0, "volume_available_mt": 8000, "cost_usd_per_t": 75},
            ... ]
            >>> result = optimizer.optimize_blend_for_target_gcv(sources, target_gcv_mj_kg=24.0)
            >>> print(result["blended_gcv"])  # ~24.0
        """
        if not sources:
            raise ValueError("sources list cannot be empty")
        if target_gcv_mj_kg <= 0:
            raise ValueError("target_gcv_mj_kg must be positive")

        # Filter out sources with no GCV data
        valid = [s for s in sources if s.get("gcv_mj_kg") is not None and s.get("volume_available_mt", 0) > 0]
        if not valid:
            raise ValueError("No valid sources with gcv_mj_kg and positive volume")

        # Sort by GCV descending
        valid_sorted = sorted(valid, key=lambda x: x["gcv_mj_kg"], reverse=True)

        best_result = None

        # Try all pairs of sources for two-source blend
        for i in range(len(valid_sorted)):
            for j in range(i + 1, len(valid_sorted)):
                high = valid_sorted[i]
                low = valid_sorted[j]

                h_gcv = high["gcv_mj_kg"]
                l_gcv = low["gcv_mj_kg"]

                if h_gcv == l_gcv:
                    continue

                # Solve: ratio * h_gcv + (1 - ratio) * l_gcv = target
                ratio_high = (target_gcv_mj_kg - l_gcv) / (h_gcv - l_gcv)

                if not (0 <= ratio_high <= 1):
                    continue

                ratio_low = 1.0 - ratio_high

                # Check volume constraints
                vol_high_needed = ratio_high * (high["volume_available_mt"] + low["volume_available_mt"])
                vol_low_needed = ratio_low * (high["volume_available_mt"] + low["volume_available_mt"])

                if vol_high_needed > high["volume_available_mt"]:
                    # Scale down to available volumes
                    scale = high["volume_available_mt"] / vol_high_needed
                    ratio_high *= scale
                    ratio_low = 1 - ratio_high

                achieved_gcv = ratio_high * h_gcv + ratio_low * l_gcv
                deviation = abs(achieved_gcv - target_gcv_mj_kg)

                if deviation <= tolerance:
                    total_vol = high["volume_available_mt"] * ratio_high + low["volume_available_mt"] * ratio_low
                    cost_high = high.get("cost_usd_per_t", 0)
                    cost_low = low.get("cost_usd_per_t", 0)
                    blended_cost = ratio_high * cost_high + ratio_low * cost_low

                    result = {
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
                        best_result = result

        if best_result is None:
            return {
                "blend_ratios": {},
                "blended_gcv_mj_kg": None,
                "target_gcv_mj_kg": target_gcv_mj_kg,
                "meets_target": False,
                "message": "No valid two-source blend found within tolerance",
            }

        return best_result
