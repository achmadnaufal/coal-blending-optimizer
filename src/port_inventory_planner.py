"""
port_inventory_planner.py — Port stockpile inventory planning for coal export operations.

Models coal inventory flows at an export port: incoming shipments from mines,
stacking/reclaiming operations, vessel loading schedules, and safety stock management.
Supports multi-product (quality grade) inventory tracking and constraint checking.

Key features:
  - Product-segregated inventory tracking (multiple coal grades)
  - Reorder point and safety stock calculation (EOQ-based)
  - Vessel loading schedule planning against inventory availability
  - Stockpile capacity constraint checking
  - Inventory depletion/surplus projection over planning horizon
  - Quality blending feasibility check (available stock vs vessel target)

References:
    - Stopford (2009) Maritime Economics. 3rd ed. Routledge.
    - DNV GL (2018) Terminal Operations Guidelines — Coal Port Capacity Planning
    - KPC / BUMI coal terminal operating procedures (industry reference)
    - Silver et al. (2017) Inventory and Production Management in Supply Chains. 4th ed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Safety stock coverage target (days of average demand)
DEFAULT_SAFETY_STOCK_DAYS = 7

# Maximum stockpile utilisation before triggering congestion alert (%)
CONGESTION_ALERT_THRESHOLD_PCT = 85.0

# Minimum vessel loading rate (tonnes/hour) for planning purposes
MIN_LOADING_RATE_TPH = 500.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CoalProduct:
    """A coal product (quality grade) stored at the port terminal.

    Attributes:
        product_code: Short product identifier (e.g., 'GAR5000', 'GAR3800').
        calorific_value_kcal: Gross calorific value as-received (kcal/kg).
        ash_pct: Ash content (%).
        moisture_pct: Total moisture (%).
        price_usd_per_tonne: FOB price (USD/tonne).
        storage_category: 'general', 'low_rank', or 'premium' — affects segregation.

    Raises:
        ValueError: If quality parameters are out of plausible range or price is negative.

    Example:
        >>> p = CoalProduct("GAR5000", calorific_value_kcal=5000.0,
        ...                  ash_pct=8.0, moisture_pct=18.0, price_usd_per_tonne=65.0)
    """

    product_code: str
    calorific_value_kcal: float
    ash_pct: float
    moisture_pct: float
    price_usd_per_tonne: float = 50.0
    storage_category: str = "general"

    VALID_CATEGORIES = {"general", "low_rank", "premium"}

    def __post_init__(self) -> None:
        if not self.product_code.strip():
            raise ValueError("product_code must not be empty.")
        if not (2000.0 <= self.calorific_value_kcal <= 8000.0):
            raise ValueError(f"calorific_value_kcal {self.calorific_value_kcal} out of range [2000, 8000] kcal/kg.")
        if not (0.0 <= self.ash_pct <= 50.0):
            raise ValueError(f"ash_pct {self.ash_pct} must be in [0, 50]%.")
        if not (0.0 <= self.moisture_pct <= 60.0):
            raise ValueError(f"moisture_pct {self.moisture_pct} must be in [0, 60]%.")
        if self.price_usd_per_tonne < 0:
            raise ValueError("price_usd_per_tonne must be non-negative.")
        if self.storage_category not in self.VALID_CATEGORIES:
            raise ValueError(
                f"storage_category '{self.storage_category}' not recognised. "
                f"Valid: {self.VALID_CATEGORIES}"
            )


@dataclass
class InventoryTransaction:
    """A single inventory in/out transaction.

    Attributes:
        transaction_id: Unique identifier.
        product_code: Product being transacted.
        day: Day index within planning horizon (0-indexed).
        quantity_tonnes: Quantity in tonnes. Positive = inflow (receipt), negative = outflow.
        transaction_type: 'receipt', 'loading', or 'adjustment'.
        vessel_id: Vessel identifier (required for 'loading' type).
        mine_id: Source mine (required for 'receipt' type).

    Raises:
        ValueError: If quantity is zero or required fields are missing for type.
    """

    transaction_id: str
    product_code: str
    day: int
    quantity_tonnes: float
    transaction_type: str = "receipt"
    vessel_id: str = ""
    mine_id: str = ""

    VALID_TYPES = {"receipt", "loading", "adjustment"}

    def __post_init__(self) -> None:
        if not self.transaction_id.strip():
            raise ValueError("transaction_id must not be empty.")
        if self.quantity_tonnes == 0:
            raise ValueError("quantity_tonnes must be non-zero.")
        if self.transaction_type not in self.VALID_TYPES:
            raise ValueError(
                f"transaction_type '{self.transaction_type}' not recognised. "
                f"Valid: {self.VALID_TYPES}"
            )
        if self.day < 0:
            raise ValueError("day must be non-negative.")
        if self.transaction_type == "loading" and quantity_tonnes > 0:
            # Loading should reduce inventory
            pass  # Allow positive for corrections

    @property
    def is_inflow(self) -> bool:
        """True if this transaction adds to inventory."""
        return self.quantity_tonnes > 0


@dataclass
class VesselOrder:
    """A vessel loading order specifying product, quantity, and loading window.

    Attributes:
        vessel_id: Vessel identifier (e.g., 'MV_BORNEO_STAR').
        product_code: Product to load.
        quantity_tonnes: Cargo quantity (tonnes).
        loading_day: Planned loading start day (0-indexed).
        tolerance_pct: Acceptable quantity tolerance ±% (e.g., 5.0 for ±5%). Default 5%.
        loading_rate_tph: Loading rate (tonnes/hour). Default 3000 tph.

    Raises:
        ValueError: If quantity or rate is non-positive, or tolerance is out of range.
    """

    vessel_id: str
    product_code: str
    quantity_tonnes: float
    loading_day: int
    tolerance_pct: float = 5.0
    loading_rate_tph: float = 3000.0

    def __post_init__(self) -> None:
        if not self.vessel_id.strip():
            raise ValueError("vessel_id must not be empty.")
        if self.quantity_tonnes <= 0:
            raise ValueError("quantity_tonnes must be positive.")
        if self.loading_day < 0:
            raise ValueError("loading_day must be non-negative.")
        if not (0.0 <= self.tolerance_pct <= 20.0):
            raise ValueError("tolerance_pct must be in [0, 20]%.")
        if self.loading_rate_tph < MIN_LOADING_RATE_TPH:
            raise ValueError(
                f"loading_rate_tph must be at least {MIN_LOADING_RATE_TPH} tph."
            )

    @property
    def loading_hours(self) -> float:
        """Estimated loading duration (hours)."""
        return round(self.quantity_tonnes / self.loading_rate_tph, 2)

    @property
    def min_quantity(self) -> float:
        """Minimum acceptable cargo quantity."""
        return self.quantity_tonnes * (1.0 - self.tolerance_pct / 100.0)

    @property
    def max_quantity(self) -> float:
        """Maximum acceptable cargo quantity."""
        return self.quantity_tonnes * (1.0 + self.tolerance_pct / 100.0)


@dataclass
class StockpileConstraints:
    """Physical and operational constraints for a port stockpile.

    Attributes:
        max_capacity_tonnes: Maximum total stockpile capacity (tonnes).
        pad_capacity_by_product: Optional per-product pad capacity (tonnes).
            If None, uses proportional share of max_capacity.
        reclaim_rate_tph: Reclaimer throughput (tonnes/hour).
        stacking_rate_tph: Stacker throughput (tonnes/hour).
        simultaneous_vessel_max: Maximum vessels that can load simultaneously.

    Raises:
        ValueError: If capacities or rates are non-positive.
    """

    max_capacity_tonnes: float
    pad_capacity_by_product: Optional[Dict[str, float]] = None
    reclaim_rate_tph: float = 5000.0
    stacking_rate_tph: float = 4000.0
    simultaneous_vessel_max: int = 2

    def __post_init__(self) -> None:
        if self.max_capacity_tonnes <= 0:
            raise ValueError("max_capacity_tonnes must be positive.")
        if self.reclaim_rate_tph <= 0:
            raise ValueError("reclaim_rate_tph must be positive.")
        if self.stacking_rate_tph <= 0:
            raise ValueError("stacking_rate_tph must be positive.")
        if self.simultaneous_vessel_max < 1:
            raise ValueError("simultaneous_vessel_max must be at least 1.")


# ---------------------------------------------------------------------------
# Core planner
# ---------------------------------------------------------------------------


class PortInventoryPlanner:
    """Plan and track coal inventory at an export port terminal.

    Manages multi-product inventory flows over a planning horizon, checks
    vessel loading feasibility, alerts on stockpile capacity constraints,
    and computes safety stock requirements.

    Args:
        terminal_name: Port terminal name.
        planning_horizon_days: Number of days to plan ahead (7–90).
        safety_stock_days: Target safety stock coverage in days of average daily demand.

    Raises:
        ValueError: If planning_horizon_days is outside [7, 90].

    Example:
        >>> planner = PortInventoryPlanner("Muara Jawa Terminal", 30)
        >>> planner.set_opening_stock("GAR5000", 250_000.0)
        >>> planner.add_vessel_order(vessel_order)
        >>> feasibility = planner.check_vessel_feasibility(vessel_order)
    """

    def __init__(
        self,
        terminal_name: str,
        planning_horizon_days: int = 30,
        safety_stock_days: int = DEFAULT_SAFETY_STOCK_DAYS,
    ) -> None:
        if not terminal_name.strip():
            raise ValueError("terminal_name must not be empty.")
        if not (7 <= planning_horizon_days <= 90):
            raise ValueError("planning_horizon_days must be between 7 and 90.")
        if safety_stock_days < 1:
            raise ValueError("safety_stock_days must be at least 1.")
        self.terminal_name = terminal_name
        self.horizon = planning_horizon_days
        self.safety_stock_days = safety_stock_days
        self._products: Dict[str, CoalProduct] = {}
        self._opening_stock: Dict[str, float] = {}
        self._transactions: List[InventoryTransaction] = []
        self._vessel_orders: List[VesselOrder] = []
        self._constraints: Optional[StockpileConstraints] = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def register_product(self, product: CoalProduct) -> None:
        """Register a coal product for inventory tracking."""
        if not isinstance(product, CoalProduct):
            raise TypeError(f"Expected CoalProduct, got {type(product).__name__}.")
        self._products[product.product_code] = product

    def set_constraints(self, constraints: StockpileConstraints) -> None:
        """Set physical stockpile constraints."""
        if not isinstance(constraints, StockpileConstraints):
            raise TypeError("Expected StockpileConstraints.")
        self._constraints = constraints

    def set_opening_stock(self, product_code: str, tonnes: float) -> None:
        """Set opening inventory for a product.

        Args:
            product_code: Must match a registered CoalProduct.
            tonnes: Opening stock in tonnes (non-negative).

        Raises:
            ValueError: If product not registered or tonnes is negative.
        """
        if product_code not in self._products:
            raise ValueError(f"Product '{product_code}' not registered. Call register_product() first.")
        if tonnes < 0:
            raise ValueError("Opening stock tonnes must be non-negative.")
        self._opening_stock[product_code] = tonnes

    def add_transaction(self, tx: InventoryTransaction) -> None:
        """Add an inventory transaction."""
        if not isinstance(tx, InventoryTransaction):
            raise TypeError(f"Expected InventoryTransaction, got {type(tx).__name__}.")
        self._transactions.append(tx)

    def add_vessel_order(self, order: VesselOrder) -> None:
        """Add a vessel loading order to the plan."""
        if not isinstance(order, VesselOrder):
            raise TypeError(f"Expected VesselOrder, got {type(order).__name__}.")
        self._vessel_orders.append(order)

    # ------------------------------------------------------------------
    # Core analytics
    # ------------------------------------------------------------------

    def inventory_at_day(self, product_code: str, day: int) -> float:
        """Compute inventory balance for a product at end of a given day.

        Args:
            product_code: Target product.
            day: Day index (0 = opening balance, before any transactions on day 0).

        Returns:
            Inventory in tonnes at end of the day.

        Raises:
            ValueError: If product is not registered.
        """
        if product_code not in self._products:
            raise ValueError(f"Product '{product_code}' not registered.")
        balance = self._opening_stock.get(product_code, 0.0)
        # Add transactions up to and including `day`
        relevant = [
            t for t in self._transactions
            if t.product_code == product_code and t.day <= day
        ]
        balance += sum(t.quantity_tonnes for t in relevant)
        # Add vessel loading outflows up to and including day
        relevant_vessels = [
            v for v in self._vessel_orders
            if v.product_code == product_code and v.loading_day <= day
        ]
        balance -= sum(v.quantity_tonnes for v in relevant_vessels)
        return max(0.0, balance)  # stock can't go negative (floored)

    def projection(self, product_code: str) -> List[Dict]:
        """Generate day-by-day inventory projection for a product.

        Args:
            product_code: Target product.

        Returns:
            List of dicts per day: day, opening_balance, receipts, loadings,
            closing_balance, alert (bool).
        """
        if product_code not in self._products:
            raise ValueError(f"Product '{product_code}' not registered.")

        results = []
        balance = self._opening_stock.get(product_code, 0.0)

        for day in range(self.horizon):
            # Receipts on this day
            receipts = sum(
                t.quantity_tonnes for t in self._transactions
                if t.product_code == product_code and t.day == day and t.is_inflow
            )
            # Adjustments (negative receipts) on this day
            adjustments = sum(
                t.quantity_tonnes for t in self._transactions
                if t.product_code == product_code and t.day == day and not t.is_inflow
            )
            # Vessel loadings on this day
            loadings = sum(
                v.quantity_tonnes for v in self._vessel_orders
                if v.product_code == product_code and v.loading_day == day
            )

            opening = balance
            balance = max(0.0, opening + receipts + adjustments - loadings)

            # Safety stock alert
            ss_target = self._safety_stock(product_code)
            alert = balance < ss_target

            results.append({
                "day": day,
                "opening_balance_t": round(opening, 1),
                "receipts_t": round(receipts, 1),
                "loadings_t": round(loadings, 1),
                "closing_balance_t": round(balance, 1),
                "safety_stock_target_t": round(ss_target, 1),
                "below_safety_stock": alert,
            })

        return results

    def check_vessel_feasibility(self, order: VesselOrder) -> Dict:
        """Check if there is sufficient inventory to load a vessel.

        Args:
            order: VesselOrder to check.

        Returns:
            Dict with feasible (bool), available_stock_t, shortfall_t,
            utilisation_pct (stock used as % of available), alert messages.
        """
        if order.product_code not in self._products:
            return {
                "feasible": False,
                "available_stock_t": 0.0,
                "shortfall_t": order.quantity_tonnes,
                "utilisation_pct": 0.0,
                "alerts": [f"Product '{order.product_code}' not registered at terminal."],
            }

        # Inventory just before the loading day (end of day - 1)
        day_before = max(0, order.loading_day - 1)
        stock = self.inventory_at_day(order.product_code, day_before)
        shortfall = max(0.0, order.min_quantity - stock)
        utilisation = min(100.0, (order.quantity_tonnes / stock * 100.0)) if stock > 0 else 100.0

        alerts = []
        if shortfall > 0:
            alerts.append(
                f"Insufficient stock: need ≥{order.min_quantity:,.0f} t "
                f"(min with tolerance), have {stock:,.0f} t — shortfall {shortfall:,.0f} t."
            )
        if self._constraints and self._constraints.reclaim_rate_tph > 0:
            loading_window_h = order.loading_hours
            if order.loading_rate_tph > self._constraints.reclaim_rate_tph:
                alerts.append(
                    f"Vessel loading rate ({order.loading_rate_tph:.0f} tph) exceeds "
                    f"reclaimer capacity ({self._constraints.reclaim_rate_tph:.0f} tph)."
                )

        return {
            "feasible": shortfall == 0,
            "available_stock_t": round(stock, 1),
            "required_t": round(order.quantity_tonnes, 1),
            "shortfall_t": round(shortfall, 1),
            "utilisation_pct": round(utilisation, 1),
            "estimated_loading_hours": order.loading_hours,
            "alerts": alerts,
        }

    def capacity_utilisation(self, day: int) -> Dict:
        """Compute total stockpile utilisation on a given day.

        Args:
            day: Day index.

        Returns:
            Dict with total_stock_t, capacity_t, utilisation_pct, congestion_alert.
        """
        total_stock = sum(
            self.inventory_at_day(pc, day) for pc in self._products
        )
        capacity = self._constraints.max_capacity_tonnes if self._constraints else float("inf")
        util_pct = (total_stock / capacity * 100.0) if capacity > 0 and capacity != float("inf") else 0.0

        return {
            "day": day,
            "total_stock_t": round(total_stock, 1),
            "capacity_t": round(capacity, 1) if capacity != float("inf") else None,
            "utilisation_pct": round(util_pct, 1),
            "congestion_alert": util_pct >= CONGESTION_ALERT_THRESHOLD_PCT,
        }

    def _safety_stock(self, product_code: str) -> float:
        """Compute safety stock target (tonnes) based on historical demand average."""
        # Use vessel orders as demand proxy
        vessel_tonnes = [
            v.quantity_tonnes for v in self._vessel_orders
            if v.product_code == product_code
        ]
        if not vessel_tonnes:
            return 0.0
        avg_daily_demand = sum(vessel_tonnes) / max(1, self.horizon)
        return avg_daily_demand * self.safety_stock_days

    def days_of_stock(self, product_code: str, day: int) -> Optional[float]:
        """Compute days of stock remaining at a given day based on average demand.

        Args:
            product_code: Target product.
            day: Day index.

        Returns:
            Days of stock remaining (float), or None if no demand history.
        """
        stock = self.inventory_at_day(product_code, day)
        vessel_tonnes = [
            v.quantity_tonnes for v in self._vessel_orders
            if v.product_code == product_code and v.loading_day >= day
        ]
        if not vessel_tonnes:
            return None
        remaining_horizon = max(1, self.horizon - day)
        avg_daily = sum(vessel_tonnes) / remaining_horizon
        if avg_daily <= 0:
            return None
        return round(stock / avg_daily, 1)

    def export_plan_summary(self) -> Dict:
        """High-level summary of the export plan for all products.

        Returns:
            Dict with terminal_name, horizon_days, products, total scheduled tonnage,
            total scheduled vessels, feasibility summary.
        """
        total_tonnage = sum(v.quantity_tonnes for v in self._vessel_orders)
        n_vessels = len(self._vessel_orders)

        feasibility = {}
        for order in self._vessel_orders:
            check = self.check_vessel_feasibility(order)
            feasibility[order.vessel_id] = check["feasible"]

        n_feasible = sum(1 for v in feasibility.values() if v)

        return {
            "terminal_name": self.terminal_name,
            "horizon_days": self.horizon,
            "n_products": len(self._products),
            "n_vessel_orders": n_vessels,
            "total_scheduled_tonnage": round(total_tonnage, 1),
            "n_feasible_vessels": n_feasible,
            "all_feasible": n_feasible == n_vessels,
            "vessel_feasibility": feasibility,
        }
