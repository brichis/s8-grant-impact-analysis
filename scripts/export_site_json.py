#!/usr/bin/env python3
"""Bundle output/ into one JSON the site imports directly.

The measured CSVs in output/ are Datawrapper-shaped: one file per chart, with
display labels ("ΔTVL (USD)") as column headers. That is the right shape to
paste into Datawrapper and the wrong shape to import into a React app, which
wants stable machine keys and real nulls. This script is the seam between the
two — it re-keys, types, and concatenates, and changes no measured value.

Deliberately a build step, not a service: the whole of output/ is ~244KB and is
regenerated in batches by run.py, so the site ships the data in its own bundle
rather than fetching it. No database, no auth, no runtime dependency on this
repo being reachable.

Three grantee states exist in output/ and all three are represented rather than
normalised away, because each means something different to a reader:

  * Targeted scope   — per-contract files present; `contracts` is populated.
  * Global scope     — run.py --global, no on-chain per-contract reads exist, so
                       `contracts` is [] and `scope` says why. Not a failed run.
  * Ongoing incentive — window has no announced end; the end checkpoint is an
                       interim read and +30d retention is absent by design.
                       Flagged as `ongoing` so the site can label it, rather
                       than presenting an in-flight grant as a final result.

Usage:
    python scripts/export_site_json.py            # -> site/data/grantees.json
    python scripts/export_site_json.py --out DIR
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
OUT_BASE = BASE / "output"
SITE_DATA = BASE / "site" / "data"

# Trailing parenthetical run.py appends when the registry has no end date.
ONGOING_RE = re.compile(r"\(incentive ongoing[^)]*\)")


def _num(value):
    """CSV cell -> float | int | None. Blank and NaN both mean 'not measured',
    which must reach the site as null, not 0 — a zero would render as a real
    measurement of zero."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() and abs(number) < 2**53 else number


def _bool(value):
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    return None  # e.g. no snapshot measured, so M1 was never evaluated


def _read(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    return frame if not frame.empty else None


def _split_window(window: str) -> dict:
    """'2026-04-17 -> 2026-08-31 (incentive ongoing, ...)' -> parts + flag."""
    ongoing = bool(ONGOING_RE.search(window))
    bare = ONGOING_RE.sub("", window).strip()
    start, _, end = bare.partition("->")
    return {
        "window": window,
        "windowStart": start.strip() or None,
        "windowEnd": end.strip() or None,
        "ongoing": ongoing,
    }


def build_grantee(directory: Path) -> dict:
    scorecard = _read(directory / "scorecard.csv")
    if scorecard is None:
        raise SystemExit(f"{directory.name}: scorecard.csv missing or empty")
    row = scorecard.iloc[0]

    scope_text = str(row["scope"])
    record = {
        "slug": directory.name,
        "grantee": str(row["grantee"]),
        "grantId": str(row["grant_id"]),
        "scope": "global" if scope_text.lower().startswith("global") else "targeted",
        "scopeLabel": scope_text,
        **_split_window(str(row["window"])),
        "deltaTvlUsd": _num(row.get("delta_tvl_usd")),
        "deltaTvlAtSnapshotUsd": _num(row.get("delta_tvl_at_snapshot_usd")),
        "targetMilestone1": _num(row.get("target_milestone1")),
        "targetTotal": _num(row.get("target_total")),
        "milestone1Met": _bool(row.get("milestone1_met")),
        "totalTargetMet": _bool(row.get("total_target_met")),
        "opBudget": _num(row.get("op_budget")),
        "usdPerOp": _num(row.get("usd_per_op")),
        # Supplementary — reported in the methodology as context, NOT as S8
        # success metrics. Namespaced so the site cannot render them as one.
        "supplementary": {
            "retention30dPct": _num(row.get("retention_30d_pct")),
            "priceQtyWedgeUsd": _num(row.get("price_qty_wedge_usd")),
            "usdLevelChange": _num(row.get("usd_level_change")),
        },
        "contracts": [],
        "deltaTvlByContract": [],
        "checkpoints": [],
        "quantities": [],
    }

    contracts = _read(directory / "table_contracts.csv")
    if contracts is not None:
        record["contracts"] = [
            {
                "address": str(r["contract"]),
                "pool": str(r["pool"]),
                "token": str(r["token"]),
                "type": str(r["type"]),
                "chain": str(r["chain"]),
                "quantityStart": _num(r["quantity_start"]),
                "quantityEnd": _num(r["quantity_end"]),
                "priceEnd": _num(r["price_end"]),
                "deltaTvlUsd": _num(r["delta_tvl_usd"]),
            }
            for _, r in contracts.iterrows()
        ]

    by_contract = _read(directory / "chart_delta_tvl_by_contract.csv")
    if by_contract is not None:
        label, value = by_contract.columns[0], by_contract.columns[1]
        record["deltaTvlByContract"] = [
            {"label": str(r[label]), "deltaTvlUsd": _num(r[value])}
            for _, r in by_contract.iterrows()
        ]

    checkpoints = _read(directory / "chart_delta_tvl_checkpoints.csv")
    if checkpoints is not None:
        label, value = checkpoints.columns[0], checkpoints.columns[1]
        record["checkpoints"] = [
            {"label": str(r[label]), "deltaTvlUsd": _num(r[value])}
            for _, r in checkpoints.iterrows()
        ]

    quantities = _read(directory / "measured_quantities.csv")
    if quantities is not None:
        record["quantities"] = [
            {
                "address": str(r["contract"]),
                "pool": str(r["pool"]),
                "token": str(r["token"]),
                "tokenAddress": str(r["address"]),
                "type": str(r["type"]),
                "chain": str(r["chain"]),
                "checkpoint": str(r["checkpoint"]),
                "date": str(r["date"]),
                "quantity": _num(r["quantity"]),
                "nfts": _num(r.get("nfts")),
            }
            for _, r in quantities.iterrows()
        ]

    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SITE_DATA,
                        help="directory to write grantees.json into")
    args = parser.parse_args()

    directories = sorted(d for d in OUT_BASE.iterdir()
                         if d.is_dir() and (d / "scorecard.csv").exists())
    if not directories:
        raise SystemExit("no measured grantees in output/ — run run.py first")

    grantees = [build_grantee(d) for d in directories]
    payload = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "granteeCount": len(grantees),
        "grantees": grantees,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "grantees.json"
    # ensure_ascii=False keeps Δ and · readable in the file; the site serves UTF-8.
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")

    print(f"wrote {path.relative_to(BASE)}  ({path.stat().st_size / 1024:.0f} KB)")
    for g in grantees:
        note = []
        if g["scope"] == "global":
            note.append("global scope, no per-contract data")
        if g["ongoing"]:
            note.append("ongoing — end checkpoint is interim")
        suffix = f"  [{'; '.join(note)}]" if note else ""
        delta = g["deltaTvlUsd"]
        print(f"  {g['grantee']:<20} ΔTVL ${delta:>12,.0f}"
              f"  {len(g['contracts'])} contracts{suffix}")


if __name__ == "__main__":
    main()
