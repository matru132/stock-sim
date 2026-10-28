"""
AGENT 3 — TRADER
The only code allowed to change a portfolio. Applies the rules mechanically:
sell what fell out of the top 30, buy what entered, keep everything near equal
weight, and pay the currency-conversion cost whenever CAD <-> USD is needed.

Each portfolio holds two cash balances (CAD and USD), like a real Canadian
brokerage account. US sale proceeds stay in USD, so money is only converted
when one currency runs short — that is what keeps FX costs realistic.
"""
from . import config as C
from .agent_analyst import portfolio_value, price_cad


def new_strategy(spec):
    return {**spec, "kind": "strategy",
            "cash": {"CAD": C.INITIAL_CASH_CAD, "USD": 0.0},
            "positions": {}, "fees": {"fx": 0.0, "commission": 0.0},
            "n_trades": 0, "last_rebalance": None}


def new_benchmark(spec):
    return {**spec, "kind": "benchmark",
            "cash": {"CAD": C.INITIAL_CASH_CAD, "USD": 0.0},
            "positions": {}, "fees": {"fx": 0.0, "commission": 0.0},
            "n_trades": 0, "last_rebalance": None}


def _log(log, date, p, action, ticker, shares, price, ccy, value_cad, fee_cad=0.0):
    log.append({"date": date, "portfolio": p["id"], "action": action, "ticker": ticker,
                "shares": round(shares, 6), "price": round(price, 4), "currency": ccy,
                "value_cad": round(value_cad, 2), "fee_cad": round(fee_cad, 2)})


def _commission(p):
    if C.COMMISSION_CAD:
        p["cash"]["CAD"] -= C.COMMISSION_CAD
        p["fees"]["commission"] += C.COMMISSION_CAD


def ensure_cash(p, ccy, amount, fx, date, log):
    """Make sure `amount` of currency `ccy` is available, converting from the
    other currency (and paying FX_COST) only for the shortfall."""
    short = amount - p["cash"][ccy]
    if short <= 1e-9:
        return
    other = "USD" if ccy == "CAD" else "CAD"
    rate = fx if other == "USD" else 1.0 / fx              # units of ccy per unit of other
    gross = min(short / (rate * (1 - C.FX_COST)), max(p["cash"][other], 0.0))
    if gross <= 1e-9:
        return
    received = gross * rate * (1 - C.FX_COST)
    fee_cad = gross * rate * C.FX_COST * (1.0 if ccy == "CAD" else fx)
    p["cash"][other] -= gross
    p["cash"][ccy] += received
    p["fees"]["fx"] += fee_cad
    _log(log, date, p, f"FX {other}->{ccy}", "", gross, rate, other,
         gross * (fx if other == "USD" else 1.0), fee_cad)


def buy(p, t, ccy, price, amount_native, fx, date, log):
    ensure_cash(p, ccy, amount_native, fx, date, log)
    amount_native = min(amount_native, p["cash"][ccy])
    if amount_native < 1.0:                                   # nothing meaningful left to spend
        return
    shares = amount_native / price                            # fractional shares allowed
    p["cash"][ccy] -= amount_native
    pos = p["positions"].setdefault(t, {"shares": 0.0, "ccy": ccy, "cost_cad": 0.0})
    pos["shares"] += shares
    cost_cad = amount_native * (fx if ccy == "USD" else 1.0)
    pos["cost_cad"] += cost_cad
    p["n_trades"] += 1
    _commission(p)
    _log(log, date, p, "BUY", t, shares, price, ccy, cost_cad)


def sell(p, t, shares, price, fx, date, log):
    pos = p["positions"][t]
    shares = min(shares, pos["shares"])
    proceeds = shares * price
    frac = shares / pos["shares"] if pos["shares"] else 1.0
    pos["cost_cad"] *= (1 - frac)
    pos["shares"] -= shares
    p["cash"][pos["ccy"]] += proceeds                         # proceeds stay in their own currency
    p["n_trades"] += 1
    _commission(p)
    _log(log, date, p, "SELL", t, shares, price, pos["ccy"],
         proceeds * (fx if pos["ccy"] == "USD" else 1.0))
    if pos["shares"] <= 1e-9:
        del p["positions"][t]


def credit_dividends(p, divs_per_share, date, log):
    """divs_per_share: {ticker: total dividend per share with ex-date this week}."""
    for t, pos in p["positions"].items():
        d = divs_per_share.get(t, 0.0)
        if d > 0:
            cash = d * pos["shares"]
            p["cash"][pos["ccy"]] += cash
            _log(log, date, p, "DIVIDEND", t, pos["shares"], d, pos["ccy"], 0.0)


def _plan(p, targets, prices, ccys, fx, target_val):
    """Return {ticker: change in CAD value} — positive = buy, negative = sell."""
    plan = {}
    for t in targets:
        cur = 0.0
        if t in p["positions"]:
            cur = p["positions"][t]["shares"] * price_cad(t, prices, fx, ccys[t])
        if t not in p["positions"]:
            plan[t] = target_val
        elif abs(cur - target_val) > C.DRIFT_BAND * target_val:
            plan[t] = target_val - cur
    return plan


def _estimated_fx_fee(p, plan, ccys, fx):
    cash = dict(p["cash"])
    need = {"CAD": 0.0, "USD": 0.0}
    for t, dv in plan.items():
        native = dv / (fx if ccys[t] == "USD" else 1.0)
        if dv < 0:
            cash[ccys[t]] += -native
        else:
            need[ccys[t]] += native
    short_usd = max(0.0, need["USD"] - cash["USD"]) * fx
    short_cad = max(0.0, need["CAD"] - cash["CAD"])
    return (short_usd + short_cad) * C.FX_COST / (1 - C.FX_COST)


def rebalance(p, targets, prices, ccys, fx, date, log):
    """targets: ordered list of tickers (best first) this portfolio should hold."""
    # 1. Sell everything that fell out of the target list.
    for t in list(p["positions"]):
        if t not in targets:
            sell(p, t, p["positions"][t]["shares"], prices[t], fx, date, log)

    # 2. Work out equal-weight target size, net of the FX cost the trades will need.
    total = portfolio_value(p, prices, fx)
    target_val = total / C.N_HOLD
    for _ in range(3):
        plan = _plan(p, targets, prices, ccys, fx, target_val)
        target_val = (total - _estimated_fx_fee(p, plan, ccys, fx)) / C.N_HOLD
    plan = _plan(p, targets, prices, ccys, fx, target_val)

    # 3. Trim overweight positions first (frees cash), then buy in rank order.
    for t, dv in plan.items():
        if dv < 0:
            px = prices[t]
            sell(p, t, -dv / (fx if ccys[t] == "USD" else 1.0) / px, px, fx, date, log)
    for t in targets:
        dv = plan.get(t, 0.0)
        if dv > 0:
            buy(p, t, ccys[t], prices[t], dv / (fx if ccys[t] == "USD" else 1.0), fx, date, log)
    p["last_rebalance"] = date


def setup_benchmark(p, prices, ccys, fx, date, log):
    """Day 0: buy the index funds once."""
    total = C.INITIAL_CASH_CAD
    for t, w in p["weights"].items():
        cad_amount = total * w
        if ccys[t] == "USD":
            native = cad_amount * (1 - C.FX_COST) / fx
        else:
            native = cad_amount
        buy(p, t, ccys[t], prices[t], native, fx, date, log)
    p["last_rebalance"] = date


def reinvest_benchmark_cash(p, prices, ccys, fx, date, log):
    """Dividends received by the index funds are reinvested in the same fund
    (same currency, so no conversion cost)."""
    for t, pos in list(p["positions"].items()):
        cash = p["cash"][pos["ccy"]]
        if cash >= 1.0:
            buy(p, t, pos["ccy"], prices[t], cash, fx, date, log)
