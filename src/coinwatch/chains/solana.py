"""Solana pump.fun bonding-curve reader.

This is the only module that imports ``solana`` or ``solders``. It reads the
bonding-curve account and does not submit orders or load wallet keys.

Volume is not on that account. ``volume_1m``, ``volume_5m``, and
``volume_15m`` are ``Decimal(0)`` until a later source exists. ``holders`` is
``None``. SOL/USD is the configured ``sol_usd`` value. This module does not
call a price HTTP API and does not log the RPC URL.
"""

import asyncio
import struct
from collections.abc import Callable
from decimal import Decimal

from solana.exceptions import SolanaRpcException
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.core import RPCException
from solders.pubkey import Pubkey

from coinwatch.chains.base import CoinState
from coinwatch.errors import ChainReadError

_PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
_BONDING_CURVE_SEED = b"bonding-curve"
_DISCRIMINATOR_LEN = 8
_CURVE_BODY = struct.Struct("<QQQQQ?")
_MIN_ACCOUNT_LEN = _DISCRIMINATOR_LEN + _CURVE_BODY.size
_TOKEN_SCALE = Decimal(1_000_000)
_SOL_SCALE = Decimal(1_000_000_000)
_INITIAL_REAL_TOKEN_RESERVES = Decimal(793_100_000_000_000)
_HUNDRED = Decimal(100)
_ZERO = Decimal(0)
_RPC_TIMEOUT_SECONDS = 10.0

AccountReader = Callable[[str], bytes | None]


def bonding_curve_address(mint_address: str) -> str:
    """Return the pump.fun bonding-curve account address for ``mint_address``.

    The address is the program-derived account with seeds
    ``[b"bonding-curve", mint_pubkey_bytes]`` under the pump.fun program.
    Raises ``ChainReadError`` with failure ``mint`` when ``mint_address`` is
    not a Solana address.
    """
    return str(_curve_pda(mint_address))


def decode_bonding_curve(data: bytes, sol_usd: Decimal, *, mint: str) -> CoinState:
    """Decode a pump.fun bonding-curve account into ``CoinState``.

    The first 8 bytes are the Anchor discriminator. The body is little-endian
    ``virtual_token_reserves``, ``virtual_sol_reserves``, ``real_token_reserves``,
    ``real_sol_reserves``, ``token_total_supply`` (each ``u64``), then
    ``complete`` (``bool``). Token amounts use 6 decimals and SOL amounts use 9.
    Price is virtual SOL divided by virtual tokens. Market cap is that price
    times the total supply times ``sol_usd``. Curve progress is the share of
    the initial real token reserves that have been sold, clamped to 0–100, or
    100 when ``complete`` is true. Volume is not in this account, so the volume
    windows are ``Decimal(0)`` and ``holders`` is ``None``. Raises
    ``ChainReadError`` when the body is short or the virtual token reserve is zero.
    """
    if len(data) < _MIN_ACCOUNT_LEN:
        raise ChainReadError("short", mint)
    virtual_token, virtual_sol, real_token, real_sol, total_supply, complete = (
        _CURVE_BODY.unpack_from(data, _DISCRIMINATOR_LEN)
    )
    if virtual_token == 0:
        raise ChainReadError("invalid", mint)
    price_native = (Decimal(virtual_sol) / _SOL_SCALE) / (Decimal(virtual_token) / _TOKEN_SCALE)
    price_usd = price_native * sol_usd
    supply_tokens = Decimal(total_supply) / _TOKEN_SCALE
    return CoinState(
        price_native=price_native,
        price_usd=price_usd,
        mcap_usd=price_native * supply_tokens * sol_usd,
        liquidity_native=Decimal(real_sol) / _SOL_SCALE,
        curve_pct=_curve_pct(real_token, complete),
        volume_1m=_ZERO,
        volume_5m=_ZERO,
        volume_15m=_ZERO,
        holders=None,
        complete=complete,
    )


class SolanaPumpAdapter:
    """Read pump.fun bonding-curve state for one mint.

    ``id`` is ``solana`` and ``native_symbol`` is ``SOL``. ``get_coin_state``
    loads the bonding-curve account from ``SOLANA_RPC_URL`` unless
    ``account_reader`` is supplied. The reader returns raw account bytes, or
    ``None`` when the account is missing. Failures raise ``ChainReadError``
    with the mint and a failure label. The RPC URL is not included in the
    error or in logs from this class. Volume windows stay ``Decimal(0)``
    because the curve account does not include volume. ``holders`` stays
    ``None``.
    """

    id = "solana"
    native_symbol = "SOL"

    def __init__(
        self,
        rpc_url: str,
        sol_usd: Decimal,
        *,
        account_reader: AccountReader | None = None,
    ) -> None:
        """Store the RPC URL, the configured SOL price, and an optional reader."""
        self._rpc_url = rpc_url
        self._sol_usd = sol_usd
        self._account_reader = account_reader

    def get_coin_state(self, coin_address: str) -> CoinState:
        """Return bonding-curve state for ``coin_address``.

        Raises ``ChainReadError`` when the mint is invalid, the account is
        missing, the body is short, the curve cannot be priced, or the RPC
        call fails. The exception does not contain the RPC URL.
        """
        raw = self._read_account(coin_address)
        if raw is None:
            raise ChainReadError("missing", coin_address)
        return decode_bonding_curve(raw, self._sol_usd, mint=coin_address)

    def _read_account(self, coin_address: str) -> bytes | None:
        """Return curve-account bytes from the injected reader or from RPC."""
        if self._account_reader is not None:
            return self._account_reader(coin_address)
        pda = _curve_pda(coin_address)
        return _fetch_account_data(self._rpc_url, pda, coin_address)


def _curve_pda(mint_address: str) -> Pubkey:
    """Derive the bonding-curve PDA, or raise ``ChainReadError`` for a bad mint."""
    try:
        mint = Pubkey.from_string(mint_address)
    except ValueError:
        raise ChainReadError("mint", mint_address) from None
    pda, _bump = Pubkey.find_program_address([_BONDING_CURVE_SEED, bytes(mint)], _PUMP_PROGRAM)
    return pda


def _curve_pct(real_token_reserves: int, complete: bool) -> Decimal:
    """Return curve progress, forced to 100 when the curve is complete."""
    if complete:
        return _HUNDRED
    progress = (
        (_INITIAL_REAL_TOKEN_RESERVES - Decimal(real_token_reserves))
        / _INITIAL_REAL_TOKEN_RESERVES
        * _HUNDRED
    )
    if progress < _ZERO:
        return _ZERO
    if progress > _HUNDRED:
        return _HUNDRED
    return progress


def _fetch_account_data(rpc_url: str, account: Pubkey, mint: str) -> bytes | None:
    """Load account bytes over RPC.

    A missing account returns ``None``. Transport and RPC failures raise
    ``ChainReadError`` with failure ``rpc``. The URL is not copied into the
    exception.
    """

    async def _read() -> bytes | None:
        async with AsyncClient(
            rpc_url, commitment=Confirmed, timeout=_RPC_TIMEOUT_SECONDS
        ) as client:
            response = await client.get_account_info(account)
        info = response.value
        if info is None:
            return None
        return info.data

    try:
        return asyncio.run(_read())
    except (SolanaRpcException, RPCException, OSError, ValueError):
        # Drop the cause: transport errors can include the RPC endpoint.
        raise ChainReadError("rpc", mint) from None
