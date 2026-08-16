"""Global Scope S8 ΔTVL — the methodology's other sanctioned option (Step 1):

    "If the grant targets the entire protocol use the protocol-wide change."

Unlike Targeted Scope (measure.py + metrics.py), this needs no on-chain reads
at all: DefiLlama already publishes a protocol-wide TVL-in-USD series per
chain (chainTvls[chain].tvl), so ΔTVL is just that series' value at each
checkpoint date, summed across the grant's chains. Same window, attribution,
and supplementary-metric conventions as Targeted Scope — only the quantity
being measured changes, from per-contract token deltas to protocol-wide TVL.

Use this when Targeted Scope's on-chain reads aren't viable for a grant (e.g.
the read tooling a pool type needs didn't exist yet at the grant's dates) — a
disclosed trade-off of per-contract precision for data availability, not a
default.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


def _tvl_series(payload: dict, defillama_chains: list) -> dict:
    """{chain: [(day, totalLiquidityUSD), ...]} sorted by day."""
    out = {}
    for chain in defillama_chains:
        rows = payload["chainTvls"].get(chain, {}).get("tvl", [])
        out[chain] = sorted(
            (pd.Timestamp(r["date"], unit="s").normalize(), r["totalLiquidityUSD"])
            for r in rows)
    return out


def _value_at(series: list, day) -> float | None:
    cutoff = pd.Timestamp(day)
    candidates = [v for d, v in series if d <= cutoff]
    return candidates[-1] if candidates else None


def tvl_at_checkpoints(config, payload: dict, checkpoints: dict) -> dict:
    """{checkpoint_label: total_tvl_usd}, summed across the grant's chains.
    A chain with no DefiLlama TVL value at or before a date counts as $0 for
    that checkpoint (e.g. the grantee hasn't launched on that chain yet) —
    printed visibly rather than silently dropped, since the same "no value"
    signal could also mean a genuine DefiLlama data gap worth checking.
    """
    series = _tvl_series(payload, config.defillama_chains)
    out = {}
    for label, day in checkpoints.items():
        values = {c: _value_at(series[c], day) for c in config.defillama_chains}
        missing = [c for c, v in values.items() if v is None]
        if missing:
            print(f"      note: no DefiLlama TVL for {missing} at "
                  f"{label} ({day}) — treated as $0; confirm that's really "
                  f"$0 and not a data gap")
        out[label] = sum(v or 0 for v in values.values())
    return out


@dataclass
class GlobalScopeResult:
    grant_id: str
    grantee: str
    window: str
    scope: str
    chains: list
    tvl_start_usd: float
    tvl_end_usd: float
    delta_tvl_usd: float
    target_milestone1: float | None
    target_total: float | None
    milestone1_met: bool | None
    total_target_met: bool | None
    op_budget: float | None
    usd_per_op: float | None
    tvl_at_snapshot_usd: float | None
    delta_tvl_at_snapshot_usd: float | None
    retention_30d_pct: float | None

    def scorecard_dict(self) -> dict:
        return asdict(self)


def compute(config, tvl: dict) -> GlobalScopeResult:
    if "start" not in tvl or "end" not in tvl:
        raise SystemExit(
            f"{config.grant_id}: DefiLlama has no chainTvls[...].tvl data at "
            f"the start/end checkpoint for {config.defillama_chains} — Global "
            f"Scope needs a plain protocol TVL series, which this protocol/"
            f"chain combination doesn't have."
        )
    tvl_start, tvl_end = tvl["start"], tvl["end"]
    delta = tvl_end - tvl_start

    tvl_snap = tvl.get("snapshot")
    delta_snap = (tvl_snap - tvl_start) if tvl_snap is not None else None

    tvl_stick = tvl.get("plus30d")
    retention = (tvl_stick / tvl_end * 100.0) if (tvl_stick is not None and tvl_end) else None

    m1_met = (delta >= config.target_milestone1) if config.target_milestone1 else None
    total_met = (delta >= config.target_total) if config.target_total else None
    usd_per_op = (delta / config.budget_op) if config.budget_op else None

    return GlobalScopeResult(
        grant_id=config.grant_id, grantee=config.grantee,
        window=f"{config.incentive_start} -> {config.incentive_end}",
        scope="Global (protocol-wide)", chains=config.defillama_chains,
        tvl_start_usd=round(tvl_start, 2), tvl_end_usd=round(tvl_end, 2),
        delta_tvl_usd=round(delta, 2),
        target_milestone1=config.target_milestone1, target_total=config.target_total,
        milestone1_met=m1_met, total_target_met=total_met,
        op_budget=config.budget_op,
        usd_per_op=round(usd_per_op, 2) if usd_per_op is not None else None,
        tvl_at_snapshot_usd=round(tvl_snap, 2) if tvl_snap is not None else None,
        delta_tvl_at_snapshot_usd=round(delta_snap, 2) if delta_snap is not None else None,
        retention_30d_pct=round(retention, 1) if retention is not None else None)
