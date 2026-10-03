"""GoC curve interpolation and G-spreads."""

import numpy as np
import pandas as pd


def interpolate_goc(curve, maturity):
    """Interpolate the GoC yield (%) at a maturity in years.

    `curve` is a Series indexed by tenor (years). Linear between benchmarks;
    flat beyond the 2y and 10y ends, which costs a few bp for 10-12y bonds.
    """
    curve = curve.dropna().sort_index()
    return np.interp(maturity, curve.index.to_numpy(dtype=float), curve.to_numpy())


def add_g_spreads(bonds, goc_monthly):
    """Add `goc_yield` (%) and `g_spread` (bp) columns to the bond panel.

    `bonds` needs `date`, `years_to_maturity`, `yield` (%); `goc_monthly` is
    month-end GoC yields indexed by date with tenor columns.
    """
    out = bonds.copy()
    month_end = out["date"] + pd.offsets.MonthEnd(0)

    goc = np.full(len(out), np.nan)
    for date, idx in out.groupby(month_end).groups.items():
        if date not in goc_monthly.index:
            continue
        pos = out.index.get_indexer(idx)
        goc[pos] = interpolate_goc(goc_monthly.loc[date],
                                   out.loc[idx, "years_to_maturity"].to_numpy())

    out["goc_yield"] = goc
    out["g_spread"] = (out["yield"] - out["goc_yield"]) * 100
    return out
