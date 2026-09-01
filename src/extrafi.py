"""Extrafi lending market reads.

Extrafi's registry rows under `lend` actually span two unrelated lending
protocols, distinguishable only by which contract interface the given
address implements — the registry's `type` column doesn't tell them apart,
since both show up as `lend`. (Extrafi has no AMM-pool product at all, so the
`pool` two-leg dispatch never applies to any of its contracts.)

  XLend — a fork of Aave v3. The registry address is the market's aToken.
  Aave v3 aTokens rebase 1:1 with the underlying, so `totalSupply()` already
  *is* the total value deposited — no exchange-rate math needed. Detected via
  `UNDERLYING_ASSET_ADDRESS()`, a real aToken getter that reverts on
  anything else.

  LYF (Leveraged Yield Farming) — a Compound/Tarot-style fork. The registry
  address is the pool's eToken, but unlike XLend's aToken, the eToken does
  NOT hold the pool's real balance — actual reserves live in one shared
  `LendingPool` contract, keyed by a small integer reserveId, not an
  address. Detected via `lendingPool()`, a real getter on Extrafi's own
  `ExtraInterestBearingToken.sol` that returns that shared contract's
  address directly, so no LendingPool address needs to be hardcoded here.
  The reserveId itself has no reverse lookup on-chain, so it's found by
  scanning the LendingPool's `getETokenAddress(id)` for id in
  [1, nextReserveId) until one matches the given eToken address — batched,
  and bounded by the LendingPool's own `nextReserveId` counter rather than
  an arbitrary cap.

Verified against Extrafi's own published milestone-completion numbers (not
used as an input, only as a cross-check): the XLend weETH market's
totalSupply() on 2025-12-29 read 78.13 weETH against their self-reported
"~78 weETH"; the LYF USDC reserve's totalLiquidityOfReserve() trajectory
($1.03M pre-incentive -> $1.70M by 2025-12-03) matches their reported
figures, and its current value matches the live TVL Extrafi's own UI shows
for that pool.
"""

from __future__ import annotations

from rpc import ArchiveRPC

UNDERLYING_ASSET_ADDRESS = "0xb16a19de"   # UNDERLYING_ASSET_ADDRESS() -- Aave v3 aToken
LENDING_POOL = "0xa59a9973"               # lendingPool() -- LYF eToken
UNDERLYING_ASSET = "0x7158da7c"           # underlyingAsset() -- LYF eToken
NEXT_RESERVE_ID = "0x46dbf589"            # nextReserveId() -- LYF LendingPool
GET_ETOKEN_ADDRESS = "0x54018896"         # getETokenAddress(uint256) -- LYF LendingPool
TOTAL_LIQUIDITY_OF_RESERVE = "0x11d584e4"  # totalLiquidityOfReserve(uint256)
TOTAL_SUPPLY = "0x18160ddd"
DECIMALS = "0x313ce567"


def _pad_uint(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def _try_read(rpc: ArchiveRPC, address: str, selector: str, block: int) -> str | None:
    """None on revert or an all-zero/empty result, rather than raising --
    used purely for architecture detection, where a revert just means "not
    this one", not an error."""
    try:
        raw = rpc.read(address, selector, block)
    except RuntimeError:
        return None
    if not raw or raw in ("0x", "0x" + "0" * 64):
        return None
    return raw


def _resolve_reserve_id(rpc: ArchiveRPC, lending_pool: str, etoken: str) -> int:
    latest = rpc.latest_block()
    next_id = int(rpc.read(lending_pool, NEXT_RESERVE_ID, latest), 16)
    calls = [
        ("eth_call", [{"to": lending_pool, "data": GET_ETOKEN_ADDRESS + _pad_uint(i)}, hex(latest)],
         f"{rpc.slug}:call:{lending_pool}:{GET_ETOKEN_ADDRESS}{_pad_uint(i)}:{latest}")
        for i in range(1, next_id)
    ]
    results = rpc.call_batch(calls)
    rpc.flush()
    target = etoken.lower()
    for reserve_id, raw in zip(range(1, next_id), results):
        if ("0x" + raw[-40:]).lower() == target:
            return reserve_id
    raise SystemExit(
        f"eToken {etoken} has a lendingPool() but its address isn't returned "
        f"by getETokenAddress() for any reserveId in [1, {next_id}) on "
        f"{lending_pool} — verify this is really an Extrafi LYF eToken."
    )


def measure_lend(rpc: ArchiveRPC, address: str, block: int) -> tuple[float, str]:
    """Extrafi lending-market quantity in underlying units, whichever of
    XLend or LYF `address` turns out to be. Returns (quantity, underlying
    token address) -- same contract as measure.measure_vault, so it drops
    into the same `lend`-type dispatch slot in measure.py.
    """
    xlend_underlying = _try_read(rpc, address, UNDERLYING_ASSET_ADDRESS, block)
    if xlend_underlying is not None:
        underlying = "0x" + xlend_underlying[-40:]
        decimals = int(rpc.read(address, DECIMALS, block), 16)
        raw = rpc.read(address, TOTAL_SUPPLY, block)
        return int(raw, 16) / 10 ** decimals, underlying

    lyf_pool = _try_read(rpc, address, LENDING_POOL, block)
    if lyf_pool is not None:
        lending_pool = "0x" + lyf_pool[-40:]
        underlying_raw = _try_read(rpc, address, UNDERLYING_ASSET, block)
        if underlying_raw is None:
            raise SystemExit(
                f"{address} has a lendingPool() but no underlyingAsset() -- "
                f"doesn't match Extrafi's ExtraInterestBearingToken interface."
            )
        underlying = "0x" + underlying_raw[-40:]
        reserve_id = _resolve_reserve_id(rpc, lending_pool, address)
        decimals = int(rpc.read(underlying, DECIMALS, block), 16)
        raw = rpc.read(lending_pool, TOTAL_LIQUIDITY_OF_RESERVE + _pad_uint(reserve_id), block)
        return int(raw, 16) / 10 ** decimals, underlying

    raise SystemExit(
        f"{address} implements neither UNDERLYING_ASSET_ADDRESS() (XLend "
        f"aToken) nor lendingPool() (LYF eToken) -- not a recognized "
        f"Extrafi lending-market contract."
    )
