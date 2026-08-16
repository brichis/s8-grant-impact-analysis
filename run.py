#!/usr/bin/env python3
"""S8 grant impact analysis — 40acres pilot (Targeted Scope).

Registry in, per-contract S8 ΔTVL out. Every quantity is read directly from the
incentivized contracts; prices come from DefiLlama; nothing a grantee
self-reported is an input.

    export ALCHEMY_KEY=...          # archive-capable RPC (needed: reads are on-chain)
    python run.py                   # 40acres, default
    python run.py APP-XXXX-XXXX     # any grant_id whose scope tab is filled

Method (S8 Impact Measurement Methodology):
  * Scope     : Targeted — each incentivized contract measured on its own,
                summed. (Step 1, "Targeted Scope".)
  * Formula   : ΔTVL = Σ (quantity_end − quantity_start) × price_end
  * Window    : incentive start → incentive end (execution start; see registry.py)
  * Attribution: per-contract; 100% unless a co-incentive overlapped a contract.

Supplementary context (S7-derived, labelled non-S8): retention +30d, price-qty wedge.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

import measure          # noqa: E402
import metrics          # noqa: E402
import outputs          # noqa: E402
import prices as pricing  # noqa: E402
from registry import load_grant  # noqa: E402

DEFAULT_GRANT = "APP-CS0S7GDN-MR3JI7"
BASE = Path(__file__).parent
OUT_DIR = BASE / "output"
RAW_DIR = BASE / "data"
RPC_CACHE = RAW_DIR / "rpc_cache.json"


def main() -> None:
    grant_id = next((a for a in sys.argv[1:] if a.startswith("APP-")), DEFAULT_GRANT)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/4] Registry…")
    config = load_grant(grant_id)
    print(f"      {config.grantee} ({config.grant_id})")
    print(f"      scope: Targeted, {len(config.scope_contracts)} contracts")
    print(f"      window: {config.incentive_start} → {config.incentive_end} "
          f"(snapshot {config.snapshot})")

    checkpoints = {"start": config.incentive_start,
                   "snapshot": config.snapshot,
                   "end": config.incentive_end,
                   "plus30d": config.stickiness_end}
    checkpoints = {k: v for k, v in checkpoints.items() if v is not None}

    print("[2/4] Measuring scope contracts on-chain (archive reads, cached)…")
    measured = measure.measure_all(config, checkpoints, RPC_CACHE)

    print("[3/4] Prices from DefiLlama…")
    payload = pricing.fetch_protocol(config.defillama_slug, raw_dir=RAW_DIR)
    series = pricing.price_series(payload, config.defillama_chains)
    price_map = pricing.prices_at(series, checkpoints)

    result = metrics.compute(config, measured, price_map)

    print(f"\n  S8 ΔTVL (Targeted, attributed): "
          f"${result.delta_tvl_attributed_usd:,.0f}")
    for c in result.contracts:
        print(f"    {c.pool:<8} ({c.chain:<11}) "
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
    measured.to_csv(OUT_DIR / "measured_quantities.csv", index=False)
    print(f"      output/measured_quantities.csv  (cross-check vs DefiLlama)")


if __name__ == "__main__":
    main()
