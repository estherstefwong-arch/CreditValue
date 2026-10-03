"""Build the final model panel: bonds + G-spreads + fundamentals, validated and frozen."""

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bloomberg_adapter.boc_valet import RAW_DIR as BOC_DIR
from bloomberg_adapter.boc_valet import fetch_benchmark_yields
from bloomberg_adapter.edgar_xbrl import load_all as load_edgar
from bloomberg_adapter.etf_holdings import PROCESSED_DIR, XCBHoldingsAdapter
from bloomberg_adapter.fundamentals import attach, combine, load_manual
from bloomberg_adapter.goc_curve import align_to_curve_dates, g_spread_bp, interpolate_goc_yield, years_between
from bloomberg_adapter.validate import render_report, run_checks

ROOT = PROCESSED_DIR.parent.parent
MIN_AMOUNT_OUTSTANDING = 300e6  # PRD filter, C$
MAX_RATING_NUMERIC = 10  # BBB- on the AAA = 1 scale


def load_goc_curve(start: date, end: date, refresh: bool) -> pd.DataFrame:
    path = BOC_DIR / "goc_benchmark_daily.csv"
    if refresh or not path.exists():
        BOC_DIR.mkdir(parents=True, exist_ok=True)
        fetch_benchmark_yields(start - pd.Timedelta(days=10), end).to_csv(path, index=False)
    return pd.read_csv(path, parse_dates=["date"])


def add_spreads(panel: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    """G-spread to the workout date (call date for bonds priced to call)."""
    out = panel.copy()
    out["curve_date"] = align_to_curve_dates(out["date"], curve)
    out["years_to_workout"] = years_between(out["date"], out["workout_date"])
    out["goc_yield"] = interpolate_goc_yield(curve, out["curve_date"], out["years_to_workout"])
    out["g_spread_bp"] = g_spread_bp(out["yield"], out["goc_yield"])
    return out


def add_model_fields(panel: pd.DataFrame) -> pd.DataFrame:
    """Flags and controls the regression needs. Unknown filters stay NaN rather than False."""
    out = panel.copy()
    out["is_bank"] = out["sector"] == "Banks"
    out["discount_pts"] = (100 - out["price"]).clip(lower=0)  # tax-driven richness of discount bonds
    out["size_ok"] = np.where(out["amount_outstanding"].isna(), np.nan,
                              out["amount_outstanding"] >= MIN_AMOUNT_OUTSTANDING)
    out["rating_ok"] = np.where(out["rating_numeric"].isna(), np.nan, out["rating_numeric"] <= MAX_RATING_NUMERIC)
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(outputs: list[Path], panel: pd.DataFrame, args: argparse.Namespace) -> Path:
    """Record what was built from what, so a result can be traced to an exact snapshot."""
    inputs = sorted(p for d in ["data/raw", "bloomberg_adapter/reference"] for p in (ROOT / d).rglob("*") if p.is_file())
    manifest = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "window": [str(args.start), str(args.end)],
        "rows": len(panel),
        "bonds": int(panel["isin"].nunique()),
        "month_ends": int(panel["date"].nunique()),
        "outputs": {str(p.relative_to(ROOT)): sha256(p) for p in outputs},
        "inputs": {str(p.relative_to(ROOT)): sha256(p) for p in inputs},
    }
    path = PROCESSED_DIR / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 30))
    parser.add_argument("--refresh-curve", action="store_true", help="re-download the GoC curve")
    args = parser.parse_args()

    adapter = XCBHoldingsAdapter()
    print("Loading XCB holdings (cached)...")
    classified = adapter.load_classified(args.start, args.end)
    bonds = adapter.panel_from_classified(classified)

    curve = load_goc_curve(args.start, args.end, args.refresh_curve)
    fundamentals = combine(load_edgar(), load_manual())
    panel = add_model_fields(attach(add_spreads(bonds, curve), fundamentals))

    checks = run_checks(panel, classified, fundamentals)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outputs = [PROCESSED_DIR / "bond_panel.csv", PROCESSED_DIR / "issuer_fundamentals.csv",
               PROCESSED_DIR / "validation_report.md"]
    panel.to_csv(outputs[0], index=False)
    fundamentals.to_csv(outputs[1], index=False)
    outputs[2].write_text(render_report(checks, panel))
    manifest = write_manifest(outputs, panel, args)

    for c in checks:
        print(f"  [{c.status}] {c.name}: {c.detail}")
    print(f"\nWrote {', '.join(p.name for p in outputs)}, {manifest.name}")
    if any(c.status == "FAIL" for c in checks):
        raise SystemExit("Validation failed; see validation_report.md")


if __name__ == "__main__":
    main()
