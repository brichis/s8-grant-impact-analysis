# S8 Grant Impact Analysis — 40acres Pilot (Targeted Scope)

A reproducible measurement of what a Season 8 Optimism incentive grant did to
Total Value Locked, implementing the [S8 Impact Measurement
Methodology](https://gov.optimism.io/t/s8-impact-measurement-methodology/10219)
under **Targeted Scope** — each incentivized contract measured on its own.

Input: the grants registry (a Google Sheet) + on-chain reads. **Nothing a
grantee self-reported is used as an input.**

First grant: **40acres.finance** (`APP-CS0S7GDN-MR3JI7`, 200,000 OP, OP + Base).

---

## Method

**Primary metric — the S8 TVL formula, per contract, summed:**

```
ΔTVL = Σ_contracts (tokens_end − tokens_start) × price_end
```

**Targeted Scope** (Step 1 of the methodology, the rigorous option): we measure
only the incentivized contracts listed in the registry `scope` tab, so there is
no protocol-wide over-count to proportion away. This makes attribution a
per-contract co-incentive flag rather than a protocol-wide calculation.

Each contract's token quantity is read directly from chain state at the last
block of each checkpoint's UTC day, dispatched by the scope `type`:

| type | measurement | status |
|---|---|---|
| `vault` | `totalAssets()` (ERC-4626, underlying units) | proven |
| `loan` | governance token locked across held veNFTs (`locked(tokenId)`) | proven |
| `pool` | incentivized token reserve (`balanceOf(pool)`) | implemented, untested |

The 40acres pilot exercises `vault` and `loan`. The `pool` path exists for DEX
grants (e.g. PancakeSwap, Velodrome) but has not yet been validated end-to-end —
pilot a single pool before relying on it.

**Window = incentive start → incentive end.** The methodology's literal text says
Start = actual grant delivery, but that was flagged as ambiguous in governance
(GFXlabs, Jan 2026), which recommended anchoring to when execution began. We
adopt that reading as a deliberate, disclosed choice.

**Prices** come from DefiLlama (token price on each checkpoint date); its token
quantities also serve as an independent cross-check on the on-chain reads.

**Supplementary** (reported, not S8 success metrics, labelled as such): retention
+30d, and the price-vs-quantity wedge (the USD change attributable to token
price, which the fixed-end-price formula excludes).

## Usage

```bash
pip install -r requirements.txt
export ALCHEMY_KEY=...        # archive-capable RPC; reads are on-chain
python run.py                 # 40acres (default)
python run.py APP-XXXX-XXXX   # any grant whose scope tab is filled
```

Outputs in `output/`:

| File | Contents |
|---|---|
| `chart_delta_tvl_by_contract.csv` | ΔTVL per scope contract — shows which pool drove the result |
| `chart_delta_tvl_checkpoints.csv` | grant-total ΔTVL at M1 / M2 / +30d |
| `table_contracts.csv` | per-contract qty_start / qty_end / price / ΔTVL |
| `scorecard.csv` | one-row grant summary |
| `measured_quantities.csv` | raw per-contract reads (cross-check vs DefiLlama) |

## Repository layout

```
run.py            orchestrator: registry → measure → price → metrics → outputs
src/registry.py   grant config from the registry Google Sheet
src/measure.py    per-contract quantity reads, dispatched by type (vault/loan/pool)
src/rpc.py        archive JSON-RPC client: block-at-timestamp, veNFT enumeration
src/prices.py     DefiLlama prices (+ quantity cross-check)
src/metrics.py    per-contract S8 formula, attribution, supplementary metrics
src/outputs.py    Datawrapper CSVs and tables
```

## Extending

The pipeline is registry-driven; a new grant runs once its `scope` tab is filled
(every incentivized contract with chain / address / pool / type). Per-grant
decisions:

- **Co-incentives** → set per-contract attribution below 100% via
  `attribution_overrides` on the config; the registry flags which grants have them.
- **DEX pools** → validate the `pool` reserve read on one pool first; two-sided
  pools need a decision on which token(s) to count, and `TOKEN_ADDRESS` in
  measure.py must map the token symbols.
- **Fee-metric grants** → the S8 Transaction Fees formula is a separate path,
  not implemented here.
- **Escrow addresses / selectors** in measure.py were confirmed for 40acres;
  re-verify against the block explorer for any new protocol.
