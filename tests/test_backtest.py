import numpy as np
import pandas as pd
import pytest

from src.backtest import add_forward_changes, fama_macbeth, horizon_half_life


def _ar1_panel(rho=0.9, n_bonds=150, months=36, seed=1):
    """Residuals follow AR(1); spreads move one-for-one with the residual (fair value fixed)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-31", periods=months, freq="BME")
    rows = []
    for i in range(n_bonds):
        e = rng.normal(0, 10)
        for d in dates:
            e = rho * e + rng.normal(0, 10 * np.sqrt(1 - rho**2))
            rows.append({"isin": f"B{i}", "date": d, "sector": "Telecom", "reg_resid": e,
                         "reg_fit": 100.0, "g_spread": 100.0 + e, "years_to_workout": 5.0})
    return pd.DataFrame(rows)


def test_forward_change_aligns_to_the_right_month():
    p = _ar1_panel(n_bonds=3, months=4)
    stats = pd.DataFrame({"date": p["date"].unique(), "b_log_t": 0.0})
    out = add_forward_changes(p, stats, horizons=[1])
    first, second = sorted(p["date"].unique())[:2]
    now = p[p["date"] == first].set_index("isin")["g_spread"]
    nxt = p[p["date"] == second].set_index("isin")["g_spread"]
    expected = (nxt - now) - (nxt - now).mean()  # minus the sector average change
    got = out[out["date"] == first].set_index("isin")["fwd_1"]
    assert got.to_dict() == pytest.approx(expected.to_dict())
    assert out[out["date"] == out["date"].max()]["fwd_1"].isna().all()  # no future month


def test_horizon_half_life_recovers_rho():
    hh = horizon_half_life(_ar1_panel(rho=0.9), "reg_resid", h=3)
    assert hh["rho_month"] == pytest.approx(0.9, abs=0.03)
    assert hh["from_fair_value"] == pytest.approx(0.0, abs=1e-9)


def test_fama_macbeth_slope_matches_reversion():
    p = _ar1_panel(rho=0.9)
    p["fwd_1"] = p.groupby("isin")["g_spread"].shift(-1) - p["g_spread"]
    fm = fama_macbeth(p, "reg_resid", "fwd_1", lags=1)
    assert fm["slope"] == pytest.approx(-0.1, abs=0.03) and fm["t"] < -3
