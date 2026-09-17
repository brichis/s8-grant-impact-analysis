"""S8 metric calculation (Targeted Scope, per-contract).

Primary metric — the S8 TVL formula, applied to each scope contract and summed:

    ΔTVL = Σ_contracts (quantity_end − quantity_start) × price_end

  * quantities come from measure.py (direct chain-state reads of each contract)
  * price_end is the token's price on the end date, from DefiLlama
  * start = incentive start, end = min(incentive end, S8 end); for a grant
    whose incentive is still running, end = the last fully-elapsed day and the
    result is an interim measurement (see registry.measurement_end)

Attribution is 100% throughout, and is not a configurable input. Under Targeted
Scope we measure only the contracts the grant actually incentivized, so there is
no protocol-wide over-count to proportion away — the change we measure is the
change on the incentivized contracts. Splitting that credit against a grantee's
own co-incentives would need a defensible per-grant split, and the data to
compute one honestly is not available, so no such split is invented here.

Supplementary context (not S8 success metrics, labelled as such in outputs):
  * Retention +30d — value-weighted token retention 30 days after incentive end.
  * Milestones: both are tested against the highest ΔTVL the grant reached on
    any day inside the incentive window (see `daily` in compute). A target that
    was genuinely reached counts as reached, even if the liquidity left later;
    the snapshot and end figures stay in the outputs as data. To keep this
    honest against one-day spikes, the series it reads is a validated daily
    series, not a single read.
  * Historic rule (kept for reference): M1 was tested against the snapshot, M2 (target_total)
    against the end checkpoint.
  * Price-vs-quantity wedge — the share of the USD change that is token-price
    movement, which the fixed-end-price formula deliberately excludes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from chains import canonical


@dataclass
class ContractResult:
    contract: str
    pool: str
    token: str
    type: str
    chain: str
    quantity_start: float
    quantity_end: float
    price_end: float
    delta_tvl_usd: float


@dataclass
class GrantResult:
    grant_id: str
    grantee: str
    window: str
    scope: str
    contracts: list
    delta_tvl_usd: float
    target_milestone1: float | None
    target_total: float | None
    milestone1_met: bool | None
    total_target_met: bool | None
    peak_delta_tvl_usd: float | None
    peak_date: str | None
    op_budget: float | None
    usd_per_op: float | None
    delta_tvl_at_snapshot_usd: float | None
    retention_30d_pct: float | None
    price_qty_wedge_usd: float
    usd_level_change: float

    def scorecard_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "contracts"}

    def contracts_frame(self):
        import pandas as pd
        return pd.DataFrame([asdict(c) for c in self.contracts])


def _pivot_quantities(measured):
    """One row per (contract, token) — a plain `contract` group-by would silently
    collapse a two-sided pool's two legs (same contract address, different
    token) into a single row via aggfunc='last'."""
    import pandas as pd
    measured = measured.copy()
    measured["_key"] = measured["contract"] + "|" + measured["token"].astype(str)
    meta = (measured[["_key", "contract", "pool", "token", "type", "chain"]]
            .drop_duplicates().set_index("_key"))
    wide = measured.pivot_table(index="_key", columns="checkpoint",
                                values="quantity", aggfunc="last")
    return meta.join(wide)


def compute(config, measured, prices, daily=None) -> GrantResult:
    """Per-contract and grant-level S8 metrics.

    measured : per-contract quantities (measure.measure_all output).
    prices   : {(defillama_chain, TOKEN): {"start":p,"snapshot":p,"end":p}}.
    daily    : [(date, delta_tvl_usd), ...] inside the incentive window, at the
               same fixed end prices as delta_tvl_usd — the validated daily
               series the milestones are judged on. Without it both verdicts
               are None (unevaluated), never False: a missing series is not
               evidence that a target was missed.
    """
    wide = _pivot_quantities(measured)

    contract_results = []
    total_delta = 0.0
    for _key, row in wide.iterrows():
        token_key = (canonical(row["chain"]), str(row["token"]).upper())
        price_end = prices.get(token_key, {}).get("end", 0.0)
        q_start = float(row.get("start", 0.0) or 0.0)
        q_end = float(row.get("end", 0.0) or 0.0)
        dtvl = (q_end - q_start) * price_end
        total_delta += dtvl
        contract_results.append(ContractResult(
            contract=row["contract"], pool=row["pool"], token=row["token"],
            type=row["type"], chain=row["chain"],
            quantity_start=round(q_start, 2), quantity_end=round(q_end, 2),
            price_end=round(price_end, 6), delta_tvl_usd=round(dtvl, 2)))

    # M1 is evaluated at the snapshot, M2 at the end, matching how the two
    # checkpoints are labelled in outputs.py. Each milestone is measured over
    # its own window, so the S8 formula's fixed price is the price at the end
    # of *that* window: start->snapshot valued at snapshot prices, start->end
    # at end prices. Testing M1 against the end figure (as this did) asks
    # whether a milestone due months earlier is still met today, which for a
    # grant whose TVL has since receded reports "not met" for a milestone that
    # was in fact reached -- Velodrome cleared $3.2M at its snapshot and then
    # gave it back.
    at_snapshot = None
    if "snapshot" in set(measured["checkpoint"]):
        at_snapshot = 0.0
        for _key, row in wide.iterrows():
            token_key = (canonical(row["chain"]), str(row["token"]).upper())
            p = prices.get(token_key, {})
            price_snap = p.get("snapshot", p.get("end", 0.0))
            q_start = float(row.get("start", 0.0) or 0.0)
            q_snap = float(row.get("snapshot", 0.0) or 0.0)
            at_snapshot += (q_snap - q_start) * price_snap

    # None, not False, when the snapshot wasn't measured: unevaluated is not
    # the same as missed.
    # Milestones are judged on the peak of the daily series: the question is
    # whether the grant ever reached the target inside its incentive window,
    # not whether it happened to be above it on one particular date.
    peak_value = peak_date = None
    if daily:
        peak_date, peak_value = max(daily, key=lambda dv: dv[1])
        peak_date = str(peak_date)
    m1_met = (None if peak_value is None or not config.target_milestone1
              else peak_value >= config.target_milestone1)
    total_met = (None if peak_value is None or not config.target_total
                 else peak_value >= config.target_total)
    usd_per_op = (total_delta / config.budget_op) if config.budget_op else None

    # Retention needs an actual +30d read. For a still-running grant run.py
    # omits that checkpoint entirely — treat it as "not measured" (None), not
    # as 0% retention, which a plain `row.get("plus30d", 0.0)` would produce.
    retention = None
    if "plus30d" in set(measured["checkpoint"]):
        num = den = 0.0
        for _key, row in wide.iterrows():
            token_key = (canonical(row["chain"]), str(row["token"]).upper())
            price_end = prices.get(token_key, {}).get("end", 0.0)
            q_end = float(row.get("end", 0.0) or 0.0)
            q_stick = float(row.get("plus30d", 0.0) or 0.0)
            num += q_stick * price_end
            den += q_end * price_end
        retention = (num / den * 100.0) if den else None

    usd_level = 0.0
    for _key, row in wide.iterrows():
        token_key = (canonical(row["chain"]), str(row["token"]).upper())
        p_start = prices.get(token_key, {}).get("start", 0.0)
        p_end = prices.get(token_key, {}).get("end", 0.0)
        q_start = float(row.get("start", 0.0) or 0.0)
        q_end = float(row.get("end", 0.0) or 0.0)
        usd_level += q_end * p_end - q_start * p_start
    raw_total = sum(c.delta_tvl_usd for c in contract_results)
    wedge = usd_level - raw_total

    return GrantResult(
        grant_id=config.grant_id, grantee=config.grantee,
        window=config.window_label,
        scope="Targeted (per-contract)", contracts=contract_results,
        delta_tvl_usd=round(total_delta, 2),
        target_milestone1=config.target_milestone1,
        target_total=config.target_total,
        milestone1_met=m1_met, total_target_met=total_met,
        peak_delta_tvl_usd=round(peak_value, 2) if peak_value is not None else None,
        peak_date=peak_date,
        op_budget=config.budget_op,
        usd_per_op=round(usd_per_op, 2) if usd_per_op is not None else None,
        delta_tvl_at_snapshot_usd=round(at_snapshot, 2) if at_snapshot is not None else None,
        retention_30d_pct=round(retention, 1) if retention is not None else None,
        price_qty_wedge_usd=round(wedge, 2), usd_level_change=round(usd_level, 2))
