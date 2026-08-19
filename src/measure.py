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
    pool   — AMM pool. quantity = the incentivized token's reserve held by the
             pool (balanceOf on the token, or both reserves for a 2-sided view).

All reads are point-in-time at the last block of the checkpoint's UTC day, which
is the correct tool for balances that include positions opened before the
measurement window.

The RPC plumbing (block-at-timestamp, caching, veNFT enumeration) lives in
rpc.py; this module only decides *what* to read for each contract type.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

import uniswap_v4
from rpc import ArchiveRPC, RPC_SLUG

# Selectors (function signatures -> 4-byte selector).
TOTAL_ASSETS = "0x01e1d114"   # totalAssets()
ASSET = "0x38d52e0f"          # asset()
DECIMALS = "0x313ce567"       # decimals()
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
# TOKEN_ADDRESS, RPC_SLUG all key off these, matching registry.py's
# CHAIN_TO_DEFILLAMA convention).
CHAIN_LABEL = {"OP Mainnet": "Optimism"}


def _normalize_chain(chain: str) -> str:
    return CHAIN_LABEL.get(chain, chain)


# Underlying token address per (chain, symbol), for `pool` reserve reads.
# Extend as new pools are added to the registry; verify each against the block
# explorer before trusting output that depends on it.
TOKEN_ADDRESS = {
    ("Base", "USDC"): "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    ("Optimism", "USDC"): "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
    ("Base", "CBBTC"): "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
    # OP Stack predeploys — identical address on every OP Stack chain, so these
    # are safe to hardcode (no per-chain verification needed).
    ("Optimism", "OP"): "0x4200000000000000000000000000000000000042",
    ("Base", "OP"): "0x4200000000000000000000000000000000000042",
    ("Optimism", "WETH"): "0x4200000000000000000000000000000000000006",
    ("Optimism", "ETH"): "0x4200000000000000000000000000000000000006",  # pools hold WETH, not native ETH
    ("Base", "WETH"): "0x4200000000000000000000000000000000000006",
    ("Base", "ETH"): "0x4200000000000000000000000000000000000006",
    # Confirmed by the grantee (Super DCA deployment notes), Optimism only.
    ("Optimism", "DCA"): "0xb1599cde32181f48f89683d3c5db5c5d2c7c93cc",
    ("Optimism", "WBTC"): "0x68f180fcce6836688e9084f035309e29bf0a2095",
    #                          which one Super DCA's pool actually holds.
}


def _erc20_decimals(rpc: ArchiveRPC, token: str, block: int) -> int:
    return int(rpc.read(token, DECIMALS, block), 16)


def measure_vault(rpc: ArchiveRPC, address: str, block: int) -> tuple[float, str]:
    """ERC-4626 totalAssets() in underlying units. Returns (quantity, token_symbol)."""
    asset = "0x" + rpc.read(address, ASSET, block)[-40:]
    decimals = _erc20_decimals(rpc, asset, block)
    raw = rpc.read(address, TOTAL_ASSETS, block)
    return int(raw, 16) / 10 ** decimals, asset


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


def measure_pool_reserve(rpc: ArchiveRPC, pool_address: str, chain: str,
                         token_symbol: str, block: int) -> float:
    """Incentivized token's reserve held by an AMM pool: balanceOf(pool).

    Uses the token's balance held by the pool contract, which is the reserve for
    that side. Works for any standard AMM without needing pool-specific ABIs.
    """
    key = (chain, token_symbol.upper())
    token = TOKEN_ADDRESS.get(key)
    if token is None:
        raise SystemExit(
            f"No token address mapped for {key}. Add it to TOKEN_ADDRESS in "
            f"measure.py (find it on the block explorer)."
        )
    decimals = _erc20_decimals(rpc, token, block)
    arg = pool_address[2:].rjust(64, "0")
    raw = rpc.read(token, BALANCE_OF + arg, block)
    return int(raw, 16) / 10 ** decimals


def _split_pool_label(pool_label: str) -> list[str]:
    """'cbBTC-USDC' -> ['CBBTC', 'USDC']. Two-sided pools are measured on both
    legs (both reserves), which is standard pool-TVL semantics and requires no
    change to the S8 formula — each leg is just another term in the same
    per-contract sum. A one-sided label (no '-') returns a single-element list.
    """
    return [t.strip().upper() for t in pool_label.split("-") if t.strip()]


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

    if ctype == "vault":
        quantity, _asset = measure_vault(rpc, contract["address"], block)
        return {"quantity": quantity, "block": block}

    if ctype == "lend":
        # UNVERIFIED: assumes the same ERC-4626 interface as `vault`
        # (asset()/totalAssets()). Extrafi lists two products (XLend, LYF)
        # under this type and they may not share one interface — confirm
        # against each contract's ABI on the block explorer before trusting
        # Extrafi output. If a contract doesn't implement totalAssets(), this
        # will raise (RPC read failure) rather than silently returning 0.
        quantity, _asset = measure_vault(rpc, contract["address"], block)
        return {"quantity": quantity, "block": block}

    if ctype == "loan":
        chain = _normalize_chain(contract["chain"])
        escrow = VE_ESCROW.get(chain)
        if escrow is None:
            raise SystemExit(f"No veNFT escrow mapped for {chain} in measure.py.")
        quantity, nfts = measure_loan_collateral(
            rpc, escrow, contract["address"], block)
        return {"quantity": quantity, "block": block, "nfts": nfts}

    if ctype == "pool":
        # Two-sided pools are measured on both legs (both reserves) and summed
        # — standard pool-TVL semantics, no change to the S8 formula itself.
        chain = _normalize_chain(contract["chain"])
        labels = _split_pool_label(contract["pool"])

        if uniswap_v4.is_pool_id(contract["address"]):
            reserves = uniswap_v4.pool_reserves(rpc, chain, contract["address"], block)
            legs = {}
            for label in labels:
                expected = TOKEN_ADDRESS.get((chain, label))
                if expected is None:
                    raise SystemExit(
                        f"No token address mapped for {(chain, label)}. Add it "
                        f"to TOKEN_ADDRESS in measure.py."
                    )
                match = next((a for a in reserves
                             if a.lower() == expected.lower()), None)
                if match is None:
                    raise SystemExit(
                        f"Pool {contract['address'][:10]}… labelled "
                        f"'{contract['pool']}' expects {label} "
                        f"({expected}) but the pool's actual currencies are "
                        f"{list(reserves)} — the registry label may not match "
                        f"this PoolId."
                    )
                decimals = _erc20_decimals(rpc, expected, block)
                legs[label] = reserves[match] / 10 ** decimals
            return {"legs": legs, "block": block}

        _verify_pool_contract(rpc, contract["address"], block)
        legs = {
            token: measure_pool_reserve(rpc, contract["address"], chain, token, block)
            for token in labels
        }
        return {"legs": legs, "block": block}

    raise SystemExit(
        f"Unknown scope type '{ctype}' for {contract['address'][:10]}. "
        f"Expected vault | loan | pool | lend."
    )


def measure_all(config, checkpoints: dict[str, date], cache_path) -> pd.DataFrame:
    """Measure every scope contract at every checkpoint.

    Returns a tidy frame: contract, pool, token, type, chain, checkpoint, date,
    quantity (+ nfts for loan rows). A two-sided `pool` contract contributes one
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
                for token, quantity in result["legs"].items():
                    rows.append({
                        "contract": contract["address"],
                        "pool": contract["pool"],
                        "token": token,
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
