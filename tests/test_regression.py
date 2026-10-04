import numpy as np
import pandas as pd
import pytest

from src.regression import TERMS, fit_monthly, prepare


def _bonds(n_issuers=12, per_issuer=6, rating_bp=6.0, seed=0):
    rng = np.random.default_rng(seed)
    sectors = ["Banks", "Telecom", "Pipelines", "Utilities"]
    rows = []
    for i in range(n_issuers):
        sector, rating = sectors[i % 4], 4 + (i % 7)
        for k in range(per_issuer):
            yrs = 2 + k * 1.6
            price = 100 + rng.normal(0, 3)
            rows.append({
                "date": pd.Timestamp("2026-09-30"), "issuer": f"I{i}", "sector": sector,
                "seniority": "senior_bail_in" if sector == "Banks" else "senior",
                "years_to_workout": yrs, "maturity": pd.Timestamp("2026-09-30") + pd.DateOffset(years=int(yrs) + 1),
                "workout_date": pd.Timestamp("2026-09-30") + pd.DateOffset(years=int(yrs) + 1),
                "issue_date": pd.Timestamp("2026-09-30") - pd.DateOffset(years=k), "price": price,
                "rating": rating, "leverage": np.nan if sector == "Banks" else 3 + 0.7 * (i % 5),
                "coverage": np.nan if sector == "Banks" else 4 + i % 3,
                "amount_outstanding": np.nan, "etf_par_held": 1e7 * (1 + k),
                "g_spread": 20 + 30 * np.log(yrs) + rating_bp * rating + rng.normal(0, 2),
            })
    return pd.DataFrame(rows)


def test_prepare_zeroes_bank_fundamentals_and_keeps_banks():
    df = prepare(_bonds())
    banks = df[df["sector"] == "Banks"]
    assert len(banks) > 0 and (banks["leverage_nb"] == 0).all() and (banks["coverage_nb"] == 0).all()
    assert (df["callable"] == 0).all()


def test_monthly_fit_drops_constant_terms_and_recovers_rating():
    fits, stats = fit_monthly(prepare(_bonds()))
    assert np.isnan(stats["b_callable"].iloc[0])  # no callable bonds: term dropped, no rank error
    assert stats["b_rating"].iloc[0] == pytest.approx(6.0, abs=1.5)
    assert stats["r2"].iloc[0] > 0.9
    assert set(fits.columns) == {"reg_fit", "reg_resid"}
