#!/usr/bin/env python3
"""S8 grant impact analysis (Targeted Scope).

Registry in, per-contract S8 ΔTVL out. Every quantity is read directly from the
incentivized contracts; prices come from DefiLlama; nothing a grantee
self-reported is an input.

    export ALCHEMY_KEY=...          # archive-capable RPC (needed: reads are on-chain)
    python run.py                   # default grant (40acres)
    python run.py APP-XXXX-XXXX     # any grant_id whose scope tab is filled
    python run.py APP-XXXX-XXXX --global   # Global Scope instead of Targeted

Method (S8 Impact Measurement Methodology):
  * Scope     : Targeted (default) — each incentivized contract measured on
                its own, summed. (Step 1, "Targeted Scope".) `--global` uses
                the methodology's other sanctioned option instead: DefiLlama's
                protocol-wide TVL change on the grant's chains, no on-chain
                reads. A disclosed trade-off (precision for data availability)
                made per grant, not a default — see global_scope.py.
  * Formula   : ΔTVL = Σ (quantity_end − quantity_start) × price_end
  * Window    : incentive start → incentive end (execution start; see registry.py)
  * Attribution: per-contract; 100% unless a co-incentive overlapped a contract.

Supplementary context (S7-derived, labelled non-S8): retention +30d, price-qty wedge.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

import global_scope      # noqa: E402
import measure          # noqa: E402
import metrics          # noqa: E402
import outputs          # noqa: E402
import prices as pricing  # noqa: E402
from registry import load_grant  # noqa: E402

DEFAULT_GRANT = "APP-CS0S7GDN-MR3JI7"
BASE = Path(__file__).parent
OUT_BASE = BASE / "output"
RAW_DIR = BASE / "data"
RPC_CACHE = RAW_DIR / "rpc_cache.json"


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main() -> None:
    args = sys.argv[1:]
    grant_id = next((a for a in args if a.startswith("APP-")), DEFAULT_GRANT)
    use_global = "--global" in args

    print("[1/4] Registry…")
    config = load_grant(grant_id)
    print(f"      {config.grantee} ({config.grant_id})")
    scope_note = ("Global (protocol-wide)" if use_global
                  else f"Targeted, {len(config.scope_contracts)} contracts")
    print(f"      scope: {scope_note}")
    print(f"      window: {config.incentive_start} → {config.incentive_end} "
          f"(snapshot {config.snapshot})")

    # One subdirectory per grantee — output/ used to be a single shared
    # directory that every run overwrote, so only the most-recently-run
    # grant's numbers ever survived. Charts need all grants' output at once.
    OUT_DIR = OUT_BASE / _slugify(config.grantee)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    checkpoints = {"start": config.incentive_start,
                   "snapshot": config.snapshot,
                   "end": config.incentive_end,
                   "plus30d": config.stickiness_end}
    checkpoints = {k: v for k, v in checkpoints.items() if v is not None}

    if use_global:
        run_global(config, checkpoints, OUT_DIR)
        return

    print("[2/4] Measuring scope contracts on-chain (archive reads, cached)…")
    measured = measure.measure_all(config, checkpoints, RPC_CACHE)

    print("[3/4] Prices from DefiLlama…")
    payload = pricing.fetch_protocol(config.defillama_slug, raw_dir=RAW_DIR)
    series = pricing.price_series(payload, config.defillama_chains)
    price_map = pricing.prices_at(series, checkpoints)
    price_map = pricing.fill_price_gaps(price_map, measured, checkpoints)

    result = metrics.compute(config, measured, price_map)

    print(f"\n  S8 ΔTVL (Targeted, attributed): "
          f"${result.delta_tvl_attributed_usd:,.0f}")
    for c in result.contracts:
        label = c.pool if c.pool.upper() == c.token else f"{c.pool} [{c.token}]"
        print(f"    {label:<14} ({c.chain:<11}) "
              f"{c.quantity_start:,.0f} → {c.quantity_end:,.0f} "
              f"@ ${c.price_end:,.4f} = ${c.delta_tvl_attributed_usd:,.0f}"
              + ("" if c.attribution_pct == 100 else f"  [{c.attribution_pct}%]"))
    if config.target_milestone1:
        print(f"    vs M1 ${config.target_milestone1:,.0f}: "
              f"{'MET' if result.milestone1_met else 'not met'}")
    if config.target_total:
        print(f"    vs total ${config.target_total:,.0f}: "
              f"{'MET' if result.total_target_met else 'not met'}")
    if result.usd_per_op is not None:
        print(f"  Efficiency: ${result.usd_per_op:,.2f} per OP")
    if result.delta_tvl_at_snapshot_usd is not None:
        print(f"  Interim M1 @ snapshot: ${result.delta_tvl_at_snapshot_usd:,.0f} "
              f"(honest-baseline side note)")
    print(f"  Supplementary — retention +30d: {result.retention_30d_pct}% · "
          f"price-qty wedge: ${result.price_qty_wedge_usd:,.0f}")

    print("\n[4/4] Writing outputs…")
    for p in (outputs.write_contract_chart(result, OUT_DIR),
              outputs.write_checkpoint_chart(config, measured, price_map, OUT_DIR),
              outputs.write_contract_table(result, OUT_DIR),
              outputs.write_scorecard(result, OUT_DIR)):
        print(f"      {p.relative_to(BASE)}")

    # DefiLlama quantity cross-check (independent of the on-chain reads)
    measured_path = OUT_DIR / "measured_quantities.csv"
    measured.to_csv(measured_path, index=False)
    print(f"      {measured_path.relative_to(BASE)}  (cross-check vs DefiLlama)")


def run_global(config, checkpoints: dict, OUT_DIR: Path) -> None:
    print("[2/4] Skipping on-chain reads (Global Scope needs none)…")
    print("[3/4] TVL from DefiLlama…")
    payload = pricing.fetch_protocol(config.defillama_slug, raw_dir=RAW_DIR)
    tvl = global_scope.tvl_at_checkpoints(config, payload, checkpoints)
    result = global_scope.compute(config, tvl)

    print(f"\n  S8 ΔTVL (Global, protocol-wide): ${result.delta_tvl_usd:,.0f}")
    print(f"    chains: {', '.join(result.chains)}")
    print(f"    TVL {config.incentive_start} → {config.incentive_end}: "
          f"${result.tvl_start_usd:,.0f} → ${result.tvl_end_usd:,.0f}")
    if config.target_milestone1:
        print(f"    vs M1 ${config.target_milestone1:,.0f}: "
              f"{'MET' if result.milestone1_met else 'not met'}")
    if config.target_total:
        print(f"    vs total ${config.target_total:,.0f}: "
              f"{'MET' if result.total_target_met else 'not met'}")
    if result.usd_per_op is not None:
        print(f"  Efficiency: ${result.usd_per_op:,.2f} per OP")
    if result.delta_tvl_at_snapshot_usd is not None:
        print(f"  Interim M1 @ snapshot: ${result.delta_tvl_at_snapshot_usd:,.0f} "
              f"(honest-baseline side note)")
    if result.retention_30d_pct is not None:
        print(f"  Supplementary — retention +30d: {result.retention_30d_pct}%")

    print("\n[4/4] Writing outputs…")
    for p in (outputs.write_scorecard(result, OUT_DIR),
              outputs.write_global_checkpoint_chart(tvl, OUT_DIR)):
        print(f"      {p.relative_to(BASE)}")


if __name__ == "__main__":
    main()
