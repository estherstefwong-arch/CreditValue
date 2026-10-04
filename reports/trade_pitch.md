# Trade pitch: long Pembina Pipeline 3.62% Apr-2029 / short TransCanada PipeLines 3.00% Sep-2029

As of September 30, 2026. CS01-neutral relative value; draft numbers from `src/trade.py`.

## Trade

| | Long (cheap) | Short (rich) |
| --- | --- | --- |
| Bond | Pembina Pipeline 3.62% Apr-2029 | TransCanada PipeLines 3.00% Sep-2029 |
| ISIN | CA70632ZAM38 | CA89353ZCE66 |
| Price / yield | 98.92 / 4.08% | 97.24 / 4.00% |
| G-spread | 65bp | 53bp |
| Model residual (z) | +14bp (+1.8) | -8bp (-1.0) |
| Workout / duration | Apr-2029 / 2.33 | Sep-2029 / 2.80 |
| Face | C$10.0mm | C$8.61mm |

CS01 per leg: **C$2,346** per bp. Market value long C$10.07mm, short C$8.38mm.

## Economics over 12 months (bp of relative spread; × CS01 for C$)

| Component | bp | C$ |
| --- | --- | --- |
| Expected convergence (21bp gap × (1 − 0.94^12)) | +11.2 | +26,202 |
| Carry | +7.9 | +18,593 |
| Roll-down | -5.2 | -12,091 |
| Bid/ask (assumed 2bp per leg) | -4.0 | -9,384 |
| **Expected** | **+9.9** | **+23,321** |

- Entry spread difference (long − short): **13bp**
- Target: **1bp** (expected convergence by 12 months; half the gap closes in ~11 months)
- Stop: **43bp** (+30bp; 2× the pair's monthly volatility of 4.4bp scaled to 12 months, minimum 10bp)
- Breakeven: carry + roll-down − costs = -1.2bp over 12 months, so the trade needs 1.2bp of convergence just to break even
- Risk/reward: expected +9.9bp vs 30bp to the stop

## Why the gap should close

- Both legs are flagged by the fundamental regression, and sit cheap/rich on both their issuer and sector curves.
- The legs are comparable in sector, workout, price, coupon vintage and call structure, so the gap is not a coupon, liquidity or structure effect the model already controls for.
- Backtest: residuals close with a ~11-month half-life; the cheapest-minus-richest quintile beat by ~3bp per 3 months, in every year 2023–2026.

## Risks

- Rating actions since last year: long — none on file; short — none on file.
- Leverage (net debt / EBITDA, as filed): long — 3.16x (Dec-2023) → 3.05x (Dec-2025); short — 5.53x (Dec-2023) → 4.43x (Dec-2025). The regression's leverage term is not significant, so a real credit improvement on one side can look like a mispricing.
- Persistence: the residual gap averaged 20bp over the last 12 months and was above half today's level in 12 of them. A gap that hasn't closed in a year may be pricing something real.
- The gap may exist for a reason the model can't see: upcoming issuance by the long's issuer, M&A or regulatory news, index events. Check news and the new-issue calendar before entry.
- Prices are evaluated (vendor model), not traded. Confirm both legs on CIRO trade data.
- Slow convergence: most of the expected P&L arrives over months; carry decides whether waiting pays.
- Short-leg availability and borrow cost in CAD corporates are not modelled.
- Bank bonds only: the data can't distinguish covered, deposit-note and bail-in programs. Check each leg's pricing supplement before trading a bank pair.

![Pair history](figures/pair_history.png)
