from src.load_data import load_bonds, load_goc_yields


def test_load_bonds_reads_real_panel():
    b = load_bonds()
    assert len(b) > 5000 and b["isin"].nunique() > 200
    assert {"g_spread", "rating", "years_to_maturity", "leverage", "seniority"} <= set(b.columns)
    assert b["years_to_maturity"].between(2, 12.01).all()


def test_goc_month_ends_are_complete_months():
    g = load_goc_yields()
    assert list(g.columns) == [2.0, 3.0, 5.0, 7.0, 10.0, 30.0]
    assert g.index.max().day >= 25  # no partial final month
