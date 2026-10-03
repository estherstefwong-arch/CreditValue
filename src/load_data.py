"""Read and clean raw data: GoC benchmark yields and the Bloomberg bond panel."""

from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"

# Bank of Canada benchmark bond yield series, keyed by tenor in years.
GOC_SERIES = {
    2: "BD.CDN.2YR.DQ.YLD",
    3: "BD.CDN.3YR.DQ.YLD",
    5: "BD.CDN.5YR.DQ.YLD",
    7: "BD.CDN.7YR.DQ.YLD",
    10: "BD.CDN.10YR.DQ.YLD",
}

# Numeric rating scale: AAA = 1 ... BBB- = 10 (lower is better).
_SP_SCALE = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-"]
_MOODY_SCALE = ["Aaa", "Aa1", "Aa2", "Aa3", "A1", "A2", "A3", "Baa1", "Baa2", "Baa3"]
_DBRS_SCALE = ["AAA", "AA (high)", "AA", "AA (low)", "A (high)", "A", "A (low)",
               "BBB (high)", "BBB", "BBB (low)"]
RATING_MAP = {
    **{r: i + 1 for i, r in enumerate(_SP_SCALE)},
    **{r: i + 1 for i, r in enumerate(_MOODY_SCALE)},
    **{r.upper(): i + 1 for i, r in enumerate(_DBRS_SCALE)},
}


def fetch_goc_yields(start="2023-01-01", end=None, save=True):
    """Download daily GoC benchmark yields (in %) from the Bank of Canada Valet API.

    Returns a DataFrame indexed by date with one column per tenor (years).
    """
    params = {"start_date": start}
    if end:
        params["end_date"] = end
    resp = requests.get(VALET_URL.format(series=",".join(GOC_SERIES.values())),
                        params=params, timeout=30)
    resp.raise_for_status()

    rows = []
    for obs in resp.json()["observations"]:
        row = {"date": obs["d"]}
        for tenor, series in GOC_SERIES.items():
            value = obs.get(series, {}).get("v")
            row[tenor] = float(value) if value not in (None, "") else np.nan
        rows.append(row)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    if save:
        RAW.mkdir(parents=True, exist_ok=True)
        df.to_csv(RAW / "goc_yields_daily.csv")
    return df


def load_goc_yields(monthly=True):
    """Load saved GoC yields; month-end resample by default to match the bond panel."""
    df = pd.read_csv(RAW / "goc_yields_daily.csv", index_col="date", parse_dates=True)
    df.columns = df.columns.astype(int)
    if monthly:
        df = df.resample("ME").last()
    return df


def rating_to_numeric(rating):
    """Convert one agency rating string to the 1-10 scale; NaN if missing or below BBB-."""
    if not isinstance(rating, str) or not rating.strip():
        return np.nan
    r = rating.strip().replace("*-", "").replace("*+", "").replace("*", "")
    return RATING_MAP.get(r, RATING_MAP.get(r.upper(), np.nan))


def average_rating(df, cols=("rtg_sp", "rtg_moody", "rtg_dbrs")):
    """Average the numeric rating across whichever agencies rate the bond."""
    present = [c for c in cols if c in df.columns]
    numeric = df[present].apply(lambda s: s.map(rating_to_numeric))
    return numeric.mean(axis=1)


def load_bonds(path=None):
    """Load the Bloomberg month-end bond panel and apply basic cleaning.

    Expects the columns in data/raw/bonds_template.csv.
    """
    path = path or RAW / "bonds_monthly.csv"
    df = pd.read_csv(path, parse_dates=["date", "maturity", "issue_date"])

    df["rating"] = average_rating(df)
    df["years_to_maturity"] = (df["maturity"] - df["date"]).dt.days / 365.25

    # PRD filters: IG only, 2-12 years, at least C$300mm outstanding.
    df = df[
        (df["rating"] <= 10)
        & df["years_to_maturity"].between(2, 12)
        & (df["amt_outstanding"] >= 300e6)
    ]
    return df.sort_values(["isin", "date"]).reset_index(drop=True)
