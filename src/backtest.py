"""Does a bond's rich/cheap residual predict its future spread change, and how fast does it close?"""

import argparse

import matplotlib
import numpy as np
import pandas as pd
import statsmodels.api as sm

from src.curves import FIGURES, Z_FLAG
from src.load_data import PROCESSED, ROOT, load_bonds
from src.regression import add_regression_signals

matplotlib.use("Agg")  # render to files; no display needed
import matplotlib.pyplot as plt  # noqa: E402  (must follow the backend choice)

HORIZONS = [1, 2, 3]
SIGNALS = {"reg": "reg_resid", "issuer": "issuer_resid", "sector": "sector_resid"}
MIN_AR1_MONTHS = 12


def add_forward_changes(panel: pd.DataFrame, stats: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
    """Add fwd_{h}: excess spread change over the next h month-ends (bp), for each horizon.

    Excess = own change - that month's sector average change - (own roll-down - sector average
    roll-down). Roll-down uses the month's maturity slope from the regression:
    b_log_t × [ln(T_{t+h}) - ln(T_t)]. Positive = widened relative to peers.
    """
    out = panel.copy()
    out["month"] = out["date"].dt.to_period("M")
    slope = stats.set_index("date")["b_log_t"]
    keyed = out.set_index(["isin", "month"])
    for h in horizons:
        future = keyed[["g_spread", "years_to_workout"]].rename(lambda c: f"{c}_f", axis=1)
        future.index = pd.MultiIndex.from_arrays([future.index.get_level_values(0),
                                                  future.index.get_level_values(1) - h])
        j = out.join(future, on=["isin", "month"])
        change = j["g_spread_f"] - j["g_spread"]
        roll = out["date"].map(slope) * (np.log(j["years_to_workout_f"]) - np.log(j["years_to_workout"]))
        net = change - roll
        out[f"fwd_{h}"] = net - net.groupby([out["date"], out["sector"]]).transform("mean")
    return out.drop(columns="month")


def fama_macbeth(df: pd.DataFrame, x: str, y: str, lags: int) -> dict:
    """Monthly cross-sectional slope of y on x; mean slope with a Newey-West t-stat."""
    slopes = []
    for _, g in df[[x, y, "date"]].dropna().groupby("date"):
        if len(g) >= 20:
            slopes.append(np.polyfit(g[x], g[y], 1)[0])
    s = pd.Series(slopes)
    nw = sm.OLS(s, np.ones(len(s))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return {"slope": s.mean(), "t": float(nw.tvalues.iloc[0]), "months": len(s), "share_negative": (s < 0).mean()}


def quintile_table(df: pd.DataFrame, z: str, horizons=HORIZONS) -> pd.DataFrame:
    """Mean forward excess change by z-score quintile (1 = richest, 5 = cheapest), formed monthly."""
    d = df.dropna(subset=[z]).copy()
    d["q"] = d.groupby("date")[z].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) + 1)
    table = d.groupby("q")[[f"fwd_{h}" for h in horizons]].mean()
    table.loc["5-1"] = table.loc[5] - table.loc[1]
    return table


def signal_performance(df: pd.DataFrame, flag_col: str, horizons=HORIZONS) -> pd.DataFrame:
    """For flagged bonds: bp of excess tightening captured (cheap tightens / rich widens) and hit rate."""
    rows = []
    for side, sign in (("cheap", -1), ("rich", 1)):
        d = df[df[flag_col] == side]
        for h in horizons:
            captured = (d[f"fwd_{h}"] * sign).dropna()
            rows.append({"side": side, "h": h, "n": len(captured),
                         "captured_bp": captured.mean(), "hit_rate": (captured > 0).mean()})
    return pd.DataFrame(rows)


def half_lives(df: pd.DataFrame, resid: str) -> tuple[pd.DataFrame, dict]:
    """AR(1) of each bond's residual on consecutive month-ends; plus a pooled estimate."""
    d = df[["isin", "date", resid]].dropna().sort_values(["isin", "date"])
    d["month"] = d["date"].dt.to_period("M")
    prev = d.groupby("isin")[[resid, "month"]].shift()
    consecutive = (d["month"] - prev["month"]).apply(lambda x: x.n if pd.notna(x) else np.nan) == 1
    pairs = pd.DataFrame({"isin": d["isin"], "x": prev[resid], "y": d[resid]})[consecutive]

    def hl(rho):
        return -np.log(2) / np.log(rho) if 0 < rho < 1 else np.nan

    per_bond = []
    for isin, g in pairs.groupby("isin"):
        if len(g) >= MIN_AR1_MONTHS:
            rho = np.polyfit(g["x"], g["y"], 1)[0]
            # Kendall small-sample correction: OLS ρ is biased down by about (1 + 3ρ) / T.
            rho_c = min(rho + (1 + 3 * rho) / len(g), 0.999)
            per_bond.append({"isin": isin, "months": len(g), "rho": rho, "half_life": hl(rho),
                             "rho_corrected": rho_c, "half_life_corrected": hl(rho_c)})
    per_bond = pd.DataFrame(per_bond)
    # Pooled: demean by bond so persistent bond-level offsets don't masquerade as persistence.
    px = pairs["x"] - pairs.groupby("isin")["x"].transform("mean")
    py = pairs["y"] - pairs.groupby("isin")["y"].transform("mean")
    rho_within = float((px * py).sum() / (px**2).sum())
    rho_raw = float(np.polyfit(pairs["x"], pairs["y"], 1)[0])
    pooled = {"rho_raw": rho_raw, "half_life_raw": -np.log(2) / np.log(rho_raw),
              "rho_within": rho_within, "half_life_within": -np.log(2) / np.log(rho_within), "pairs": len(pairs)}
    return per_bond, pooled


def horizon_half_life(df: pd.DataFrame, resid: str, h: int = 3) -> dict:
    """Half-life implied by how much of today's residual is left after h months.

    Pooled slope of resid_{t+h} on resid_t gives ρ^h directly, without the short-sample bias of
    per-bond AR(1) fits. Also splits the residual change into spread moves and fair-value moves.
    """
    d = df[["isin", "date", resid, "reg_fit", "g_spread"]].dropna().copy()
    d["month"] = d["date"].dt.to_period("M")
    fut = d.set_index(["isin", "month"])[[resid, "reg_fit"]]
    fut.index = pd.MultiIndex.from_arrays([fut.index.get_level_values(0), fut.index.get_level_values(1) - h])
    j = d.join(fut.rename(lambda c: f"{c}_f", axis=1), on=["isin", "month"]).dropna(subset=[f"{resid}_f"])
    rho_h = np.polyfit(j[resid], j[f"{resid}_f"], 1)[0]
    rho = rho_h ** (1 / h) if rho_h > 0 else np.nan
    d_fit = j["reg_fit_f"] - j["reg_fit"]
    return {"h": h, "rho_h": rho_h, "rho_month": rho, "half_life": -np.log(2) / np.log(rho),
            "from_spread": np.polyfit(j[resid], j[f"{resid}_f"] - j[resid] + d_fit, 1)[0],
            "from_fair_value": np.polyfit(j[resid], -d_fit, 1)[0]}


def plot_quintiles(tables: dict, path):
    fig, axes = plt.subplots(1, len(tables), figsize=(5 * len(tables), 4), sharey=True)
    for ax, (name, t) in zip(np.atleast_1d(axes), tables.items()):
        q = t.drop(index="5-1")
        for col in q.columns:
            ax.plot(q.index, q[col], marker="o", label=f"{col.split('_')[1]}m")
        ax.axhline(0, color="grey", lw=0.8)
        ax.set_title(f"{name} residual quintiles")
        ax.set_xlabel("Quintile (1 = richest, 5 = cheapest)")
        ax.grid(alpha=0.3)
    np.atleast_1d(axes)[0].set_ylabel("Forward excess spread change (bp)")
    np.atleast_1d(axes)[0].legend()
    fig.suptitle("Cheap bonds should tighten (negative), rich bonds widen (positive)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    signals, _, stats = add_regression_signals(load_bonds())
    df = add_forward_changes(signals, stats)
    df.to_csv(PROCESSED / "backtest_panel.csv", index=False)

    print(f"Backtest: {df['date'].nunique()} month-ends, {df['isin'].nunique()} bonds; "
          f"forward excess change = own − sector average, net of roll-down.\n")

    print("Fama-MacBeth: forward excess change (bp) on today's residual (bp).")
    print("  slope −1 = gap fully closes within h months; 0 = no closing.")
    fm = pd.DataFrame([{"signal": name, "h": h, **fama_macbeth(df, col, f"fwd_{h}", lags=h)}
                       for name, col in SIGNALS.items() for h in HORIZONS])
    print(fm.round(3).to_string(index=False))

    tables = {name: quintile_table(df, col.replace("_resid", "_z")) for name, col in SIGNALS.items()}
    print("\nQuintile sorts on regression z (mean forward excess change, bp):")
    print(tables["reg"].round(2).to_string())

    perf = signal_performance(df, "signal")
    print("\nCombined signal (regression + both curves): bp captured per bond, hit rate")
    print(perf.round(2).to_string(index=False))

    per_bond, pooled = half_lives(df, "reg_resid")
    print(f"\nAR(1) on regression residuals: {len(per_bond)} bonds with ≥{MIN_AR1_MONTHS} consecutive months")
    print(f"  per-bond ρ median {per_bond['rho'].median():.2f}; half-life median "
          f"{per_bond['half_life'].median():.1f} months (IQR {per_bond['half_life'].quantile(.25):.1f}–"
          f"{per_bond['half_life'].quantile(.75):.1f})")
    print(f"  bias-corrected per-bond half-life median {per_bond['half_life_corrected'].median():.1f} months "
          f"(raw per-bond fits are biased toward fast reversion with ~{per_bond['months'].median():.0f} months each)")
    print(f"  pooled ρ {pooled['rho_raw']:.2f} (half-life {pooled['half_life_raw']:.1f}m); "
          f"within-bond ρ {pooled['rho_within']:.2f} (half-life {pooled['half_life_within']:.1f}m, also short-sample biased)")
    hh = horizon_half_life(df, "reg_resid", 3)
    print(f"  3-month horizon: {hh['rho_h']:.2f} of the residual remains → monthly ρ {hh['rho_month']:.2f}, "
          f"half-life {hh['half_life']:.1f} months (preferred estimate)")
    print(f"  residual change over 3m: {hh['from_spread']:.3f} from spread moves, "
          f"{hh['from_fair_value']:.3f} from fair-value moves (per bp of residual)")
    years = {y: round(float(quintile_table(g, "reg_z").loc["5-1", "fwd_3"]), 2) for y, g in df.groupby(df["date"].dt.year)}
    print(f"  cheapest-minus-richest quintile, 3m excess change by year (bp): {years}")

    fm.to_csv(PROCESSED / "backtest_fama_macbeth.csv", index=False)
    per_bond.to_csv(PROCESSED / "backtest_half_lives.csv", index=False)
    fig = plot_quintiles(tables, FIGURES / "backtest_quintiles.png")
    print(f"\nChart: {fig.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
