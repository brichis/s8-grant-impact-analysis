"""
Scrape Incentra (https://incentra.brevis.network) for PancakeSwap pools on Base
with Ended status. No public backing API exists (growth.brevis.network does not
resolve outside Brevis's own edge network), so this drives a real browser.

Usage:
    pip install playwright
    playwright install chromium
    python scripts/scrape_incentra.py
"""

import csv
import re

from playwright.sync_api import sync_playwright

URL = "https://incentra.brevis.network/"
OUT_CSV = "incentra_pancakeswap_base_ended.csv"


def main():
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(URL, wait_until="networkidle")

        # Filter: Chain -> Base
        page.get_by_text("Chain", exact=True).first.click()
        page.get_by_text("Base", exact=True).click()
        page.keyboard.press("Escape")

        # Filter: Status -> Ended
        page.get_by_text("Status", exact=True).first.click()
        page.get_by_text("Ended", exact=True).click()
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        page_num = 1
        while True:
            page.wait_for_timeout(300)
            row_els = page.locator("text=Protocol and Action").locator(
                "xpath=following::*[.//text()[contains(.,'Provide Liquidity') or contains(.,'Earn') or contains(.,'Lend') or contains(.,'Hold')]]"
            )

            # Simpler: grab every card block by its "Status" label sibling.
            cards = page.locator("div:has(> div > *:text('Status'))")
            texts = page.locator("body").inner_text()
            print(f"--- page {page_num} captured, raw text length {len(texts)} ---")

            for block in re.split(r"\n(?=Provide Liquidity|Earn |Lend or Borrow|Hold )", texts):
                if "PancakeSwap" not in block:
                    continue
                name_m = re.match(r"(.+?)\n", block)
                protocol_m = re.search(r"PancakeSwap (V\d|Infinity)", block)
                chain_m = re.search(r"\n(Base|Ethereum|OP Mainnet|BNB Chain|Linea Mainnet|Arbitrum One)\n", block)
                tvl_m = re.search(r"\$[\d,.]+[KMB]?", block)
                status_m = re.search(r"\n(Active|Ended|Upcoming)\b", block)
                reward_m = re.search(r"([\d.<> ]*\s?OP\s?per\s?hour|OP)", block)

                if not (name_m and chain_m and status_m):
                    continue
                if chain_m.group(1) != "Base" or status_m.group(1) != "Ended":
                    continue

                rows.append(
                    {
                        "pool": name_m.group(1).strip(),
                        "protocol_version": protocol_m.group(0) if protocol_m else "",
                        "chain": chain_m.group(1),
                        "tvl": tvl_m.group(0) if tvl_m else "",
                        "status": status_m.group(1),
                        "reward": reward_m.group(0) if reward_m else "",
                    }
                )

            next_btn = page.get_by_role("button", name="Next Page")
            if next_btn.count() == 0 or not next_btn.first.is_enabled():
                break
            next_btn.first.click()
            page_num += 1
            if page_num > 30:  # safety cap
                break

        browser.close()

    # de-dupe (in case of overlap across paginated reads)
    seen = set()
    deduped = []
    for r in rows:
        key = (r["pool"], r["tvl"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["pool", "protocol_version", "chain", "tvl", "status", "reward"])
        writer.writeheader()
        writer.writerows(deduped)

    print(f"Wrote {len(deduped)} rows to {OUT_CSV}")


if __name__ == "__main__":
    main()
