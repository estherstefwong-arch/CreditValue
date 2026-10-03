import numpy as np
import pandas as pd

from bloomberg_adapter.build_panel import add_model_fields
from bloomberg_adapter.validate import check_spreads


def test_model_fields_keep_unknown_filters_as_nan():
    panel = pd.DataFrame({"sector": ["Banks", "Telecom", "Utilities"], "price": [97.5, 101.0, 99.0],
                          "amount_outstanding": [500e6, 200e6, np.nan], "rating_numeric": [5.0, np.nan, 10.0]})
    out = add_model_fields(panel)
    assert out["is_bank"].tolist() == [True, False, False]
    assert out["discount_pts"].tolist() == [2.5, 0.0, 1.0]
    assert out["size_ok"].tolist()[:2] == [1.0, 0.0] and np.isnan(out["size_ok"].iloc[2])
    assert np.isnan(out["rating_ok"].iloc[1]) and out["rating_ok"].iloc[2] == 1.0


def _sector_curve(sector, base, date="2025-06-30"):
    yrs = np.array([2.0, 3.0, 5.0, 7.0, 10.0])
    return pd.DataFrame({"date": pd.Timestamp(date), "sector": sector, "years_to_workout": yrs,
                         "g_spread_bp": base + 20 * np.log(yrs), "issuer": sector, "coupon": 4.0,
                         "maturity": pd.Timestamp("2030-01-01"), "isin": [f"{sector}{i}" for i in range(5)]})


def test_spread_checks_detect_ordering_and_slope():
    panel = pd.concat([_sector_curve("Banks", 60), _sector_curve("Telecom", 110), _sector_curve("Pipelines", 120)])
    status = {c.name: c.status for c in check_spreads(panel)}
    assert status["banks tighter than telecom and pipelines"] == "PASS"
    assert status["spread curves slope upward with maturity"] == "PASS"

    inverted = panel.assign(g_spread_bp=np.where(panel["sector"] == "Banks", 200.0, panel["g_spread_bp"]))
    status = {c.name: c.status for c in check_spreads(inverted)}
    assert status["banks tighter than telecom and pipelines"] == "WARN"
