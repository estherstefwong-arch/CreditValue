"""Yield and duration for plain fixed-coupon bonds, semi-annual, Canadian conventions."""

from datetime import date

import numpy as np
from dateutil.relativedelta import relativedelta
from scipy.optimize import brentq

FREQ = 2


def coupon_dates(settle: date, maturity: date) -> tuple[date, list[date]]:
    """Previous coupon date and all remaining coupon dates, rolled back from maturity."""
    dates = []
    d, k = maturity, 0
    while d > settle:
        dates.append(d)
        k += 1
        d = maturity - relativedelta(months=6 * k)
    return d, dates[::-1]


def _cash_flow_times(settle: date, maturity: date, coupon: float):
    prev, dates = coupon_dates(settle, maturity)
    period_days = (dates[0] - prev).days
    # Canadian corporates accrue on Actual/365, capped at a full coupon.
    accrued = min(coupon * (settle - prev).days / 365, coupon / FREQ)
    w = (dates[0] - settle).days / period_days  # fraction of the first period remaining
    times = w + np.arange(len(dates))  # in coupon periods
    flows = np.full(len(dates), coupon / FREQ)
    flows[-1] += 100.0
    return times, flows, accrued


def yield_from_price(clean_price: float, coupon: float, settle: date, maturity: date) -> float:
    """Yield to maturity in percent, semi-annual compounding. NaN if it can't be solved."""
    if maturity <= settle or not np.isfinite(clean_price):
        return np.nan
    times, flows, accrued = _cash_flow_times(settle, maturity, coupon)
    dirty = clean_price + accrued

    def pv_gap(y):
        return np.sum(flows / (1 + y / FREQ) ** times) - dirty

    try:
        return brentq(pv_gap, -0.10, 1.0) * 100
    except ValueError:
        return np.nan


def modified_duration(yield_pct: float, coupon: float, settle: date, maturity: date) -> float:
    """Modified duration in years."""
    if not np.isfinite(yield_pct) or maturity <= settle:
        return np.nan
    times, flows, _ = _cash_flow_times(settle, maturity, coupon)
    y = yield_pct / 100
    disc = flows / (1 + y / FREQ) ** times
    macaulay = np.sum(times / FREQ * disc) / np.sum(disc)
    return macaulay / (1 + y / FREQ)


def accrued_interest(coupon: float, settle: date, maturity: date) -> float:
    """Accrued interest per 100 face (Actual/365, Canadian convention)."""
    if maturity <= settle:
        return 0.0
    return _cash_flow_times(settle, maturity, coupon)[2]
