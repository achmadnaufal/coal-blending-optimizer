# Changelog

## [1.10.0] - 2026-04-02

### Added
- **StockpileSegregationPlanner** (`src/stockpile_segregation_planner.py`) — Pad-level coal stockpile segregation optimiser with rank compatibility contamination risk and spontaneous heating safety checks
- **Unit tests** — new comprehensive test suite in `tests/test_stockpile_segregation_planner.py`
- **CHANGELOG** updated to v1.10.0

## [1.8.0]
# Changelog

## [1.9.0] - 2026-03-31

### Added
- **Wash Plant Efficiency Calculator** (`src/wash_plant_efficiency_calculator.py`) — DMC/jig coal beneficiation performance assessment
  - `WashabilityFraction` dataclass: float-sink SG fraction with mass%, ash%, moisture%, CV
  - `WashPlantFeed` dataclass: plant ID, feed rate (tph), target product ash, separation technology
  - `WashPlantEfficiencyCalculator` class with linear-up/log-down trapezoidal AUC for theoretical yield
  - `theoretical_max_yield()`: float-sink curve integration to find max yield at target ash (ASTM D4371)
  - `organic_efficiency()`: OE = actual/theoretical yield × 100
  - `compute_ep()`: Ecart Probable Moyen (d75-d25)/2 — sharpness-of-separation metric
  - `partition_curve()`: theoretical Tromp curve (normal CDF approximation, Horner method)
  - `two_product_mass_balance()`: feed/product/reject ash-based mass balance with 2% closure check
  - `evaluate()`: full circuit performance report with Ep classification (excellent/good/fair/poor)
  - Technology-specific default Ep: DMC 0.025, bath 0.040, jig 0.070, spiral 0.080, flotation 0.100
  - Recommendations: DMC wear, medium-to-coal ratio, NGM misplacement, load factor optimisation
- **Unit tests** — 32 new tests in `tests/test_wash_plant_efficiency_calculator.py` (all passing)

### References
- Wills & Finch (2016) Wills' Mineral Processing Technology. 8th ed. Elsevier.
- ASTM D4371 Standard Test Method for Determining the Washability of Coal.
- King (2001) Modelling and Simulation of Mineral Processing Systems.

 - 2026-03-30

### Added
- **Dust Suppression Cost Calculator** (`src/dust_suppression_cost_calculator.py`) — annual cost estimation for coal dust suppression on stockpiles and haul roads
  - 5 suppression methods: water_spray, polymer_binder, bitumen_emulsion, calcium_chloride, lignin_sulphonate
  - Climate-adjusted application frequency: temperature (+5%/5°C above 25°C), rainfall (–10%/500mm above 1000mm), dry coal (<8% moisture: +20%)
  - Cost components: chemical, labour, equipment (Indonesian mine operator rates)
  - `DustSuppressionEstimate.to_dict()`: structured output for reporting
  - `compare_methods()`: all methods ranked by cost-effectiveness ratio (effectiveness/cost)
  - `annual_water_consumption_m3()`: water balance planning helper
  - `cost_per_tonne_suppressed_usd`: efficiency metric when dust generation rate is provided
  - Full input validation: area, temperature, rainfall, moisture ranges
- **Unit tests** — 30 new tests in `tests/test_dust_suppression_cost_calculator.py`

### References
- ACARP (2018) Dust Suppression on Coal Haul Roads: Technical Review. Report C26063.
- IFC (2007) EHS Guidelines for Coal Mining. World Bank Group.
- SNI 5018:2011 Indonesian Coal Mine Environmental Standard — Dust Management.

## [1.7.0] - 2026-03-30

### Added
- **ContractComplianceChecker** (`src/contract_compliance_checker.py`)
  - `ContractComplianceChecker` — validates blended coal quality against contractual Guaranteed/Typical/Rejection specifications with price adjustment computation
  - `ContractParameter` — flexible spec definition with direction (higher/lower better), penalty/bonus rates per unit deviation, bonus caps
  - `ConsignmentComplianceReport` — structured report with per-parameter breakdown, rejection flags, risk tier (green/amber/red), and total financial impact
  - Default GAR 5500 kcal/kg Indonesian export contract template (CV, moisture, ash, sulphur, volatile matter)
  - `check_batch()` + `batch_summary()` for fleet/month compliance reporting
  - Bonus cap enforcement to prevent over-claiming
- **Test Suite** (`tests/test_contract_compliance_checker.py`) — 32 unit tests covering instantiation, acceptance, rejection, price adjustments, bonus capping, risk tiers, and batch operations

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
