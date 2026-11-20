"""
Runs 12 fake weeks and checks the money math.
Run with:  python -m tests.test_simulation      (from the project folder)
"""
import json
import os
from pathlib import Path

import tempfile
import pandas as pd

from sim import config as C
from sim import run_weekly
from tests.fake_market import FakeProvider

C.MIN_ELIGIBLE_TOTAL = 50     # the fake market is smaller than the real one
C.MIN_ELIGIBLE_CA = 20

FRIDAYS = pd.date_range("2026-10-30", periods=12, freq="7D")


def approx(a, b, abs_tol=None, rel=1e-6):
    tol = abs_tol if abs_tol is not None else rel * max(abs(a), abs(b), 1)
    return abs(a - b) <= tol


def in_tempdir(fn):
    """Run each test in its own empty folder so they never share files."""
    def wrapper():
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                fn()
            finally:
                os.chdir(old)
    wrapper.__name__ = fn.__name__
    return wrapper


def run_weeks(n=12):
    prov = FakeProvider()
    for d in FRIDAYS[:n]:
        run_weekly.run(provider=prov, today=d + pd.Timedelta(days=1))   # Saturday run
    return prov


@in_tempdir
def test_full_run():
    run_weeks()
    state = json.loads(Path("data/state.json").read_text())
    hist = pd.read_csv("data/history.csv")
    trades = pd.read_csv("data/trades.csv")

    assert state["last_week"] == 11
    assert hist["date"].nunique() == 12
    n_total = len(C.STRATEGIES) + len(C.BENCHMARKS)
    assert len(state["portfolios"]) == n_total

    for p in state["portfolios"]:
        assert p["cash"]["CAD"] >= -0.01 and p["cash"]["USD"] >= -0.01, p["id"]
        if p["kind"] == "strategy" and p.get("signal") != "insider":   # insider ones hold stocks + index
            assert len(p["positions"]) <= C.N_HOLD
            assert len(p["positions"]) >= C.N_HOLD - 1, (p["id"], len(p["positions"]))

    by = {p["id"]: p for p in state["portfolios"]}
    # Canada-only portfolios never convert currency
    for pid in ("CA-1W", "CA-2W", "CA-4W", "REF-TSX"):
        assert by[pid]["fees"]["fx"] == 0
        assert by[pid]["cash"]["USD"] == 0
    # Mixed portfolios do pay FX
    assert by["MIX-1W"]["fees"]["fx"] > 0

    # Rebalance schedule: count distinct trading dates (BUY/SELL) per portfolio
    tr = trades[trades["action"].isin(["BUY", "SELL"])]
    days = tr.groupby("portfolio")["date"].nunique()
    assert days["MIX-1W"] == 12
    assert days["MIX-2W"] == 6
    assert days["MIX-4W"] == 3
    assert days["BENCH"] <= 1 + trades[trades["action"] == "DIVIDEND"]["date"].nunique()

    # Day 0: every portfolio worth exactly 100k minus what conversion and spread cost
    d0 = hist[hist["week"] == 0].set_index("portfolio")
    for pid in ("CA-1W", "REF-TSX", "MIX-1W", "MIX-2W-SC"):
        row = d0.loc[pid]
        assert row["spread_fees_cad"] > 0, pid
        assert approx(row["value_cad"], C.INITIAL_CASH_CAD - row["fx_fees_cad"] - row["spread_fees_cad"],
                      abs_tol=0.05), pid
    # Spread on Day 0 is about 0.1% of what was bought
    assert approx(d0.loc["CA-1W", "spread_fees_cad"], C.INITIAL_CASH_CAD * C.SPREAD_COST, abs_tol=5)

    # Sector cap respected at every point in time
    for pid in ("MIX-2W-SC", "CA-2W-SC"):
        p = by[pid]
        sectors = pd.Series([FakeProvider().sector[t] for t in p["positions"]]).value_counts()
        assert sectors.max() <= p["sector_cap"], (pid, sectors.to_dict())
        assert len(p["positions"]) >= C.N_HOLD - 1, (pid, len(p["positions"]))
    # ...while the uncapped version is free to load up on the big sector
    unc = pd.Series([FakeProvider().sector[t] for t in by["MIX-2W"]["positions"]]).value_counts()
    assert unc.max() > 6, unc.to_dict()

    # Dashboard file exists and is valid JSON with all portfolios
    data = json.loads(Path("docs/data.json").read_text())
    assert len(data["portfolios"]) == n_total
    assert len(data["top_mix"]) == C.TOP_N


@in_tempdir
def test_rerun_same_week_is_ignored():
    prov = FakeProvider()
    run_weekly.run(provider=prov, today=FRIDAYS[0] + pd.Timedelta(days=1))
    run_weekly.run(provider=prov, today=FRIDAYS[0] + pd.Timedelta(days=2))   # Sunday re-run
    run_weekly.run(provider=prov, today=FRIDAYS[1] - pd.Timedelta(days=2))   # mid-week manual run
    hist = pd.read_csv("data/history.csv")
    assert hist["week"].max() == 0 and hist["date"].nunique() == 1


@in_tempdir
def test_trade_conserves_money():
    """On a rebalance day, value after trades = value before trades - FX and spread fees."""
    prov = FakeProvider()
    run_weekly.run(provider=prov, today=FRIDAYS[0] + pd.Timedelta(days=1))
    before = json.loads(Path("data/state.json").read_text())
    run_weekly.run(provider=prov, today=FRIDAYS[1] + pd.Timedelta(days=1))
    after = json.loads(Path("data/state.json").read_text())
    trades = pd.read_csv("data/trades.csv")
    wk1 = trades[trades["date"] == after["last_date"]]

    closes = prov.closes.loc[:after["last_date"]].ffill().iloc[-1]
    fx = after["fx"]

    def value(p):
        v = p["cash"]["CAD"] + p["cash"]["USD"] * fx
        for t, pos in p["positions"].items():
            v += pos["shares"] * closes[t] * (fx if pos["ccy"] == "USD" else 1)
        return v

    for pb, pa in zip(before["portfolios"], after["portfolios"]):
        if pa["id"] != "MIX-1W":
            continue
        divs = wk1[(wk1["portfolio"] == pa["id"]) & (wk1["action"] == "DIVIDEND")]
        div_cad = sum(r.shares * r.price * (fx if r.currency == "USD" else 1) for r in divs.itertuples())
        fee = (pa["fees"]["fx"] - pb["fees"]["fx"]) + (pa["fees"]["spread"] - pb["fees"]["spread"])
        assert pa["fees"]["spread"] > pb["fees"]["spread"]
        assert approx(value(pa), value(pb) + div_cad - fee), (value(pa), value(pb) + div_cad - fee)


@in_tempdir
def test_bad_data_week_does_not_trade():
    """If analyst data comes back mostly empty, the run stops and holdings are untouched."""
    prov = FakeProvider()
    run_weekly.run(provider=prov, today=FRIDAYS[0] + pd.Timedelta(days=1))
    before = Path("data/state.json").read_text()
    good = prov.get_fundamentals
    prov.get_fundamentals = lambda tickers: good(tickers[:10])
    try:
        run_weekly.run(provider=prov, today=FRIDAYS[1] + pd.Timedelta(days=1))
        raise AssertionError("should have stopped")
    except RuntimeError:
        pass
    assert Path("data/state.json").read_text() == before


def test_ticker_cleanup():
    """Blank cells and footnote junk in the Wikipedia tables must not crash the run."""
    from sim.agent_gather import clean_symbols, to_yahoo_ca, to_yahoo_us
    col = pd.Series(["RY", None, float("nan"), " TD ", "RCI.B", "TSX:BN", "Notes", "RY", "[a]"])
    ca = list(clean_symbols(col).map(to_yahoo_ca))
    assert ca == ["RY.TO", "TD.TO", "RCI-B.TO", "BN.TO"], ca
    us = list(clean_symbols(pd.Series(["BRK.B", "AAPL", None])).map(to_yahoo_us))
    assert us == ["BRK-B", "AAPL"], us


@in_tempdir
def test_portfolios_added_later_start_fresh():
    """Portfolios added to config.py mid-experiment start with their own $100,000,
    keep their own rebalance schedule, and leave older portfolios untouched."""
    import copy
    full_s, full_b = copy.deepcopy(C.STRATEGIES), copy.deepcopy(C.BENCHMARKS)
    late = {"MIX-2W-SC", "CA-2W-SC", "REF-XEQT"}
    prov = FakeProvider()
    try:
        C.STRATEGIES = [s for s in full_s if s["id"] not in late]
        C.BENCHMARKS = [b for b in full_b if b["id"] not in late]
        for d in FRIDAYS[:3]:
            run_weekly.run(provider=prov, today=d + pd.Timedelta(days=1))
        before = {p["id"]: p for p in json.loads(Path("data/state.json").read_text())["portfolios"]}
        assert not late & set(before)
    finally:
        C.STRATEGIES, C.BENCHMARKS = full_s, full_b
    for d in FRIDAYS[3:8]:
        run_weekly.run(provider=prov, today=d + pd.Timedelta(days=1))

    state = json.loads(Path("data/state.json").read_text())
    by = {p["id"]: p for p in state["portfolios"]}
    assert late <= set(by)
    for pid in late:
        assert by[pid]["start_week"] == 3 and by[pid]["start_date"] == FRIDAYS[3].strftime("%Y-%m-%d")
        assert by[pid]["positions"]
    assert by["MIX-1W"]["start_week"] == 0

    hist = pd.read_csv("data/history.csv")
    first = hist[hist["portfolio"] == "REF-XEQT"].iloc[0]
    assert first["week"] == 3
    assert approx(first["value_cad"], C.INITIAL_CASH_CAD - first["spread_fees_cad"], abs_tol=0.05)
    # Old rows written before the spread column existed are still readable
    assert hist[hist["week"] == 0]["portfolio"].nunique() == len(before)

    # Late every-2-weeks portfolio trades on weeks 3, 5, 7 (its own schedule), not 4, 6
    trades = pd.read_csv("data/trades.csv")
    tr = trades[(trades["portfolio"] == "CA-2W-SC") & trades["action"].isin(["BUY", "SELL"])]
    assert sorted(tr["date"].unique()) == [FRIDAYS[i].strftime("%Y-%m-%d") for i in (3, 5, 7)]

    data = json.loads(Path("docs/data.json").read_text())
    p = {q["id"]: q for q in data["portfolios"]}["MIX-2W-SC"]
    assert p["bench_same_period"] is not None and p["start_date"] == FRIDAYS[3].strftime("%Y-%m-%d")
    assert all("in_target" in h for h in p["holdings"])


def test_sector_cap_selection():
    from sim.agent_analyst import targets_for
    r = pd.DataFrame({"ticker": [f"T{i}" for i in range(60)], "market": "CA",
                      "sector": ["Gold"] * 20 + ["Bank"] * 20 + [None] * 20})
    picks = targets_for(r, "ca", sector_cap=6)
    assert picks[:6] == [f"T{i}" for i in range(6)]          # best 6 gold names kept, in rank order
    assert picks[6:12] == [f"T{i}" for i in range(20, 26)]   # then the best 6 banks
    assert len(picks) == 18                                   # 6 gold + 6 bank + 6 unknown
    assert targets_for(r, "ca") == [f"T{i}" for i in range(30)]


def test_insider_filter_keeps_only_real_open_market_buys():
    from sim.agent_analyst import filter_insider_buys
    raw = pd.DataFrame([
        ["AAA", 1000, 250_000, "Purchase at price 250.00 per share.", "CEO A", "Chief Executive Officer", "2026-11-01"],
        ["BBB.TO", 3000, None, "Acquisition in the public market at price 15.50 per share.", "Dir B", "Director of Issuer", "2026-10-20"],
        ["CCC.TO", 900, 18_000, "Acquisition under a purchase/ownership plan at price 20.00 per share.", "Plan", "Senior Officer of Issuer", "2026-11-02"],
        ["DDD", 90_000, 9e6, "Purchase at price 100.00 per share.", "Fund", "Beneficial Owner of more than 10% of a Class of Security", "2026-11-02"],
        ["EEE", 100, 2_000, "Purchase at price 20.00 per share.", "Dir E", "Director", "2026-11-02"],
        ["FFF", 5000, 500_000, "Sale at price 100.00 per share.", "Dir F", "Director", "2026-11-02"],
        ["GGG", 5000, 500_000, "Purchase at price 100.00 per share.", "Dir G", "Director", "2026-05-01"],
        ["HHH", 5000, 500_000, "Conversion of Exercise of derivative security at price 10.00 per share.", "Dir H", "Officer", "2026-11-02"],
    ], columns=["ticker", "Shares", "Value", "Text", "Insider", "Position", "Start Date"])
    b = filter_insider_buys(raw, "2026-11-13")
    assert sorted(b["ticker"]) == ["AAA", "BBB.TO"], b
    assert approx(float(b.loc[b["ticker"] == "BBB.TO", "value"].iloc[0]), 3000 * 15.5)   # value from price x shares


def test_insider_ranking_and_weights():
    from sim.agent_analyst import insider_picks, insider_weights
    buys = pd.DataFrame({"ticker": ["A", "A", "B", "C.TO", "C.TO", "C.TO", "Z"],
                         "date": pd.to_datetime(["2026-11-01"] * 7),
                         "insider": ["x", "y", "x", "x", "y", "z", "q"],
                         "position": "Director", "value": [50e3, 50e3, 900e3, 30e3, 30e3, 30e3, 1e6]})
    uni = pd.DataFrame({"ticker": ["A", "B", "C.TO"], "market": ["US", "US", "CA"]})   # Z not in universe
    prices = {"A": 1, "B": 1, "C.TO": 1}
    mix = insider_picks(buys, uni, prices, "mix", fx=1.4)
    assert list(mix["ticker"]) == ["C.TO", "A", "B"]          # 3 buyers, then 2, then 1
    assert list(insider_picks(buys, uni, prices, "ca", fx=1.4)["ticker"]) == ["C.TO"]
    w = insider_weights(mix, {"SPY": 0.5, "XIC.TO": 0.5})
    assert approx(sum(w.values()), 1.0)
    assert approx(w["A"], 1 / C.N_HOLD) and approx(w["SPY"], (1 - 3 / C.N_HOLD) / 2)
    empty = insider_weights(mix.head(0), {"XIC.TO": 1.0})
    assert empty == {"XIC.TO": 1.0}                           # no insider buying -> 100% index


@in_tempdir
def test_insider_portfolios_run():
    prov = FakeProvider()
    for d in FRIDAYS[:5]:
        run_weekly.run(provider=prov, today=d + pd.Timedelta(days=1))
    state = json.loads(Path("data/state.json").read_text())
    by = {p["id"]: p for p in state["portfolios"]}
    data = json.loads(Path("docs/data.json").read_text())
    picks = data["insider"]
    assert picks and picks["mix"] and picks["ca"]
    allowed_ca = {r["ticker"] for r in picks["ca"]}
    for pid, fill in (("MIX-INS", {"SPY", "XIC.TO"}), ("CA-INS", {"XIC.TO"})):
        p = by[pid]
        stocks = set(p["positions"]) - fill
        assert len(stocks) <= C.N_HOLD
        assert fill & set(p["positions"]) or len(stocks) == C.N_HOLD    # empty slots sit in the index
        assert p["cash"]["CAD"] >= -0.01 and p["cash"]["USD"] >= -0.01
    # Week 4 is a rebalance week, so every stock held is one of this week's insider picks
    assert set(by["CA-INS"]["positions"]) - {"XIC.TO"} <= allowed_ca
    assert set(by["MIX-INS"]["positions"]) - {"SPY", "XIC.TO"} <= {r["ticker"] for r in picks["mix"]}
    # Weights: each stock about 1/30, the index takes the rest
    vals = {q["id"]: q for q in data["portfolios"]}["MIX-INS"]["holdings"]
    n_stocks = sum(1 for x in vals if x["ticker"] not in ("SPY", "XIC.TO"))
    idx_w = sum(x["weight"] for x in vals if x["ticker"] in ("SPY", "XIC.TO"))
    assert abs(idx_w - (1 - n_stocks / C.N_HOLD)) < 0.02, (n_stocks, idx_w)
    assert all(t.endswith(".TO") for t in by["CA-INS"]["positions"])
    assert by["CA-INS"]["fees"]["fx"] == 0                                  # Canada only: no conversions
    # Excluded rows never become picks
    buys = pd.read_csv(sorted(Path("data/insider").glob("*.csv"))[-1])
    assert not buys["insider"].isin(["Payroll Plan", "Big Fund", "Token Buyer", "Seller"]).any()
    # Dashboard marks the index slots and insider picks as "keep"
    h = {q["id"]: q for q in data["portfolios"]}["CA-INS"]["holdings"]
    assert all(x["in_target"] for x in h)


@in_tempdir
def test_insider_data_outage_holds_positions():
    prov = FakeProvider()
    for d in FRIDAYS[:3]:
        run_weekly.run(provider=prov, today=d + pd.Timedelta(days=1))
    before = {p["id"]: p for p in json.loads(Path("data/state.json").read_text())["portfolios"]}
    prov.insider_coverage = 0.1
    for d in FRIDAYS[3:5]:
        run_weekly.run(provider=prov, today=d + pd.Timedelta(days=1))
    after = {p["id"]: p for p in json.loads(Path("data/state.json").read_text())["portfolios"]}
    assert set(after["MIX-INS"]["positions"]) == set(before["MIX-INS"]["positions"])
    assert after["MIX-INS"]["n_trades"] == before["MIX-INS"]["n_trades"]
    assert after["MIX-2W"]["n_trades"] > before["MIX-2W"]["n_trades"]      # other portfolios keep trading
    data = json.loads(Path("docs/data.json").read_text())
    assert data["insider"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS  {name}")
