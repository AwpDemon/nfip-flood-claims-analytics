# Interview guide: NFIP flood-claims analytics

This is your study guide for defending this project in a data/BI-analyst, analytics-engineer, or
insurance-analytics interview. It explains every design decision in plain English, the math, the
findings, and the questions you are likely to get. Every number here comes from
`docs/RESULTS.md`, `docs/DATA_QUALITY.md`, or `docs/results/model_*.csv`, which the code
regenerates. If an interviewer asks "how do you know?", the answer is always "here is the query /
test that produces it."

---

## 0. The 30-second pitch

> "I built a small analytics warehouse on FEMA's public National Flood Insurance Program data:
> 2.7 million claims and 74.7 million policy records. An extract script pulls the bulk files and
> then incrementally refreshes recent loss years from FEMA's API with rate limiting. dbt on DuckDB
> models it into a star schema - a claims fact, an earned-exposure fact, and six dimensions - with
> 87 data tests, two of which caught real data problems (a fake 1492 construction date on 12% of
> claims and a missing CPI month that nulled every inflation-adjusted number). On top I built the
> actuarial basics: frequency per policy-year, severity, loss cost, a credibility-weighted
> state-by-zone index, and a severity GLM validated out-of-time. The headline: outside the
> high-risk flood zones sits 45% of the insured exposure and a quarter of the claim dollars, and
> claim severity has grown about 2.8% a year faster than inflation."

Memorize the four numbers in that pitch: **2.7M claims, 87 tests, 45% / 25%, 2.8% real trend.**

---

## 1. NFIP basics you must know cold

- **What the NFIP is.** The National Flood Insurance Program, run by FEMA since 1968, writes most
  U.S. residential flood insurance (standard homeowners policies exclude flood). About 4.5-5
  million policies are in force. Residential limits: **$250,000 building, $100,000 contents.**
- **Flood zones (FIRM = Flood Insurance Rate Map).**
  - **A zones** (A, AE, A1-A30, AH, AO...): the 1%-annual-chance ("100-year") floodplain.
  - **V zones** (V, VE, V1-V30): coastal high hazard, the 1% flood plus 3-ft+ waves.
  - A and V together are the **SFHA (Special Flood Hazard Area)**. A federally backed mortgage in
    the SFHA **must** carry flood insurance ("mandatory purchase").
  - **X / B / C**: outside the SFHA (moderate 0.2% or minimal hazard). Insurance is optional there.
  - **D**: undetermined.
- **Pre-FIRM vs post-FIRM.** Built before the community's first flood map = pre-FIRM, historically
  subsidized; built after = post-FIRM, built to elevation standards.
- **Risk Rating 2.0** (phased in from Oct 2021) replaced zone-based rating with property-level
  rating. That is why occupancy codes 11-19 appear (RR2.0 codes) next to legacy 1-6.
- **Coverage parts.** Building, Contents, and **ICC (Increased Cost of Compliance**, up to $30k to
  elevate/demolish a substantially damaged building to meet code).
- **Repetitive loss (RL)**: roughly, 2+ paid claims over $1,000 within a rolling 10 years.
  **Severe RL (SRL)**: roughly, 4+ claims over $5,000 each (over $20,000 combined), or 2+ claims
  whose building payments together exceed the building's value. (Check FEMA's current wording
  before quoting it exactly.) These properties are a well-known drain on the program and a
  mitigation target.
- **Redaction.** OpenFEMA strips names/addresses/policy numbers and rounds lat/long to 0.1 degree.
  Consequence: **you cannot join a claim to its policy.** Everything that divides claims by
  policies must be done at an aggregate level (that is the core modeling challenge here).

---

## 2. Architecture and why each piece exists

```
OpenFEMA bulk Parquet ──┐
OpenFEMA REST API ──────┼─> data/raw (Parquet, SHA-256 manifest) ─> dbt-duckdb:
FRED CPI-U CSV ─────────┘                                         staging (views) -> intermediate -> marts (star)
                                                                   87 tests, docs
marts ─> Python: RESULTS.md + figures | severity model | Power BI export -> Streamlit app / .pbix
```

**Q: Why DuckDB instead of Postgres or pandas?**
DuckDB is an in-process columnar database: it reads Parquet directly, streams larger-than-memory
data, and aggregated the 3.7 GB / 74.7M-row policy file in about a minute on a laptop without
loading it into RAM. pandas would need the whole table in memory. Postgres would need a server and
a load step. For an analytics workload (scan + aggregate) a columnar engine is the right tool.

**Q: How does this map to production?**
Same dbt project, different adapter: raw Parquet lands in **S3**, Snowflake reads it via an
external stage (or `COPY INTO`), and `dbt build --target prod` runs with `dbt-snowflake`. The SQL is
mostly portable; the DuckDB-specific bits are `read_parquet(...)` sources, `qualify`, `group by
all` and `generate_series` - Snowflake supports `qualify` and `group by all`, and has
`generator`/`flatten` for the series. Orchestration via Airflow/Dagster or dbt Cloud on a schedule.
I deliberately did not create cloud accounts; the README documents the design.

**Q: Why dbt?**
It turns SQL into a tested, documented, version-controlled DAG: `ref()` gives dependency order and
lineage, tests run on every build, docs are generated from the YAML, and environments (dev / CI /
prod) are a profile switch. It is the de facto standard for the "T" in ELT.

---

## 3. Extract layer (src/nfip/extract.py)

- **Bulk first, API for increments.** Downloading one 164 MB Parquet file is cheaper for FEMA and
  for us than paging 2.7M rows through a REST API. The file is streamed to disk in 8 MB chunks
  (never held in memory), written to `.part` and atomically renamed, and its SHA-256, size and
  server Last-Modified go into `data/raw/manifest.json` (provenance).
- **Polite client.** Descriptive User-Agent, minimum 1 s between requests, bounded retries with
  exponential backoff, honoring `Retry-After` on HTTP 429. Unit-tested with a fake clock.
- **Why not incremental on `asOfDate`?** I checked: every record carries the snapshot date
  (all 2,725,989 claims had the same `asOfDate`), so it is not a "last modified" timestamp and
  cannot drive change-data-capture. **This is a good interview story about checking assumptions.**
- **What I did instead: rolling loss-year window.** Claims keep developing (supplemental
  payments, re-openings) for a couple of years after a flood. So each run re-pulls the current
  loss year plus the prior two (2024-2026: 121,354 claims in 14 HTTP requests), overwrites those
  partitions (idempotent), and dbt de-duplicates on claim id keeping the newest snapshot.
  Reconciliation: the API pull matched the bulk file exactly for those years (same ids, same
  paid amounts) because both came from the same FEMA refresh.
- **CPI.** FRED series CPIAUCSL (BLS CPI-U, all items, seasonally adjusted). Annual averages give a
  factor to convert year-t dollars to 2025 dollars.

---

## 4. Dimensional modeling (Kimball) - the concepts

**Fact vs dimension.** A *fact* table holds measurable events at a declared grain (one row per
claim) with numeric measures (paid amounts) and foreign keys. A *dimension* holds the descriptive
context you slice by (where, when, which zone, which event).

**Grain is the first decision.** "One row per claim" for `fact_claims`; "one row per county x
calendar year x rated zone x occupancy" for `fact_policy_exposure`. Mixing grains in one table is
the classic mistake.

**Star schema.** Facts in the middle, denormalized dimensions around them, one join hop each. BI
tools (Power BI especially) are optimized for exactly this shape.

**Conformed dimensions.** `dim_geography`, `dim_flood_zone`, `dim_occupancy` and `dim_date` are
shared by both facts, with identical keys. That is the only reason "claims per policy-year by state
and zone" is possible at all: the two facts can be filtered by the same dimension rows and divided.

**Unknown members.** Instead of dropping claims with a missing county, they point to an explicit
`UNK-<state>` row. Inner joins never lose data and `relationships` tests stay green. Same for
`UNKNOWN` flood zone, occupancy `-1`, cause `UNK`.

**Degenerate dimensions / junk attributes.** Claim id, community id and small building flags
(elevated, primary residence...) stay on the fact. In a bigger model those flags would go into a
"building profile" junk dimension.

**Why is `dim_event` keyed on (name, year)?** Storm names are reused - "Hurricane Florence" appears
in 1988 and 2018. Keying on name alone would merge two storms 30 years apart. Trade-off: an event
spanning New Year splits into two rows (documented).

**Slowly changing dimensions?** Not needed here: zone, county and occupancy attributes are recorded
on each claim as-of the loss. If we tracked a property's current zone over time, `dim_property`
would be SCD Type 2 (valid_from / valid_to rows).

**The model (tables):**

| Layer | Models | Materialization |
|---|---|---|
| staging | `stg_openfema__claims` (bulk + API union, dedupe), `stg_openfema__policies`, `stg_openfema__multiple_loss_properties`, `stg_fred__cpi_annual` | views (cheap; only typing/renaming) |
| intermediate | `int_claims_enriched` (all claim business rules), `int_policy_earned_exposure` (earned exposure) | tables |
| marts | `dim_date`, `dim_geography`, `dim_flood_zone`, `dim_event`, `dim_occupancy`, `dim_cause_of_damage`, `fact_claims` (incremental), `fact_policy_exposure`, 5 aggregate marts | tables |

**Why staging -> intermediate -> marts?** Staging = 1:1 with sources, only cleaning. Intermediate =
business logic in one place (e.g. the 1492 fix lives only in `int_claims_enriched`). Marts =
consumer-facing shapes. If the CPI rule changes, it changes once.

---

## 5. Earned exposure - the most "actuarial" part

A claim is counted by **date of loss** (calendar time). So the denominator must also be
calendar time: **earned** policy-years, not "policies written."

A policy effective 2019-06-01 to 2020-06-01 is in force 214 days in 2019 and 152 days in 2020, so
it contributes 214/365 = 0.586 policy-years to 2019 and 0.416 to 2020, and its premium is
earned in the same proportions. SQL: explode each policy term into the calendar years it touches
(`unnest(generate_series(year(start), year(end - 1 day)))`), clip to the year boundaries, and sum
the day fractions.

- Term end = the earlier of scheduled termination and cancellation.
- Exposure is complete only for **2010-2025**: the policy file starts with policies effective in
  2009, so 2009 is missing the tail of 2008 policies.
- **Tested:** a pytest asserts that summing earned policy-years over all years equals the total
  term length of all policies exactly (conservation), and a dbt test asserts earned years never
  exceed the count of active policy terms.

**Q: Why not just count policies in force on Jan 1?** That is a snapshot; frequency needs
"exposure time." Point-in-time counts bias frequency when the book grows or shrinks during the year.

---

## 6. Metrics and their definitions

- **Frequency** = paid claims / earned policy-years x 1,000.
- **Severity** = paid dollars / paid claims.
- **Loss cost** (pure premium) = paid dollars / earned policy-years = frequency x severity / 1,000.
  A dbt test enforces this identity on every row of the state-year mart.
- **Paid-to-earned-premium (proxy)** = paid / earned premium. **Never call it a loss ratio**:
  paid amounts for recent years are not developed to ultimate, loss-adjustment expense is excluded,
  and the premium field excludes the federal policy fee and HFIAA surcharge.
- **Constant dollars**: amount x (CPI 2025 / CPI loss year). CPI is general inflation; construction
  costs rose faster, which is part of why real severity still trends up (see findings).
- **Credibility (limited fluctuation)**: Z = min(1, sqrt(n / 1,082)). 1,082 claims = full
  credibility for frequency at 90% probability within +/-5% (Poisson: (1.645/0.05)^2). The
  credibility-weighted relativity shrinks thin cells toward 1.0:
  `Z x raw_relativity + (1 - Z) x 1.0`. Thin state/zone cells (Vermont V-zone, say) cannot swing
  the index on a handful of claims.

---

## 7. dbt tests - which, why, and the two that caught real problems

87 data tests in four families:

1. **Generic built-ins:** `unique` + `not_null` on every primary key; `relationships` from every
   fact foreign key to its dimension; `accepted_values` on zone groups, occupancy groups, event
   types, record source.
2. **Custom generic tests** (`dbt/tests/generic/`): `non_negative`, `in_range`,
   `unique_combination` (grain test without a package), and **`flag_rate_below`** - a *thresholded*
   data-quality test: it fails if, say, more than 6% of claims are missing a flood zone. Real data
   always has some bad rows; a test that fails on the first one is noise, a test that never fails
   is useless. Thresholds are set just above the documented baseline so they catch a regression in
   the source.
3. **Singular tests** (`dbt/tests/*.sql`): fact reconciles to staging (row count and paid dollars to
   the cent); exposure fact reconciles to intermediate; loss cost = frequency x severity; earned
   exposure never exceeds active policy terms.
4. **Severity config:** some tests are `warn` (negative payments FEMA documents as re-issued
   checks; 4 typo'd construction years) with `error_if: ">100"` so they would still fail the build
   if the problem grew.

**Story 1: the 1492 placeholder.** My `in_range` test on building age (0-400 years) failed with
333,370 rows. Profiling showed 333,366 claims with `originalConstructionDate = 1492-10-12` -
Columbus Day, a placeholder for "unknown" - 12.2% of all claims, all pre-FIRM, concentrated in
1970s-80s losses (88% of 1970s claims). Fix in `int_claims_enriched`: null it, flag it, guard the
rate with `flag_rate_below`. Without the test, "average building age" or any age-based rating
factor would be garbage.

**Story 2: the missing CPI month.** After the first build every inflation-adjusted number was
NULL. Cause: BLS did not publish October 2025 CPI during the federal shutdown, so FRED has a blank
for 2025-10; my "12 complete months" rule dropped 2025 - the base year - so every factor was NULL.
Fix: require >= 11 months, keep `months_observed`, and add `not_null` on
`fact_claims.total_paid_real` plus `in_range(11, 12)` on `months_observed` so it can never recur
silently. Honest version: I caught this one in my own validation query, then wrote the tests.

---

## 8. Incremental model

`fact_claims` is `materialized='incremental'` with `unique_key='claim_id'` and
`delete+insert`. On an incremental run it only rebuilds rows whose loss year is within the rolling
window (`max(loss_year) - 2` onward), mirroring the extract. Older loss years are treated as
settled. `dbt build --full-refresh` rebuilds everything.

**Q: When does incremental matter?** On DuckDB the full build takes ~1.5 minutes, so it barely
matters here. On Snowflake, where you pay per compute-second and the fact could be billions of
rows, rebuilding only the developing years is the difference between seconds and hours of credit
burn. I built it so the pattern is in place, and I would say exactly that.

**Q: What is the risk?** A late correction to an old loss year (e.g. a 2019 claim updated in 2026)
would be missed until the next full refresh. Mitigation: periodic full refresh (weekly), or widen
the window.

---

## 9. Findings (with the numbers)

All 2010-2025 figures use complete earned exposure; dollars are constant 2025 dollars unless noted.

1. **Scale.** 2,725,989 claims (77.7% paid), 1978-2026; $89.9B paid nominal = **$136.0B in 2025
   dollars**. Katrina alone: $26.8B real (166,818 paid claims).
2. **Zone drives frequency far more than severity.** Paid claims per 1,000 policy-years:
   V 19.3, A 16.1, X/B/C 6.7. Severity: V $98.5k, A $83.8k, X/B/C $78.3k. Loss cost per
   policy-year: V $1,899, A $1,348, X/B/C $521. So A-zone loss cost is ~2.6x X-zone, and almost
   all of that gap is frequency (2.4x) rather than severity (1.07x).
3. **Outside the SFHA is not "no risk."** X/B/C/D zones hold **45.3%** of earned policy-years and
   produce **25.4%** of paid claims and **24.1%** of paid dollars. Since purchase is optional there,
   the uninsured share is much larger than the insured book suggests.
4. **Catastrophe years dominate.** National paid / earned premium (proxy) over 2010-2025 is 0.94,
   but five years exceed 1.0 (2012 Sandy 2.91, 2016 Louisiana floods 1.35, 2017 Harvey 3.24,
   2022 Ian 1.54, 2024 Helene/Milton 2.37) and the quiet years run 0.11-0.56. The top 5 named
   events carry 46.8% of all real paid dollars since 1978.
5. **Severity is heavy-tailed.** Median paid claim $23,809 vs mean $64,246; p99 $461,673. The top
   1% of paid claims carry 11.4% of dollars; the top 10% carry 48.0%.
6. **Severity outgrew inflation.** The median real paid claim rose from ~$10.7k (1980s) to ~$45.5k
   (2020s). The severity GLM estimates a **+2.8%/year real trend** (2000-2019), controlling for
   zone, coverage and building characteristics. Drivers: higher insured values (median real
   building coverage rose too), construction cost inflation above CPI, and more large events.
7. **Northeast stands out per policy.** 2010-2025 loss cost per policy-year: NJ $3,006 (3.1x the
   national average) and NY $2,958 (3.0x) - Sandy plus Ida - with paid/premium proxies of 1.76-1.77.
   TX is the largest absolute book of losses ($15.7B real) at 1.5x national loss cost.
8. **Building dominates.** 82.3% of building+contents dollars are building; only 46.9% of paid
   claims include a contents payment. Residential units (condo units) are the exception: 59% of
   their paid dollars are contents, because the association policy covers the structure.
9. **Repetitive loss.** FEMA's Multiple Loss Properties list has 240,651 properties (219,028
   NFIP repetitive-loss, 34,996 severe repetitive-loss). Only **5.5% are mitigated** and only 43.6%
   are currently insured; 76.8% are in the SFHA. Texas, Louisiana and Florida lead by count.

---

## 10. The severity model (src/nfip/model.py)

**Question:** using only what an underwriter knows *before* a flood, how well can we rank how
big a claim will be?

- **Target:** building + contents paid on paid claims, constant 2025 dollars (ICC excluded - its own
  cap and purpose).
- **Features (underwriting-time):** flood-zone detail, occupancy group, state (top 15 + other),
  floors, basement/enclosure type, building-age band (with "unknown" for the 1492 rows),
  deductible code, primary residence, elevated, post-FIRM, log building and contents coverage.
- **Split: out-of-time.** Train 2000-2019, test 2020-2025. A random split would put claims from
  the same hurricane in both train and test; they are strongly correlated (same water, same
  adjusters), so the test would be leaky and flattering. Out-of-time is how a pricing model is
  actually used.
- **Sample:** deterministic 1-in-3 of paid claims (hash of claim id) to keep RAM modest.

**Models:**
1. Zone-mean baseline.
2. **Gamma GLM, log link** - the actuarial standard for severity: Gamma fits positive, right-skewed
   amounts whose variance grows with the mean; the log link makes effects multiplicative, so
   `exp(coefficient)` is a **rating relativity** an underwriter can read (from the fitted GLM:
   elevated buildings x0.84, finished-basement type 1 x0.57, deductible code 1 ($1,000) x0.84 vs $500;
   `docs/results/model_glm_relativities.csv`). Relativities are *conditional* on the other
   variables, and state factors absorb event history (Mississippi x2.30 is mostly Katrina).
3. Same GLM + a linear calendar-year term (severity trend), extrapolated into the test years.
4. Gradient-boosted trees (`HistGradientBoostingRegressor`, Gamma loss).
5. GBM + post-loss facts (water depth, cause, event type) - **triage only, never pricing**: you
   do not know the water depth when you write the policy. It shows how much signal only exists
   after the flood.

**Metrics:**
- **Normalized Gini**: rank test claims by prediction, plot cumulative share of actual dollars
  (Lorenz curve); Gini = 2 x area between that curve and the diagonal, divided by the same for a
  perfect model. 0 = random, 1 = perfect ordering. It measures *ranking* (segmentation), which is
  what rating relativities need. Ties are handled by averaging within tie groups so a coarse
  baseline cannot get lucky from row order.
- **Mean Gamma deviance**: the likelihood-based fit metric matching the Gamma assumption.
- **A/E (actual / expected)** overall and by predicted decile: calibration.
- **Top-decile lift**: mean actual in the highest-predicted 10% / overall mean.

**Results** (test = loss years 2020-2025, 72,931 claims; `docs/results/model_metrics.csv`):

| Model | Normalized Gini | Mean Gamma deviance | A/E | Top-decile lift |
|---|---|---|---|---|
| Baseline: zone mean | 0.114 | 1.816 | 1.11 | 1.08 |
| Gamma GLM | 0.426 | 1.665 | 1.26 | 1.91 |
| Gamma GLM + calendar-year trend | 0.436 | 1.582 | 0.95 | 1.93 |
| GBM (Gamma loss) | 0.570 | 1.566 | 1.54 | 2.61 |
| GBM + post-loss facts (triage only) | 0.631 | 1.461 | 1.54 | 2.77 |

Fitted trend: +2.77% per year real severity. Trended-GLM A/E by test year: 0.50 (2020),
0.56 (2021), 1.43 (2022, Ian), 0.72 (2023), 1.12 (2024, Helene/Milton), 0.65 (2025, undeveloped).
Two consecutive full runs reproduced every number exactly (fixed seed, sample ordered by claim id).
An earlier version read rows in DuckDB's parallel scan order and the GBM Gini drifted by ~0.005
between runs - adding `order by claim_id` fixed it. Good story about reproducibility.

**How to talk about them honestly:**
- The GLM roughly quadruples the baseline's ranking power and the GBM adds more - real
  segmentation signal from underwriting-time facts alone.
- **Level is the hard part, not ranking.** The untrended models under-predict the 2020-2025
  period (A/E > 1) because severity trends up in real terms and the test window contains Ian and
  Helene. Adding the calendar-year trend brings the overall A/E close to 1, **but year-by-year A/E
  still swings widely** (see `model_gini_by_year.csv`) because the size of a year's claims depends
  on which storms hit, which no policy-level feature can know. That is exactly why catastrophe
  load is priced separately from attritional severity in practice.
- The top predicted decile is over-predicted by the trended GLM (A/E below 1): the GLM's
  multiplicative structure extrapolates the largest coverages too aggressively. A GBM or capping /
  a coverage spline would help.
- The GBM ranks better but its level is worse; in production I would calibrate it (isotonic or an
  offset) or use the GLM for filed rates and the GBM to find interactions to add to the GLM.

**Q: Why not R-squared?** Severity is heavy-tailed; squared error is dominated by a few huge
claims and does not match the Gamma variance structure. Gini (ranking) + deviance (fit) + A/E
(calibration) is the standard pricing-model trio.

**Q: Is this a pricing model?** No. No frequency model (redaction prevents a policy-level join),
no expenses, no cat load, sample not credibility-weighted by state. It is a severity-segmentation
study with honest validation. Say that before they do.

---

## 11. Dashboards and Power BI

- **Streamlit app** (`app/dashboard.py`): filters (state, loss years, zone group), KPI tiles, trend,
  zone comparison, top events, frequency and paid/premium, and the credibility-weighted relativity
  table. It reads the exported Parquet star schema through in-memory DuckDB, so the same files back
  the app and Power BI. Screenshots in `docs/figures/dashboard_*.png`.
- **Power BI**: no account was created; `python -m nfip export-powerbi` writes the star schema and
  `docs/POWERBI_GUIDE.md` lists the relationships and DAX measures to build the .pbix.
- **Q: Power BI vs Tableau?** Power BI: DAX measures over a star schema, strong for governed
  semantic models and Microsoft shops. Tableau: faster exploratory viz, relationships in the logical
  layer. Same star schema serves both.

---

## 12. Limitations (say these first)

- Redaction: no claim-to-policy join, so frequency and paid/premium are aggregate ratios at
  county x year x zone x occupancy, not policy-level.
- Recent loss years (2024-2026) are undeveloped: paid will grow.
- Paid/premium is a proxy, not a loss ratio (no LAE, no IBNR, premium ex-fees).
- CPI-U is general inflation; construction-cost indices would be a better deflator for building
  losses.
- Rated flood zone is as of the policy; map revisions move properties between zones over time.
- The severity model has no frequency counterpart and no catastrophe load.
- Unknown-zone frequency (31.6 per 1,000) is an artifact: claims missing a zone are not matched by
  policies missing a zone. Excluded from conclusions.

## 13. What I would do next

Policy-level frequency model if FEMA ever exposes a join key; NOAA Storm Events join to separate
event intensity from vulnerability; construction-cost deflator; chain-ladder development of recent
years; deploy dbt to Snowflake with S3 landing and a scheduled run; build the .pbix and publish.

---

## 14. Rapid-fire questions

- *What's the grain of fact_claims?* One NFIP claim (OpenFEMA record id).
- *How do you avoid double counting API and bulk rows?* `qualify row_number() over (partition by
  claim_id order by asOfDate desc, api first) = 1`, plus a reconciliation test.
- *What's a conformed dimension?* A dimension shared, with identical keys and meaning, by more than
  one fact - here geography, zone, occupancy, date.
- *Why views for staging?* They only rename/cast; materializing 74.7M policy rows would cost 3+ GB
  for nothing.
- *What does `store_failures` do?* Writes failing rows of each test to a `test_failures` schema so
  you can inspect them (`select * from test_failures.<test>`).
- *How would you schedule this?* Monthly (FEMA refresh cadence): extract-bulk, extract-increment,
  `dbt build`, analyze, export; alert on any test error.
- *What would you monitor?* `flag_rate_below` thresholds, source row counts vs previous run,
  freshness of the bulk file's Last-Modified, and reconciliation tests.
- *Why Gamma and not lognormal?* Both handle skew; Gamma GLM predicts the mean directly on the
  original scale (no retransformation bias) and gives multiplicative relativities.
