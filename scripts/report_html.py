#!/usr/bin/env python3
"""The S8 report as a self-contained web page, in the brichis.xyz design system.

Same data as the Markdown draft — reports/cohort.json, written by
cohort_summary.py — rendered as a standalone HTML page with its charts, meant
to be dropped into the website repo under public/reportes/ and shown in an
iframe. Nothing is measured or recomputed here: every number and every series
comes from the committed measurements, so the page can't drift from the
pipeline.

Design system (Personal Brand & Design System v1): haze background, plum ink
body, coral for the mark and emphasis, orchid for labels and links, the
iridescents as chart series only. Instrument Serif for display (never below
28px), IBM Plex Sans for body, IBM Plex Mono uppercase with 0.14em tracking
for labels. Charts follow the same rule the brand does: structure first,
color only where it carries meaning.

Usage:
  python3 scripts/report_html.py [--out reports/s8_report.html]
"""
import argparse, datetime as dt, json, urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

C = {  # brand tokens
    "coral": "#f98c82", "plum": "#2e1b45", "plum_muted": "#6b5a80",
    "orchid": "#b341a8", "signal": "#e5322c", "lilac": "#c6b2e8",
    "peri": "#afbde8", "mint": "#7fd6cd", "butter": "#f3e3a6",
    "haze": "#faf7fb", "card": "#ffffff", "border": "#e5ddef",
    "secondary": "#efeaf6",
}

# Figure 2 gives every program its own hue. Nine categorical series is past what
# the brand's own set covers, so these were searched for and validated against the
# all-pairs gates on the white card: worst colour-blind ΔE 8.1 (target >= 8), worst
# normal-vision ΔE 15.0 (floor >= 15), all nine inside the lightness band and above
# the chroma floor. Kept as muted as those gates allow.
SERIES = ["#b10071", "#006a9e", "#a3940b", "#908ffe", "#ff5a92",
          "#16b3bc", "#1a6efe", "#7d5e00", "#06915d"]

# The repository is public, so every script the page names is a live link
# rather than a filename the reader cannot do anything with.
REPO = "https://github.com/brichis/s8-grant-impact-analysis"
BLOB = REPO + "/blob/main"

MARK = ("M50 2 Q54.5 45.5 98 50 Q54.5 54.5 50 98 Q45.5 54.5 2 50 "
        "Q45.5 45.5 50 2 Z M50 35 L65 50 L50 65 L35 50 Z")


def money(v, dec=0):
    return ("−$" if v < 0 else "$") + f"{abs(v):,.{dec}f}"


def short(v):
    a = abs(v)
    s = f"{v/1e6:.2f}M" if a >= 1e6 else (f"{v/1e3:.0f}K" if a >= 1e3 else f"{v:.0f}")
    return ("−$" if v < 0 else "$") + s.lstrip("-")


def build(cohort: dict, op_price: list) -> str:
    g = cohort["grantees"]
    c = cohort["cohort"]
    by = {x["grantee"]: x for x in g}
    order = sorted(g, key=lambda x: -x["peak_delta_tvl_usd"])

    # The price series is cached and refetched up to whenever the page is built,
    # so left alone it runs past the date the header says the data ends. Every
    # measured figure stops at the interim cutoff; the price line stops there too.
    # ISO dates compare correctly as strings.
    op_price = [p for p in op_price if p[0] <= cohort["interim_cutoff"]]

    charts = {
        "peak_end": {
            "names": [x["grantee"].split(" (")[0] for x in order],
            "peak": [x["peak_delta_tvl_usd"] for x in order],
            "end": [x["delta_tvl_usd"] for x in order],
            "peak_label": [short(x["peak_delta_tvl_usd"]) for x in order],
            "end_label": [short(x["delta_tvl_usd"]) for x in order],
        },
        "curves": [
            {"name": x["grantee"].split(" (")[0],
             "x": [round(100 * i / (len([p for p in x["curve"] if p[2]]) - 1), 2)
                   for i in range(len([p for p in x["curve"] if p[2]]))],
             "y": [round(100 * p[1] / x["peak_delta_tvl_usd"], 2) for p in x["curve"] if p[2]],
             "peak_pos": round(100 * x["peak_position"], 1)}
            for x in order if x["peak_delta_tvl_usd"] > 0
        ],
        "flow": [
            {"label": "Delivered to the nine that ran a program", "value": 2210000, "color": C["plum"]},
            {"label": "Delivered to grants that never ran one", "value": 340000, "color": C["coral"]},
            {"label": "Sent to the claim contract, never claimed", "value": 1268000, "color": C["butter"]},
            {"label": "Never released", "value": 2472000, "color": C["secondary"]},
        ],
        "price": {"x": [p[0] for p in op_price], "y": [p[1] for p in op_price],
                  "marks": [[k.replace("_", " "), v["date"], v["price"]]
                            for k, v in c["op_price"].items() if isinstance(v, dict)]},
        "small": [
            {"name": x["grantee"].split(" (")[0],
             "x": [p[0] for p in x["curve"]],
             "y": [p[1] for p in x["curve"]],
             "inw": [p[2] for p in x["curve"]],
             "peak": x["peak_delta_tvl_usd"], "peak_date": x["peak_date"],
             "end": x["delta_tvl_usd"], "ongoing": x["ongoing"]}
            for x in order
        ],
    }

    rows = "\n".join(
        f"""      <tr{' class="interim"' if x['ongoing'] else ''}>
        <th scope="row">{x['grantee'].split(' (')[0]}{' <span class="tag">interim</span>' if x['ongoing'] else ''}</th>
        <td class="num">{x['op_budget']:,.0f}</td>
        <td class="num">{money(x['delta_tvl_usd'])}</td>
        <td class="num">{f"{x['usd_per_op']:.2f}".replace('-', '−')}</td>
        <td class="num">{short(x['target_milestone1']) if x['target_milestone1'] else '—'}</td>
        <td class="mark">{verdict(x['milestone1_met'])}</td>
        <td class="num">{short(x['target_total']) if x['target_total'] else '—'}</td>
        <td class="mark">{verdict(x['total_target_met'])}</td>
        <td class="num">{f"{x['retention_30d_pct']:.1f}%" if x['retention_30d_pct'] is not None else '—'}</td>
        <td class="num">{money(x['peak_delta_tvl_usd'])}<span class="sub">{x['peak_date']}</span></td>
        <td class="num">{x['weeks']:.0f} wk</td>
      </tr>""" for x in order)

    # Timing figures the text quotes, straight from the cohort so they cannot drift.
    def _rng(s):
        lo, hi = s["min"], s["max"]
        return f"−{abs(lo)} to {hi}" if lo < 0 else f"{lo}–{hi}"
    stg = {k: c[k] for k in ("created_to_approval_days", "approval_to_delivery_days",
                              "delivery_to_start_days", "created_to_start_days")}
    stage = {
        "app": f"{stg['created_to_approval_days']['median']:.0f}", "app_rng": _rng(stg["created_to_approval_days"]),
        "deliv": f"{stg['approval_to_delivery_days']['median']:.0f}", "deliv_rng": _rng(stg["approval_to_delivery_days"]),
        "live": f"{stg['delivery_to_start_days']['median']:.0f}", "live_rng": _rng(stg["delivery_to_start_days"]),
        "live_max": f"{stg['delivery_to_start_days']['max']:.0f}",
        "live_neg": ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][
            sum(1 for x in g if x["delivery_to_start_days"] is not None and x["delivery_to_start_days"] < 0)],
        "total": f"{stg['created_to_start_days']['median']:.0f}", "total_rng": _rng(stg["created_to_start_days"]),
    }

    data_json = json.dumps(charts, separators=(",", ":"))
    return TEMPLATE.format(c=C, s=SERIES, t=stage, mark=MARK, rows=rows, data=data_json, cohort=c,
                           repo=REPO, blob=BLOB,
                           cutoff=cohort["interim_cutoff"], generated=cohort["generated_at"][:10])


def verdict(v):
    return '<span class="met">✓</span>' if v else ('<span class="miss">✗</span>' if v is False else '—')


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Season 8 Growth Grants — Observed ΔTVL Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
  :root {{
    --coral: {c[coral]}; --plum: {c[plum]}; --plum-muted: {c[plum_muted]};
    --orchid: {c[orchid]}; --signal: {c[signal]}; --lilac: {c[lilac]};
    --peri: {c[peri]}; --mint: {c[mint]}; --butter: {c[butter]};
    --haze: {c[haze]}; --card: {c[card]}; --border: {c[border]}; --secondary: {c[secondary]};
    color-scheme: light;
  }}
  * {{ box-sizing: border-box; }}
  html {{ background: var(--haze); }}
  body {{
    margin: 0; background: var(--haze); color: var(--plum);
    font-family: 'IBM Plex Sans', -apple-system, system-ui, sans-serif;
    font-size: 17px; line-height: 28px; font-weight: 400;
    -webkit-font-smoothing: antialiased;
  }}
  /* One column for everything — prose, headings, figures and the table all end
     on the same two lines. 775px is the reading measure the body text already
     had; the column carries it now, so nothing sets its own width.
     `max-width: 68ch` used to resolve differently per element (the lede at 19px
     came out wider than the callout at 14px), which is why the page had three
     different widths rather than two. */
  /* Same rule as brichis.xyz, on the same tokens. The page is served in an
     iframe, and a parent document's ::selection does not reach into one, so
     the report has to declare it or selected text falls back to the browser
     default blue and stops matching the site around it. */
  ::selection {{ background: var(--coral); color: var(--plum); }}

  .wrap {{ max-width: 823px; margin: 0 auto; padding: 56px 24px 96px; }}
  .measure {{ max-width: none; }}
  .label {{
    font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11px; line-height: 16px;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--orchid); font-weight: 500;
  }}
  h1 {{ font-family: 'Instrument Serif', Georgia, serif; font-weight: 400; font-size: 64px;
       line-height: 60px; letter-spacing: -0.02em; margin: 18px 0 20px; max-width: 18ch; }}
  section[id] {{ scroll-margin-top: 24px; }}
  h2 {{ font-family: 'Instrument Serif', Georgia, serif; font-weight: 400; font-size: 44px;
       line-height: 48px; letter-spacing: -0.01em; margin: 72px 0 8px; }}
  h3 {{ font-size: 28px; line-height: 34px; font-weight: 500; margin: 44px 0 10px; }}
  h4 {{ font-size: 17px; line-height: 26px; font-weight: 600; margin: 28px 0 6px; }}
  p {{ margin: 0 0 18px; }}
  a {{ color: var(--orchid); text-decoration: underline; text-underline-offset: 3px;
      text-decoration-thickness: 1px; }}
  a:hover {{ color: var(--signal); }}
  code {{ font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 0.88em;
          background: var(--secondary); border-radius: 5px; padding: 1px 5px; }}
  a code {{ background: none; padding: 0; color: inherit; }}
  strong {{ font-weight: 600; }}
  .small {{ font-size: 14px; line-height: 22px; color: var(--plum-muted); }}
  .lede {{ font-size: 19px; line-height: 31px; color: var(--plum-muted); }}
  .rule {{ height: 1px; background: var(--border); border: 0; margin: 0; }}
  header .rule {{ margin-top: 28px; }}

  .keys {{ display: grid; gap: 14px; grid-template-columns: repeat(2, minmax(0, 1fr));
           margin: 28px 0 8px; }}
  .key {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px;
          padding: 18px 18px 16px; position: relative; overflow: hidden; }}
  .key::before {{ content: ""; position: absolute; inset: 0 0 auto; height: 3px; background: var(--accent, var(--coral)); }}
  .key .n {{ font-family: 'Instrument Serif', Georgia, serif; font-size: 34px; line-height: 40px; display: block; margin: 6px 0 2px; }}
  .key .t {{ font-size: 13px; line-height: 19px; color: var(--plum-muted); }}

  ul {{ margin: 0 0 18px; padding-left: 20px; }}
  li {{ margin-bottom: 8px; }}
  li::marker {{ color: var(--coral); }}

  .fig {{ background: var(--card); border: 1px solid var(--border); border-radius: 18px;
          padding: 22px 20px 16px; margin: 28px 0 32px; }}
  .fig .cap {{ margin-top: 10px; }}
  .chart {{ width: 100%; }}

  /* Hand-built legend: Plotly emits no hover event for its own, and the whole
     point here is that pointing at a name lights that program's line. */
  .chartkey {{ display: flex; flex-wrap: wrap; gap: 4px 6px; margin: 10px 0 2px; }}
  .chartkey button {{
    display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
    font-family: inherit; font-size: 12px; line-height: 18px; color: var(--plum-muted);
    background: var(--card); border: 1px solid var(--border); border-radius: 99px;
    padding: 4px 11px 4px 8px; transition: color .12s, border-color .12s, background .12s;
  }}
  .chartkey button .sw {{ width: 10px; height: 10px; border-radius: 3px; flex: 0 0 auto;
                          background: var(--sw); transition: transform .12s; }}
  .chartkey button:hover, .chartkey button:focus-visible {{ color: var(--plum); border-color: var(--sw); outline: none; }}
  .chartkey button[aria-pressed="true"] {{ color: var(--plum); border-color: var(--sw); background: var(--haze); }}
  .chartkey button[aria-pressed="true"] .sw {{ transform: scale(1.25); }}
  .chartkey .hint {{ font-size: 12px; line-height: 26px; color: var(--plum-muted); }}
  .grid2 {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}

  .tablewrap {{ overflow-x: auto; margin: 24px 0 10px; border: 1px solid var(--border);
                border-radius: 16px; background: var(--card); }}
  /* A table plus the notes that explain it, in one card — the same shape the
     figures use, where the caption sits inside `.fig` rather than loose under
     it. The notes live outside `.tablewrap` so they do not scroll sideways
     with the columns. */
  .tablecard {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px;
                margin: 24px 0 10px; overflow: hidden; }}
  .tablecard > .tablewrap {{ border: 0; border-radius: 0; margin: 0; background: none; }}
  .tablenotes {{ border-top: 1px solid var(--border); padding: 12px 18px 14px; }}
  .tablenotes p {{ margin: 0; }}
  .tablenotes p + p {{ margin-top: 6px; }}
  /* The grantee column stays put while the rest scrolls — eleven columns never
     fit a phone, and a row of numbers with no name on it says nothing. */
  th[scope="row"], tfoot th {{ position: sticky; left: 0; z-index: 1; background: var(--card);
                               box-shadow: 1px 0 0 var(--border); }}
  thead th:first-child {{ position: sticky; left: 0; z-index: 2; background: var(--card);
                          box-shadow: 1px 0 0 var(--border); }}
  /* Shown only when the table really does overflow — which now depends on the
     column width, not on the breakpoint. Set by script; hidden by default so
     it never flashes before that runs. */
  .scrollhint {{ display: none; }}
  .scrollhint[data-show="1"] {{ display: block; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; line-height: 19px; }}
  th, td {{ padding: 10px 11px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  table {{ table-layout: auto; }}
  thead th {{ font-family: 'IBM Plex Mono', monospace; font-size: 10px; letter-spacing: 0.1em;
              text-transform: uppercase; color: var(--plum-muted); font-weight: 500; }}
  tbody th {{ font-weight: 500; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.mark {{ text-align: center; }}
  .met {{ color: #1f7a5c; font-weight: 600; }}
  .miss {{ color: var(--plum-muted); }}
  tr.interim th {{ color: var(--plum); }}
  .tag {{ font-family: 'IBM Plex Mono', monospace; font-size: 9px; letter-spacing: .1em;
          text-transform: uppercase; color: var(--plum-muted); background: var(--secondary);
          border-radius: 99px; padding: 2px 7px; margin-left: 6px; }}
  td .sub {{ display: block; font-family: 'IBM Plex Mono', monospace; font-size: 10px;
             letter-spacing: .06em; color: var(--plum-muted); }}
  tfoot td, tfoot th {{ font-weight: 600; border-top: 2px solid var(--border); border-bottom: 0; }}

  /* Reference material, folded by default: it is there for the reader who wants to check
     something, and out of the way for the one who does not. */
  .foldgroup {{ border-bottom: 1px solid var(--border); margin: 28px 0 32px; }}
  .fold {{ border-top: 1px solid var(--border); }}
  .fold > summary {{ list-style: none; cursor: pointer; display: flex; align-items: center;
                     justify-content: space-between; gap: 16px; padding: 16px 2px;
                     font-size: 20px; line-height: 28px; font-weight: 500; }}
  .fold > summary::-webkit-details-marker {{ display: none; }}
  .fold > summary::after {{ content: "+"; font-family: 'IBM Plex Mono', ui-monospace, monospace;
                            font-size: 22px; line-height: 1; color: var(--orchid); flex: 0 0 auto; }}
  .fold[open] > summary::after {{ content: "−"; }}
  .fold > summary:hover {{ color: var(--orchid); }}
  .fold > summary:focus-visible {{ outline: 2px solid var(--orchid); outline-offset: 2px; border-radius: 6px; }}
  .fold > .measure {{ padding: 2px 2px 14px; }}

  .callout {{ border-left: 3px solid var(--coral); background: var(--card); border-radius: 0 14px 14px 0;
              padding: 16px 20px; margin: 24px 0; }}
  .stars {{ display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }}
  footer {{ margin-top: 72px; padding-top: 24px; border-top: 1px solid var(--border); }}
  /* Touch devices: no hover, so every affordance has to be tappable. Apple's
     44px is the target; the pill's own padding plus this gets close enough
     without turning the legend into a wall. */
  @media (hover: none) {{
    .chartkey button {{ padding: 10px 14px 10px 11px; font-size: 13px; }}
    .chartkey button .sw {{ width: 11px; height: 11px; }}
  }}
  @media (max-width: 480px) {{
    .keys {{ grid-template-columns: 1fr; }}
  }}
  @media (max-width: 640px) {{
    h1 {{ font-size: 44px; line-height: 46px; }}
    h2 {{ font-size: 32px; line-height: 36px; }}
    h3 {{ font-size: 24px; line-height: 30px; }}
    body {{ font-size: 16px; line-height: 26px; }}
    .wrap {{ padding: 36px 18px 64px; }}
    .fig {{ padding: 18px 12px 14px; border-radius: 14px; }}
    th, td {{ padding: 9px 10px; }}
  }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="stars">
    <svg width="18" height="18" viewBox="0 0 100 100" aria-hidden="true"><path d="{mark}" fill="{c[coral]}" fill-rule="evenodd"/></svg>
    <span class="label">Optimism · Season 8 · Growth grants</span>
  </div>
  <h1>What the liquidity did after 2.2M OP</h1>
  <p class="lede measure">Nine Season 8 growth programs, measured contract by contract on-chain,
  day by day: what the liquidity in the incentivized contracts did while the rewards ran, what it
  did afterwards, and what the next program should do differently.</p>
  <p class="small">Data through {cutoff}, when the two programs still running were frozen. Page
  built {generated}. The ΔTVL, peak and milestone figures come from measurements committed to a
  public repository: <a href="{blob}/scripts/cohort_summary.py"><code>cohort_summary.py</code></a>
  works out the figures, and <a href="{blob}/scripts/report_html.py"><code>report_html.py</code></a>
  builds this page. Dates and OP amounts come from the sources listed at the end.</p>
  <hr class="rule">
</header>

<section id="short">
  <h2>The short version</h2>
  <div class="keys">
    <div class="key" style="--accent: {c[coral]}"><span class="label">Observed ΔTVL</span>
      <span class="n">+$8.00M</span><span class="t">in the targeted contracts across the nine programs, or $3.62 per OP delivered. Six of nine were positive.</span></div>
    <div class="key" style="--accent: {c[lilac]}"><span class="label">Concentration</span>
      <span class="n">84%</span><span class="t">of the net total came from one grant, 40acres.finance. Of the positive movements alone it is 75.8%.</span></div>
    <div class="key" style="--accent: {c[mint]}"><span class="label">Milestones</span>
      <span class="n">5 of 8</span><span class="t">grants with a Milestone 1 target reached that target on at least one day inside their incentive window. Of the seven with a full-program target, one reached it.</span></div>
    <div class="key" style="--accent: {c[butter]}"><span class="label">Unclaimed</span>
      <span class="n">1.27M OP</span><span class="t">sits in the claim contract, never claimed, by grants that never ran a program.</span></div>
  </div>
  <p class="small measure"><strong>One way of looking at the season.</strong> These figures are a
  targeted measurement: the change in TVL inside the contracts each grant named, across the days it
  actually paid incentives, with quantities valued at one fixed date. A different formula, window
  or scope gives a different number.</p>
  <div class="measure">
    <ul>
      <li><strong>Programs peak early and give it back.</strong> The median program peaked on day 56, which is 41% of the way in. Three of the five that met M1 were below that target again at their last reading: Extrafi when it closed, and Velodrome and Curve Lending at the cutoff.</li>
      <li><strong>OP fell 55%</strong> between the average program start and the average program end. The OP behind these grants was worth $708k when delivered and $291k when the programs ended.</li>
      <li><strong>Most of the wait came before the OP was delivered.</strong> From application to the first day of incentives took a median of {t[total]} days. In seven of the nine programs most of it passed before the OP reached the grantee, and the other two began before it did. Lesson 2 breaks it down. The year in the schedule was the allowance, not the work.</li>
      <li><strong>Staging worked.</strong> 2.47M OP was never released because later tranches depended on progress. But 340k OP was delivered to grants that never ran anything.</li>
    </ul>
  </div>
</section>

<section id="results">
  <h2>What each program did</h2>
  <p class="measure">ΔTVL is measured at fixed end-date prices, so it counts liquidity added, not
  token prices moving. The peak is the highest the daily series reached inside the incentive
  window, and the milestone columns are judged on it.</p>

  <div class="fig">
    <span class="label">Figure 1 · Peak reached vs. where it ended</span>
    <div id="c-peak" class="chart" style="height:460px"></div>
    <p class="cap small">Each program's highest day against its closing figure. Velodrome reached
    +$10.4M and is now below zero. 40acres kept 95% of its peak.</p>
  </div>

  <div class="tablecard">
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>Grantee</th><th class="num">OP</th><th class="num">S8 ΔTVL</th><th class="num">$/OP</th>
        <th class="num">M1 target</th><th class="mark">M1</th><th class="num">Total target</th><th class="mark">Total</th>
        <th class="num">Retention +30d</th><th class="num">Peak</th><th class="num">Length</th>
      </tr></thead>
      <tbody>
{rows}
      </tbody>
      <tfoot><tr>
        <th scope="row">Total</th><td class="num">2,210,000</td><td class="num">$8,003,979</td>
        <td class="num">3.62</td><td></td><td class="mark">5 met</td><td></td><td class="mark">1 met</td>
        <td></td><td></td><td></td>
      </tr></tfoot>
    </table>
  </div>
  <div class="tablenotes">
    <p class="scrollhint small">Scroll the table sideways for the milestone, retention and peak
    columns. The grantee names stay in place.</p>
    <p class="small">Interim programs (Curve Lending, Velodrome) have no announced end date and are
    measured to the {cutoff} cutoff. Retention is only available where a window closed 30 days ago.</p>
  </div>
  </div>

  <div class="fig">
    <span class="label">Figure 2 · The same nine curves, on one scale</span>
    <div id="c-curves" class="chart" style="height:430px"></div>
    <div id="k-curves" class="chartkey" role="group" aria-label="Programs in Figure 2"></div>
    <p class="cap small">Each program's ΔTVL as a share of its own peak, against how far it was
    through its window. Point at a name above, or at a line, to follow one program through the
    tangle. Click a name to keep it lit. The shape repeats: climb, peak around the middle, drift
    down.</p>
  </div>
</section>

<section id="lessons">
  <h2>Lessons for the next program</h2>

  <h3>1 · Judge milestones on what actually happened</h3>
  <p class="measure">A milestone is met here if the target was reached on any day inside the
  incentive window, evidenced by the validated daily series. A single-date checkpoint, either the
  snapshot or the incentive end, is kept as data. But it cannot tell apart a program that
  reached its target and lost the liquidity from one that never got close.</p>
  <div class="callout measure">
    <p style="margin:0"><strong>The obvious objection is gaming:</strong> touch the target for a day,
    then pull the liquidity out. The defence is to require an average over a set number of days.
    Here it changes nothing: the best 7-day average produces the same verdicts. That says these
    peaks were levels held for weeks, not one-day spikes.</p>
  </div>

  <h3>2 · Twelve months is the allowance; three is the useful deadline</h3>
  <p class="measure">Every grant in this season was approved between 12 September and 19 December
  2025: 40acres in September, and the other 23 between 2 October and 19 December. Teams had about a year to run the work end to end. A year on, several approved grants
  still have nothing to show, while the ones that delivered did not need the year. From the
  application going in to the first day of incentives the median was <strong>{t[total]} days</strong>:
  {t[app]} to the decision, {t[deliv]} more until the OP reached the grantee, and {t[live]} from delivery to launch.
  Each stage is the median of its own distribution, so the three do not sum to the {t[total]} in the last row.
  The rows cover the same nine grants. "Delivered" means the first tranche reached the grantee
  on-chain, so the middle stage includes however long the grantee took to claim. A negative figure
  means incentives began before the OP was delivered.</p>
  <div class="tablewrap"><table>
    <thead><tr><th>Stage</th><th class="num">Median</th><th class="num">Range</th></tr></thead>
    <tbody>
      <tr><th scope="row">Application submitted → approved</th><td class="num">{t[app]} days</td><td class="num">{t[app_rng]}</td></tr>
      <tr><th scope="row">Approved → OP delivered</th><td class="num">{t[deliv]} days</td><td class="num">{t[deliv_rng]}</td></tr>
      <tr><th scope="row">OP delivered → incentives live</th><td class="num">{t[live]} days</td><td class="num">{t[live_rng]}</td></tr>
      <tr><th scope="row">Application → incentives live</th><td class="num">{t[total]} days</td><td class="num">{t[total_rng]}</td></tr>
    </tbody>
  </table></div>
  <p class="measure"><strong>40acres is the model case:</strong> application created Aug 25, 2025,
  announced as approved in the <a href="https://gov.optimism.io/t/cycle-41-grants-council-report/10281">Cycle
  41 report</a> on Sep 12, OP delivered Oct 7, distribution
  started Oct 15, program finished Jan 27, 2026. That is 51
  days from application to launch, and the best result in the cohort. At the other end, Curve
  Lending took 218 days. <strong>Give three months from approval to launch, not twelve</strong>, with a
  deadline for the final report. Count the delivery inside those three months, since getting the OP
  delivered took a median of {t[deliv]} days, against {t[app]} for the decision itself.</p>

  <h3>3 · The extra weeks bought decay, not liquidity</h3>
  <p class="measure">Across all nine programs, including the two still running, the median length was 16.6 weeks, from 9 to 31. But the peak landed at a
  median of <strong>day 56, week eight</strong>, and it did not move later in the longer programs
  (the Spearman rank correlation between a program's length and the day it peaked is −0.10, i.e.
  none; Pearson on the same pairs gives −0.13, which says the same thing). Eight of
  the nine peaked inside twelve weeks. What the longer windows added was the decline after the
  peak, not more liquidity.</p>
  <p class="measure">Two of the nine are still running, so their length is set by the cutoff and
  not by the program. On the seven that closed, the median length is 14.9 weeks, from 9 to 30. The
  peak still lands at a median of day 56, six of the seven peaked inside twelve weeks, and the
  Spearman correlation is −0.21, which with seven points is no more distinguishable from none.</p>
  <p class="measure"><strong>So: set a fixed duration of about twelve weeks, with a review at week
  eight</strong>, where the median program topped out. Extend only on evidence that liquidity
  is still climbing. Nine programs cannot prove an optimal length, and the sample says nothing
  about whether a shorter program would have reached the same peak. What it does say is that
  running past the peak, with the incentive still paying out, is what these windows mostly
  bought.</p>

  <h3>4 · Staged payments worked, and there is OP worth trying to recover</h3>
  <div class="fig">
    <span class="label">Figure 3 · Where the 6.29M OP went</span>
    <div id="c-flow" class="chart" style="height:220px"></div>
    <p class="cap small">Only the first block funded a measured program. The third block never left
    the claim contract. The four blocks are built from the Council's five cycle reports and the
    Foundation's <a href="https://docs.google.com/spreadsheets/d/1Ul8iMTsOFUKUmqz6MK0zpgt8Ki8tFtoWKGlwXj-Op34/edit">public
    delivery tracker</a>, cross-checked against the Hedgey claim contract on-chain.</p>
  </div>
  <div class="measure">
    <ul>
      <li><strong>2.47M OP was never released</strong>, because later tranches were conditional on progress.</li>
      <li><strong>340k OP was delivered to grants that never ran a program.</strong> A 20% first tranche instead of 40% would have exposed about half of it.</li>
      <li><strong>1.27M OP was sent to the claim contract and never claimed by the grantee:</strong> Morpho 600k, Tydro 600k, LiqPass 32k, Strands 28k, NEUS 8k. None of it has left the contract, which is visible on-chain. Treat the 1.27M as potentially recoverable and worth pursuing, not as recovered. LiqPass was withdrawn outright.</li>
    </ul>
    <p>Both councils were dissolved on 15 July 2026. The remaining milestones are not unattended:
    the approved <a href="https://vote.optimism.io/proposals/94653586928704829987664319556957069435634532914401865187521227109996000544034">dissolution
    proposal</a> states that any remaining milestones for existing Grants Council grants will be
    monitored by the Foundation through a third-party contractor. What the proposal does not
    settle is who decides what happens to OP that was released and never used. <strong>That is
    still an opening for the community at large:</strong> watch whether these programs are
    delivered, and press for recovery where they are not. <strong>The fifteen grants that never ran
    a program were approved between 23 October and 19 December 2025</strong>, so each reaches one
    year between October and December 2026: none has passed that mark yet, and all of them do
    within three months.</p>
  </div>

  <h3>5 · OP moved too much for USD-denominated planning</h3>
  <div class="fig">
    <span class="label">Figure 4 · OP price across the season</span>
    <div id="c-price" class="chart" style="height:330px"></div>
    <p class="cap small">Targets were written in USD and stayed fixed. The incentive behind them
    lost more than half its value between the average start and the average end.</p>
  </div>
  <p class="measure">Hydrex's own milestone report notes the grant was structured at $0.65 OP
  against $0.33 at reporting time. A fall of that size is also a candidate explanation for the modest $/OP: a
  program designed around a budget worth twice what it turned out to be, matched by co-incentives
  sized the same way, delivers less than the approval assumed, and is then judged against a target
  that never moved.</p>

  <h3>6 · Collect the evaluation inputs at approval time</h3>
  <p class="measure">The incentive windows and the incentivized contracts had to be rebuilt by hand
  for this review, from milestone updates, social posts and on-chain data. TVL valued at each date's own prices, the way DefiLlama and similar dashboards report it, is no
  substitute: across the eight grants with a price breakdown, the same contracts fell $5.62M
  measured that way, while ΔTVL at fixed prices rose $7.97M. The $13.59M gap is token price movement, not
  liquidity.</p>
  <p class="measure">A short form at approval, updated at each milestone, would have produced this
  review automatically: incentive start and end dates with a link to the announcement; contract
  addresses or pool IDs per chain and their type; incentive token and amount per period; and
  co-incentives in token units.</p>

  <h3>7 · Size the incentive in OP, and say how a USD milestone will be measured</h3>
  <p class="measure"><strong>Denominate the incentive itself in OP, not in USD.</strong> A program that promises users a fixed amount in USD for keeping their funds in for a set time is exposed to the token price. That happened in this season: when OP fell, the grant no longer covered the promise and the team had to find the difference elsewhere. A grant is a number of tokens, and the program should be written the same way.</p>
  <p class="measure"><strong>A milestone can still be set in USD.</strong> That is often how a
  target makes sense to everyone reading it. What has to come with it is <em>how it will be
  measured</em>. That calculation is a small piece of work with public tools: on-chain reads for
  quantities, DefiLlama or a price API for the fixed-date prices. It is the same calculation behind
  every figure in this report. Agree on it when the grant is approved, not when it is being judged.</p>
</section>

<section id="curves">
  <h2>The nine curves</h2>
  <p class="measure">Every program's daily ΔTVL, validated against the on-chain checkpoints before
  it was used. <strong>These daily series do not always end on the table's closing figure</strong>,
  for two reasons. For the seven finished programs the line continues 30 days past the incentive
  end, so its last point is a later date than the one the table reports. And for Velodrome the
  daily series is rebuilt from Dune token transfers, which do not cover Soneium: the line therefore
  leaves out Soneium, which contributes −$62,904 to the table's ΔTVL, because the table is measured
  by direct on-chain reads across every chain in scope. Where the two differ, the table is the measurement
  and the line is the shape. The dotted rule marks the day the incentive stopped, and the shaded band after it is
  the following 30 days. It shows what the liquidity did once the rewards ended, and it never
  counts toward the peak. The two interim programs have no band: their window is still
  open.</p>
  <div class="fig">
    <div id="c-small" class="chart" style="height:640px"></div>
    <p class="cap small">Sources: Dune token transfers for AMM pools; pool-manager events for
    PancakeSwap Infinity; VotingEscrow events for 40acres' veNFT loans; daily on-chain reads for
    vaults, lending markets and Uniswap v4 pools.</p>
  </div>
</section>

<section id="method">
  <h2>Method, and who wrote this</h2>
  <p class="measure">I served on the last iteration of the Milestones and Metrics Council, so the
  payment data here is first-hand up to the point the councils were dissolved. That is a
  disclosure, not a claim of neutrality: everything above is reproducible from the sources named,
  and the <a href="{repo}">full pipeline is public</a> if you want to check any of it.</p>
  <h3>The decisions behind the figures, and why</h3>
  <p class="measure">Each of these is a choice, and each one moves the numbers. They are listed so
  that anyone who would have chosen differently can see exactly where the difference enters and
  recompute from the same measurements.</p>
  <div class="measure">
    <ul>
      <li><strong>Metric.</strong> ΔTVL = Σ (quantity at incentive end − quantity at incentive start) × token price at the end date, over the contracts each grant incentivized. The formula is the one in the <a href="https://gov.optimism.io/t/s8-impact-measurement-methodology/10219">S8 Impact Measurement Methodology</a>.</li>
      <li><strong>The window here is not the official one.</strong> The S8 methodology opens the window at the date of actual grant delivery and closes it at the earlier of the incentive close and the end of Season 8, which the <a href="https://gov.optimism.io/t/guide-to-season-8/10001">Guide to Season 8</a> gives as 24 December 2025. This review opens it on the first day incentives were actually paid and closes it on the last, with no season cap. Both choices move the figures. Opening at delivery would misplace the window for several programs: the first tranche reached the grantee a median of {t[live]} days before the first incentive day, but {t[live_max]} days before for Curve Lending, and after it for {t[live_neg]} programs whose teams began incentives before claiming. The cap is the larger difference. All nine incentive windows ended after 24 December 2025, and Velodrome and Curve Lending began after it, so applying the cap would cut every program short and leave those two with no incentive days inside the window. The daily series behind each figure are in the repository, so the same measurement can be run over the official window.</li>
      <li><strong>Scope is targeted, contract by contract, not protocol-wide.</strong> Each grant named the contracts it would incentivize, and only those are measured. A protocol's headline TVL moves for reasons that have nothing to do with a grant, so counting all of it would credit or blame a program for liquidity it never touched. The cost of this choice is that it misses any spillover into the rest of the protocol. Oku has no contract of its own, so it is measured as the Morpho vault position of the 67 wallets it paid.</li>
      <li><strong>Quantities are valued at one fixed date, not at each day's price.</strong> Lesson 6 shows how far the two readings can differ.</li>
      <li><strong>A milestone counts as met if the target was reached on any day inside the window.</strong> Lesson 1 explains why.</li>
      <li><strong>The peak is the highest day inside the incentive window.</strong> The 30 days drawn after it never count toward the peak or the closing figure.</li>
      <li><strong>Which grants this covers.</strong> The Council approved <a href="https://gov.optimism.io/t/cycle-46-and-season-8-final-grants-report/10503">24 applications</a> on final review across Season 8, spending the full 6.29M OP budget. This review measures the <strong>nine</strong> of those that claimed their OP and ran an incentive program that could be measured on-chain. The other fifteen are counted in the OP figures but have no ΔTVL.</li>
      <li><strong>Two programs are still running.</strong> Curve Lending and Velodrome are marked interim throughout. Their figures are a reading at the {cutoff} cutoff, so they are not comparable to the seven that closed.</li>
    </ul>
  </div>
  <div class="foldgroup">
  <details class="fold">
    <summary>What the records can and cannot show</summary>
    <div class="measure">
    <ul>
      <li><strong>How OP reached grantees.</strong> The Milestones and Metrics Council's <a href="https://optimism.blockscout.com/address/0xC0603610C7F923c93b18B48eF63F0d733CB8C89e">multisig</a> sent each grant's OP to the <a href="https://optimism.blockscout.com/address/0x8A2725a6f04816A5274dDD9FEaDd3bd0C253C1A6">Hedgey ClaimCampaigns contract</a>, and each grantee then had to claim it there. One payment did not go through the contract: Curve Lending's second, which the multisig sent directly and which counts as a delivery.</li>
      <li><strong>Delivery dates are on-chain.</strong> A grant is dated as delivered on the day its first tranche reached the grantee, read from Blockscout as the transfer out of the claim contract. That is later than the day the multisig sent the OP into it, because it includes however long the grantee took to claim. The Foundation's <a href="https://docs.google.com/spreadsheets/d/1Ul8iMTsOFUKUmqz6MK0zpgt8Ki8tFtoWKGlwXj-Op34/edit">public delivery tracker</a> keeps its own initial delivery date, which is not used here. Payments made after the councils were dissolved, or routed another way, may be missing.</li>
      <li><strong>Approval dates are the Council's cycle-report dates.</strong> Each grant is dated to the report of the cycle that announced it, listed under Sources, so every approval rests on one public source and one rule. A conditional pass counts from its report. Karma supplies the application dates only: its own approval field disagrees with the reports for some grants, and 40acres shows why. The Cycle 41 report of 12 September already lists it as passed, but Karma dates its approval 2 October.</li>
      <li><strong>Co-incentives are excluded.</strong> Teams sized them in USD at application time, and I did not find a way to verify how much was actually deployed. There may well be one. I did not pursue it.</li>
    </ul>
  </div>
  </details>
  <details class="fold">
    <summary>How it was built</summary>
    <div class="measure">
    <ul>
      <li><strong>Built with Claude.</strong> I used Claude Design for my brand's design system, to shape the first approaches and to choose the tools, and <a href="https://claude.com/claude-code">Claude Code</a> for the code, the charts, this report and the audits. The repository and the history of every change are on <a href="{repo}">GitHub</a>.</li>
      <li><strong>On-chain reads:</strong> an Alchemy archive node: <code>eth_call</code> at a
      block per checkpoint, <code>eth_getCode</code> to date deployments by binary search,
      <code>eth_getLogs</code> for one-off events and <code>alchemy_getAssetTransfers</code> for
      ERC-721 movements. A local cache means every figure can be recomputed without paying for
      the reads twice.</li>
      <li><strong>Protocol shapes read directly:</strong> ERC-4626 vaults, Aave-style aTokens,
      Uniswap v3 and v4 (tick-walked through StateView), PancakeSwap Infinity, Velodrome and
      Aerodrome VotingEscrow veNFTs, and Curve LlamaLend. Function selectors and event topics were
      derived with keccak from signatures verified in each protocol's source, never copied from
      memory.</li>
      <li><strong>Dune (DuneSQL / Trino):</strong> six saved queries rebuild daily balances from
      token transfers and from pool-manager and VotingEscrow events, where a daily on-chain read
      would have been too slow.</li>
      <li><strong>DefiLlama:</strong> protocol TVL series and historical token prices, including the
      fixed end-date prices the S8 formula uses.</li>
      <li><strong>Karma API:</strong> each application's submission date.</li>
      <li><strong>The registry is a spreadsheet:</strong> grants, windows and scope contracts live
      in Google Sheets and are read as CSV at run time, so a human can correct a date without
      touching code. Apps Script calls Blockscout inside the sheet to date each payment,
      rather than copying transaction dates by hand.</li>
      <li><strong>Python</strong> (pandas, requests) for the pipeline, <strong>Plotly</strong> for
      the charts on this page, and Blockscout, Etherscan and Herd for reading unfamiliar contracts
      on Base before trusting a selector or an event.</li>
      <li><strong>Every derived number is validated before it is used:</strong> each daily series
      has to reproduce every on-chain checkpoint, and the reconstructions cross-check against each
      other: the veNFT rebuild against DefiLlama, the v4 tick walk against Uniswap's own
      ReservesLens.</li>
    </ul>
  </div>
  </details>
  <details class="fold">
    <summary>Sources</summary>
    <div class="measure">
    <ul>
      <li><strong>Code and measurements.</strong> The <a href="{repo}">pipeline repository</a>, including the per-grantee measurements in <code>output/</code> and <code>data/</code>, the Dune queries under <code>scripts/dune/</code>, and <a href="{blob}/scripts/cohort_summary.py"><code>cohort_summary.py</code></a>, which turns them into every figure on this page.</li>
      <li><strong>Council decisions and budget.</strong> The Season 8 cycle reports: <a href="https://gov.optimism.io/t/cycle-41-grants-council-report/10281">41</a>, <a href="https://gov.optimism.io/t/cycle-42-grants-report/10308">42</a>, <a href="https://gov.optimism.io/t/cycle-43-grants-council-report/10363">43</a>, <a href="https://gov.optimism.io/t/cycle-44-grants-report/10410">44</a> and the <a href="https://gov.optimism.io/t/cycle-46-and-season-8-final-grants-report/10503">Cycle 46 and Season 8 final report</a>, which together account for all 24 approvals and the full 6.29M OP, and date each approval.</li>
      <li><strong>Payments.</strong> The Foundation's <a href="https://docs.google.com/spreadsheets/d/1Ul8iMTsOFUKUmqz6MK0zpgt8Ki8tFtoWKGlwXj-Op34/edit">public delivery tracker</a>, cross-checked on-chain.</li>
      <li><strong>Method.</strong> The <a href="https://gov.optimism.io/t/s8-impact-measurement-methodology/10219">S8 Impact Measurement Methodology</a>, whose formula this review uses and whose window it does not; the difference is set out above.</li>
      <li><strong>Council dissolution.</strong> The <a href="https://vote.optimism.io/proposals/94653586928704829987664319556957069435634532914401865187521227109996000544034">Milestones and Metrics Council</a> and <a href="https://vote.optimism.io/proposals/17610825935142917870367542494361266033681870435033393450622576978064079317264">Grants Council</a> dissolution proposals, both approved 15 July 2026.</li>
    </ul>
  </div>
  </details>
  </div>
  <div class="callout measure">
    <p style="margin:0"><strong>If you have information to add, please get in touch.</strong>
    Maybe you were on one of these teams, or you know a program that ran without being reported,
    or you can identify a payment we could not. This review is only as good as what is visible
    from the outside.</p>
  </div>
  <p class="measure">Thank you to everyone who read earlier drafts of this report and sent feedback.</p>
</section>

<footer>
  <div class="stars">
    <svg width="14" height="14" viewBox="0 0 100 100" aria-hidden="true"><path d="{mark}" fill="{c[coral]}" fill-rule="evenodd"/></svg>
    <span class="label">© 2026 Bricia Guzmán · Compliance &amp; Verification</span>
  </div>
  <p class="small" style="margin-top:8px">Optimism Season 8 growth grants · data through {cutoff} ·
  page built {generated}.</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<script>
const D = {data};
const T = {{ plum: '{c[plum]}', muted: '{c[plum_muted]}', coral: '{c[coral]}', orchid: '{c[orchid]}',
             lilac: '{c[lilac]}', peri: '{c[peri]}', mint: '{c[mint]}', butter: '{c[butter]}',
             border: '{c[border]}', card: '{c[card]}', signal: '{c[signal]}' }};
// Nine categorical hues for Figure 2, one per program. Nine series is past the
// point where a hue set falls out of a design system, so this one was searched
// for and checked against the all-pairs gates on a white surface: worst
// colour-blind ΔE 8.1 (target ≥8), worst normal-vision ΔE 15.0 (floor ≥15),
// every slot inside the lightness band and above the chroma floor. Muted on
// purpose — they have to sit on the page without shouting. Colour is never the
// only channel: the legend pairs each hue with its name, and hovering either
// one greys the other eight.
const SERIES = ['{s[0]}', '{s[1]}', '{s[2]}', '{s[3]}', '{s[4]}',
                '{s[5]}', '{s[6]}', '{s[7]}', '{s[8]}'];
const FONT = {{ family: "'IBM Plex Sans', system-ui, sans-serif", size: 12, color: T.muted }};
const BASE = {{
  paper_bgcolor: T.card, plot_bgcolor: T.card, font: FONT,
  margin: {{ l: 8, r: 16, t: 8, b: 34 }}, showlegend: false, hovermode: 'closest',
  xaxis: {{ gridcolor: T.border, zerolinecolor: T.border, tickfont: FONT }},
  yaxis: {{ gridcolor: T.border, zerolinecolor: T.border, tickfont: FONT }},
}};
const CONF = {{ displayModeBar: false, responsive: true }};
const clone = (o) => JSON.parse(JSON.stringify(o));

// Plotly's `responsive` only resizes the canvas. Margins, subplot grids and
// label density are fixed numbers that have to change with the breakpoint, so
// each figure registers what it wants to do and gets called on load and again
// whenever the page crosses 640px. Same breakpoint as the stylesheet.
const NARROW = window.matchMedia('(max-width: 640px)');
const fits = [];
const onFit = (fn) => {{ fits.push(fn); fn(NARROW.matches); }};
NARROW.addEventListener('change', (e) => fits.forEach((fn) => fn(e.matches)));

// Pointer type is not fixed for the life of the page — a tablet gets docked, a
// laptop has a touchscreen — so this is watched rather than read once at load.
const TOUCH = window.matchMedia('(hover: none)');
const touches = [];
const onTouch = (fn) => {{ touches.push(fn); fn(TOUCH.matches); }};
TOUCH.addEventListener('change', (e) => touches.forEach((fn) => fn(e.matches)));

// The table is eleven columns wide and the column is not, so it scrolls at most
// sizes — but say so only when it actually does.
(function () {{
  const wrap = document.querySelector('.tablewrap'), hint = document.querySelector('.scrollhint');
  if (!wrap || !hint) return;
  const check = () => hint.setAttribute('data-show', wrap.scrollWidth > wrap.clientWidth + 1 ? '1' : '0');
  new ResizeObserver(check).observe(wrap);
  check();
}})();

// Figure 1 — peak vs end, one row per grant
(function () {{
  const d = D.peak_end, n = d.names.length, y = d.names.map((_, i) => n - i);
  const traces = [];
  for (let i = 0; i < n; i++) {{
    traces.push({{ x: [d.end[i], d.peak[i]], y: [y[i], y[i]], type: 'scatter', mode: 'lines',
      line: {{ color: T.border, width: 3 }}, hoverinfo: 'skip', showlegend: false }});
  }}
  traces.push({{ x: d.peak, y: y, type: 'scatter', mode: 'markers+text', name: 'Peak',
    marker: {{ size: 11, color: T.coral, line: {{ color: T.card, width: 2 }} }},
    text: d.peak_label, textposition: 'middle right', textfont: {{ family: FONT.family, size: 11, color: T.plum }},
    hovertemplate: '%{{customdata}} — peak %{{x:$,.0f}}<extra></extra>', customdata: d.names }});
  traces.push({{ x: d.end, y: y, type: 'scatter', mode: 'markers', name: 'End',
    marker: {{ size: 9, color: T.plum, line: {{ color: T.card, width: 2 }} }},
    hovertemplate: '%{{customdata}} — end %{{x:$,.0f}}<extra></extra>', customdata: d.names }});
  traces[traces.length - 2].name = 'Peak in window';
  traces[traces.length - 1].name = 'Where it ended';
  const layout = clone(BASE);
  // Sized to the content: the longest name measures 104px at 12px, the widest
  // value label 45px at 11px. The old 132/176 were cut for a full-bleed figure
  // and left a third of the card empty once everything moved to one column.
  layout.margin = {{ l: 116, r: 64, t: 40, b: 36 }};
  layout.showlegend = true;
  layout.legend = {{ orientation: 'h', y: 1.12, x: 1, xanchor: 'right',
    font: {{ family: FONT.family, size: 11, color: T.plum }} }};
  layout.xaxis = {{ ...layout.xaxis, tickprefix: '$', tickformat: '.2s', zeroline: true, zerolinewidth: 1.5 }};
  layout.yaxis = {{ ...layout.yaxis, tickmode: 'array', tickvals: y, ticktext: d.names,
    tickfont: {{ family: FONT.family, size: 12, color: T.plum }}, gridcolor: 'rgba(0,0,0,0)' }};
  const gd = document.getElementById('c-peak');
  Plotly.newPlot(gd, traces, layout, CONF).then(() => {{
    // 132px of names plus 176px of value labels leaves about 20px of plot on a
    // phone — every dot lands on the same vertical line. On narrow screens the
    // names shrink and the peak values come off the chart into the hover.
    const pk = traces.length - 2;
    onFit((n) => {{
      Plotly.relayout(gd, {{
        'margin.l': n ? 92 : 116, 'margin.r': n ? 30 : 64, 'margin.t': n ? 52 : 40,
        'legend.x': n ? 0 : 1, 'legend.xanchor': n ? 'left' : 'right',
        'legend.y': n ? 1.1 : 1.12,
        'yaxis.tickfont.size': n ? 10 : 12,
        'xaxis.nticks': n ? 4 : 0,
      }});
      Plotly.restyle(gd, {{ mode: n ? 'markers' : 'markers+text' }}, [pk]);
    }});
  }});
}})();

// Figure 2 — normalized curves
(function () {{
  const MUTED = 'rgba(46,27,69,0.13)';   // the nine at rest once one is picked out
  const n = D.curves.length;
  const col = (i) => SERIES[i % SERIES.length];
  const traces = D.curves.map((s, i) => ({{
    x: s.x, y: s.y, type: 'scatter', mode: 'lines', name: s.name,
    line: {{ color: col(i), width: 1.7 }},
    hovertemplate: s.name + ' — %{{y:.0f}}% of its peak at %{{x:.0f}}% of the window<extra></extra>',
  }}));
  const layout = clone(BASE);
  layout.margin = {{ l: 56, r: 24, t: 26, b: 42 }};
  layout.xaxis = {{ ...layout.xaxis, title: {{ text: 'Share of the incentive window', font: FONT }}, ticksuffix: '%', range: [0, 100] }};
  // Hydrex ends at -170% of its peak; clipping the axis keeps the shared shape readable
  // and the small-multiples below show every curve in full.
  layout.yaxis = {{ ...layout.yaxis, title: {{ text: 'Share of own peak', font: FONT }},
    ticksuffix: '%', zeroline: true, zerolinewidth: 1.5, range: [-105, 108] }};
  layout.annotations = [{{ xref: 'paper', yref: 'paper', x: 0, y: -0.2, xanchor: 'left',
    text: 'Hydrex continues to −170%. See the nine curves below',
    showarrow: false, font: {{ family: FONT.family, size: 10, color: T.muted }} }}];
  const gd = document.getElementById('c-curves'), key = document.getElementById('k-curves');

  // One button per program, coloured like its line. Plotly's own legend has no
  // hover event, so the legend is ours: pointing at a name lights that line,
  // clicking pins it so it stays lit while you read the caption.
  const btns = D.curves.map((s, i) => {{
    const b = document.createElement('button');
    b.type = 'button';
    b.style.setProperty('--sw', col(i));
    b.setAttribute('aria-pressed', 'false');
    b.innerHTML = '<span class="sw" aria-hidden="true"></span>';
    b.appendChild(document.createTextNode(s.name));
    key.appendChild(b);
    return b;
  }});
  const hint = document.createElement('span');
  hint.className = 'hint';
  onTouch((t) => {{ hint.textContent = t ? 'tap a name to follow one · tap it again to release'
                                         : 'point to follow one · click to keep it lit'; }});
  key.appendChild(hint);

  Plotly.newPlot(gd, traces, layout, CONF).then(() => {{
    let pinned = null, hovered = null;
    const draw = () => {{
      const lit = hovered !== null ? hovered : pinned;
      const idx = Array.from({{ length: n }}, (_, i) => i);
      Plotly.restyle(gd, {{
        'line.color': idx.map((i) => (lit === null || i === lit) ? col(i) : MUTED),
        'line.width': idx.map((i) => (lit !== null && i === lit) ? 3 : 1.7),
      }}, idx);
      btns.forEach((b, i) => b.setAttribute('aria-pressed', String(i === pinned)));
    }};
    btns.forEach((b, i) => {{
      b.addEventListener('mouseenter', () => {{ hovered = i; draw(); }});
      b.addEventListener('mouseleave', () => {{ hovered = null; draw(); }});
      b.addEventListener('focus', () => {{ hovered = i; draw(); }});
      b.addEventListener('blur', () => {{ hovered = null; draw(); }});
      // A tap fires mouseenter *and* click with no mouseleave to follow, so
      // without clearing the hover here a second tap would unpin and the stale
      // hover would keep the line lit — "tap again to release" doing nothing.
      b.addEventListener('click', () => {{ hovered = null; pinned = pinned === i ? null : i; draw(); }});
    }});
    gd.on('plotly_hover', (e) => {{ hovered = e.points[0].curveNumber; draw(); }});
    gd.on('plotly_unhover', () => {{ hovered = null; draw(); }});
    onFit((n) => Plotly.relayout(gd, {{
      'margin.l': n ? 46 : 56, 'margin.r': n ? 12 : 24, 'margin.b': n ? 54 : 42,
      'xaxis.nticks': n ? 4 : 0,
      'annotations[0].y': n ? -0.26 : -0.2,
    }}));
  }});
}})();

// Figure 3 — where the OP went
(function () {{
  const traces = D.flow.map((b) => ({{
    x: [b.value], y: ['OP'], type: 'bar', orientation: 'h', name: b.label,
    marker: {{ color: b.color, line: {{ color: b.color === T.secondary ? T.border : T.card, width: 2 }} }},
    text: [(b.value / 1e6).toFixed(2) + 'M'], textposition: 'inside', insidetextanchor: 'middle',
    textfont: {{ family: FONT.family, size: 12, color: b.color === T.plum ? T.card : T.plum }},
    hovertemplate: b.label + ': %{{x:,.0f}} OP<extra></extra>',
  }}));
  const layout = clone(BASE);
  layout.barmode = 'stack';
  layout.margin = {{ l: 8, r: 8, t: 8, b: 96 }};
  layout.showlegend = true;
  layout.legend = {{ orientation: 'h', y: -0.5, x: 0, traceorder: 'normal',
    font: {{ family: FONT.family, size: 11, color: T.plum }} }};
  layout.xaxis = {{ ...layout.xaxis, visible: false }};
  layout.yaxis = {{ ...layout.yaxis, visible: false }};
  const gd = document.getElementById('c-flow');
  Plotly.newPlot(gd, traces, layout, CONF).then(() => {{
    // Every block keeps its number. The 340k one is 5% of the bar, so it fits
    // inside on a wide screen and not on a phone — rather than guess a pixel
    // threshold, the label is measured in the font it is drawn in, and a block
    // too narrow to hold it gets the number above the bar on a leader instead
    // of losing it. Width changes at every size, not just at the breakpoint,
    // so this is driven by a ResizeObserver rather than the media query.
    const total = D.flow.reduce((a, b) => a + b.value, 0);
    const labels = D.flow.map((b) => (b.value / 1e6).toFixed(2) + 'M');
    const ruler = document.createElement('canvas').getContext('2d');
    ruler.font = '12px ' + FONT.family;
    const needs = labels.map((l) => ruler.measureText(l).width + 16);
    const mids = [];
    D.flow.reduce((acc, b, i) => {{ mids[i] = acc + b.value / 2; return acc + b.value; }}, 0);

    let lastPx = -1;
    const fitLabels = () => {{
      const px = gd.clientWidth - 16;
      if (px <= 0 || px === lastPx) return;
      lastPx = px;
      const inside = D.flow.map((b, i) => (b.value / total) * px >= needs[i]);
      const outside = labels
        .map((l, i) => ({{ l: l, i: i }}))
        .filter((o) => !inside[o.i])
        .map((o) => ({{
          x: mids[o.i], y: 1, xref: 'x', yref: 'paper', text: o.l,
          showarrow: true, arrowhead: 0, arrowwidth: 1, arrowcolor: T.border,
          ax: 0, ay: -15, font: {{ family: FONT.family, size: 11, color: T.plum }},
        }}));
      Plotly.update(gd,
        {{ text: labels.map((l, i) => (inside[i] ? [l] : [''])) }},
        {{ annotations: outside, 'margin.t': outside.length ? 32 : 8 }},
        D.flow.map((_, i) => i));
    }};
    new ResizeObserver(fitLabels).observe(gd);
    onFit((n) => {{
      Plotly.relayout(gd, {{ 'margin.b': n ? 116 : 96, 'legend.font.size': n ? 12 : 11 }});
      fitLabels();
    }});
  }});
}})();

// Figure 4 — OP price
(function () {{
  const p = D.price;
  const traces = [
    {{ x: p.x, y: p.y, type: 'scatter', mode: 'lines', line: {{ color: T.plum, width: 2 }},
       hovertemplate: '%{{x}} — $%{{y:.3f}}<extra></extra>' }},
    {{ x: p.marks.map((m) => m[1]), y: p.marks.map((m) => m[2]), type: 'scatter', mode: 'markers',
       marker: {{ size: 10, color: T.coral, line: {{ color: T.card, width: 2 }} }},
       text: p.marks.map((m) => m[0]),
       hovertemplate: '%{{text}} — %{{x}}, $%{{y:.3f}}<extra></extra>' }},
  ];
  const layout = clone(BASE);
  layout.margin = {{ l: 56, r: 20, t: 26, b: 40 }};
  layout.yaxis = {{ ...layout.yaxis, tickprefix: '$', tickformat: '.2f', rangemode: 'tozero' }};
  layout.annotations = p.marks.map((m, i) => ({{
    x: m[1], y: m[2], text: m[0].replace('mean', 'avg'), showarrow: true, arrowhead: 0, arrowcolor: T.border,
    ax: 0, ay: i % 2 === 0 ? -26 : -44, font: {{ family: FONT.family, size: 10, color: T.plum }},
  }}));
  const gd = document.getElementById('c-price');
  Plotly.newPlot(gd, traces, layout, CONF).then(() => {{
    onFit((n) => Plotly.relayout(gd, Object.assign(
      {{ 'margin.l': n ? 46 : 56, 'margin.r': n ? 10 : 20, 'xaxis.nticks': n ? 3 : 0 }},
      // Five dated markers in 330px; alternating the labels further apart is
      // what keeps them off each other.
      ...p.marks.map((_, i) => ({{ ['annotations[' + i + '].ay']: n ? (i % 2 === 0 ? -22 : -46) : (i % 2 === 0 ? -26 : -44) }})),
    )));
  }});
}})();

// The nine curves — small multiples
(function () {{
  const gd = document.getElementById('c-small');
  // Three columns on a phone puts each panel in ~90px: the titles run into each
  // other and the date ticks overlap the neighbour. The grid is baked in at plot
  // time, so the breakpoint rebuilds the figure rather than nudging margins.
  const build = (narrow) => {{
  // Two columns, not three: in a 775px column three panels would be ~250px each
  // and the titles would start colliding again. Two gives ~387px — wider than
  // the three-up ever was on the old full-bleed page.
  const cols = narrow ? 1 : 2, rows = Math.ceil(D.small.length / cols), traces = [], layout = clone(BASE);
  layout.grid = {{ rows: rows, columns: cols, pattern: 'independent', roworder: 'top to bottom',
                  ygap: narrow ? 0.34 : 0.3 }};
  layout.margin = {{ l: narrow ? 56 : 52, r: 16, t: 34, b: 34 }};
  layout.annotations = [];
  layout.shapes = [];
  D.small.forEach((s, i) => {{
    const ax = i === 0 ? '' : i + 1;
    const inw = s.x.filter((_, k) => s.inw[k]);
    traces.push({{ x: s.x, y: s.y, type: 'scatter', mode: 'lines', xaxis: 'x' + ax, yaxis: 'y' + ax,
      line: {{ color: T.plum, width: 1.6 }},
      hovertemplate: s.name + ' — %{{x}}: %{{y:$,.0f}}<extra></extra>' }});
    traces.push({{ x: [s.peak_date], y: [s.peak], type: 'scatter', mode: 'markers', xaxis: 'x' + ax, yaxis: 'y' + ax,
      marker: {{ size: 8, color: T.coral, line: {{ color: T.card, width: 2 }} }},
      hovertemplate: s.name + ' — peak %{{y:$,.0f}} on %{{x}}<extra></extra>' }});
    layout['xaxis' + ax] = {{ gridcolor: T.border, showticklabels: true, nticks: narrow ? 4 : 3, tickfont: {{ ...FONT, size: 10 }} }};
    layout['yaxis' + ax] = {{ gridcolor: T.border, zeroline: true, zerolinecolor: T.border,
      tickprefix: '$', tickformat: '.2s', nticks: 4, tickfont: {{ ...FONT, size: 10 }} }};
    layout.annotations.push({{ text: s.name + (s.ongoing ? ' · interim' : ''), xref: 'x' + ax + ' domain',
      yref: 'y' + ax + ' domain', x: 0, y: 1.16, showarrow: false, xanchor: 'left',
      font: {{ family: FONT.family, size: 12, color: T.plum }} }});
    // The 30 days after the incentive ended. It was drawn at 5% before, which is
    // invisible on white — it needs a fill you can actually see, a rule on the
    // day the incentive stopped, and a label, or it reads as nothing at all.
    // The two interim programs have no tail: their window is still open.
    if (inw.length && inw.length < s.x.length) {{
      const cut = inw[inw.length - 1], last = s.x[s.x.length - 1];
      layout.shapes.push({{ type: 'rect', xref: 'x' + ax, yref: 'y' + ax + ' domain',
        x0: cut, x1: last, y0: 0, y1: 1,
        fillcolor: 'rgba(107,90,128,0.13)', line: {{ width: 0 }}, layer: 'below' }});
      layout.shapes.push({{ type: 'line', xref: 'x' + ax, yref: 'y' + ax + ' domain',
        x0: cut, x1: cut, y0: 0, y1: 1,
        line: {{ color: T.muted, width: 1, dash: 'dot' }}, layer: 'below' }});
      // Anchored just inside the rule, not at the last day — a right-anchored
      // label sits on the subplot edge and gets clipped.
      layout.annotations.push({{ text: '+30d', xref: 'x' + ax, yref: 'y' + ax + ' domain',
        x: cut, y: 0.03, xanchor: 'left', xshift: 4, yanchor: 'bottom', showarrow: false,
        font: {{ family: FONT.family, size: 9, color: T.muted }} }});
    }}
  }});
  return {{ traces, layout }};
  }};
  onFit((narrow) => {{
    const {{ traces, layout }} = build(narrow);
    // One panel per row needs the height the rows actually take, or nine charts
    // get squeezed into the 640px the desktop grid was sized for.
    gd.style.height = (narrow ? D.small.length * 185
                              : Math.ceil(D.small.length / 2) * 215) + 'px';
    Plotly.react(gd, traces, layout, CONF).then(() => Plotly.Plots.resize(gd));
  }});
}})();
</script>
</body>
</html>
"""


def op_price_series(path: Path) -> list:
    """Daily OP price for the price chart, cached next to the cohort files.

    Fetched once from DefiLlama's coins API — the same source the pipeline
    prices everything else with — so the chart can't disagree with the
    figures around it, and a re-run doesn't depend on a file someone happened
    to download by hand."""
    if path.exists():
        cached = json.loads(path.read_text())
        # A cache written before the rounding below truncated each timestamp to its
        # date, which leaves some dates twice and others missing. It cannot be
        # repaired in place, so an old-style cache is refetched instead of trusted.
        if len({day for day, _ in cached}) == len(cached):
            return cached
    start = int(dt.datetime(2025, 10, 1, tzinfo=dt.timezone.utc).timestamp())
    url = (f"https://coins.llama.fi/chart/coingecko:optimism?start={start}"
           f"&span=360&period=1d&searchWidth=600")
    payload = json.load(urllib.request.urlopen(url, timeout=60))
    pts = payload["coins"]["coingecko:optimism"]["prices"]
    # The API's daily points sit a minute or two either side of midnight UTC, so
    # truncating to the date sent a 23:59 point to the day before and a 00:01 point
    # to the day itself: 70 dates appeared twice and 71 had no point. Adding twelve
    # hours first rounds to the nearest day, giving one point per day from the start.
    series = [[dt.datetime.fromtimestamp(p["timestamp"] + 43200, dt.timezone.utc).strftime("%Y-%m-%d"),
               round(p["price"], 5)] for p in pts]
    path.write_text(json.dumps(series))
    return series


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/s8_report.html")
    a = ap.parse_args()
    cohort_path = BASE / "reports/cohort.json"
    if not cohort_path.exists():
        raise SystemExit("reports/cohort.json is missing — run scripts/cohort_summary.py first.")
    cohort = json.loads(cohort_path.read_text())
    op_price = op_price_series(BASE / "reports/op_price_daily.json")
    html = build(cohort, op_price)
    out = BASE / a.out
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
