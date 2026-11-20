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
        tickers = self.us + self.ca + ["SPY", "XIC.TO", "XEQT.TO"]
        # Sectors are lopsided on purpose (a third are "Mining") so the sector cap matters.
        others = ["Tech", "Energy", "Banks", "Industrials", "Utilities", "Retail", "Health"]
        self.sector = {t: ("Mining" if i % 3 == 0 else others[i % len(others)])
                       for i, t in enumerate(self.us + self.ca)}
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
                "sector": self.sector.get(t),
            })
        return pd.DataFrame(rows)

    insider_coverage = 1.0          # tests can lower this to simulate Yahoo not answering

    def get_insider(self, tickers):
        """Yahoo-style insider rows: real open-market buys mixed with things the
        filter must ignore (employee plans, 10% holders, tiny buys, sales)."""
        r = np.random.default_rng(55)
        rows = []
        for i, t in enumerate(tickers):
            ca = t.endswith(".TO")
            buy_txt = ("Acquisition in the public market at price 20.00 per share." if ca
                       else "Purchase at price 20.00 per share.")
            for k in range(int(r.integers(0, 4)) if i % 4 == 0 else 0):     # a quarter of stocks get buys
                rows.append({"ticker": t, "Shares": 5000, "Value": float(r.uniform(30_000, 900_000)),
                             "Text": buy_txt, "Insider": f"Exec {k}", "Position": "Director",
                             "Transaction": "", "Start Date": self.dates[int(r.integers(0, len(self.dates)))]})
            d = self.dates[int(r.integers(0, len(self.dates)))]
            rows += [
                {"ticker": t, "Shares": 900, "Value": 18_000.0, "Text": "Acquisition under a purchase/ownership plan at price 20.00 per share.",
                 "Insider": "Payroll Plan", "Position": "Senior Officer of Issuer", "Transaction": "", "Start Date": d},
                {"ticker": t, "Shares": 90_000, "Value": 1_800_000.0, "Text": buy_txt,
                 "Insider": "Big Fund", "Position": "10% Security Holder of Issuer", "Transaction": "", "Start Date": d},
                {"ticker": t, "Shares": 100, "Value": 2_000.0, "Text": buy_txt,
                 "Insider": "Token Buyer", "Position": "Director", "Transaction": "", "Start Date": d},
                {"ticker": t, "Shares": 50_000, "Value": 1_000_000.0, "Text": "Sale at price 20.00 per share.",
                 "Insider": "Seller", "Position": "Officer", "Transaction": "", "Start Date": d},
            ]
        return pd.DataFrame(rows), self.insider_coverage

    def get_prices(self, tickers, start, end):
        m = (self.dates >= pd.Timestamp(start)) & (self.dates < pd.Timestamp(end))
        cols = [t for t in tickers if t in self.closes]
        return self.closes.loc[m, cols], self.divs.loc[m, cols]
