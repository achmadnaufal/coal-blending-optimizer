# coal-blending-optimizer

**Domain:** Coal Mining

Optimization toolkit for blending coal batches to achieve target quality specifications while minimizing cost. Uses linear programming to find optimal mixing ratios from multiple coal sources.

## ⚙️ Features

- **Quality Constraint Optimization:** Blend coal to meet specs for ash %, sulfur %, moisture %, BTU/kg
- **Cost Minimization:** Find cheapest mix meeting quality requirements
- **Volatility Handling:** Manage uncertainty in incoming coal quality parameters
- **Batch Tracking:** Monitor historical blend performance vs targets
- **Sensitivity Analysis:** Identify critical parameters and price breakpoints

## 🚀 Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/achmadnaufal/coal-blending-optimizer.git
cd coal-blending-optimizer

# Create Python 3.9+ environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Basic Usage

```python
from coal_optimizer import CoalBlender
import pandas as pd

# Load available coal sources
sources = pd.DataFrame({
    'source_name': ['Indonesia_Pit_A', 'Australia_Queensland', 'South_Africa_Witbank'],
    'ash_pct': [12.5, 8.3, 9.8],
    'sulfur_pct': [0.4, 0.5, 0.6],
    'moisture_pct': [22, 15, 18],
    'btu_per_kg': [5600, 6200, 6100],
    'cost_per_ton': [35, 52, 48]
})

# Define target quality specs
specs = {
    'ash_pct': {'min': 10, 'max': 14},
    'sulfur_pct': {'max': 0.7},
    'moisture_pct': {'max': 20},
    'btu_per_kg': {'min': 5500}
}

# Create optimizer
blender = CoalBlender(sources, specs)

# Find optimal blend
result = blender.optimize(total_output_tons=1000)

print(f"Optimal Blend Cost: ${result['total_cost']:.2f}")
print(f"Blend Composition:")
for source, pct in result['blend_pcts'].items():
    print(f"  {source}: {pct:.1f}%")
print(f"Quality Achieved:")
for param, value in result['blended_quality'].items():
    print(f"  {param}: {value:.2f}")
```

## 📊 Example: Mine Blend Optimization

### Problem Definition

**Available Sources:**
```
Source              Ash%  Sulfur%  BTU/kg  Moisture%  Cost/ton
─────────────────────────────────────────────────────────────────
Indonesia Pit A     12.5   0.4     5600    22%       $35
Australia QLD        8.3   0.5     6200    15%       $52
S. Africa Witbank    9.8   0.6     6100    18%       $48
```

**Target Specifications:**
- Ash: 10–14% ✓ Must meet for boiler efficiency
- Sulfur: ≤0.7% ✓ Environmental compliance
- BTU: ≥5,500 ✓ Energy content requirement
- Moisture: ≤20% ✓ Transport/handling spec

### Solution

```
Optimal Blend:
─────────────────────────
Indonesia Pit A:     40%  (400 tons)
Australia QLD:       35%  (350 tons)
S. Africa Witbank:   25%  (250 tons)
─────────────────────────
Total:              100% (1,000 tons)

Cost Breakdown:
  Indonesia: 400 × $35 = $14,000
  Australia: 350 × $52 = $18,200
  S. Africa: 250 × $48 = $12,000
  ─────────────────────────
  TOTAL:              $44,200 ($44.20/ton)

Quality Achieved:
  Ash: 10.3% ✓ (target: 10–14%)
  Sulfur: 0.49% ✓ (target: ≤0.7%)
  BTU: 5,843 ✓ (target: ≥5,500)
  Moisture: 19.1% ✓ (target: ≤20%)
```

## 🔬 Advanced Features

### Volatility Constraint Handling

```python
# When incoming coal quality varies
volatility = pd.DataFrame({
    'source_name': ['Indonesia_Pit_A', 'Australia_Queensland', 'S_Africa_Witbank'],
    'ash_std_dev': [1.2, 0.8, 0.9],
    'sulfur_std_dev': [0.15, 0.12, 0.18],
    'btu_std_dev': [150, 100, 120]
})

# Include in optimizer
result = blender.optimize(
    total_output_tons=1000,
    volatility_data=volatility,
    confidence_level=0.95  # 95% confidence of meeting specs
)
```

### Price Breakpoint Analysis

```python
# Find cost-quality tradeoff
breakpoints = blender.price_sensitivity_analysis(
    parameter='ash_pct',
    tolerance_range=[10, 11, 12, 13, 14],
    total_output_tons=1000
)

print("Cost vs Ash Tolerance:")
for tolerance, cost in breakpoints.items():
    print(f"  Ash ≤ {tolerance}%: ${cost:.2f}/ton")
```

Output:
```
Cost vs Ash Tolerance:
  Ash ≤ 10%: $48.50/ton (tight constraint)
  Ash ≤ 11%: $46.20/ton
  Ash ≤ 12%: $44.80/ton
  Ash ≤ 13%: $44.20/ton
  Ash ≤ 14%: $43.90/ton (loose constraint)
```

### Batch Tracking & Variance

```python
# Track historical blend performance
history = pd.DataFrame({
    'batch_id': ['B001', 'B002', 'B003'],
    'target_ash': [12.0, 12.0, 12.0],
    'actual_ash': [11.8, 12.3, 12.1],
    'target_sulfur': [0.5, 0.5, 0.5],
    'actual_sulfur': [0.48, 0.52, 0.51]
})

# Analyze variance
variance_report = blender.variance_analysis(history)
print(f"Ash Variance: ±{variance_report['ash_std_dev']:.2f}%")
print(f"Sulfur Variance: ±{variance_report['sulfur_std_dev']:.3f}%")
```

## 📈 Optimization Model

**Objective Function:**
```
Minimize: Σ(source_i × cost_i × blend_pct_i)
```

**Constraints:**
```
Quality Constraints:
  ash_min ≤ Σ(source_ash_i × blend_pct_i) ≤ ash_max
  sulfur_pct_i × blend_pct_i ≤ sulfur_max
  btu_i × blend_pct_i ≥ btu_min
  moisture_i × blend_pct_i ≤ moisture_max

Balance Constraints:
  Σ(blend_pct_i) = 1.0 (100%)
  blend_pct_i ≥ 0 ∀ i (non-negative)
```

## 🧪 Testing

```bash
# Run all tests with edge cases
pytest tests/ -v

# Test constraint validation
pytest tests/test_core.py::TestConstraintValidation -v

# Test optimizer edge cases
pytest tests/test_coal_blending.py::TestOptimizationEdgeCases -v
```

Test Coverage:
- `test_core.py` – Constraint handling, volatile quality parameters
- `test_coal_blending.py` – Optimization edge cases, boundary conditions
- `test_sensitivity.py` – Price sensitivity and what-if analysis

## 📂 Project Structure

```
coal-blending-optimizer/
├── src/
│   ├── coal_optimizer.py      # Main optimization engine
│   ├── constraints.py         # Quality constraint validation
│   └── sensitivity_analysis.py # Price/quality tradeoff
├── data/
│   ├── coal_sources.csv       # Available coal sources
│   ├── specs_standard.json    # Standard quality specs
│   └── sample_blends.csv      # Historical blend records
├── tests/
│   ├── test_core.py
│   ├── test_coal_blending.py
│   └── test_sensitivity.py
├── examples/
│   └── optimize_batch_1000tons.py
├── requirements.txt
└── README.md
```

## 🔧 Configuration

### Quality Specifications (specs_standard.json)

```json
{
  "default": {
    "ash_pct": {"min": 10, "max": 14},
    "sulfur_pct": {"max": 0.7},
    "moisture_pct": {"max": 20},
    "btu_per_kg": {"min": 5500}
  },
  "premium": {
    "ash_pct": {"min": 8, "max": 11},
    "sulfur_pct": {"max": 0.5},
    "moisture_pct": {"max": 18},
    "btu_per_kg": {"min": 5800}
  },
  "export": {
    "ash_pct": {"min": 8, "max": 12},
    "sulfur_pct": {"max": 0.6},
    "moisture_pct": {"max": 18},
    "btu_per_kg": {"min": 5700}
  }
}
```

### Load Custom Specifications

```python
import json

with open('specs_custom.json') as f:
    custom_specs = json.load(f)

result = blender.optimize(
    total_output_tons=1000,
    specifications=custom_specs['premium']
)
```

## 💡 Real-World Scenario

**Daily Optimization at Coal Blend Plant:**

```python
# 1. Load morning quality test results
incoming_coal = read_lab_results('lab_results_2026_03_07.csv')

# 2. Update source qualities in optimizer
blender.update_sources(incoming_coal)

# 3. Calculate optimal blend for 500-ton batch
batch_order = blender.optimize(
    total_output_tons=500,
    production_deadline='14:00'  # Must be ready by 2 PM
)

# 4. Generate blend instruction sheet
blend_instructions = batch_order.to_instruction_sheet()
print(f"""
BLEND BATCH #{batch_order['batch_id']}
Target Output: {batch_order['total_tons']} tons
Ready by: {batch_order['deadline']}

Source Allocations:
  Stockpile A: {batch_order['source_allocations']['A']:.0f} tons
  Stockpile B: {batch_order['source_allocations']['B']:.0f} tons
  Stockpile C: {batch_order['source_allocations']['C']:.0f} tons

Predicted Quality:
  Ash: {batch_order['predicted_quality']['ash']:.1f}%
  Sulfur: {batch_order['predicted_quality']['sulfur']:.2f}%
  BTU: {batch_order['predicted_quality']['btu']:,.0f}
""")

# 5. Send to blend plant operators
send_to_blend_plant(blend_instructions)

# 6. Track actual results when batch completes
actual_quality = measure_batch_quality(batch_order['batch_id'])
log_variance(batch_order, actual_quality)
```

## ⚠️ Important Notes

- **Quality Data Freshness:** Lab results should be <12 hours old
- **Price Updates:** Source costs updated daily from market feeds
- **Seasonal Variations:** Moisture content varies by season; update quarterly
- **Regulatory Changes:** Sulfur limits may change by region/buyer

## 📊 Dependencies

- `pulp` ≥ 2.7 (linear programming)
- `pandas` ≥ 1.3 (data manipulation)
- `numpy` ≥ 1.21 (numerical)

See `requirements.txt` for exact versions.

## 📋 Troubleshooting

**"No feasible solution found"**
→ Quality specs too tight for available sources. Relax one constraint or source new coal.

**"Optimization unstable"**
→ Use volatility data to improve model robustness.

**"High cost for marginal quality gain"**
→ Run sensitivity analysis to find cost-quality breakpoints.

## 📄 License

MIT License

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and improvements.


## Usage Examples

### Optimize Blend for Target GCV

```python
from src.main import CoalBlendingOptimizer

optimizer = CoalBlendingOptimizer()

sources = [
    {"source_id": "PIT-A", "gcv_mj_kg": 27.0, "volume_available_mt": 5000, "cost_usd_per_t": 110},
    {"source_id": "PIT-B", "gcv_mj_kg": 21.0, "volume_available_mt": 8000, "cost_usd_per_t": 75},
    {"source_id": "PIT-C", "gcv_mj_kg": 24.5, "volume_available_mt": 3000, "cost_usd_per_t": 92},
]

result = optimizer.optimize_blend_for_target_gcv(sources, target_gcv_mj_kg=25.0)
print(f"Meets target: {result['meets_target']}")
print(f"Achieved GCV: {result['blended_gcv_mj_kg']:.2f} MJ/kg")
print(f"Blend cost:   ${result['blending_cost_usd_per_t']:.2f}/t")
for src, ratio in result["blend_ratios"].items():
    print(f"  {src}: {ratio*100:.1f}%")
```

Refer to the `tests/` directory for comprehensive example implementations.
