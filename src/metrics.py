"""S8 metric calculation (Targeted Scope, per-contract).

Primary metric — the S8 TVL formula, applied to each scope contract and summed:

    ΔTVL = Σ_contracts (quantity_end − quantity_start) × price_end

  * quantities come from measure.py (direct chain-state reads of each contract)
  * price_end is the token's price on the end date, from DefiLlama
  * start = incentive start, end = min(incentive end, S8 end)

Attribution under Targeted Scope is simpler than under Global Scope: because we
measure only the incentivized contracts, there is no protocol-wide over-count to
proportion away. Attribution is applied per contract only where a co-incentive
overlapped that specific contract; absent that, it is 100%.

Supplementary context (not S8 success metrics, labelled as such in outputs):
  * Retention +30d — value-weighted token retention 30 days after incentive end.
  * Price-vs-quantity wedge — the share of the USD change that is token-price
    movement, which the fixed-end-price formula deliberately excludes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


def _defillama_chain(registry_chain: str) -> str:
    return {"OP Mainnet": "Optimism", "Optimism": "Optimism",
            "Base": "Base"}.get(registry_chain, registry_chain)


def _contract_attribution(config, contract_address: str) -> float:
    """Per-contract attribution %. Default 100%. Co-incentive overlaps lower it."""
    overrides = getattr(config, "attribution_overrides", {}) or {}
    return overrides.get(contract_address.lower(), 100.0)


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
    attribution_pct: float
    delta_tvl_attributed_usd: float


@dataclass
class GrantResult:
    grant_id: str
    grantee: str
    window: str
    scope: str
    contracts: list
    delta_tvl_attributed_usd: float
    target_milestone1: float | None
    target_total: float | None
    milestone1_met: bool | None
    total_target_met: bool | None
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
    import pandas as pd
    meta = (measured[["contract", "pool", "token", "type", "chain"]]
            .drop_duplicates().set_index("contract"))
    wide = measured.pivot_table(index="contract", columns="checkpoint",
                                values="quantity", aggfunc="last")
    return meta.join(wide)


def compute(config, measured, prices) -> GrantResult:
    """Per-contract and grant-level S8 metrics.

    measured : per-contract quantities (measure.measure_all output).
    prices   : {(defillama_chain, TOKEN): {"start":p,"snapshot":p,"end":p}}.
    """
    wide = _pivot_quantities(measured)

    contract_results = []
    total_attr = 0.0
    for contract, row in wide.iterrows():
        token_key = (_defillama_chain(row["chain"]), str(row["token"]).upper())
        price_end = prices.get(token_key, {}).get("end", 0.0)
        q_start = float(row.get("start", 0.0) or 0.0)
        q_end = float(row.get("end", 0.0) or 0.0)
        dtvl = (q_end - q_start) * price_end
        attr_pct = _contract_attribution(config, contract)
        dtvl_attr = dtvl * attr_pct / 100.0
        total_attr += dtvl_attr
        contract_results.append(ContractResult(
            contract=contract, pool=row["pool"], token=row["token"],
            type=row["type"], chain=row["chain"],
            quantity_start=round(q_start, 2), quantity_end=round(q_end, 2),
            price_end=round(price_end, 6), delta_tvl_usd=round(dtvl, 2),
            attribution_pct=attr_pct,
            delta_tvl_attributed_usd=round(dtvl_attr, 2)))

    m1_met = (total_attr >= config.target_milestone1
              if config.target_milestone1 else None)
    total_met = (total_attr >= config.target_total
                 if config.target_total else None)
    usd_per_op = (total_attr / config.budget_op) if config.budget_op else None

    at_snapshot = None
    if "snapshot" in set(measured["checkpoint"]):
        at_snapshot = 0.0
        for contract, row in wide.iterrows():
            token_key = (_defillama_chain(row["chain"]), str(row["token"]).upper())
            p = prices.get(token_key, {})
            price_snap = p.get("snapshot", p.get("end", 0.0))
            q_start = float(row.get("start", 0.0) or 0.0)
            q_snap = float(row.get("snapshot", 0.0) or 0.0)
            at_snapshot += (q_snap - q_start) * price_snap

    num = den = 0.0
    for contract, row in wide.iterrows():
        token_key = (_defillama_chain(row["chain"]), str(row["token"]).upper())
        price_end = prices.get(token_key, {}).get("end", 0.0)
        q_end = float(row.get("end", 0.0) or 0.0)
        q_stick = float(row.get("plus30d", 0.0) or 0.0)
        num += q_stick * price_end
        den += q_end * price_end
    retention = (num / den * 100.0) if den else None

    usd_level = 0.0
    for contract, row in wide.iterrows():
        token_key = (_defillama_chain(row["chain"]), str(row["token"]).upper())
        p_start = prices.get(token_key, {}).get("start", 0.0)
        p_end = prices.get(token_key, {}).get("end", 0.0)
        q_start = float(row.get("start", 0.0) or 0.0)
        q_end = float(row.get("end", 0.0) or 0.0)
        usd_level += q_end * p_end - q_start * p_start
    raw_total = sum(c.delta_tvl_usd for c in contract_results)
    wedge = usd_level - raw_total

    return GrantResult(
        grant_id=config.grant_id, grantee=config.grantee,
        window=f"{config.incentive_start} -> {config.incentive_end}",
        scope="Targeted (per-contract)", contracts=contract_results,
        delta_tvl_attributed_usd=round(total_attr, 2),
        target_milestone1=config.target_milestone1,
        target_total=config.target_total,
        milestone1_met=m1_met, total_target_met=total_met,
        op_budget=config.budget_op,
        usd_per_op=round(usd_per_op, 2) if usd_per_op is not None else None,
        delta_tvl_at_snapshot_usd=round(at_snapshot, 2) if at_snapshot is not None else None,
        retention_30d_pct=round(retention, 1) if retention is not None else None,
        price_qty_wedge_usd=round(wedge, 2), usd_level_change=round(usd_level, 2))
