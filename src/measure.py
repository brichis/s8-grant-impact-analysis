"""Per-contract measurement engine (Targeted Scope).

Under Targeted Scope each incentivized contract in the registry `scope` tab is
measured on its own, and the grant's ΔTVL is the sum across contracts. This is
the more rigorous option in the S8 methodology (Step 1, "Targeted Scope"):

    "If the grant targets specific pools limit the TVL measurement to those pools."

Because we measure only what was incentivized, there is nothing to proportion
away afterwards — attribution reduces to a per-contract co-incentive flag rather
than a protocol-wide proration.

Each contract's token quantity at a given block is read directly from chain
state, dispatched by the `type` column in the scope tab:

    vault  — ERC-4626 lending vault. quantity = totalAssets() (underlying units).
    loan   — holds veNFTs as collateral. quantity = governance token locked
             across the veNFTs the contract owns (sum of locked(tokenId)).
    pool   — AMM pool ("pool", "pool (V3)", or "pool (Infinity)"). quantity =
             the incentivized token's reserve(s). Standard v2/v3-style pools
             read balanceOf(pool) directly; Uniswap-v4-style singleton pools
             (32-byte PoolId instead of a pool contract) dispatch to
             uniswap_v4.py or pancake_infinity.py depending on which
             singleton deployment the pool belongs to.

All reads are point-in-time at the last block of the checkpoint's UTC day, which
is the correct tool for balances that include positions opened before the
measurement window.

The RPC plumbing (block-at-timestamp, caching, veNFT enumeration) lives in
rpc.py; this module only decides *what* to read for each contract type.
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

import extrafi
import pancake_infinity
import uniswap_v4
from rpc import ArchiveRPC, RPC_SLUG

# Selectors (function signatures -> 4-byte selector).
TOTAL_ASSETS = "0x01e1d114"   # totalAssets()
ASSET = "0x38d52e0f"          # asset()
DECIMALS = "0x313ce567"       # decimals()
SYMBOL = "0x95d89b41"         # symbol()
LOCKED = "0xb45a3c0e"         # locked(uint256) -> (int128 amount, uint256 end)
BALANCE_OF = "0x70a08231"     # balanceOf(address)
TOKEN0 = "0x0dfe1681"          # token0() — v2/v3 pool sanity check
TOKEN1 = "0xd21220a7"          # token1()

# Voting-escrow contracts that custody `loan` collateral, per chain.
# Confirm each against the block explorer before relying on the output.
VE_ESCROW = {
    "Optimism": "0xfaf8fd17d9840595845582fcb047df13f006787d",   # veVELO
    "Base": "0xebf418fe2512e7e6bd9b87a8f0f294acdc67e6b4",       # veAERO
}

# Registry chain labels -> the canonical labels used below (VE_ESCROW,
# RPC_SLUG, uniswap_v4/pancake_infinity's deployment dicts all key off these,
# matching registry.py's CHAIN_TO_DEFILLAMA convention).
CHAIN_LABEL = {"OP Mainnet": "Optimism"}


def _normalize_chain(chain: str) -> str:
    return CHAIN_LABEL.get(chain, chain)


# Registry pool labels vs. a token's own on-chain symbol() sometimes
# legitimately differ (e.g. OP-Stack pools hold WETH, never native ETH, but
# the registry still labels the leg "ETH"). Add entries here only for
# confirmed, deliberate mismatches — not as a shortcut around verification.
SYMBOL_ALIASES = {
    "ETH": "WETH",
    "USDT0": "USD₮0",
}


def _erc20_decimals(rpc: ArchiveRPC, token: str, block: int) -> int:
    return int(rpc.read(token, DECIMALS, block), 16)


def _erc20_symbol(rpc: ArchiveRPC, token: str, block: int) -> str:
    raw = rpc.read(token, SYMBOL, block)
    data = bytes.fromhex(raw[2:])
    if len(data) < 64:
        raise SystemExit(
            f"{token} symbol() returned unparseable data ({raw}) — not a "
            f"standard string-returning ERC20."
        )
    length = int.from_bytes(data[32:64], "big")
    return data[64:64 + length].decode("utf-8", errors="replace").strip("\x00")


def _match_label_to_currency(rpc: ArchiveRPC, label: str, currencies: list[str],
                             block: int) -> str:
    """Match a registry pool-label token (e.g. 'USDC') to one of a pool's
    actual on-chain currencies, by reading each currency's own symbol().

    Replaces a hand-maintained symbol->address table: with pools being added
    to the registry continuously, pre-populating and block-explorer-verifying
    one address per new token symbol doesn't scale, and is exactly the kind
    of manual step that's easy to get wrong silently. Reading the pool's own
    currencies and matching by their real symbol() verifies itself every run.
    """
    wanted = SYMBOL_ALIASES.get(label.upper(), label.upper())
    found = {}
    for addr in currencies:
        sym = _erc20_symbol(rpc, addr, block).upper()
        found[sym] = addr
        if sym == wanted:
            return addr
    raise SystemExit(
        f"Pool label token '{label}' (on-chain symbol '{wanted}') doesn't "
        f"match either of this pool's currencies: {found}. If the registry "
        f"label and the token's real symbol() legitimately differ, add a "
        f"SYMBOL_ALIASES entry in measure.py."
    )


def measure_vault(rpc: ArchiveRPC, address: str, block: int) -> tuple[float, str]:
    """ERC-4626 totalAssets() in underlying units. Returns (quantity, token_symbol)."""
    asset = "0x" + rpc.read(address, ASSET, block)[-40:]
    decimals = _erc20_decimals(rpc, asset, block)
    raw = rpc.read(address, TOTAL_ASSETS, block)
    return int(raw, 16) / 10 ** decimals, asset


def _try_erc4626(rpc: ArchiveRPC, address: str, block: int) -> tuple[float, str] | None:
    """None if `address` doesn't implement asset() -- used purely for `lend`
    architecture detection (see measure_contract), where a revert just means
    "not this one", not an error."""
    try:
        raw = rpc.read(address, ASSET, block)
    except RuntimeError:
        return None
    if not raw or raw in ("0x", "0x" + "0" * 64):
        return None
    return measure_vault(rpc, address, block)


def measure_loan_collateral(rpc: ArchiveRPC, escrow: str, loan_address: str,
                            block: int) -> tuple[float, int]:
    """Governance token locked across the veNFTs held by the loan contract.

    Returns (locked_total, nft_count). Enumerates ERC-721 transfers in/out of the
    loan contract up to `block`, then reads locked(tokenId) for each held NFT.
    """
    moves = (
        [(tid, "in") for tid in rpc.venft_transfers(escrow, loan_address, "in", block)]
        + [(tid, "out") for tid in rpc.venft_transfers(escrow, loan_address, "out", block)]
    )
    held: dict[str, str] = {}
    for token_id, direction in moves:
        held[token_id] = direction
    owned = [tid for tid, d in held.items() if d == "in"]

    total = 0.0
    for i, token_id in enumerate(owned, 1):
        arg = token_id[2:].rjust(64, "0")
        raw = rpc.read(escrow, LOCKED + arg, block)
        amount = int(raw[2:66], 16)
        if amount >= 2 ** 127:            # int128 negative
            amount -= 2 ** 128
        total += amount / 1e18
        if i % 100 == 0:
            rpc.flush()
    rpc.flush()
    return total, len(owned)


def _verify_pool_contract(rpc: ArchiveRPC, address: str, block: int) -> None:
    """balanceOf(address) never reverts, even for a non-pool address (an EOA,
    a hook/gauge contract, a token contract) — it just silently returns
    whatever that address happens to hold, which would look like a plausible
    but meaningless reserve number. Confirm the address actually implements
    the standard v2/v3 pool interface (token0()/token1()) before trusting a
    balanceOf read on it.
    """
    for selector, name in ((TOKEN0, "token0()"), (TOKEN1, "token1()")):
        try:
            raw = rpc.read(address, selector, block)
        except RuntimeError:
            raw = None
        if not raw or raw == "0x" or len(raw) < 66:
            raise SystemExit(
                f"{address} doesn't implement {name} — it doesn't look like a "
                f"v2/v3 pool contract. If this is a Uniswap v4 pool, the "
                f"registry should have its 32-byte PoolId here instead of a "
                f"20-byte address; if it's some other AMM, measure_pool_reserve "
                f"needs a different check."
            )


def measure_pool_reserve(rpc: ArchiveRPC, pool_address: str, token: str,
                         block: int) -> float:
    """Incentivized token's reserve held by an AMM pool: balanceOf(pool).

    Uses the token's balance held by the pool contract, which is the reserve for
    that side. Works for any standard AMM without needing pool-specific ABIs.
    `token` is the token's own contract address, already resolved (see
    _match_label_to_currency) — this function does no symbol lookup itself.
    """
    decimals = _erc20_decimals(rpc, token, block)
    arg = pool_address[2:].rjust(64, "0")
    raw = rpc.read(token, BALANCE_OF + arg, block)
    return int(raw, 16) / 10 ** decimals


# Registry `pool` labels for V3-style pools carry a trailing fee tier, e.g.
# "EURC-USDC 0.01%" — strip it before splitting into legs.
_FEE_SUFFIX = re.compile(r"\s+[\d.]+%$")


_POOL_TYPE_PREFIX = re.compile(r"^(CL\d+|sAMM|vAMM)-", re.IGNORECASE)


def _split_pool_label(pool_label: str) -> list[str]:
    """'cbBTC-USDC' -> ['CBBTC', 'USDC']; 'USDC/DAI' -> ['USDC', 'DAI'] (Hydrex
    uses '/' instead of '-'); 'CL100-USDC/WETH' -> ['USDC', 'WETH'] (Velodrome
    prefixes CL pools with a tick-spacing tag and stable/volatile pools with
    'sAMM-'/'vAMM-' — stripped before splitting so it isn't mistaken for a
    third leg). Two-sided pools are measured on both legs (both reserves),
    which is standard pool-TVL semantics and requires no change to the S8
    formula — each leg is just another term in the same per-contract sum. A
    one-sided label (no separator) returns a single-element list.
    """
    label = _FEE_SUFFIX.sub("", pool_label.strip())
    label = _POOL_TYPE_PREFIX.sub("", label)
    return [t.strip().upper() for t in re.split(r"[-/]", label) if t.strip()]


def _token_symbol(pool_label: str) -> str:
    """The token whose price values this contract.

    For veNFT loan collateral the pool label is 've<TOKEN>'; the priced token is
    the underlying (veVELO -> VELO). For vaults/pools the label is already the
    token symbol.
    """
    label = pool_label.strip()
    if label.lower().startswith("ve") and len(label) > 2:
        return label[2:].upper()
    return label.upper()


def _end_of_day_ts(day: date) -> int:
    return int(pd.Timestamp(day).replace(
        hour=23, minute=59, second=59).to_pydatetime().timestamp())


def measure_contract(rpc: ArchiveRPC, contract: dict, day: date) -> dict:
    """Measure one scope contract's incentivized token quantity on a date."""
    block = rpc.block_at(_end_of_day_ts(day))
    ctype = contract["type"]
    address = contract["address"]

    if ctype in ("vault", "lend", "loan") and not rpc.has_code(address, block):
        if rpc.has_code(address, rpc.latest_block()):
            # Contract created partway through the grant window -- it
            # genuinely didn't exist yet at this checkpoint, so its quantity
            # genuinely was zero (not an unknown value). Same handling as the
            # `pool` branch below for a pool created mid-window.
            return {"quantity": 0.0, "block": block, "address": None}
        raise SystemExit(
            f"{address} has no contract code at block {block} or at the "
            f"current chain head -- not a valid {ctype} address."
        )

    if ctype == "vault":
        quantity, asset = measure_vault(rpc, contract["address"], block)
        return {"quantity": quantity, "block": block, "address": asset}

    if ctype == "lend":
        # `lend` spans lending markets from more than one grantee, with
        # unrelated on-chain shapes the registry `type` column doesn't
        # distinguish: Curve LlamaLend's market is a plain ERC-4626 vault
        # (same interface as the `vault` type above), while Extrafi's
        # XLend/LYF are not ERC-4626 at all -- see extrafi.py for those two
        # interfaces and how they're told apart. Detected by interface probe,
        # ERC-4626 tried first since it's the cheaper, single-call check.
        erc4626 = _try_erc4626(rpc, contract["address"], block)
        if erc4626 is not None:
            quantity, asset = erc4626
            return {"quantity": quantity, "block": block, "address": asset}
        quantity, asset = extrafi.measure_lend(rpc, contract["address"], block)
        return {"quantity": quantity, "block": block, "address": asset}

    if ctype == "loan":
        chain = _normalize_chain(contract["chain"])
        escrow = VE_ESCROW.get(chain)
        if escrow is None:
            raise SystemExit(f"No veNFT escrow mapped for {chain} in measure.py.")
        quantity, nfts = measure_loan_collateral(
            rpc, escrow, contract["address"], block)
        return {"quantity": quantity, "block": block, "nfts": nfts}

    if ctype.startswith("pool"):
        # Two-sided pools are measured on both legs (both reserves) and summed
        # — standard pool-TVL semantics, no change to the S8 formula itself.
        # `type` values seen: "pool" (bare — Super DCA's v4 pools), "pool
        # (V3)", "pool (Infinity)". The parenthetical, when present, picks
        # the protocol; address shape (32-byte PoolId vs. 20-byte contract)
        # is the fail-loud backstop, not the primary signal.
        chain = _normalize_chain(contract["chain"])
        address = contract["address"]
        labels = _split_pool_label(contract["pool"])
        is_infinity = "infinity" in ctype.lower()

        if is_infinity or uniswap_v4.is_pool_id(address):
            if not uniswap_v4.is_pool_id(address):
                raise SystemExit(
                    f"{address} is typed '{ctype}' but isn't a 32-byte "
                    f"PoolId — check the registry row."
                )
            module = pancake_infinity if is_infinity else uniswap_v4
            reserves = module.pool_reserves(rpc, chain, address, block)
            remaining = list(reserves)
            legs = {}
            leg_addresses = {}
            for label in labels:
                # Match against the currencies not yet claimed by an earlier
                # label in this same pool — needed for pools where both legs
                # share a symbol (e.g. a legacy-bridged token that never
                # updated its on-chain symbol() off "USDC"), so the second
                # label doesn't re-match the first leg's currency and drop
                # the other leg's reserve silently. Same-label legs (that
                # really are the same token symbol) are summed, not
                # overwritten — both price at the same token's price anyway.
                match = _match_label_to_currency(rpc, label, remaining, block)
                remaining = [c for c in remaining if c != match]
                decimals = _erc20_decimals(rpc, match, block)
                legs[label] = legs.get(label, 0.0) + reserves[match] / 10 ** decimals
                leg_addresses[label] = match
            return {"legs": legs, "leg_addresses": leg_addresses, "block": block}

        if not rpc.has_code(address, block):
            if rpc.has_code(address, rpc.latest_block()):
                # Pool created partway through the grant window — it
                # genuinely didn't exist yet at this checkpoint, so its
                # reserves genuinely were zero (not an unknown value). Same
                # handling as pancake_infinity.pool_reserves for the
                # equivalent Infinity case.
                return {"legs": {label: 0.0 for label in labels},
                        "leg_addresses": {}, "block": block}
            raise SystemExit(
                f"{address} has no contract code at block {block} or at the "
                f"current chain head — not a valid pool address."
            )

        _verify_pool_contract(rpc, address, block)
        token0 = "0x" + rpc.read(address, TOKEN0, block)[-40:]
        token1 = "0x" + rpc.read(address, TOKEN1, block)[-40:]
        remaining = [token0, token1]
        legs = {}
        leg_addresses = {}
        for label in labels:
            # See the matching v4/Infinity branch above for why matching is
            # done against `remaining` (not the full pair) and legs are
            # summed rather than overwritten.
            match = _match_label_to_currency(rpc, label, remaining, block)
            remaining = [c for c in remaining if c != match]
            legs[label] = legs.get(label, 0.0) + measure_pool_reserve(rpc, address, match, block)
            leg_addresses[label] = match
        return {"legs": legs, "leg_addresses": leg_addresses, "block": block}

    raise SystemExit(
        f"Unknown scope type '{ctype}' for {contract['address'][:10]}. "
        f"Expected vault | loan | pool | lend."
    )


def measure_all(config, checkpoints: dict[str, date], cache_path) -> pd.DataFrame:
    """Measure every scope contract at every checkpoint.

    Returns a tidy frame: contract, pool, token, type, chain, checkpoint, date,
    quantity, address (the token's own on-chain contract address — used by
    prices.py to fill DefiLlama price gaps without a second symbol->address
    table) (+ nfts for loan rows). A two-sided `pool` contract contributes one
    row per leg (per token), all sharing the same `contract` address — metrics.py
    groups by (contract, token), not contract alone, so legs don't collide.
    """
    if not config.scope_contracts:
        raise SystemExit(
            f"{config.grant_id} has no scope contracts in the registry. "
            f"Targeted Scope needs them; fill the scope tab or use Global Scope."
        )

    rpc_by_slug: dict[str, ArchiveRPC] = {}

    def rpc_for(chain_label: str) -> ArchiveRPC:
        slug = RPC_SLUG.get(chain_label)
        if slug is None:
            raise SystemExit(f"No RPC slug for chain '{chain_label}'.")
        if slug not in rpc_by_slug:
            rpc_by_slug[slug] = ArchiveRPC(slug, cache_path)
        return rpc_by_slug[slug]

    rows = []
    for contract in config.scope_contracts:
        rpc = rpc_for(contract["chain"])
        print(f"  {contract['type']:<5} {contract['pool']:<8} "
              f"{contract['address'][:10]}… ({contract['chain']})")
        for label, day in checkpoints.items():
            result = measure_contract(rpc, contract, day)

            if "legs" in result:
                leg_addresses = result.get("leg_addresses", {})
                for token, quantity in result["legs"].items():
                    rows.append({
                        "contract": contract["address"],
                        "pool": contract["pool"],
                        "token": token,
                        "address": leg_addresses.get(token),
                        "type": contract["type"],
                        "chain": contract["chain"],
                        "checkpoint": label,
                        "date": day.isoformat(),
                        "quantity": quantity,
                    })
                legs_str = ", ".join(f"{t}={q:,.0f}" for t, q in result["legs"].items())
                print(f"      {label:<10} {day}  {legs_str}")
                continue

            row = {
                "contract": contract["address"],
                "pool": contract["pool"],
                "token": _token_symbol(contract["pool"]),
                "address": result.get("address"),
                "type": contract["type"],
                "chain": contract["chain"],
                "checkpoint": label,
                "date": day.isoformat(),
                "quantity": result["quantity"],
            }
            if "nfts" in result:
                row["nfts"] = result["nfts"]
            rows.append(row)
            extra = f", {result['nfts']} NFTs" if "nfts" in result else ""
            print(f"      {label:<10} {day}  qty={result['quantity']:,.0f}{extra}")
    return pd.DataFrame(rows)
