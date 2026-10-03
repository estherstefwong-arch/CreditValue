"""Month-end bond prices and terms from iShares XCB holdings files (BlackRock Canada)."""

import argparse
import io
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from bloomberg_adapter.base import BondDataAdapter
from bloomberg_adapter.bond_math import modified_duration, yield_from_price
from bloomberg_adapter.manual_csv import attach_ratings, load_amounts, load_ratings, write_templates
from bloomberg_adapter.schema import BOND_PANEL, BOND_PANEL_KEY, conform
from bloomberg_adapter.universe import classify

PRODUCT_URL = "https://www.blackrock.com/ca/investors/en/products/239485/ishares-canadian-corporate-bond-index-etf"
CSV_URL = f"{PRODUCT_URL}/1464253357814.ajax?fileType=csv&fileName=XCB_holdings&dataType=fund"
JSON_URL = f"{PRODUCT_URL}/1464253357814.ajax?tab=all&fileType=json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "xcb"
PROCESSED_DIR = ROOT / "data" / "processed"

MAX_HOLIDAY_STEPBACK = 4  # business days to step back when a month-end has no file
DURATION_TOLERANCE = 0.25  # years; flag yields whose implied duration disagrees with the ETF's


class EmptyHoldings(Exception):
    pass


def _get(url: str, as_of: date) -> str:
    resp = requests.get(f"{url}&asOfDate={as_of:%Y%m%d}", headers=HEADERS, timeout=60)
    resp.raise_for_status()
    time.sleep(1)  # be polite to BlackRock's site
    return resp.content.decode("utf-8-sig")


def download(as_of: date) -> tuple[Path, Path]:
    """Download the CSV and JSON files for one date into RAW_DIR, reusing cached copies."""
    csv_path, json_path = RAW_DIR / f"{as_of:%Y-%m-%d}.csv", RAW_DIR / f"{as_of:%Y-%m-%d}.json"
    if csv_path.exists() and json_path.exists():
        return csv_path, json_path
    text = _get(CSV_URL, as_of)
    if text.splitlines()[0].strip().endswith('"-"'):  # "Fund Holdings as of,"-"" = no file that day
        raise EmptyHoldings(as_of)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(text, encoding="utf-8")
    json_path.write_text(_get(JSON_URL, as_of), encoding="utf-8")
    return csv_path, json_path


def parse(csv_path: Path, json_path: Path) -> pd.DataFrame:
    """Raw fixed-income holdings for one date, with ISINs joined from the JSON file."""
    header, _, body = csv_path.read_text(encoding="utf-8").split("\n", 2)
    as_of = pd.to_datetime(header.split(",", 1)[1].strip().strip('"'), format="%b %d, %Y")
    h = pd.read_csv(io.StringIO(body), thousands=",")
    h = h[h["Asset Class"] == "Fixed Income"]

    # The CSV has no ISIN; the JSON does. Market value (to the cent) identifies each row.
    rows = json.loads(json_path.read_text(encoding="utf-8"))["aaData"]
    isin_by_mv = {round(r[4]["raw"], 2): r[5] for r in rows}
    h["isin"] = h["Market Value"].round(2).map(isin_by_mv)
    if h["isin"].isna().any():
        raise ValueError(f"{csv_path.name}: {int(h['isin'].isna().sum())} rows without an ISIN match")

    return pd.DataFrame({
        "date": as_of,
        "isin": h["isin"],
        "name": h["Name"],
        "etf_sector": h["Sector"],
        "currency": h["Currency"],
        "coupon": h["Coupon (%)"].astype("float64"),
        "maturity": pd.to_datetime(h["Maturity"], format="%b %d, %Y"),
        "issue_date": pd.to_datetime(h["Effective Date"], format="%b %d, %Y"),
        "price": h["Price"].astype("float64"),
        "etf_duration": h["Duration"].astype("float64"),
        "etf_par_held": h["Par Value"].astype("float64"),
    }).reset_index(drop=True)


def month_end_dates(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.date_range(start, end, freq="BME")]


def fetch_month_end(target: date) -> pd.DataFrame:
    """Holdings for the last business day of target's month, stepping back over holidays."""
    d = pd.Timestamp(target)
    for _ in range(MAX_HOLIDAY_STEPBACK + 1):
        try:
            return parse(*download(d.date()))
        except EmptyHoldings:
            d -= pd.offsets.BDay(1)
    raise EmptyHoldings(f"no holdings file within {MAX_HOLIDAY_STEPBACK} business days of {target}")


def add_yields(df: pd.DataFrame) -> pd.DataFrame:
    """Yield from price to each row's workout date, plus implied duration vs the ETF's."""
    df = df.copy()
    if "workout_date" not in df:
        df["workout_date"] = df["maturity"]
    args = list(zip(df["price"], df["coupon"], df["date"].dt.date, df["workout_date"].dt.date))
    df["yield"] = [yield_from_price(p, c, s, w) for p, c, s, w in args]
    df["implied_duration"] = [modified_duration(y, c, s, w) for y, (_, c, s, w) in zip(df["yield"], args)]
    df["duration_gap"] = df["implied_duration"] - df["etf_duration"]
    return df


def resolve_structure(df: pd.DataFrame) -> pd.DataFrame:
    """Use the ETF's duration to detect structures the holdings file doesn't label.

    - Floating-rate notes have near-zero duration: excluded.
    - Bonds callable one year before maturity (bank 4NC3 / 5NC4 / 6NC5 bail-in senior) match
      the ETF's duration when priced to the call: kept, with workout_date = the call date.
    - Anything else that disagrees: excluded.
    Judged per bond on the median across months, so one noisy month doesn't decide.
    """
    df = df.copy()
    live = df["exclude_reason"] == ""
    by_bond = df[live].groupby("isin")
    ratio = (df["etf_duration"] / df["implied_duration"])[live].groupby(df.loc[live, "isin"]).median()
    off = by_bond["duration_gap"].median().abs() > DURATION_TOLERANCE
    frn = ratio < 0.5
    suspects = off[off & ~frn].index

    # Re-price suspects to a call one year before maturity and keep the ones that then match.
    rows = live & df["isin"].isin(suspects)
    to_call = add_yields(df[rows].assign(workout_date=df.loc[rows, "maturity"] - pd.DateOffset(years=1)))
    call_ok = to_call.groupby("isin")["duration_gap"].median().abs() <= DURATION_TOLERANCE
    keep_called = call_ok[call_ok].index
    swap = to_call["isin"].isin(keep_called)
    cols = ["workout_date", "yield", "implied_duration", "duration_gap"]
    df.loc[to_call.index[swap], cols] = to_call.loc[swap, cols]

    reason = pd.Series("", index=ratio.index)
    reason[suspects.difference(keep_called)] = "callable or fix-to-float (duration check)"
    reason[frn[frn].index] = "floating rate (duration check)"
    df.loc[live, "exclude_reason"] = df.loc[live, "isin"].map(reason).to_numpy()
    return df


def flag_stale(panel: pd.DataFrame) -> pd.Series:
    """'stale' when a bond's price is unchanged from the prior month-end, else 'evaluated'."""
    prev = panel.sort_values("date").groupby("isin")["price"].shift()
    return pd.Series(np.where(panel["price"] == prev.reindex(panel.index), "stale", "evaluated"), index=panel.index)


class XCBHoldingsAdapter(BondDataAdapter):
    source = "ishares_xcb"

    def load_classified(self, start: date, end: date) -> pd.DataFrame:
        """Every fixed-income holding at each month-end, with yields and exclude_reason."""
        frames = []
        for target in month_end_dates(start, end):
            frames.append(fetch_month_end(target))
            print(f"  {target:%Y-%m}: {len(frames[-1])} holdings", flush=True)
        return resolve_structure(add_yields(classify(pd.concat(frames, ignore_index=True))))

    def get_panel(self, start: date, end: date) -> pd.DataFrame:
        return self.panel_from_classified(self.load_classified(start, end))

    def panel_from_classified(self, classified: pd.DataFrame) -> pd.DataFrame:
        df = classified[classified["exclude_reason"] == ""].copy()
        df["source"] = self.source
        df["price_quality"] = flag_stale(df)
        df["amount_outstanding"] = df["isin"].map(load_amounts())
        df = attach_ratings(df, load_ratings())
        return conform(df, BOND_PANEL, BOND_PANEL_KEY)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    adapter = XCBHoldingsAdapter()
    print(f"Fetching XCB month-ends {args.start} to {args.end} (cached files are reused)")
    classified = adapter.load_classified(args.start, args.end)
    universe = classified[classified["exclude_reason"] == ""]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    classified.to_csv(PROCESSED_DIR / "xcb_classified.csv", index=False)
    write_templates(universe)
    panel = adapter.panel_from_classified(classified)
    panel.to_csv(PROCESSED_DIR / "bond_panel_xcb.csv", index=False)

    target = classified[classified["sector"].notna()]
    print("\nExclusions among target-issuer bonds (all months):")
    print(target["exclude_reason"].replace("", "IN UNIVERSE").value_counts().to_string())
    bad = universe["duration_gap"].abs() > DURATION_TOLERANCE
    print(f"\nYield check (after structure exclusions): {int(bad.sum())} of {len(universe)} rows off by "
          f"more than {DURATION_TOLERANCE}y; median |gap| {universe['duration_gap'].abs().median():.3f}y")
    called = panel.loc[panel["workout_date"] < panel["maturity"], "isin"].nunique()
    print(f"Priced to a 1y-early call: {called} bonds")
    print(f"Panel: {len(panel)} rows, {panel['isin'].nunique()} bonds, {panel['date'].nunique()} month-ends")


if __name__ == "__main__":
    main()
