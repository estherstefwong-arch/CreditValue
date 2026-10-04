# Resume bullets

Numbers as of the Sep 30, 2026 build. Rerun the pipeline and update after filling the missing ratings (Hydro One, BIP, National Bank).

- Built a relative value model across 233 Canadian IG corporate bonds (45 month-ends, 2023–26) in Python from public data (ETF holdings, Bank of Canada, SEC filings), regressing G-spreads on rating, maturity, size and structure (monthly R² 0.87–0.96), and validated pricing against Bloomberg BVAL and CIRO trade prints
- Found rich/cheap residuals revert slowly but reliably: the cheapest quintile beat the richest by ~3bp per quarter in every year tested (Fama-MacBeth t ≈ −6), with a ~11-month half-life
- Pitched a CS01-neutral long Pembina 3.62% 2029 / short TCPL 3.00% 2029 trade on a 21bp model mispricing; showed that real Bloomberg bid/ask and repo costs cut the 12-month expectation from ~10bp to breakeven, and set a ~29bp entry threshold instead

## Interview notes

- The R² is high mostly because rating and sector explain each issuer's average spread. Say so first; leverage and coverage were not significant once rating and sector were in.
- Coupon effect: within an issuer, premium bonds trade ~1–2.5bp wider per price point and discount bonds tighter (tax treatment of Canadian discount bonds, plus older bonds being less liquid). Without adjusting for it, "cheap" just meant "high coupon".
- Callable effect: bank bail-in notes callable a year early (4NC3, 5NC4, 6NC5) trade ~20bp wider than bullets.
- The model history uses evaluated (vendor model) prices; on the pitched pair, BVAL (12bp gap) and September CIRO prints (12–14bp) agreed with the ETF data (13bp).
- Costs decide the trade: full BVAL bid/ask (~7bp round trip) and a ~15bp/yr repo special on the short (~5bp) take +9.9bp expected to +1.2bp. The disciplined answer is to wait for a wider gap, not to force the trade.
