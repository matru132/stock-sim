"""
ORCHESTRATOR — runs the three agents in order, once a week.

    Gatherer  ->  Analyst  ->  Trader  ->  Analyst (report)  ->  save files

Run it with:  python -m sim.run_weekly
GitHub runs this automatically every Saturday (see .github/workflows/weekly.yml).
"""
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import config as C
from . import agent_analyst as analyst
from . import agent_trader as trader
from .agent_gather import YahooProvider

DATA = Path("data")
DOCS = Path("docs")
STATE_FILE = DATA / "state.json"
HISTORY_FILE = DATA / "history.csv"
TRADES_FILE = DATA / "trades.csv"
BENCH_TICKERS = sorted({t for b in C.BENCHMARKS for t in b["weights"]})


def _ccy(ticker):
    return "CAD" if ticker.endswith(".TO") else "USD"


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None


def run(provider=None, today=None):
    provider = provider or YahooProvider()
    today = pd.Timestamp(today or date.today()).normalize()
    state = load_state()
    DATA.mkdir(exist_ok=True)
    (DATA / "rankings").mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)

    # --- 1. GATHER ---
    universe = provider.get_universe()
    held = set()
    if state:
        for p in state["portfolios"]:
            held |= set(p["positions"])
    tickers = sorted(set(universe["ticker"]) | held | set(BENCH_TICKERS) | {C.FX_TICKER})
    since = (pd.Timestamp(state["last_date"]) - timedelta(days=5)) if state else today - timedelta(days=21)
    closes, divs = provider.get_prices(tickers, since.date(), (today + timedelta(days=1)).date())

    # The "as of" date is the latest day with an FX rate and both benchmark prices.
    needed = [c for c in [C.FX_TICKER] + BENCH_TICKERS if c in closes]
    valid = closes[needed].dropna()
    if valid.empty:
        raise RuntimeError("No usable prices returned — data source may be down.")
    as_of = valid.index.max()
    as_of_s = as_of.strftime("%Y-%m-%d")

    # Only end-of-week closes count (Friday, or Thursday when Friday is a holiday).
    # A manual run on a Tuesday therefore does nothing instead of adding a fake week.
    if as_of.weekday() < 3:
        print(f"[run] Latest close is {as_of_s} (mid-week) — waiting for the Friday close.")
        return state

    if state:
        week = round((as_of - pd.Timestamp(state["start_date"])).days / 7)
        if week <= state["last_week"]:
            print(f"[run] Week of {as_of_s} already processed — nothing to do.")
            return state
    else:
        week = 0

    fx = float(closes.loc[as_of, C.FX_TICKER])
    last = closes.loc[:as_of].ffill().iloc[-1]
    prices = {t: float(v) for t, v in last.items() if pd.notna(v) and v > 0}
    if state:                                            # holdings with no fresh price keep their last one
        for t, v in state.get("last_price", {}).items():
            prices.setdefault(t, v)

    fundamentals = provider.get_fundamentals(list(universe["ticker"]))

    # --- 2. ANALYZE (rank) ---
    ranking = analyst.rank(fundamentals, universe, pd.Series(prices), fx)
    n_ca = int((ranking["market"] == "CA").sum()) if len(ranking) else 0
    if len(ranking) < C.MIN_ELIGIBLE_TOTAL or n_ca < C.MIN_ELIGIBLE_CA:
        raise RuntimeError(f"Only {len(ranking)} stocks ({n_ca} Canadian) had usable analyst data — "
                           "data source looks broken this week. Stopping without trading.")
    ranking.to_csv(DATA / "rankings" / f"{as_of_s}.csv", index=False)
    names = dict(state.get("names", {})) if state else {}
    names.update(dict(zip(fundamentals["ticker"], fundamentals["name"])))
    names.setdefault("SPY", "SPDR S&P 500 ETF")
    names.setdefault("XIC.TO", "iShares Core S&P/TSX Capped Composite ETF")
    ccys = {t: _ccy(t) for t in prices}
    print(f"[analyst] {len(ranking)} stocks passed the filters "
          f"({(ranking['market'] == 'CA').sum()} Canadian)")

    # --- 3. TRADE ---
    log = []
    if state is None:
        state = {"start_date": as_of_s, "portfolios":
                 [trader.new_strategy(s) for s in C.STRATEGIES]
                 + [trader.new_benchmark(b) for b in C.BENCHMARKS]}
        new_benchmarks = True
    else:
        new_benchmarks = False
        div_window = divs.loc[(divs.index > pd.Timestamp(state["last_date"])) & (divs.index <= as_of)]
        per_share = div_window.sum().to_dict() if len(div_window) else {}
        for p in state["portfolios"]:
            trader.credit_dividends(p, per_share, as_of_s, log)

    for p in state["portfolios"]:
        if p["kind"] == "strategy":
            if week % p["every_weeks"] == 0:
                targets = [t for t in analyst.targets_for(ranking, p["universe"]) if t in prices]
                trader.rebalance(p, targets, prices, ccys, fx, as_of_s, log)
        elif new_benchmarks:
            trader.setup_benchmark(p, prices, ccys, fx, as_of_s, log)
        else:
            trader.reinvest_benchmark_cash(p, prices, ccys, fx, as_of_s, log)

    # --- 4. REPORT ---
    state.update(last_date=as_of_s, last_week=week, fx=fx, names=names,
                 last_price={t: prices[t] for p in state["portfolios"] for t in p["positions"]})
    rows = []
    for p in state["portfolios"]:
        total, _ = analyst.portfolio_report(p, prices, fx, ranking, names)
        rows.append({"date": as_of_s, "week": week, "portfolio": p["id"], "value_cad": round(total, 2),
                     "cash_cad": round(p["cash"]["CAD"] + p["cash"]["USD"] * fx, 2),
                     "fx_fees_cad": round(p["fees"]["fx"], 2), "n_trades": p["n_trades"],
                     "n_positions": len(p["positions"])})
    _append_csv(HISTORY_FILE, rows)
    _append_csv(TRADES_FILE, log)
    STATE_FILE.write_text(json.dumps(state, indent=1))
    write_dashboard_data(state, prices, fx, ranking, names, week)
    print(f"[run] Week {week} ({as_of_s}) done — {len(log)} transactions logged.")
    return state


def _append_csv(path, rows):
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def write_dashboard_data(state, prices, fx, ranking, names, week):
    hist = pd.read_csv(HISTORY_FILE)
    trades = pd.read_csv(TRADES_FILE) if TRADES_FILE.exists() else pd.DataFrame()
    start = pd.Timestamp(state["start_date"])
    out_p = []
    for p in state["portfolios"]:
        total, holdings = analyst.portfolio_report(p, prices, fx, ranking, names)
        h = hist[hist["portfolio"] == p["id"]]
        nxt = None
        if p["kind"] == "strategy":
            k = week + 1
            while k % p["every_weeks"]:
                k += 1
            nxt = (start + timedelta(days=7 * k)).strftime("%Y-%m-%d")
        out_p.append({
            "id": p["id"], "name": p["name"], "kind": p["kind"],
            "universe": p.get("universe"), "every_weeks": p.get("every_weeks"),
            "reference": p.get("reference", False),
            "value": round(total, 2),
            "return_pct": round(total / C.INITIAL_CASH_CAD - 1, 5),
            "cash_cad": round(p["cash"]["CAD"], 2), "cash_usd": round(p["cash"]["USD"], 2),
            "fx_fees": round(p["fees"]["fx"], 2), "n_trades": p["n_trades"],
            "last_rebalance": p.get("last_rebalance"), "next_rebalance": nxt,
            "history": [[d, v] for d, v in zip(h["date"], h["value_cad"])],
            "holdings": holdings,
        })
    top = ranking.head(C.TOP_N)
    ca = ranking[ranking["market"] == "CA"].head(C.TOP_N)

    def rows(df):
        return [{"rank": int(r["rank"]), "ticker": r["ticker"], "name": r["name"], "market": r["market"],
                 "rec_mean": round(float(r["rec_mean"]), 2), "n_analysts": int(r["n_analysts"]),
                 "upside": round(float(r["upside"]), 4), "score": round(float(r["score"]), 4)}
                for _, r in df.iterrows()]

    data = {
        "as_of": state["last_date"], "start_date": state["start_date"], "week": week,
        "fx": round(fx, 4), "settings": {"fx_cost": C.FX_COST, "n_hold": C.N_HOLD,
                                         "initial": C.INITIAL_CASH_CAD},
        "portfolios": out_p, "top_mix": rows(top), "top_ca": rows(ca),
        "recent_trades": trades.tail(300).fillna("").to_dict("records") if len(trades) else [],
    }
    (DOCS / "data.json").write_text(json.dumps(_clean(data)))


def _clean(o):
    """JSON can't hold NaN — replace with null."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if hasattr(o, "item"):                               # numpy scalar -> python
        return _clean(o.item())
    return o


if __name__ == "__main__":
    run()
