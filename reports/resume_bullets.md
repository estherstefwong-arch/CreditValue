# Resume bullets

Numbers as of the Sep 30, 2026 build. Rerun the pipeline and update after filling the missing ratings (Hydro One, BIP, National Bank).

- Built a relative value model across 233 Canadian IG corporate bonds (45 month-ends, 2023–26) in Python with no Bloomberg access, sourcing prices, fundamentals and ratings from ETF holdings, the Bank of Canada and SEC filings, and regressing G-spreads on rating, maturity, size and structure (monthly R² 0.87–0.96)
- Found rich/cheap residuals revert slowly but reliably: the cheapest quintile beat the richest by ~3bp per quarter in every year tested (Fama-MacBeth t ≈ −6), with a ~11-month half-life
- Pitched a CS01-neutral long Pembina 3.62% 2029 / short TCPL 3.00% 2029 trade, capturing a 21bp mispricing between similar-vintage pipeline bonds with ~8bp/yr of carry and ~10bp expected over 12 months

## Interview notes

- The R² is high mostly because rating and sector explain each issuer's average spread. Say so first; leverage and coverage were not significant once rating and sector were in.
- Coupon effect: within an issuer, premium bonds trade ~1–2.5bp wider per price point and discount bonds tighter (tax treatment of Canadian discount bonds, plus older bonds being less liquid). Without adjusting for it, "cheap" just meant "high coupon".
- Callable effect: bank bail-in notes callable a year early (4NC3, 5NC4, 6NC5) trade ~20bp wider than bullets.
- The backtest uses evaluated (vendor model) prices, so it is evidence, not proof; check pitched bonds on CIRO trade data.
