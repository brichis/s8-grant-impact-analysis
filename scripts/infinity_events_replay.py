#!/usr/bin/env python3
"""Daily PancakeSwap Infinity reserves rebuilt from pool-manager events (Dune).

Infinity pools keep their tokens in a singleton vault, so — unlike V3 pools —
their reserves can't be rebuilt from token transfers. They can be rebuilt from
the pool manager's own events: each ModifyLiquidity adds liquidityDelta to
tickLower's liquidityNet and removes it from tickUpper's, and the last Swap of
a day (or Initialize, before any swap) carries the pool's price and tick.
Feeding that state through pancake_infinity._reserves_from_ticks — the function
the pipeline itself applies after its on-chain tick walk — gives what the
pipeline would have read that day.

The events cover EVERY position, while the pipeline's local tick scan can miss
one lying entirely beyond its reach (see pancake_infinity.pool_reserves). So
this first compares its absolute reserves with the committed quantity of every
leg at every checkpoint and reports each difference. Only if all of them match
does it write the derived daily flows, in dune_peak.py's transfer-export
format, to be combined there with the V3 export.

Days end on the pipeline's checkpoint blocks: the Dune query groups events by
DATE(block_time - INTERVAL '6' HOUR), like the transfer queries.

Usage:
  python3 scripts/infinity_events_replay.py output/pancakeswap <events.csv> --out <flows.csv>
"""
import argparse, collections, csv, datetime as dt, re, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pancake_infinity as pi  # noqa: E402

REL_TOL = 1e-9
DECIMALS_SELECTOR = "0x313ce567"


def fail(msg):
    print(f"FALLA: {msg}\nNo se escribio nada.")
    sys.exit(1)


def _int(x):
    return int(x) if (x or "").strip() not in ("", "<nil>", "null") else None


def load_events(path):
    """{pool_id: [event dict, ...]} sorted by (block, log_index)."""
    by_pool = collections.defaultdict(list)
    for r in csv.DictReader(open(path)):
        by_pool[r["pool_id"].lower()].append({
            "event": r["event"], "block": int(r["block_number"]), "idx": int(r["log_index"]),
            "day": dt.date.fromisoformat(r["local_date"][:10]),
            "currency0": (r.get("currency0") or "").lower(), "currency1": (r.get("currency1") or "").lower(),
            "tl": _int(r["tick_lower"]), "tu": _int(r["tick_upper"]), "ld": _int(r["liquidity_delta"]),
            "sqrtp": _int(r["sqrt_price_x96"]), "tick": _int(r["tick"]), "liq": _int(r["liquidity"]),
            "n": _int(r.get("n_events")) or 1,
        })
    for ev in by_pool.values():
        ev.sort(key=lambda e: (e["block"], e["idx"]))
    return by_pool


def replay(events, days):
    """{day: (amount0_raw, amount1_raw, active_liquidity, events_that_day) | None}.

    None before the pool is initialized — it holds nothing yet, which the
    pipeline reports as 0 for that checkpoint (PoolNotYetCreated)."""
    net, price, out, i = collections.defaultdict(int), None, {}, 0
    for d in days:
        n_today = 0
        while i < len(events) and events[i]["day"] <= d:
            e = events[i]
            if e["event"] == "modify":
                net[e["tl"]] += e["ld"]
                net[e["tu"]] -= e["ld"]
            else:                                       # init or the day's last swap
                price = (e["sqrtp"], e["tick"])
            if e["day"] == d:
                n_today += e["n"]
            i += 1
        if price is None:
            out[d] = None
            continue
        ticks = sorted(t for t, v in net.items() if v != 0)
        if not ticks:
            out[d] = (0, 0, 0, n_today)
            continue
        a0, a1, active = pi._reserves_from_ticks(ticks, {t: net[t] for t in ticks}, price[1], price[0])
        out[d] = (round(a0), round(a1), active, n_today)   # pool_reserves rounds the same way
    return out


def cached_decimals(token):
    """decimals() as the pipeline read it, from its own RPC cache."""
    out = subprocess.run(["grep", "-o", "-E", f'"base-mainnet:call:{token}:{DECIMALS_SELECTOR}:[0-9]+": "0x[0-9a-fA-F]+"',
                          str(REPO / "data/rpc_cache.json")], capture_output=True, text=True, env={"LC_ALL": "C"}).stdout.strip()
    m = re.search(r'"(0x[0-9a-fA-F]+)"$', out.splitlines()[0]) if out else None
    return int(m.group(1), 16) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grantee_dir"); ap.add_argument("events_csv"); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    g = Path(a.grantee_dir)

    legs, dates = collections.defaultdict(dict), {}           # pool -> symbol -> {addr, q}
    for r in csv.DictReader(open(g / "measured_quantities.csv")):
        if r["type"] != "pool (Infinity)":
            continue
        leg = legs[r["contract"].lower()].setdefault(r["token"].upper(), {"addr": set(), "q": {}})
        if r["address"].strip():
            leg["addr"].add(r["address"].lower())
        leg["q"][r["checkpoint"]] = float(r["quantity"])
        dates[r["checkpoint"]] = r["date"]
    events = load_events(a.events_csv)
    if set(events) != set(legs):
        fail(f"el export no cubre exactamente los pools Infinity: faltan {sorted(set(legs) - set(events))}, "
             f"sobran {sorted(set(events) - set(legs))}")

    start = dt.date.fromisoformat(dates["start"])
    last = max(dt.date.fromisoformat(d) for d in dates.values())
    days = [start + dt.timedelta(i) for i in range((last - start).days + 1)]

    rows, bad, checks = [], 0, 0
    print("Reservas reconstruidas vs cantidades comiteadas (la prueba del punto ciego):")
    for pool, ev in sorted(events.items()):
        init = next((e for e in ev if e["event"] == "init"), None)
        if init is None:
            fail(f"{pool[:12]}… sin evento Initialize en el export")
        cur = {init["currency0"]: 0, init["currency1"]: 1}
        by_addr = {next(iter(l["addr"])): sym for sym, l in legs[pool].items() if l["addr"]}
        if set(by_addr) != set(cur):
            fail(f"{pool[:12]}… monedas del Initialize {sorted(cur)} != legs comiteados {sorted(by_addr)}")
        dec = {}
        for addr in cur:
            dec[addr] = cached_decimals(addr)
            if dec[addr] is None:
                fail(f"sin decimales en el cache del pipeline para {addr}")
        series = replay(ev, days)
        n_mod = sum(e["event"] == "modify" for e in ev)
        print(f"  {pool[:14]}…  {n_mod} ModifyLiquidity · init {init['day']}")
        for addr, pos in cur.items():
            sym = by_addr[addr]
            qty = {d: (0.0 if v is None else v[pos] / 10 ** dec[addr]) for d, v in series.items()}
            for cp, want in legs[pool][sym]["q"].items():
                checks += 1
                got = qty[dt.date.fromisoformat(dates[cp])]
                ok = abs(got - want) <= REL_TOL * max(1.0, abs(want))
                bad += not ok
                print(f"    {'ok ' if ok else 'DIF'} {sym:<6} {cp:<8} comiteado {want:>16,.6f}  eventos {got:>16,.6f}"
                      + ("" if ok else f"  (eventos {'-' if got < want else '+'}{abs(got - want):,.6f})"))
            prev = None
            for d in days:
                raw = 0 if series[d] is None else series[d][pos]
                if prev is not None and raw != prev:
                    rows.append([pool, addr, sym, dec[addr], d.isoformat(), str(raw - prev),
                                 0 if series[d] is None else series[d][3]])
                prev = raw
    print(f"Coinciden {checks - bad}/{checks}")
    if bad:
        fail(f"{bad} checkpoint(s) difieren: si los eventos dan MAS que lo comiteado, el escaneo local del "
             f"pipeline perdio posiciones y la cifra oficial infracuenta — hay que decidir antes de seguir")
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["pool", "token", "symbol", "decimals", "local_date", "net_raw", "transfers"])
        w.writerows(rows)
    print(f"Escrito {out} ({len(rows)} filas de flujo diario derivado)")


if __name__ == "__main__":
    main()
