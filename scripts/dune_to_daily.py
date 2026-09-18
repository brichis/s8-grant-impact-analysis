#!/usr/bin/env python3
"""Daily quantities for pool legs, from a Dune transfers export.

dune_peak.py turns a transfers export into a whole grantee's ΔTVL curve. This
does the smaller job that a mixed grantee needs: it converts the same export
into per-leg daily quantities in rpc_daily_peak.py's --extra-daily shape, so a
grant measured partly on-chain and partly from Dune can be drawn as one curve.

Curve Lending is that case: its LlamaLend vaults have to be read on-chain (an
ERC-4626 share price accrues interest with no event to rebuild it from), while
its three incentivised pools are plain balances that Dune can replay from
transfers far faster than a daily read.

The quantities are anchored on the pipeline's own committed start reading and
moved by each day's net flow, so a leg's whole series hangs off a measured
number rather than a Dune one. Every committed checkpoint the export covers
must be reproduced before anything is written.

Usage:
  python3 scripts/dune_to_daily.py output/<grantee> <export.csv> --out <daily.csv>
"""
import argparse
import collections
import csv
import datetime as dt
import sys
from pathlib import Path

REL_TOL = 1e-9
DUNE_TO_CHAIN = {"optimism": "OP Mainnet", "base": "Base", "unichain": "Unichain", "ink": "Ink"}


def fail(msg: str) -> None:
    print(f"FALLA: {msg}\nNo se escribio nada.")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("grantee_dir")
    ap.add_argument("export_csv")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    committed = collections.defaultdict(dict)      # (chain, contract, TOKEN) -> checkpoint -> qty
    label, dates = {}, {}
    for row in csv.DictReader(open(Path(args.grantee_dir) / "measured_quantities.csv")):
        key = (row["chain"], row["contract"].lower(), row["token"].upper())
        committed[key][row["checkpoint"]] = float(row["quantity"])
        label[key] = row["pool"]
        dates[row["checkpoint"]] = row["date"]

    flows = collections.defaultdict(dict)          # key -> date -> net quantity
    decimals, seen = {}, set()
    for row in csv.DictReader(open(args.export_csv)):
        chain = DUNE_TO_CHAIN.get(row.get("blockchain", "optimism"))
        key = (chain, row["pool"].lower(), (row["symbol"] or "").upper())
        if key not in committed:
            fail(f"el export trae un leg que el pipeline no mide: {key}")
        dec = int(row["decimals"])
        decimals[key] = dec
        seen.add(key)
        day = row["local_date"][:10]
        flows[key][day] = flows[key].get(day, 0.0) + int(row["net_raw"]) / 10 ** dec

    start = dt.date.fromisoformat(dates["start"])
    last = max(dt.date.fromisoformat(d) for d in dates.values())
    days = [start + dt.timedelta(i) for i in range((last - start).days + 1)]

    series, checks, bad = {}, 0, 0
    print("Cantidades reconstruidas vs comiteadas:")
    for key in sorted(seen):
        qty = committed[key].get("start", 0.0)
        series[key] = {}
        for day in days:
            qty += flows[key].get(day.isoformat(), 0.0)
            series[key][day] = qty
        for checkpoint, want in committed[key].items():
            when = dt.date.fromisoformat(dates[checkpoint])
            if when not in series[key]:
                continue
            checks += 1
            got = series[key][when]
            ok = abs(got - want) <= REL_TOL * max(1.0, abs(want))
            bad += not ok
            if not ok:
                print(f"  DIF {label[key]} [{key[2]}] {checkpoint}: comiteado {want:,.6f} · export {got:,.6f}")
    print(f"  {checks - bad}/{checks} coinciden")
    if bad:
        fail(f"{bad} checkpoint(s) no cuadran con las lecturas on-chain")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["chain", "contract", "label", "token", "date", "quantity"])
        for key in sorted(seen):
            for day in days:
                writer.writerow([key[0], key[1], label[key], key[2], day.isoformat(),
                                 repr(series[key][day])])
    print(f"Escrito {out} ({len(seen)} legs x {len(days)} dias)")


if __name__ == "__main__":
    main()
