"""Fundamental spread regression: residuals say how far each bond trades from its fundamentals."""

import argparse

import matplotlib
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from statsmodels.stats.outliers_influence import variance_inflation_factor

from src.curves import FIGURES, Z_FLAG, add_curve_signals, robust_z
from src.load_data import PROCESSED, ROOT, load_bonds

matplotlib.use("Agg")  # render to files; no display needed
import matplotlib.pyplot as plt  # noqa: E402  (must follow the backend choice)

# PRD spec plus the controls the curve step showed are needed. Banks have no leverage or
# coverage: those enter as zero for banks and the sector dummy absorbs the bank level.
TERMS = ["log_t", "rating", "leverage_nb", "coverage_nb", "log_size", "premium", "log_age", "callable"]
SECTOR = "C(sector, Treatment('Utilities'))"


def formula(terms: list[str], bail_in: bool = True) -> str:
    return "g_spread ~ " + " + ".join(terms) + f" + {SECTOR}" + (" + bail_in" if bail_in else "")


FORMULA = formula(TERMS)
MIN_ROWS = 40


def prepare(bonds: pd.DataFrame) -> pd.DataFrame:
    """Regression variables; rows missing a required input are dropped (reported by caller)."""
    df = bonds.copy()
    non_bank = df["sector"] != "Banks"
    df["log_t"] = np.log(df["years_to_workout"])
    df["leverage_nb"] = df["leverage"].where(non_bank, 0.0)
    df["coverage_nb"] = df["coverage"].where(non_bank, 0.0)
    # Amount outstanding isn't filled yet; ETF par held scales with it (XCB is market-value weighted).
    df["log_size"] = np.log(df["amount_outstanding"].fillna(df["etf_par_held"]))
    df["premium"] = df["price"] - 100
    df["log_age"] = np.log1p(((df["date"] - df["issue_date"]).dt.days / 365.25).clip(lower=0))
    df["callable"] = (df["workout_date"] < df["maturity"]).astype(float)
    df["bail_in"] = (df["seniority"] == "senior_bail_in").astype(float)
    return df.dropna(subset=["g_spread", *TERMS])


def _loo_resid(fit) -> np.ndarray:
    """Leave-one-out residuals: e / (1 - h)."""
    h = fit.get_influence().hat_matrix_diag
    return fit.resid.to_numpy() / np.clip(1 - h, 1e-9, None)


def fit_monthly(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cross-sectional OLS each month (point-in-time). Returns per-row fits and per-month stats."""
    rows, stats = [], []
    for date, g in df.groupby("date"):
        if len(g) < MIN_ROWS:
            continue
        # Drop terms with no variation this month (e.g. no callable bonds before 2024); bail_in
        # duplicates the bank dummy unless the month still has legacy bank bonds.
        terms = [t for t in TERMS if g[t].nunique() > 1]
        has_legacy = (g["seniority"] == "senior_legacy").any()
        fit = smf.ols(formula(terms, bail_in=has_legacy), data=g).fit()
        rows.append(pd.DataFrame({"reg_fit": fit.fittedvalues, "reg_resid": _loo_resid(fit)}, index=g.index))
        stats.append({"date": date, "n": int(fit.nobs), "r2": fit.rsquared, "adj_r2": fit.rsquared_adj,
                      "rmse": np.sqrt(fit.mse_resid), **{f"b_{t}": fit.params.get(t, np.nan) for t in TERMS}})
    return pd.concat(rows), pd.DataFrame(stats)


def fit_pooled(df: pd.DataFrame):
    """Pooled panel with month fixed effects; standard errors clustered by issuer."""
    groups = df["issuer"].astype("category").cat.codes
    return smf.ols(FORMULA + " + C(date)", data=df).fit(cov_type="cluster", cov_kwds={"groups": groups})


def vif_table(df: pd.DataFrame) -> pd.Series:
    X = df[TERMS].assign(const=1.0)
    return pd.Series({t: variance_inflation_factor(X.values, i) for i, t in enumerate(TERMS)}).round(1)


def add_regression_signals(bonds: pd.DataFrame):
    """Curve signals plus reg_fit / reg_resid / reg_z, and the combined `signal`.

    signal is "cheap"/"rich" only when the regression z and both curve z-scores agree beyond
    ±Z_FLAG (the PRD's "prefer bonds flagged by both").
    """
    curves, _ = add_curve_signals(bonds)
    df = prepare(curves)
    fits, stats = fit_monthly(df)
    out = curves.join(fits)
    out["reg_z"] = robust_z(out["reg_resid"], out["date"])
    cheap = (out["reg_z"] > Z_FLAG) & (out["curve_signal"] == "cheap")
    rich = (out["reg_z"] < -Z_FLAG) & (out["curve_signal"] == "rich")
    out["signal"] = np.select([cheap, rich], ["cheap", "rich"], default="")
    return out, df, stats


def plot_actual_vs_model(out: pd.DataFrame, date: pd.Timestamp, path):
    """Actual vs model-predicted spread on one date, with flagged bonds labelled."""
    d = out[(out["date"] == date) & out["reg_fit"].notna()]
    fig, ax = plt.subplots(figsize=(9, 8))
    for sector, g in d.groupby("sector"):
        ax.scatter(g["reg_fit"], g["g_spread"], s=20, label=sector)
    lim = [min(d["reg_fit"].min(), d["g_spread"].min()) - 5, max(d["reg_fit"].max(), d["g_spread"].max()) + 5]
    ax.plot(lim, lim, color="grey", lw=1)
    flagged = d[d["reg_z"].abs() > Z_FLAG]
    ax.scatter(flagged["reg_fit"], flagged["g_spread"], s=70, facecolors="none", edgecolors="black")
    for r in d[d["signal"] != ""].itertuples():
        ax.annotate(f"{r.issuer.split()[0]} {r.coupon:.2f}% {r.maturity:%Y}", (r.reg_fit, r.g_spread),
                    fontsize=7, xytext=(4, -10 if r.signal == "rich" else 4), textcoords="offset points")
    ax.set_xlabel("Model-predicted G-spread (bp)")
    ax.set_ylabel("Actual G-spread (bp)")
    ax.set_title(f"Actual vs fundamental model, {date:%Y-%m-%d}\n(circled: |z| > 1.5; labelled: also flagged by both curves)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=pd.Timestamp, default=None, help="month-end to report (default: latest)")
    args = parser.parse_args()

    bonds = load_bonds()
    out, df, stats = add_regression_signals(bonds)
    pooled = fit_pooled(df)
    out.to_csv(PROCESSED / "signals.csv", index=False)
    stats.to_csv(PROCESSED / "regression_monthly.csv", index=False)
    (PROCESSED / "regression_pooled.txt").write_text(pooled.summary().as_text())

    excluded = bonds.loc[~bonds.index.isin(df.index), "issuer"].value_counts()
    print(f"Regression sample: {len(df)} of {len(bonds)} bond-months, {df['issuer'].nunique()} issuers. "
          f"Excluded (missing rating/fundamentals): {', '.join(f'{k} ({v})' for k, v in excluded.items())}")
    print(f"\nMonthly cross-section R²: median {stats['r2'].median():.2f} "
          f"(range {stats['r2'].min():.2f}–{stats['r2'].max():.2f}); PRD target 0.50")

    keep = [t for t in TERMS] + [p for p in pooled.params.index if p.startswith("C(sector") or p == "bail_in"]
    table = pd.DataFrame({"coef": pooled.params[keep], "se (issuer-clustered)": pooled.bse[keep],
                          "p": pooled.pvalues[keep]})
    monthly = stats[[f"b_{t}" for t in TERMS]]
    table.loc[TERMS, "monthly median"] = monthly.median().to_numpy()
    same = np.sign(monthly).eq(np.sign(monthly.median())).where(monthly.notna())
    table.loc[TERMS, "monthly same sign %"] = (same.mean() * 100).to_numpy()
    print(f"\nPooled panel (month FE), R² {pooled.rsquared:.2f}, n={int(pooled.nobs)}:")
    print(table.round(3).to_string())
    print("\nVIF:", vif_table(df).to_dict())

    date = args.date or out["date"].max()
    fig = plot_actual_vs_model(out, date, FIGURES / f"actual_vs_model_{date:%Y-%m}.png")
    day = out[(out["date"] == date) & (out["signal"] != "")]
    cols = ["signal", "issuer", "coupon", "maturity", "g_spread", "reg_fit", "reg_z", "issuer_z", "sector_z"]
    print(f"\nFlagged by regression AND both curves on {date:%Y-%m-%d}:")
    print(day.sort_values("reg_z")[cols].assign(maturity=day["maturity"].dt.strftime("%Y-%m")).round(1).to_string(index=False)
          if len(day) else "  none")
    per_month = (out["signal"] != "").groupby(out["date"]).sum()
    print(f"Combined signals: {per_month.mean():.1f} per month on average. Chart: {fig.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
