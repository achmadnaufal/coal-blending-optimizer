# Changelog

## [1.4.0] - 2026-03-21

### Added
- **Blend Compliance Checker** (`src/blend_compliance_checker.py`) — ASTM/ISO contract specification validation
  - Checks blend quality against min/max/target specs with configurable warn bands
  - ComplianceStatus enum: PASS, WARN (near limit), FAIL
  - `check_batch()` for multi-lot compliance verification before shipment
  - `summary_table()` for concise compliance dashboard output
  - Auto-generated corrective action recommendations for failed parameters
  - `BlendComplianceReport` and `ParameterCheck` dataclasses
- **Sample data** — `data/blend_compliance_specs.csv` with 4 contract templates (PLN Indonesia, JERA Japan, POSCO Korea, ENEL Italy)
- **Unit tests** — 18 new tests in `tests/test_blend_compliance_checker.py`

## [1.3.0] - 2026-03-15

### Added
- **GCV-Target Blend Optimizer** — `optimize_blend_for_target_gcv()`: Finds optimal two-source blend ratios to hit a target gross calorific value within tolerance; respects volume constraints and calculates blended cost
- **Unit Tests** — 7 new tests in `tests/test_gcv_optimizer.py` covering midpoint blends, ratio validation, unreachable targets, and multi-source selection
- **README** — Added GCV optimization usage example

### Improved
- Docstrings: added `Raises` and `Example` sections to all public methods

## [CURRENT] - 2026-03-07

### Added
- Add constraint handling for volatile coal quality parameters
- Enhanced README with getting started guide
- Comprehensive unit tests for core functions
- Real-world sample data and fixtures

### Improved
- Edge case handling for null/empty inputs
- Boundary condition validation

### Fixed
- Various edge cases and corner scenarios

---

## [2026-03-08]
- Enhanced documentation and examples
- Added unit test fixtures and test coverage
- Added comprehensive docstrings to key functions
- Added error handling for edge cases
- Improved README with setup and usage examples
