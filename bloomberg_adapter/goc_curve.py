"""Interpolate the GoC benchmark curve to any maturity and compute G-spreads."""

import numpy as np
import pandas as pd


def years_between(start: pd.Series, end: pd.Series) -> pd.Series:
    return (pd.to_datetime(end) - pd.to_datetime(start)).dt.days / 365.25


def align_to_curve_dates(dates: pd.Series, curve: pd.DataFrame, max_lag_days: int = 5) -> pd.Series:
    """Map each date to the latest curve date on or before it.

    Bond and curve month-ends can differ when a holiday closes one market but not the other
    (e.g. Sept 30). Raises if no curve date falls within max_lag_days.
    """
    dates = pd.to_datetime(pd.Series(dates)).dt.normalize()
    curve_dates = pd.DataFrame({"curve_date": sorted(curve["date"].unique())})
    left = pd.DataFrame({"date": dates, "pos": range(len(dates))}).sort_values("date")
    hit = pd.merge_asof(left, curve_dates, left_on="date", right_on="curve_date").sort_values("pos")
    lag = (hit["date"] - hit["curve_date"]).dt.days
    if hit["curve_date"].isna().any() or (lag > max_lag_days).any():
        bad = hit.loc[hit["curve_date"].isna() | (lag > max_lag_days), "date"].dt.strftime("%Y-%m-%d")
        raise KeyError(f"no GoC curve within {max_lag_days} days before: {sorted(set(bad))}")
    return pd.Series(hit["curve_date"].to_numpy(), index=dates.index)


def interpolate_goc_yield(curve: pd.DataFrame, dates: pd.Series, maturity_years: pd.Series) -> pd.Series:
    """GoC yield (%) at each (date, remaining maturity), linear in maturity.

    Maturities outside the curve's tenors take the nearest tenor's yield (flat extrapolation).
    Raises KeyError if a date has no curve.
    """
    dates = pd.to_datetime(pd.Series(dates)).dt.normalize()
    maturity_years = pd.Series(maturity_years, index=dates.index, dtype="float64")
    by_date = {d: g.sort_values("tenor_years") for d, g in curve.groupby("date")}

    missing = set(dates.unique()) - set(by_date)
    if missing:
        raise KeyError(f"no GoC curve for dates: {sorted(d.strftime('%Y-%m-%d') for d in missing)}")

    out = pd.Series(np.nan, index=dates.index, dtype="float64")
    for d, idx in dates.groupby(dates).groups.items():
        g = by_date[d]
        out[idx] = np.interp(maturity_years[idx], g["tenor_years"], g["yield"])
    return out


def g_spread_bp(bond_yield: pd.Series, goc_yield: pd.Series) -> pd.Series:
    """G-spread in basis points from yields in percent."""
    return (bond_yield - goc_yield) * 100
