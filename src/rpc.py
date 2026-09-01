"""Archive JSON-RPC client (plumbing only).

Provides an archive-capable RPC wrapper with on-disk response caching, plus the
two non-trivial operations the measurement engine needs:

  * block_at(timestamp) — the last block at or before a UTC instant, so every
    read is taken at a defined, citable block.
  * venft_transfers(...) — ERC-721 token_ids moved in/out of an address, via
    alchemy_getAssetTransfers (works on the free tier; eth_getLogs does not,
    because of its 10-block range cap there).

Requires an archive-capable endpoint. Alchemy's free tier is sufficient:

    export ALCHEMY_KEY=...
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

# block_at's search step assumes ~2s blocks (true for OP Mainnet/Base/Soneium);
# Unichain and Ink run ~1s blocks, measured live via eth_getBlockByNumber over
# a 10k-block window — a wrong estimate here doesn't return a wrong answer
# (block_at still converges on the exact boundary block), it just may not
# converge within the fixed iteration budget.
BLOCK_TIME = {
    "unichain-mainnet": 1.0,
    "ink-mainnet": 1.0,
}


class ArchiveRPC:
    """Minimal archive JSON-RPC client with a persistent response cache."""

    def __init__(self, slug: str, cache_path: Path):
        key = os.environ.get("ALCHEMY_KEY")
        if not key:
            raise SystemExit("ALCHEMY_KEY not set (required for on-chain reads).")
        self.url = f"https://{slug}.g.alchemy.com/v2/{key}"
        self.slug = slug
        self.block_time = BLOCK_TIME.get(slug, 2.0)
        self.cache_path = Path(cache_path)
        self.cache = (json.loads(self.cache_path.read_text())
                      if self.cache_path.exists() else {})

    def _call(self, method: str, params: list, cache_key: str | None = None):
        if cache_key and cache_key in self.cache:
            return self.cache[cache_key]
        for attempt in range(5):
            try:
                body = requests.post(
                    self.url, timeout=45,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                ).json()
            except requests.exceptions.RequestException:
                # Transient network hiccups (read timeouts, connection resets)
                # shouldn't kill a long scan that's tens of minutes in — retry
                # with backoff the same as a rate-limit response, rather than
                # losing all progress since the last flush().
                if attempt == 4:
                    raise
                time.sleep(1.5 * (attempt + 1))
                continue
            if "result" in body:
                if cache_key:
                    self.cache[cache_key] = body["result"]
                return body["result"]
            if "rate" in str(body.get("error", "")).lower():
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"{self.slug} RPC error: {body.get('error')}")
        raise RuntimeError(f"{self.slug}: rate-limited/network-flaky repeatedly")

    def call_batch(self, calls: list[tuple[str, list, str | None]]) -> list:
        """Multiple eth JSON-RPC calls in one HTTP round trip (a JSON-RPC
        batch request — every major provider, including Alchemy, supports
        this). Returns results in the same order as `calls`. Cache hits are
        served locally and never sent; only the misses go over the wire.

        This exists because a handful of read paths (PancakeSwap Infinity's
        tick-bitmap scan, in particular) need thousands of independent
        eth_call reads at the same block — one HTTP round trip each was both
        slow (tens of minutes) and fragile (a single transient timeout partway
        through loses all unflushed progress). Batching cuts both the wall
        time and the number of chances for a network hiccup to hit.
        """
        results: list = [None] * len(calls)
        pending: list[tuple[int, str, list, str | None]] = []
        for i, (method, params, cache_key) in enumerate(calls):
            if cache_key and cache_key in self.cache:
                results[i] = self.cache[cache_key]
            else:
                pending.append((i, method, params, cache_key))

        BATCH_SIZE = 40
        for start in range(0, len(pending), BATCH_SIZE):
            chunk = pending[start:start + BATCH_SIZE]
            payload = [
                {"jsonrpc": "2.0", "id": i, "method": method, "params": params}
                for i, method, params, _ in chunk
            ]
            for attempt in range(6):
                try:
                    resp = requests.post(self.url, timeout=60, json=payload).json()
                except requests.exceptions.RequestException:
                    if attempt == 5:
                        raise
                    time.sleep(1.5 * (attempt + 1))
                    continue
                by_id = {r.get("id"): r for r in resp} if isinstance(resp, list) else {}
                errors = [r for r in by_id.values() if "result" not in r]
                # Alchemy's per-second compute-unit cap surfaces as a 429
                # with "exceeded its compute units per second capacity" in
                # the message, not the word "rate" — match broadly rather
                # than the exact wording of one provider's error.
                if errors and any(
                    e.get("error", {}).get("code") == 429
                    or any(w in str(e.get("error", "")).lower()
                           for w in ("rate", "capacity", "compute unit"))
                    for e in errors
                ):
                    time.sleep(2 * (attempt + 1))
                    continue
                if len(by_id) != len(chunk):
                    raise RuntimeError(
                        f"{self.slug}: batch call returned {len(by_id)} results "
                        f"for {len(chunk)} requests — {resp}"
                    )
                for i, _method, _params, cache_key in chunk:
                    r = by_id[i]
                    if "result" not in r:
                        raise RuntimeError(f"{self.slug} RPC error: {r.get('error')}")
                    results[i] = r["result"]
                    if cache_key:
                        self.cache[cache_key] = r["result"]
                break
            else:
                raise RuntimeError(f"{self.slug}: rate-limited/network-flaky repeatedly")
            time.sleep(0.15)  # stay under the per-second compute-unit cap proactively
        return results

    def flush(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache))

    # ---- blocks ----
    def _block(self, number):
        tag = number if isinstance(number, str) else hex(number)
        block = self._call("eth_getBlockByNumber", [tag, False],
                           None if tag == "latest" else f"{self.slug}:blk:{tag}")
        return int(block["number"], 16), int(block["timestamp"], 16)

    def latest_block(self) -> int:
        return self._block("latest")[0]

    def block_at(self, timestamp: int, block_time: float | None = None) -> int:
        """Last block with block.timestamp <= `timestamp`."""
        block_time = block_time if block_time is not None else self.block_time
        cache_key = f"{self.slug}:at:{timestamp}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        head_num, head_ts = self._block("latest")
        if timestamp >= head_ts:
            # Seconds/minutes past head = clock skew or a checkpoint landing on
            # "today" — clamp to head. Hours past head means a genuinely future
            # checkpoint date reached this far, which must not happen (run.py
            # measures a still-running grant only up to the last elapsed day —
            # see registry.measurement_end). Fail loud rather than silently
            # returning today's state as if it were a future reading.
            if timestamp - head_ts > 3600:
                raise RuntimeError(
                    f"{self.slug}: block_at got a timestamp "
                    f"{(timestamp - head_ts) / 86400:.1f} days past chain head "
                    f"— a checkpoint date in the future? Still-running grants "
                    f"must measure only up to the last elapsed day."
                )
            return head_num
        guess = max(1, head_num - int((head_ts - timestamp) / block_time))
        for _ in range(15):
            num, ts = self._block(guess)
            if -2 * block_time <= ts - timestamp <= 0:
                break
            guess = max(1, num - int((ts - timestamp) / block_time))
        else:
            raise RuntimeError("block_at did not converge")
        num, ts = self._block(guess)
        while ts > timestamp:
            num, ts = self._block(num - 1)
        while True:
            nxt_num, nxt_ts = self._block(num + 1)
            if nxt_ts > timestamp:
                break
            num, ts = nxt_num, nxt_ts
        self.cache[cache_key] = num
        return num

    # ---- contract reads ----
    def read(self, to: str, data: str, block: int) -> str:
        return self._call("eth_call", [{"to": to, "data": data}, hex(block)],
                          f"{self.slug}:call:{to}:{data}:{block}")

    def has_code(self, address: str, block: int) -> bool:
        """Whether `address` has contract code deployed at `block` — used to
        tell "not a pool" apart from "not deployed yet" (a pool created
        partway through a grant window has no code at an earlier checkpoint,
        which is a different situation from an address that will never be
        a pool)."""
        code = self._call("eth_getCode", [address, hex(block)],
                          f"{self.slug}:code:{address}:{block}")
        return code not in (None, "0x", "0x0")

    def logs(self, address: str, topics: list, from_block: int, to_block: int) -> list:
        """Raw eth_getLogs. Alchemy's free tier caps the range to 10 blocks —
        callers needing a wide historical search should locate a narrow block
        first (e.g. binary search on state via `read`, which has no such cap)
        rather than widen this call.
        """
        cache_key = (f"{self.slug}:logs:{address}:"
                     f"{':'.join(t or '' for t in topics)}:{from_block}:{to_block}")
        return self._call("eth_getLogs", [{
            "address": address, "topics": topics,
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
        }], cache_key)

    # ---- ERC-721 enumeration ----
    def venft_transfers(self, escrow: str, address: str, direction: str,
                        block: int) -> list[str]:
        """veNFT token_ids moved in/out of `address` up to `block`."""
        token_ids, page = [], None
        field = "toAddress" if direction == "in" else "fromAddress"
        while True:
            params = {"fromBlock": "0x0", "toBlock": hex(block),
                      "category": ["erc721"], "contractAddresses": [escrow],
                      "withMetadata": False, "excludeZeroValue": False,
                      "maxCount": "0x3e8", field: address}
            if page:
                params["pageKey"] = page
            res = self._call(
                "alchemy_getAssetTransfers", [params],
                None if page else
                f"{self.slug}:xfer:{escrow}:{address}:{direction}:{block}")
            for t in res.get("transfers", []):
                tid = t.get("tokenId") or t.get("erc721TokenId") or ""
                if tid:
                    token_ids.append(tid.lower())
            page = res.get("pageKey")
            if not page:
                return token_ids
