#!/usr/bin/env python3
"""Daily veNFT collateral of 40acres' loan contracts, rebuilt from events (Dune).

measure.measure_loan_collateral reads, at one block, the sum of locked(tokenId)
over the veNFTs a loan contract holds — hundreds of NFTs, so hundreds of reads
per contract per day, which made a daily series by RPC impractical. The same
number follows from events: ERC-721 Transfers to/from the loan contract say
which NFTs it holds, and the VotingEscrow's lock events say how much each one
locks. Semantics verified in Velodrome's VotingEscrow.sol (Aerodrome's is a
fork with an identical interface):
  add   Deposit            locked += value
  zero  Withdraw           locked = 0 (the NFT is burned)
  set   Merge (to)         locked = amountFinal        zero  Merge (from)
  set   Split (t1, t2)     locked = splitAmount1/2     zero  Split (from)
  zero  DepositManaged     locked moves into the managed NFT
  set   WithdrawManaged    locked = weight + locked rewards (the event's _weight)
Permanent (un)locks don't change the amount.

Ownership is replayed chronologically. The pipeline doesn't: it concatenates
ALL transfers in, then ALL transfers out, and keeps each NFT's last direction,
so an NFT that left and came back counts as gone. Both are computed and
checked against the committed quantity and NFT count at every checkpoint. Only
if the chronological replay matches everywhere is anything written; if only
the pipeline's logic matches, the committed figure is missing re-deposited
collateral and that is a decision for a person, not this script.

Output: daily quantities in rpc_daily_peak.py's --extra-daily format.

Usage:
  python3 scripts/venft_events_replay.py output/40acres-finance <events.csv> --out <daily.csv>
"""
import argparse, collections, csv, datetime as dt, sys
from pathlib import Path

REL_TOL = 1e-9
DUNE_TO_LABEL = {"optimism": "OP Mainnet", "base": "Base"}


def fail(msg):
    print(f"FALLA: {msg}\nNo se escribio nada.")
    sys.exit(1)


def replay(events, days):
    """events: dicts sorted by (block, idx) with kind in/out/add/set/zero,
    token (int id), amount (int raw or None), day. Returns {day: {"chrono":
    (sum, count), "pipeline": (sum, count)}}."""
    amount = collections.defaultdict(int)
    held, ever_in, ever_out = set(), set(), set()
    out, i = {}, 0
    for d in days:
        while i < len(events) and events[i]["day"] <= d:
            e, t = events[i], events[i]["token"]
            k = e["kind"]
            if k == "in":
                held.add(t); ever_in.add(t)
            elif k == "out":
                held.discard(t); ever_out.add(t)
            elif k == "add":
                amount[t] += e["amount"]
            elif k == "set":
                amount[t] = e["amount"]
            elif k == "zero":
                amount[t] = 0
            else:
                raise ValueError(f"tipo de evento desconocido: {k}")
            i += 1
        pipe = ever_in - ever_out
        out[d] = {"chrono": (sum(amount[t] for t in held), len(held)),
                  "pipeline": (sum(amount[t] for t in pipe), len(pipe))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grantee_dir"); ap.add_argument("events_csv"); ap.add_argument("--out", required=True)
    a = ap.parse_args()

    loans, dates = {}, {}
    for r in csv.DictReader(open(Path(a.grantee_dir) / "measured_quantities.csv")):
        if r["type"] != "loan":
            continue
        L = loans.setdefault(r["chain"], {"contract": r["contract"].lower(), "label": r["pool"],
                                          "token": r["token"].upper(), "q": {}, "nfts": {}})
        L["q"][r["checkpoint"]] = float(r["quantity"])
        L["nfts"][r["checkpoint"]] = int(float(r["nfts"]))
        dates[r["checkpoint"]] = r["date"]

    by_chain = collections.defaultdict(list)
    for r in csv.DictReader(open(a.events_csv)):
        chain = DUNE_TO_LABEL.get(r["blockchain"])
        if chain not in loans:
            fail(f"cadena del export sin contrato de prestamo comiteado: {r['blockchain']}")
        amt = (r.get("amount") or "").strip()
        by_chain[chain].append({"block": int(r["block_number"]), "idx": int(r["log_index"]),
                                "day": dt.date.fromisoformat(r["local_date"][:10]), "kind": r["kind"],
                                "token": int(r["token_id"]), "amount": int(amt) if amt else None})
    if set(by_chain) != set(loans):
        fail(f"el export no cubre los prestamos comiteados: faltan {sorted(set(loans) - set(by_chain))}")

    start = dt.date.fromisoformat(dates["start"])
    last = max(dt.date.fromisoformat(d) for d in dates.values())
    days = [start + dt.timedelta(i) for i in range((last - start).days + 1)]

    ok_logic = {"chrono": 0, "pipeline": 0}; checks = 0; series = {}
    print("Colateral reconstruido vs comiteado (cantidad y numero de NFT):")
    for chain, L in sorted(loans.items()):
        ev = sorted(by_chain[chain], key=lambda e: (e["block"], e["idx"]))
        s = replay(ev, days)
        series[chain] = s
        kinds = collections.Counter(e["kind"] for e in ev)
        print(f"  {chain} {L['label']} ({L['contract'][:10]}…) · eventos {dict(kinds)}")
        for cp in ("start", "snapshot", "end", "plus30d"):
            if cp not in L["q"]:
                continue
            checks += 1
            d = dt.date.fromisoformat(dates[cp])
            for logic in ("chrono", "pipeline"):
                raw, n = s[d][logic]
                good = abs(raw / 1e18 - L["q"][cp]) <= REL_TOL * max(1.0, L["q"][cp]) and n == L["nfts"][cp]
                ok_logic[logic] += good
                print(f"    {'ok ' if good else 'DIF'} {cp:<8} {logic:<8} comiteado {L['q'][cp]:>18,.6f} ({L['nfts'][cp]} NFT)"
                      f"  eventos {raw / 1e18:>18,.6f} ({n} NFT)")
    print(f"Coinciden: cronologico {ok_logic['chrono']}/{checks} · logica del pipeline {ok_logic['pipeline']}/{checks}")
    if ok_logic["chrono"] != checks:
        if ok_logic["pipeline"] == checks:
            fail("solo cuadra la logica del pipeline: la cifra comiteada omite NFT que salieron y volvieron "
                 "a entrar al prestamo — decidir antes de seguir")
        fail("la reconstruccion no reproduce los checkpoints comiteados")

    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["chain", "contract", "label", "token", "date", "quantity", "nfts"])
        for chain, L in sorted(loans.items()):
            for d in days:
                raw, n = series[chain][d]["chrono"]
                w.writerow([chain, L["contract"], L["label"], L["token"], d.isoformat(), repr(raw / 1e18), n])
    print(f"Escrito {out}")


if __name__ == "__main__":
    main()
