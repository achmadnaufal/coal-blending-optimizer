"""
CLI demo for coal-blending-optimizer.
Demonstrates the full optimization pipeline with sample data.

Usage:
    python examples/cli_demo.py
    python examples/cli_demo.py --data sample_data/stockpiles.csv
    python examples/cli_demo.py --target-volume 80000
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.main import BlendOptimizer
from src.blend_compliance_checker import BlendComplianceChecker
import pandas as pd


def main():
    parser = argparse.ArgumentParser(
        description="Coal Blending Optimizer - find optimal blend ratios to meet quality specs"
    )
    parser.add_argument(
        "--data",
        default="sample_data/stockpiles.csv",
        help="Path to coal source CSV (default: sample_data/stockpiles.csv)",
    )
    parser.add_argument(
        "--target-volume",
        type=float,
        default=100_000,
        help="Target blend volume in metric tonnes (default: 100000)",
    )
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = Path(__file__).parent.parent / data_path

    print("=" * 65)
    print("  COAL BLENDING OPTIMIZER")
    print("=" * 65)

    # Load data
    optimizer = BlendOptimizer()
    df = optimizer.load_data(str(data_path))
    print(f"\n[1] Loaded {len(df)} coal sources from {args.data}")
    print(f"    Columns: {list(df.columns)}")

    # Show source summary
    print(f"\n[2] Source Quality Summary:")
    print(f"    {'Source':<10} {'CV (kcal)':<12} {'Moisture%':<12} {'Ash%':<8} {'Sulfur%':<10} {'Avail (MT)':<12} {'Price $/t'}")
    print(f"    {'-'*10} {'-'*12} {'-'*12} {'-'*8} {'-'*10} {'-'*12} {'-'*9}")
    for _, row in df.iterrows():
        print(
            f"    {row['source_id']:<10} {row['calorific_value']:<12,.0f} "
            f"{row['total_moisture']:<12.1f} {row['ash_pct']:<8.1f} "
            f"{row['sulfur_pct']:<10.2f} {row['volume_available_mt']:<12,.0f} "
            f"{row['price_usd_t']:.1f}"
        )

    # Run optimization
    target_mt = args.target_volume
    print(f"\n[3] Optimizing blend for {target_mt:,.0f} MT target volume...")
    result = optimizer.optimize_blend(df, target_volume_mt=target_mt)

    print(f"\n[4] Blend Ratios:")
    print(f"    {'Source':<10} {'Ratio (%)':<12} {'Volume (MT)':<14}")
    print(f"    {'-'*10} {'-'*12} {'-'*14}")
    for src in result["blend_ratios"]:
        ratio = result["blend_ratios"][src]
        vol = result["blend_volume_mt"][src]
        print(f"    {src:<10} {ratio:<12.2f} {vol:<14,.1f}")

    # Blended quality
    print(f"\n[5] Blended Quality:")
    for param, val in result["blended_quality"].items():
        print(f"    {param:<20} {val:.3f}")

    # Quality check
    print(f"\n[6] Quality Compliance Check:")
    print(f"    {'Parameter':<20} {'Value':<10} {'Min':<8} {'Max':<8} {'Target':<8} {'Status'}")
    print(f"    {'-'*20} {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")
    for param, check in result["quality_check"].items():
        status_str = "PASS" if check["pass"] else "FAIL"
        print(
            f"    {param:<20} {check['value']:<10.3f} "
            f"{check['min'] if check['min'] is not None else 'N/A':<8} "
            f"{check['max'] if check['max'] is not None else 'N/A':<8} "
            f"{check['target'] if check['target'] is not None else 'N/A':<8} "
            f"{status_str}"
        )

    feasible_str = "YES - All specs met" if result["feasible"] else "NO - Spec violations detected"
    print(f"\n    Feasible: {feasible_str}")

    if "estimated_cost_usd" in result:
        print(f"    Estimated Cost: ${result['estimated_cost_usd']:,.2f}")
        print(f"    Blended Price:  ${result['blended_price_usd_t']:.2f}/t")

    # Constraint report
    print(f"\n[7] Constraint Report:")
    constraint_df = optimizer.constraint_report(df, target_volume_mt=target_mt)
    print(f"    {'Parameter':<20} {'Blended':<10} {'Target':<10} {'Headroom':<10} {'Status'}")
    print(f"    {'-'*20} {'-'*10} {'-'*10} {'-'*10} {'-'*8}")
    for _, row in constraint_df.iterrows():
        print(
            f"    {row['parameter']:<20} {row['blended_value']:<10.3f} "
            f"{row['target'] if row['target'] is not None else 'N/A':<10} "
            f"{row['headroom_to_max'] if row['headroom_to_max'] is not None else 'N/A':<10} "
            f"{row['status']}"
        )

    # Compliance check
    print(f"\n[8] Blend Compliance Report:")
    specs = {
        "calorific_value_kcal": {"min": 5800, "max": 6200, "target": 6000},
        "total_moisture_pct": {"max": 14.0},
        "ash_pct": {"max": 8.0},
        "sulfur_pct": {"max": 0.8},
    }
    checker = BlendComplianceChecker(specs=specs)
    blend_quality = {
        "calorific_value_kcal": result["blended_quality"].get("calorific_value", 0),
        "total_moisture_pct": result["blended_quality"].get("total_moisture", 0),
        "ash_pct": result["blended_quality"].get("ash_pct", 0),
        "sulfur_pct": result["blended_quality"].get("sulfur_pct", 0),
    }
    report = checker.check(blend_id="BLEND-2024-001", blend_quality=blend_quality)
    print(f"    Blend ID:       {report.blend_id}")
    print(f"    Overall Status: {report.overall_status.value.upper()}")
    print(f"    Compliance:     {report.compliance_pct:.0f}%")
    if report.recommendations:
        print(f"    Recommendations:")
        for rec in report.recommendations:
            print(f"      - {rec}")

    print(f"\n{'=' * 65}")
    print(f"  Optimization complete.")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
