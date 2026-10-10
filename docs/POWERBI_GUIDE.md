# Power BI guide: building the .pbix from the exported star schema

No Power BI / Tableau account was created for this project. Instead, the pipeline exports a
**BI-ready star schema** so the report can be built in Power BI Desktop (free) in about an hour.

```bash
python -m nfip export-powerbi      # writes exports/powerbi/*.parquet (+ .csv for small tables)
```

| File | Grain | Rows (full run) | Role |
|---|---|---|---|
| `fact_claims.parquet` | one NFIP claim | 2,725,989 | Fact |
| `fact_policy_exposure.parquet` | county x calendar year x rated zone x occupancy | 960,701 | Fact (exposure) |
| `dim_date` | day | 17,897 | Dimension |
| `dim_geography` | county (+ one "unknown county" member per state) | 3,297 | Dimension |
| `dim_flood_zone` | rated FIRM zone code | 74 | Dimension |
| `dim_event` | (event name, loss year) | 305 | Dimension |
| `dim_occupancy` | NFIP occupancy code | 15 | Dimension |
| `dim_cause_of_damage` | cause code | 13 | Dimension |
| `mart_*` | pre-aggregated | small | Optional: use for quick visuals, or ignore and use DAX |

Row counts are from `exports/powerbi/_manifest.json` on the 2026-10-10 run.

## 1. Load

Power BI Desktop -> **Get data -> Parquet** (one file at a time), or **Get data -> Folder** pointed at
`exports/powerbi/` and filter to `.parquet`. CSVs are provided for the small tables if Parquet is
not available in your Power BI build.

In Power Query set types: all `*_key` columns as Text except `occupancy_key` (Whole number) and
`date_key` / `period_date_key` (Date).

## 2. Relationships (Model view)

All are **many-to-one, single direction (dimension filters fact)**:

| From (many) | To (one) |
|---|---|
| `fact_claims[date_key]` | `dim_date[date_key]` |
| `fact_claims[geography_key]` | `dim_geography[geography_key]` |
| `fact_claims[flood_zone_key]` | `dim_flood_zone[flood_zone_key]` |
| `fact_claims[event_key]` | `dim_event[event_key]` |
| `fact_claims[occupancy_key]` | `dim_occupancy[occupancy_key]` |
| `fact_claims[cause_key]` | `dim_cause_of_damage[cause_key]` |
| `fact_policy_exposure[period_date_key]` | `dim_date[date_key]` |
| `fact_policy_exposure[geography_key]` | `dim_geography[geography_key]` |
| `fact_policy_exposure[flood_zone_key]` | `dim_flood_zone[flood_zone_key]` |
| `fact_policy_exposure[occupancy_key]` | `dim_occupancy[occupancy_key]` |

The two facts share four conformed dimensions, which is what lets one visual divide claims by
exposure. `dim_event` and `dim_cause_of_damage` only filter claims (exposure has no event).
`period_date_key` is January 1 of the exposure year, so use `dim_date[calendar_year]` (not a
day-level slicer) whenever a visual mixes the two facts.

Mark `dim_date` as the date table (Table tools -> Mark as date table -> `date_key`).

## 3. DAX measures

Create a `_Measures` table and add:

```DAX
Paid Claims = CALCULATE ( COUNTROWS ( fact_claims ), fact_claims[is_paid_claim] = TRUE () )
Total Paid = SUM ( fact_claims[total_paid] )
Total Paid (2025 $) = SUM ( fact_claims[total_paid_real] )
Severity (2025 $) = DIVIDE ( [Total Paid (2025 $)], [Paid Claims] )
Building Share = DIVIDE ( SUM ( fact_claims[building_paid] ),
                          SUM ( fact_claims[building_paid] ) + SUM ( fact_claims[contents_paid] ) )

Earned Policy-Years =
    CALCULATE ( SUM ( fact_policy_exposure[earned_policy_years] ),
                fact_policy_exposure[is_complete_exposure_year] = TRUE () )
Earned Premium =
    CALCULATE ( SUM ( fact_policy_exposure[earned_premium] ),
                fact_policy_exposure[is_complete_exposure_year] = TRUE () )

Frequency per 1,000 = DIVIDE ( [Paid Claims], [Earned Policy-Years] ) * 1000
Loss Cost (2025 $) = DIVIDE ( [Total Paid (2025 $)], [Earned Policy-Years] )
Paid to Premium (proxy) = DIVIDE ( [Total Paid], [Earned Premium] )
```

`Frequency`, `Loss Cost` and `Paid to Premium` are only meaningful for calendar years 2010-2025
(complete earned exposure). Add a page-level filter `dim_date[calendar_year]` 2010-2025 on pages
that use them, and label the paid/premium measure as a **proxy** (undeveloped paid losses, no
loss-adjustment expense, premium excludes fees and surcharges).

## 4. Suggested pages (mirror the Streamlit app)

1. **Overview** - cards: Paid Claims, Total Paid (2025 $), Severity, Building Share; column chart of
   Total Paid (2025 $) by `dim_date[calendar_year]`; slicers for state and `dim_flood_zone[flood_zone_group]`.
2. **Flood zones** - clustered columns of Frequency per 1,000, Severity, Loss Cost by `flood_zone_group`.
3. **Events** - bar chart of Total Paid (2025 $) by `dim_event[event_label]`, Top N = 15 filter;
   tooltip with Paid Claims and Building Share.
4. **Geography** - filled map on `dim_geography[state_name]` with Loss Cost (2025 $); table of
   `mart_loss_cost_relativity` with conditional formatting on `credibility_weighted_relativity`.

Expected numbers to sanity-check the model against (from `docs/RESULTS.md`): total paid
$89.9B nominal / $136.0B in 2025 dollars; 2010-2025 frequency 16.1 per 1,000 in A zones vs
6.7 in X/B/C zones.

## 5. Tableau / Looker Studio

The same files work unchanged: Tableau Public can read the CSVs (Parquet needs Tableau Desktop
2024.2+); for Looker Studio upload the CSV marts to Google Sheets or BigQuery. The star schema and
measure definitions above carry over one-to-one (Tableau: relationships in the logical layer,
calculated fields instead of DAX).
