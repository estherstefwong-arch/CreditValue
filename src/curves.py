"""Issuer and sector spread curves: G-spread = a + b·ln(years to workout), fit every month."""

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

from src.load_data import PROCESSED, ROOT, load_bonds

matplotlib.use("Agg")  # render to files; no display needed
import matplotlib.pyplot as plt  # noqa: E402  (must follow the backend choice)

FIGURES = ROOT / "reports" / "figures"
MIN_BONDS = 4  # leave-one-out needs at least 3 other bonds to fit a 2-parameter curve
Z_FLAG = 1.5

CURVES = {
    "issuer": ["date", "issuer", "seniority"],
    "sector": ["date", "sector", "seniority"],
}


def _fit_group(x: np.ndarray, y: np.ndarray) -> dict:
    """OLS fit plus leave-one-out residuals via the hat matrix: e_loo = e / (1 - h)."""
    X = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ beta
    resid = y - fitted
    h = np.einsum("ij,jk,ik->i", X, np.linalg.pinv(X.T @ X), X)
    with np.errstate(divide="ignore", invalid="ignore"):
        loo = np.where(h < 1 - 1e-9, resid / (1 - h), np.nan)
    rmse = float(np.sqrt(np.sum(resid**2) / max(len(y) - 2, 1)))
    return {"a": beta[0], "b": beta[1], "fitted": fitted, "resid": resid, "loo": loo, "rmse": rmse}


def fit_curves(panel: pd.DataFrame, by: list[str], min_bonds: int = MIN_BONDS, spread_col: str = "g_spread"):
    """Per-row fitted spread, in-sample and leave-one-out residuals; plus a parameter table.

    Groups with fewer than min_bonds bonds, or fewer than 3 distinct maturities, get NaN.
    """
    rows = pd.DataFrame(np.nan, index=panel.index, columns=["fitted", "resid", "loo_resid", "n"])
    params = []
    x_all = np.log(panel["years_to_workout"].to_numpy())
    y_all = panel[spread_col].to_numpy()
    for key, idx in panel.groupby(by, dropna=False).indices.items():
        x, y = x_all[idx], y_all[idx]
        if len(idx) < min_bonds or np.unique(np.round(x, 3)).size < 3:
            continue
        f = _fit_group(x, y)
        rows.iloc[idx] = np.column_stack([f["fitted"], f["resid"], f["loo"], np.full(len(idx), len(idx))])
        params.append({**dict(zip(by, key)), "a": f["a"], "b": f["b"], "n": len(idx), "rmse": f["rmse"]})
    return rows, pd.DataFrame(params)


def robust_z(resid: pd.Series, dates: pd.Series) -> pd.Series:
    """Residual divided by that month's robust std (1.4826 × median absolute deviation)."""
    def scale(s):
        mad = (s - s.median()).abs().median()
        return 1.4826 * mad if mad > 0 else np.nan
    return resid / resid.groupby(dates).transform(scale)


def premium_adjust(panel: pd.DataFrame, iterations: int = 3) -> pd.DataFrame:
    """Add par_spread: G-spread adjusted to a par-priced, newly issued bullet of the same issuer.

    Within an issuer, premium bonds, older bonds and callable bonds trade wider (tax treatment
    of discount bonds; off-the-run liquidity; call/extension risk on bank 5NC4-style notes).
    Each month, issuer-curve residuals are regressed on price - 100, ln(1 + age) and a callable
    flag; slopes are kept as premium_slope, age_slope and callable_slope.
    """
    out = panel.copy()
    premium = out["price"] - 100
    log_age = np.log1p(((out["date"] - out["issue_date"]).dt.days / 365.25).clip(lower=0))
    callable_ = (out["workout_date"] < out["maturity"]).astype(float)
    names = ["premium_slope", "age_slope", "callable_slope"]
    out["par_spread"] = out["g_spread"]
    for _ in range(iterations):
        rows, _ = fit_curves(out, CURVES["issuer"], spread_col="par_spread")
        resid = out["g_spread"] - rows["fitted"]
        slopes = {}
        for d, idx in out[resid.notna()].groupby("date").groups.items():
            X = np.column_stack([np.ones(len(idx)), premium[idx], log_age[idx], callable_[idx]])
            slopes[d] = np.linalg.lstsq(X, resid[idx].to_numpy(), rcond=None)[0][1:]
        slopes = pd.DataFrame(slopes, index=names).T
        out[names] = slopes.reindex(out["date"]).to_numpy()
        out["par_spread"] = (out["g_spread"] - out["premium_slope"] * premium - out["age_slope"] * log_age
                             - out["callable_slope"] * callable_)
    return out


def add_curve_signals(panel: pd.DataFrame, adjust_premium: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add {issuer,sector}_{fit,resid,n,z} columns and a combined rich/cheap flag.

    Curves are fit to par_spread (premium-adjusted) unless adjust_premium is False. resid is
    leave-one-out: spread minus the curve fitted on the issuer's (or sector's) other bonds.
    Positive = cheap (wide), negative = rich (tight).
    """
    out = premium_adjust(panel) if adjust_premium else panel.assign(par_spread=panel["g_spread"])
    all_params = []
    for name, by in CURVES.items():
        rows, params = fit_curves(out, by, spread_col="par_spread")
        out[f"{name}_fit"] = rows["fitted"]
        out[f"{name}_resid"] = rows["loo_resid"]
        out[f"{name}_n"] = rows["n"]
        out[f"{name}_z"] = robust_z(out[f"{name}_resid"], out["date"])
        all_params.append(params.assign(curve=name))
    iz, sz = out["issuer_z"], out["sector_z"]
    out["curve_signal"] = np.select(
        [(iz > Z_FLAG) & (sz > Z_FLAG), (iz < -Z_FLAG) & (sz < -Z_FLAG)], ["cheap", "rich"], default="")
    return out, pd.concat(all_params, ignore_index=True)


def plot_sector_curves(panel: pd.DataFrame, params: pd.DataFrame, date: pd.Timestamp, path: Path) -> Path:
    """Spread vs maturity by sector on one date, with fitted sector curves and flagged bonds."""
    day = panel[panel["date"] == date]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
    for ax, sector in zip(axes.flat, ["Banks", "Telecom", "Pipelines", "Utilities"]):
        d = day[day["sector"] == sector]
        for issuer, g in d.groupby("issuer"):
            ax.scatter(g["years_to_workout"], g["par_spread"], s=18, label=issuer)
        flagged = d[d["curve_signal"] != ""]
        ax.scatter(flagged["years_to_workout"], flagged["par_spread"], s=90, facecolors="none",
                   edgecolors="black", linewidths=1.2, label="flagged (both curves)")
        curves = params[(params["curve"] == "sector") & (params["date"] == date) & (params["sector"] == sector)]
        for p in curves.itertuples():
            xs = np.linspace(d["years_to_workout"].min(), d["years_to_workout"].max(), 50)
            ax.plot(xs, p.a + p.b * np.log(xs), color="black", lw=1, ls="--" if p.seniority == "senior_legacy" else "-")
        ax.set_title(sector)
        ax.legend(fontsize=7, loc="upper left")
        ax.grid(alpha=0.3)
    for ax in axes[1]:
        ax.set_xlabel("Years to workout")
    for ax in axes[:, 0]:
        ax.set_ylabel("Par-equivalent G-spread (bp)")
    fig.suptitle(f"CAD IG spread curves, {date:%Y-%m-%d}")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=pd.Timestamp, default=None, help="month-end to report (default: latest)")
    args = parser.parse_args()

    signals, params = add_curve_signals(load_bonds())
    PROCESSED.mkdir(parents=True, exist_ok=True)
    signals.to_csv(PROCESSED / "curve_signals.csv", index=False)
    params.to_csv(PROCESSED / "curve_params.csv", index=False)

    date = args.date or signals["date"].max()
    fig = plot_sector_curves(signals, params, date, FIGURES / f"sector_curves_{date:%Y-%m}.png")
    day = signals[signals["date"] == date].dropna(subset=["issuer_z", "sector_z"])
    day = day.assign(score=day["issuer_z"] + day["sector_z"], maturity=day["maturity"].dt.strftime("%Y-%m"))
    cols = ["issuer", "coupon", "price", "maturity", "g_spread", "par_spread", "issuer_resid", "issuer_z", "sector_resid", "sector_z"]
    for label, asc in (("Cheapest", False), ("Richest", True)):
        print(f"\n{label} on {date:%Y-%m-%d} (issuer + sector z):")
        print(day.sort_values("score", ascending=asc).head(5)[cols].round(1).to_string(index=False))
    flagged = (signals["curve_signal"] != "").groupby(signals["date"]).sum()
    print(f"\nFlagged on both curves: {int(flagged.loc[date])} bonds on {date:%Y-%m-%d}; "
          f"{flagged.mean():.1f} per month on average. Chart: {fig.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
