#!/usr/bin/env python3
"""Supplementary daily peak from a Dune export of in-window token flows.

Rebuilds each pool leg's daily balance as the pipeline's own on-chain START
reading plus the cumulative net transfers Dune reports, checks that against
every other checkpoint the pipeline measured on-chain, and only then derives
the daily ΔTVL curve (fixed end-date price, as the S8 formula) and its peak
inside the incentive window.

Why this works for `pool` legs: measure.py reads a pool as balanceOf(pool) for
each leg token, which is exactly the running sum of that token's transfers in
minus out. It does NOT work for Uniswap v4 / PancakeSwap Infinity pools
(tokens sit in a singleton vault, not attributable by transfers) or for
vault/lend/loan types (totalAssets accrues interest with no event).

Supplementary by design: reads output/<grantee>/ and writes two NEW files; it
never touches scorecard.csv or any M1/M2 verdict. Fails loud, writing
nothing, if the Dune flows don't reproduce the pipeline's checkpoints.

Multi-chain grantees need a `blockchain` column in the export, and legs are
keyed by (chain, pool, token): pool and token addresses repeat across OP-stack
chains (WETH is 0x4200…0006 on all of them), so an address alone is ambiguous.
Chains Dune doesn't index (Soneium) are dropped with --exclude-chain; their
share of scope TVL is reported, and the end-of-window check compares against
the scorecard minus their contribution.

The day boundary follows measure._end_of_day_ts, which is 23:59:59 in the
machine's LOCAL zone (UTC-6 on the machine that produced output/): queries
group transfers by DATE(block_time - INTERVAL '6' HOUR) so each day ends on
the same block the pipeline's checkpoints do.

A leg can be backed by more than one token. When both of a pool's currencies
report the same symbol() — OP Mainnet's native USDC and bridged USDC.e are both
"USDC" — measure.py SUMS them under one label but records only the last
matched address, so the export must cover both tokens and --also-token maps
the second one onto that leg. Decimals are resolved per token, not per leg.

Usage:
  python3 scripts/dune_peak.py output/<grantee> data/dune/<export>.csv [more.csv ...]
      [--out DIR] [--exclude-chain Soneium] [--exclude-type "pool (Infinity)"]
      [--also-token "CHAIN,POOL,SYMBOL,TOKEN_ADDRESS"] [--note TEXT]

--exclude-type drops legs by their `type` the same way --exclude-chain drops
them by chain: e.g. PancakeSwap's Infinity pools, whose tokens sit in a
singleton vault and can't be attributed by transfers, so the V3 export can be
validated on its own.
"""
import argparse, collections, csv, datetime as dt, re, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

REL_TOL = 1e-9        # Dune amounts are exact integers; slack only for float storage
DECIMALS_SELECTOR = "0x313ce567"
# Registry chain label -> Dune `blockchain`. Kept here rather than in
# src/chains.py because Dune is not part of the pipeline; move it there if it
# ever becomes one.
DUNE_CHAIN = {"Base": "base", "OP Mainnet": "optimism", "Optimism": "optimism",
              "Unichain": "unichain", "Ink": "ink"}


def fail(msg):
    print(f"FAILED: {msg}\nNothing was written.")
    sys.exit(1)


def cached_decimals(label, token):
    """decimals() as the pipeline itself read it, from its RPC cache — the
    fallback when Dune's tokens.erc20 has no row for a token."""
    from chains import rpc_slug
    pat = f'"{rpc_slug(label)}:call:{token}:{DECIMALS_SELECTOR}:[0-9]+": "0x[0-9a-fA-F]+"'
    out = subprocess.run(["grep", "-o", "-E", pat, str(REPO / "data/rpc_cache.json")],
                         capture_output=True, text=True, env={"LC_ALL": "C"}).stdout.strip()
    m = re.search(r'"(0x[0-9a-fA-F]+)"$', out.splitlines()[0]) if out else None
    return int(m.group(1), 16) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grantee_dir"); ap.add_argument("dune_csv", nargs="+")
    ap.add_argument("--out"); ap.add_argument("--note", default="")
    ap.add_argument("--exclude-chain", action="append", default=[])
    ap.add_argument("--exclude-type", action="append", default=[])
    ap.add_argument("--also-token", action="append", default=[],
                    help='"CHAIN,POOL,SYMBOL,TOKEN_ADDRESS": another token summed into that leg')
    a = ap.parse_args()
    g = Path(a.grantee_dir); out = Path(a.out) if a.out else g

    # A leg is (chain, pool contract, token SYMBOL) — not the token address.
    # At a checkpoint before a pool existed, measure.py reports quantity 0 with
    # no resolved address, so keying by address splits one leg's checkpoints
    # across two keys. The address comes from whichever rows did resolve it.
    qty, dates, label, ltype = collections.defaultdict(dict), {}, {}, {}
    addr = collections.defaultdict(set)
    for r in csv.DictReader(open(g / "measured_quantities.csv")):
        k = (r["chain"], r["contract"].lower(), r["token"].upper())
        qty[k][r["checkpoint"]] = float(r["quantity"])
        dates[r["checkpoint"]] = r["date"]
        label[k] = (r["pool"], r["token"].upper())
        ltype[k] = r["type"]
        if (r.get("address") or "").strip():
            addr[k].add(r["address"].lower())
    for k in qty:
        if len(addr[k]) > 1:
            fail(f"{label[k]} en {k[0]} resolvio a varias direcciones: {sorted(addr[k])}")
        if "start" not in qty[k] or "end" not in qty[k]:
            fail(f"{label[k]} en {k[0]} no tiene checkpoint start/end en measured_quantities")
    by_token = {(k[0], k[1], next(iter(addr[k]))): k for k in qty if addr[k]}
    also = []
    for spec in a.also_token:
        chain, pool, sym, tok = [x.strip() for x in spec.split(",")]
        k = (chain, pool.lower(), sym.upper())
        if k not in qty:
            fail(f"--also-token apunta a un leg que no existe: {k}")
        by_token[(chain, pool.lower(), tok.lower())] = k
        also.append(f"{label[k][0]} ({chain}) also sums {tok.lower()}")
    table = list(csv.DictReader(open(g / "table_contracts.csv")))
    price = {(r["chain"], r["contract"].lower(), r["token"].upper()): float(r["price_end"]) for r in table}
    p = lambda k: price[k]
    official = float(list(csv.DictReader(open(g / "scorecard.csv")))[0]["delta_tvl_usd"])

    excluded = {k for k in qty if k[0] in a.exclude_chain or ltype[k] in a.exclude_type}
    dropped_what = a.exclude_chain + a.exclude_type
    covered = [k for k in qty if k not in excluded]
    chains = {k[0] for k in covered}
    to_label = {DUNE_CHAIN[c]: c for c in chains}

    raw = collections.defaultdict(lambda: collections.defaultdict(int))   # (leg, token) -> day -> raw
    dec = {}                                                               # (chain, token) -> decimals
    for path in a.dune_csv:
        for r in csv.DictReader(open(path)):
            if "blockchain" in r:
                chain = to_label.get(r["blockchain"])
                if chain is None:
                    fail(f"el export trae la cadena '{r['blockchain']}', que no esta en el scope cubierto")
            elif len(chains) == 1:
                chain = next(iter(chains))
            else:
                fail("grantee multi-cadena: el export necesita una columna 'blockchain'")
            tok = r["token"].lower()
            k = by_token.get((chain, r["pool"].lower(), tok))
            if k is None or k in excluded:
                fail(f"Dune devolvio un leg que el pipeline no mide: {chain} {r['pool']} {r['token']}")
            raw[(k, tok)][r["local_date"][:10]] += int(r["net_raw"])
            if (r.get("decimals") or "").strip() not in ("", "<nil>", "null"):
                dec[(chain, tok)] = int(float(r["decimals"]))

    flows, dropped = collections.defaultdict(lambda: collections.defaultdict(float)), []
    for (k, tok), series in raw.items():
        d = dec.get((k[0], tok))
        if d is None:
            d = cached_decimals(k[0], tok)
        if d is None:
            if p(k) == 0:
                dropped.append(label[k]); continue       # unpriced: contributes $0 either way
            fail(f"no decimals for {label[k]} ({tok}), neither in Dune nor in the pipeline cache")
        for day, v in series.items():
            flows[k][day] += v / 10 ** d
    covered = [k for k in covered if label[k] not in dropped]

    start, end = dt.date.fromisoformat(dates["start"]), dt.date.fromisoformat(dates["end"])
    last = max(dt.date.fromisoformat(d) for d in dates.values())
    days = [start + dt.timedelta(i) for i in range((last - start).days + 1)]
    balance = {}
    for k in covered:
        run, series = qty[k]["start"], {}
        for d in days:
            if d > start:
                run += flows[k].get(d.isoformat(), 0.0)
            series[d] = run
        balance[k] = series

    print(f"Legs: {len(covered)} validated" + (f" · {len(excluded)} excluded ({', '.join(dropped_what)})" if excluded else "")
          + (f" · {len(dropped)} skipped, with neither price nor decimals: {dropped}" if dropped else ""))
    bad, checks = 0, 0
    for k in sorted(covered, key=lambda k: (k[0],) + label[k]):
        for cp in ("snapshot", "end", "plus30d"):
            if cp not in qty[k]:
                continue
            checks += 1
            want, got = qty[k][cp], balance[k][dt.date.fromisoformat(dates[cp])]
            if abs(got - want) > REL_TOL * max(1.0, abs(want)):
                bad += 1
                print(f"  DIFF {k[0]:<10} {label[k][0]:<18} {label[k][1]:<9} {cp:<8} pipeline {want:,.6f}  dune {got:,.6f}")
    print(f"Checked against the on-chain checkpoints: {checks - bad}/{checks} match")
    if bad:
        fail(f"{bad} checkpoint(s) don't reconcile — the peak would not be trustworthy")

    excl_delta = sum(float(r["delta_tvl_usd"]) for r in table
                     if r["chain"] in a.exclude_chain or r["type"] in a.exclude_type)
    curve = [(d, sum((balance[k][d] - qty[k]["start"]) * p(k) for k in covered)) for d in days]
    at_end = dict(curve)[end]
    # table_contracts.csv rounds price_end to 6 decimals, and that rounded price
    # is all the curve has; the scorecard's ΔTVL used the unrounded one. So the
    # two may differ by up to half a unit in the 6th decimal on every token
    # moved, plus cents from rounding the scorecard and each excluded leg's
    # delta. Check against exactly that bound — a fixed $1 is fine for Hydrex
    # and too tight for a grantee moving millions of stablecoins (Velodrome).
    slack = 5e-7 * sum(abs(qty[k]["end"] - qty[k]["start"]) for k in covered) + 0.01 * (len(excluded) + 1)
    target = official - excl_delta
    if abs(at_end - target) > slack:
        fail(f"the curve ends at ${at_end:,.2f}; the scorecard minus what is excluded gives ${target:,.2f} "
             f"(off by ${abs(at_end - target):,.2f} > the rounding allowance of ${slack:,.2f})")
    print(f"End of window: curve ${at_end:,.2f} vs scorecard{' minus exclusions' if excluded else ''} ${target:,.2f} "
          f"(off by ${abs(at_end - target):,.2f}, rounding allowance ${slack:,.2f})")
    peak_d, peak_v = max(((d, v) for d, v in curve if d <= end), key=lambda dv: dv[1])

    if excluded:
        share = []
        for cp in ("start", "snapshot", "end"):
            tot = sum(qty[k][cp] * p(k) for k in qty if cp in qty[k])
            ex = sum(qty[k][cp] * p(k) for k in excluded if cp in qty[k])
            share.append(f"{ex / tot:.1%}" if tot else "n/a")
        coverage = (f"excludes {', '.join(dropped_what)} ({' / '.join(share)} of scope TVL at "
                    f"start / snapshot / end; ${excl_delta:,.0f} of the official ΔTVL)")
    else:
        coverage = "100% of scope contracts"
    if also:
        coverage += "; " + "; ".join(also)

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "supplementary_daily_curve.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["Date", "ΔTVL (USD)", "in_window"])
        for d, v in curve:
            w.writerow([d.isoformat(), round(v), d <= end])
    with open(out / "supplementary_peak.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["peak_delta_tvl_usd", "peak_date", "delta_tvl_at_end_usd", "coverage", "source", "note"])
        w.writerow([round(peak_v, 2), peak_d.isoformat(), round(at_end, 2), coverage,
                    "Dune tokens.transfers, anchored on the pipeline's on-chain start reading", a.note])
    print(f"Peak in window: ${peak_v:,.0f} on {peak_d}  ·  ${at_end:,.0f} at the end  ·  coverage: {coverage}")
    print(f"Wrote {out}/: supplementary_peak.csv, supplementary_daily_curve.csv")


if __name__ == "__main__":
    main()
