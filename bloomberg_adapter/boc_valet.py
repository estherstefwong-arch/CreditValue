"""Government of Canada benchmark yields from the Bank of Canada Valet API (free, no key)."""

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from bloomberg_adapter.schema import GOC_CURVE, GOC_CURVE_KEY, conform

VALET_URL = "https://www.bankofcanada.ca/valet/observations/group/bond_yields_benchmark/json"

# The "long" benchmark is the current ~30-year bond; its exact maturity drifts as benchmarks
# roll, so treat it as 30 years. It only matters for bonds beyond 10 years.
SERIES_TENORS = {
    "BD.CDN.2YR.DQ.YLD": 2.0,
    "BD.CDN.3YR.DQ.YLD": 3.0,
    "BD.CDN.5YR.DQ.YLD": 5.0,
    "BD.CDN.7YR.DQ.YLD": 7.0,
    "BD.CDN.10YR.DQ.YLD": 10.0,
    "BD.CDN.LONG.DQ.YLD": 30.0,
}

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "boc"


def parse_observations(payload: dict) -> pd.DataFrame:
    """Turn a Valet JSON payload into the GOC_CURVE schema (daily, long format)."""
    rows = []
    for obs in payload["observations"]:
        for series, tenor in SERIES_TENORS.items():
            value = obs.get(series, {}).get("v")
            if value not in (None, ""):
                rows.append({"date": obs["d"], "tenor_years": tenor, "yield": float(value)})
    return conform(pd.DataFrame(rows, columns=["date", "tenor_years", "yield"]), GOC_CURVE, GOC_CURVE_KEY)


def fetch_benchmark_yields(start: date, end: date | None = None) -> pd.DataFrame:
    params = {"start_date": start.isoformat()}
    if end:
        params["end_date"] = end.isoformat()
    resp = requests.get(VALET_URL, params=params, timeout=30)
    resp.raise_for_status()
    return parse_observations(resp.json())


def to_month_end(daily: pd.DataFrame) -> pd.DataFrame:
    """Keep each tenor's last observation in each calendar month, stamped with its actual date.

    The actual (last business) date is kept rather than the calendar month-end so it lines up
    with the ETF holdings snapshots, which are also taken on the last business day.
    """
    month = daily["date"].dt.to_period("M")
    last_date = daily.groupby(month)["date"].transform("max")
    monthly = daily[daily["date"] == last_date]

    # Drop the final month if it is still in progress (its last date is before the month's
    # last business day). Earlier months are complete even if a holiday ends them early.
    final = monthly["date"].max()
    if final < final + pd.offsets.BMonthEnd(0):
        monthly = monthly[monthly["date"] != final]
    return monthly.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    daily = fetch_benchmark_yields(args.start, args.end)
    monthly = to_month_end(daily)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    daily.to_csv(RAW_DIR / "goc_benchmark_daily.csv", index=False)
    monthly.to_csv(RAW_DIR / "goc_benchmark_month_end.csv", index=False)
    print(
        f"{daily['date'].nunique()} days, {monthly['date'].nunique()} month-ends "
        f"({daily['date'].min():%Y-%m-%d} to {daily['date'].max():%Y-%m-%d}) -> {RAW_DIR}"
    )


if __name__ == "__main__":
    main()
