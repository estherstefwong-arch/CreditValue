"""Turn the strongest rich/cheap signal into a CS01-neutral long/short pair with carry, target and stop."""

import argparse
from dataclasses import dataclass

import matplotlib
import numpy as np
import pandas as pd

from bloomberg_adapter.bond_math import accrued_interest, modified_duration
from bloomberg_adapter.manual_csv import load_ratings
from src.curves import FIGURES, add_curve_signals
from src.load_data import PROCESSED, ROOT, load_bonds, load_fundamentals, load_goc_yields
from src.regression import add_regression_signals

matplotlib.use("Agg")  # render to files; no display needed
import matplotlib.pyplot as plt  # noqa: E402  (must follow the backend choice)

LONG_NOTIONAL = 10_000_000  # C$ face of the cheap (long) leg
MONTHLY_RHO = 0.94  # residual persistence from src/backtest.py (3-month horizon estimate)
HORIZON_MONTHS = 12  # ~one half-life (src/backtest.py)
BID_ASK_BP = 2.0  # assumed round-trip cost per leg, in spread bp
MIN_Z = 0.75  # each leg's regression |z| at entry
PERSIST_MONTHS = 3  # each leg's residual must have had the same sign this long

# Comparability: the pair should differ in credit pricing, not in structure.
MAX_WORKOUT_GAP_YEARS = 1.5
MAX_PRICE_GAP = 8.0
MAX_VINTAGE_GAP_YEARS = 5.0


@dataclass
class Leg:
    isin: str
    issuer: str
    coupon: float
    maturity: pd.Timestamp
    workout: pd.Timestamp
    price: float
    yield_pct: float
    g_spread: float
    resid: float
    z: float
    duration: float
    dirty: float

    @property
    def name(self) -> str:
        return f"{self.issuer} {self.coupon:.2f}% {self.maturity:%b-%Y}"

    def cs01_per_face(self) -> float:
        """C$ change in value per C$1 face for a 1bp spread move."""
        return self.duration * self.dirty / 100 * 1e-4


def persistent(df: pd.DataFrame, date: pd.Timestamp, months: int = PERSIST_MONTHS) -> pd.Series:
    """True for bonds whose regression residual kept the same sign over the last `months` month-ends."""
    recent = df[df["date"] <= date].sort_values("date").groupby("isin").tail(months)
    return recent.groupby("isin")["reg_resid"].agg(lambda s: len(s) == months and np.sign(s).nunique() == 1)


def candidate_pairs(df: pd.DataFrame, date: pd.Timestamp, min_z: float = MIN_Z, max_price_gap: float = MAX_PRICE_GAP,
                    max_vintage_gap: float = MAX_VINTAGE_GAP_YEARS, exclude_sectors: tuple = ()) -> pd.DataFrame:
    """All comparable (cheap long, rich short) pairs in the same sector on `date`, best first."""
    day = df[(df["date"] == date) & df["reg_z"].notna()].copy()
    ok = persistent(df, date)
    day = day[day["isin"].map(ok).fillna(False).astype(bool) & ~day["sector"].isin(exclude_sectors)]
    day["callable"] = day["workout_date"] < day["maturity"]
    day["vintage"] = day["issue_date"].dt.year + day["issue_date"].dt.dayofyear / 365
    cheap = day[day["reg_z"] >= min_z]
    rich = day[day["reg_z"] <= -min_z]
    pairs = cheap.merge(rich, on=["sector", "callable"], suffixes=("_l", "_s"))
    pairs = pairs[
        ((pairs["years_to_workout_l"] - pairs["years_to_workout_s"]).abs() <= MAX_WORKOUT_GAP_YEARS)
        & ((pairs["price_l"] - pairs["price_s"]).abs() <= max_price_gap)
        & ((pairs["vintage_l"] - pairs["vintage_s"]).abs() <= max_vintage_gap)
    ].copy()
    pairs["gap_bp"] = pairs["reg_resid_l"] - pairs["reg_resid_s"]
    pairs["curve_agree"] = (pairs["issuer_z_l"] > 0) & (pairs["sector_z_l"] > 0) & \
        (pairs["issuer_z_s"] < 0) & (pairs["sector_z_s"] < 0)
    pairs["spread_pickup_bp"] = pairs["g_spread_l"] - pairs["g_spread_s"]
    return pairs.sort_values(["curve_agree", "gap_bp"], ascending=False).reset_index(drop=True)


def make_leg(r: pd.Series, suffix: str) -> Leg:
    def get(col):
        return r[f"{col}{suffix}"]
    settle, workout = get("date").date(), get("workout_date").date()
    return Leg(isin=get("isin"), issuer=get("issuer"), coupon=get("coupon"), maturity=get("maturity"),
               workout=get("workout_date"), price=get("price"), yield_pct=get("yield"), g_spread=get("g_spread"),
               resid=get("reg_resid"), z=get("reg_z"),
               duration=modified_duration(get("yield"), get("coupon"), settle, workout),
               dirty=get("price") + accrued_interest(get("coupon"), settle, workout))


def rolldown_bp(params: pd.DataFrame, date, issuer, seniority, years: float, months: int) -> float:
    """Expected spread change from rolling down the issuer curve over `months` (negative = tightens)."""
    p = params[(params["curve"] == "issuer") & (params["date"] == date) & (params["issuer"] == issuer)
               & (params["seniority"] == seniority)]
    if p.empty:
        return 0.0
    b = p["b"].iloc[0]
    return b * (np.log(max(years - months / 12, 0.25)) - np.log(years))


def pair_history(df: pd.DataFrame, long: Leg, short: Leg) -> pd.DataFrame:
    a = df[df["isin"] == long.isin].set_index("date")[["g_spread", "reg_resid"]]
    b = df[df["isin"] == short.isin].set_index("date")[["g_spread", "reg_resid"]]
    h = a.join(b, lsuffix="_l", rsuffix="_s", how="inner")
    h["spread_diff"] = h["g_spread_l"] - h["g_spread_s"]
    h["resid_diff"] = h["reg_resid_l"] - h["reg_resid_s"]
    return h


def rating_changes(issuer: str, since: pd.Timestamp) -> list[str]:
    """Agency rating changes for an issuer on or after `since`, e.g. '2024-08-30 moodys Baa1 → Baa2'."""
    all_ratings = load_ratings()
    mine = all_ratings[all_ratings["issuer"] == issuer].sort_values("effective_date")
    r, prev = mine[mine["effective_date"] >= since], mine[mine["effective_date"] < since].tail(1)
    out, last = [], prev[["sp", "moodys", "dbrs"]].iloc[0].to_dict() if len(prev) else {}
    for row in r.itertuples():
        for agency in ("sp", "moodys", "dbrs"):
            new = getattr(row, agency)
            if last.get(agency) and new and new != last[agency]:
                out.append(f"{row.effective_date:%Y-%m-%d} {agency} {last[agency]} → {new}")
            last[agency] = new
    return out


def leverage_trend(parent: str, date: pd.Timestamp, years: int = 2) -> str:
    """'5.53x (FY2023) → 4.43x (FY2025)' from filings available by `date`; '' for banks / no data."""
    f = load_fundamentals()
    f = f[(f["parent"] == parent) & (f["available_date"] <= date)].sort_values("period_end")
    f = f[f["period_end"] >= date - pd.DateOffset(years=years + 1)]
    if len(f) < 2:
        return ""
    first, last = f.iloc[0], f.iloc[-1]
    lev = lambda r: r["net_debt"] / r["ebitda"]  # noqa: E731
    return f"{lev(first):.2f}x ({first['period_end']:%b-%Y}) → {lev(last):.2f}x ({last['period_end']:%b-%Y})"


def evaluate(pair: pd.Series, df: pd.DataFrame, params: pd.DataFrame, funding_pct: float,
             horizon: int = HORIZON_MONTHS) -> dict:
    """Sizing, carry, roll-down, expected convergence, breakeven, target and stop for one pair."""
    long, short = make_leg(pair, "_l"), make_leg(pair, "_s")
    date = pair["date_l"]
    cs01_l = long.cs01_per_face() * LONG_NOTIONAL
    short_face = cs01_l / short.cs01_per_face()
    mv_l, mv_s = long.dirty / 100 * LONG_NOTIONAL, short.dirty / 100 * short_face

    # Yield carry per year: the long earns its yield and is funded; the short pays its yield and
    # earns funding on the proceeds. Repo is approximated by the 2y GoC yield.
    carry_yr = (long.yield_pct - funding_pct) / 100 * mv_l - (short.yield_pct - funding_pct) / 100 * mv_s
    carry_bp = carry_yr / cs01_l  # in bp of relative spread per year

    yrs_l = (long.workout - date).days / 365.25
    yrs_s = (short.workout - date).days / 365.25
    roll_l = rolldown_bp(params, date, long.issuer, pair["seniority_l"], yrs_l, horizon)
    roll_s = rolldown_bp(params, date, short.issuer, pair["seniority_s"], yrs_s, horizon)
    roll_bp = -(roll_l - roll_s)  # long gains if its spread falls more than the short's

    gap = long.resid - short.resid
    convergence_bp = gap * (1 - MONTHLY_RHO**horizon)
    cost_bp = 2 * BID_ASK_BP  # both legs, round trip
    expected_bp = convergence_bp + carry_bp * horizon / 12 + roll_bp - cost_bp

    hist = pair_history(df, long, short)
    vol = hist["spread_diff"].diff().std()
    stop_bp = max(10.0, 2 * vol * np.sqrt(horizon))
    entry_diff = long.g_spread - short.g_spread
    return {
        "long": long, "short": short, "short_face": short_face, "cs01": cs01_l, "mv_long": mv_l, "mv_short": mv_s,
        "gap_bp": gap, "convergence_bp": convergence_bp, "carry_bp_yr": carry_bp, "carry_cad_yr": carry_yr,
        "roll_bp": roll_bp, "cost_bp": cost_bp, "expected_bp": expected_bp, "expected_cad": expected_bp * cs01_l,
        "entry_diff": entry_diff, "target_diff": entry_diff - convergence_bp, "stop_diff": entry_diff + stop_bp,
        "stop_bp": stop_bp, "breakeven_bp": carry_bp * horizon / 12 + roll_bp - cost_bp,
        "monthly_vol_bp": vol, "history": hist, "horizon": horizon, "date": date,
        "full_close_months": np.log(0.5) / np.log(MONTHLY_RHO),
        "curve_agree": bool(pair["curve_agree"]),
        "gap_12m_mean": hist["resid_diff"].tail(12).mean(),
        "gap_months_above_half": int((hist["resid_diff"].tail(12) > gap / 2).sum()),
        "leverage": {"long": leverage_trend(pair["parent_l"], date), "short": leverage_trend(pair["parent_s"], date)},
    }


def plot_pair(t: dict, path):
    h = t["history"]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    ax1.plot(h.index, h["g_spread_l"], label=f"Long: {t['long'].name}")
    ax1.plot(h.index, h["g_spread_s"], label=f"Short: {t['short'].name}")
    ax1.set_ylabel("G-spread (bp)")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    ax2.plot(h.index, h["resid_diff"], color="black", label="Residual gap (long − short)")
    ax2.plot(h.index, h["spread_diff"], color="grey", ls="--", label="Spread difference (long − short)")
    ax2.axhline(0, color="grey", lw=0.8)
    ax2.set_ylabel("bp")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.suptitle("Pitched pair: spreads and model residual gap")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def pitch_markdown(t: dict, chart_rel: str, rating_notes: dict) -> str:
    L, S = t["long"], t["short"]
    lines = [
        f"# Trade pitch: long {L.name} / short {S.name}",
        "",
        f"As of {t['date']:%B %d, %Y}. CS01-neutral relative value; draft numbers from `src/trade.py`.",
        "",
        "## Trade",
        "",
        "| | Long (cheap) | Short (rich) |",
        "| --- | --- | --- |",
        f"| Bond | {L.name} | {S.name} |",
        f"| ISIN | {L.isin} | {S.isin} |",
        f"| Price / yield | {L.price:.2f} / {L.yield_pct:.2f}% | {S.price:.2f} / {S.yield_pct:.2f}% |",
        f"| G-spread | {L.g_spread:.0f}bp | {S.g_spread:.0f}bp |",
        f"| Model residual (z) | {L.resid:+.0f}bp ({L.z:+.1f}) | {S.resid:+.0f}bp ({S.z:+.1f}) |",
        f"| Workout / duration | {L.workout:%b-%Y} / {L.duration:.2f} | {S.workout:%b-%Y} / {S.duration:.2f} |",
        f"| Face | C${LONG_NOTIONAL / 1e6:.1f}mm | C${t['short_face'] / 1e6:.2f}mm |",
        "",
        f"CS01 per leg: **C${t['cs01']:,.0f}** per bp. Market value long C${t['mv_long'] / 1e6:.2f}mm, "
        f"short C${t['mv_short'] / 1e6:.2f}mm.",
        "",
        f"## Economics over {t['horizon']} months (bp of relative spread; × CS01 for C$)",
        "",
        "| Component | bp | C$ |",
        "| --- | --- | --- |",
        f"| Expected convergence ({t['gap_bp']:.0f}bp gap × (1 − {MONTHLY_RHO}^{t['horizon']})) | "
        f"{t['convergence_bp']:+.1f} | {t['convergence_bp'] * t['cs01']:+,.0f} |",
        f"| Carry | {t['carry_bp_yr'] * t['horizon'] / 12:+.1f} | {t['carry_bp_yr'] * t['horizon'] / 12 * t['cs01']:+,.0f} |",
        f"| Roll-down | {t['roll_bp']:+.1f} | {t['roll_bp'] * t['cs01']:+,.0f} |",
        f"| Bid/ask (assumed {BID_ASK_BP:.0f}bp per leg) | {-t['cost_bp']:+.1f} | {-t['cost_bp'] * t['cs01']:+,.0f} |",
        f"| **Expected** | **{t['expected_bp']:+.1f}** | **{t['expected_cad']:+,.0f}** |",
        "",
        f"- Entry spread difference (long − short): **{t['entry_diff']:.0f}bp**",
        f"- Target: **{t['target_diff']:.0f}bp** (expected convergence by {t['horizon']} months; "
        f"half the gap closes in ~{t['full_close_months']:.0f} months)",
        f"- Stop: **{t['stop_diff']:.0f}bp** (+{t['stop_bp']:.0f}bp; 2× the pair's monthly volatility of "
        f"{t['monthly_vol_bp']:.1f}bp scaled to {t['horizon']} months, minimum 10bp)",
        (f"- Breakeven: carry + roll-down − costs = {t['breakeven_bp']:+.1f}bp over {t['horizon']} months, "
         f"so the gap can widen {t['breakeven_bp']:.1f}bp before the trade loses money"
         if t["breakeven_bp"] >= 0 else
         f"- Breakeven: carry + roll-down − costs = {t['breakeven_bp']:+.1f}bp over {t['horizon']} months, "
         f"so the trade needs {-t['breakeven_bp']:.1f}bp of convergence just to break even"),
        f"- Risk/reward: expected {t['expected_bp']:+.1f}bp vs {t['stop_bp']:.0f}bp to the stop",
        "",
        "## Why the gap should close",
        "",
        "- Both legs are flagged by the fundamental regression"
        + (", and sit cheap/rich on both their issuer and sector curves." if t["curve_agree"]
           else "; the curve fits only partly agree, so lean on the regression view."),
        "- The legs are comparable in sector, workout, price, coupon vintage and call structure, so the gap is "
        "not a coupon, liquidity or structure effect the model already controls for.",
        f"- Backtest: residuals close with a ~{t['full_close_months']:.0f}-month half-life; the cheapest-minus-richest "
        "quintile beat by ~3bp per 3 months, in every year 2023–2026.",
        "",
        "## Risks",
        "",
        f"- Rating actions since last year: long — {', '.join(rating_notes['long']) or 'none on file'}; "
        f"short — {', '.join(rating_notes['short']) or 'none on file'}.",
        f"- Leverage (net debt / EBITDA, as filed): long — {t['leverage']['long'] or 'n/a'}; "
        f"short — {t['leverage']['short'] or 'n/a'}. The regression's leverage term is not significant, so a "
        "real credit improvement on one side can look like a mispricing.",
        f"- Persistence: the residual gap averaged {t['gap_12m_mean']:.0f}bp over the last 12 months and was above "
        f"half today's level in {t['gap_months_above_half']} of them. A gap that hasn't closed in a year may be "
        "pricing something real.",
        "- The gap may exist for a reason the model can't see: upcoming issuance by the long's issuer, "
        "M&A or regulatory news, index events. Check news and the new-issue calendar before entry.",
        "- Prices are evaluated (vendor model), not traded. Confirm both legs on CIRO trade data.",
        "- Slow convergence: most of the expected P&L arrives over months; carry decides whether waiting pays.",
        "- Short-leg availability and borrow cost in CAD corporates are not modelled.",
        "- Bank bonds only: the data can't distinguish covered, deposit-note and bail-in programs. Check each "
        "leg's pricing supplement before trading a bank pair.",
        "",
        f"![Pair history]({chart_rel})",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=pd.Timestamp, default=None)
    parser.add_argument("--pick", type=int, default=0, help="candidate row to pitch (default: best)")
    parser.add_argument("--min-z", type=float, default=MIN_Z)
    parser.add_argument("--max-price-gap", type=float, default=MAX_PRICE_GAP)
    parser.add_argument("--max-vintage-gap", type=float, default=MAX_VINTAGE_GAP_YEARS)
    parser.add_argument("--exclude-sector", action="append", default=[], help="e.g. Banks; repeatable")
    parser.add_argument("--horizon", type=int, default=HORIZON_MONTHS, help="holding period in months")
    args = parser.parse_args()

    bonds = load_bonds()
    df, _, _ = add_regression_signals(bonds)
    _, params = add_curve_signals(bonds)
    date = args.date or df["date"].max()
    goc = load_goc_yields()
    funding = float(goc.loc[goc.index.to_period("M") == date.to_period("M"), 2.0].iloc[0])

    pairs = candidate_pairs(df, date, args.min_z, args.max_price_gap, args.max_vintage_gap, tuple(args.exclude_sector))
    if pairs.empty:
        raise SystemExit("No comparable pairs pass the filters on this date.")
    evaluated = [evaluate(r, df, params, funding, args.horizon) for _, r in pairs.head(15).iterrows()]
    table = pd.DataFrame([{
        "long": e["long"].name, "short": e["short"].name, "gap_bp": e["gap_bp"],
        "curves_agree": bool(pairs.loc[i, "curve_agree"]), "carry_bp_yr": e["carry_bp_yr"], "roll_bp": e["roll_bp"],
        "expected_bp": e["expected_bp"], "stop_bp": e["stop_bp"],
    } for i, e in enumerate(evaluated)])
    table.to_csv(PROCESSED / "trade_candidates.csv", index=False)
    print(f"{len(pairs)} comparable pairs on {date:%Y-%m-%d} (same sector & call structure, workout within "
          f"{MAX_WORKOUT_GAP_YEARS}y, price within {args.max_price_gap}, vintage within {args.max_vintage_gap}y, "
          f"|z| ≥ {args.min_z}, residual sign stable {PERSIST_MONTHS}m"
          + (f", excluding {', '.join(args.exclude_sector)}" if args.exclude_sector else "") + "). Top candidates "
          f"({args.horizon}m horizon, funding {funding:.2f}%):")
    print(table.round(1).to_string())

    t = evaluated[args.pick]
    since = date - pd.DateOffset(years=1)
    notes = {"long": rating_changes(t["long"].issuer, since), "short": rating_changes(t["short"].issuer, since)}
    chart = plot_pair(t, FIGURES / "pair_history.png")
    pitch = ROOT / "reports" / "trade_pitch.md"
    pitch.write_text(pitch_markdown(t, str(chart.relative_to(pitch.parent)), notes))
    print(f"\nPitched #{args.pick}: long {t['long'].name} / short {t['short'].name}")
    print(f"  short face C${t['short_face'] / 1e6:.2f}mm vs long C${LONG_NOTIONAL / 1e6:.0f}mm; CS01 C${t['cs01']:,.0f}/bp")
    print(f"  {args.horizon}m expected {t['expected_bp']:+.1f}bp (C${t['expected_cad']:+,.0f}): convergence "
          f"{t['convergence_bp']:+.1f}, carry {t['carry_bp_yr'] * args.horizon / 12:+.1f}, roll {t['roll_bp']:+.1f}, "
          f"cost {-t['cost_bp']:+.1f}")
    print(f"  entry {t['entry_diff']:.0f}bp → target {t['target_diff']:.0f}bp, stop {t['stop_diff']:.0f}bp; "
          f"leverage long {t['leverage']['long'] or 'n/a'}, short {t['leverage']['short'] or 'n/a'}; "
          f"gap 12m avg {t['gap_12m_mean']:.0f}bp")
    print(f"Wrote {pitch.relative_to(ROOT)} and {chart.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
