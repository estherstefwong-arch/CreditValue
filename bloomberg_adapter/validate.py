"""Sanity checks on the final panel. FAIL means the data is wrong; WARN means it is incomplete."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

DURATION_TOLERANCE = 0.25
SPREAD_RANGE_BP = (0, 300)
MIN_BONDS_PER_MONTH = 60


@dataclass
class Check:
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str


def _status(ok: bool, warn_only: bool = False) -> str:
    return "PASS" if ok else ("WARN" if warn_only else "FAIL")


def check_structure(panel: pd.DataFrame) -> list[Check]:
    per_month = panel.groupby("date")["isin"].nunique()
    sectors = panel.groupby("date")["sector"].nunique()
    return [
        Check("bonds per month-end", _status(per_month.min() >= MIN_BONDS_PER_MONTH),
              f"{per_month.min()}–{per_month.max()} over {len(per_month)} month-ends"),
        Check("all four sectors every month", _status((sectors == 4).all()),
              f"{int((sectors < 4).sum())} month-ends missing a sector"),
        Check("no duplicate bond-months", _status(not panel.duplicated(["date", "isin"]).any()), "key: date + isin"),
    ]


def check_yields(panel: pd.DataFrame, classified: pd.DataFrame) -> list[Check]:
    universe = classified[classified["exclude_reason"] == ""]
    gap = universe["duration_gap"].abs()
    off = int((gap > DURATION_TOLERANCE).sum())
    return [
        Check("yield vs ETF duration", _status(off == 0),
              f"{off} rows off by >{DURATION_TOLERANCE}y; median gap {gap.median():.3f}y "
              "(stands in for the CIRO cross-check, which can't be scripted)"),
        Check("yields solved", _status(panel["yield"].notna().all()), f"{int(panel['yield'].isna().sum())} unsolved"),
    ]


def check_spreads(panel: pd.DataFrame) -> list[Check]:
    g = panel["g_spread_bp"]
    lo, hi = SPREAD_RANGE_BP
    outside = panel[(g < lo) | (g > hi)]
    names = ", ".join(sorted(f"{r.issuer} {r.coupon:.2f}% {r.maturity:%Y}" for r in
                             outside.drop_duplicates("isin").itertuples()))
    checks = [Check(f"G-spread within {lo}–{hi}bp", _status(len(outside) / len(panel) < 0.01, warn_only=True),
                    f"{len(outside)} of {len(panel)} rows outside ({names or 'none'}); "
                    f"median {g.median():.0f}bp")]

    med = panel.groupby(["date", "sector"])["g_spread_bp"].median().unstack()
    banks_tightest = (med["Banks"] <= med[["Telecom", "Pipelines"]].min(axis=1)).mean()
    checks.append(Check("banks tighter than telecom and pipelines", _status(banks_tightest >= 0.9, warn_only=True),
                        f"true in {banks_tightest:.0%} of month-ends"))

    def slope(g: pd.DataFrame) -> float:
        if len(g) < 5 or g["years_to_workout"].nunique() < 3:
            return np.nan
        return np.polyfit(np.log(g["years_to_workout"]), g["g_spread_bp"], 1)[0]

    slopes = panel.groupby(["date", "sector"])[["years_to_workout", "g_spread_bp"]].apply(slope).dropna()
    upward = (slopes > 0).mean()
    by_sector = (slopes > 0).groupby(level="sector").mean()
    checks.append(Check("spread curves slope upward with maturity", _status(upward >= 0.8, warn_only=True),
                        f"{upward:.0%} of sector-months; " + ", ".join(f"{s} {v:.0%}" for s, v in by_sector.items())))
    return checks


def check_coverage(panel: pd.DataFrame, fundamentals: pd.DataFrame) -> list[Check]:
    non_bank = panel[~panel["is_bank"]]
    lev = non_bank["leverage"].notna().mean()
    rating = panel["rating_numeric"].notna().mean()
    amount = panel["amount_outstanding"].notna().mean()
    stale = (panel["price_quality"] == "stale").mean()
    no_fund = sorted(set(non_bank.loc[non_bank["leverage"].isna(), "parent"].dropna()))
    return [
        Check("leverage coverage (non-banks)", _status(lev >= 0.95, warn_only=True),
              f"{lev:.0%} of bond-months; missing for {', '.join(no_fund) or 'none'}"),
        Check("rating coverage", _status(rating >= 0.95, warn_only=True),
              f"{rating:.0%} of bond-months (fill reference/ratings.csv)"),
        Check("amount outstanding coverage", _status(amount >= 0.95, warn_only=True),
              f"{amount:.0%} of bond-months; size filter not applied where missing"),
        Check("stale prices", _status(stale < 0.02, warn_only=True), f"{stale:.1%} of bond-months"),
    ]


def run_checks(panel: pd.DataFrame, classified: pd.DataFrame, fundamentals: pd.DataFrame) -> list[Check]:
    return check_structure(panel) + check_yields(panel, classified) + check_spreads(panel) + \
        check_coverage(panel, fundamentals)


def render_report(checks: list[Check], panel: pd.DataFrame) -> str:
    lines = ["# Bond panel validation", "",
             f"{len(panel):,} bond-months, {panel['isin'].nunique()} bonds, "
             f"{panel['date'].nunique()} month-ends ({panel['date'].min():%Y-%m} to {panel['date'].max():%Y-%m}).", "",
             "| Status | Check | Detail |", "| --- | --- | --- |"]
    lines += [f"| {c.status} | {c.name} | {c.detail} |" for c in checks]

    med = panel.groupby([panel["date"].dt.year, "sector"])["g_spread_bp"].median().unstack().round(0)
    lines += ["", "## Median G-spread by sector (bp, all month-ends in year)", "",
              "| Year | " + " | ".join(med.columns) + " |", "| --- |" + " --- |" * len(med.columns)]
    lines += [f"| {y} | " + " | ".join(f"{v:.0f}" for v in row) + " |" for y, row in med.iterrows()]
    return "\n".join(lines) + "\n"
