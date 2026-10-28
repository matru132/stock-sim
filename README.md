# Analyst Picks — Paper Trading Experiment

A forward-running simulation: 7 hypothetical portfolios of $100,000 CAD that follow
analyst buy recommendations, compared against simply buying the index and doing
nothing. **No real money, no brokerage account involved.** Educational only.

## The rulebook

| Rule | Value |
|---|---|
| Starting money | $100,000 CAD per portfolio |
| Universe | S&P 500 (US) + S&P/TSX Composite (Canada) |
| Eligible stock | ≥ 10 analysts (US) or ≥ 6 (Canada), company worth ≥ $2B CAD |
| Score | 50% analyst rating + 50% upside to the average target price (upside capped at +50%) |
| Weekly list | Top 100 by score, saved every week in `data/rankings/` |
| Holdings | Top 30, equal weight (≈ 3.33% each), fractional shares allowed |
| Drift band | A holding still in the top 30 is only resized if it drifts more than 25% from its target weight |
| Prices | Friday closing prices (Thursday when Friday is a holiday) |
| Commission | $0 (BMO InvestorLine, since Sept 14, 2026) |
| Currency | Everything reported in CAD. Two cash balances (CAD and USD); 1.5% fee on every conversion |
| Dividends | Strategy portfolios: added to cash, invested at the next rebalance. Index portfolios: reinvested weekly |

### The 7 portfolios (+ 2 reference lines)

| ID | Universe | Rebalances |
|---|---|---|
| MIX-1W | US + Canada | every week |
| MIX-2W | US + Canada | every 2 weeks |
| MIX-4W | US + Canada | every 4 weeks |
| CA-1W | Canada only | every week |
| CA-2W | Canada only | every 2 weeks |
| CA-4W | Canada only | every 4 weeks |
| BENCH | 50% S&P 500 (SPY) + 50% TSX (XIC.TO) | never — bought once on Day 0 |
| REF-SPX / REF-TSX | 100% SPY / 100% XIC.TO | never — shown for reference |

All rules live in `sim/config.py`. Change a number there and every part of the system follows.

## How it works

```
Every Saturday morning (GitHub Actions)
   │
   ├─ Agent 1 · Gatherer   sim/agent_gather.py   index members, analyst data, prices
   ├─ Agent 2 · Analyst    sim/agent_analyst.py  scores and ranks; reports portfolio status
   ├─ Agent 3 · Trader     sim/agent_trader.py   sells, buys, converts currency, logs trades
   └─ Orchestrator         sim/run_weekly.py     runs them in order, saves everything
                                │
                                ├─ data/state.json       current holdings and cash
                                ├─ data/history.csv      weekly value of every portfolio
                                ├─ data/trades.csv       every transaction ever made
                                ├─ data/rankings/*.csv   each week's full ranking
                                └─ docs/data.json  ──►   docs/index.html (the dashboard)
```

The "agents" are plain Python steps, not AI. The rules are mechanical, so code is
cheaper, faster, and gives the same answer every time.

## Setup (one time, about 15 minutes)

1. **Create a GitHub account** at github.com (free).
2. **Create a repository**: the **+** menu at the top right → **New repository**.
   Name it `stock-sim`, choose **Public**, then click **Create repository**.
   It must be public for the free dashboard hosting. Nothing private is stored in it.
3. **Upload the files**: on the empty repository page, click **uploading an existing file**,
   then drag in the *contents* of the `stock-sim` folder.
   The `.github` folder is hidden on Mac and Windows by default; see step 4.
4. **Add the weekly schedule file**, if it didn't upload: **Add file → Create new file**, type the name
   `.github/workflows/weekly.yml`, paste the contents of that file, then **Commit changes**.
5. **Turn on the dashboard**: **Settings → Pages**. Under "Branch", choose `main` and folder `/docs`, then **Save**.
   The dashboard address appears there (like `https://YOUR-NAME.github.io/stock-sim/`).
6. **Run Day 0**: on or after Saturday, go to **Actions → Weekly simulation run → Run workflow**.
   It takes 10–20 minutes. After that, it runs by itself every Saturday.

## Checking that it's working

- **Actions** tab: each weekly run shows a green check (worked) or a red X (failed).
  GitHub emails you when a run fails.
- A failed run changes nothing, and the next week continues normally.
  If it fails repeatedly, the data source has probably changed: bring the error message to Claude.

## Running the tests

`python -m tests.test_simulation` simulates 12 weeks on fake market data and checks
the money math (no money appears or disappears, FX fees only where expected,
rebalancing on schedule). GitHub runs it before every weekly run.
