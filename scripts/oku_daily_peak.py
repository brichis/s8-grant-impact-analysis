#!/usr/bin/env python3
"""Supplementary daily curve and peak for Oku's wallet cohort.

oku_wallet_cohort.py measures Oku as the cohort's Morpho vault position at
incentive start and end: each wallet's shares (balanceOf), converted to USDC
with convertToAssets at the same block. This reads exactly that, every day,
reusing that script's registry, vault and call helpers — share price accrues
interest without an event, so the path can't be rebuilt from transfers.

Every wallet's daily position must reproduce its committed qty_start and
qty_end (output/oku_wallet_cohort.csv) before anything is written; the curve
is ΔTVL at the end-date USDC price, as the S8 formula, and the peak is taken
inside the incentive window only. The +30 day tail is drawn for context.
Supplementary only: nothing official changes.

Usage: python3 scripts/oku_daily_peak.py [--probe N]
"""
import argparse, csv, datetime as dt, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oku_wallet_cohort as oku  # noqa: E402

REL_TOL = 1e-9


def fail(msg):
    print(f"FALLA: {msg}\nNo se escribio nada.")
    sys.exit(1)


def positions(rpc, vault, wallets, block, decimals):
    """{wallet: USDC} at `block` — same two-phase read as oku_wallet_cohort."""
    shares = rpc.call_batch([oku._eth_call(vault, oku.BALANCE_OF + oku._addr_arg(w), block, rpc.slug) for w in wallets])
    held = [(w, int(s, 16)) for w, s in zip(wallets, shares) if int(s, 16) > 0]
    assets = rpc.call_batch([oku._eth_call(vault, oku.CONVERT_TO_ASSETS + hex(n)[2:].rjust(64, "0"), block, rpc.slug)
                             for _, n in held])
    out = {w: 0.0 for w in wallets}
    for (w, _), raw in zip(held, assets):
        out[w] = int(raw, 16) / 10 ** decimals
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--probe", type=int, default=0); a = ap.parse_args()
    base = oku.BASE
    committed = {r["wallet"].lower(): (float(r["qty_start"]), float(r["qty_end"]))
                 for r in csv.DictReader(open(base / "output" / "oku_wallet_cohort.csv"))}

    windows = oku._read_tab("windows")
    w = windows[windows["grant_id"].astype(str).str.strip() == oku.GRANT_ID].iloc[0]
    start, end = oku._to_date(w["incentive_start_date"]), oku._to_date(w["incentive_end_date"])
    vault, chain = oku._cohort_vault()
    wallets = sorted({x.strip().lower() for x in oku._fetch_oku_wallets()["wallet_address"].dropna()})
    if set(wallets) != set(committed):
        fail(f"la cohorte del registro ya no coincide con la comiteada: "
             f"{len(set(wallets) ^ set(committed))} wallets distintas")

    rpc = oku.ArchiveRPC(oku.rpc_slug(chain), oku.RPC_CACHE)
    head_day = dt.date.today() - dt.timedelta(days=1)
    last = min(end + dt.timedelta(days=30), head_day)
    days = [start + dt.timedelta(i) for i in range((last - start).days + 1)]
    b_end = rpc.block_at(oku._end_of_day_ts(end))
    asset = "0x" + rpc.read(vault, oku.ASSET, b_end)[-40:]
    decimals = int(rpc.read(asset, oku.DECIMALS, b_end), 16)
    print(f"Oku: {len(wallets)} wallets · vault {vault} ({chain}) · {len(days)} dias ({start} -> {last}; fin {end})", flush=True)

    if a.probe:
        sample = days[len(days) // 2: len(days) // 2 + a.probe]
        t = time.monotonic()
        for d in sample:
            positions(rpc, vault, wallets, rpc.block_at(oku._end_of_day_ts(d)), decimals)
        rpc.flush()
        secs = (time.monotonic() - t) / len(sample)
        print(f"PRUEBA: {secs:.1f} s/dia -> ~{secs * len(days) / 60:.0f} min")
        return

    daily, t = {}, time.monotonic()
    for i, d in enumerate(days, 1):
        daily[d] = positions(rpc, vault, wallets, rpc.block_at(oku._end_of_day_ts(d)), decimals)
        if i % 20 == 0 or i == len(days):
            rpc.flush()
            print(f"  {i}/{len(days)} dias · {time.monotonic() - t:.0f} s", flush=True)

    bad = 0
    for wlt in wallets:
        for label, day, want in (("start", start, committed[wlt][0]), ("end", end, committed[wlt][1])):
            got = daily[day][wlt]
            if abs(got - want) > REL_TOL * max(1.0, abs(want)):
                bad += 1
                print(f"  DISTINTO {wlt} {label}: comiteado {want:,.6f} diario {got:,.6f}")
    checks = 2 * len(wallets)
    print(f"Validacion por wallet contra lo comiteado: {checks - bad}/{checks} coinciden")
    if bad:
        fail(f"{bad} posiciones no cuadran")

    ts = oku._end_of_day_ts(end)
    coin = f"{oku.coins_slug(chain)}:{asset}"
    price = oku.requests.get(oku.COINS_API_URL.format(ts=ts, coins=coin), timeout=30).json()["coins"][coin]["price"]
    total = {d: sum(daily[d].values()) for d in days}
    curve = [(d, (total[d] - total[start]) * price) for d in days]
    at_end = dict(curve)[end]
    peak_d, peak_v = max(((d, v) for d, v in curve if d <= end), key=lambda dv: dv[1])

    raw = base / "data" / "rpc_daily" / "oku_daily_quantities.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    with open(raw, "w", newline="") as f:
        wr = csv.writer(f); wr.writerow(["date", "wallet", "usdc"])
        for d in days:
            for wlt in wallets:
                wr.writerow([d.isoformat(), wlt, repr(daily[d][wlt])])
    out = base / "output" / "oku"; out.mkdir(parents=True, exist_ok=True)
    with open(out / "supplementary_daily_curve.csv", "w", newline="") as f:
        wr = csv.writer(f); wr.writerow(["Date", "ΔTVL (USD)", "in_window"])
        for d, v in curve:
            wr.writerow([d.isoformat(), round(v), d <= end])
    with open(out / "supplementary_peak.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["peak_delta_tvl_usd", "peak_date", "delta_tvl_at_end_usd", "coverage", "source", "note"])
        wr.writerow([round(peak_v, 2), peak_d.isoformat(), round(at_end, 2),
                     f"{len(wallets)}-wallet cohort in {vault} ({chain})",
                     "daily balanceOf + convertToAssets per wallet, with oku_wallet_cohort.py's own helpers",
                     f"USDC price at end ${price:.6f} (DefiLlama coins API)"])
    print(f"ΔTVL al cierre: ${at_end:,.2f} (Σdelta x precio)")
    print(f"Pico en ventana: ${peak_v:,.0f} el {peak_d}  ·  al cierre ${at_end:,.0f}")
    print(f"Escrito: {raw.relative_to(base)}, output/oku/supplementary_peak.csv y supplementary_daily_curve.csv")


if __name__ == "__main__":
    main()
