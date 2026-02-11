# Changelog

## [1.6.0] - 2026-03-26

### Added
- **TransportCostOptimizer** (`src/transport_cost_optimizer.py`) — mine-to-port logistics cost modelling
  - Multi-modal leg modelling: haul truck, conveyor, rail, barge, vessel
  - Full cost breakdown: variable (rate × distance) + fixed handling per leg
  - GHG emission intensity per leg (IPCC 2006 / IMO 4th GHG Study factors)
  - Bottleneck identification: minimum effective capacity across supply chain
  - Capacity utilisation and feasibility check per route
  - `compare_routes()`: rank multiple routes by landed cost (USD/tonne)
  - `sensitivity_analysis()`: landed cost vs volume range (±% variation, N steps)
  - Incoterms 2020 support: FOB / CFR / CIF / DAP cost boundaries
  - Marine insurance cost modelled as % of coal cargo value
  - `LogisticsRoute` aggregates full chain: mine FOB + legs + port charges + insurance
- Unit tests: 13 new tests in `tests/test_transport_cost_optimizer.py`

## [1.5.0] - 2026-03-22

### Added
- **Washability Analyzer** (`src/washability_analyzer.py`) — DMS yield-vs-ash curve modeling from float-sink data
  - `FloatSinkFraction` dataclass for structured float-sink test input (weight%, ash%, optional sulfur/GCV per fraction)
  - `WashabilityResult` dataclass with yield, ash, sulfur, GCV, NGM index, and separability index
  - `analyze_at_density()` — theoretical product characteristics at any cut SG using linear boundary-fraction splitting
  - `find_density_for_target_ash()` — back-calculates optimal cut density for a given clean coal ash specification
  - `generate_curve()` — sweeps SG range to produce full yield-ash washability curve for visualization
  - `raw_coal_characteristics()` — weighted mean properties of the raw (as-received) feed coal
  - Near-gravity material (NGC) index calculation per ±0.1 SG band
  - Separability index (refuse_ash / clean_ash) as a process difficulty indicator
  - Weight-sum validation on construction (configurable tolerance)
- **Unit tests** — 23 new tests in `tests/test_washability_analyzer.py`

### References
- ASTM D4371 Standard Test Method for Float-and-Sink Analysis of Coal
- Osborne (1988) Coal Preparation Technology, Graham & Trotman
- Sanders & Schapman (1999) Washability Analysis in Coal Preparation

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

## [1.9.0] - 2026-03-27

### Added
- **Port Inventory Planner** (`src/port_inventory_planner.py`) — Multi-product coal export terminal inventory management
  - `CoalProduct` dataclass: product code, GCV, ash, moisture, price, storage category (general/low_rank/premium)
  - `InventoryTransaction` dataclass: receipt/loading/adjustment types, day-indexed, `is_inflow` property
  - `VesselOrder` dataclass: vessel ID, product, quantity, loading day, tolerance±%; computed `loading_hours`, `min_quantity`, `max_quantity` properties
  - `StockpileConstraints` dataclass: total capacity, pad capacity by product, reclaim/stacking rates, max simultaneous vessels
  - `PortInventoryPlanner` class (7–90 day horizon, configurable safety stock days)
  - `register_product()`, `set_opening_stock()`, `set_constraints()`, `add_transaction()`, `add_vessel_order()` setup methods
  - `inventory_at_day()`: point-in-time balance computation (receipts + adjustments - vessel loadings), floored at 0
  - `projection()`: day-by-day inventory flow table with opening/closing balance, receipts, loadings, safety stock alert
  - `check_vessel_feasibility()`: stock availability vs vessel requirement with tolerance, loading rate vs reclaimer capacity checks
  - `capacity_utilisation()`: total stock vs max capacity with congestion alert at 85% threshold
  - `days_of_stock()`: remaining days of stock based on projected demand rate
  - `export_plan_summary()`: fleet-level summary with per-vessel feasibility, total tonnage, all_feasible flag
  - Safety stock target: average daily demand × safety_stock_days
- **Unit tests** — 44 new tests in `tests/test_port_inventory_planner.py` (all passing)

### References
- Stopford (2009) Maritime Economics. 3rd ed. Routledge
- DNV GL (2018) Terminal Operations Guidelines — Coal Port Capacity Planning
- Silver et al. (2017) Inventory and Production Management in Supply Chains. 4th ed.
