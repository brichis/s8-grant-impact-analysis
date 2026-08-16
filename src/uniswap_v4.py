"""Uniswap v4 pool reserve reads.

v4 has no per-pool contract to run `balanceOf` against (the pattern
measure.py's v2/v3 `pool` path uses) — every pool's liquidity lives inside one
shared `PoolManager` singleton, addressed by a `PoolId` (a 32-byte hash of the
pool's key: currency0, currency1, fee, tickSpacing, hooks). The scope tab's
`contract_address` for a v4 pool is that PoolId, not a contract — 32 bytes
(66-char hex incl. `0x`) instead of the usual 20-byte address, which is how
`is_pool_id` tells the two apart.

The PoolKey itself is never stored in contract state, only emitted once in the
pool's `Initialize` event, so reading a v4 pool means: (1) recover the PoolKey
by finding that event, then (2) ask Uniswap's own `ReservesLens` periphery
contract for the exact reserves — it walks every initialized tick on-chain and
sums them, not a current-tick approximation.

Step (1) needs `eth_getLogs`, which Alchemy's free tier caps to a 10-block
range — too narrow to scan a pool's whole history for one `Initialize` event.
Instead we binary-search for the exact init block using `StateView.getSlot0`
via plain `eth_call` (uncapped): sqrtPriceX96 is 0 before initialization and
non-zero after, so the search converges to a single block in ~30 calls, then
one 1-block `eth_getLogs` at that block gets the event.

Addresses below are Uniswap's own deployment addresses (docs.uniswap.org/
contracts/v4/deployments), cross-checked against a live call against the
Super DCA DCA-USDC pool on Optimism: currency0 resolved to Optimism's known
USDC address, currency1 matched Super DCA's own published token address, and
`hooks` matched Super DCA's own published Gauge address exactly. Only
Optimism is verified — add + verify other chains against the docs above
before trusting output for pools there.
"""

from __future__ import annotations

from rpc import ArchiveRPC

POOL_MANAGER = {
    "Optimism": "0x9a13f98cb987694c9f086b1f5eb990eea8264ec3",
}
STATE_VIEW = {
    "Optimism": "0xc18a3169788f4f75a170290584eca6395c75ecdb",
}
RESERVES_LENS = {
    "Optimism": "0x0000001b173c3bbf3984d417d8614e3eed34865b",
}

GET_SLOT0 = "0xc815641c"     # StateView.getSlot0(bytes32)
GET_POOL_TVL = "0xf95138f2"  # ReservesLens.getPoolTVL(address,(address,address,uint24,int24,address))
INITIALIZE_TOPIC0 = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"


def is_pool_id(value: str) -> bool:
    """True if `value` is a 32-byte v4 PoolId rather than a 20-byte address."""
    return len(value) == 66


def _pad_addr(addr: str) -> str:
    return addr[2:].rjust(64, "0").lower()


def _pad_uint(n: int) -> str:
    return hex(n & (2 ** 256 - 1))[2:].rjust(64, "0")


def _sqrt_price_at(rpc: ArchiveRPC, chain: str, pool_id: str, block: int) -> int:
    raw = rpc.read(STATE_VIEW[chain], GET_SLOT0 + pool_id[2:], block)
    if not raw or raw == "0x" or len(raw) < 66:
        return 0
    return int(raw[2:66], 16)


def _find_init_block(rpc: ArchiveRPC, chain: str, pool_id: str) -> int:
    cache_key = f"{rpc.slug}:v4init:{pool_id}"
    if cache_key in rpc.cache:
        return rpc.cache[cache_key]
    lo, hi = 1, rpc.latest_block()
    while lo < hi:
        mid = (lo + hi) // 2
        if _sqrt_price_at(rpc, chain, pool_id, mid):
            hi = mid
        else:
            lo = mid + 1
    rpc.cache[cache_key] = lo
    return lo


def _pool_key(rpc: ArchiveRPC, chain: str, pool_id: str) -> dict:
    """Recover the full PoolKey from the pool's one-time Initialize event."""
    cache_key = f"{rpc.slug}:v4key:{pool_id}"
    if cache_key in rpc.cache:
        return rpc.cache[cache_key]

    block = _find_init_block(rpc, chain, pool_id)
    logs = rpc.logs(POOL_MANAGER[chain], [INITIALIZE_TOPIC0, pool_id], block, block)
    if len(logs) != 1:
        raise SystemExit(
            f"Expected exactly one Initialize log for pool {pool_id} at block "
            f"{block}, found {len(logs)}. The init-block search may not have "
            f"converged correctly — verify manually before trusting this pool."
        )
    log = logs[0]
    currency0 = "0x" + log["topics"][2][-40:]
    currency1 = "0x" + log["topics"][3][-40:]
    data = log["data"][2:]
    words = [data[i:i + 64] for i in range(0, len(data), 64)]
    fee = int(words[0], 16)
    tick_spacing = int(words[1], 16)
    if tick_spacing >= 2 ** 23:
        tick_spacing -= 2 ** 24
    hooks = "0x" + words[2][-40:]

    key = {"currency0": currency0, "currency1": currency1, "fee": fee,
           "tick_spacing": tick_spacing, "hooks": hooks}
    rpc.cache[cache_key] = key
    return key


def pool_reserves(rpc: ArchiveRPC, chain: str, pool_id: str, block: int) -> dict:
    """{currency_address: raw_quantity} for both sides of a v4 pool at `block`."""
    if chain not in POOL_MANAGER:
        raise SystemExit(
            f"No verified Uniswap v4 deployment addresses for chain '{chain}' "
            f"in uniswap_v4.py. Confirm PoolManager/StateView/ReservesLens "
            f"against https://docs.uniswap.org/contracts/v4/deployments and "
            f"add them before trusting output for this chain."
        )
    key = _pool_key(rpc, chain, pool_id)
    calldata = (
        GET_POOL_TVL
        + _pad_addr(POOL_MANAGER[chain])
        + _pad_addr(key["currency0"])
        + _pad_addr(key["currency1"])
        + _pad_uint(key["fee"])
        + _pad_uint(key["tick_spacing"] & (2 ** 24 - 1))
        + _pad_addr(key["hooks"])
    )
    raw = rpc.read(RESERVES_LENS[chain], calldata, block)
    data = raw[2:]
    words = [data[i:i + 64] for i in range(0, len(data), 64)]
    if len(words) < 2:
        raise SystemExit(
            f"ReservesLens returned no data for pool {pool_id} at block "
            f"{block} — it most likely wasn't deployed yet at that block "
            f"(verify against https://optimistic.etherscan.io/address/"
            f"{RESERVES_LENS[chain]}). It can't be used for a date earlier "
            f"than its own deployment; a checkpoint that predates it needs a "
            f"different reserve-reading approach for this pool."
        )
    return {key["currency0"]: int(words[0], 16), key["currency1"]: int(words[1], 16)}
