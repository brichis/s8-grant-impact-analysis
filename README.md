# S8 Grant Impact Analysis

A reproducible measurement of what a Season 8 Optimism incentive grant did to
Total Value Locked, implementing the [S8 Impact Measurement
Methodology](https://gov.optimism.io/t/s8-impact-measurement-methodology/10219)
under **Targeted Scope** — each incentivized contract measured on its own.

Input: the grants registry (a Google Sheet) + on-chain reads. **Nothing a
grantee self-reported is used as an input.**

Started as a single-grantee pilot on **40acres.finance**
(`APP-CS0S7GDN-MR3JI7`, 200,000 OP, OP + Base); the pipeline is now
registry-driven and covers the nine Season 8 Growth grants that ran a
measurable program: 40acres, Truemarkets, PancakeSwap, Curve Lending, Oku,
Super DCA, Extrafi, Hydrex and Velodrome. Every one of them has official
checkpoint figures **and** a validated daily ΔTVL series.

---

## Method

**Primary metric — the S8 TVL formula, per contract, summed:**

```
ΔTVL = Σ_contracts (tokens_end − tokens_start) × price_end
```

**Targeted Scope** (Step 1 of the methodology, the rigorous option): we measure
only the incentivized contracts listed in the registry `scope` tab, so there is
no protocol-wide over-count to proportion away, and no attribution step:
attribution is 100% for every grant, because the change being measured is
already the change on the contracts the grant incentivized.

Each contract's token quantity is read directly from chain state at the last
block of each checkpoint's day (a day ends at 23:59:59 UTC−6; see *Time
convention* below), dispatched by the scope `type`:

| type | measurement | status |
|---|---|---|
| `vault` | `totalAssets()` (ERC-4626, underlying units) | proven |
| `loan` | governance token locked across held veNFTs (`locked(tokenId)`), ownership replayed chronologically from transfers | proven (matches a full VotingEscrow event replay at every checkpoint) |
| `lend` | lending market, by interface probe: ERC-4626 `totalAssets()` (Curve LlamaLend), else Extrafi XLend/LYF (`src/extrafi.py`) | proven (Curve vaults match Curve's own API to 97–100%; Extrafi XLend matches DefiLlama to 99.7–100.5%) |
| `pool` / `pool (V3)` | incentivized token reserve, standard one-contract-per-pool AMM (`balanceOf(pool)`) | proven |
| `pool (Infinity)` | PancakeSwap Infinity's singleton-`Vault` CL pools — no deployed reserves-lens contract exists, so reserves come from an off-chain tick walk (`src/pancake_infinity.py`), cross-checked against the pool's own `getLiquidity()` every run | proven |
| `pool` (32-byte PoolId, non-Infinity) | Uniswap v4 pools, via Uniswap's `ReservesLens` where it exists at the block, else a full-range `StateView` tick walk (`src/uniswap_v4.py`) | proven (Optimism only) |
| `wallet-cohort` | Oku: the Morpho vault position of the wallets Oku paid, `balanceOf` + `convertToAssets` per wallet (`scripts/oku_wallet_cohort.py`) | proven |

For a `pool` type, the registry `token0`/`token1` symbols are matched to the
contract's *actual* on-chain currencies by reading each currency's own
`symbol()` — not a hand-maintained address table, which doesn't scale as pools
keep getting added. See `SYMBOL_ALIASES` in `measure.py` for the few
token/label mismatches that need it (e.g. OP-Stack pools hold WETH, never
native ETH). A pool created partway through a grant's window reports zero
reserves at any checkpoint before its own creation date — the literal truth,
not a placeholder.

**Window = incentive start → incentive end.** The methodology's literal text says
Start = actual grant delivery, but that was flagged as ambiguous in governance
(GFXlabs, Jan 2026), which recommended anchoring to when execution began. We
adopt that reading as a deliberate, disclosed choice.

**Time convention: a day ends at 23:59:59 UTC−6.** Every start and end quantity
is read at the last block on or before that moment, which is 05:59:59 UTC the
next day, and every daily series is cut at the same boundary. The Dune queries
do it in SQL (`DATE(block_time - INTERVAL '6' HOUR)`); the Python side does it
through `measure._end_of_day_ts` and `rpc.block_at`, which returns the last block
whose timestamp is at or before the moment. UTC−6 is the local time of the
machine that produced `output/` (Mexico City, which has no daylight saving on any
date this review covers). A different cut would move each reading by some hours,
and liquidity can change within a day.

`cohort_summary.py` pins the offset (`CHECKPOINT_TZ`), so the report's OP prices
reproduce anywhere. `src/measure.py`, `src/prices.py` and
`scripts/oku_wallet_cohort.py` still take it from the machine's local time zone,
so **run the measurement scripts under `TZ=America/Mexico_City`** to reproduce
the committed outputs; in another zone they would read different blocks.

**Still-running grants.** An incentive counts as ongoing when the registry's
`incentive_end_date` is today or later, **or when it is blank** — the registry's
way of recording "still running, no end announced" for a grantee that never
published an end date. For an ongoing grant the `end` checkpoint is read at
`registry.INTERIM_CUTOFF` (2026-09-15; set it to `None` to track the chain head
again) — an **interim** measurement, labelled as such in the window string and
console output, frozen so the published review stops moving between re-runs —
and the supplementary +30d retention metric is omitted because the window
hasn't closed.

**Milestones are judged on the whole window, not on one date.** M1 and M2
count as met if the grant's validated daily ΔTVL series reached the target on
any day between incentive start and end (`metrics.compute`). A grant without a
daily series is "not evaluated", never "not met". The snapshot checkpoint is
still measured and reported (`delta_tvl_at_snapshot_usd`, and a bar in the
checkpoint chart) as data — it decides nothing.

**Daily series.** For every grant the ΔTVL curve is rebuilt day by day, at the
same fixed end-date prices as the official figure, by one of the `scripts/`
tools below. Each script refuses to write anything unless its series
reproduces every on-chain checkpoint the official run measured (quantity to
1e-9, NFT counts exactly), so the curve is evidence of the same quality as the
checkpoints, at daily resolution. The curve's in-window peak is what the
milestone verdicts read.

**Prices** come from DefiLlama (token price on each checkpoint date); its token
quantities also serve as an independent cross-check on the on-chain reads.
A token DefiLlama cannot price contributes $0 and is named in the console
output (Super DCA's DCA token, which has no market price independent of the
pools the grant itself incentivized).

**Supplementary** (reported, not S8 success metrics, labelled as such): retention
+30d, and the price-vs-quantity wedge (the USD change attributable to token
price, which the fixed-end-price formula excludes).

## Usage

```bash
pip install -r requirements.txt
export ALCHEMY_KEY=...        # archive-capable RPC; reads are on-chain (or put it in .env)
python run.py APP-XXXX-XXXX   # any grant whose scope tab is filled
python run.py APP-XXXX-XXXX --global   # Global Scope instead of Targeted
python scripts/oku_wallet_cohort.py    # Oku (wallet cohort, no run.py scorecard)
```

Outputs go to `output/<grantee-slug>/` — one subdirectory per grantee (e.g.
`output/pancakeswap/`), so running a different grant never overwrites another
grant's numbers:

| File | Written by | Contents |
|---|---|---|
| `scorecard.csv` | run.py | one-row grant summary: ΔTVL, peak + date, M1/M2 verdicts, snapshot ΔTVL (data), $/OP, retention, wedge |
| `table_contracts.csv` | run.py | per-contract qty_start / qty_end / price / ΔTVL — **key charts on `contract`, not the pool label, which repeats across distinct contracts** |
| `chart_delta_tvl_by_contract.csv` | run.py | ΔTVL per scope contract — shows which pool drove the result |
| `chart_delta_tvl_checkpoints.csv` | run.py | grant-total ΔTVL at snapshot / end / +30d, end-date prices |
| `measured_quantities.csv` | run.py | raw per-contract reads (cross-check vs DefiLlama; input to the daily scripts) |
| `supplementary_daily_curve.csv` | `scripts/*_peak.py` | daily ΔTVL, `in_window` flag, +30 days drawn for context |
| `supplementary_peak.csv` | `scripts/*_peak.py` | in-window peak + date, curve end, coverage and source |

(Global Scope runs only write `scorecard.csv` and `chart_delta_tvl_checkpoints.csv` — there are no per-contract reads to report.)
Oku writes `output/oku_wallet_cohort.csv` (per-wallet positions) and `output/oku/` (its curve and peak).

### Daily series, per contract type

Run the official `run.py` first; every script reads its output and validates against it.

| Contract type | Script | Data source |
|---|---|---|
| `vault`, `lend`, Uniswap v4 `pool` | `scripts/rpc_daily_peak.py <GRANT_ID> output/<grantee>` | daily on-chain reads with the pipeline's own `measure.py` (Truemarkets, Curve, Extrafi, Super DCA, 40acres vaults) |
| `loan` (40acres) | `scripts/venft_events_replay.py output/40acres-finance data/dune/40acres_venft_events.csv --out data/rpc_daily/40acres-finance_loans_daily.csv`, then `rpc_daily_peak.py … --extra-daily <that file>` | VotingEscrow events (Dune, `scripts/dune/40acres_venft_events.sql`) |
| `pool`, `pool (V3)` | `scripts/dune_peak.py output/<grantee> data/dune/<export>.csv [--exclude-chain Soneium] [--exclude-type "pool (Infinity)"] [--also-token "OP Mainnet,<pool>,USDC,<USDC.e address>"]` | daily token transfers (Dune, `scripts/dune/*_daily_flows.sql`). Velodrome excludes Soneium (Dune doesn't index it, ~1% of scope TVL) |
| `pool (Infinity)` (PancakeSwap) | `scripts/infinity_events_replay.py output/pancakeswap data/dune/pancakeswap_infinity_events.csv --out data/dune/pancakeswap_infinity_daily_flows.csv`, then `dune_peak.py` with both flow exports | CLPoolManager events (Dune, `scripts/dune/pancakeswap_infinity_events.sql`) |
| Oku | `scripts/oku_daily_peak.py` | daily per-wallet reads |
| Global Scope | `scripts/global_peak.py output/<grantee> data/defillama_<slug>_<date>.json` | DefiLlama daily TVL series |

The Dune exports and derived daily quantities are committed under `data/dune/`
and `data/rpc_daily/`; each SQL file's header carries its query version. Every
script prints its checkpoint validation (`N/N coinciden`) before writing.

### Cohort summary (the report's inputs)

```bash
python scripts/cohort_summary.py     # -> reports/cohort.json + reports/cohort.csv
```

Derives everything the general report quotes about the cohort — totals, $/OP,
M1/M2 counts under the peak rule and under a best-7-day-average variant,
program length, when the peak came, share of the peak given back,
approval-to-start lag — from the committed outputs, the registry's budgets,
targets and dates, and `data/karma_dates.json` (application dates,
committed so the timing figures reproduce; refresh it with
`scripts/karma_dates.py`, which needs `KARMA_API_KEY`). Per-grantee records include the in-window daily curve, so
charts can be drawn from this one file.

The timing stages use three dates, each with one source. **Application** is
Karma's creation date. **Approval** is the report of the cycle that announced the
grant (`CYCLE_APPROVED`; a conditional pass counts from its report). **Delivery**
is the day the first tranche reached the grantee on-chain: registry `date_tx1`,
written from Blockscout by the sheet's Apps Script (the claim from the Hedgey
contract, or a direct payment), with `date_tx2` as the second tranche. The
registry's `initial_delivery_date` is the tracker's own date; it is carried into
`cohort.json` as `tracker_delivery_date` for context and feeds no figure.

### Publishing to a website

The CSVs above are Datawrapper-shaped — one file per chart, display labels as
column headers. To feed a site instead, bundle them into one typed JSON:

```bash
python scripts/export_site_json.py     # output/ -> site/data/grantees.json
```

`site/` is a Next.js app that reads it directly:

```bash
python scripts/export_site_json.py
npm --prefix site install
npm --prefix site run dev        # http://localhost:3000
```

Anything else importing the data does so the same way:

```ts
import { grantees, bySlug, ongoing } from "@/data";
```

This is a build step, not a service. All of `output/` is small and is
regenerated in batches by `run.py`, so the site ships the data in its own
bundle — no database, no API keys, no runtime dependency on this repo being
reachable. Re-run the export after any `run.py` run and redeploy.

The export re-keys and types the data; it changes no measured value. It
preserves the three states a reader has to be able to tell apart: Targeted vs
Global scope (`scope`), and whether the incentive is still running
(`ongoing` — its end checkpoint is an interim read, and `+30d` retention is
absent by design). Blank cells become `null`, never `0`. (It currently reads
scorecards only, so Oku and the daily curves are not in the bundle yet.)

| File | Contents |
|---|---|
| `site/data/grantees.json` | every grantee: scorecard, contracts, chart rows, tidy quantity series |
| `site/data/types.ts` | TypeScript types for the above |
| `site/data/index.ts` | typed entry point — `grantees`, `bySlug`, `targeted`, `ongoing` |
| `site/app`, `site/components` | the Next.js site: index of grantees + a page per grant |
| `site/V0_PROMPT.md` | prompt for regenerating the front end in v0 against this data |

## Repository layout

```
run.py                        orchestrator: registry → measure → price → metrics → outputs
src/registry.py               grant config from the registry Google Sheet; INTERIM_CUTOFF
src/chains.py                 chain name/slug table (registry / DefiLlama / RPC)
src/measure.py                per-contract quantity reads, dispatched by type (vault/loan/lend/pool)
src/extrafi.py                Extrafi XLend / LYF lending-market reads
src/uniswap_v4.py             Uniswap v4 pool reserves (Optimism; ReservesLens or StateView tick walk)
src/pancake_infinity.py       PancakeSwap Infinity CL pool reserves (Base, off-chain tick walk)
src/rpc.py                    archive JSON-RPC client: block-at-timestamp, batching, cache, veNFT enumeration
src/prices.py                 DefiLlama prices (+ quantity cross-check)
src/metrics.py                per-contract S8 formula, milestone verdicts on the daily peak, supplementary metrics
src/global_scope.py           Global Scope (protocol-wide DefiLlama TVL) alternative
src/outputs.py                Datawrapper CSVs and tables
scripts/rpc_daily_peak.py     daily series by on-chain reads (vault / lend / v4 pool)
scripts/dune_peak.py          daily series from Dune token transfers (v2/v3 pools)
scripts/infinity_events_replay.py  Infinity pools from CLPoolManager events
scripts/venft_events_replay.py     veNFT loan collateral from VotingEscrow events
scripts/global_peak.py        daily series for a Global Scope grantee
scripts/oku_wallet_cohort.py  Oku official figure (wallet cohort)
scripts/oku_daily_peak.py     Oku daily series
scripts/cohort_summary.py     reports/cohort.{json,csv} — the general report's inputs
scripts/export_site_json.py   output/ → site/data/grantees.json
scripts/dune/*.sql            the Dune queries behind data/dune/*.csv
data/rpc_cache.json           RPC response cache (ignored; ~150 MB; never run two writers at once)
data/karma_dates.json         Karma application dates (committed; dates and statuses only)
data/dune/, data/rpc_daily/   committed event exports and daily quantities
```

## Extending

The pipeline is registry-driven; a new grant runs once its `scope` tab is filled
(every incentivized contract with chain / address / token0 / token1 / type). Per-grant
decisions:

- **Co-incentives** → not discounted. Several grantees ran their own token
  incentives alongside the OP grant, but splitting credit between the two needs
  a defensible per-grant ratio that the available data does not support, so
  none is invented: every grant reports at 100% attribution, disclosed here.
- **DEX pools** → the `pool` type dispatches on the registry `type` string
  (`pool`, `pool (V3)`, `pool (Infinity)`) and on address shape (20-byte
  contract vs. 32-byte PoolId); a genuinely new AMM architecture (not
  standard v2/v3, not Uniswap-v4-style singleton) needs its own reserve-read
  module alongside `uniswap_v4.py` / `pancake_infinity.py`.
- **Fee-metric grants** → the S8 Transaction Fees formula is a separate path,
  not implemented here.
- **Escrow addresses / selectors** in measure.py were confirmed for 40acres;
  re-verify against the block explorer for any new protocol.
- **Fail loud, never silently wrong.** Every read path raises on anything it
  cannot verify (a label that doesn't match the on-chain symbol, a tick walk
  that doesn't reconcile with `getLiquidity()`, a daily series that doesn't
  reproduce a checkpoint) rather than returning a plausible number.
