"""
AGENT 1 — GATHERER
Collects the raw facts each week: which companies are in the S&P 500 and the
S&P/TSX Composite, what analysts say about each one, and closing prices.

All internet access lives in this file, behind the YahooProvider class. The
tests swap in a fake provider with the same three methods, so the rest of the
system never knows (or cares) where the data came from.
"""
import io
import time
from pathlib import Path

import pandas as pd
import requests

UNIVERSE_FILE = Path("data/universe.csv")
WIKI_SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKI_TSX = "https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index"
HEADERS = {"User-Agent": "Mozilla/5.0 (paper-trading-experiment)"}


def _read_tables(url):
    html = requests.get(url, headers=HEADERS, timeout=30).text
    return pd.read_html(io.StringIO(html))


def _find_symbol_column(tables, min_rows):
    for t in tables:
        if len(t) < min_rows:
            continue
        for col in t.columns:
            if str(col).strip().lower() in ("symbol", "ticker", "ticker symbol"):
                return clean_symbols(t[col])
    raise ValueError("No ticker column found")


def clean_symbols(col):
    """Drop blank cells and anything that doesn't look like a ticker
    (pandas 3 keeps blank cells as NaN even after astype(str))."""
    s = col.dropna().map(lambda v: str(v).strip())
    s = s[s.str.fullmatch(r"(TSX:\s*)?[A-Z0-9][A-Z0-9.\-]*")]      # real tickers are upper-case
    return s.drop_duplicates().reset_index(drop=True)


def to_yahoo_us(sym):
    return sym.strip().replace(".", "-")                 # BRK.B -> BRK-B


def to_yahoo_ca(sym):
    sym = sym.strip().upper().replace("TSX:", "").strip()
    return sym.replace(".", "-") + ".TO"                 # RCI.B -> RCI-B.TO


class YahooProvider:
    """Real data: Wikipedia for index members, Yahoo Finance for everything else."""

    def get_universe(self):
        try:
            us = _find_symbol_column(_read_tables(WIKI_SP500), 400).map(to_yahoo_us)
            ca = _find_symbol_column(_read_tables(WIKI_TSX), 150).map(to_yahoo_ca)
            uni = pd.concat([
                pd.DataFrame({"ticker": us, "market": "US"}),
                pd.DataFrame({"ticker": ca, "market": "CA"}),
            ]).drop_duplicates("ticker").reset_index(drop=True)
            UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
            uni.to_csv(UNIVERSE_FILE, index=False)
            print(f"[gather] universe: {len(us)} US + {len(ca)} Canadian tickers")
            return uni
        except Exception as e:                           # Wikipedia down or page changed
            if UNIVERSE_FILE.exists():
                print(f"[gather] universe fetch failed ({e}); using last saved list")
                return pd.read_csv(UNIVERSE_FILE)
            raise

    def get_fundamentals(self, tickers):
        """Analyst consensus for each ticker. One request per ticker, with retries."""
        import yfinance as yf
        rows = []
        for i, t in enumerate(tickers):
            info = {}
            for attempt in range(3):
                try:
                    info = yf.Ticker(t).info or {}
                    break
                except Exception:
                    time.sleep(2 * (attempt + 1))
            rows.append({
                "ticker": t,
                "name": info.get("shortName") or info.get("longName") or t,
                "currency": (info.get("currency") or "").upper(),
                "rec_mean": info.get("recommendationMean"),       # 1 = strong buy … 5 = sell
                "n_analysts": info.get("numberOfAnalystOpinions"),
                "target_mean": info.get("targetMeanPrice"),
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector"),
            })
            if i % 50 == 0:
                print(f"[gather] analyst data {i}/{len(tickers)}")
            time.sleep(0.25)                                       # be polite to Yahoo
        return pd.DataFrame(rows)

    def get_prices(self, tickers, start, end):
        """Daily closes and dividends. Returns (closes, dividends) DataFrames,
        indexed by date, one column per ticker."""
        import yfinance as yf
        raw = yf.download(sorted(set(tickers)), start=start, end=end, auto_adjust=False,
                          actions=True, progress=False, threads=True, group_by="column")
        closes = raw["Close"] if "Close" in raw else pd.DataFrame()
        divs = raw["Dividends"] if "Dividends" in raw else pd.DataFrame(index=closes.index)
        if isinstance(closes, pd.Series):                          # single ticker case
            closes = closes.to_frame(tickers[0])
            divs = divs.to_frame(tickers[0])
        closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
        divs.index = pd.to_datetime(divs.index).tz_localize(None).normalize()
        return closes, divs.fillna(0.0)
