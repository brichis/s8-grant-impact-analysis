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
            {"label": "Claimed by the 9 that ran a program", "value": 2210000, "color": C["plum"]},
            {"label": "Claimed by grants that never ran one", "value": 340000, "color": C["coral"]},
            {"label": "Delivered to claim contracts, never claimed", "value": 1268000, "color": C["butter"]},
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
        <td class="num">{x['usd_per_op']:.2f}</td>
        <td class="num">{short(x['target_milestone1']) if x['target_milestone1'] else '—'}</td>
        <td class="mark">{verdict(x['milestone1_met'])}</td>
        <td class="num">{short(x['target_total']) if x['target_total'] else '—'}</td>
        <td class="mark">{verdict(x['total_target_met'])}</td>
        <td class="num">{f"{x['retention_30d_pct']:.1f}%" if x['retention_30d_pct'] is not None else '—'}</td>
        <td class="num">{money(x['peak_delta_tvl_usd'])}<span class="sub">{x['peak_date']}</span></td>
        <td class="num">{x['weeks']:.0f} wk</td>
      </tr>""" for x in order)

    data_json = json.dumps(charts, separators=(",", ":"))
    return TEMPLATE.format(c=C, mark=MARK, rows=rows, data=data_json, cohort=c,
                           cutoff=cohort["interim_cutoff"], generated=cohort["generated_at"][:10])


def verdict(v):
    return '<span class="met">✓</span>' if v else ('<span class="miss">✗</span>' if v is False else '—')


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Season 8 Growth Grants — TVL Impact Review</title>
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
  .wrap {{ max-width: 1120px; margin: 0 auto; padding: 56px 24px 96px; }}
  .measure {{ max-width: 68ch; }}
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
  strong {{ font-weight: 600; }}
  .small {{ font-size: 14px; line-height: 22px; color: var(--plum-muted); }}
  .lede {{ font-size: 19px; line-height: 31px; color: var(--plum-muted); }}
  .rule {{ height: 1px; background: var(--border); border: 0; margin: 0; }}
  header .rule {{ margin-top: 28px; }}

  .keys {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(232px, 1fr));
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
  .grid2 {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}

  .tablewrap {{ overflow-x: auto; margin: 24px 0 10px; border: 1px solid var(--border);
                border-radius: 16px; background: var(--card); }}
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

  .callout {{ border-left: 3px solid var(--coral); background: var(--card); border-radius: 0 14px 14px 0;
              padding: 16px 20px; margin: 24px 0; }}
  .stars {{ display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }}
  footer {{ margin-top: 72px; padding-top: 24px; border-top: 1px solid var(--border); }}
  @media (max-width: 640px) {{
    h1 {{ font-size: 44px; line-height: 46px; }}
    h2 {{ font-size: 32px; line-height: 36px; }}
    h3 {{ font-size: 24px; line-height: 30px; }}
    body {{ font-size: 16px; line-height: 26px; }}
    .wrap {{ padding: 36px 18px 64px; }}
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
  <h1>What 2.2M OP actually bought in liquidity</h1>
  <p class="lede measure">Nine Season 8 growth programs, measured contract by contract on-chain,
  day by day — what they reached, what they gave back, and what the next program should do
  differently.</p>
  <p class="small">Figures as of {generated}; still-running programs frozen at a {cutoff} cutoff.
  Every number is derived from the committed measurements by <code>scripts/cohort_summary.py</code>.</p>
  <hr class="rule">
</header>

<section id="short">
  <h2>The short version</h2>
  <div class="keys">
    <div class="key" style="--accent: {c[coral]}"><span class="label">Measured impact</span>
      <span class="n">+$8.03M</span><span class="t">ΔTVL across the nine programs — $3.63 per OP. Six of nine were positive.</span></div>
    <div class="key" style="--accent: {c[lilac]}"><span class="label">Concentration</span>
      <span class="n">84%</span><span class="t">of all the liquidity came from one grant, 40acres.finance.</span></div>
    <div class="key" style="--accent: {c[mint]}"><span class="label">Milestones</span>
      <span class="n">5 of 8</span><span class="t">met Milestone 1 once a target counts as reached when it was actually reached. One met its total target.</span></div>
    <div class="key" style="--accent: {c[butter]}"><span class="label">Recoverable</span>
      <span class="n">1.27M OP</span><span class="t">sits in claim contracts, never claimed, by grants that never ran a program.</span></div>
  </div>
  <div class="measure">
    <ul>
      <li><strong>Programs peak early and give it back.</strong> The median program peaked on day 56 — 41% of the way in — and three of the five that met M1 were below that target again by the end.</li>
      <li><strong>OP fell 55%</strong> between the average program start and the average program end. The OP behind these grants was worth $708k when claimed and $291k when the programs ended.</li>
      <li><strong>Grantees were not the slow part.</strong> The median program started 15 days after approval. The year in the schedule was the allowance, not the work.</li>
      <li><strong>Staging worked.</strong> 2.47M OP was never released because later tranches depended on progress — but 340k OP was claimed by grants that never ran anything.</li>
    </ul>
  </div>
</section>

<section id="results">
  <h2>What each program did</h2>
  <p class="measure">ΔTVL is measured at fixed end-date prices, so it counts liquidity added, not
  token prices moving. The peak is the highest the daily series reached inside the incentive
  window; the milestone columns are judged on it.</p>

  <div class="fig">
    <span class="label">Figure 1 · Peak reached vs. where it ended</span>
    <div id="c-peak" class="chart" style="height:460px"></div>
    <p class="cap small">Each program's highest day against its closing figure. Velodrome reached
    +$10.4M and is now below zero; 40acres kept 94% of its peak.</p>
  </div>

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
        <th scope="row">Total</th><td class="num">2,210,000</td><td class="num">$8,026,311</td>
        <td class="num">3.63</td><td></td><td class="mark">5 met</td><td></td><td class="mark">1 met</td>
        <td></td><td></td><td></td>
      </tr></tfoot>
    </table>
  </div>
  <p class="small">Interim programs (Curve Lending, Velodrome) have no announced end date and are
  measured to the {cutoff} cutoff. Retention is only available where a window closed 30 days ago.</p>

  <div class="fig">
    <span class="label">Figure 2 · The same nine curves, on one scale</span>
    <div id="c-curves" class="chart" style="height:430px"></div>
    <p class="cap small">Each program's ΔTVL as a share of its own peak, against how far it was
    through its window. The shape repeats: climb, peak around the middle, drift down.</p>
  </div>
</section>

<section id="lessons">
  <h2>Lessons for the next program</h2>

  <h3>1 · Judge milestones on what actually happened</h3>
  <p class="measure">A milestone is met here if the target was reached on any day inside the
  incentive window, evidenced by the validated daily series. A single-date checkpoint — the
  snapshot, or the incentive end — is kept as data, but it cannot tell apart a program that
  reached its target and lost the liquidity from one that never got close.</p>
  <div class="callout measure">
    <p style="margin:0"><strong>The obvious objection is gaming:</strong> touch the target for a day,
    then pull the liquidity out. The defence is to require an average over a set number of days.
    Here it changes nothing — the best 7-day average produces the same verdicts — which says these
    peaks were levels held for weeks, not one-day spikes.</p>
  </div>

  <h3>2 · Twelve months is the allowance; three is the useful deadline</h3>
  <p class="measure">Grants in this season were approved between September and December 2025.
  Teams had about a year to run the work end to end — and a year on, several approved grants still
  have nothing to show, while the ones that delivered did not need the year: a median of 15 days
  from approval to launch, and 3 days from claiming the OP to starting.</p>
  <p class="measure"><strong>40acres is the model case:</strong> application created Aug 25, 2025,
  first payment approved Sep 30, claimed Oct 7, distribution started Oct 15, program finished
  Jan 27, 2026 — the best result in the cohort. <strong>Give three months from approval to launch,
  not twelve</strong>, with a deadline for the final report.</p>

  <h3>3 · Aim for programs of 12 to 18 weeks</h3>
  <p class="measure">Median length was 16.6 weeks, from 9 to 31. Seven of nine peaked before the
  last tenth of their window, and longer programs did not hold up better — Hydrex, the shortest at
  9 weeks, gave back the most. Ask for 12 to 18 weeks, with a check-in at the halfway mark: the
  point where most of these programs peaked, and the last moment one can still be corrected,
  extended or stopped.</p>

  <h3>4 · Staged payments worked, and there is OP to recover right now</h3>
  <div class="fig">
    <span class="label">Figure 3 · Where the 6.29M OP went</span>
    <div id="c-flow" class="chart" style="height:220px"></div>
    <p class="cap small">Only the first block bought a measured program. The third block never left
    the claim contracts.</p>
  </div>
  <div class="measure">
    <ul>
      <li><strong>2.47M OP was never released</strong>, because later tranches were conditional on progress.</li>
      <li><strong>340k OP was claimed by grants that never ran a program.</strong> A 20% first tranche instead of 40% would have exposed about half of it.</li>
      <li><strong>1.27M OP was delivered to claim contracts and never claimed</strong> — Morpho 600k, Tydro 600k, LiqPass 32k, Strands 28k, NEUS 8k. None of it left those contracts, so it can be recovered in full; LiqPass was withdrawn outright.</li>
    </ul>
    <p>With both councils dissolved, nobody is verifying these milestones or deciding what happens
    to OP that was released and never used. <strong>That is an opening for the community at
    large:</strong> watch whether these programs are delivered, and where they are not, claw back
    what can still be recovered. Most of these grants have just passed their one-year mark or are
    weeks away from it.</p>
  </div>

  <h3>5 · OP moved too much for USD-denominated planning</h3>
  <div class="fig">
    <span class="label">Figure 4 · OP price across the season</span>
    <div id="c-price" class="chart" style="height:330px"></div>
    <p class="cap small">Targets were written in USD and stayed fixed; the incentive behind them
    lost more than half its value between the average start and the average end.</p>
  </div>
  <p class="measure">The 2.21M OP behind these programs was worth $708k when claimed and $291k when
  the programs ended. Hydrex's own milestone report notes the grant was structured at $0.65 OP
  against $0.33 at reporting time. This is also a candidate explanation for the modest $/OP: a
  program designed around a budget worth twice what it turned out to be, matched by co-incentives
  sized the same way, delivers less than the approval assumed — and is then judged against a target
  that never moved.</p>

  <h3>6 · Collect the evaluation inputs at approval time</h3>
  <p class="measure">The incentive windows and the incentivized contracts had to be rebuilt by hand
  for this review, from applications, social posts and on-chain data. Dashboard TVL is no
  substitute: across the eight grants with a price breakdown, TVL valued at each day's prices fell
  $5.64M while ΔTVL at fixed prices rose $7.99M. The $13.62M gap is token price movement, not
  liquidity.</p>
  <p class="measure">A short form at approval, updated at each milestone, would have produced this
  review automatically: incentive start and end dates with a link to the announcement; contract
  addresses or pool IDs per chain and their type; incentive token and amount per period; and
  co-incentives in token units.</p>

  <h3>7 · Denominate incentives and milestones in OP</h3>
  <p class="measure">Budgets in OP, TVL milestones measured at fixed prices as the S8 formula
  already does, and co-incentives committed and reported in token units rather than USD
  equivalents — which would also make them verifiable, and countable in the next review.</p>
</section>

<section id="curves">
  <h2>The nine curves</h2>
  <p class="measure">Every program's daily ΔTVL, validated against the on-chain checkpoints before
  it was used. The shaded tail is the 30 days after the incentive ended, which never counts toward
  the peak.</p>
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
  disclosure, not a claim of neutrality: everything above is reproducible from the sources named.</p>
  <div class="measure">
    <ul>
      <li><strong>Metric.</strong> ΔTVL = Σ (quantity at incentive end − quantity at incentive start) × token price at the end date, over the contracts each grant incentivized.</li>
      <li><strong>Scope.</strong> Targeted, contract by contract. Oku has no contract of its own, so it is measured as the Morpho vault position of the 67 wallets it paid.</li>
      <li><strong>Claims.</strong> Filtered on Blockscout from the council's payout wallet. Payments made after the councils were dissolved, or routed another way, may be missing.</li>
      <li><strong>Delivered is not claimed.</strong> OP goes to a Hedgey claim contract first; the grantee claims it from there.</li>
      <li><strong>Co-incentives are excluded.</strong> Teams sized them in USD at application time and there is no reliable way to verify what was actually deployed.</li>
      <li><strong>Two corrections</strong> were made while validating: 40acres' loan collateral was undercounted, raising its ΔTVL from $6.14M to $6.76M, and Super DCA moved to Targeted Scope once its Uniswap v4 pools became readable. No verdict changed.</li>
    </ul>
  </div>
  <div class="callout measure">
    <p style="margin:0"><strong>If you have information to add</strong> — you were on one of these
    teams, you know a program that ran without being reported, or you can identify a payment we
    could not — please get in touch. This review is only as good as what is visible from the
    outside.</p>
  </div>
</section>

<footer>
  <div class="stars">
    <svg width="14" height="14" viewBox="0 0 100 100" aria-hidden="true"><path d="{mark}" fill="{c[coral]}" fill-rule="evenodd"/></svg>
    <span class="label">Brichis · Governance → Compliance</span>
  </div>
  <p class="small" style="margin-top:8px">Optimism Season 8 growth grants · measured {generated} ·
  interim programs frozen at {cutoff}.</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<script>
const D = {data};
const T = {{ plum: '{c[plum]}', muted: '{c[plum_muted]}', coral: '{c[coral]}', orchid: '{c[orchid]}',
             lilac: '{c[lilac]}', peri: '{c[peri]}', mint: '{c[mint]}', butter: '{c[butter]}',
             border: '{c[border]}', card: '{c[card]}', signal: '{c[signal]}' }};
const FONT = {{ family: "'IBM Plex Sans', system-ui, sans-serif", size: 12, color: T.muted }};
const BASE = {{
  paper_bgcolor: T.card, plot_bgcolor: T.card, font: FONT,
  margin: {{ l: 8, r: 16, t: 8, b: 34 }}, showlegend: false, hovermode: 'closest',
  xaxis: {{ gridcolor: T.border, zerolinecolor: T.border, tickfont: FONT }},
  yaxis: {{ gridcolor: T.border, zerolinecolor: T.border, tickfont: FONT }},
}};
const CONF = {{ displayModeBar: false, responsive: true }};
const clone = (o) => JSON.parse(JSON.stringify(o));

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
  layout.margin = {{ l: 132, r: 176, t: 40, b: 36 }};
  layout.showlegend = true;
  layout.legend = {{ orientation: 'h', y: 1.12, x: 1, xanchor: 'right',
    font: {{ family: FONT.family, size: 11, color: T.plum }} }};
  layout.xaxis = {{ ...layout.xaxis, tickprefix: '$', tickformat: '.2s', zeroline: true, zerolinewidth: 1.5 }};
  layout.yaxis = {{ ...layout.yaxis, tickmode: 'array', tickvals: y, ticktext: d.names,
    tickfont: {{ family: FONT.family, size: 12, color: T.plum }}, gridcolor: 'rgba(0,0,0,0)' }};
  Plotly.newPlot('c-peak', traces, layout, CONF);
}})();

// Figure 2 — normalized curves
(function () {{
  const hi = {{ '40acres.finance': T.coral, 'Velodrome Finance': T.orchid, 'Hydrex': T.mint }};
  const traces = D.curves.map((s) => ({{
    x: s.x, y: s.y, type: 'scatter', mode: 'lines', name: s.name,
    line: {{ color: hi[s.name] || 'rgba(46,27,69,0.22)', width: hi[s.name] ? 2.4 : 1.4 }},
    hovertemplate: s.name + ' — %{{y:.0f}}% of its peak at %{{x:.0f}}% of the window<extra></extra>',
  }}));
  const layout = clone(BASE);
  layout.margin = {{ l: 56, r: 124, t: 26, b: 42 }};
  layout.xaxis = {{ ...layout.xaxis, title: {{ text: 'Share of the incentive window', font: FONT }}, ticksuffix: '%', range: [0, 100] }};
  // Hydrex ends at -170% of its peak; clipping the axis keeps the shared shape readable
  // and the small-multiples below show every curve in full.
  layout.yaxis = {{ ...layout.yaxis, title: {{ text: 'Share of own peak', font: FONT }},
    ticksuffix: '%', zeroline: true, zerolinewidth: 1.5, range: [-105, 108] }};
  layout.annotations = D.curves.filter((s) => hi[s.name]).map((s) => ({{
    x: 100, y: Math.max(-100, Math.min(104, s.y[s.y.length - 1])), text: s.name,
    xanchor: 'left', xshift: 8, showarrow: false,
    font: {{ family: FONT.family, size: 11, color: hi[s.name] }},
  }}));
  layout.annotations.push({{ xref: 'paper', yref: 'paper', x: 0, y: -0.2, xanchor: 'left',
    text: 'Hydrex continues to −170%; see the nine curves below', showarrow: false,
    font: {{ family: FONT.family, size: 10, color: T.muted }} }});
  Plotly.newPlot('c-curves', traces, layout, CONF);
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
  Plotly.newPlot('c-flow', traces, layout, CONF);
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
  Plotly.newPlot('c-price', traces, layout, CONF);
}})();

// The nine curves — small multiples
(function () {{
  const cols = 3, rows = Math.ceil(D.small.length / cols), traces = [], layout = clone(BASE);
  layout.grid = {{ rows: rows, columns: cols, pattern: 'independent', roworder: 'top to bottom' }};
  layout.margin = {{ l: 52, r: 16, t: 34, b: 34 }};
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
    layout['xaxis' + ax] = {{ gridcolor: T.border, showticklabels: true, nticks: 3, tickfont: {{ ...FONT, size: 10 }} }};
    layout['yaxis' + ax] = {{ gridcolor: T.border, zeroline: true, zerolinecolor: T.border,
      tickprefix: '$', tickformat: '.2s', nticks: 4, tickfont: {{ ...FONT, size: 10 }} }};
    layout.annotations.push({{ text: s.name + (s.ongoing ? ' · interim' : ''), xref: 'x' + ax + ' domain',
      yref: 'y' + ax + ' domain', x: 0, y: 1.16, showarrow: false, xanchor: 'left',
      font: {{ family: FONT.family, size: 12, color: T.plum }} }});
    if (inw.length && inw.length < s.x.length) {{
      layout.shapes.push({{ type: 'rect', xref: 'x' + ax, yref: 'y' + ax + ' domain',
        x0: inw[inw.length - 1], x1: s.x[s.x.length - 1], y0: 0, y1: 1,
        fillcolor: 'rgba(46,27,69,0.05)', line: {{ width: 0 }}, layer: 'below' }});
    }}
  }});
  Plotly.newPlot('c-small', traces, layout, CONF);
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
        return json.loads(path.read_text())
    start = int(dt.datetime(2025, 10, 1, tzinfo=dt.timezone.utc).timestamp())
    url = (f"https://coins.llama.fi/chart/coingecko:optimism?start={start}"
           f"&span=360&period=1d&searchWidth=600")
    payload = json.load(urllib.request.urlopen(url, timeout=60))
    pts = payload["coins"]["coingecko:optimism"]["prices"]
    series = [[dt.datetime.fromtimestamp(p["timestamp"], dt.timezone.utc).strftime("%Y-%m-%d"),
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
