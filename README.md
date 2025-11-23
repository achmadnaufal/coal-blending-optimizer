# Coal Blending Optimizer

Coal blending optimization to meet quality targets and maximize value

## Features
- Data ingestion from CSV/Excel input files
- Automated analysis and KPI calculation
- Summary statistics and trend reporting
- Sample data generator for testing and development

## Installation

```bash
pip install -r requirements.txt
```

## Quick Start

```python
from src.main import BlendOptimizer

analyzer = BlendOptimizer()
df = analyzer.load_data("data/sample.csv")
result = analyzer.analyze(df)
print(result)
```

## Data Format

Expected CSV columns: `source_id, calorific_value, total_moisture, ash_pct, sulfur_pct, volume_available, price_usd_t`

## Project Structure

```
coal-blending-optimizer/
├── src/
│   ├── main.py          # Core analysis logic
│   └── data_generator.py # Sample data generator
├── data/                # Data directory (gitignored for real data)
├── examples/            # Usage examples
├── requirements.txt
└── README.md
```

## License

MIT License — free to use, modify, and distribute.

## 🚀 New Features (2026-03-02)
- Add real-time quality KPI tracking and Pareto optimization
- Enhanced error handling and edge case coverage
- Comprehensive unit tests and integration examples
