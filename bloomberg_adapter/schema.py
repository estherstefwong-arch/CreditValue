"""Canonical schemas that every adapter returns. Comments name the matching Bloomberg field."""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Column:
    name: str
    kind: str  # "date", "str", "float"
    required: bool = True  # required columns may not contain nulls
    min: float | None = None
    max: float | None = None


BOND_PANEL = [
    Column("date", "date"),
    Column("isin", "str"),  # ID_ISIN
    Column("issuer", "str"),  # ISSUER, normalized
    Column("parent", "str", required=False),  # ULT_PARENT_TICKER_EXCHANGE
    Column("sector", "str"),  # INDUSTRY_SECTOR: Banks / Telecom / Pipelines / Utilities
    Column("seniority", "str", required=False),  # PAYMENT_RANK: senior_bail_in / senior_legacy / senior
    Column("coupon", "float", min=0, max=20),  # CPN, %
    Column("maturity", "date"),  # MATURITY
    Column("workout_date", "date", required=False),  # WORKOUT_DT_MID: call date if priced to call
    Column("issue_date", "date", required=False),  # ISSUE_DT
    Column("amount_outstanding", "float", required=False, min=0),  # AMT_OUTSTANDING, C$
    Column("price", "float", min=1, max=250),  # PX_LAST, clean
    Column("yield", "float", required=False, min=-5, max=30),  # YLD_YTM_MID, %
    Column("rating_numeric", "float", required=False, min=1, max=22),  # AAA = 1 … BBB- = 10
    Column("source", "str"),
    Column("price_quality", "str", required=False),  # traded / evaluated / stale
    Column("etf_par_held", "float", required=False, min=0),  # no Bloomberg field; size proxy
]
BOND_PANEL_KEY = ["date", "isin"]

ISSUER_FUNDAMENTALS = [
    Column("parent", "str"),
    Column("period_end", "date"),
    Column("available_date", "date"),  # filing date; used to lag fundamentals
    Column("net_debt", "float", required=False),
    Column("ebitda", "float", required=False),
    Column("interest_expense", "float", required=False, min=0),
    Column("total_assets", "float", required=False, min=0),
    Column("currency", "str", required=False),  # ratios are unitless; levels are not
    Column("source", "str"),
]
ISSUER_FUNDAMENTALS_KEY = ["parent", "period_end"]

# Government of Canada benchmark curve, long format: one row per date per tenor.
GOC_CURVE = [
    Column("date", "date"),
    Column("tenor_years", "float", min=0, max=50),
    Column("yield", "float", min=-5, max=30),  # %
]
GOC_CURVE_KEY = ["date", "tenor_years"]


class SchemaError(ValueError):
    pass


def conform(df: pd.DataFrame, schema: list[Column], key: list[str]) -> pd.DataFrame:
    """Return df with exactly the schema's columns, coerced to the right types.

    Optional columns that are missing are added as nulls. Raises SchemaError on missing
    required columns, nulls in required columns, out-of-range values, or duplicate keys.
    """
    missing = [c.name for c in schema if c.required and c.name not in df.columns]
    if missing:
        raise SchemaError(f"missing required columns: {missing}")

    out = pd.DataFrame(index=df.index)
    for col in schema:
        values = df[col.name] if col.name in df.columns else pd.Series(None, index=df.index)
        if col.kind == "date":
            out[col.name] = pd.to_datetime(values).dt.normalize()
        elif col.kind == "float":
            out[col.name] = pd.to_numeric(values).astype("float64")
        else:
            out[col.name] = values.astype("string")

    errors = []
    for col in schema:
        s = out[col.name]
        if col.required and s.isna().any():
            errors.append(f"{col.name}: {int(s.isna().sum())} nulls")
        if col.min is not None and (s < col.min).any():
            errors.append(f"{col.name}: values below {col.min}")
        if col.max is not None and (s > col.max).any():
            errors.append(f"{col.name}: values above {col.max}")
    dupes = out.duplicated(subset=key).sum()
    if dupes:
        errors.append(f"{int(dupes)} duplicate rows on key {key}")
    if errors:
        raise SchemaError("; ".join(errors))

    return out.sort_values(key).reset_index(drop=True)
