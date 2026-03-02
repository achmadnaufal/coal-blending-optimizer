# Contributing to Coal Blending Optimizer

Thank you for your interest in contributing! This project optimizes coal blend ratios for quality compliance and cost efficiency. Contributions to optimization algorithms, new quality parameters, transport/port modules, and documentation are all welcome.

## Getting Started

1. Fork the repository and clone your fork
2. Create a virtual environment: `python -m venv venv && source venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. Create a branch: `git checkout -b feature/your-feature-name`

## Development Guidelines

- **Linear programming** — optimization uses `scipy.optimize.linprog`; maintain compatibility
- **Tests required** — add `pytest` tests for all new modules in `tests/`
- **Parameter validation** — validate coal quality inputs before optimization
- **Units** — document all units in function docstrings (%, BTU/kg, AUD/tonne, etc.)

## Submitting Changes

1. Run tests: `pytest tests/ -v`
2. Update `CHANGELOG.md`
3. Open a pull request describing the change and any test results

## Domain Context

Quality parameters follow ASTM D5865 (calorific value), ASTM D3177 (sulfur), and ISO 1171 (ash) standards where applicable.

## Reporting Bugs

Open an issue with Python version, OS, input data (anonymized), and the full error traceback.
