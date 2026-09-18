"""PancakeSwap Infinity (CL) pool reserve reads.

PancakeSwap Infinity is architecturally similar to Uniswap v4 — pools live
inside a singleton `Vault`, addressed by a 32-byte `PoolId` rather than a
callable contract — but it is a separate deployment with its own contracts,
its own `PoolKey` layout, and (critically) **no deployed reserves-lens
helper**. Uniswap v4 support in this repo (`uniswap_v4.py`) leans on
Uniswap's own `ReservesLens` periphery contract, which walks every
initialized tick on-chain and returns the summed reserves in one call.
PancakeSwap has not deployed an equivalent (checked the full `infinity-core`/
`infinity-periphery` deployment lists — only a raw `clTickLens` exists, no
TVL/reserves aggregator), so this module does the tick walk itself, off-chain,
via repeated `eth_call`s.

Deployment addresses below are from PancakeSwap's own deployment config
(`script/config/base-mainnet.json` in both `infinity-core` and
`infinity-periphery` on GitHub), cross-checked live: `poolIdToPoolKey` was
called against three real Base pool IDs from the registry and correctly
decoded currency0/currency1/fee/tickSpacing; `getSlot0`'s selector
independently matched the constant already verified for Uniswap's StateView
in `uniswap_v4.py` (same function signature, different deployment). Only
Base is verified — add + verify other chains against
https://developer.pancakeswap.finance/contracts/infinity/resources/addresses
before trusting output there.

Key simplification vs. Uniswap v4: `CLPoolManager.poolIdToPoolKey` is a
**public mapping**, so the PoolKey is one plain `eth_call` away — no
Initialize-event archaeology / binary search needed. The PoolKey layout does
differ from Uniswap's though: `(currency0, currency1, hooks, poolManager,
fee, parameters)`, where `parameters` is a bit-packed bytes32 (tickSpacing
lives at bits [16:40), per `CLPoolParametersHelper.sol`) rather than a plain
int24 field.

Reserve algorithm (replicates what a ReservesLens does on-chain, here in
Python):
  1. Resolve the PoolKey (try CLPoolManager, fall back to BinPoolManager —
     Infinity also has a separate DLMM/"Bin" pool type; none of the pools
     this was built against turned out to be Bin pools, so that path is
     unverified — it fails loud rather than silently misreading a Bin pool
     with CL math).
  2. Read the current `sqrtPriceX96`/tick (`getSlot0`) and active liquidity
     (`getLiquidity`) — the latter is used purely as a cross-check.
  3. Scan the tick bitmap (`getPoolBitmapInfo`) outward from the current
     tick's word until several consecutive words come back empty on both
     sides — live-tested against a real pool: liquidity was concentrated in
     a handful of words around the current price, so this converges in tens
     of calls, not the tens of thousands a blind full-range scan would need.
     A hard word cap guards against a pathologically wide pool — hit that
     and this raises rather than silently truncating real liquidity.
  4. Read `liquidityNet` at every initialized tick found (`getPoolTickInfo`),
     walk them in order accumulating active liquidity per interval, and sum
     each interval's token0/token1 contribution via the standard
     concentrated-liquidity amount formulas.
  5. Cross-check: the running liquidity in the interval containing the
     current tick must equal step 2's `getLiquidity()` exactly (both are
     integers) — if it doesn't, the tick scan missed something and this
     raises rather than returning a plausible-but-wrong number.

`get_sqrt_ratio_at_tick` is a straight port of Uniswap's `TickMath.sol` bit-
shift algorithm (PancakeSwap Infinity's `TickMath.sol` is byte-for-byte the
same constants) — verified live: computing it for a real pool's current tick
landed exactly between that tick's and the next tick's boundary, as it must.
Everything past that (interval amounts) is done in plain Python floats, not
Solidity's exact integer math — more than sufficient precision for a
USD-scale TVL figure, but not wei-exact.
"""

from __future__ import annotations

from rpc import ArchiveRPC

VAULT = {
    "Base": "0x238a358808379702088667322f80aC48bAd5e6c4",
}
CL_POOL_MANAGER = {
    "Base": "0xa0FfB9c1CE1Fe56963B0321B32E7A0302114058b",
}
BIN_POOL_MANAGER = {
    "Base": "0xC697d2898e0D09264376196696c51D7aBbbAA4a9",
}

POOL_ID_TO_POOL_KEY = "0x0e2d484a"    # poolIdToPoolKey(bytes32)
GET_SLOT0 = "0xc815641c"              # getSlot0(bytes32)
GET_LIQUIDITY = "0xfa6793d5"          # getLiquidity(bytes32)
GET_POOL_TICK_INFO = "0x5aa208a4"     # getPoolTickInfo(bytes32,int24)
GET_POOL_BITMAP_INFO = "0x7c352ef6"   # getPoolBitmapInfo(bytes32,int16)

MIN_TICK = -887272
MAX_TICK = 887272

# How many consecutive empty bitmap words (256 compressed ticks each) to see
# before assuming there's no more initialized liquidity in that direction.
# This only needs to be "usually right" — pool_reserves cross-checks the
# local scan's result against the pool's own getLiquidity() and escalates to
# an exhaustive full-range scan on mismatch, so a too-small value here costs
# an extra full scan rather than a wrong answer.
EMPTY_WORD_STOP = 15
# Safety cap on total words scanned (both directions combined) — hitting this
# means the pool has liquidity spread wider than any pool this was tuned
# against; fail loud rather than silently return a partial sum.
MAX_WORDS_SCANNED = 4000

# bit -> 128-bit multiplier, from Uniswap/PancakeSwap TickMath.sol
_TICK_MATH_STEPS = [
    (0x2, 0xfff97272373d413259a46990580e213a),
    (0x4, 0xfff2e50f5f656932ef12357cf3c7fdcc),
    (0x8, 0xffe5caca7e10e4e61c3624eaa0941cd0),
    (0x10, 0xffcb9843d60f6159c9db58835c926644),
    (0x20, 0xff973b41fa98c081472e6896dfb254c0),
    (0x40, 0xff2ea16466c96a3843ec78b326b52861),
    (0x80, 0xfe5dee046a99a2a811c461f1969c3053),
    (0x100, 0xfcbe86c7900a88aedcffc83b479aa3a4),
    (0x200, 0xf987a7253ac413176f2b074cf7815e54),
    (0x400, 0xf3392b0822b70005940c7a398e4b70f3),
    (0x800, 0xe7159475a2c29b7443b29c7fa6e889d9),
    (0x1000, 0xd097f3bdfd2022b8845ad8f792aa5825),
    (0x2000, 0xa9f746462d870fdf8a65dc1f90e061e5),
    (0x4000, 0x70d869a156d2a1b890bb3df62baf32f7),
    (0x8000, 0x31be135f97d08fd981231505542fcfa6),
    (0x10000, 0x9aa508b5b7a84e1c677de54f3e99bc9),
    (0x20000, 0x5d6af8dedb81196699c329225ee604),
    (0x40000, 0x2216e584f5fa1ea926041bedfe98),
    (0x80000, 0x48a170391f7dc42444e8fa2),
]


def _pad_addr(addr: str) -> str:
    return addr[2:].rjust(64, "0").lower()


def _pad_signed(n: int) -> str:
    """Two's-complement sign-extend a signed int (int16, int24, ...) to a
    full 32-byte ABI word. `n % 2**256` gives the correct wraparound
    representation for negative values directly — bit-width-specific masking
    first would zero-pad the high bits instead of sign-extending them,
    corrupting calldata for any negative tick/word (silently wrong or, as
    hit during testing here, an outright revert)."""
    return hex(n % (2 ** 256))[2:].rjust(64, "0")


def _words(raw: str) -> list[str]:
    data = raw[2:]
    return [data[i:i + 64] for i in range(0, len(data), 64)]


def _to_signed(value: int, bits: int) -> int:
    if value >= 2 ** (bits - 1):
        value -= 2 ** bits
    return value


def get_sqrt_ratio_at_tick(tick: int) -> int:
    """Port of TickMath.sol's getSqrtRatioAtTick — exact integer bit-shift
    algorithm, no floating point. Verified live against real pool state (see
    module docstring)."""
    abs_tick = abs(tick)
    ratio = (0xfffcb933bd6fad37aa2d162d1a594001 if abs_tick & 0x1
              else 0x100000000000000000000000000000000)
    for bit, multiplier in _TICK_MATH_STEPS:
        if abs_tick & bit:
            ratio = (ratio * multiplier) >> 128
    if tick > 0:
        ratio = ((1 << 256) - 1) // ratio
    return (ratio >> 32) + (0 if ratio % (1 << 32) == 0 else 1)


class PoolNotYetCreated(Exception):
    """Raised when a PoolId doesn't resolve at the requested block but does
    resolve at the chain's latest block — the pool is valid, it just didn't
    exist yet at that checkpoint (created partway through the grant window).
    Carries the PoolKey from the latest block so the caller knows the
    currencies without a second round of resolution."""
    def __init__(self, key: dict):
        self.key = key


def _resolve_pool_key(rpc: ArchiveRPC, chain: str, pool_id: str, block: int) -> tuple[str, dict]:
    """Try CLPoolManager then BinPoolManager at `block`; return (kind,
    decoded PoolKey). Raises PoolNotYetCreated if the PoolId is valid (it
    resolves at the chain's current head) but wasn't initialized yet at
    `block`, or SystemExit if it's not a valid PoolId at all."""
    result = _try_resolve(rpc, chain, pool_id, block)
    if result is not None:
        return result

    latest = rpc.latest_block()
    if block != latest:
        at_latest = _try_resolve(rpc, chain, pool_id, latest)
        if at_latest is not None:
            _kind, key = at_latest
            raise PoolNotYetCreated(key)

    raise SystemExit(
        f"PoolId {pool_id} not found in CLPoolManager or BinPoolManager on "
        f"{chain} at block {block} or at the current chain head — not a "
        f"valid PancakeSwap Infinity pool, or this chain's deployment "
        f"addresses in pancake_infinity.py are wrong/missing."
    )


def _try_resolve(rpc: ArchiveRPC, chain: str, pool_id: str, block: int) -> tuple[str, dict] | None:
    """One resolution attempt at a specific block; None if not initialized
    there under either manager."""
    for kind, managers in (("cl", CL_POOL_MANAGER), ("bin", BIN_POOL_MANAGER)):
        manager = managers.get(chain)
        if manager is None:
            continue
        raw = rpc.read(manager, POOL_ID_TO_POOL_KEY + pool_id[2:], block)
        w = _words(raw)
        currency0 = "0x" + w[0][-40:]
        if int(currency0, 16) == 0:
            continue
        currency1 = "0x" + w[1][-40:]
        hooks = "0x" + w[2][-40:]
        pool_manager = "0x" + w[3][-40:]
        fee = int(w[4], 16)
        parameters = int(w[5], 16)
        if kind == "bin":
            raise SystemExit(
                f"Pool {pool_id[:10]}… resolved under BinPoolManager, not "
                f"CLPoolManager — this is a DLMM/'Bin' pool, and this module "
                f"only implements CL (concentrated-liquidity) reserve math. "
                f"Add Bin support before trusting output for this pool."
            )
        tick_spacing = _to_signed((parameters >> 16) & 0xFFFFFF, 24)
        return kind, {
            "currency0": currency0, "currency1": currency1, "hooks": hooks,
            "pool_manager": pool_manager, "fee": fee,
            "tick_spacing": tick_spacing,
        }
    return None


def _slot0(rpc: ArchiveRPC, manager: str, pool_id: str, block: int) -> tuple[int, int]:
    raw = rpc.read(manager, GET_SLOT0 + pool_id[2:], block)
    w = _words(raw)
    sqrt_price_x96 = int(w[0], 16)
    # ABI-encoded return words for signed types are already sign-extended to
    # the full 256 bits by the encoder — decode at bits=256, not the
    # solidity-level bit width, or a negative tick decodes as a huge
    # positive number instead.
    tick = _to_signed(int(w[1], 16), 256)
    return sqrt_price_x96, tick


def _liquidity(rpc: ArchiveRPC, manager: str, pool_id: str, block: int) -> int:
    raw = rpc.read(manager, GET_LIQUIDITY + pool_id[2:], block)
    return int(raw, 16)


def _liquidity_net_batch(rpc: ArchiveRPC, manager: str, pool_id: str,
                         ticks: list[int], block: int) -> dict[int, int]:
    calls = [
        ("eth_call", [{"to": manager, "data": GET_POOL_TICK_INFO + pool_id[2:] + _pad_signed(t)}, hex(block)],
         f"{rpc.slug}:call:{manager}:{GET_POOL_TICK_INFO + pool_id[2:] + _pad_signed(t)}:{block}")
        for t in ticks
    ]
    results = rpc.call_batch(calls)
    rpc.flush()
    out = {}
    for t, raw in zip(ticks, results):
        w = _words(raw)
        out[t] = _to_signed(int(w[1], 16), 256)
    return out


def _bitmap_word(rpc: ArchiveRPC, manager: str, pool_id: str, word: int, block: int) -> int:
    raw = rpc.read(manager, GET_POOL_BITMAP_INFO + pool_id[2:] + _pad_signed(word), block)
    return int(raw, 16)


def _scan_words(rpc: ArchiveRPC, manager: str, pool_id: str, tick_spacing: int,
                words: range, block: int) -> list[int]:
    """Batched version of reading getPoolBitmapInfo across a word range — one
    JSON-RPC batch request per 100 words instead of one HTTP round trip per
    word. For a full-range scan (thousands of words) this is the difference
    between tens of minutes and well under one, and it flushes the cache
    after every batch so a late failure doesn't discard earlier progress."""
    words = list(words)
    calls = [
        ("eth_call", [{"to": manager, "data": GET_POOL_BITMAP_INFO + pool_id[2:] + _pad_signed(w)}, hex(block)],
         f"{rpc.slug}:call:{manager}:{GET_POOL_BITMAP_INFO + pool_id[2:] + _pad_signed(w)}:{block}")
        for w in words
    ]
    results = rpc.call_batch(calls)
    rpc.flush()
    found = []
    for word, raw in zip(words, results):
        bitmap = int(raw, 16)
        if bitmap:
            for bit in range(256):
                if bitmap & (1 << bit):
                    found.append(((word << 8) + bit) * tick_spacing)
    return found


def _scan_initialized_ticks_local(rpc: ArchiveRPC, manager: str, pool_id: str,
                                  tick_spacing: int, current_tick: int, block: int) -> list[int]:
    """Ticks with liquidity, found by expanding outward from the current
    price until EMPTY_WORD_STOP consecutive empty words on each side. Cheap
    (tens of calls in practice — see module docstring) and correct for the
    overwhelming majority of pools, whose liquidity clusters near the current
    price, but misses an isolated wide- or full-range position that sits
    further out than the stop threshold. `pool_reserves` cross-checks this
    scan's result and escalates to `_scan_full_range` if it doesn't
    reconcile, so an incomplete local scan fails loud rather than silently
    undercounting."""
    center_word = (current_tick // tick_spacing) >> 8
    ticks: list[int] = []
    for direction in (1, -1):
        empty_run, words_scanned, word = 0, 0, center_word
        while empty_run < EMPTY_WORD_STOP:
            if words_scanned > MAX_WORDS_SCANNED:
                raise SystemExit(
                    f"Pool {pool_id[:10]}… tick liquidity spread past "
                    f"{MAX_WORDS_SCANNED} bitmap words without "
                    f"{EMPTY_WORD_STOP} consecutive empty ones — refusing to "
                    f"guess the pool's true range. Raise MAX_WORDS_SCANNED "
                    f"in pancake_infinity.py if this pool genuinely has "
                    f"liquidity spread this wide."
                )
            bitmap = _bitmap_word(rpc, manager, pool_id, word, block)
            if bitmap == 0:
                empty_run += 1
            else:
                empty_run = 0
                for bit in range(256):
                    if bitmap & (1 << bit):
                        ticks.append(((word << 8) + bit) * tick_spacing)
            words_scanned += 1
            word += direction
    rpc.flush()
    return sorted(set(ticks))


def _scan_initialized_ticks_full(rpc: ArchiveRPC, manager: str, pool_id: str,
                                 tick_spacing: int, block: int) -> list[int]:
    """Every initialized tick across the pool's entire valid range — the
    exhaustive fallback when the local scan's cross-check fails (i.e. there's
    a real position outside the local scan's reach). For tickSpacing=1 this
    is ~6900 eth_calls, batched (see _scan_words) into ~70 HTTP round trips;
    bounded and guaranteed complete, unlike widening the local heuristic
    further with no real bound on how far a wide-range position might sit."""
    lo_word = (MIN_TICK // tick_spacing) >> 8
    hi_word = (MAX_TICK // tick_spacing) >> 8
    ticks = _scan_words(rpc, manager, pool_id, tick_spacing, range(lo_word, hi_word + 1), block)
    rpc.flush()
    return sorted(set(ticks))


def _reserves_from_ticks(ticks: list[int], liquidity_net: dict[int, int],
                         current_tick: int, sqrt_price_x96: int) -> tuple[float, float, int]:
    """Walk initialized ticks ascending, summing per-interval token amounts.
    Returns (amount0, amount1, active_liquidity_at_current_tick) — the third
    value is the cross-check against the pool's own getLiquidity()."""
    sqrt_current = sqrt_price_x96 / 2 ** 96
    amount0 = 0.0
    amount1 = 0.0
    running_liquidity = 0
    cross_check_liquidity = 0

    for i in range(len(ticks) - 1):
        lower, upper = ticks[i], ticks[i + 1]
        running_liquidity += liquidity_net[lower]
        if lower <= current_tick < upper:
            cross_check_liquidity = running_liquidity

        sqrt_lower = get_sqrt_ratio_at_tick(lower) / 2 ** 96
        sqrt_upper = get_sqrt_ratio_at_tick(upper) / 2 ** 96
        L = running_liquidity
        if L == 0:
            continue
        if sqrt_current <= sqrt_lower:
            amount0 += L * (1 / sqrt_lower - 1 / sqrt_upper)
        elif sqrt_current >= sqrt_upper:
            amount1 += L * (sqrt_upper - sqrt_lower)
        else:
            amount0 += L * (1 / sqrt_current - 1 / sqrt_upper)
            amount1 += L * (sqrt_current - sqrt_lower)

    if current_tick < ticks[0] or current_tick >= ticks[-1]:
        cross_check_liquidity = 0
    return amount0, amount1, cross_check_liquidity


def pool_reserves(rpc: ArchiveRPC, chain: str, pool_id: str, block: int) -> dict:
    """{currency_address: raw_quantity} for both sides of an Infinity CL
    pool at `block`, via an off-chain tick walk (see module docstring).

    Two-tier: tries the cheap local scan first, and only pays for a full
    range scan if that scan's on-chain cross-check fails (an isolated wide-
    range position sitting outside the local scan's reach) — first observed
    on a live pool during development, where a ~$0.00001%-of-liquidity
    position several thousand ticks out was invisible to the local scan.

    A pool created partway through a grant's window (so it doesn't exist yet
    at an earlier checkpoint) returns zero for both currencies at that
    checkpoint — a pool that doesn't exist yet genuinely holds zero reserves,
    not an unknown value, so this isn't a placeholder for missing data.
    """
    try:
        _kind, key = _resolve_pool_key(rpc, chain, pool_id, block)
    except PoolNotYetCreated as e:
        return {e.key["currency0"]: 0, e.key["currency1"]: 0}
    manager = CL_POOL_MANAGER[chain]
    tick_spacing = key["tick_spacing"]

    sqrt_price_x96, current_tick = _slot0(rpc, manager, pool_id, block)
    onchain_liquidity = _liquidity(rpc, manager, pool_id, block)

    scan_funcs = (
        ("local", lambda: _scan_initialized_ticks_local(
            rpc, manager, pool_id, tick_spacing, current_tick, block)),
        ("full-range", lambda: _scan_initialized_ticks_full(
            rpc, manager, pool_id, tick_spacing, block)),
    )
    for scan_name, scan in scan_funcs:
        ticks = scan()  # lazy — only pay for the full-range scan if local fails
        if not ticks:
            continue
        liquidity_net = _liquidity_net_batch(rpc, manager, pool_id, ticks, block)
        amount0, amount1, cross_check = _reserves_from_ticks(
            ticks, liquidity_net, current_tick, sqrt_price_x96)
        if cross_check == onchain_liquidity:
            return {key["currency0"]: round(amount0), key["currency1"]: round(amount1)}
        if scan_name == "local":
            continue  # escalate to the full-range scan
        raise SystemExit(
            f"Pool {pool_id[:10]}… reserve tick-walk is inconsistent with "
            f"on-chain state even after a full-range scan: reconstructed "
            f"active liquidity at the current tick is {cross_check}, but "
            f"getLiquidity() reports {onchain_liquidity}. This points at a "
            f"bug in the tick-walk math itself, not scan coverage — do not "
            f"trust this pool's output until that's found."
        )

    raise SystemExit(
        f"Pool {pool_id[:10]}… has no initialized ticks anywhere in its "
        f"valid range — either it's genuinely empty or something upstream "
        f"(PoolKey/tick_spacing decode) is wrong. Verify manually before "
        f"trusting a zero here."
    )
