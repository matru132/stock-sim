"""
AGENT 2 — ANALYST
Turns the raw facts into decisions-ready information:
  1. rank()            -> the weekly ranked list (score for every eligible stock)
  2. portfolio_report() -> where each portfolio stands: value, weights, gains,
                           and the current rank of every holding
No buying or selling happens here.
"""
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


def targets_for(ranking: pd.DataFrame, universe: str):
    """The tickers a strategy should hold right now, best-ranked first."""
    r = ranking if universe == "mix" else ranking[ranking["market"] == "CA"]
    return list(r["ticker"].head(C.N_HOLD))


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
