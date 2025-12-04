# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.3.0] - 2026-03-05
### Added
- `constraint_report()`: per-parameter quality constraint report with OK/WARNING/BREACH status
- `multi_product_optimize()`: optimize blends for multiple product grades from shared stockpile
- 7 new unit tests covering constraint report and multi-product optimization
### Improved
- README updated with constraint reporting and multi-product workflow examples

## [1.2.0] - 2026-03-04
### Added
- `optimize_blend()` method with score-based weighted allocation across N coal sources
- Quality compliance checker: pass/fail per parameter vs specs (CV, moisture, ash, sulfur)
- Cost estimation: blended price per tonne and total shipment cost
- `sensitivity_analysis()` method to model blend quality under source variation
- Realistic sample data: 8 Indonesian coal seams with full quality parameters
- Comprehensive unit tests (16 test cases covering validation, optimization, sensitivity)
- Improved README with domain context, data format table, and usage examples
### Fixed
- `validate()` now checks for required quality columns explicitly
- `preprocess()` fills missing numeric values with column medians (not dropped)
- `load_data()` raises FileNotFoundError with clear message if file missing
## [1.1.0] - 2026-03-02
### Added
- Add real-time quality KPI tracking and Pareto optimization
- Improved unit test coverage
- Enhanced documentation with realistic examples
