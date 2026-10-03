import pandas as pd
import pytest

from bloomberg_adapter.boc_valet import parse_observations, to_month_end
from bloomberg_adapter.goc_curve import (
    align_to_curve_dates,
    g_spread_bp,
    interpolate_goc_yield,
    years_between,
)
from bloomberg_adapter.schema import GOC_CURVE, GOC_CURVE_KEY, SchemaError, conform

PAYLOAD = {
    "observations": [
        {"d": "2024-05-30", "BD.CDN.2YR.DQ.YLD": {"v": "4.20"}, "BD.CDN.5YR.DQ.YLD": {"v": "3.80"}},
        {"d": "2024-05-31", "BD.CDN.2YR.DQ.YLD": {"v": "4.10"}, "BD.CDN.5YR.DQ.YLD": {"v": "3.70"},
         "BD.CDN.10YR.DQ.YLD": {"v": "3.60"}},
        {"d": "2024-06-28", "BD.CDN.2YR.DQ.YLD": {"v": "4.00"}, "BD.CDN.5YR.DQ.YLD": {"v": ""}},
    ]
}


def test_parse_observations_skips_blanks_and_maps_tenors():
    df = parse_observations(PAYLOAD)
    assert len(df) == 6  # blank 5y on 2024-06-28 dropped
    row = df[(df["date"] == "2024-05-31") & (df["tenor_years"] == 10.0)]
    assert row["yield"].item() == 3.60


def test_to_month_end_keeps_last_business_day():
    monthly = to_month_end(parse_observations(PAYLOAD))
    assert sorted(monthly["date"].dt.strftime("%Y-%m-%d").unique()) == ["2024-05-31", "2024-06-28"]


def test_to_month_end_drops_incomplete_final_month():
    payload = {"observations": PAYLOAD["observations"] + [{"d": "2024-07-02", "BD.CDN.2YR.DQ.YLD": {"v": "3.9"}}]}
    monthly = to_month_end(parse_observations(payload))
    assert monthly["date"].max() == pd.Timestamp("2024-06-28")


def test_interpolation_linear_and_flat_extrapolation():
    curve = parse_observations(PAYLOAD)
    d = pd.Series(pd.to_datetime(["2024-05-31"] * 4))
    y = interpolate_goc_yield(curve, d, pd.Series([1.0, 2.0, 3.5, 12.0]))
    assert y.tolist() == pytest.approx([4.10, 4.10, 3.90, 3.60])


def test_interpolation_missing_date_raises():
    curve = parse_observations(PAYLOAD)
    with pytest.raises(KeyError):
        interpolate_goc_yield(curve, pd.Series(pd.to_datetime(["2024-07-31"])), pd.Series([5.0]))


def test_align_to_curve_dates_steps_back_over_holiday():
    curve = parse_observations(PAYLOAD)
    aligned = align_to_curve_dates(pd.Series(pd.to_datetime(["2024-07-02", "2024-05-31"])), curve)
    assert aligned.tolist() == [pd.Timestamp("2024-06-28"), pd.Timestamp("2024-05-31")]
    with pytest.raises(KeyError):
        align_to_curve_dates(pd.Series(pd.to_datetime(["2024-08-30"])), curve)


def test_g_spread_and_years():
    assert g_spread_bp(pd.Series([4.95]), pd.Series([3.70])).item() == pytest.approx(125.0)
    yrs = years_between(pd.Series(pd.to_datetime(["2024-05-31"])), pd.Series(pd.to_datetime(["2029-05-31"])))
    assert yrs.item() == pytest.approx(5.0, abs=0.01)


def test_conform_rejects_duplicates_and_out_of_range():
    df = pd.DataFrame({"date": ["2024-05-31"] * 2, "tenor_years": [2.0, 2.0], "yield": [4.0, 4.1]})
    with pytest.raises(SchemaError, match="duplicate"):
        conform(df, GOC_CURVE, GOC_CURVE_KEY)
    df = pd.DataFrame({"date": ["2024-05-31"], "tenor_years": [2.0], "yield": [99.0]})
    with pytest.raises(SchemaError, match="above"):
        conform(df, GOC_CURVE, GOC_CURVE_KEY)
