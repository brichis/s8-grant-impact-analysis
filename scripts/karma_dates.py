#!/usr/bin/env python3
"""Application dates for every grant, from Karma's API.

The registry has no approval date — `initial_delivery_date` is the day the OP
reached the Hedgey claim contract, which happens weeks after the decision — and
the cycle reports only date a whole cycle at once. Karma's funding-application
record carries the real thing: when the application was created and every
status change it went through, including the approval.

Only the dates and statuses are kept. The application payload also contains the
applicant's email and address, which this review has no use for, so they are
never written to disk.

Set KARMA_API_KEY in .env (never in code). Output: data/karma_dates.json, which
cohort_summary.py reads.

Usage:
  python3 scripts/karma_dates.py [--out data/karma_dates.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "src"))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

import registry  # noqa: E402

API = "https://api.karmahq.org/v2/funding-applications/{app_id}"
KEEP_STATUSES = ("pending", "under_review", "revision_requested", "approved", "rejected")


def _redact(text: str, key: str) -> str:
    return re.sub(re.escape(key), "<redacted>", str(text)) if key else str(text)


def fetch(app_id: str, key: str) -> dict | None:
    """The application's dates, or None if Karma doesn't have that reference."""
    req = urllib.request.Request(API.format(app_id=app_id),
                                 headers={"x-api-key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise SystemExit(f"Karma API error for {app_id}: {_redact(exc, key)}") from None
    except Exception as exc:                                  # noqa: BLE001
        raise SystemExit(f"Karma API error for {app_id}: {_redact(exc, key)}") from None

    history = [{"status": h.get("status"), "timestamp": h.get("timestamp")}
               for h in (payload.get("statusHistory") or []) if h.get("timestamp")]
    history.sort(key=lambda h: h["timestamp"])
    first = {}
    for h in history:                                         # first time it entered each status
        first.setdefault(h["status"], h["timestamp"][:10])
    return {
        "reference": payload.get("referenceNumber"),
        "project": payload.get("resolvedProjectName"),
        "status": payload.get("status"),
        "created": (payload.get("createdAt") or "")[:10] or None,
        "status_history": history,
        **{f"{s}_at": first.get(s) for s in KEEP_STATUSES},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/karma_dates.json")
    args = ap.parse_args()

    key = os.environ.get("KARMA_API_KEY")
    if not key:
        raise SystemExit("KARMA_API_KEY is not set — add it to .env (see the module docstring).")

    grantees = registry._read_tab("grantees")
    out: dict[str, dict] = {}
    missing: list[str] = []
    for row in grantees.itertuples():
        app_id = str(row.grant_id).strip()
        if not app_id.startswith("APP-"):
            continue
        record = fetch(app_id, key)
        if record is None:
            missing.append(f"{row.grantee} ({app_id})")
            continue
        record["grantee"] = row.grantee
        record["cycle"] = str(row.cycle).strip()
        out[app_id] = record
        print(f"  {row.grantee[:28]:<29} created {record['created']} · "
              f"approved {record.get('approved_at') or '—'} · status {record['status']}")
        time.sleep(0.2)

    path = BASE / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"\n{len(out)} applications -> {path}")
    if missing:
        print(f"not found in Karma ({len(missing)}): {', '.join(missing)}")


if __name__ == "__main__":
    main()
