#!/usr/bin/env python3
"""Cohort summary: every measured grantee in one JSON + one CSV, for the report
and its charts.

Everything the general report quotes about the cohort — totals, $/OP, how
many grants met M1/M2, program length, when the peak came, what share of the
peak was given back, approval-to-start lag — used to be computed by hand from
the per-grantee files and pasted in. This script derives all of it from the
committed measurements so a re-run of the pipeline is one command away from a
refreshed report, and every number in the post can be traced to a file here.

Inputs (all already in the repo, nothing is measured here):
  output/<grantee>/scorecard.csv                 official figures (8 run.py grantees)
  output/<grantee>/supplementary_peak.csv        peak + end of the daily series
  output/<grantee>/supplementary_daily_curve.csv the validated daily ΔTVL series
  output/oku/… + output/oku_wallet_cohort.csv    Oku, measured by its own script
  registry `grantees` + `windows` tabs           budgets, targets, dates (live)

Milestone rule, as in metrics.compute: a target is met if the daily series
reached it on any in-window day. The "best 7-day average" variant is computed
too, as the anti-gaming check the report discusses. The snapshot figure is
carried as data only.

OP prices (claim date, end date, and the cohort's mean/earliest/latest dates)
come from DefiLlama's coins API, so the report's "OP moved too much" section
and the price-effect figure are derived here too.

Outputs:
  reports/cohort.json      per-grantee records (with the in-window curve) + cohort stats
  reports/cohort.csv       one row per grantee
  reports/cohort_table.md  the report's results table, ready to paste

Usage:
  python3 scripts/cohort_summary.py [--out reports]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import registry  # noqa: E402
from oku_wallet_cohort import COINS_API_URL, GRANT_ID as OKU_GRANT_ID  # noqa: E402
from oku_wallet_cohort import requests  # noqa: E402

OUT = REPO / "output"
# OP on OP Mainnet, priced by the same DefiLlama coins API the pipeline uses to
# fill price gaps — end-of-day UTC-6 like every other checkpoint read.
OP_COIN = "optimism:0x4200000000000000000000000000000000000042"


def op_price(day: dt.date) -> float:
    ts = int(dt.datetime.combine(day, dt.time(23, 59, 59)).timestamp())
    resp = requests.get(COINS_API_URL.format(ts=ts, coins=OP_COIN), timeout=30)
    resp.raise_for_status()
    return float(resp.json()["coins"][OP_COIN]["price"])


def _mean_date(days: list[dt.date]) -> dt.date:
    return dt.date.fromordinal(round(statistics.mean(d.toordinal() for d in days)))


def _rows(path: Path) -> list[dict]:
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _num(text):
    text = (text or "").strip()
    return float(text) if text else None


def _bool(text):
    return {"True": True, "False": False}.get((text or "").strip())


def _curve(directory: Path) -> list[tuple[dt.date, float, bool]]:
    return [(dt.date.fromisoformat(r["Date"]), float(r["ΔTVL (USD)"]), r["in_window"] == "True")
            for r in _rows(directory / "supplementary_daily_curve.csv")]


def _registry_rows() -> tuple[dict, dict]:
    grantees = registry._read_tab("grantees")
    windows = registry._read_tab("windows")
    key = lambda frame: {str(r["grant_id"]).strip(): r for _, r in frame.iterrows()}
    return key(grantees), key(windows)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, no scipy. None when fewer than 3 pairs."""
    if len(xs) < 3:
        return None
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else None


def build(directory: Path, grantees: dict, windows: dict) -> dict:
    slug = directory.name
    peak_row = _rows(directory / "supplementary_peak.csv")[0]
    curve = _curve(directory)
    window = [(d, v) for d, v, w in curve if w]
    if not window:
        raise SystemExit(f"{slug}: the daily curve has no in-window rows")

    scorecard_path = directory / "scorecard.csv"
    if scorecard_path.exists():
        sc = _rows(scorecard_path)[0]
        grant_id = sc["grant_id"]
        official_end = float(sc["delta_tvl_usd"])
        ongoing = "incentive ongoing" in sc["window"]
    else:
        # Oku: measured by scripts/oku_wallet_cohort.py, no run.py scorecard.
        if slug != "oku":
            raise SystemExit(f"{slug}: no scorecard.csv and not a known special case")
        sc = {}
        grant_id = OKU_GRANT_ID
        official_end = float(peak_row["delta_tvl_at_end_usd"])
        ongoing = False
    g = grantees.get(grant_id)
    if g is None:
        raise SystemExit(f"{slug}: grant {grant_id} not in the registry grantees tab")
    w = windows.get(grant_id)
    if w is None:
        raise SystemExit(f"{slug}: grant {grant_id} not in the registry windows tab")

    start, end = window[0][0], window[-1][0]
    days = (end - start).days
    peak_date, peak = max(window, key=lambda dv: dv[1])
    values = [v for _, v in window]
    best7 = max(sum(values[i:i + 7]) / 7 for i in range(len(values) - 6)) if len(values) >= 7 else None
    t1 = registry._to_number(registry._cell(g, "target_milestone1"))
    t2 = registry._to_number(registry._cell(g, "target_total"))
    budget = registry._to_number(registry._cell(g, "budget_total_op"))
    verdict = lambda value, target: None if value is None or not target else value >= target
    if sc:
        # The scorecard already carries the verdicts; make sure this agrees.
        for mine, theirs, name in ((verdict(peak, t1), _bool(sc["milestone1_met"]), "M1"),
                                   (verdict(peak, t2), _bool(sc["total_target_met"]), "M2")):
            if mine != theirs:
                raise SystemExit(f"{slug}: {name} verdict from the curve ({mine}) != scorecard ({theirs})")
        if abs(float(sc["peak_delta_tvl_usd"]) - peak) > 1:
            raise SystemExit(f"{slug}: scorecard peak {sc['peak_delta_tvl_usd']} != curve peak {peak:.2f}")

    approved = registry._to_date(registry._cell(g, "initial_delivery_date"))
    claimed = registry._to_date(registry._cell(g, "date_tx1"))
    snapshot = registry._to_date(registry._cell(w, "snapshot"))
    op_at_claim = op_price(claimed) if claimed else None
    op_at_end = op_price(end)
    return {
        "slug": slug,
        "grantee": str(registry._cell(g, "grantee") or slug).strip(),
        "grant_id": grant_id,
        "op_budget": budget,
        "target_milestone1": t1,
        "target_total": t2,
        "scope": sc.get("scope", "Targeted (wallet cohort)"),
        "ongoing": ongoing,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "snapshot": snapshot.isoformat() if snapshot else None,
        "days": days,
        "weeks": round(days / 7, 1),
        "approved": approved.isoformat() if approved else None,
        "claimed": claimed.isoformat() if claimed else None,
        "approval_to_start_days": (start - approved).days if approved else None,
        "claim_to_start_days": (start - claimed).days if claimed else None,
        "delta_tvl_usd": round(official_end, 2),
        "usd_per_op": round(official_end / budget, 2) if budget else None,
        "delta_tvl_at_snapshot_usd": _num(sc.get("delta_tvl_at_snapshot_usd")),
        "delta_tvl_at_snapshot_end_prices_usd": next(
            (v for d, v in window if snapshot and d == snapshot), None),
        "peak_delta_tvl_usd": round(peak, 2),
        "peak_date": peak_date.isoformat(),
        "peak_day": (peak_date - start).days,
        "peak_position": round((peak_date - start).days / days, 3) if days else None,
        "share_of_peak_given_back": round((peak - official_end) / peak, 3) if peak > 0 else None,
        "best_7day_avg_usd": round(best7, 2) if best7 is not None else None,
        "milestone1_met": verdict(peak, t1),
        "total_target_met": verdict(peak, t2),
        "milestone1_met_7day": verdict(best7, t1),
        "total_target_met_7day": verdict(best7, t2),
        "op_price_at_claim": round(op_at_claim, 4) if op_at_claim else None,
        "op_price_at_end": round(op_at_end, 4),
        "op_value_at_claim_usd": round(budget * op_at_claim) if budget and op_at_claim else None,
        "op_value_at_end_usd": round(budget * op_at_end) if budget else None,
        "retention_30d_pct": _num(sc.get("retention_30d_pct")),
        "price_qty_wedge_usd": _num(sc.get("price_qty_wedge_usd")),
        "usd_level_change": _num(sc.get("usd_level_change")),
        "curve_coverage": peak_row["coverage"],
        "curve_source": peak_row["source"],
        "curve": [[d.isoformat(), v, w] for d, v, w in curve],
    }


def cohort_stats(gs: list[dict]) -> dict:
    total = sum(g["delta_tvl_usd"] for g in gs)
    op = sum(g["op_budget"] or 0 for g in gs)
    weeks = [g["weeks"] for g in gs]
    peak_pos = [g["peak_position"] for g in gs if g["peak_position"] is not None]
    lag = [g["approval_to_start_days"] for g in gs if g["approval_to_start_days"] is not None]
    claim_lag = [g["claim_to_start_days"] for g in gs if g["claim_to_start_days"] is not None]
    given_back = [(g["weeks"], g["share_of_peak_given_back"]) for g in gs
                  if g["share_of_peak_given_back"] is not None]
    count = lambda key: {"met": sum(1 for g in gs if g[key] is True),
                         "evaluated": sum(1 for g in gs if g[key] is not None)}
    starts = [dt.date.fromisoformat(g["start"]) for g in gs]
    ends = [dt.date.fromisoformat(g["end"]) for g in gs]
    claims = [dt.date.fromisoformat(g["claimed"]) for g in gs if g["claimed"]]
    marks = {"earliest_claim": min(claims), "earliest_start": min(starts),
             "mean_start": _mean_date(starts), "mean_end": _mean_date(ends), "latest_end": max(ends)}
    op_table = {k: {"date": d.isoformat(), "price": round(op_price(d), 4)} for k, d in marks.items()}
    pct = lambda a, b: round((op_table[b]["price"] / op_table[a]["price"] - 1) * 100, 1)
    # Price effect: TVL level change at each day's prices vs the fixed-price
    # S8 figure, over the grantees whose scorecard carries the breakdown.
    with_level = [g for g in gs if g["usd_level_change"] is not None]
    level = sum(g["usd_level_change"] for g in with_level)
    fixed = sum(g["delta_tvl_usd"] for g in with_level)
    return {
        "op_price": {**op_table,
                     "mean_start_to_mean_end_pct": pct("mean_start", "mean_end"),
                     "earliest_start_to_latest_end_pct": pct("earliest_start", "latest_end"),
                     "op_value_at_claim_usd": sum(g["op_value_at_claim_usd"] or 0 for g in gs),
                     "op_value_at_end_usd": sum(g["op_value_at_end_usd"] or 0 for g in gs)},
        "price_effect": {"grantees": len(with_level), "usd_level_change": round(level, 2),
                         "delta_tvl_fixed_prices": round(fixed, 2), "price_effect_usd": round(level - fixed, 2)},
        "grantees": len(gs),
        "op_budget_total": op,
        "delta_tvl_usd_total": round(total, 2),
        "usd_per_op": round(total / op, 2) if op else None,
        "positive": sum(1 for g in gs if g["delta_tvl_usd"] > 0),
        "ongoing": sum(1 for g in gs if g["ongoing"]),
        "milestone1": count("milestone1_met"),
        "total_target": count("total_target_met"),
        "milestone1_7day": count("milestone1_met_7day"),
        "total_target_7day": count("total_target_met_7day"),
        "weeks": {"median": round(statistics.median(weeks), 1), "mean": round(statistics.mean(weeks), 1),
                  "min": min(weeks), "max": max(weeks)},
        "peak_day": {"median": statistics.median(g["peak_day"] for g in gs),
                     "median_position": round(statistics.median(peak_pos), 2),
                     "before_last_10pct": sum(1 for p in peak_pos if p < 0.9)},
        "approval_to_start_days": {"median": statistics.median(lag) if lag else None,
                                   "mean": round(statistics.mean(lag), 1) if lag else None,
                                   "n": len(lag)},
        "claim_to_start_days": {"median": statistics.median(claim_lag) if claim_lag else None,
                                "n": len(claim_lag)},
        "spearman_weeks_vs_share_given_back": _spearman([w for w, _ in given_back],
                                                        [s for _, s in given_back]),
    }


def results_table(gs: list[dict]) -> str:
    """The report's results table, in Markdown, straight from the records."""
    money = lambda v: f"−${-v:,.0f}" if v < 0 else f"${v:,.0f}"
    ratio = lambda v: f"−{-v:.2f}" if v < 0 else f"{v:.2f}"
    target = lambda v: "—" if not v else (f"${v / 1e6:g}M" if v >= 1e6 else f"${v / 1e3:g}K")
    mark = lambda v: "—" if v is None else ("✓" if v else "✗")
    pct = lambda v: "—" if v is None else f"{v:.1f}%"
    lines = ["| Grantee | OP | S8 ΔTVL | $/OP | M1 target | M1 | M2 target | M2 | Retention +30d | Peak (date) | Length |",
             "|---|---:|---:|---:|---:|:-:|---:|:-:|---:|---:|---:|"]
    for g in gs:
        name = g["grantee"].split(" (")[0] + (" *(interim)*" if g["ongoing"] else "")
        peak_date = dt.date.fromisoformat(g["peak_date"]).strftime("%b %-d")
        lines.append(f"| {name} | {g['op_budget']:,.0f} | {money(g['delta_tvl_usd'])} | {ratio(g['usd_per_op'])} | "
                     f"{target(g['target_milestone1'])} | {mark(g['milestone1_met'])} | {target(g['target_total'])} | "
                     f"{mark(g['total_target_met'])} | {pct(g['retention_30d_pct'])} | "
                     f"{money(g['peak_delta_tvl_usd'])} ({peak_date}) | {round(g['weeks'])} wk |")
    total = sum(g["delta_tvl_usd"] for g in gs); op = sum(g["op_budget"] or 0 for g in gs)
    m1 = sum(1 for g in gs if g["milestone1_met"]); m2 = sum(1 for g in gs if g["total_target_met"])
    lines.append(f"| **Total** | **{op:,.0f}** | **{money(total)}** | **{total / op:.2f}** | | **{m1} met** | | **{m2} met** | | | |")
    return "\n".join(lines) + "\n"


CSV_COLUMNS = ["slug", "grantee", "grant_id", "scope", "ongoing", "op_budget", "start", "end", "claimed",
               "op_price_at_claim", "op_price_at_end",
               "weeks", "approved", "approval_to_start_days", "delta_tvl_usd", "usd_per_op",
               "target_milestone1", "milestone1_met", "target_total", "total_target_met",
               "peak_delta_tvl_usd", "peak_date", "peak_day", "peak_position",
               "share_of_peak_given_back", "best_7day_avg_usd", "milestone1_met_7day",
               "total_target_met_7day", "delta_tvl_at_snapshot_usd", "retention_30d_pct",
               "price_qty_wedge_usd", "usd_level_change", "curve_coverage"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO / "reports")
    a = ap.parse_args()

    grantees, windows = _registry_rows()
    dirs = sorted(d for d in OUT.iterdir() if d.is_dir() and (d / "supplementary_peak.csv").exists())
    gs = [build(d, grantees, windows) for d in dirs]
    gs.sort(key=lambda g: -(g["usd_per_op"] if g["usd_per_op"] is not None else g["delta_tvl_usd"]))
    stats = cohort_stats(gs)

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "cohort.json").write_text(json.dumps(
        {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "interim_cutoff": registry.INTERIM_CUTOFF.isoformat() if registry.INTERIM_CUTOFF else None,
         "milestone_rule": "met if the validated daily ΔTVL series reached the target on any "
                           "in-window day (fixed end prices); snapshot kept as data only",
         "cohort": stats, "grantees": gs},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    with (a.out / "cohort.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(gs)
    (a.out / "cohort_table.md").write_text(results_table(gs), encoding="utf-8")

    print(f"{stats['grantees']} grantees · ΔTVL ${stats['delta_tvl_usd_total']:,.0f} · "
          f"${stats['usd_per_op']:.2f}/OP · {stats['positive']} positive · "
          f"M1 {stats['milestone1']['met']}/{stats['milestone1']['evaluated']} · "
          f"M2 {stats['total_target']['met']}/{stats['total_target']['evaluated']}")
    print(f"weeks median {stats['weeks']['median']} mean {stats['weeks']['mean']} · "
          f"peak day median {stats['peak_day']['median']} ({stats['peak_day']['median_position']:.0%}) · "
          f"approval->start median {stats['approval_to_start_days']['median']} d")
    for g in gs:
        m = lambda v: "—" if v is None else ("✓" if v else "✗")
        print(f"  {g['grantee']:<32} ${g['delta_tvl_usd']:>13,.0f}  peak ${g['peak_delta_tvl_usd']:>13,.0f} "
              f"{g['peak_date']}  M1 {m(g['milestone1_met'])} M2 {m(g['total_target_met'])}  {g['weeks']:>5} wk")
    op = stats["op_price"]
    print(f"OP {op['mean_start']['date']} ${op['mean_start']['price']:.3f} -> {op['mean_end']['date']} "
          f"${op['mean_end']['price']:.3f} ({op['mean_start_to_mean_end_pct']:+.1f}%) · executed OP worth "
          f"${op['op_value_at_claim_usd']:,.0f} at claim, ${op['op_value_at_end_usd']:,.0f} at end")
    pe = stats["price_effect"]
    print(f"price effect over {pe['grantees']} grants: level {pe['usd_level_change']:+,.0f} vs fixed "
          f"{pe['delta_tvl_fixed_prices']:+,.0f} -> {pe['price_effect_usd']:+,.0f}")
    print(f"wrote {a.out / 'cohort.json'}, {a.out / 'cohort.csv'} and {a.out / 'cohort_table.md'}")


if __name__ == "__main__":
    main()
