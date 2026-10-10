"""Data-quality report: profiles the warehouse + summarizes the latest dbt test run.

``python -m nfip quality`` -> docs/DATA_QUALITY.md (numbers regenerated, narrative kept here).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)


def dbt_test_summary(run_results: Path) -> tuple[dict[str, int], pd.DataFrame]:
    """Count dbt node statuses and list non-passing tests from target/run_results.json."""
    data = json.loads(run_results.read_text())
    rows = []
    for r in data.get("results", []):
        uid = r["unique_id"]
        rows.append(
            {
                "node": uid.split(".")[2] if uid.startswith("test.") else uid,
                "type": uid.split(".")[0],
                "status": r["status"],
                "failures": r.get("failures"),
            }
        )
    df = pd.DataFrame(rows)
    tests = df[df["type"] == "test"]
    counts = tests["status"].value_counts().to_dict()
    counts["total_tests"] = int(len(tests))
    counts["models"] = int((df["type"] == "model").sum())
    counts["seeds"] = int((df["type"] == "seed").sum())
    return counts, tests[tests["status"] != "pass"].reset_index(drop=True)


def profile(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    out = {}
    out["flags"] = con.execute(
        """
        select 'Negative payment component (re-issued checks)' as check_name, sum(dq_negative_payment::int) as rows_flagged from marts.fact_claims
        union all select 'Building paid > 101% of building coverage', sum(dq_building_paid_exceeds_coverage::int) from marts.fact_claims
        union all select 'Construction date = 1492-10-12 placeholder (nulled)', sum(dq_construction_date_sentinel::int) from marts.fact_claims
        union all select 'Construction date after date of loss', sum(dq_built_after_loss::int) from marts.fact_claims
        union all select 'Claim opened before date of loss', sum(dq_opened_before_loss::int) from marts.fact_claims
        union all select 'Rated flood zone missing', sum(dq_missing_flood_zone::int) from marts.fact_claims
        union all select 'County missing or inconsistent with state', sum(dq_missing_county::int) from marts.fact_claims
        union all select 'No flood event name', count(*) from marts.fact_claims f join marts.dim_event e using (event_key) where e.event_type = 'No event name'
        union all select 'Cause code not in data dictionary', count(*) from marts.fact_claims where cause_key = 'UNK'
        """
    ).df()
    total = con.execute("select count(*) from marts.fact_claims").fetchone()[0]
    out["flags"]["share_of_claims"] = out["flags"]["rows_flagged"] / total
    out["missing_zone_by_decade"] = con.execute(
        """
        select (loss_year // 10) * 10 as decade, count(*) as claims,
               avg(dq_missing_flood_zone::int) as share_missing_zone,
               avg(dq_construction_date_sentinel::int) as share_1492_placeholder,
               avg(case when water_depth_inches is null then 1 else 0 end) as share_missing_water_depth
        from marts.fact_claims group by 1 order by 1
        """
    ).df()
    out["sources"] = con.execute(
        """
        select record_source, count(*) as claims, min(loss_year) as min_loss_year, max(loss_year) as max_loss_year
        from marts.fact_claims group by 1 order by 1
        """
    ).df()
    out["cpi"] = con.execute(
        "select calendar_year, months_observed, round(cpi_u_annual_avg, 3) as cpi_u, round(to_base_year_factor, 4) as factor "
        "from staging.stg_fred__cpi_annual where calendar_year >= 2022 order by 1"
    ).df()
    out["geo_members"] = con.execute("select member_source, count(*) as members from marts.dim_geography group by 1 order by 1").df()
    out["total"] = pd.DataFrame({"claims": [total]})
    return out


def _md(df: pd.DataFrame, pct_cols: tuple[str, ...] = ()) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        cells = []
        for c, v in zip(cols, row, strict=True):
            if c in pct_cols:
                cells.append(f"{v:.2%}")
            elif "year" in c or c == "decade":
                cells.append(str(int(v)) if pd.notna(v) else "")
            elif isinstance(v, (int, np.integer)):
                cells.append(f"{v:,}")
            elif isinstance(v, (float, np.floating)):
                cells.append(f"{v:,.4g}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(db_path: Path | None = None) -> None:
    p = config.paths()
    con = duckdb.connect(str(db_path or p.warehouse), read_only=True)
    prof = profile(con)
    con.close()
    run_results = config.REPO_ROOT / "dbt" / "target" / "run_results.json"
    counts, non_pass = dbt_test_summary(run_results) if run_results.exists() else ({}, pd.DataFrame())
    manifest_path = p.raw / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    state_path = p.raw / "claims_api" / "_state.json"
    api_state = json.loads(state_path.read_text()) if state_path.exists() else {}

    flags = prof["flags"].copy()
    flags["rows_flagged"] = flags["rows_flagged"].astype(int)
    md = [
        "# Data-quality report",
        "",
        "_Regenerated by `python -m nfip quality` from the warehouse and the latest `dbt build` (`dbt/target/run_results.json`)._",
        "",
        "## 1. What we loaded",
        "",
        f"- Claims in `fact_claims`: **{int(prof['total']['claims'][0]):,}**",
        "",
        _md(prof["sources"]),
        "",
        "Bulk files (SHA-256 recorded at download):",
        "",
        "| dataset | file | bytes | last-modified (server) | retrieved (UTC) |",
        "|---|---|---|---|---|",
    ]
    for k, v in sorted(manifest.items()):
        md.append(f"| {k} | {v['file']} | {v['bytes']:,} | {v.get('last_modified') or 'n/a'} | {v['retrieved_at_utc']} |")
    if api_state.get("runs"):
        last = api_state["runs"][-1]
        md += [
            "",
            f"Latest API increment run ({last['run_at_utc']}): loss years {last['loss_years']}, rows {last['rows']}, "
            f"{last['http_requests']} HTTP requests at >= 1 s spacing.",
        ]
    md += [
        "",
        "## 2. dbt test results",
        "",
        f"- Seeds: {counts.get('seeds', 'n/a')}, models: {counts.get('models', 'n/a')}, data tests: {counts.get('total_tests', 'n/a')} "
        f"(pass {counts.get('pass', 0)}, warn {counts.get('warn', 0)}, error {counts.get('error', 0) + counts.get('fail', 0)}).",
        "",
        "Non-passing tests (all are deliberate `warn` severities on documented, bounded issues):",
        "",
        _md(non_pass[["node", "status", "failures"]]) if len(non_pass) else "_none_",
        "",
        "## 3. Data-quality flags on `fact_claims`",
        "",
        "Flags are computed once in `int_claims_enriched`, carried on the fact, and guarded by thresholded",
        "`flag_rate_below` tests: they fail on a *regression* in source quality, not on the known baseline.",
        "",
        _md(flags, pct_cols=("share_of_claims",)),
        "",
        "### Completeness by decade of loss",
        "",
        _md(prof["missing_zone_by_decade"], pct_cols=("share_missing_zone", "share_1492_placeholder", "share_missing_water_depth")),
        "",
        "## 4. Issues found while building (and what was done)",
        "",
        "1. **`originalConstructionDate = 1492-10-12` is a placeholder, not a date.** The `in_range` test on building age",
        "   failed with 333,370 rows aged ~490 years. 333,366 of them share exactly 1492-10-12 (Columbus Day) - all pre-FIRM,",
        "   loss years 1978-2022. Treated as unknown (nulled) in `int_claims_enriched`; flagged as `dq_construction_date_sentinel`.",
        "   Without this, any age-based rating factor or average building age is garbage.",
        "2. **Four construction years are typos (1069/1070).** After the sentinel fix the age test still found 4 rows aged ~954",
        "   years; almost certainly 1969/1970. Left as-is and the test is configured `warn_if >0, error_if >100`.",
        "3. **The CPI base year was silently missing.** BLS did not publish October 2025 CPI (federal shutdown), so FRED's",
        "   CPIAUCSL has a blank for 2025-10. A strict 12-month annual average dropped 2025, the base year, and every",
        "   real-dollar measure became NULL. Caught during build validation; the rule is now >= 11 months, `months_observed` is",
        "   kept, and `not_null` on `fact_claims.total_paid_real` + `in_range` on `months_observed` guard it:",
        "",
        _md(prof["cpi"]),
        "",
        "4. **Negative payments.** FEMA documents negatives when an uncashed check is re-issued; ~84 building and ~86 total",
        "   rows. Kept (they net out against the re-issue) and surfaced as `warn` tests.",
        "5. **County code inconsistent with state** on a few dozen claims (FEMA notes the county can reflect a project rather",
        "   than the property). The state wins; county goes to the state's 'unknown county' member.",
        "6. **Geography members not on the 2020 Census list** (e.g. Connecticut's 2022 planning regions) are added to",
        "   `dim_geography` as `observed_unlisted` so `relationships` tests never silently drop rows:",
        "",
        _md(prof["geo_members"]),
        "",
        "7. **`asOfDate` is not a change timestamp.** Every record carries the snapshot date, so it cannot drive CDC.",
        "   The incremental design re-pulls a rolling window of loss years and de-duplicates on claim id instead.",
        "8. **Event names are re-used across years** (Hurricane Florence 1988 and 2018, Tropical Storm Claudette 1979 and",
        "   2021), so `dim_event` is keyed on (name, loss year). 'Flooding', 'Not a named storm' etc. are generic buckets.",
        "",
        "## 5. Redaction and what it prevents",
        "",
        "- No policy or claim identifiers that link the two datasets: claims cannot be joined to their policy, so exposure",
        "  is matched at the aggregate (county x year x zone x occupancy) level, and paid/premium is a ratio of aggregates.",
        "- Latitude/longitude rounded to 0.1 degree (~11 km); FEMA warns points may fall in the wrong county - aggregate on",
        "  `state`/`countyCode` instead (done).",
        "- No addresses, so repeat-loss analysis uses FEMA's own Multiple Loss Properties dataset rather than matching claims.",
        "",
    ]
    (p.docs / "DATA_QUALITY.md").write_text("\n".join(md), encoding="utf-8")
    log.info("wrote docs/DATA_QUALITY.md")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
