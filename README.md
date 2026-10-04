# Canadian Credit Relative Value

Finds Canadian investment-grade corporate bonds trading cheap or rich versus their credit fundamentals, and turns the strongest signal into a CS01-neutral long/short trade. See the [PRD](PRD%20Canadian%20Corporate%20Bond%20Relative%20Value%20Analysis.md) for the full plan.

The PRD assumed Bloomberg data. This repo builds the same bond panel from free sources instead, so no terminal is needed. Details and design decisions are in [`bloomberg_adapter/PLAN.md`](bloomberg_adapter/PLAN.md).

## Setup

```bash
uv sync                    # creates .venv with the dependencies in pyproject.toml
uv sync --group notebook   # adds Jupyter for notebooks/
```

To download fresh SEC data, create `.env` with a contact address (SEC requires one; `.env` is git-ignored):

```bash
echo 'SEC_USER_AGENT="Your Name you@example.com"' > .env
```

## Data

| Data | Source | Module |
| --- | --- | --- |
| GoC benchmark yields | Bank of Canada Valet API | `bloomberg_adapter/boc_valet.py` |
| Bond universe, prices, terms | iShares XCB month-end holdings (BlackRock Canada) | `bloomberg_adapter/etf_holdings.py` |
| Issuer fundamentals | SEC EDGAR XBRL (40-F / 10-K / 20-F) | `bloomberg_adapter/edgar_xbrl.py` |
| Credit ratings | Annual Information Forms on EDGAR | `bloomberg_adapter/aif_ratings.py` |
| Hand-entered gaps | `bloomberg_adapter/reference/*.csv` | `bloomberg_adapter/manual_csv.py` |

All downloads are cached in `data/raw/`, so the build runs offline:

```bash
uv run python -m bloomberg_adapter.build_panel
```

This writes `data/processed/bond_panel.csv` (one row per bond per month-end: yield, G-spread, rating, leverage, coverage), `issuer_fundamentals.csv`, `validation_report.md`, and `manifest.json` (hashes of every input and output). In code:

```python
from src.load_data import load_bonds, load_goc_yields, load_fundamentals
bonds = load_bonds()   # 233 bonds, 45 month-ends (Jan 2023 – Sep 2026)
```

## Status

| Step | Module | Status |
| --- | --- | --- |
| GoC yields | `bloomberg_adapter/boc_valet.py` | Done |
| Bond panel (233 bonds, 45 month-ends) | `bloomberg_adapter/etf_holdings.py` | Done |
| G-spreads | `bloomberg_adapter/goc_curve.py`, `src/spreads.py` | Done |
| Fundamentals (leverage, coverage) | `bloomberg_adapter/edgar_xbrl.py` | Done; Hydro One manual |
| Ratings (S&P / Moody's / DBRS → 1–10) | `bloomberg_adapter/aif_ratings.py` | 80% done; Hydro One, BIP, National Bank manual |
| Amount outstanding (C$300mm screen) | `bloomberg_adapter/reference/amount_outstanding.csv` | **To fill** |
| Issuer and sector curves | `src/curves.py` | Done (premium/age-adjusted, leave-one-out) |
| Fundamental regression | `src/regression.py` | Done (12 of 15 issuers until manual ratings are filled) |
| Mean-reversion backtest | `src/backtest.py` | Done |
| Trade construction | `src/trade.py` | Done; draft pitch in `reports/trade_pitch.md` |

## Curves

`uv run python -m src.curves` fits G-spread = a + b·ln(years to workout) for every issuer (4+ bonds) and sector, each month, and writes `data/processed/curve_signals.csv`, `curve_params.csv`, and `reports/figures/sector_curves_<month>.png`.

- Spreads are first adjusted to a par-priced, new-issue equivalent (`par_spread`). Within an issuer, premium bonds trade ~1–2.5bp wider per price point and older bonds wider still (discount-bond tax treatment, off-the-run liquidity). Without this, "cheap" and "rich" just mean high and low coupon.
- Residuals are leave-one-out (judged against a curve fit on the issuer's other bonds), turned into robust z-scores per month. `curve_signal` flags bonds beyond ±1.5σ on both the issuer and sector curves.
- Caveat: sector curves ignore credit quality, so a weaker issuer looks cheap versus its sector (e.g. BIP versus Hydro One in Utilities). The regression step accounts for rating and leverage.

## Regression

`uv run python -m src.regression` fits G-spread on ln(years to workout), rating, leverage and coverage (non-banks), ln(size), price premium, ln(age), a callable flag, sector and bail-in dummies. It runs a cross-section each month (the point-in-time signal) and a pooled panel with month fixed effects and issuer-clustered errors (the coefficient table). Outputs: `data/processed/signals.csv` (curve + regression z-scores and the combined `signal`), `regression_monthly.csv`, `regression_pooled.txt`, `reports/figures/actual_vs_model_<month>.png`.

Results (Jan 2023 – Sep 2026, 4,708 bond-months, 12 issuers):
- Monthly R² 0.87–0.96 (PRD target 0.50), mostly because issuer-level variables nearly pin down each issuer's average spread.
- Significant and stable: maturity, rating (+5.8bp per notch), size (−4bp per log unit), premium (+1.5bp per price point), age, callable (+20bp), bail-in (+31bp vs legacy).
- **Leverage and coverage are not significant** (p 0.33 / 0.86) and flip sign in ~half the months: 7 non-bank issuers can't separate them from rating and sector.
- `signal` = cheap/rich only when the regression and both curves agree beyond ±1.5σ: ~9 bonds per month.
- Caveat: low-coupon 2020–21 vintages (e.g. TCPL 3.0% 2031, ENB 2.8% 2031, TELUS 2.8% 2031) cluster as rich even after the premium control. Pair bonds of similar coupon and vintage so a trade doesn't bet on that effect.

## Backtest

`uv run python -m src.backtest` uses each month's point-in-time signal and measures the bond's **excess** spread change over the next 1–3 months (own change − sector average, net of roll-down). Outputs: `data/processed/backtest_*.csv`, `reports/figures/backtest_quintiles.png`.

| Test | Result |
| --- | --- |
| Fama-MacBeth slope, forward excess change on regression residual | −0.05 (1m), −0.08 (2m), −0.10 (3m); Newey-West t −5 to −6; negative in 77–88% of months |
| Cheapest − richest quintile, 3m excess change | −3.0bp; monotonic across quintiles; negative every year 2023–2026 (−1.2 to −4.8bp) |
| Combined signal (regression + both curves), 3m | cheap bonds tighten 2.0bp, rich widen 2.5bp vs peers; hit rate 59–67% |
| Half-life of the residual | **~8–12 months**: 11.7m from the 3-month horizon, 8.8m pooled AR(1), 7.8m bias-corrected per bond |

Reading it honestly:
- The signal is real and stable, but slow: about 16% of a residual is gone after 3 months, mostly through the bond's own spread moving (−0.13 of −0.16), not the model's fair value.
- Raw per-bond AR(1) fits suggest a 3-month half-life; that is small-sample bias (~32 months per bond biases ρ down by ~(1+3ρ)/T). Don't quote it.
- A few bp over 3 months is about the size of a CAD IG bid/ask, so a trade needs a large residual and a holding period of months, plus carry, to pay.
- Prices are evaluated, not traded; structure detection uses each bond's full history (small look-ahead in classification only).

## Trade

`uv run python -m src.trade --exclude-sector Banks --pick 1` builds the pitch in `reports/trade_pitch.md` with `reports/figures/pair_history.png`; `uv run python -m src.trade` lists all candidates (`data/processed/trade_candidates.csv`).

- Candidates: same sector and call structure, workout within 1.5y, price within 8 points, issue vintage within 5 years, regression |z| ≥ 0.75, residual sign stable for 3 months. This keeps the coupon/vintage and callable effects out of the pair.
- Sizing: CS01-neutral (CS01 = modified duration × dirty price × face × 1bp), C$10mm on the long leg.
- Expected P&L over the horizon (default 12 months ≈ one half-life) = gap × (1 − 0.94^h) + yield carry (repo ≈ 2y GoC) + roll-down on each issuer curve − 2bp bid/ask per leg. Stop = 2× the pair's monthly volatility scaled to the horizon.
- The pitch reports rating actions, leverage trends and how long the gap has persisted, so reasons the model might be wrong are visible.

**Current pitch (Sep 30, 2026):** long Pembina 3.62% Apr-2029 / short TCPL 3.00% Sep-2029. Same sector, both 2019 vintage, prices 98.9 / 97.2. Pembina trades 13bp wider despite lower leverage (3.1x vs 4.4x) and similar ratings; 21bp residual gap. Over 12 months: +9.9bp (≈C$23k on C$10mm; CS01 C$2,346) = convergence +11.2, carry +7.9, roll −5.2, costs −4.0; stop 30bp wider. Risks: the gap has persisted ~20bp for a year as TC Energy deleveraged after the South Bow spin-off; Pembina is smaller and has no Moody's rating.

Bank pairs score higher (e.g. long BMO 4.54% 2028 / short TD 4.23% 2029, +10bp over 6 months) but the data can't tell covered, deposit-note and bail-in programs apart; check pricing supplements before using one.

## Differences from a Bloomberg pull

- Prices are the ETF's evaluated (model) prices, not trades. Check pitched bonds against CIRO trade data.
- Yields are computed from price; bank bail-in senior bonds callable a year early (4NC3, 5NC4, 6NC5) are priced to the call.
- Leverage uses reported (GAAP) figures, not company-adjusted EBITDA.
- `bloomberg_adapter/reference/bloomberg_template.csv` lists the Bloomberg fields to pull if terminal access comes back; a `bloomberg_csv.py` adapter can map them onto the same panel.

## Tests

```bash
uv run pytest
```
