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
  * Attribution: 100% — only the incentivized contracts are measured.

Supplementary context (S7-derived, labelled non-S8): retention +30d, price-qty wedge.
"""

from __future__ import annotations

import re
import sys
from datetime import date
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


def _print_milestones(config, result) -> None:
    """M1 is judged at the snapshot, M2 at the end (see metrics.compute).
    Printing the figure each verdict rests on keeps the two from being read
    against the wrong checkpoint, which is how a met M1 came to report as
    missed."""
    if config.target_milestone1:
        verdict = ("not evaluated (no snapshot measured)"
                   if result.milestone1_met is None
                   else ("MET" if result.milestone1_met else "not met"))
        at = (f" — ${result.delta_tvl_at_snapshot_usd:,.0f} at snapshot"
              if result.delta_tvl_at_snapshot_usd is not None else "")
        print(f"    M1 vs ${config.target_milestone1:,.0f}: {verdict}{at}")
    if config.target_total:
        end_delta = getattr(result, "delta_tvl_usd", None)
        at = f" — ${end_delta:,.0f} at end" if end_delta is not None else ""
        print(f"    M2 vs ${config.target_total:,.0f}: "
              f"{'MET' if result.total_target_met else 'not met'}{at}")


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
    window_end = config.measurement_end
    print(f"      window: {config.incentive_start} → {window_end} "
          f"(snapshot {config.snapshot})")
    if config.incentive_ongoing:
        scheduled = (f"registry end {config.incentive_end} is scheduled, not "
                     f"proven" if config.incentive_end
                     else "no end date announced by the grantee")
        print(f"      ⚠ incentive still running — {scheduled}. INTERIM "
              f"measurement as of {config.measurement_end}; +30d retention "
              f"omitted (window not closed).")

    # One subdirectory per grantee — output/ used to be a single shared
    # directory that every run overwrote, so only the most-recently-run
    # grant's numbers ever survived. Charts need all grants' output at once.
    OUT_DIR = OUT_BASE / _slugify(config.grantee)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # `plus30d` needs a window that has closed *and* 30 days elapsed since —
    # otherwise the read would land past chain head and rpc.block_at would
    # (before its guard) have silently returned today's state. Drop it rather
    # than report a retention figure the window can't support yet.
    include_plus30d = (not config.incentive_ongoing
                       and config.stickiness_end is not None
                       and config.stickiness_end <= date.today())
    checkpoints = {"start": config.incentive_start,
                   "snapshot": config.snapshot,
                   "end": config.measurement_end,
                   "plus30d": config.stickiness_end if include_plus30d else None}
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

    print(f"\n  S8 ΔTVL (Targeted): ${result.delta_tvl_usd:,.0f}")
    for c in result.contracts:
        label = c.pool if c.pool.upper() == c.token else f"{c.pool} [{c.token}]"
        print(f"    {label:<14} ({c.chain:<11}) "
              f"{measure.format_quantity(c.quantity_start)} → "
              f"{measure.format_quantity(c.quantity_end)} "
              f"@ ${c.price_end:,.4f} = ${c.delta_tvl_usd:,.0f}")

    # A token DefiLlama has no price for contributes $0 to ΔTVL. That is a
    # silent understatement, not a measured zero, so name it rather than let it
    # disappear into the total — the quantity change was real, only the price
    # is missing (e.g. syrupUSDT on Ink, which neither the protocol series nor
    # the coins API quotes).
    unpriced = [c for c in result.contracts
                if c.price_end == 0 and c.quantity_end != c.quantity_start]
    if unpriced:
        print(f"\n  ⚠ no DefiLlama price for {len(unpriced)} token(s) — each "
              f"contributed $0 to the total above, understating it:")
        for c in unpriced:
            print(f"      {c.pool} [{c.token}] ({c.chain}): "
                  f"{c.quantity_start:,.2f} → {c.quantity_end:,.2f} unpriced")

    _print_milestones(config, result)
    if result.usd_per_op is not None:
        print(f"  Efficiency: ${result.usd_per_op:,.2f} per OP")
    retention = (f"{result.retention_30d_pct}%"
                 if result.retention_30d_pct is not None
                 else "n/a (window not closed)")
    print(f"  Supplementary — retention +30d: {retention} · "
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
    print(f"    TVL over {config.window_label}: "
          f"${result.tvl_start_usd:,.0f} → ${result.tvl_end_usd:,.0f}")
    _print_milestones(config, result)
    if result.usd_per_op is not None:
        print(f"  Efficiency: ${result.usd_per_op:,.2f} per OP")
    if result.retention_30d_pct is not None:
        print(f"  Supplementary — retention +30d: {result.retention_30d_pct}%")

    print("\n[4/4] Writing outputs…")
    for p in (outputs.write_scorecard(result, OUT_DIR),
              outputs.write_global_checkpoint_chart(tvl, OUT_DIR)):
        print(f"      {p.relative_to(BASE)}")


if __name__ == "__main__":
    main()
