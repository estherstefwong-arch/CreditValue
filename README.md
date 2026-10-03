# Canadian Credit Relative Value

Finds Canadian investment-grade corporate bonds trading cheap or rich versus their credit fundamentals, and turns the strongest signal into a CS01-neutral long/short trade. See the [PRD](PRD%20Canadian%20Corporate%20Bond%20Relative%20Value%20Analysis.md) for the full plan.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Status

| Step | Module | Status |
| --- | --- | --- |
| GoC yields (Bank of Canada Valet API) | `src/load_data.py` | Done |
| Rating scale (S&P / Moody's / DBRS → 1–10) | `src/load_data.py` | Done |
| G-spreads | `src/spreads.py` | Done, waiting on bond data |
| Bloomberg bond panel | `data/raw/bonds_monthly.csv` | **To pull** |
| Issuer and sector curves | `src/curves.py` | Not started |
| Fundamental regression | `src/regression.py` | Not started |
| Mean-reversion backtest | `src/backtest.py` | Not started |
| Trade construction | `src/trade.py` | Not started |

## Pulling GoC yields

```bash
python -c "from src.load_data import fetch_goc_yields; fetch_goc_yields(start='2023-01-01')"
```

Saves daily 2/3/5/7/10-year benchmark yields to `data/raw/goc_yields_daily.csv`.

## Bloomberg pull

Save month-end history for the last 3 years as `data/raw/bonds_monthly.csv`, one row per bond per month, with the columns in `data/raw/bonds_template.csv`:

| Column | Bloomberg field | Notes |
| --- | --- | --- |
| `date` | — | Month-end date |
| `isin` | `ID_ISIN` | |
| `ticker`, `issuer` | `TICKER`, `ISSUER` | |
| `parent` | `ULT_PARENT_TICKER_EXCHANGE` | Used to match fundamentals |
| `sector` | — | Banks, Telecom, Pipelines, Utilities |
| `bail_in` | `BAIL_IN_BOND_DESIGNATION` | `Y`/`N`; banks only |
| `coupon`, `maturity`, `issue_date` | `CPN`, `MATURITY`, `ISSUE_DT` | |
| `amt_outstanding` | `AMT_OUTSTANDING` | In C$ |
| `rtg_sp`, `rtg_moody`, `rtg_dbrs` | `RTG_SP`, `RTG_MOODY`, `RTG_DBRS` | |
| `price`, `yield` | `PX_LAST`, `YLD_YTM_MID` | Use `BDH` with monthly periodicity |
| `g_spread_bbg` | `G_SPREAD_MID_CALC` | Cross-check against our own G-spread |

Use the screen filters from the PRD: CAD, senior unsecured, fixed coupon, BBB- or better, 2–12 years, at least C$300mm outstanding.
