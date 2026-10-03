import pandas as pd
import pytest

from bloomberg_adapter.edgar_xbrl import ltm_series, resolve
from bloomberg_adapter.fundamentals import attach, combine
from bloomberg_adapter.schema import ISSUER_FUNDAMENTALS, ISSUER_FUNDAMENTALS_KEY, conform


def _fact(val, end, filed, start=None, form="10-K"):
    f = {"val": val, "end": end, "filed": filed, "form": form}
    if start:
        f["start"] = start
    return f


def _company(**tags):
    return {"facts": {"us-gaap": {t: {"units": {"CAD": facts}} for t, facts in tags.items()}}}


def test_ltm_from_annual_and_year_to_date():
    c = _company(OperatingIncomeLoss=[
        _fact(100, "2023-12-31", "2024-02-10", "2023-01-01"),
        _fact(30, "2023-06-30", "2023-08-01", "2023-01-01", "10-Q"),   # H1 2023
        _fact(40, "2024-06-30", "2024-08-01", "2024-01-01", "10-Q"),   # H1 2024
    ])
    ltm = ltm_series(c, "us-gaap", "OperatingIncomeLoss", "CAD")
    assert ltm.loc["2023-12-31", "val"] == 100
    assert ltm.loc["2024-06-30", "val"] == 110  # 40 + 100 - 30
    assert ltm.loc["2024-06-30", "filed"] == pd.Timestamp("2024-08-01")


def test_first_filed_value_wins_over_restatement():
    c = _company(Assets=[_fact(500, "2023-12-31", "2024-02-10"), _fact(520, "2023-12-31", "2025-02-10")])
    out = resolve(c, {"taxonomy": "us-gaap", "unit": "CAD", "assets": [["Assets"]]}, "assets", "instant")
    assert out.loc["2023-12-31", "val"] == 500


def test_resolve_required_optional_and_earliest_alternative():
    c = _company(
        LongTermDebt=[_fact(80, "2023-12-31", "2024-02-10"), _fact(90, "2024-12-31", "2025-02-10")],
        ShortTermBorrowings=[_fact(5, "2023-12-31", "2024-02-10"), _fact(7, "2024-12-31", "2026-02-10")],
        OtherDebt=[_fact(99, "2023-12-31", "2023-11-01")],  # filed earlier: wins for 2023
    )
    spec = {"taxonomy": "us-gaap", "unit": "CAD",
            "debt": [["LongTermDebt", "?ShortTermBorrowings"], ["OtherDebt"]]}
    out = resolve(c, spec, "debt", "instant")
    assert out.loc["2023-12-31", "val"] == 99
    assert out.loc["2024-12-31", "val"] == 90  # optional filed a year late: ignored


def _fundamentals(rows):
    return conform(pd.DataFrame(rows).assign(source="test"), ISSUER_FUNDAMENTALS, ISSUER_FUNDAMENTALS_KEY)


def test_attach_is_point_in_time_and_drops_stale():
    f = _fundamentals([
        {"parent": "ENB", "period_end": "2022-12-31", "available_date": "2023-02-10", "net_debt": 600, "ebitda": 100,
         "interest_expense": 25},
        {"parent": "ENB", "period_end": "2023-12-31", "available_date": "2024-02-09", "net_debt": 550, "ebitda": 100,
         "interest_expense": 25},
    ])
    panel = pd.DataFrame({
        "date": pd.to_datetime(["2023-01-31", "2023-03-31", "2024-01-31", "2024-02-29", "2025-09-30"]),
        "parent": ["ENB"] * 5,
    })
    out = attach(panel, f)
    assert pd.isna(out.loc[0, "leverage"])            # nothing filed yet
    assert out.loc[1, "leverage"] == pytest.approx(6.0)
    assert out.loc[2, "leverage"] == pytest.approx(6.0)  # FY2023 not filed until Feb 9
    assert out.loc[3, "leverage"] == pytest.approx(5.5)
    assert out.loc[3, "coverage"] == pytest.approx(4.0)
    assert pd.isna(out.loc[4, "leverage"])            # FY2023 figures too stale by Sep 2025


def test_manual_overrides_cell_by_cell():
    edgar = _fundamentals([{"parent": "PPL", "period_end": "2022-12-31", "available_date": "2023-02-23",
                            "net_debt": 10, "ebitda": 4.4, "interest_expense": 0.4}])
    manual = pd.DataFrame({"parent": ["PPL", "H"], "period_end": pd.to_datetime(["2022-12-31", "2023-12-31"]),
                           "available_date": pd.to_datetime(["2023-02-23", "2024-02-15"]),
                           "net_debt": [None, 15.0], "ebitda": [3.3, 2.5], "interest_expense": [None, 0.6],
                           "total_assets": [None, None], "currency": [None, "CAD"]})
    out = combine(edgar, manual).set_index("parent")
    assert out.loc["PPL", "ebitda"] == 3.3 and out.loc["PPL", "net_debt"] == 10
    assert out.loc["H", "net_debt"] == 15.0 and out.loc["H", "source"] == "manual"
