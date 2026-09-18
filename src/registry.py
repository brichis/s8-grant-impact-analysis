"""Registry reader.

Loads one grant's configuration from the S8 grants registry (a Google Sheet
exported live as CSV). The registry is the only hand-maintained input; every
other number in the pipeline is fetched from a public source.

This module serves both scopes, so it reads what each needs: the `scope` tab's
per-contract rows for Targeted Scope (the default), and the grant's chain list
for Global Scope (`--global`). Which one a run uses is decided in run.py, not
here.

Window: **incentive start -> incentive end**. The methodology's literal text says
Start = "Date of Actual Grant Delivery", but that term was flagged as ambiguous
in the governance discussion (GFXlabs, Jan 2026), which recommended anchoring to
when the grantee "officially began their grant plan execution". We adopt that
reading: incentive start = execution start. This is a deliberate, disclosed
choice, not the literal-delivery default.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import quote

import pandas as pd
import requests

from chains import canonical, known

SHEET_ID = "16zihpXzVmE0q8SweTMS57Va87VejqJh7h2Zm5104JM0"
GVIZ_URL = ("https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
            "?tqx=out:csv&sheet={tab}")


def _fetch_rows(tab: str, headers: int | None) -> list[list[str]]:
    """One gviz CSV fetch, parsed into rows. `headers` maps to gviz's own
    `headers` parameter; None omits it (gviz then guesses)."""
    url = GVIZ_URL.format(sheet_id=SHEET_ID, tab=quote(tab))
    if headers is not None:
        url += f"&headers={headers}"
    text = requests.get(url, timeout=60).text
    if text.lstrip().startswith("<"):
        raise SystemExit(
            f"Tab '{tab}' returned HTML, not CSV. Set the sheet to "
            f"'Anyone with the link can view', or check the tab name."
        )
    return list(csv.reader(io.StringIO(text)))


def _read_tab(tab: str) -> pd.DataFrame:
    """Read one sheet tab as a DataFrame, working around two gviz behaviours
    that each silently corrupt the result in a different direction.

    1. Left to guess, gviz decides for itself how many leading rows are header
       and *space-joins* them into the column labels. It guessed 10 for the
       `scope` tab (the first nine rows have an empty `pool_details`), folding
       nine real contracts into the header — unrecoverably, since the join is
       by space and values like "OP Mainnet" contain one. Passing `headers=1`
       states there is exactly one header row and stops the guessing.

    2. gviz also infers a type per column and blanks any cell that doesn't
       match it — including the header text above a numeric or date column.
       Under `headers=1` that costs `grantees` the labels for
       budget_total_op / target_milestone1 / target_total and `windows` its
       date columns, which `_cell` would then read as absent, i.e. None,
       without complaint. The data rows themselves are unaffected.

    So: rows come from the `headers=1` fetch, and any label it blanked is
    recovered from the default fetch, which types nothing away. A recovered
    label is only trusted if it contains no space — that is exactly what
    distinguishes a real column name from one of case 1's folded labels.
    """
    rows = _fetch_rows(tab, headers=1)
    if not rows:
        raise SystemExit(f"Tab '{tab}' came back empty.")
    labels = [c.strip() for c in rows[0]]
    data = rows[1:]

    if not all(labels):
        for i, label in enumerate(_fetch_rows(tab, headers=None)[0]):
            label = label.strip()
            if i < len(labels) and not labels[i] and label and " " not in label:
                labels[i] = label
    if not all(labels):
        raise SystemExit(
            f"Tab '{tab}' has unnamed column(s) at position(s) "
            f"{[i for i, l in enumerate(labels) if not l]} — {labels}. Give "
            f"every column a header; a blank one reads as a missing column."
        )

    width = len(labels)
    bad = [i for i, r in enumerate(data, 2) if len(r) != width]
    if bad:
        raise SystemExit(
            f"Tab '{tab}': row(s) {bad[:5]} have a different column count than "
            f"the {width} headers — the export is malformed."
        )
    frame = pd.DataFrame(data, columns=labels, dtype=str)
    # gviz gives an absent cell as ""; the rest of this module tests for None.
    # Dict form: on pandas 2.0/2.1 the scalar form replace("", None) means
    # "forward-fill" instead, and a blank incentive_end_date inheriting the
    # row above's date would turn a still-running grant into a finished one.
    return frame.replace({"": None})


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
    # None when the windows tab's incentive_end_date is blank, which is how the
    # registry records "still running, no end announced" — see incentive_ongoing.
    incentive_end: date | None

    # optional on-chain verification targets (registry `scope` tab)
    scope_contracts: list[dict] = field(default_factory=list)

    @property
    def stickiness_end(self) -> date | None:
        """Incentive end + 30 days, for the supplementary retention metric.
        None while the incentive has no end date — there is no window to
        measure 30 days past yet."""
        if self.incentive_end is None:
            return None
        return self.incentive_end + pd.Timedelta(days=30).to_pytimedelta()

    @property
    def incentive_ongoing(self) -> bool:
        """True when the incentive has not demonstrably closed.

        A blank incentive_end_date is the registry's way of saying "still
        running, no end announced" — the honest state for a grant whose team
        never published an end date. It is deliberately NOT filled with a
        placeholder such as today's date: that has to be re-edited daily, and
        the moment it goes stale the grant silently reads as finished and
        publishes a final measurement of a still-running program.

        A future end date (a real, announced schedule) also counts as ongoing.
        Either way the grant is measured as interim — see `measurement_end`."""
        return self.incentive_end is None or self.incentive_end >= date.today()

    @property
    def window_label(self) -> str:
        """The window as reported in scorecards. Says plainly when a result is
        interim and why, so a still-running grant can never be mistaken for a
        closed one — and never quotes an end date the grantee hasn't given."""
        label = f"{self.incentive_start} -> {self.measurement_end}"
        if not self.incentive_ongoing:
            return label
        reason = (f"scheduled end {self.incentive_end}" if self.incentive_end
                  else "no end date announced")
        return f"{label} (incentive ongoing, {reason})"

    @property
    def measurement_end(self) -> date:
        """The date the `end` checkpoint is actually read at: normally the
        incentive end, but for a still-running grant the last fully-elapsed
        UTC day — a complete-day snapshot at a block that exists and DefiLlama
        has a price for, instead of a partial current day that rpc.block_at
        would silently clamp to chain head. See metrics.py:
        end = min(incentive end, this)."""
        if self.incentive_ongoing:
            last_elapsed = date.today() - timedelta(days=1)
            return min(last_elapsed, INTERIM_CUTOFF) if INTERIM_CUTOFF else last_elapsed
        return self.incentive_end


# Interim grants (still running, no announced end) were measured to a fixed
# date instead of "yesterday", so the published review stops moving and any
# re-run reproduces it: every figure quoted for Curve Lending and Velodrome is
# as of this date. A later review can move the cutoff forward (or set it to
# None to track the chain head again), which re-reads those grants only.
INTERIM_CUTOFF = date(2026, 9, 15)


def load_scope(grant_id: str) -> list[dict]:
    """The registry `scope` rows for one grant, parsed.

    Separate from load_grant because a grant can need its scope rows without a
    full GrantConfig: Oku has no `defillama_slug` (load_grant refuses without
    one) yet still keeps the vault it routes into in the scope tab, like every
    other measured contract.
    """
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
    rows = []
    for _, r in smatch.iterrows():
        addr = _cell(r, addr_col)
        if not (isinstance(addr, str) and addr.strip()):
            continue
        # token0/token1 are the measured token symbols, one per column, so no
        # label parsing is needed: the registry used to pack them into a single
        # `pool` cell ("CL100-USDC/WETH", "EURC-USDC 0.01%") which had to be
        # un-decorated with regexes before it could be split. `pool_details`
        # carries the tick-spacing/fee tag that used to be embedded, and is
        # never parsed — it only makes otherwise-identical pools on the same
        # chain distinguishable in output labels.
        token0 = str(_cell(r, "token0") or "").strip()
        token1 = str(_cell(r, "token1") or "").strip()
        # A third token column, for pools that hold more than a pair (Curve's
        # tricrypto pools). Optional: the tab predates it, and every two-sided
        # pool leaves it blank.
        token2 = str(_cell(r, "token2") or "").strip() if "token2" in smatch.columns else ""
        details = str(_cell(r, "pool_details") or "").strip()
        scope_id = str(_cell(r, "scope_id") or "?").strip()
        if not token0:
            raise SystemExit(
                f"Scope row {scope_id} ({addr.strip()}) has no token0. Every "
                f"row needs the symbol of the token being measured; a blank "
                f"one silently measures nothing at all."
            )
        rows.append({
            "chain": str(_cell(r, "chain") or "").strip(),
            "address": addr.strip().lower(),
            "token0": token0,
            "token1": token1,
            "token2": token2,
            "details": details,
            # display only — charts and console lines
            "label": " ".join(x for x in (
                "/".join(t for t in (token0, token1) if t), details) if x),
            "type": str(_cell(r, "type") or "").strip(),
        })
    return rows


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

    scope_contracts = load_scope(grant_id)

    chains = [c.strip() for c in str(_cell(g, "chains") or "").split(",") if c.strip()]
    unmapped = [c for c in chains if not known(c)]
    if unmapped:
        raise SystemExit(f"Chain(s) {unmapped} are not in chains.py — add them.")

    incentive_start = _to_date(_cell(w, "incentive_start_date"))
    if not incentive_start:
        raise SystemExit(
            f"Grant {grant_id} is missing incentive_start_date in the windows "
            f"tab. The window has to start somewhere; there is no sane default."
        )
    # A blank end date is meaningful, not missing: the incentive is still
    # running with no announced end (see GrantConfig.incentive_ongoing).
    incentive_end = _to_date(_cell(w, "incentive_end_date"))

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
        defillama_chains=[canonical(c) for c in chains],
        incentive_start=incentive_start,
        snapshot=_to_date(_cell(w, "snapshot")),
        incentive_end=incentive_end,
        scope_contracts=scope_contracts,
    )
