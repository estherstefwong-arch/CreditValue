import pandas as pd
import pytest

from src.trade import LONG_NOTIONAL, candidate_pairs, make_leg


def _row(isin, issuer, sector, z, resid, yrs, price, issued, g, coupon=4.0):
    date = pd.Timestamp("2026-09-30")
    maturity = date + pd.DateOffset(days=int(yrs * 365.25))
    return {"date": date, "isin": isin, "issuer": issuer, "sector": sector, "reg_z": z, "reg_resid": resid,
            "issuer_z": z, "sector_z": z, "years_to_workout": yrs, "price": price, "coupon": coupon,
            "issue_date": pd.Timestamp(f"{issued}-01-15"), "maturity": maturity, "workout_date": maturity,
            "g_spread": g, "yield": 3.4 + g / 100, "seniority": "senior", "parent": issuer[:3]}


def _panel():
    rows = []
    for d in pd.date_range("2026-07-31", periods=3, freq="BME"):
        for r in [
            _row("CHEAP", "Pembina", "Pipelines", 1.8, 14, 2.6, 98.9, 2019, 65),
            _row("RICH", "TCPL", "Pipelines", -1.0, -8, 3.0, 97.2, 2019, 53),
            _row("RICH_OLD_COUPON", "TCPL", "Pipelines", -2.0, -15, 3.1, 88.0, 2012, 40),  # price too far
            _row("RICH_TELECOM", "TELUS", "Telecom", -2.0, -15, 3.0, 97.0, 2019, 50),       # other sector
        ]:
            rows.append({**r, "date": d})
    return pd.DataFrame(rows)


def test_candidate_pairs_require_same_sector_and_comparable_bonds():
    pairs = candidate_pairs(_panel(), pd.Timestamp("2026-09-30"))
    assert list(zip(pairs["isin_l"], pairs["isin_s"])) == [("CHEAP", "RICH")]
    assert pairs.loc[0, "gap_bp"] == pytest.approx(22)


def test_legs_are_cs01_neutral():
    pair = candidate_pairs(_panel(), pd.Timestamp("2026-09-30")).iloc[0]
    long, short = make_leg(pair, "_l"), make_leg(pair, "_s")
    short_face = long.cs01_per_face() * LONG_NOTIONAL / short.cs01_per_face()
    assert short.cs01_per_face() * short_face == pytest.approx(long.cs01_per_face() * LONG_NOTIONAL)
    assert 1500 < long.cs01_per_face() * LONG_NOTIONAL < 3500  # ~2.4y duration on C$10mm
