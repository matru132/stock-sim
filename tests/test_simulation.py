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
    assert len(state["portfolios"]) == 9

    for p in state["portfolios"]:
        assert p["cash"]["CAD"] >= -0.01 and p["cash"]["USD"] >= -0.01, p["id"]
        if p["kind"] == "strategy":
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

    # Day 0: every portfolio worth exactly 100k minus what conversion cost
    d0 = hist[hist["week"] == 0].set_index("portfolio")
    for pid in ("CA-1W", "REF-TSX"):
        assert approx(d0.loc[pid, "value_cad"], C.INITIAL_CASH_CAD, abs_tol=0.05)
    mix0 = d0.loc["MIX-1W"]
    assert approx(mix0["value_cad"], C.INITIAL_CASH_CAD - mix0["fx_fees_cad"], abs_tol=0.05)

    # Dashboard file exists and is valid JSON with all portfolios
    data = json.loads(Path("docs/data.json").read_text())
    assert len(data["portfolios"]) == 9
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
    """On a rebalance day, value after trades = value before trades - FX fees."""
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
        fee = pa["fees"]["fx"] - pb["fees"]["fx"]
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS  {name}")
