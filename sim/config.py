"""
Experiment rules — the single place to change how the simulation behaves.
Every other file reads its settings from here.
"""

# --- Money ---
INITIAL_CASH_CAD = 100_000.0      # every portfolio starts with this, in CAD
FX_COST = 0.015                   # 1.5% charged on every CAD<->USD conversion
COMMISSION_CAD = 0.0              # BMO InvestorLine: $0 per stock/ETF trade since Sept 14, 2026

# --- Ranking rules ---
TOP_N = 100                       # size of the weekly ranked list
N_HOLD = 30                       # stocks held by each strategy portfolio
MIN_ANALYSTS_US = 10              # US stock needs at least this many analysts
MIN_ANALYSTS_CA = 6               # Canadian stock needs at least this many analysts
MIN_MARKET_CAP_CAD = 2e9          # $2B CAD minimum company size
UPSIDE_CAP = 0.50                 # target upside counted up to +50% only
WEIGHT_RATING = 0.5               # share of the score from the analyst rating
WEIGHT_UPSIDE = 0.5               # share of the score from target-price upside

# --- Rebalancing ---
# Positions still in the top 30 are only topped up / trimmed back to equal
# weight when they drift more than 25% away from their target (e.g. a 3.33%
# target is left alone between 2.5% and 4.17%). Avoids pointless tiny trades.
DRIFT_BAND = 0.25

# --- Portfolios ---
# universe: "mix" = US + Canada, "ca" = Canada only
# every_weeks: 1 = weekly, 2 = every 2 weeks, 4 = every 4 weeks
STRATEGIES = [
    {"id": "MIX-1W", "name": "US + Canada · weekly",        "universe": "mix", "every_weeks": 1},
    {"id": "MIX-2W", "name": "US + Canada · every 2 weeks", "universe": "mix", "every_weeks": 2},
    {"id": "MIX-4W", "name": "US + Canada · every 4 weeks", "universe": "mix", "every_weeks": 4},
    {"id": "CA-1W",  "name": "Canada only · weekly",        "universe": "ca",  "every_weeks": 1},
    {"id": "CA-2W",  "name": "Canada only · every 2 weeks", "universe": "ca",  "every_weeks": 2},
    {"id": "CA-4W",  "name": "Canada only · every 4 weeks", "universe": "ca",  "every_weeks": 4},
]

# Buy-and-hold: bought once on Day 0, never sold. Dividends are reinvested.
# SPY tracks the S&P 500 (in USD, so it pays the FX cost once);
# XIC.TO tracks the S&P/TSX Composite (in CAD).
BENCHMARKS = [
    {"id": "BENCH",   "name": "Benchmark · 50% S&P 500 + 50% TSX", "weights": {"SPY": 0.5, "XIC.TO": 0.5}, "reference": False},
    {"id": "REF-SPX", "name": "Reference · S&P 500 only",          "weights": {"SPY": 1.0},                 "reference": True},
    {"id": "REF-TSX", "name": "Reference · TSX only",              "weights": {"XIC.TO": 1.0},              "reference": True},
]

# --- Safety ---
# If the data source returns too few usable stocks (outage, blocking), the run
# stops WITHOUT trading rather than selling everything on bad data.
MIN_ELIGIBLE_TOTAL = 200
MIN_ELIGIBLE_CA = 40

FX_TICKER = "CAD=X"              # Yahoo: Canadian dollars per 1 US dollar
