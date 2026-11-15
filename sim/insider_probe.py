"""
INSIDER DATA CHECK — read-only test, never trades or saves anything.
Answers: for our US + Canadian universe, how many companies have insider
open-market PURCHASES in recent weeks, according to Yahoo Finance?

Run it with:  python -m sim.insider_probe
On GitHub:    Actions -> "Insider data check (no trading)" -> Run workflow
"""
import re
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from .agent_gather import YahooProvider

SELL = re.compile(r"\b(sale|sold|sell)\b", re.I)


def classify(row):
    text = f"{row.get('Text', '')} {row.get('Transaction', '')}"
    if re.search(r"\bpurchase\b|\bbuy\b|\bbought\b", text, re.I):
        return "buy"
    if SELL.search(text):
        return "sell"
    return "other"


def main():
    import yfinance as yf
    uni = YahooProvider().get_universe()
    now = pd.Timestamp(datetime.utcnow().date())
    rows, raw_examples, errors = [], {}, 0
    t0 = time.time()
    for i, (t, mkt) in enumerate(zip(uni["ticker"], uni["market"])):
        try:
            df = yf.Ticker(t).insider_transactions
        except Exception:
            df, errors = None, errors + 1
        n_rows = 0 if df is None else len(df)
        rec = {"ticker": t, "market": mkt, "rows": n_rows, "last_date": None,
               "buys_90d": 0, "buy_value_90d": 0.0, "buyers_90d": 0, "sells_90d": 0}
        if n_rows:
            df = df.copy()
            date_col = "Start Date" if "Start Date" in df else df.columns[-2]
            df["d"] = pd.to_datetime(df[date_col], errors="coerce")
            df["kind"] = df.apply(classify, axis=1)
            rec["last_date"] = df["d"].max()
            recent = df[df["d"] >= now - timedelta(days=90)]
            b = recent[recent["kind"] == "buy"]
            rec["buys_90d"] = len(b)
            rec["buy_value_90d"] = float(pd.to_numeric(b.get("Value"), errors="coerce").fillna(0).sum()) if len(b) else 0.0
            rec["buyers_90d"] = b["Insider"].nunique() if "Insider" in b and len(b) else 0
            rec["sells_90d"] = int((recent["kind"] == "sell").sum())
            if mkt not in raw_examples:
                raw_examples[mkt] = df.head(3).drop(columns=["URL"], errors="ignore").to_string()
            if len(b) and mkt + " buys" not in raw_examples:
                raw_examples[mkt + " buys"] = b.head(3).drop(columns=["URL"], errors="ignore").to_string()
        rows.append(rec)
        if i % 100 == 0:
            print(f"[probe] {i}/{len(uni)}  ({time.time() - t0:.0f}s)")
        time.sleep(0.2)

    r = pd.DataFrame(rows)
    print(f"\n[probe] done in {time.time() - t0:.0f}s, {errors} request errors\n")
    for mkt in ("US", "CA"):
        g = r[r["market"] == mkt]
        has = g[g["rows"] > 0]
        fresh = has[pd.to_datetime(has["last_date"]) >= now - timedelta(days=90)]
        print(f"[probe] {mkt}: {len(g)} tickers | any insider data: {len(has)} "
              f"| data from last 90 days: {len(fresh)} | with BUYS in 90 days: {(g['buys_90d'] > 0).sum()} "
              f"| with 2+ distinct buyers: {(g['buyers_90d'] >= 2).sum()}")
        if len(has):
            print(f"        most recent insider entry seen: {pd.to_datetime(has['last_date']).max()}")
        top = g.sort_values(["buyers_90d", "buy_value_90d"], ascending=False).head(15)
        print(top[["ticker", "buys_90d", "buyers_90d", "buy_value_90d", "sells_90d", "last_date"]].to_string(index=False))
        print()
    for k, v in raw_examples.items():
        print(f"[probe] raw sample ({k}):\n{v}\n")

    # Fallback sources: are they reachable from GitHub's machines?
    for name, url, hdr in [
        ("SEC EDGAR (US Form 4)", "https://data.sec.gov/submissions/CIK0000320193.json",
         {"User-Agent": "stock-sim paper-trading experiment research-contact@example.com"}),
        ("SEDI (Canada)", "https://www.sedi.ca/sedi/SVTWeeklySummaryACL?name=W1ALLPDFI&locale=en_CA",
         {"User-Agent": "Mozilla/5.0"}),
    ]:
        try:
            resp = requests.get(url, headers=hdr, timeout=20)
            print(f"[probe] {name}: HTTP {resp.status_code}, {len(resp.content)} bytes, type {resp.headers.get('content-type')}")
        except Exception as e:
            print(f"[probe] {name}: failed ({e})")


if __name__ == "__main__":
    main()
