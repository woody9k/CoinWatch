"""Chain-neutral port for reading coin state.

The engine depends on this module. Venue account layouts and RPC clients stay
inside a concrete adapter.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CoinState:
    """Cheap coin snapshot shared by every chain adapter.

    ``price_native``, ``price_usd``, ``mcap_usd``, ``liquidity_native``,
    ``volume_1m``, ``volume_5m``, and ``volume_15m`` are ``Decimal`` amounts.
    ``curve_pct`` is bonding-curve progress from 0 to 100, or ``None`` when
    the venue has no curve. ``holders`` is ``None`` until a holder scan runs.
    Volume windows are ``Decimal(0)`` when the adapter has no volume source
    yet. ``complete`` is true when the venue reports that the curve has finished.
    """

    price_native: Decimal
    price_usd: Decimal
    mcap_usd: Decimal
    liquidity_native: Decimal
    curve_pct: Decimal | None
    volume_1m: Decimal
    volume_5m: Decimal
    volume_15m: Decimal
    holders: int | None
    complete: bool


class ChainAdapter(Protocol):
    """Read-only chain port used by the coin poller.

    ``id`` is the chain key stored on coins and ticks, such as ``solana``.
    ``native_symbol`` is the gas asset, such as ``SOL``.
    """

    id: str
    native_symbol: str

    def get_coin_state(self, coin_address: str) -> CoinState:
        """Return the latest cheap state for ``coin_address``."""
        ...
