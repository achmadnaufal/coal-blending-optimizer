# Coal Blending Optimizer

Coal quality blending optimization to meet product quality targets while minimizing cost.

## Domain Context

In thermal coal mining and trading, coal from different seams or stockpiles has varying quality
(calorific value, moisture, ash, sulfur). Blending multiple sources allows producers to hit
customer specifications (e.g. GAR 6000 kcal/kg, TM <14%, Ash <8%, S <0.8%) consistently.
This tool automates blend ratio calculation using score-based weighted allocation.

## Features
- **Blend optimization**: Score-based allocation to meet CV/moisture/ash/sulfur targets
- **Quality compliance check**: Pass/fail per quality parameter vs specs
- **Cost estimation**: Blended price and total cost calculation
- **Sensitivity analysis**: See how blend quality changes as source quality varies
- **Data ingestion**: CSV/Excel input with automatic column normalization
- **Sample data generator**: Realistic Indonesian coal stockpile data

## Installation

```bash
pip install -r requirements.txt
```

## Quick Start

```python
from src.main import BlendOptimizer
import pandas as pd

optimizer = BlendOptimizer()

# Load stockpile data
df = pd.read_csv("sample_data/stockpiles.csv")

# Optimize blend for 150,000 MT shipment
result = optimizer.optimize_blend(df, target_volume_mt=150_000)

print("Blend Ratios (%):")
for source, ratio in result["blend_ratios"].items():
    print(f"  {source}: {ratio:.1f}%")

print(f"\nBlended CV: {result['blended_quality']['calorific_value']:.0f} kcal/kg")
print(f"Feasible: {result['feasible']}")
print(f"Estimated Cost: USD {result['estimated_cost_usd']:,.0f}")

# Quality compliance
print("\nQuality Check:")
for param, check in result["quality_check"].items():
    status = "✅ PASS" if check["pass"] else "❌ FAIL"
    print(f"  {param}: {check['value']} (target: {check['target']}) {status}")
```

## Sensitivity Analysis

```python
# How does blend quality change if Seam A CV drops by 5%?
sensitivity = optimizer.sensitivity_analysis(df, param="calorific_value", delta_pct=5.0)
print(sensitivity[["delta_pct", "blended_cv", "feasible"]])
```

## Data Format

| Column | Description | Unit |
|--------|-------------|------|
| source_id | Seam/stockpile identifier | - |
| calorific_value | Gross As-Received calorific value | kcal/kg |
| total_moisture | Total moisture content | % |
| ash_pct | Ash content | % |
| sulfur_pct | Total sulfur | % |
| volume_available_mt | Available volume | Metric tonnes |
| price_usd_t | Cost per metric tonne | USD/t |

## Project Structure

```
coal-blending-optimizer/
├── src/
│   ├── main.py           # BlendOptimizer class with optimization logic
│   └── data_generator.py # Sample data generator
├── sample_data/
│   └── stockpiles.csv    # Example Indonesian coal stockpile data
├── tests/
│   └── test_main.py      # Unit tests (pytest)
├── examples/             # Jupyter notebook examples
└── CHANGELOG.md
```

## Running Tests

```bash
pytest tests/ -v
```
