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

RPC_SLUG = {
    "OP Mainnet": "opt-mainnet",
    "Optimism": "opt-mainnet",
    "Base": "base-mainnet",
}


class ArchiveRPC:
    """Minimal archive JSON-RPC client with a persistent response cache."""

    def __init__(self, slug: str, cache_path: Path):
        key = os.environ.get("ALCHEMY_KEY")
        if not key:
            raise SystemExit("ALCHEMY_KEY not set (required for on-chain reads).")
        self.url = f"https://{slug}.g.alchemy.com/v2/{key}"
        self.slug = slug
        self.cache_path = Path(cache_path)
        self.cache = (json.loads(self.cache_path.read_text())
                      if self.cache_path.exists() else {})

    def _call(self, method: str, params: list, cache_key: str | None = None):
        if cache_key and cache_key in self.cache:
            return self.cache[cache_key]
        for attempt in range(5):
            body = requests.post(
                self.url, timeout=45,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            ).json()
            if "result" in body:
                if cache_key:
                    self.cache[cache_key] = body["result"]
                return body["result"]
            if "rate" in str(body.get("error", "")).lower():
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"{self.slug} RPC error: {body.get('error')}")
        raise RuntimeError(f"{self.slug}: rate-limited repeatedly")

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

    def block_at(self, timestamp: int, block_time: float = 2.0) -> int:
        """Last block with block.timestamp <= `timestamp`."""
        cache_key = f"{self.slug}:at:{timestamp}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        head_num, head_ts = self._block("latest")
        if timestamp >= head_ts:
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
