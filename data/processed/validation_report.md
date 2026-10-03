# Bond panel validation

5,873 bond-months, 233 bonds, 45 month-ends (2023-01 to 2026-09).

| Status | Check | Detail |
| --- | --- | --- |
| PASS | bonds per month-end | 114–140 over 45 month-ends |
| PASS | all four sectors every month | 0 month-ends missing a sector |
| PASS | no duplicate bond-months | key: date + isin |
| PASS | yield vs ETF duration | 0 rows off by >0.25y; median gap 0.026y (stands in for the CIRO cross-check, which can't be scripted) |
| PASS | yields solved | 0 unsolved |
| PASS | G-spread within 0–300bp | 13 of 5873 rows outside (National Bank of Canada 1.57% 2026); median 97bp |
| PASS | banks tighter than telecom and pipelines | true in 100% of month-ends |
| PASS | spread curves slope upward with maturity | 100% of sector-months; Banks 100%, Pipelines 100%, Telecom 100%, Utilities 100% |
| WARN | leverage coverage (non-banks) | 85% of bond-months; missing for H |
| WARN | rating coverage | 0% of bond-months (fill reference/ratings.csv) |
| WARN | amount outstanding coverage | 0% of bond-months; size filter not applied where missing |
| PASS | stale prices | 0.5% of bond-months |

## Median G-spread by sector (bp, all month-ends in year)

| Year | Banks | Pipelines | Telecom | Utilities |
| --- | --- | --- | --- | --- |
| 2023 | 112 | 172 | 164 | 132 |
| 2024 | 70 | 125 | 120 | 102 |
| 2025 | 69 | 110 | 105 | 87 |
| 2026 | 68 | 94 | 84 | 74 |
