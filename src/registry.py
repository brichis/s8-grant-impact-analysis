"""Registry reader.

Loads one grant's configuration from the S8 grants registry (a Google Sheet
exported live as CSV). The registry is the only hand-maintained input; every
other number in the pipeline is fetched from a public source.

Scope model: **Global Scope** (per the S8 methodology, Step 1). We measure the
protocol-wide token change, limited to the chains the grant targets. We do not
enumerate individual pools. This is the sanctioned lighter-weight option in the
framework:

    "If the grant targets the entire protocol use the protocol-wide change."
    — S8 Impact Measurement Methodology, Step 1 (Global Scope)

Window: **incentive start -> incentive end**. The methodology's literal text says
Start = "Date of Actual Grant Delivery", but that term was flagged as ambiguous
in the governance discussion (GFXlabs, Jan 2026), which recommended anchoring to
when the grantee "officially began their grant plan execution". We adopt that
reading: incentive start = execution start. This is a deliberate, disclosed
choice, not the literal-delivery default.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd
import requests

SHEET_ID = "16zihpXzVmE0q8SweTMS57Va87VejqJh7h2Zm5104JM0"
GVIZ_URL = ("https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
            "?tqx=out:csv&sheet={tab}")

# Registry chain labels -> DefiLlama `chainTvls` keys.
CHAIN_TO_DEFILLAMA = {
    "OP Mainnet": "Optimism",
    "Optimism": "Optimism",
    "Base": "Base",
    "Unichain": "Unichain",
    "Ink": "Ink",
    "Soneium": "Soneium",
}


def _read_tab(tab: str) -> pd.DataFrame:
    """Read one sheet tab as a DataFrame.

    Tabs may carry totals/notes rows above the real header (the `grantees` tab
    does), and gviz picks its own header row, so we locate the header by finding
    the line that contains `grant_id` rather than assuming a fixed position.
    """
    url = GVIZ_URL.format(sheet_id=SHEET_ID, tab=tab)
    text = requests.get(url, timeout=60).text
    if text.lstrip().startswith("<"):
        raise SystemExit(
            f"Tab '{tab}' returned HTML, not CSV. Set the sheet to "
            f"'Anyone with the link can view', or check the tab name."
        )
    lines = text.splitlines()
    header_row = next(
        (i for i, line in enumerate(lines) if "grant_id" in line.lower()), None
    )
    if header_row is None:
        raise SystemExit(f"No 'grant_id' column found in tab '{tab}'.")
    frame = pd.read_csv(io.StringIO("\n".join(lines[header_row:])), dtype=str)
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def _cell(row, column):
    """Fetch a cell, tolerating a missing column, returning None for blanks."""
    try:
        value = row[column]
    except (KeyError, IndexError):
        return None
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _to_number(value):
    """'3,500,000' / '$200,000' -> float. Blanks and NULL -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or text.upper() in {"NULL", "NA", "REQUESTED"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_date(value):
    """Parse the registry's M/D/YYYY (and a couple of variants) into a date."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class GrantConfig:
    """Everything the pipeline needs about one grant, drawn from the registry."""

    grant_id: str
    grantee: str
    defillama_slug: str
    budget_op: float | None
    target_milestone1: float | None
    target_total: float | None

    # registry chain labels and their DefiLlama keys (Global Scope: chains only)
    chains: list[str]
    defillama_chains: list[str]

    # window (incentive execution period)
    incentive_start: date
    snapshot: date | None
    incentive_end: date

    # attribution (S8 Steps 3-4). 40acres has no co-incentives -> 100%, uncapped.
    attribution_pct: float = 100.0
    attribution_cap_usd_per_op: float | None = None
    co_incentives: str | None = None

    # optional on-chain verification targets (registry `scope` tab)
    scope_contracts: list[dict] = field(default_factory=list)

    @property
    def stickiness_end(self) -> date:
        """Incentive end + 30 days, for the supplementary retention metric."""
        return self.incentive_end + pd.Timedelta(days=30).to_pytimedelta()


def load_grant(grant_id: str) -> GrantConfig:
    """Build a GrantConfig for `grant_id` from the live registry."""
    grantees = _read_tab("grantees")
    match = grantees[grantees["grant_id"].astype(str).str.strip() == grant_id]
    if match.empty:
        raise SystemExit(f"Grant {grant_id} not found in the grantees tab.")
    g = match.iloc[0]

    windows = _read_tab("windows")
    wmatch = windows[windows["grant_id"].astype(str).str.strip() == grant_id]
    if wmatch.empty:
        raise SystemExit(f"Grant {grant_id} has no row in the windows tab.")
    w = wmatch.iloc[0]

    scope = _read_tab("scope")
    # The registry's contract-address column has been renamed at least once
    # already (plain "contract_address" -> "contract_address / pool_id", once
    # the scope tab started holding v4-style 32-byte PoolIds alongside normal
    # addresses) — match by prefix so a hand-edited header tweak doesn't
    # silently zero out every grant's Targeted Scope the way an exact-name
    # lookup just did.
    addr_cols = [c for c in scope.columns if c.strip().lower().startswith("contract_address")]
    if len(addr_cols) != 1:
        raise SystemExit(
            f"Expected exactly one 'contract_address...' column in the scope "
            f"tab, found {addr_cols}. Fix the column header or this lookup."
        )
    addr_col = addr_cols[0]
    smatch = scope[scope["grant_id"].astype(str).str.strip() == grant_id]
    scope_contracts = []
    for _, r in smatch.iterrows():
        addr = _cell(r, addr_col)
        if isinstance(addr, str) and addr.strip():
            scope_contracts.append({
                "chain": str(_cell(r, "chain") or "").strip(),
                "address": addr.strip().lower(),
                "pool": str(_cell(r, "pool") or "").strip(),
                "type": str(_cell(r, "type") or "").strip(),
            })

    chains = [c.strip() for c in str(_cell(g, "chains") or "").split(",") if c.strip()]
    unmapped = [c for c in chains if c not in CHAIN_TO_DEFILLAMA]
    if unmapped:
        raise SystemExit(
            f"Chain(s) {unmapped} are not in CHAIN_TO_DEFILLAMA — add them."
        )

    incentive_start = _to_date(_cell(w, "incentive_start_date"))
    incentive_end = _to_date(_cell(w, "incentive_end_date"))
    if not incentive_start or not incentive_end:
        raise SystemExit(
            f"Grant {grant_id} is missing incentive_start_date or "
            f"incentive_end_date in the windows tab."
        )

    slug = str(_cell(g, "defillama_slug") or "").strip()
    if not slug:
        raise SystemExit(
            f"Grant {grant_id} has no defillama_slug. This TVL pipeline needs "
            f"one; fee-metric grants use a different path."
        )

    return GrantConfig(
        grant_id=grant_id,
        grantee=str(_cell(g, "grantee") or grant_id).strip(),
        defillama_slug=slug,
        budget_op=_to_number(_cell(g, "budget_total_op")),
        target_milestone1=_to_number(_cell(g, "target_milestone1")),
        target_total=_to_number(_cell(g, "target_total")),
        chains=chains,
        defillama_chains=[CHAIN_TO_DEFILLAMA[c] for c in chains],
        incentive_start=incentive_start,
        snapshot=_to_date(_cell(w, "snapshot")),
        incentive_end=incentive_end,
        co_incentives=str(_cell(g, "coincentives") or "").strip() or None,
        scope_contracts=scope_contracts,
    )
