"""Risk-based position sizing.

Given how many ticks away the stop is and how much you're willing to
lose on the trade, work out the largest number of contracts whose total
risk does not exceed your risk budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PositionSizeResult:
    contracts: int
    risk_per_contract_usd: float
    total_risk_usd: float
    stop_distance_ticks: float
    skipped_reason: Optional[str] = None

    @property
    def tradeable(self) -> bool:
        return self.contracts > 0 and self.skipped_reason is None


def calculate_position_size(
    stop_distance_ticks: float,
    tick_value: float,
    risk_amount_usd: float,
    min_contracts: int = 1,
    max_contracts: int = 10,
) -> PositionSizeResult:
    """Floor-divide risk budget by per-contract risk.

    Example: risking $600, one contract risks $400 (stop distance *
    tick value) -> floor(600 / 400) = 1 contract, since 2 contracts
    would risk $800, which exceeds the $600 budget.
    """
    if stop_distance_ticks <= 0:
        return PositionSizeResult(0, 0.0, 0.0, stop_distance_ticks, "stop_distance_ticks must be > 0")
    if tick_value <= 0:
        return PositionSizeResult(0, 0.0, 0.0, stop_distance_ticks, "tick_value must be > 0")
    if risk_amount_usd <= 0:
        return PositionSizeResult(0, 0.0, 0.0, stop_distance_ticks, "risk_amount_usd must be > 0")

    risk_per_contract = stop_distance_ticks * tick_value
    contracts = math.floor(risk_amount_usd / risk_per_contract)

    if contracts < min_contracts:
        return PositionSizeResult(
            contracts=0,
            risk_per_contract_usd=risk_per_contract,
            total_risk_usd=0.0,
            stop_distance_ticks=stop_distance_ticks,
            skipped_reason=(
                f"risk_amount_usd (${risk_amount_usd:,.2f}) is less than the risk of "
                f"{min_contracts} contract(s) (${risk_per_contract * min_contracts:,.2f})"
            ),
        )

    contracts = min(contracts, max_contracts)
    return PositionSizeResult(
        contracts=contracts,
        risk_per_contract_usd=risk_per_contract,
        total_risk_usd=contracts * risk_per_contract,
        stop_distance_ticks=stop_distance_ticks,
        skipped_reason=None,
    )
