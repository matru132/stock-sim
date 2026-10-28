"""
DATA CHECK — runs the Gatherer and the Analyst on live data, WITHOUT trading
or saving anything. Use it to confirm the data source still works.

Run it with:  python -m sim.dry_run
On GitHub:    Actions -> "Data check (no trading)" -> Run workflow
"""
import sys
from datetime import date, timedelta

import pandas as pd

from . import config as C
from . import agent_analyst as analyst
from .agent_gather import YahooProvider
from .run_weekly import BENCH_TICKERS


def main(provider=None, today=None):
    prov = provider or YahooProvider()
    today = pd.Timestamp(today or date.today()).normalize()

    uni = prov.get_universe()
    n_us, n_ca = int((uni["market"] == "US").sum()), int((uni["market"] == "CA").sum())
    print(f"\n[check] Universe: {n_us} US + {n_ca} Canadian tickers")
    for mkt in ("US", "CA"):
        print(f"        {mkt} sample: {list(uni[uni['market'] == mkt]['ticker'].head(12))}")

    tickers = sorted(set(uni["ticker"]) | set(BENCH_TICKERS) | {C.FX_TICKER})
    closes, _ = prov.get_prices(tickers, (today - timedelta(days=10)).date(), (today + timedelta(days=1)).date())
    last = closes.ffill().iloc[-1]
    fx = float(last[C.FX_TICKER])
    prices = last.dropna()
    prices = prices[prices > 0]
    missing = sorted(set(uni["ticker"]) - set(prices.index))
    print(f"[check] Prices as of {closes.index.max():%Y-%m-%d}: {len(prices)} tickers, "
          f"{len(missing)} missing {missing[:10]}")
    print(f"[check] USD -> CAD: {fx:.4f}   SPY: {last.get('SPY')}   XIC.TO: {last.get('XIC.TO')}")

    fund = prov.get_fundamentals(list(uni["ticker"]))
    f = fund.merge(uni, on="ticker")
    print("\n[check] Analyst data coverage (share of tickers with a value):")
    for mkt in ("US", "CA"):
        g = f[f["market"] == mkt]
        cov = {c: f"{g[c].notna().mean():.0%}" for c in ("rec_mean", "n_analysts", "target_mean", "market_cap")}
        print(f"        {mkt}: {cov}")

    ranking = analyst.rank(fund, uni, prices, fx)
    n_ca_ok = int((ranking["market"] == "CA").sum()) if len(ranking) else 0
    print(f"\n[check] {len(ranking)} stocks pass the filters ({n_ca_ok} Canadian). "
          f"Minimum needed to trade: {C.MIN_ELIGIBLE_TOTAL} ({C.MIN_ELIGIBLE_CA} Canadian)")

    pd.set_option("display.width", 200)
    cols = ["rank", "ticker", "name", "market", "rec_mean", "n_analysts", "upside", "score"]
    if len(ranking):
        print("\n[check] Top 30 — US + Canada:")
        print(ranking[cols].head(C.N_HOLD).to_string(index=False))
        print("\n[check] Top 30 — Canada only:")
        print(ranking[ranking["market"] == "CA"][cols].head(C.N_HOLD).to_string(index=False))

    ok = len(ranking) >= C.MIN_ELIGIBLE_TOTAL and n_ca_ok >= C.MIN_ELIGIBLE_CA
    print("\n[check] RESULT:", "OK — data source works." if ok else "PROBLEM — not enough usable data.")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
