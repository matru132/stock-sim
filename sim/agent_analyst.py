"""
AGENT 2 — ANALYST
Turns the raw facts into decisions-ready information:
  1. rank()            -> the weekly ranked list (score for every eligible stock)
  2. portfolio_report() -> where each portfolio stands: value, weights, gains,
                           and the current rank of every holding
No buying or selling happens here.
"""
import re

import pandas as pd

from . import config as C


def rank(fundamentals: pd.DataFrame, universe: pd.DataFrame, last_close: pd.Series, fx: float):
    """Score every stock that passes the filters. Returns a DataFrame sorted best-first,
    with columns: rank, ticker, name, market, currency, close, rec_mean, n_analysts,
    target_mean, upside, score, in_top (bool)."""
    df = fundamentals.merge(universe, on="ticker", how="inner")
    df["close"] = df["ticker"].map(last_close)

    # Currency sanity: US stocks must quote in USD, TSX stocks in CAD.
    expected = df["market"].map({"US": "USD", "CA": "CAD"})
    df["currency"] = df["currency"].where(df["currency"] != "", expected)
    df = df[df["currency"] == expected].copy()

    for col in ("rec_mean", "n_analysts", "target_mean", "market_cap", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["market_cap_cad"] = df["market_cap"] * df["currency"].map({"USD": fx, "CAD": 1.0})
    min_analysts = df["market"].map({"US": C.MIN_ANALYSTS_US, "CA": C.MIN_ANALYSTS_CA})

    ok = (
        df["rec_mean"].between(1, 5)
        & (df["target_mean"] > 0)
        & (df["close"] > 0)
        & (df["n_analysts"] >= min_analysts)
        & (df["market_cap_cad"] >= C.MIN_MARKET_CAP_CAD)
    )
    df = df[ok].copy()
    if df.empty:
        return df

    df["upside"] = df["target_mean"] / df["close"] - 1
    df["upside_capped"] = df["upside"].clip(upper=C.UPSIDE_CAP)
    df["rating"] = 5 - df["rec_mean"]                     # higher = more bullish

    # Percentile ranks put both signals on the same 0–1 scale before blending.
    df["score"] = (C.WEIGHT_RATING * df["rating"].rank(pct=True)
                   + C.WEIGHT_UPSIDE * df["upside_capped"].rank(pct=True))

    df = df.sort_values(["score", "n_analysts"], ascending=[False, False]).reset_index(drop=True)
    df["rank"] = df.index + 1
    df["in_top"] = df["rank"] <= C.TOP_N
    cols = ["rank", "ticker", "name", "market", "currency", "sector", "close", "rec_mean",
            "n_analysts", "target_mean", "upside", "score", "in_top"]
    return df[cols]


def targets_for(ranking: pd.DataFrame, universe: str, sector_cap=None):
    """The tickers a strategy should hold right now, best-ranked first.
    With sector_cap, walk down the ranking and skip a stock once its sector
    already has `sector_cap` picks."""
    r = ranking if universe == "mix" else ranking[ranking["market"] == "CA"]
    if not sector_cap:
        return list(r["ticker"].head(C.N_HOLD))
    picks, count = [], {}
    sectors = r["sector"].fillna("Unknown") if "sector" in r else pd.Series("Unknown", index=r.index)
    for t, s in zip(r["ticker"], sectors):
        if count.get(s, 0) < sector_cap:
            picks.append(t)
            count[s] = count.get(s, 0) + 1
            if len(picks) == C.N_HOLD:
                break
    return picks


# Discretionary open-market purchases only. Yahoo's wording:
#   US (SEC Form 4, code P):  "Purchase at price 249.81 per share."
#   Canada (SEDI):            "Acquisition in the public market at price 7.55 per share."
# Not counted: automatic employee plans ("Acquisition under a purchase/ownership
# plan"), option exercises, grants, gifts, private placements, and sales.
_OPEN_MARKET_BUY = re.compile(r"^\s*(?:purchase at price|acquisition in the public market)", re.I)
_NOT_AN_INSIDER = re.compile(r"10%|beneficial owner|^issuer$", re.I)   # "Director of Issuer" is fine; the issuer itself is not
_PRICE = re.compile(r"price\s+([\d,]*\.?\d+)", re.I)


def filter_insider_buys(raw: pd.DataFrame, as_of, window_days=None, min_value=None):
    """Keep only meaningful purchases by executives/directors in the window.
    Returns columns: ticker, date, insider, position, value."""
    window_days = window_days or C.INSIDER_WINDOW_DAYS
    min_value = C.INSIDER_MIN_VALUE if min_value is None else min_value
    cols = ["ticker", "date", "insider", "position", "value"]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)
    df = raw.copy()
    text = df.get("Text", pd.Series("", index=df.index)).fillna("").astype(str)
    pos = df.get("Position", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    df["date"] = pd.to_datetime(df.get("Start Date"), errors="coerce")
    shares = pd.to_numeric(df.get("Shares"), errors="coerce")
    value = pd.to_numeric(df.get("Value"), errors="coerce")
    px = pd.to_numeric(text.str.extract(_PRICE)[0].str.replace(",", ""), errors="coerce")
    df["value"] = value.where(value > 0, shares * px)          # some rows only give price x shares
    as_of = pd.Timestamp(as_of)
    keep = (text.str.contains(_OPEN_MARKET_BUY)
            & ~pos.str.contains(_NOT_AN_INSIDER)
            & df["date"].between(as_of - pd.Timedelta(days=window_days), as_of)
            & (df["value"] >= min_value))
    out = df[keep].assign(insider=df.get("Insider", "").astype(str).str.strip(), position=pos)
    return out[cols].reset_index(drop=True)


def insider_picks(buys: pd.DataFrame, universe: pd.DataFrame, prices: dict, which: str, fx: float):
    """Rank companies by insider buying: most distinct buyers first, then most CAD
    bought. Returns a DataFrame (best first) with rank, ticker, market, buyers,
    value_cad, last_buy, n_buys. Only stocks in our universe with a price count."""
    cols = ["rank", "ticker", "market", "buyers", "value_cad", "last_buy", "n_buys"]
    if buys is None or buys.empty:
        return pd.DataFrame(columns=cols)
    b = buys.merge(universe, on="ticker", how="inner")
    if which == "ca":
        b = b[b["market"] == "CA"]
    b = b[b["ticker"].isin([t for t in prices])]
    if b.empty:
        return pd.DataFrame(columns=cols)
    b = b.assign(value_cad=b["value"] * b["market"].map({"US": fx, "CA": 1.0}))
    g = (b.groupby(["ticker", "market"])
          .agg(buyers=("insider", "nunique"), value_cad=("value_cad", "sum"),
               last_buy=("date", "max"), n_buys=("insider", "size"))
          .reset_index()
          .sort_values(["buyers", "value_cad"], ascending=[False, False])
          .reset_index(drop=True))
    g["rank"] = g.index + 1
    g["last_buy"] = g["last_buy"].dt.strftime("%Y-%m-%d")
    return g[cols]


def insider_weights(picks: pd.DataFrame, fill: dict):
    """Each picked stock gets 1/N_HOLD of the portfolio; whatever is left goes to
    the fill ETFs. Returns {ticker: weight} summing to 1."""
    stocks = list(picks["ticker"].head(C.N_HOLD))
    w = {t: 1.0 / C.N_HOLD for t in stocks}
    rest = 1.0 - len(stocks) / C.N_HOLD
    if rest > 1e-9:
        for t, share in fill.items():
            w[t] = w.get(t, 0.0) + rest * share
    return w


def price_cad(ticker, prices, fx, ccy):
    return prices[ticker] * (fx if ccy == "USD" else 1.0)


def portfolio_value(p, prices, fx):
    v = p["cash"]["CAD"] + p["cash"]["USD"] * fx
    for t, pos in p["positions"].items():
        v += pos["shares"] * price_cad(t, prices, fx, pos["ccy"])
    return v


def portfolio_report(p, prices, fx, ranking, names):
    total = portfolio_value(p, prices, fx)
    rank_mix = dict(zip(ranking["ticker"], ranking["rank"])) if len(ranking) else {}
    ca = ranking[ranking["market"] == "CA"] if len(ranking) else ranking
    rank_ca = {t: i + 1 for i, t in enumerate(ca["ticker"])} if len(ranking) else {}
    rank_map = rank_ca if p.get("universe") == "ca" else rank_mix

    holdings = []
    for t, pos in p["positions"].items():
        val = pos["shares"] * price_cad(t, prices, fx, pos["ccy"])
        holdings.append({
            "ticker": t,
            "name": names.get(t, t),
            "ccy": pos["ccy"],
            "shares": round(pos["shares"], 4),
            "price": round(prices[t], 4),
            "value_cad": round(val, 2),
            "weight": round(val / total, 4) if total else 0,
            "gain_pct": round(val / pos["cost_cad"] - 1, 4) if pos["cost_cad"] > 0 else None,
            "rank": rank_map.get(t),
        })
    holdings.sort(key=lambda h: -h["value_cad"])
    return total, holdings
