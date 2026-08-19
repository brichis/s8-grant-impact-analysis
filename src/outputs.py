"""Output builders — per-contract, Datawrapper-ready.

  chart_delta_tvl_by_contract.csv  one bar per scope contract (its ΔTVL) — the
                                   primary Targeted-Scope visual; shows which
                                   pool drove the result.
  chart_delta_tvl_checkpoints.csv  grant-total ΔTVL at M1 / M2 / +30d.
  table_contracts.csv              per-contract qty_start/qty_end/price/ΔTVL.
  scorecard.csv                    one-row grant summary.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_contract_chart(result, out_dir: Path) -> Path:
    rows = [{"Contract": f"{c.pool} ({c.chain})", "ΔTVL (USD)": round(c.delta_tvl_attributed_usd)}
            for c in result.contracts]
    path = out_dir / "chart_delta_tvl_by_contract.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_checkpoint_chart(config, measured, prices, out_dir: Path) -> Path:
    """Grant-total ΔTVL at each checkpoint, end-date price."""
    from metrics import _defillama_chain, _pivot_quantities
    wide = _pivot_quantities(measured)

    labels = [("M1 · snapshot (interim)", "snapshot"),
              ("M2 · incentive end", "end"),
              ("+30 days (post-incentive)", "plus30d")]
    rows = []
    for label, col in labels:
        if col not in wide.columns:
            continue
        total = 0.0
        for _, r in wide.iterrows():
            key = (_defillama_chain(r["chain"]), str(r["token"]).upper())
            price_end = prices.get(key, {}).get("end", 0.0)
            q0 = float(r.get("start", 0.0) or 0.0)
            q = float(r.get(col, 0.0) or 0.0)
            total += (q - q0) * price_end
        rows.append({"Checkpoint": label, "ΔTVL (USD)": round(total)})
    path = out_dir / "chart_delta_tvl_checkpoints.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_global_checkpoint_chart(tvl: dict, out_dir: Path) -> Path:
    """Global Scope equivalent of write_checkpoint_chart — same filename and
    shape (Checkpoint, ΔTVL (USD)) so downstream Datawrapper usage doesn't
    care which scope produced it. `tvl` is global_scope.tvl_at_checkpoints().
    """
    labels = [("M1 · snapshot (interim)", "snapshot"),
              ("M2 · incentive end", "end"),
              ("+30 days (post-incentive)", "plus30d")]
    tvl_start = tvl.get("start")
    rows = []
    for label, col in labels:
        if col not in tvl or tvl_start is None:
            continue
        rows.append({"Checkpoint": label, "ΔTVL (USD)": round(tvl[col] - tvl_start)})
    path = out_dir / "chart_delta_tvl_checkpoints.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_contract_table(result, out_dir: Path) -> Path:
    path = out_dir / "table_contracts.csv"
    result.contracts_frame().to_csv(path, index=False)
    return path


def write_scorecard(result, out_dir: Path) -> Path:
    path = out_dir / "scorecard.csv"
    pd.DataFrame([result.scorecard_dict()]).to_csv(path, index=False)
    return path
