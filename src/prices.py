"""DefiLlama price source (and optional quantity cross-check).

Under Targeted Scope, token *quantities* come from direct contract reads
(measure.py). DefiLlama is used here only for **prices** — the token price on
each checkpoint date, needed to value the quantity deltas per the S8 formula.

We still fetch the full protocol payload (chain-filtered) because the same
endpoint carries a per-token price series (tokensInUsd / tokens), and its
quantities double as an independent cross-check on the on-chain reads.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://api.llama.fi/protocol/{slug}"
COINS_API_URL = "https://coins.llama.fi/prices/historical/{ts}/{coins}"
# DefiLlama chain label -> coins.llama.fi chain slug, for the per-token
# fallback below.
COINS_CHAIN_SLUG = {"Base": "base", "Optimism": "optimism",
                    "Unichain": "unichain", "Ink": "ink", "Soneium": "soneium"}


def fetch_protocol(slug: str, raw_dir: Path | None = None) -> dict:
    resp = requests.get(API_URL.format(slug=slug), timeout=90)
    resp.raise_for_status()
    payload = resp.json()
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        (raw_dir / f"defillama_{slug}_{stamp}.json").write_text(json.dumps(payload))
    return payload


def _frame(rows):
    if not rows:
        # Some DefiLlama protocol entries carry an aggregate `tvl` for a chain
        # but no per-token breakdown at all (e.g. PancakeSwap on Base: `tokens`
        # and `tokensInUsd` are both `[]`). Empty, not missing — return an
        # empty frame so price_series/prices_at degrade to "no price found"
        # for that chain rather than crashing; fill_price_gaps below covers it.
        return pd.DataFrame(columns=["day"]).set_index("day")
    f = pd.DataFrame([{"day": r["date"], **r["tokens"]} for r in rows])
    f["day"] = pd.to_datetime(f["day"], unit="s").dt.normalize()
    return f.set_index("day").sort_index()


# DefiLlama's own chainTvls object doesn't use one consistent key for a given
# chain across every protocol's payload -- most protocols use "Optimism", but
# at least one (Extrafi) uses "OP Mainnet" for the exact same chain. Try the
# canonical name first, then any known alias, before giving up.
CHAIN_KEY_ALIASES = {"Optimism": ["OP Mainnet"]}


def _resolve_chain_key(chain: str, available: list) -> str | None:
    if chain in available:
        return chain
    for alias in CHAIN_KEY_ALIASES.get(chain, []):
        if alias in available:
            return alias
    return None


def price_series(payload: dict, defillama_chains: list) -> pd.DataFrame:
    """Long frame: day, chain, token, price (from tokensInUsd / tokens)."""
    available = list(payload.get("chainTvls", {}))
    resolved = {c: _resolve_chain_key(c, available) for c in defillama_chains}
    missing = [c for c, key in resolved.items() if key is None]
    if missing:
        raise SystemExit(f"Chain(s) {missing} absent from DefiLlama. "
                         f"Present: {available}")
    frames = []
    for chain, payload_key in resolved.items():
        cd = payload["chainTvls"][payload_key]
        q = _frame(cd["tokens"]).stack().rename("quantity")
        u = _frame(cd["tokensInUsd"]).stack().rename("usd")
        m = pd.concat([q, u], axis=1).reset_index()
        m.columns = ["day", "token", "quantity", "usd"]
        m["chain"] = chain  # canonical label, not the payload's own key
        frames.append(m)
    df = pd.concat(frames, ignore_index=True)
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["usd"] = pd.to_numeric(df["usd"], errors="coerce")
    df = df.dropna(subset=["quantity"])
    df["price"] = (df["usd"] / df["quantity"]).where(df["quantity"] != 0)
    return df[["day", "chain", "token", "price", "quantity"]]


def prices_at(series: pd.DataFrame, checkpoints: dict) -> dict:
    """{(chain, TOKEN): {checkpoint_name: price}} at each checkpoint date."""
    out: dict = {}
    for name, day in checkpoints.items():
        cutoff = pd.Timestamp(day)
        up_to = series[series["day"] <= cutoff]
        if up_to.empty:
            continue
        latest = up_to.sort_values("day").groupby(["chain", "token"]).tail(1)
        for _, r in latest.iterrows():
            key = (r["chain"], str(r["token"]).upper())
            out.setdefault(key, {})[name] = float(r["price"])
    return out


def fill_price_gaps(price_map: dict, measured: pd.DataFrame, checkpoints: dict) -> dict:
    """Backfill (chain, TOKEN) checkpoint prices missing from the protocol
    series using DefiLlama's coins API — same provider, a different endpoint
    that prices any ERC20 directly by (chain, address) instead of relying on
    the protocol object having a per-token TVL breakdown for that chain.

    Needed for protocols like PancakeSwap, whose DefiLlama entry carries an
    aggregate `tvl` for Base but empty `tokens`/`tokensInUsd` — there is
    nothing for price_series to derive a per-token price from. Resolves each
    gap token's address from `measured`'s own `address` column — the same
    on-chain address measure.py already resolved for the reserve read itself
    (via each token's real symbol(), not a hand-maintained table) — so there
    is one place tokens get mapped to addresses, not two.
    """
    from metrics import _defillama_chain

    # A (chain, token) pair can appear in `measured` with a missing address
    # (e.g. a pool-not-yet-created checkpoint, which reports quantity=0 with
    # no resolved leg address) alongside rows that do have one — take any
    # resolved address for that pair.
    with_addr = measured.dropna(subset=["address"])
    needed = with_addr[["chain", "token", "address"]].drop_duplicates(
        subset=["chain", "token"])
    gaps = []  # [(defillama_chain_label, TOKEN, address)]
    for _, r in needed.iterrows():
        chain_label = _defillama_chain(r["chain"])
        token = str(r["token"]).upper()
        have = price_map.get((chain_label, token), {})
        if set(checkpoints) <= set(have):
            continue
        gaps.append((chain_label, token, r["address"]))

    if not gaps:
        return price_map

    for name, day in checkpoints.items():
        ts = int(pd.Timestamp(day).replace(
            hour=23, minute=59, second=59).to_pydatetime().timestamp())
        coins = ",".join(
            f"{COINS_CHAIN_SLUG.get(chain, chain.lower())}:{addr}"
            for chain, _token, addr in gaps)
        resp = requests.get(COINS_API_URL.format(ts=ts, coins=coins), timeout=30)
        resp.raise_for_status()
        quotes = resp.json().get("coins", {})
        for chain, token, addr in gaps:
            slug_key = f"{COINS_CHAIN_SLUG.get(chain, chain.lower())}:{addr}"
            quote = quotes.get(slug_key)
            if quote is None:
                continue
            price_map.setdefault((chain, token), {})[name] = float(quote["price"])
    return price_map
