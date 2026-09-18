#!/usr/bin/env python3
"""Supplementary daily peak for vault/lend grantees, from daily on-chain reads.

dune_peak.py rebuilds pool balances from transfers. That does not work for
vault and lend contracts: ERC-4626 totalAssets() and Aave-style aToken supply
grow with interest that emits no event, so no transfer history adds up to
them. But each is a handful of calls per contract per day — cheap enough to
simply read every day with the pipeline's own measure.py, which is what this
does (unlike Infinity tick walks or veNFT enumeration, which made daily reads
impractical for other grantees).

Same discipline as dune_peak.py: the daily series must reproduce, exactly,
every checkpoint the committed output measured before the ΔTVL curve (fixed
end-date price, as the S8 formula) and its in-window peak are written. Writes
nothing on a mismatch and never touches the scorecard or any M1/M2 verdict.

Dates come from the committed output, not the live registry, so the curve
lines up with the published scorecard even if the sheet has changed since.
Days are read in chunks: measure_all flushes the RPC cache at the end of each,
so an interrupted run resumes from the last finished chunk.

--extra-daily supplies legs this script shouldn't read itself — 40acres' veNFT
loan collateral, rebuilt from events by venft_events_replay.py — as daily
quantities (chain, contract, label, token, date, quantity). Their contracts
are skipped by the RPC pass, and the whole grantee is then validated and drawn
together.

Usage:
  python3 scripts/rpc_daily_peak.py <GRANT_ID> output/<grantee> [--probe N]
      [--extra-daily FILE.csv] [--note TEXT]
"""
import argparse, collections, csv, dataclasses, datetime as dt, io, sys, time
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from dotenv import find_dotenv, load_dotenv  # noqa: E402
load_dotenv(find_dotenv())
import measure   # noqa: E402
import registry  # noqa: E402

RPC_CACHE = REPO / "data" / "rpc_cache.json"
REL_TOL = 1e-9
CHUNK_DAYS = 20
SUPPORTED = ("vault", "lend")    # pools go through dune_peak.py; loans are the expensive veNFT path


def fail(msg):
    print(f"FALLA: {msg}\nNo se escribio nada.")
    sys.exit(1)


def measure_days(cfg, days):
    with redirect_stdout(io.StringIO()):
        return measure.measure_all(cfg, {d.isoformat(): d for d in days}, RPC_CACHE).to_dict("records")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grant_id"); ap.add_argument("grantee_dir")
    ap.add_argument("--probe", type=int, default=0, help="time N cold mid-window days and stop")
    ap.add_argument("--out"); ap.add_argument("--note", default="")
    ap.add_argument("--extra-daily", action="append", default=[])
    ap.add_argument("--allow-pools", action="store_true",
                    help="read plain pools daily too — cheap for a handful of them "
                         "(a balanceOf per leg), which beats a Dune round trip; "
                         "for fifty pools use dune_peak.py instead")
    a = ap.parse_args()
    g = Path(a.grantee_dir); out = Path(a.out) if a.out else g

    qty, dates, label = collections.defaultdict(dict), {}, {}
    for r in csv.DictReader(open(g / "measured_quantities.csv")):
        k = (r["chain"], r["contract"].lower(), r["token"].upper())
        qty[k][r["checkpoint"]] = float(r["quantity"])
        dates[r["checkpoint"]] = r["date"]
        label[k] = r["pool"]
    price = {(r["chain"], r["contract"].lower(), r["token"].upper()): float(r["price_end"])
             for r in csv.DictReader(open(g / "table_contracts.csv"))}
    official = float(list(csv.DictReader(open(g / "scorecard.csv")))[0]["delta_tvl_usd"])

    cfg = registry.load_grant(a.grant_id)
    live = {(c["chain"], c["address"].lower()) for c in cfg.scope_contracts}
    done = {(k[0], k[1]) for k in qty}
    if live != done:
        fail(f"el scope del registro ya no coincide con el output comiteado: "
             f"solo registro {sorted(live - done)} · solo output {sorted(done - live)}")
    extra = collections.defaultdict(dict)
    for path in a.extra_daily:
        for r in csv.DictReader(open(path)):
            extra[(r["chain"], r["contract"].lower(), r["token"].upper())][r["date"]] = float(r["quantity"])
    extra_contracts = {(k[0], k[1]) for k in extra}
    if extra_contracts - done:
        fail(f"--extra-daily trae contratos que no estan en el output comiteado: {sorted(extra_contracts - done)}")
    to_read = [c for c in cfg.scope_contracts if (c["chain"], c["address"].lower()) not in extra_contracts]
    # Uniswap v4 pools are read with a StateView tick walk — a few hundred calls
    # per pool-day at wide tick spacings (Super DCA: spacing 60, 2-9 ticks) —
    # so they're cheap enough to read daily. v2/v3 pools still go through
    # dune_peak.py, and Infinity pools through infinity_events_replay.py.
    import uniswap_v4  # noqa: E402
    is_v4 = lambda c: c["type"] == "pool" and uniswap_v4.is_pool_id(c["address"])
    cheap_pool = lambda c: is_v4(c) or (a.allow_pools and c["type"] == "pool")
    bad = sorted({c["type"] for c in to_read if not cheap_pool(c)} - set(SUPPORTED))
    if bad:
        fail(f"tipos {bad} no soportados aqui (pools v2/v3: dune_peak.py; Infinity: "
             f"infinity_events_replay.py; loans: --extra-daily desde eventos)")
    cfg = dataclasses.replace(cfg, scope_contracts=to_read)

    start, end = dt.date.fromisoformat(dates["start"]), dt.date.fromisoformat(dates["end"])
    last = max(dt.date.fromisoformat(d) for d in dates.values())
    days = [start + dt.timedelta(i) for i in range((last - start).days + 1)]
    print(f"{cfg.grantee}: {len(cfg.scope_contracts)} contrato(s), {len(days)} dias ({start} -> {last})", flush=True)

    if a.probe:
        mid = len(days) // 2
        sample = days[mid:mid + a.probe]
        t = time.monotonic(); measure_days(cfg, sample); secs = (time.monotonic() - t) / len(sample)
        print(f"PRUEBA: {len(sample)} dias ({sample[0]} .. {sample[-1]}) a {secs:.1f} s/dia "
              f"-> ventana completa ~{secs * len(days) / 60:.0f} min en frio")
        return

    rows = []
    for i in range(0, len(days), CHUNK_DAYS):
        chunk = days[i:i + CHUNK_DAYS]
        t = time.monotonic(); rows += measure_days(cfg, chunk)
        print(f"  {chunk[0]} .. {chunk[-1]}: {time.monotonic() - t:.0f} s", flush=True)

    daily = collections.defaultdict(dict)
    for r in rows:
        daily[(r["chain"], r["contract"].lower(), str(r["token"]).upper())][r["date"]] = float(r["quantity"])
    for k, series in extra.items():
        daily[k].update(series)
    if set(daily) != set(qty):
        fail(f"los legs medidos no coinciden con los comiteados: {sorted(set(daily) ^ set(qty))}")

    checks = bad_n = 0
    for k in sorted(qty):
        for cp, want in qty[k].items():
            checks += 1
            got = daily[k].get(dates[cp])
            if got is None or abs(got - want) > REL_TOL * max(1.0, abs(want)):
                bad_n += 1
                print(f"  DISTINTO {label[k]:<24} {k[2]:<8} {cp:<8} comiteado {want:,.6f}  diario {got}")
    print(f"Validacion contra checkpoints comiteados: {checks - bad_n}/{checks} coinciden")
    if bad_n:
        fail(f"{bad_n} checkpoint(s) no cuadran")

    curve = [(d, sum((daily[k][d.isoformat()] - qty[k]["start"]) * price[k] for k in qty)) for d in days]
    at_end = dict(curve)[end]
    slack = 5e-7 * sum(abs(qty[k]["end"] - qty[k]["start"]) for k in qty) + 0.01
    if abs(at_end - official) > slack:
        fail(f"la curva al cierre da ${at_end:,.2f} y el scorecard ${official:,.2f} (margen ${slack:,.2f})")
    peak_d, peak_v = max(((d, v) for d, v in curve if d <= end), key=lambda dv: dv[1])

    src = REPO / "data" / "rpc_daily" / f"{g.name}_daily_quantities.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["chain", "contract", "label", "token", "date", "quantity"])
        for k in sorted(daily):
            for d in days:
                w.writerow([k[0], k[1], label[k], k[2], d.isoformat(), repr(daily[k][d.isoformat()])])
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "supplementary_daily_curve.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["Date", "ΔTVL (USD)", "in_window"])
        for d, v in curve:
            w.writerow([d.isoformat(), round(v), d <= end])
    with open(out / "supplementary_peak.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["peak_delta_tvl_usd", "peak_date", "delta_tvl_at_end_usd", "coverage", "source", "note"])
        unpriced = sorted({k[2] for k in qty if price[k] == 0 and qty[k]["end"] != qty[k]["start"]})
        coverage = "100% of scope contracts" + (
            f"; {', '.join(unpriced)} unpriced on DefiLlama, counted at $0 as in the official figure" if unpriced else "")
        w.writerow([round(peak_v, 2), peak_d.isoformat(), round(at_end, 2), coverage,
                    "daily on-chain reads with the pipeline's own measure.py", a.note])
    print(f"Cierre: curva ${at_end:,.2f} vs scorecard ${official:,.2f} (margen ${slack:,.2f})")
    print(f"Pico en ventana: ${peak_v:,.0f} el {peak_d}  ·  al cierre ${at_end:,.0f}")
    print(f"Escrito: {src.relative_to(REPO)}, {out}/supplementary_peak.csv y supplementary_daily_curve.csv")


if __name__ == "__main__":
    main()
