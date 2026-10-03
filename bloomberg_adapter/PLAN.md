# Bloomberg Adapter: plan for building without a terminal

The PRD assumes Bloomberg for bond prices, spreads, terms, ratings, and fundamentals. This
folder replaces that dependency with free sources behind one common interface, so the rest of
the pipeline (`spreads.py`, `curves.py`, `regression.py`, ...) never knows where data came from.
If terminal access appears later, a `BloombergCSVAdapter` drops in without touching the model.

## Core idea

1. Define one **canonical bond panel schema** that mirrors the Bloomberg fields in the PRD.
2. Write one **adapter per source**. Each adapter outputs that schema and nothing else.
3. **Compute G-spreads ourselves** from yield and the Bank of Canada curve instead of relying on
   a vendor's G-spread. This removes the biggest Bloomberg-only field.

## Canonical schema

`bond_panel` (one row per bond per month-end):

| Field | Type | Bloomberg equivalent | Notes |
| --- | --- | --- | --- |
| `date` | date | — | Month-end |
| `isin` | str | `ID_ISIN` | Primary key with `date` |
| `issuer` | str | `ISSUER` | Normalized name |
| `parent` | str | `ULT_PARENT_TICKER_EXCHANGE` | For mapping to fundamentals |
| `sector` | str | `INDUSTRY_SECTOR` | Banks / Telecom / Pipelines / Utilities |
| `seniority` | str | `PAYMENT_RANK` | `senior_bail_in` vs `senior_legacy` for banks |
| `coupon` | float | `CPN` | % |
| `maturity` | date | `MATURITY` | |
| `issue_date` | date | `ISSUE_DT` | |
| `amount_outstanding` | float | `AMT_OUTSTANDING` | C$ |
| `price` | float | `PX_LAST` | Clean price |
| `yield` | float | `YLD_YTM_MID` | % |
| `rating_numeric` | float | `BB_COMPOSITE` (mapped) | AAA = 1 … BBB- = 10 |
| `source` | str | — | Which adapter produced the row |
| `price_quality` | str | — | `traded` / `evaluated` / `stale` |

`issuer_fundamentals` (one row per parent per quarter): `parent`, `period_end`,
`available_date` (filing date, used for the one-quarter lag), `net_debt`, `ebitda`,
`interest_expense`, `total_assets`, `source`.

## Data sources

| Need | Primary free source | Backup | Confidence |
| --- | --- | --- | --- |
| GoC yield curve | Bank of Canada Valet API | — | High: free, documented, daily |
| Bond universe + terms (ISIN, coupon, maturity) | iShares XCB holdings file (BlackRock Canada) | BMO ZCS / Vanguard VCB holdings | High for current snapshot |
| Month-end price history | XCB holdings via `asOfDate` (yield computed from price) | — | High: confirmed back to 2019 |
| Traded-price spot checks | CIRO bond trade data (bondtradedata.ciro.ca), in a browser | — | Manual only: blocked to scripts |
| Amount outstanding, issue date, bail-in flag | SEDAR+ pricing supplements / issuer debt IR pages | ETF file if present | Manual, ~40 bonds is manageable |
| Ratings | DBRS Morningstar / S&P / Moody's public sites (free registration) | Issuer IR pages | Manual; ratings change rarely, so keep a dated table |
| Fundamentals | SEC EDGAR XBRL API (most issuers file 40-F: big 5 banks, Enbridge, TC Energy, BCE, Rogers, Telus, Pembina) | SEDAR+ filings by hand | High for 40-F filers; manual for Hydro One, Fortis, National Bank |
| Market context | FRED (ICE BofA US IG OAS, VIX) | — | High, though US IG is a proxy for Canadian IG |

**Last-resort fallback (PRD option):** USD bonds of the same Canadian issuers via FINRA TRACE.

**If you have university access:** WRDS often includes TRACE, Mergent FISD (bond terms), and
Compustat (fundamentals). Check before building scrapers; it could replace several adapters.

## Adapters to build

```text
bloomberg_adapter/
├── PLAN.md
├── schema.py          # dataclasses / pandera schema for bond_panel and issuer_fundamentals
├── base.py            # BondDataAdapter interface: get_universe(), get_panel(start, end)
├── boc_valet.py       # GoC benchmark yields (2,3,5,7,10y), daily -> month-end
├── etf_holdings.py    # XCB (+ ZCS/VCB) holdings -> universe, price, yield, coupon, maturity
├── edgar_xbrl.py      # 40-F filers -> net debt, EBITDA, interest expense, total assets
├── manual_csv.py      # hand-maintained CSVs: ratings, amount outstanding, bail-in flags
├── bloomberg_csv.py   # future: reads BDP/BDH Excel exports into the same schema
└── build_panel.py     # runs adapters, merges, validates, writes data/processed/
```

Interface sketch:

```python
class BondDataAdapter(ABC):
    source: str

    @abstractmethod
    def get_panel(self, start: date, end: date) -> pd.DataFrame:
        """Return rows conforming to bond_panel (missing fields as NaN)."""
```

`build_panel.py` merges adapters by `(isin, date)` with a field-level priority, e.g. price from
Bloomberg when available, otherwise the ETF evaluated price, and flags `price_quality`.

## Phase 0 results (run 2026-10-03)

| Source | Result | What it means |
| --- | --- | --- |
| Bank of Canada Valet | ✅ Works, no key needed. Group `bond_yields_benchmark` gives daily 2/3/5/7/10y + long benchmark yields (series `BD.CDN.{2,3,5,7,10}YR.DQ.YLD`) | GoC curve solved |
| XCB holdings CSV | ✅ Works, including history via `&asOfDate=YYYYMMDD`. Back to at least Jun 2019 (~890 bonds then, 1,355 now). **The date must be a business day**: Sunday 2024-06-30 returns an empty file | 3+ years of month-end history is available |
| XCB CSV columns | Ticker, Name, Sector, Market Value, Weight, Par Value, **Price**, Location, Currency, **Duration**, **Maturity**, **Coupon**, Effective Date (issue/accrual date). **No ISIN, no yield, no rating, no amount outstanding** | Compute yield from price ourselves; check it against the Duration column |
| XCB JSON (`tab=all&fileType=json`) | ✅ Has **ISIN**, also accepts `asOfDate`. Joins to the CSV on market value: 1,355/1,355 rows today, 1,086/1,086 for Jun 2024 | Every row gets an ISIN as its key |
| Universe size (today, 2–12y) | Banks 75, Telecom 32, Pipelines 44, Utilities 34 → **185 candidates** | Plenty. The ≥C$300M filter and senior-unsecured filter narrow it to the PRD's 30–50 |
| SEC EDGAR XBRL | ✅ Works for all target issuers except Hydro One (no SEC CIK). **Taxonomy differs by issuer**: Enbridge uses us-gaap with quarterly 10-Q; Fortis uses us-gaap, mostly annual 40-F; BCE uses ifrs-full, mostly annual | Needs a per-issuer tag map. Expect annual data for most issuers; quarterly where available |
| CIRO bond trade data | ❌ Not scriptable. `bondtradedata.ciro.ca` sits behind a Cloudflare bot challenge; the old iiroc.ca host no longer resolves | Manual browser checks only, for flagged bonds |
| WRDS | ❓ Not checked; needs your school login | |

**Design changes from Phase 0**

- `ciro_trades.py` is dropped as an automated adapter. CIRO is a manual sanity check for the final
  pitched pair only. Staleness is detected from the ETF prices themselves (unchanged price
  month to month while GoC yields moved).
- Name normalization is required. The same issuer appears as `ROYAL BANK OF CANADA`, `... MTN`,
  `... RegS`, `... MTN RegS`. Subsidiaries must be mapped explicitly:
  Enbridge Gas ≠ Enbridge Inc, FortisAlberta / FortisBC ≠ Fortis Inc, TransCanada PipeLines →
  TC Energy, Bell Canada → BCE.
- Exclude hybrids and structured lines: names containing `NC` call schedules (e.g. `60NC10`),
  `TRUST` (e.g. TransCanada Trust), and anything that isn't a plain senior bond.
- **Bail-in flag rule:** Canada's bail-in regime applies to senior bank debt issued on or after
  2018-09-23 with an original term over 400 days. Use the Effective Date column as the issue
  date to classify bank bonds; spot-check a few against prospectuses.
- Month-end dates: request the last business day of each month (use a pandas business-month-end
  offset, with a retry on the previous day for Canadian holidays).
- Amount outstanding and ratings stay manual (`manual_csv.py`). Hydro One fundamentals come
  from SEDAR+ by hand.

## Phases

### Phase 0: Verify sources (✅ done; results above)

Answer these before writing adapter code; the answers decide the design.

- [ ] Download the current XCB holdings CSV. Confirm which columns exist (price, YTM, coupon,
      maturity, ISIN, par). Note: XCB holds ~1,350 bonds, so the 30–50 bond universe is a subset.
- [ ] Test whether past month-end holdings can be downloaded (an `asOfDate`-style parameter).
      If yes, this is the 3-year price history. If no, see the fallback below.
- [ ] Pull one bond on bondtradedata.iiroc.ca. Check fields, export options, and how painful
      36 months × 40 bonds would be (3 months per view = ~12 pulls per bond).
- [ ] Hit the Bank of Canada Valet API for benchmark bond yields.
- [ ] Hit EDGAR `companyfacts` for one 40-F filer (e.g. Enbridge) and check IFRS tags available.
- [ ] Check WRDS access through school.

**If historical ETF holdings are not available:** start saving XCB holdings every month-end
from now on, and use CIRO trades (aggregated to month-end VWAP, yield computed from price) for
the backfill. Fewer months of history is acceptable; the PRD says the backtest can shrink.

### Phase 1: Schema and GoC curve (✅ done 2026-10-03)

- [x] `schema.py` with validation (types, ranges, no duplicate `(isin, date)`).
- [x] `boc_valet.py`: daily benchmark yields → month-end, saved to `data/raw/boc/`.
- [x] Interpolation helper (`goc_curve.py`) so `spreads.py` can compute G-spread = bond yield − interpolated GoC
      yield at the bond's remaining maturity.

### Phase 2: Universe and prices (✅ done 2026-10-03, manual CSVs still to fill)

Run: `uv run python -m bloomberg_adapter.etf_holdings --start 2023-01-01 --end 2026-09-30`
(downloads are cached in `data/raw/xcb/`; reruns are offline). Outputs
`data/processed/bond_panel_xcb.csv` (universe only) and `xcb_classified.csv` (every holding,
with `exclude_reason`).

- [x] `etf_holdings.py`: 45 month-ends (Jan 2023 to Sep 2026), ISIN join, holiday step-back.
- [x] `universe.py` + `reference/issuers.csv`: exact name → issuer / parent / sector map
      (edit the CSV to change the universe), PRD filters, bail-in vs legacy.
- [x] `bond_math.py`: yield from clean price (Act/365 accrued, semi-annual), modified duration.
- [x] Staleness flag (price unchanged vs prior month-end): 22 rows.
- [x] `manual_csv.py`: rating scale (S&P / Moody's / DBRS → 1…10+, averaged), as-of rating
      joins, amount-outstanding join, template generation.
- [ ] **You:** fill `reference/ratings.csv` (16 rows) and `reference/amount_outstanding.csv`
      (sorted by ETF holding size; fill the top ones first). The ≥C$300M filter is not applied
      until amounts exist.

**Result:** 233 bonds, 5,873 bond-months, 38–44 banks / 26–31 pipelines / 27–40 telecom /
21–27 utilities per month. Implied duration matches the ETF's on every row (median gap 0.026y).
Median G-spreads, Sep 2026: banks 72bp, utilities 72bp, telecom 78bp, pipelines 85bp.

**Phase 2 findings that change the method**

1. **Bank sub debt.** CAD bank bonds with a ~10y original term are NVCC subordinated
   (10NC5). Excluded by term ≥ 9.5y (1,416 bond-months).
2. **Callable bank senior is now the norm.** Since ~2023 Canadian banks issue bail-in senior as
   4NC3 / 5NC4 / 6NC5 / 8NC7: callable one year before maturity, when it stops counting toward
   TLAC. These are **kept and priced to the call** (`workout_date` = maturity − 1y), which is how
   the market quotes them. A bond only gets this treatment if its duration to the call matches
   the ETF's. This departs from the PRD's "no callable structures" filter on purpose: excluding
   them would leave ~7 bank bonds by 2026. 41 bonds are priced to call.
3. **Floating-rate notes** hide in the file with their current coupon. Detected by ETF duration
   < 50% of bullet duration and excluded (35 bond-months).
4. **Low-coupon discount bonds trade rich.** National Bank 1.57% 2026 (price 92–97) shows G-spreads
   of −40 to −70bp in 2024. Most likely the Canadian tax effect: pull-to-par is a capital gain,
   taxed more lightly than coupon income. **Add a discount control to the Phase 3 regression**
   (e.g. `100 − price` or coupon − yield) so these aren't flagged as rich for a non-credit
   reason. Check this bond in CIRO's trade data before treating the price as real.
5. The 2–12y filter uses final maturity, not the call date. Revisit if short-call bonds look odd.

### Phase 3: Fundamentals (✅ done 2026-10-03, manual rows still to fill)

Run: `uv run python -m bloomberg_adapter.fundamentals` (EDGAR downloads cached in
`data/raw/edgar/`). Set `SEC_USER_AGENT="Your Name you@example.com"` first; SEC asks for a
real contact. Outputs `data/processed/issuer_fundamentals.csv` and
`bond_panel_with_fundamentals.csv` (panel + `leverage`, `coverage`, `fundamentals_period_end`).

- [x] `edgar_xbrl.py`: explicit per-issuer tag map for 8 parents (ENB, TRP, FTS, BCE, RCI, TU,
      PPL, BIP). Net debt = borrowings − cash; LTM EBITDA = operating income + D&A +
      impairments − disposal gains; interest expense; total assets.
- [x] Point-in-time: first-filed value per period, `available_date` = filing date of the
      leverage inputs (this replaces the PRD's flat one-quarter lag and is stricter).
- [x] `fundamentals.py`: manual overrides (cell by cell), as-of join onto the panel, figures
      older than ~15 months dropped.
- [ ] **You:** fill `reference/fundamentals_manual.csv`: Hydro One FY2020–FY2025 from SEDAR+
      (with filing dates), and Pembina FY2022 EBITDA excluding the JV gain.

**Result:** 100% of pipeline and telecom bond-months have leverage and coverage; utilities 45%
(Hydro One is the whole gap). Median leverage: BCE 3.6x, PPL 3.2x, TU 4.2x, RCI 4.9x,
TRP 5.5x, BIP 5.7x, FTS 6.1x, ENB 6.4x.

**Phase 3 findings**

1. **Mostly annual data.** 40-F filers tag balance sheets at fiscal year-end only; ENB (10-Q),
   FTS and TU (semi-annual) give more points. Fine for slow-moving leverage.
2. **Tags are not comparable across issuers.** Rogers tags its *Adjusted EBITDA* line as
   operating profit (adding D&A would double-count); BCE has no operating-income tag (EBIT is
   rebuilt as pre-tax income + interest); BIP tags net debt directly; BCE's interest tag has
   gaps, so cash interest paid is the fallback.
3. **Restatement look-ahead.** Later filings re-tag old periods (e.g. ENB's FY2022 interest first
   appears under a new tag in 2025). Without the earliest-filed rule, FY2022 would look
   "available" only in 2025, or worse, revised values would leak backwards.
4. **Leverage is GAAP-based, not "adjusted".** ENB shows ~6x vs its reported ~5x because hybrids
   count as 100% debt and EBITDA is unadjusted. It is consistent across issuers, which is what a
   cross-sectional model needs; say so in the write-up.
5. **One-off gains still slip through** when they aren't separately tagged (Pembina 2022 JV gain
   → 2.3x). Use the manual override for these.
6. **Banks:** no leverage/coverage by design. In the regression, banks get their own group
   (bank dummy + bail-in/legacy) and rely on rating; leverage enters as zero with a non-bank
   interaction, or the regression runs separately for banks.

### Phase 4: Build, validate, freeze (✅ done 2026-10-03)

Run: `uv run python -m bloomberg_adapter.build_panel` (all inputs cached; `--refresh-curve`
re-downloads the GoC curve). Rerun after editing any `reference/*.csv`.

Outputs in `data/processed/`:
- `bond_panel.csv`: **the model input**. One row per bond per month-end: terms, price, yield,
  `workout_date`, `years_to_workout`, `goc_yield`, `g_spread_bp`, `leverage`, `coverage`,
  `rating_numeric`, `amount_outstanding`, plus model fields `is_bank`, `seniority`,
  `discount_pts` (100 − price, the tax-effect control), `size_ok` / `rating_ok` (NaN = unknown).
- `issuer_fundamentals.csv`, `validation_report.md`, `manifest.json` (SHA-256 of every input
  and output, so results trace to an exact snapshot).

- [x] `build_panel.py` assembles bonds + G-spreads + fundamentals.
- [x] `validate.py`: FAIL = data wrong (build exits non-zero), WARN = incomplete.
- [x] CIRO cross-check replaced by the ETF-duration check (CIRO can't be scripted); do a manual
      CIRO check on the bonds you end up pitching.
- [ ] Commit the frozen snapshot.

**Validation (2026-10-03):** all data checks pass. 114–140 bonds per month-end, all four
sectors every month; spread curves slope upward in 100% of sector-months; banks tightest in
100% of months; only 13 rows (National Bank 1.57% 2026) outside 0–300bp. Warnings are the
manual inputs: ratings 0%, amounts 0%, non-bank leverage 85% (Hydro One).

Median G-spread (bp): 2023 banks 112 / pipelines 172 / telecom 164 / utilities 132 →
2026 banks 68 / pipelines 94 / telecom 84 / utilities 74.

**Next (outside this adapter):** the PRD's `src/` model (curves, regression, backtest, trade)
reads `data/processed/bond_panel.csv`. Filter with `size_ok != False` and `rating_ok != False`
once the manual CSVs are filled.

## Trade-offs versus Bloomberg (put these in the write-up)

- **Evaluated vs traded prices.** ETF holdings use evaluated (vendor-model) prices, which are
  smoother than trades. That can understate rich/cheap gaps and overstate mean reversion. Use
  CIRO trades to check the flagged bonds specifically.
- **History length.** Depends on Phase 0. A shorter backtest with an honest note beats a
  fabricated one.
- **Manual fields** (ratings, amount outstanding) are point-in-time only if you record dates.
  Keep an `effective_date` column.
- **Interview angle:** building a vendor-agnostic data layer from public sources is itself a
  good talking point about data quality and look-ahead bias.

## Swapping in Bloomberg later

Write `bloomberg_csv.py` to read BDP/BDH exports into `bond_panel`, set its priority highest in
`build_panel.py`, and rerun. Compare Bloomberg G-spreads against the computed ones as a
validation of the free-source pipeline.
