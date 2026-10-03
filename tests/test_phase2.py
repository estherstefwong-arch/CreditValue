from datetime import date

import pandas as pd
import pytest

from bloomberg_adapter.bond_math import coupon_dates, modified_duration, yield_from_price
from bloomberg_adapter.manual_csv import attach_ratings, rating_to_numeric
from bloomberg_adapter.universe import classify, normalize_name


def test_par_bond_on_coupon_date_yields_coupon():
    assert yield_from_price(100.0, 4.0, date(2024, 6, 1), date(2029, 6, 1)) == pytest.approx(4.0, abs=1e-6)


def test_accrued_interest_is_handled():
    # Mid-period, a bond priced at par clean still yields about its coupon.
    assert yield_from_price(100.0, 4.0, date(2024, 9, 1), date(2029, 6, 1)) == pytest.approx(4.0, abs=0.02)


def test_price_below_par_yields_above_coupon():
    assert yield_from_price(95.0, 4.0, date(2024, 6, 1), date(2029, 6, 1)) > 4.0


def test_coupon_schedule_rolls_back_from_maturity():
    prev, dates = coupon_dates(date(2024, 7, 15), date(2026, 1, 8))
    assert prev == date(2024, 7, 8)
    assert dates == [date(2025, 1, 8), date(2025, 7, 8), date(2026, 1, 8)]


def test_modified_duration_five_year_par_bond():
    # 5y 4% semi-annual par bond: modified duration ≈ 4.49 years.
    assert modified_duration(4.0, 4.0, date(2024, 6, 1), date(2029, 6, 1)) == pytest.approx(4.49, abs=0.01)


@pytest.mark.parametrize("raw,clean", [
    ("ROYAL BANK OF CANADA MTN RegS", "ROYAL BANK OF CANADA"),
    ("ROGERS COMMS INC", "ROGERS COMMUNICATIONS INC"),
    ("HYDRO ONE INC. MTN", "HYDRO ONE INC"),
    ("BELL CANADA MTN 144A", "BELL CANADA"),
    ("ENBRIDGE INC 60NC10 RegS", "ENBRIDGE INC"),
])
def test_normalize_name(raw, clean):
    assert normalize_name(raw) == clean


def _holding(name, issue, maturity, as_of="2026-09-30", coupon=4.0):
    return {"date": pd.Timestamp(as_of), "name": name, "coupon": coupon, "currency": "CAD",
            "issue_date": pd.Timestamp(issue), "maturity": pd.Timestamp(maturity)}


def test_classify_filters_and_seniority():
    df = classify(pd.DataFrame([
        _holding("ROYAL BANK OF CANADA", "2023-01-31", "2029-01-31"),       # bail-in senior
        _holding("BANK OF MONTREAL", "2018-03-01", "2028-03-01"),          # 10y at issue -> sub debt
        _holding("TORONTO-DOMINION BANK/THE", "2017-06-01", "2025-12-01", as_of="2023-06-30"),  # legacy
        _holding("NOVA SCOTIA POWER INC MTN", "2023-03-24", "2032-11-15"),  # not a target issuer
        _holding("ENBRIDGE GAS INC MTN", "2020-01-01", "2030-01-01"),       # excluded subsidiary
        _holding("ENBRIDGE INC 60NC10 RegS", "2024-01-01", "2084-01-01"),   # hybrid
        _holding("TELUS CORPORATION", "2025-01-01", "2027-06-01"),          # under 2y
    ]))
    assert df["exclude_reason"].tolist() == [
        "", "bank sub debt (NVCC)", "", "not a target issuer", "excluded issuer",
        "hybrid (long-dated)", "under 2y to maturity",
    ]
    assert df.loc[0, "seniority"] == "senior_bail_in"
    assert df.loc[2, "seniority"] == "senior_legacy"
    assert df.loc[6, "seniority"] == "senior"


def test_rating_average_across_agencies():
    assert rating_to_numeric("A", "A2", "AA (low)") == pytest.approx((6 + 6 + 4) / 3)
    assert rating_to_numeric("BBB-", "", "") == 10


def test_attach_ratings_as_of_and_seniority_override():
    panel = pd.DataFrame({
        "date": pd.to_datetime(["2023-06-30", "2025-06-30", "2025-06-30"]),
        "issuer": ["Bank X"] * 3,
        "seniority": ["senior_bail_in", "senior_bail_in", "senior_legacy"],
    })
    ratings = pd.DataFrame({
        "issuer": ["Bank X"] * 3,
        "seniority": ["", "", "senior_legacy"],
        "effective_date": pd.to_datetime(["2020-01-01", "2024-01-01", "2024-01-01"]),
        "rating_numeric": [6.0, 7.0, 4.0],
    })
    out = attach_ratings(panel, ratings)
    assert out["rating_numeric"].tolist() == [6.0, 7.0, 4.0]


def test_load_ratings_carries_agencies_forward(tmp_path):
    from bloomberg_adapter.manual_csv import load_ratings, normalize_rating
    p = tmp_path / "ratings.csv"
    p.write_text(
        "issuer,seniority,effective_date,sp,moodys,dbrs,source,quote,notes\n"
        "Enbridge Inc,,2023-02-10,BBB+,Baa1,BBB(High),x,,\n"
        "Enbridge Inc,,2024-03-29,,Baa2,,x,,rating action\n"
    )
    r = load_ratings(p)
    assert r["moodys"].tolist() == ["Baa1", "Baa2"]
    assert r["sp"].tolist() == ["BBB+", "BBB+"] and r["dbrs"].iloc[1] == "BBB (high)"
    assert r["rating_numeric"].tolist() == pytest.approx([8.0, (8 + 9 + 8) / 3])
    assert normalize_rating("sp", "A+ (stable)") == "A+"
    with pytest.raises(ValueError):
        normalize_rating("moodys", "BBB")


def test_withdrawn_rating_stops_carry_forward(tmp_path):
    from bloomberg_adapter.manual_csv import load_ratings
    p = tmp_path / "ratings.csv"
    p.write_text(
        "issuer,seniority,effective_date,sp,moodys,dbrs,source,quote,notes\n"
        "Fortis Inc,,2025-02-14,BBB+,Baa3,A (low),x,,\n"
        "Fortis Inc,,2026-01-01,,WR,,x,,rating action\n"
    )
    r = load_ratings(p)
    assert r["rating_numeric"].tolist() == pytest.approx([(8 + 10 + 7) / 3, (8 + 7) / 2])
