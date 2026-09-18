#!/usr/bin/env python3
"""Supplementary daily peak for a Global Scope grantee, from DefiLlama.

Global Scope measures protocol-wide TVL from DefiLlama's chainTvls series,
which is already daily — so, unlike the Targeted grantees that need a Dune
export (see dune_peak.py), the peak needs no extra data source. The curve is
ΔTVL(d) = TVL(d) − TVL(start) summed over the grant's chains, computed with
global_scope's own series and lookup functions, i.e. exactly how the pipeline
computes its checkpoints (USD at each date: Global Scope has no token
quantities to hold at a fixed price).

Verifies first that the payload reproduces the scorecard's ΔTVL, then writes
the same two supplementary files as dune_peak.py. Never touches the scorecard
or any M1/M2 verdict; writes nothing if the check fails.

Usage: python3 scripts/global_peak.py output/<grantee> data/defillama_<slug>_<date>.json [--note TEXT]
"""
import argparse, ast, csv, datetime as dt, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import global_scope  # noqa: E402


def fail(msg):
    print(f"FAILED: {msg}\nNothing was written.")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grantee_dir"); ap.add_argument("payload")
    ap.add_argument("--out"); ap.add_argument("--note", default="")
    a = ap.parse_args()
    g = Path(a.grantee_dir); out = Path(a.out) if a.out else g

    sc = list(csv.DictReader(open(g / "scorecard.csv")))[0]
    if not sc.get("scope", "").startswith("Global"):
        fail(f"{g} no es un grantee de Global Scope — usa dune_peak.py")
    start_s, end_s = [side.strip().split(" ")[0] for side in sc["window"].split("->")]
    chains = ast.literal_eval(sc["chains"])
    official = float(sc["delta_tvl_usd"])

    series = global_scope._tvl_series(json.load(open(a.payload)), chains)
    total = lambda day: sum(global_scope._value_at(series[c], day) or 0 for c in chains)
    base = total(start_s)
    if round(total(end_s) - base, 2) != round(official, 2):
        fail(f"el payload da ΔTVL ${total(end_s) - base:,.2f} y el scorecard ${official:,.2f} — "
             f"is not the same figure, so no peak is computed from it")

    start, end = dt.date.fromisoformat(start_s), dt.date.fromisoformat(end_s)
    tail = end + dt.timedelta(days=30)             # drawn for context, never part of the peak
    days = [start + dt.timedelta(i) for i in range((tail - start).days + 1)]
    curve = [(d, total(d.isoformat()) - base) for d in days]
    peak_d, peak_v = max(((d, v) for d, v in curve if d <= end), key=lambda dv: dv[1])

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "supplementary_daily_curve.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["Date", "ΔTVL (USD)", "in_window"])
        for d, v in curve:
            w.writerow([d.isoformat(), round(v), d <= end])
    with open(out / "supplementary_peak.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["peak_delta_tvl_usd", "peak_date", "delta_tvl_at_end_usd", "coverage", "source", "note"])
        w.writerow([round(peak_v, 2), peak_d.isoformat(), round(official, 2),
                    f"Global Scope: protocol-wide TVL on {', '.join(chains)}",
                    f"DefiLlama chainTvls daily series ({Path(a.payload).name})", a.note])
    print(f"Comprobacion: el payload reproduce el ΔTVL del scorecard (${official:,.2f})")
    print(f"Peak in window: ${peak_v:,.0f} on {peak_d}  ·  ${official:,.0f} at the end  ·  {len(curve)} days (+30d kept for context)")
    print(f"Wrote {out}/: supplementary_peak.csv, supplementary_daily_curve.csv")


if __name__ == "__main__":
    main()
