"""
A fake stock market used to test the simulation without internet.
Same three methods as YahooProvider, but every number is generated.
"""
import numpy as np
import pandas as pd


class FakeProvider:
    def __init__(self, seed=7, n_us=120, n_ca=60, start="2026-10-01", days=200):
        rng = np.random.default_rng(seed)
        self.rng = rng
        self.us = [f"US{i:03d}" for i in range(n_us)]
        self.ca = [f"CA{i:03d}.TO" for i in range(n_ca)]
        self.dates = pd.bdate_range(start, periods=days)
        tickers = self.us + self.ca + ["SPY", "XIC.TO"]
        rets = rng.normal(0.0004, 0.018, size=(days, len(tickers)))
        px = 50 * np.exp(np.cumsum(rets, axis=0)) * rng.uniform(0.5, 4, len(tickers))
        self.closes = pd.DataFrame(px, index=self.dates, columns=tickers)
        self.closes["CAD=X"] = 1.38 + np.cumsum(rng.normal(0, 0.002, days))
        # A few NaNs to imitate holidays / missing data
        self.closes.iloc[10, 5] = np.nan
        self.divs = pd.DataFrame(0.0, index=self.dates, columns=self.closes.columns)
        for t in rng.choice(tickers, 40, replace=False):
            self.divs.loc[self.dates[rng.integers(5, days)], t] = rng.uniform(0.2, 1.0)
        self.week_calls = 0

    def get_universe(self):
        return pd.DataFrame({"ticker": self.us + self.ca,
                             "market": ["US"] * len(self.us) + ["CA"] * len(self.ca)})

    def get_fundamentals(self, tickers):
        # Analyst opinions drift a little every week so the top 30 changes.
        self.week_calls += 1
        r = np.random.default_rng(100 + self.week_calls)
        last = self.closes.ffill().iloc[-1]
        rows = []
        for t in tickers:
            rows.append({
                "ticker": t, "name": f"Company {t}",
                "currency": "CAD" if t.endswith(".TO") else "USD",
                "rec_mean": float(np.clip(r.normal(2.1, 0.5), 1, 5)),
                "n_analysts": int(r.integers(3, 40)),
                "target_mean": float(last.get(t, 50) * r.uniform(0.85, 1.6)),
                "market_cap": float(r.uniform(5e8, 5e11)),
                "sector": "Test",
            })
        return pd.DataFrame(rows)

    def get_prices(self, tickers, start, end):
        m = (self.dates >= pd.Timestamp(start)) & (self.dates < pd.Timestamp(end))
        cols = [t for t in tickers if t in self.closes]
        return self.closes.loc[m, cols], self.divs.loc[m, cols]
