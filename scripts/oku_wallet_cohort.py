#!/usr/bin/env python3
"""Oku (APP-SJI1PDNL-Z0UTM9) — Targeted Scope by wallet cohort.

Oku is a router/frontend, not a TVL-holding protocol: it has no dedicated
contract, and the registry's `defillama_slug` cell for this grant is blank
(the "oku-trade" value present in the row is the `oso_slug` column — Open
Source Observer, a different data source — not DefiLlama; a direct check of
api.llama.fi/protocol/oku-trade also 404s, i.e. DefiLlama doesn't track Oku
as its own protocol either way). So neither the normal per-contract Targeted
Scope nor the --global fallback applies as-is.

Oku's Optimism campaign routes users into Morpho's Gauntlet USDC Prime vault
on Optimism (0xC30ce6A5758786e0F640cC5f881Dd96e9a1C5C59 — the only vault
Morpho's own API lists as `listed: true` for chainId 10; confirmed live via
asset() == Optimism USDC and totalAssets() ~= $1.1M as of 2026-08-20). That
vault is shared with every other Morpho depositor, so Delta(vault
totalAssets) over the grant window is not Oku's impact — it's everyone's.

This script measures the same Sigma(qty_end - qty_start) x price_end formula
as the rest of the pipeline, but scoped to just the wallets Oku itself paid
out incentives to, rather than to the whole vault. For each wallet: vault
shares (balanceOf) at incentive_start and incentive_end, each converted to
underlying USDC via convertToAssets() at that same block (share price moves
over time, so the conversion must use the block-matched rate). This is a
deliberate, disclosed Targeted-Scope variant (cohort-scoped instead of
contract-scoped) for grantees with no dedicated contract of their own -- not
a change to the S8 formula, window, or the per-contract default used
everywhere else in the registry.

The wallet cohort itself comes from the registry's own `oku-wallet-cohort`
tab (id,
wallet_address, date, Paid?, OP_Amount_Received) -- the registry is the only
hand-maintained input everywhere else in this pipeline, and this script
follows the same rule rather than reading a locally-downloaded payout CSV.

Usage:
    export ALCHEMY_KEY=...
    python scripts/oku_wallet_cohort.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from chains import rpc_slug  # noqa: E402
from registry import GVIZ_URL, SHEET_ID, _read_tab, _to_date, load_scope  # noqa: E402
from rpc import ArchiveRPC  # noqa: E402

GRANT_ID = "APP-SJI1PDNL-Z0UTM9"
# gviz silently falls back to the FIRST tab when a named tab is absent, so a
# rename here reads the grantees tab instead of failing — _fetch_oku_wallets
# checks for the wallet_address header precisely to catch that.
OKU_TAB = "oku-wallet-cohort"
BASE = Path(__file__).parent.parent
RPC_CACHE = BASE / "data" / "rpc_cache.json"

BALANCE_OF = "0x70a08231"        # balanceOf(address)
CONVERT_TO_ASSETS = "0x07a2d13a"  # convertToAssets(uint256)
ASSET = "0x38d52e0f"              # asset()
DECIMALS = "0x313ce567"           # decimals()
COINS_API_URL = "https://coins.llama.fi/prices/historical/{ts}/{coins}"

BATCH_SIZE = 40


def _addr_arg(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


def _end_of_day_ts(day) -> int:
    return int(pd.Timestamp(day).replace(hour=23, minute=59, second=59).timestamp())


def _batch_eth_call(rpc: ArchiveRPC, calls: list[tuple[str, str, int, str]]) -> list[str]:
    """calls: (to, data, block, cache_key). One HTTP round trip per BATCH_SIZE
    calls instead of one per call — this script does hundreds of independent
    per-wallet reads, and that's the difference between ~10 requests and
    ~250. Self-contained here (rather than ArchiveRPC.call_batch, which this
    grant's dedicated branch doesn't carry) so this script has no dependency
    beyond what's already on main.
    """
    results: list[str | None] = [None] * len(calls)
    pending = []
    for i, (to, data, block, key) in enumerate(calls):
        if key in rpc.cache:
            results[i] = rpc.cache[key]
        else:
            pending.append((i, to, data, block, key))

    for start in range(0, len(pending), BATCH_SIZE):
        chunk = pending[start:start + BATCH_SIZE]
        payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
                    "params": [{"to": to, "data": data}, hex(block)]}
                   for i, to, data, block, _ in chunk]
        for attempt in range(6):
            resp = requests.post(rpc.url, timeout=60, json=payload).json()
            by_id = {r.get("id"): r for r in resp} if isinstance(resp, list) else {}
            errors = [r for r in by_id.values() if "result" not in r]
            if errors and any(
                e.get("error", {}).get("code") == 429
                or any(w in str(e.get("error", "")).lower()
                       for w in ("rate", "capacity", "compute unit"))
                for e in errors
            ):
                time.sleep(2 * (attempt + 1))
                continue
            if len(by_id) != len(chunk):
                raise RuntimeError(
                    f"batch call returned {len(by_id)} results for {len(chunk)} requests — {resp}")
            for i, to, data, block, key in chunk:
                r = by_id[i]
                if "result" not in r:
                    raise RuntimeError(f"RPC error: {r.get('error')}")
                results[i] = r["result"]
                rpc.cache[key] = r["result"]
            break
        else:
            raise RuntimeError("rate-limited/network-flaky repeatedly")
        time.sleep(0.15)
    return results


def _has_code(rpc: ArchiveRPC, address: str, block: int) -> bool:
    key = f"{rpc.slug}:code:{address}:{block}"
    if key not in rpc.cache:
        resp = requests.post(rpc.url, timeout=30, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, hex(block)],
        }).json()
        rpc.cache[key] = resp["result"]
    code = rpc.cache[key]
    return code not in (None, "0x", "0x0")


def _cohort_vault() -> tuple[str, str]:
    """(vault address, chain) for this grant, from the registry `scope` tab.

    Oku has no contract of its own — the campaign routes deposits into a
    shared Morpho vault — but the address it routes into is still a measured
    contract, so it belongs in the registry alongside every other one rather
    than as a constant here.
    """
    rows = load_scope(GRANT_ID)
    if len(rows) != 1:
        raise SystemExit(
            f"Expected exactly one scope row for {GRANT_ID}, found {len(rows)}. "
            f"This grant is measured as a single shared vault plus a wallet "
            f"cohort, not as a set of contracts."
        )
    row = rows[0]
    if row["type"] != "wallet-cohort":
        raise SystemExit(
            f"{GRANT_ID}'s scope row is typed '{row['type']}', expected "
            f"'wallet-cohort'. If it now has a contract of its own, it should "
            f"go through run.py instead of this script."
        )
    return row["address"], row["chain"]


def _fetch_oku_wallets() -> pd.DataFrame:
    """The registry's `oku-wallet-cohort` tab: id, wallet_address, date, Paid?,
    OP_Amount_Received. Same live-Google-Sheet-as-CSV pattern as
    registry._read_tab, but anchored on 'wallet_address' rather than
    'grant_id' for header detection -- this tab has no grant_id column,
    it's one grant's own recipient list, not a cross-grant tab.
    """
    url = GVIZ_URL.format(sheet_id=SHEET_ID, tab=OKU_TAB)
    text = requests.get(url, timeout=60).text
    if text.lstrip().startswith("<"):
        raise SystemExit(
            f"Registry tab '{OKU_TAB}' returned HTML, not CSV — check the tab name "
            "or sharing settings."
        )
    lines = text.splitlines()
    header_row = next(
        (i for i, line in enumerate(lines) if "wallet_address" in line.lower()), None
    )
    if header_row is None:
        raise SystemExit(
            f"No 'wallet_address' column in the registry's '{OKU_TAB}' tab — "
            f"if the tab was renamed, gviz silently served a different one.")
    frame = pd.read_csv(io.StringIO("\n".join(lines[header_row:])), dtype=str)
    frame.columns = [c.strip() for c in frame.columns]
    if frame["wallet_address"].dropna().empty:
        raise SystemExit(f"Registry '{OKU_TAB}' tab has a wallet_address column but no rows.")
    return frame


def main() -> None:
    # load_grant() requires a defillama_slug (used for the normal per-contract
    # pipeline's price series) which Oku's registry row doesn't have — its
    # "oku-trade" value is in the oso_slug column, a different data source,
    # not DefiLlama. This script only needs the incentive window, so read the
    # windows tab directly rather than going through the full GrantConfig.
    windows = _read_tab("windows")
    wmatch = windows[windows["grant_id"].astype(str).str.strip() == GRANT_ID]
    if wmatch.empty:
        raise SystemExit(f"Grant {GRANT_ID} has no row in the windows tab.")
    w = wmatch.iloc[0]
    incentive_start = _to_date(w["incentive_start_date"])
    incentive_end = _to_date(w["incentive_end_date"])
    if not incentive_start or not incentive_end:
        raise SystemExit(f"Grant {GRANT_ID} is missing incentive dates in the windows tab.")
    vault, chain = _cohort_vault()
    print(f"Oku (Optimism User Acquisition) ({GRANT_ID})")
    print(f"window: {incentive_start} -> {incentive_end}")
    print(f"vault:  {vault} ({chain}, from the registry scope tab)")

    df = _fetch_oku_wallets()
    wallets = sorted({w.strip().lower() for w in df["wallet_address"].dropna()})
    paid_true = (df["Paid?"].astype(str).str.strip().str.upper() == "TRUE").sum()
    print(f"{len(wallets)} unique wallets in the registry's '{OKU_TAB}' tab "
          f"({paid_true} marked Paid=TRUE by Oku — not relied on here; every "
          f"wallet is checked on-chain independently)")

    rpc = ArchiveRPC(rpc_slug(chain), RPC_CACHE)
    block_start = rpc.block_at(_end_of_day_ts(incentive_start))
    block_end = rpc.block_at(_end_of_day_ts(incentive_end))
    print(f"block_start={block_start} ({incentive_start})  "
          f"block_end={block_end} ({incentive_end})")

    if not _has_code(rpc, vault, block_start):
        raise SystemExit(
            f"{vault} has no code at block {block_start} ({incentive_start}) "
            f"— the vault didn't exist yet at incentive_start. qty_start can't be "
            f"assumed 0 without confirming this; investigate before trusting output."
        )

    asset = "0x" + rpc.read(vault, ASSET, block_end)[-40:]
    decimals = int(rpc.read(asset, DECIMALS, block_end), 16)

    # Phase 1: shares at both checkpoints, batched.
    share_calls = []
    for w in wallets:
        arg = _addr_arg(w)
        share_calls.append((vault, BALANCE_OF + arg, block_start,
                            f"{rpc.slug}:call:{vault}:{BALANCE_OF}{arg}:{block_start}"))
        share_calls.append((vault, BALANCE_OF + arg, block_end,
                            f"{rpc.slug}:call:{vault}:{BALANCE_OF}{arg}:{block_end}"))
    share_results = _batch_eth_call(rpc, share_calls)
    rpc.flush()

    shares_start, shares_end = {}, {}
    for i, w in enumerate(wallets):
        shares_start[w] = int(share_results[2 * i], 16)
        shares_end[w] = int(share_results[2 * i + 1], 16)

    # Phase 2: convertToAssets only for nonzero share balances (block-matched).
    convert_calls, convert_index = [], []
    for w in wallets:
        if shares_start[w] > 0:
            arg = hex(shares_start[w])[2:].rjust(64, "0")
            key = f"{rpc.slug}:call:{vault}:{CONVERT_TO_ASSETS}{arg}:{block_start}"
            convert_calls.append((vault, CONVERT_TO_ASSETS + arg, block_start, key))
            convert_index.append((w, "start"))
        if shares_end[w] > 0:
            arg = hex(shares_end[w])[2:].rjust(64, "0")
            key = f"{rpc.slug}:call:{vault}:{CONVERT_TO_ASSETS}{arg}:{block_end}"
            convert_calls.append((vault, CONVERT_TO_ASSETS + arg, block_end, key))
            convert_index.append((w, "end"))
    convert_results = _batch_eth_call(rpc, convert_calls)
    rpc.flush()

    assets_start, assets_end = {}, {}
    for (w, which), raw in zip(convert_index, convert_results):
        val = int(raw, 16) / 10 ** decimals
        (assets_start if which == "start" else assets_end)[w] = val

    rows = []
    no_position = []
    for w in wallets:
        a_start = assets_start.get(w, 0.0)
        a_end = assets_end.get(w, 0.0)
        rows.append({"wallet": w, "qty_start": a_start, "qty_end": a_end,
                      "delta": a_end - a_start})
        if a_start == 0.0 and a_end == 0.0:
            no_position.append(w)

    result = pd.DataFrame(rows)
    total_delta = result["delta"].sum()

    ts = _end_of_day_ts(incentive_end)
    coin = f"optimism:{asset}"
    resp = requests.get(COINS_API_URL.format(ts=ts, coins=coin), timeout=30)
    resp.raise_for_status()
    price_end = resp.json()["coins"].get(coin, {}).get("price")
    if price_end is None:
        raise SystemExit(f"DefiLlama has no historical price for {coin} at {ts}.")

    print(f"\nasset={asset} decimals={decimals} price_end(DefiLlama)=${price_end:.4f}")
    print(f"wallets with a nonzero position at start or end: "
          f"{len(wallets) - len(no_position)} / {len(wallets)}")
    if no_position:
        print(f"NO position found in {vault} for {len(no_position)} wallets "
              f"(never held shares at start or end — either didn't deposit "
              f"here, used a different vault, or withdrew before block_end):")
        for w in no_position:
            print(f"  {w}")

    print(f"\nSigma qty_start = {result['qty_start'].sum():,.2f} {asset[:6]}...")
    print(f"Sigma qty_end   = {result['qty_end'].sum():,.2f}")
    print(f"Sigma delta     = {total_delta:,.2f}")
    print(f"delta x price_end = ${total_delta * price_end:,.2f}")
    print(f"\n(Oku's self-reported figure: $38,357 supplied to Morpho on Optimism, 67 users)")

    out_path = BASE / "output" / "oku_wallet_cohort.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    print(f"\nPer-wallet detail written to {out_path}")


if __name__ == "__main__":
    main()
