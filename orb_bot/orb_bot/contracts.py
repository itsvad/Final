"""Tick size / tick value lookup for common futures contracts.

Tradovate's contract endpoints don't hand back dollar-per-tick in one
convenient field, so we keep a small static table for the instruments
people actually day-trade ORB on. Anything not listed here must be
supplied explicitly via config.yaml (contract.tick_size / tick_value).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    description: str
    tick_size: float
    tick_value: float  # dollars per tick, per contract


# Root symbol -> spec. Extend freely.
KNOWN_CONTRACTS: dict[str, ContractSpec] = {
    # Equity index (CME)
    "ES": ContractSpec("ES", "E-mini S&P 500", 0.25, 12.50),
    "MES": ContractSpec("MES", "Micro E-mini S&P 500", 0.25, 1.25),
    "NQ": ContractSpec("NQ", "E-mini Nasdaq-100", 0.25, 5.00),
    "MNQ": ContractSpec("MNQ", "Micro E-mini Nasdaq-100", 0.25, 0.50),
    "YM": ContractSpec("YM", "E-mini Dow", 1.00, 5.00),
    "MYM": ContractSpec("MYM", "Micro E-mini Dow", 1.00, 0.50),
    "RTY": ContractSpec("RTY", "E-mini Russell 2000", 0.10, 5.00),
    "M2K": ContractSpec("M2K", "Micro E-mini Russell 2000", 0.10, 0.50),
    # Energy (NYMEX)
    "CL": ContractSpec("CL", "Crude Oil", 0.01, 10.00),
    "MCL": ContractSpec("MCL", "Micro Crude Oil", 0.01, 1.00),
    "NG": ContractSpec("NG", "Natural Gas", 0.001, 10.00),
    # Metals (COMEX)
    "GC": ContractSpec("GC", "Gold", 0.10, 10.00),
    "MGC": ContractSpec("MGC", "Micro Gold", 0.10, 1.00),
    "SI": ContractSpec("SI", "Silver", 0.005, 25.00),
    "SIL": ContractSpec("SIL", "Micro Silver", 0.005, 5.00),
    # Financials (CBOT)
    "ZB": ContractSpec("ZB", "30-Year T-Bond", 1 / 32, 31.25),
    "ZN": ContractSpec("ZN", "10-Year T-Note", 1 / 64, 15.625),
    # Currencies (CME)
    "6E": ContractSpec("6E", "Euro FX", 0.00005, 6.25),
    "6B": ContractSpec("6B", "British Pound", 0.0001, 6.25),
}


class UnknownContractError(ValueError):
    pass


def resolve_spec(
    symbol: str,
    tick_size_override: Optional[float] = None,
    tick_value_override: Optional[float] = None,
) -> ContractSpec:
    """Resolve a ContractSpec for `symbol`, honoring config overrides.

    If both overrides are given, they win outright (lets users trade
    anything, including symbols not in the table). If the symbol is in
    the table, missing overrides are filled from it. Otherwise raises.
    """
    known = KNOWN_CONTRACTS.get(symbol.upper())

    tick_size = tick_size_override if tick_size_override is not None else (known.tick_size if known else None)
    tick_value = tick_value_override if tick_value_override is not None else (known.tick_value if known else None)

    if tick_size is None or tick_value is None:
        raise UnknownContractError(
            f"No tick size/value known for symbol {symbol!r}. Set contract.tick_size "
            f"and contract.tick_value explicitly in config.yaml."
        )

    return ContractSpec(
        symbol=symbol.upper(),
        description=known.description if known else symbol.upper(),
        tick_size=tick_size,
        tick_value=tick_value,
    )
