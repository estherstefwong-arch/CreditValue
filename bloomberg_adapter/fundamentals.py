"""Combine EDGAR and manual fundamentals and attach them point-in-time to the bond panel."""

from pathlib import Path

import numpy as np
import pandas as pd

from bloomberg_adapter.schema import ISSUER_FUNDAMENTALS, ISSUER_FUNDAMENTALS_KEY, conform

MANUAL_PATH = Path(__file__).parent / "reference" / "fundamentals_manual.csv"
MANUAL_COLUMNS = ["parent", "period_end", "available_date", "net_debt", "ebitda", "interest_expense",
                  "total_assets", "currency", "source", "note"]
MAX_STALENESS = pd.Timedelta(days=550)  # an annual filer's figures stay current ~15 months
VALUE_COLUMNS = ["net_debt", "ebitda", "interest_expense", "total_assets"]


def load_manual(path: Path = MANUAL_PATH) -> pd.DataFrame:
    """Rows with at least one value filled in. Blank cells mean "no override"."""
    if not path.exists():
        return pd.DataFrame(columns=MANUAL_COLUMNS)
    m = pd.read_csv(path, dtype={"parent": str, "currency": str, "source": str, "note": str})
    m = m.dropna(subset=VALUE_COLUMNS, how="all")
    for col in ["period_end", "available_date"]:
        m[col] = pd.to_datetime(m[col])
    return m


def combine(edgar: pd.DataFrame, manual: pd.DataFrame) -> pd.DataFrame:
    """EDGAR rows with manual values overriding cell by cell; manual-only rows are added."""
    if manual.empty:
        return edgar
    if manual["available_date"].isna().any():
        bad = manual.loc[manual["available_date"].isna(), ["parent", "period_end"]].to_string(index=False)
        raise ValueError(f"manual fundamentals need an available_date (filing date):\n{bad}")
    key = ISSUER_FUNDAMENTALS_KEY
    e, m = edgar.set_index(key), manual.set_index(key)
    out = e.reindex(e.index.union(m.index))
    for col in VALUE_COLUMNS + ["available_date", "currency"]:
        override = m[col].reindex(out.index)
        out[col] = override.where(override.notna(), out[col])
    out["source"] = np.where(out.index.isin(m.index), "manual", out["source"])
    return conform(out.reset_index(), ISSUER_FUNDAMENTALS, key)


def attach(panel: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    """Add leverage (net debt / EBITDA), coverage (EBITDA / interest), and fundamentals_period_end.

    Each bond-month gets the latest figures filed on or before its date, so there is no
    look-ahead. Figures older than MAX_STALENESS are dropped. Banks get none (no parent rows).
    """
    f = fundamentals.assign(
        leverage=fundamentals["net_debt"] / fundamentals["ebitda"],
        coverage=fundamentals["ebitda"] / fundamentals["interest_expense"],
    ).rename(columns={"period_end": "fundamentals_period_end"})
    f = f.sort_values("available_date")[["parent", "available_date", "fundamentals_period_end", "leverage", "coverage"]]

    left = panel.reset_index(names="_row").sort_values("date")
    left["parent"] = left["parent"].astype(object)
    f["parent"] = f["parent"].astype(object)
    out = pd.merge_asof(left, f, left_on="date", right_on="available_date", by="parent")
    stale = (out["date"] - out["fundamentals_period_end"]) > MAX_STALENESS
    out.loc[stale, ["leverage", "coverage", "fundamentals_period_end"]] = np.nan
    return out.drop(columns="available_date").sort_values("_row").set_index("_row").rename_axis(None)


def write_template(parents_without_edgar: list[str], years: range) -> None:
    """Create the manual CSV with blank rows to fill (never overwrites an existing file)."""
    if MANUAL_PATH.exists():
        return
    rows = [{"parent": p, "period_end": f"{y}-12-31", "available_date": "", "source": "SEDAR+ annual report",
             "note": "not on EDGAR"} for p in parents_without_edgar for y in years]
    rows.append({"parent": "PPL", "period_end": "2022-12-31", "available_date": "2023-02-23",
                 "source": "Pembina 2022 annual report (adjusted EBITDA)",
                 "note": "EDGAR EBITDA includes the one-time gain from the 2022 Pembina Gas Infrastructure JV; "
                         "enter EBITDA excluding it"})
    pd.DataFrame(rows, columns=MANUAL_COLUMNS).to_csv(MANUAL_PATH, index=False)


def main() -> None:
    from bloomberg_adapter.edgar_xbrl import ISSUERS, PROCESSED_DIR, load_all

    write_template(["H"], range(2020, 2026))
    combined = combine(load_all(), load_manual())
    combined.to_csv(PROCESSED_DIR / "issuer_fundamentals.csv", index=False)

    panel = pd.read_csv(PROCESSED_DIR / "bond_panel_xcb.csv", parse_dates=["date"])
    out = attach(panel, combined)
    out.to_csv(PROCESSED_DIR / "bond_panel_with_fundamentals.csv", index=False)

    missing = sorted(set(panel.loc[panel["sector"] != "Banks", "parent"]) - set(combined["parent"]))
    print(f"Fundamentals: {len(combined)} rows for {combined['parent'].nunique()} parents "
          f"({len(ISSUERS)} from EDGAR); no data yet for: {missing or 'none'}")
    cov = out.assign(has=out["leverage"].notna()).groupby("sector")["has"].mean().mul(100).round(0)
    print("Share of bond-months with leverage, by sector (%):")
    print(cov.to_string())


if __name__ == "__main__":
    main()
