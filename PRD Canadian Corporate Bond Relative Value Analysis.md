# PRD: Canadian Credit Relative Value

Oct 3, 2026 · @Darrel Wihandi

## Overview

Build a model that finds Canadian investment-grade corporate bonds trading cheap or rich versus their credit fundamentals, then turn the strongest signal into a pitched long/short trade.

**Problem.** Bonds from issuers with similar credit quality often trade at different spreads. Credit desks look for these gaps because they tend to close. Most student projects stop at a model; this one ends with a trade a desk could act on.

**Why it matters for recruiting.** Capital markets interviews test whether you can form and defend a market view. This project shows credit analysis, fixed income math, Python, and a trade pitch in one story. It also connects directly to the credit loss modeling from the PwC internship.

## Goals and success criteria

The project succeeds if it produces one defensible trade idea backed by a model a credit analyst would recognize.

| Goal | Success criterion |
| --- | --- |
| Working relative value model | Fundamental regression explains at least 50% of spread variation (R² ≥ 0.5) across the universe |
| Evidence the signal is real | Historical test shows rich/cheap residuals shrink over the following 1–3 months, with a measured half-life |
| Actionable output | One long/short trade pitch with entry spread, target, stop, carry, and risks |
| Public, reviewable work | Clean GitHub repo plus a short write-up linked on the resume |
| Delivered on time | Finished within 6 weeks at about 8–10 hours per week |

The R² target is a benchmark, not a requirement. A lower R² with an honest explanation is still a strong result.

## Scope

The universe is 30–50 Canadian-dollar, investment-grade, senior unsecured bonds from four sectors.

**Bond universe**

| Sector | Example issuers | Why include |
| --- | --- | --- |
| Banks | RBC, TD, BMO, Scotiabank, CIBC, National Bank | Largest, most liquid part of the Canadian corporate market |
| Telecom | BCE, Rogers, Telus | High leverage creates real spread differences |
| Pipelines and midstream | Enbridge, TC Energy, Pembina | Large issuers with long curves |
| Utilities | Hydro One, Fortis, Brookfield Infrastructure | Stable credits, useful low-spread anchor |

**Filters:** rated BBB- or higher, 2–12 years to maturity, at least C$300 million outstanding, fixed coupon, no callable structures beyond standard make-whole calls.

**In scope:** spread calculation, issuer and sector spread curves, fundamental regression, rich/cheap signal, historical mean-reversion test, one pitched trade.

**Out of scope:** high yield, floating-rate notes, preferred shares, live trading, transaction cost modeling beyond a simple bid/ask assumption.

Bank bonds need care. Bail-in senior debt trades wider than legacy senior debt, so treat them as separate groups.

## Data requirements

Bloomberg is the primary source for bond data; everything else is free.

| Data | Source | Fields | Frequency |
| --- | --- | --- | --- |
| Bond prices and spreads | Bloomberg terminal (Excel BDP/BDH pulls) | Price, yield, G-spread, maturity, coupon, amount outstanding, rating, issue date | Monthly, last 3 years |
| Government of Canada yield curve | Bank of Canada Valet API (free) | Benchmark yields at 2, 3, 5, 7, 10 years | Daily, resample to monthly |
| Issuer fundamentals | Company annual and quarterly reports (SEDAR+), or Bloomberg | Net debt, EBITDA, interest expense, total assets | Quarterly |
| Market context | Bloomberg or FRED | Broad IG spread index, VIX | Monthly |

**Cleaning steps**

1. Drop bonds with stale prices (no price change for 5+ trading days).
2. Map each bond to its issuer and parent company so fundamentals line up.
3. Convert ratings to a numeric scale (AAA = 1 … BBB- = 10), averaging agencies where they differ.
4. Lag fundamentals by one quarter so the model only uses information the market had at the time.

If Bloomberg access is limited, pull a one-time snapshot plus month-end history in a single session and save it as CSV.

## Methodology

The model has six steps: measure spreads, fit curves, explain spreads with fundamentals, score rich/cheap, test the signal, then build the trade.

1. **Measure spreads.** Use G-spread: the bond's yield minus the interpolated Government of Canada yield at the same maturity. This is how the Canadian market quotes corporate bonds.
2. **Fit spread curves.** For each issuer with 3+ bonds, fit spread against maturity (a simple log-maturity fit works). A bond sitting above its own issuer's curve is cheap within that issuer. Repeat at the sector-and-rating level.
3. **Fundamental regression.** Explain spreads across the whole universe with a cross-sectional regression:

```latex
\text{Spread}_i = \alpha + \beta_1 \ln(\text{Maturity}_i) + \beta_2 \text{Rating}_i + \beta_3 \frac{\text{Net debt}}{\text{EBITDA}}_i + \beta_4 \text{Coverage}_i + \beta_5 \ln(\text{Size}_i) + \gamma_{\text{sector}} + \varepsilon_i
```

The residual is how far each bond trades from where its fundamentals say it should.

4. **Score rich/cheap.** Convert residuals to z-scores. Flag bonds beyond ±1.5 standard deviations. Prefer bonds flagged by both the curve fit and the regression.
5. **Test the signal.** Rerun the model at each month-end over 3 years. Check whether today's residual predicts spread change over the next 1–3 months. Estimate a half-life by fitting an AR(1) model to each bond's residual.
6. **Build the trade.** Pair the strongest cheap bond with a rich bond of similar maturity and sector. Size the legs so spread risk (CS01, the dollar change per 1bp of spread) is equal, which removes exposure to broad market moves. Report carry, rolldown, target spread, stop-loss, and breakeven widening.

The key interview point: the trade bets on the gap closing, not on the direction of the overall credit market.

## Deliverables

Four outputs, each usable on its own in applications and interviews.

| Deliverable | Contents | Used for |
| --- | --- | --- |
| GitHub repo | Clean code, README with results and charts, saved data snapshot | Resume link, technical interviews |
| Trade pitch (2 pages) | Trade summary, why the gap exists, entry/target/stop, carry, risks, one chart | Sales & trading and credit interviews, coffee chats |
| Write-up (4–6 pages) | Method, regression output, mean-reversion test, limitations | LinkedIn post, deeper technical conversations |
| Resume bullets | 2–3 bullets with method, number, and finding | Replaces the StockTrak project |

**Charts to include:** spread vs. maturity by sector with fitted curves; actual vs. model-predicted spread scatter with flagged bonds labeled; residual history for the pitched pair.

## Tech stack and repo structure

Python for the model, Excel only for Bloomberg pulls.

- **Python:** pandas, numpy, statsmodels (regression, AR(1)), scipy (curve fitting, interpolation), matplotlib
- **Data pulls:** Excel with Bloomberg BDP/BDH formulas, saved to CSV
- **Environment:** Jupyter for exploration, plain .py modules for the final pipeline

```text
cad-ig-relative-value/
├── README.md            # summary, key charts, the trade idea
├── data/
│   ├── raw/             # Bloomberg and Bank of Canada exports
│   └── processed/       # cleaned bond panel
├── src/
│   ├── load_data.py     # read and clean raw files
│   ├── spreads.py       # GoC curve interpolation, G-spreads
│   ├── curves.py        # issuer and sector curve fits
│   ├── regression.py    # fundamental model, residuals, z-scores
│   ├── backtest.py      # monthly reruns, mean reversion, half-life
│   └── trade.py         # CS01-neutral sizing, carry, breakevens
├── notebooks/
│   └── analysis.ipynb   # walkthrough with charts
└── reports/
    ├── trade_pitch.pdf
    └── writeup.pdf
```

## Timeline

Six weeks at about 8–10 hours per week; a first resume-ready version exists after week 4.

- [ ] **Week 1 — Data:** finalize bond list, pull Bloomberg data and GoC yields, save raw CSVs
- [ ] **Week 2 — Spreads and curves:** clean data, compute G-spreads, fit issuer and sector curves
- [ ] **Week 3 — Fundamentals:** collect leverage and coverage, run the regression, produce residuals and z-scores
- [ ] **Week 4 — Signal test:** monthly reruns, mean-reversion test, half-life; add bullets to resume
- [ ] **Week 5 — Trade:** pick the pair, size CS01-neutral, compute carry and breakevens, draft the pitch
- [ ] **Week 6 — Polish:** README, charts, write-up, LinkedIn post; get feedback from an upper-year or club member

If time is tight, weeks 4 and 6 can shrink. Do not skip the trade pitch in week 5; it is the part interviewers care about most.

## Risks and mitigations

The biggest risk is data access; every other risk is manageable inside the model.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Limited Bloomberg access | Blocks the whole project | Book terminal time early; pull all history in one session. Fallback: USD bonds of the same Canadian issuers from FINRA TRACE |
| Stale or illiquid prices | False rich/cheap signals | Drop stale bonds; add issue size and bond age to the regression |
| Small sample (30–50 bonds) | Unstable regression, overfitting | Keep to 5–6 variables; pool monthly data into a panel |
| Bail-in vs. legacy bank debt | Mixes structurally different bonds | Separate dummy variable or separate group |
| Gap exists for a real reason | Trade looks cheap but is not | Check news, ratings outlook, and upcoming issuance before pitching |
| School workload | Project stalls | Week 4 version is already resume-ready |

## Resume and interview positioning

The project replaces StockTrak and gives a ready answer to "pitch me a trade." Numbers below are placeholders; fill them with real results.

**Draft resume bullets**

- Built a relative value model across \[40\] Canadian IG corporate bonds in Python, regressing G-spreads on leverage, coverage, rating, and maturity (R² \[0.6\]) to flag bonds trading \[±1.5σ\] from fair value
- Tested signal persistence over \[3\] years of month-end data, finding mispricings closed with a \[2.5\]-month half-life
- Pitched a CS01-neutral long/short trade in \[sector\] bonds capturing \[20\]bp of mispricing with \[X\]bp of annual carry

**Interview questions to prepare**

- Walk me through your trade. Why does the gap exist, and why will it close?
- What would make you wrong, and where is your stop?
- Why G-spread and not Z-spread or OAS?
- How did you remove exposure to the overall credit market?
- How does this connect to your credit loss work at PwC?

The PwC link is the strongest talking point: ECL work estimates default risk from the lender's side, and this project shows how the market prices that same risk.
