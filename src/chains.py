"""Chain identifiers, in one place.

Every system this pipeline talks to spells a chain differently — the registry
writes "OP Mainnet", DefiLlama's `chainTvls` says "Optimism", its coins API
wants "optimism", Alchemy wants "opt-mainnet" — and those four spellings used
to live in five separate tables across five modules. They had already drifted:
metrics.py's copy listed three of the six chains and silently returned the
label unchanged for the rest, which was harmless only because the registry
label and the DefiLlama key happen to coincide for Unichain, Ink and Soneium.
The next chain where they don't would have been mispriced without an error.

Adding a chain is now one row here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    registry: str    # the registry's `chain` column
    canonical: str   # internal name, and DefiLlama's chainTvls key
    coins: str       # coins.llama.fi prefix, as in "optimism:0xabc…"
    rpc: str         # Alchemy subdomain


CHAINS = (
    Chain("OP Mainnet", "Optimism", "optimism", "opt-mainnet"),
    Chain("Base", "Base", "base", "base-mainnet"),
    Chain("Unichain", "Unichain", "unichain", "unichain-mainnet"),
    Chain("Ink", "Ink", "ink", "ink-mainnet"),
    Chain("Soneium", "Soneium", "soneium", "soneium-mainnet"),
)

# Both spellings resolve, because both are in use: the registry says
# "OP Mainnet" while every deployment-address table here is keyed "Optimism".
_BY_NAME = {}
for _c in CHAINS:
    _BY_NAME[_c.registry.lower()] = _c
    _BY_NAME[_c.canonical.lower()] = _c


def known(label: str) -> bool:
    return str(label).strip().lower() in _BY_NAME


def resolve(label: str) -> Chain:
    chain = _BY_NAME.get(str(label).strip().lower())
    if chain is None:
        raise SystemExit(
            f"Chain '{label}' is not in chains.py. Add a row there with its "
            f"DefiLlama chainTvls key, coins.llama.fi slug and RPC subdomain — "
            f"guessing any of the four would be silently wrong, not an error."
        )
    return chain


def canonical(label: str) -> str:
    """Internal chain name, which is also DefiLlama's chainTvls key."""
    return resolve(label).canonical


def coins_slug(label: str) -> str:
    return resolve(label).coins


def rpc_slug(label: str) -> str:
    return resolve(label).rpc
