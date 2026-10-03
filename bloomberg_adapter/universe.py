"""Map raw ETF names to issuers and apply the PRD universe filters."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

ISSUER_MAP_PATH = Path(__file__).parent / "reference" / "issuers.csv"

# Canada's bail-in regime covers senior bank debt issued on or after this date with an
# original term over 400 days.
BAIL_IN_START = pd.Timestamp("2018-09-23")

MIN_YEARS, MAX_YEARS = 2.0, 12.0
BANK_SUB_DEBT_MIN_TERM = 9.5  # CAD bank bonds this long at issue are NVCC sub debt (10NC5)
HYBRID_MIN_TERM = 35.0  # 60NC5 / 60NC10 hybrids

_SUFFIXES = {"MTN", "REGS", "144A"}
_ALIASES = {"COMMS": "COMMUNICATIONS"}


def normalize_name(raw: str) -> str:
    """'ROGERS COMMS INC.' -> 'ROGERS COMMUNICATIONS INC'; strips MTN / RegS / 144A / call tags."""
    tokens = re.sub(r"\.", "", raw.upper()).split()
    tokens = [_ALIASES.get(t, t) for t in tokens]
    tokens = [t for t in tokens if not re.fullmatch(r"\d+NC\d+", t)]  # call tags, e.g. 60NC10
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def load_issuer_map(path: Path = ISSUER_MAP_PATH) -> pd.DataFrame:
    m = pd.read_csv(path, dtype=str, keep_default_na=False)
    m["include"] = m["include"].str.lower() == "true"
    return m.set_index("name")


def classify(holdings: pd.DataFrame, issuer_map: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add issuer, parent, sector, seniority, and exclude_reason (empty = in universe).

    Expects columns: date, name, coupon, maturity, issue_date, currency.
    """
    issuer_map = load_issuer_map() if issuer_map is None else issuer_map
    df = holdings.copy()
    df["clean_name"] = df["name"].map(normalize_name)
    mapped = issuer_map.reindex(df["clean_name"])
    for col in ["issuer", "parent", "sector"]:
        df[col] = mapped[col].to_numpy()
    included = mapped["include"].fillna(False).astype(bool).to_numpy()

    years_left = (df["maturity"] - df["date"]).dt.days / 365.25
    term = (df["maturity"] - df["issue_date"]).dt.days / 365.25
    is_bank = df["sector"] == "Banks"

    df["seniority"] = np.where(
        is_bank,
        np.where((df["issue_date"] >= BAIL_IN_START) & (term > 400 / 365), "senior_bail_in", "senior_legacy"),
        "senior",
    )

    # First matching reason wins, so order from broadest to narrowest.
    reasons = [
        (df["issuer"].isna(), "not a target issuer"),
        (~included, "excluded issuer"),
        (df["currency"] != "CAD", "not CAD"),
        (~(df["coupon"] > 0), "no fixed coupon"),
        (term >= HYBRID_MIN_TERM, "hybrid (long-dated)"),
        (is_bank & (term >= BANK_SUB_DEBT_MIN_TERM), "bank sub debt (NVCC)"),
        (years_left < MIN_YEARS, "under 2y to maturity"),
        (years_left > MAX_YEARS, "over 12y to maturity"),
    ]
    df["exclude_reason"] = np.select([cond for cond, _ in reasons], [r for _, r in reasons], default="")
    return df
