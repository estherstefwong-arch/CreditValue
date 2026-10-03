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
| Issuer and sector curves | `src/curves.py` | Not started |
| Fundamental regression | `src/regression.py` | Not started |
| Mean-reversion backtest | `src/backtest.py` | Not started |
| Trade construction | `src/trade.py` | Not started |

## Differences from a Bloomberg pull

- Prices are the ETF's evaluated (model) prices, not trades. Check pitched bonds against CIRO trade data.
- Yields are computed from price; bank bail-in senior bonds callable a year early (4NC3, 5NC4, 6NC5) are priced to the call.
- Leverage uses reported (GAAP) figures, not company-adjusted EBITDA.
- `bloomberg_adapter/reference/bloomberg_template.csv` lists the Bloomberg fields to pull if terminal access comes back; a `bloomberg_csv.py` adapter can map them onto the same panel.

## Tests

```bash
uv run pytest
```
