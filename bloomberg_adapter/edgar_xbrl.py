"""Issuer fundamentals (net debt, EBITDA, interest, assets) from SEC EDGAR XBRL company facts."""

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from bloomberg_adapter.schema import ISSUER_FUNDAMENTALS, ISSUER_FUNDAMENTALS_KEY, conform

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "edgar"
PROCESSED_DIR = ROOT / "data" / "processed"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


def _env_file_value(key: str) -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"')
    return None


# SEC asks for a contact in the User-Agent: set SEC_USER_AGENT in the environment or in .env.
USER_AGENT = os.environ.get("SEC_USER_AGENT") or _env_file_value("SEC_USER_AGENT")

FORMS = ("10-K", "10-Q", "40-F", "20-F", "6-K", "10-K/A", "40-F/A", "20-F/A")

# Each metric lists alternative tag sets; see resolve() for how they combine.
_USGAAP_INTEREST = [["InterestExpenseNonoperating"], ["InterestExpense"]]
ISSUERS = {
    "ENB": {"cik": 895728, "taxonomy": "us-gaap", "unit": "CAD",
            "debt": [["LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                      "?ShortTermBorrowings", "?CommercialPaper"]],
            "cash": [["CashAndCashEquivalentsAtCarryingValue"]],
            "ebit": [["OperatingIncomeLoss"]], "da": [["DepreciationDepletionAndAmortization"]],
            "addbacks": ["GoodwillImpairmentLoss", "ImpairmentOfLongLivedAssetsHeldForUse"],
            "interest": _USGAAP_INTEREST, "assets": [["Assets"]]},
    "TRP": {"cik": 1232384, "taxonomy": "us-gaap", "unit": "CAD",
            "debt": [["OtherLongTermDebtNoncurrent", "LongTermDebtCurrent", "?OtherShortTermBorrowings"]],
            "cash": [["CashAndCashEquivalentsAtCarryingValue"]],
            "ebit": [["OperatingIncomeLoss"]], "da": [["DepreciationDepletionAndAmortization"]],
            "addbacks": ["AssetImpairmentCharges"],
            "interest": _USGAAP_INTEREST, "assets": [["Assets"]]},
    "FTS": {"cik": 1666175, "taxonomy": "us-gaap", "unit": "CAD",
            "debt": [["LongTermDebtNoncurrent", "LongTermDebtCurrent", "?ShortTermBorrowings"]],
            "cash": [["CashAndCashEquivalentsAtCarryingValue"]],
            "ebit": [["OperatingIncomeLoss"]], "da": [["DepreciationDepletionAndAmortization"]],
            "interest": _USGAAP_INTEREST, "assets": [["Assets"]]},
    "BCE": {"cik": 718940, "taxonomy": "ifrs-full", "unit": "CAD",
            "debt": [["LongtermBorrowings", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"]],
            "cash": [["Cash", "?CashEquivalents"]],
            # No operating-income tag: rebuild EBIT as pre-tax income + interest expense.
            "ebit": [["ProfitLossBeforeTax", "InterestExpense"],
                     ["ProfitLossBeforeTax", "InterestPaidClassifiedAsOperatingActivities"]],
            "addbacks": ["ImpairmentLoss"],
            "deductions": ["AdjustmentsForGainLossOnDisposalOfInvestmentsInSubsidiariesJointVenturesAndAssociates"],
            "da": [["AdjustmentsForDepreciationAndAmortisationExpense"], ["DepreciationAndAmortisationExpense"]],
            "interest": [["InterestExpense"], ["InterestPaidClassifiedAsOperatingActivities"]], "assets": [["Assets"]]},
    "RCI": {"cik": 733099, "taxonomy": "ifrs-full", "unit": "CAD",
            "debt": [["LongtermBorrowings", "CurrentPortionOfLongtermBorrowings", "?ShorttermBorrowings"]],
            "cash": [["CashAndCashEquivalents"]],
            # Rogers tags its Adjusted EBITDA line (before D&A) as operating profit.
            "ebitda": [["ProfitLossFromOperatingActivities"]],
            "interest": [["InterestExpenseOnBorrowings"], ["FinanceCosts"]], "assets": [["Assets"]]},
    "TU": {"cik": 868675, "taxonomy": "ifrs-full", "unit": "CAD",
           "debt": [["LongtermBorrowings", "CurrentPortionOfLongtermBorrowings", "?ShorttermBorrowings"]],
           "cash": [["CashAndCashEquivalents"]],
           "ebit": [["ProfitLossFromOperatingActivities"]], "da": [["AdjustmentsForDepreciationAndAmortisationExpense"]],
           "interest": [["InterestExpenseOnBorrowings"], ["InterestExpense"]], "assets": [["Assets"]]},
    "PPL": {"cik": 1546066, "taxonomy": "ifrs-full", "unit": "CAD",
            "debt": [["LongtermBorrowings", "CurrentPortionOfLongtermBorrowings"]],
            "cash": [["CashAndCashEquivalents"]],
            "ebit": [["ProfitLossFromOperatingActivities"]], "da": [["AdjustmentsForDepreciationAndAmortisationExpense"]],
            "addbacks": ["ImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"],
            "interest": [["InterestExpenseOnBorrowings"]], "assets": [["Assets"]]},
    "BIP": {"cik": 1406234, "taxonomy": "ifrs-full", "unit": "USD",
            "net_debt": [["NetDebt"]],  # BIP tags net debt directly, not gross borrowings
            "ebit": [["ProfitLossFromOperatingActivities"]], "da": [["AdjustmentsForDepreciationAndAmortisationExpense"]],
            "interest": [["InterestExpense"]], "assets": [["Assets"]]},
}


def _latest(*dates: pd.Series) -> pd.Series:
    """Row-wise latest of several filing-date series (missing values ignored)."""
    return pd.concat([pd.to_datetime(d) for d in dates], axis=1).max(axis=1)


def download(parent: str) -> dict:
    path = RAW_DIR / f"CIK{ISSUERS[parent]['cik']:010d}.json"
    if not path.exists():
        if not USER_AGENT:
            raise RuntimeError('Set SEC_USER_AGENT="Your Name you@example.com" in the environment or .env')
        resp = requests.get(FACTS_URL.format(cik=ISSUERS[parent]["cik"]), headers={"User-Agent": USER_AGENT}, timeout=60)
        resp.raise_for_status()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        time.sleep(0.2)  # SEC fair-access limit is 10 requests/second
    return json.loads(path.read_text())


def _facts(company: dict, taxonomy: str, tag: str, unit: str) -> pd.DataFrame:
    """First-filed value per (start, end) for one tag: what the market knew at the time."""
    obs = company["facts"].get(taxonomy, {}).get(tag, {}).get("units", {}).get(unit, [])
    df = pd.DataFrame([o for o in obs if o.get("form") in FORMS])
    if df.empty:
        return pd.DataFrame(columns=["start", "end", "val", "filed"])
    if "start" not in df:
        df["start"] = None
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df = df.sort_values("filed").drop_duplicates(["start", "end"], keep="first")
    return df[["start", "end", "val", "filed"]]


def instant_series(company: dict, taxonomy: str, tag: str, unit: str) -> pd.DataFrame:
    """Balance-sheet values by period end."""
    f = _facts(company, taxonomy, tag, unit)
    return f[f["start"].isna()].set_index("end")[["val", "filed"]]


def ltm_series(company: dict, taxonomy: str, tag: str, unit: str) -> pd.DataFrame:
    """Last-twelve-month values by period end.

    Uses an annual fact when one ends on that date. Otherwise rebuilds it from a year-to-date
    fact: LTM = YTD + prior fiscal year - prior-year YTD.
    """
    f = _facts(company, taxonomy, tag, unit)
    f = f[f["start"].notna()].copy()
    f["days"] = (f["end"] - f["start"]).dt.days
    by_span = {(r.start, r.end): r for r in f.itertuples()}
    annual = f[f["days"].between(350, 380)]
    rows = {r.end: (r.val, r.filed) for r in annual.itertuples()}
    annual_by_end = {r.end: r for r in annual.itertuples()}

    for r in f[f["days"].between(80, 290)].itertuples():  # Q1, H1, 9M year-to-date
        if r.end in rows:
            continue
        fy_prev = annual_by_end.get(r.start - pd.Timedelta(days=1))
        ytd_prev = by_span.get((r.start - pd.DateOffset(years=1), r.end - pd.DateOffset(years=1)))
        if fy_prev is None or ytd_prev is None:
            continue
        rows[r.end] = (r.val + fy_prev.val - ytd_prev.val, r.filed)
    out = pd.DataFrame.from_dict(rows, orient="index", columns=["val", "filed"])
    out.index = pd.DatetimeIndex(out.index)
    return out.sort_index()


def resolve(company: dict, spec: dict, metric: str, kind: str) -> pd.DataFrame:
    """One metric by period end, trying each alternative tag set.

    Tags in a set are summed and all required, except "?"-prefixed ones. An optional tag counts
    only when filed with the required ones. Where sets overlap, the earliest-filed one wins.
    """
    series_fn = instant_series if kind == "instant" else ltm_series
    alts = []
    for alternative in spec.get(metric, []):
        if isinstance(alternative, str):
            raise TypeError(f"{metric}: alternatives must be lists of tags")
        required = [series_fn(company, spec["taxonomy"], t, spec["unit"]) for t in alternative if not t.startswith("?")]
        optional = [series_fn(company, spec["taxonomy"], t[1:], spec["unit"]) for t in alternative if t.startswith("?")]
        ends = required[0].index
        for r in required[1:]:
            ends = ends.intersection(r.index)
        if ends.empty:
            continue
        val = sum(r["val"].reindex(ends) for r in required)
        filed = _latest(*[r["filed"].reindex(ends) for r in required])
        for o in optional:
            o = _known_by(o.reindex(ends), filed)
            val = val + o["val"].fillna(0)
        alts.append(pd.DataFrame({"val": val, "filed": filed}))
    if not alts:
        return pd.DataFrame({"val": pd.Series(dtype="float64"), "filed": pd.Series(dtype="datetime64[ns]")})
    out = pd.concat(alts)
    return out.sort_values("filed", kind="stable").groupby(level=0).head(1).sort_index()


def _known_by(x: pd.DataFrame, available: pd.Series, grace_days: int = 45) -> pd.DataFrame:
    late = x["filed"] > available + pd.Timedelta(days=grace_days)
    return x.assign(val=x["val"].mask(late))


def ebitda_series(company: dict, spec: dict) -> pd.DataFrame:
    """LTM EBITDA = EBIT + D&A + impairments - disposal gains (or a direct EBITDA tag).

    Add-backs only count when filed with the EBIT figure, so later restatements can't leak in.
    """
    if "ebitda" in spec:
        base = resolve(company, spec, "ebitda", "ltm")
    else:
        ebit, da = resolve(company, spec, "ebit", "ltm"), resolve(company, spec, "da", "ltm")
        ends = ebit.index.intersection(da.index)
        base = pd.DataFrame({"val": ebit["val"][ends] + da["val"][ends],
                             "filed": _latest(ebit["filed"][ends], da["filed"][ends])})
    val = base["val"].copy()
    for sign, key in ((1, "addbacks"), (-1, "deductions")):
        for tag in spec.get(key, []):
            adj = ltm_series(company, spec["taxonomy"], tag, spec["unit"]).reindex(base.index)
            in_time = adj["filed"] <= base["filed"] + pd.Timedelta(days=45)
            val += sign * adj["val"].where(in_time, 0).fillna(0)
    return pd.DataFrame({"val": val, "filed": base["filed"]})


def fundamentals_for(parent: str) -> pd.DataFrame:
    spec = ISSUERS[parent]
    company = download(parent)
    if "net_debt" in spec:
        net_debt = resolve(company, spec, "net_debt", "instant")
    else:
        debt, cash = resolve(company, spec, "debt", "instant"), resolve(company, spec, "cash", "instant")
        net_debt = pd.DataFrame({
            "val": debt["val"] - cash["val"].reindex(debt.index).fillna(0),
            "filed": _latest(debt["filed"], cash["filed"].reindex(debt.index)),
        })
    ebitda = ebitda_series(company, spec)
    interest, assets = resolve(company, spec, "interest", "ltm"), resolve(company, spec, "assets", "instant")

    ends = net_debt.index.intersection(ebitda.index)
    ends = ends[ends >= pd.Timestamp("2019-01-01")]
    # Availability follows the leverage inputs. Interest and assets first filed much later
    # (a tag adopted in a later year) are dropped rather than delaying the whole row.
    available = _latest(net_debt["filed"].reindex(ends), ebitda["filed"].reindex(ends))
    interest, assets = (_known_by(x.reindex(ends), available) for x in (interest, assets))
    return pd.DataFrame({
        "parent": parent,
        "period_end": ends,
        "available_date": available.to_numpy(),
        "net_debt": net_debt["val"].reindex(ends).to_numpy(),
        "ebitda": ebitda["val"].reindex(ends).to_numpy(),
        "interest_expense": interest["val"].reindex(ends).abs().to_numpy(),
        "total_assets": assets["val"].reindex(ends).to_numpy(),
        "currency": spec["unit"],
        "source": "sec_edgar_xbrl",
    })


def load_all() -> pd.DataFrame:
    frames = [fundamentals_for(p) for p in ISSUERS]
    return conform(pd.concat(frames, ignore_index=True), ISSUER_FUNDAMENTALS, ISSUER_FUNDAMENTALS_KEY)


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    f = load_all()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    f.to_csv(PROCESSED_DIR / "issuer_fundamentals_edgar.csv", index=False)

    show = f.assign(leverage=f["net_debt"] / f["ebitda"], coverage=f["ebitda"] / f["interest_expense"])
    show["period_end"] = show["period_end"].dt.strftime("%Y-%m-%d")
    show["available_date"] = show["available_date"].dt.strftime("%Y-%m-%d")
    print(show[["parent", "period_end", "available_date", "leverage", "coverage"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
