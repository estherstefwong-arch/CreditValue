"""Hand-maintained reference data: issuer ratings and bond amounts outstanding."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

REFERENCE_DIR = Path(__file__).parent / "reference"
RATINGS_PATH = REFERENCE_DIR / "ratings.csv"
AMOUNTS_PATH = REFERENCE_DIR / "amount_outstanding.csv"

_SP = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-"]
_MOODYS = ["Aaa", "Aa1", "Aa2", "Aa3", "A1", "A2", "A3", "Baa1", "Baa2", "Baa3", "Ba1", "Ba2", "Ba3"]
_DBRS = ["AAA", "AA (high)", "AA", "AA (low)", "A (high)", "A", "A (low)",
         "BBB (high)", "BBB", "BBB (low)", "BB (high)", "BB", "BB (low)"]
_SCALES = {
    "sp": {r: i + 1 for i, r in enumerate(_SP)},
    "moodys": {r: i + 1 for i, r in enumerate(_MOODYS)},
    "dbrs": {r: i + 1 for i, r in enumerate(_DBRS)},
}

RATINGS_COLUMNS = ["issuer", "seniority", "effective_date", "sp", "moodys", "dbrs", "source", "quote", "notes"]
AGENCIES = ["sp", "moodys", "dbrs"]
WITHDRAWN = "WR"  # agency withdrew its rating: stops the carry-forward
AMOUNTS_COLUMNS = ["isin", "issuer", "coupon", "maturity", "amount_outstanding", "source"]


def normalize_rating(agency: str, raw: str | None) -> str:
    """'BBB(high)' -> 'BBB (high)', drops outlooks / footnotes; '' if not a recognized rating."""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    if raw.strip().upper() == WITHDRAWN:
        return WITHDRAWN
    r = re.sub(r"\s*\(\s*(high|low)\s*\)", r" (\1)", raw.strip(), flags=re.I)
    r = re.sub(r"\((high|low)\)", lambda m: f"({m.group(1).lower()})", r, flags=re.I)
    r = re.sub(r"\((?!high\)|low\))[^)]*\)", "", r)  # (hyb), (stable), (sf) ...
    r = re.split(r"\s*(?:\*|,|/|stable|negative|positive|outlook)", r, flags=re.I)[0].strip()
    if r not in _SCALES[agency]:
        raise ValueError(f"unrecognized {agency} rating: {raw!r}")
    return r


def rating_to_numeric(sp: str | None, moodys: str | None, dbrs: str | None) -> float:
    """Average of available agency ratings on the AAA = 1 … BBB- = 10 scale."""
    scores = [
        _SCALES[agency][str(r).strip()]
        for agency, r in (("sp", sp), ("moodys", moodys), ("dbrs", dbrs))
        if isinstance(r, str) and r.strip() and r.strip() != WITHDRAWN
    ]
    return float(np.mean(scores)) if scores else np.nan


def load_ratings(path: Path = RATINGS_PATH) -> pd.DataFrame:
    """One row per issuer (and seniority for banks) per rating change."""
    if not path.exists():
        return pd.DataFrame(columns=RATINGS_COLUMNS + ["rating_numeric"])
    r = pd.read_csv(path, dtype=str, keep_default_na=False)
    r = r[r["effective_date"] != ""].copy()
    r["effective_date"] = pd.to_datetime(r["effective_date"])
    for agency in AGENCIES:
        r[agency] = [normalize_rating(agency, x) for x in r[agency]]
    # A rating-action row changes one agency; carry the others forward from earlier rows.
    r = r.sort_values("effective_date")
    r[AGENCIES] = r[AGENCIES].replace("", np.nan).groupby([r["issuer"], r["seniority"]]).ffill().fillna("")
    r["rating_numeric"] = [rating_to_numeric(*x) for x in zip(r["sp"], r["moodys"], r["dbrs"])]
    return r.dropna(subset=["rating_numeric"]).reset_index(drop=True)


def attach_ratings(panel: pd.DataFrame, ratings: pd.DataFrame) -> pd.DataFrame:
    """Latest rating effective on or before each row's date.

    A row with seniority blank applies to every seniority of that issuer; a seniority-specific
    row (e.g. senior_legacy) wins over it.
    """
    panel = panel.copy()
    panel["rating_numeric"] = np.nan
    if ratings.empty:
        return panel
    for (issuer, seniority), idx in panel.groupby(["issuer", "seniority"]).groups.items():
        r = ratings[(ratings["issuer"] == issuer) & ratings["seniority"].isin(["", seniority])]
        if r.empty:
            continue
        # Specific seniority sorts after blank, so it wins on the same effective date.
        r = r.assign(specific=r["seniority"] != "").sort_values(["effective_date", "specific"])
        rows = panel.loc[idx, ["date"]].sort_values("date")
        hit = pd.merge_asof(rows, r[["effective_date", "rating_numeric"]],
                            left_on="date", right_on="effective_date")
        panel.loc[rows.index, "rating_numeric"] = hit["rating_numeric"].to_numpy()
    return panel


def load_amounts(path: Path = AMOUNTS_PATH) -> pd.Series:
    """Amount outstanding (C$) by ISIN, from rows that have been filled in."""
    if not path.exists():
        return pd.Series(dtype="float64")
    a = pd.read_csv(path, dtype={"isin": str})
    a = a.dropna(subset=["amount_outstanding"])
    return a.set_index("isin")["amount_outstanding"].astype("float64")


def write_templates(universe: pd.DataFrame) -> None:
    """Create or extend the manual CSVs with any universe issuers / ISINs not yet listed."""
    REFERENCE_DIR.mkdir(exist_ok=True)

    latest = universe.sort_values("date").groupby("isin").tail(1)
    latest = latest.sort_values("etf_par_held", ascending=False)  # biggest first: fill these first
    new_amounts = pd.DataFrame({
        "isin": latest["isin"], "issuer": latest["issuer"], "coupon": latest["coupon"],
        "maturity": latest["maturity"].dt.strftime("%Y-%m-%d"),
        "amount_outstanding": np.nan, "source": "",
    })
    _append_new(AMOUNTS_PATH, new_amounts, key=["isin"], columns=AMOUNTS_COLUMNS)

    pairs = universe[["issuer", "sector", "seniority"]].drop_duplicates()
    rows = []
    for issuer, g in pairs.groupby("issuer"):
        seniorities = sorted(g["seniority"]) if g["sector"].iat[0] == "Banks" else [""]
        rows += [{"issuer": issuer, "seniority": s, "effective_date": "", "sp": "", "moodys": "",
                  "dbrs": "", "source": ""} for s in seniorities]
    _append_new(RATINGS_PATH, pd.DataFrame(rows), key=["issuer", "seniority"], columns=RATINGS_COLUMNS)


def _append_new(path: Path, new: pd.DataFrame, key: list[str], columns: list[str]) -> None:
    if path.exists():
        existing = pd.read_csv(path, dtype=str, keep_default_na=False)
        seen = set(map(tuple, existing[key].to_numpy()))
        new = new[[tuple(k) not in seen for k in new[key].astype(str).to_numpy()]]
        out = pd.concat([existing, new.astype(str).replace("nan", "")], ignore_index=True)
    else:
        out = new
    out[columns].to_csv(path, index=False)
