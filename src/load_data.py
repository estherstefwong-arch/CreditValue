"""Load the model inputs built by bloomberg_adapter (run `python -m bloomberg_adapter.build_panel`)."""

from pathlib import Path

import pandas as pd

from bloomberg_adapter.boc_valet import to_month_end

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

GOC_DAILY = RAW / "boc" / "goc_benchmark_daily.csv"
BOND_PANEL = PROCESSED / "bond_panel.csv"
FUNDAMENTALS = PROCESSED / "issuer_fundamentals.csv"


def load_goc_yields(monthly=True):
    """GoC benchmark yields (%) indexed by date, one column per tenor in years.

    Month-end (last business day) by default to match the bond panel.
    """
    long = pd.read_csv(GOC_DAILY, parse_dates=["date"])
    if monthly:
        long = to_month_end(long)  # drops an in-progress final month
    df = long.pivot(index="date", columns="tenor_years", values="yield").sort_index()
    df.columns = df.columns.astype(float)
    return df


def load_bonds(path=None, prd_filters=True):
    """Month-end bond panel: one row per bond per month with yield, G-spread and fundamentals.

    `rating` is the average agency rating (AAA = 1 … BBB- = 10). With prd_filters, bonds known
    to fail the BBB- or C$300mm screens are dropped; bonds with unknown rating/size are kept.
    The 2-12 year and senior/fixed-coupon screens are already applied upstream.
    """
    df = pd.read_csv(path or BOND_PANEL,
                     parse_dates=["date", "maturity", "workout_date", "issue_date", "fundamentals_period_end"])
    df = df.rename(columns={"rating_numeric": "rating", "g_spread_bp": "g_spread"})
    df["years_to_maturity"] = (df["maturity"] - df["date"]).dt.days / 365.25
    if prd_filters:
        df = df[(df["rating_ok"] != 0) & (df["size_ok"] != 0)]
    return df.sort_values(["isin", "date"]).reset_index(drop=True)


def load_fundamentals():
    """Issuer fundamentals by period end, with the filing date they became available."""
    return pd.read_csv(FUNDAMENTALS, parse_dates=["period_end", "available_date"])
