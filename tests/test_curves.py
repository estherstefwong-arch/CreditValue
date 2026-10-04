import numpy as np
import pandas as pd
import pytest

from src.curves import _fit_group, add_curve_signals, premium_adjust, robust_z


def test_leave_one_out_matches_brute_force():
    rng = np.random.default_rng(0)
    x = np.log(np.array([2.0, 3.0, 4.5, 6.0, 8.0, 10.0]))
    y = 40 + 25 * x + rng.normal(0, 5, len(x))
    loo = _fit_group(x, y)["loo"]
    for i in range(len(x)):
        keep = np.arange(len(x)) != i
        b = np.polyfit(x[keep], y[keep], 1)
        assert loo[i] == pytest.approx(y[i] - np.polyval(b, x[i]))


def _issuer_panel(premium_bp_per_point=2.0):
    rows = []
    for issuer, level in [("A", 60), ("B", 90)]:
        for yrs, price, age in [(2, 104, 6), (3, 98, 1), (5, 101, 2), (7, 95, 4), (9, 110, 15), (10, 99, 1)]:
            rows.append({"date": pd.Timestamp("2026-09-30"), "issuer": issuer, "seniority": "senior",
                         "sector": "Telecom", "years_to_workout": yrs, "price": price,
                         "issue_date": pd.Timestamp("2026-09-30") - pd.DateOffset(years=age),
                         "maturity": pd.Timestamp("2026-09-30") + pd.DateOffset(years=yrs),
                         "workout_date": pd.Timestamp("2026-09-30") + pd.DateOffset(years=yrs),
                         "g_spread": level + 20 * np.log(yrs) + premium_bp_per_point * (price - 100)})
    return pd.DataFrame(rows)


def test_premium_adjust_recovers_slope_and_removes_it():
    out = premium_adjust(_issuer_panel(2.0))
    assert out["premium_slope"].iloc[0] == pytest.approx(2.0, abs=0.05)
    signals, _ = add_curve_signals(_issuer_panel(2.0))
    assert signals["issuer_resid"].abs().max() < 1.0  # nothing left once premium is removed


def test_robust_z_scales_by_month():
    resid = pd.Series([1.0, -1.0, 2.0, -2.0, 10.0])
    z = robust_z(resid, pd.Series(["m"] * 5))
    assert z.iloc[4] > 3 and abs(z.iloc[0]) < 1
