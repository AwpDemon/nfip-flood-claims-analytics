"""End-to-end: dbt build on the real-data fixture, then invariants on the warehouse.

Runs `dbt build` (seeds, 19 models, all data tests) against tests/fixtures/raw into a temp
DuckDB file, then checks business invariants that are easy to get wrong: de-duplication of
API increments, the 1492 placeholder fix, exposure conservation, and the downstream Python
steps (analysis, quality report, Power BI export) running on the result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RAW = ROOT / "tests" / "fixtures" / "raw"


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory) -> Path:
    if shutil.which("dbt", path=str(Path(sys.executable).parent)) is None and shutil.which("dbt") is None:
        pytest.skip("dbt not installed")
    tmp = tmp_path_factory.mktemp("wh")
    db = tmp / "fixture.duckdb"
    env = os.environ.copy()
    env.update(NFIP_RAW_DIR=FIXTURE_RAW.as_posix(), NFIP_DUCKDB_PATH=db.as_posix(), NFIP_DUCKDB_MEMORY="1GB")
    cmd = [sys.executable, "-m", "nfip", "dbt", "build", "--target-path", (tmp / "target").as_posix()]
    res = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
    assert "ERROR=0" in res.stdout
    return db


@pytest.fixture(scope="module")
def con(warehouse):
    c = duckdb.connect(str(warehouse), read_only=True)
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_fact_row_count_is_bulk_plus_new_api_ids(con):
    raw = FIXTURE_RAW.as_posix()
    bulk = one(con, f"select count(distinct id) from read_parquet('{raw}/NfipClaimsV3.parquet')")
    new = one(
        con,
        f"""select count(*) from read_parquet('{raw}/claims_api/*.parquet', union_by_name=true)
                       where id not in (select id from read_parquet('{raw}/NfipClaimsV3.parquet'))""",
    )
    assert new == 5
    assert one(con, "select count(*) from marts.fact_claims") == bulk + new


def test_api_increment_wins_over_bulk_snapshot(con):
    raw = FIXTURE_RAW.as_posix()
    mismatches = one(
        con,
        f"""
        select count(*) from read_parquet('{raw}/claims_api/loss_year=updates.parquet') a
        join marts.fact_claims f on f.claim_id = a.id
        where f.record_source <> 'api' or abs(f.building_paid - a.amountPaidOnBuildingClaim::double) > 0.001
    """,
    )
    assert mismatches == 0
    assert one(con, "select count(*) from marts.fact_claims where record_source = 'api'") == 155


def test_construction_placeholder_is_nulled(con):
    assert one(con, "select count(*) from marts.fact_claims where building_age_at_loss_years > 400") == 0
    assert one(con, "select count(*) from marts.fact_claims where dq_construction_date_sentinel") > 0


def test_every_claim_has_real_dollars(con):
    assert one(con, "select count(*) from marts.fact_claims where total_paid_real is null") == 0


def test_earned_exposure_is_conserved(con):
    """Sum of earned policy-years over all calendar years == total term length in years."""
    total_term_years = one(
        con,
        """
        select sum(date_diff('day', policy_effective_date, term_end_date)) / 365.0
        from staging.stg_openfema__policies where term_end_date > policy_effective_date
    """,
    )
    earned = one(con, "select sum(earned_policy_years) from marts.fact_policy_exposure")
    assert earned == pytest.approx(total_term_years, rel=1e-9)


def test_star_schema_has_no_orphans(con):
    for key, dim in [
        ("geography_key", "dim_geography"),
        ("flood_zone_key", "dim_flood_zone"),
        ("event_key", "dim_event"),
        ("occupancy_key", "dim_occupancy"),
        ("cause_key", "dim_cause_of_damage"),
    ]:
        orphans = one(con, f"select count(*) from marts.fact_claims f anti join marts.{dim} d using ({key})")
        assert orphans == 0, key


def test_downstream_python_steps_run_on_fixture(warehouse, tmp_path, monkeypatch):
    from nfip import analysis, export, quality

    monkeypatch.setenv("NFIP_EXPORT_DIR", str(tmp_path / "exports"))
    counts = export.export_star(warehouse, tmp_path / "exports" / "powerbi")
    assert counts["fact_claims"] > 2000 and (tmp_path / "exports" / "powerbi" / "dim_event.csv").exists()

    # analysis + quality write into docs/; redirect by patching config paths to tmp
    from nfip import config

    class P(config.Paths):
        pass

    p = P(raw=FIXTURE_RAW, warehouse=warehouse, docs=tmp_path / "docs", figures=tmp_path / "docs" / "figures", exports=tmp_path / "exports")
    monkeypatch.setattr(config, "paths", lambda: p)
    h = analysis.main(db_path=warehouse)
    assert h["claims"] > 2000
    assert (tmp_path / "docs" / "RESULTS.md").exists()
    assert len(list((tmp_path / "docs" / "figures").glob("*.png"))) == 9
    quality.main(db_path=warehouse)
    assert "Data-quality report" in (tmp_path / "docs" / "DATA_QUALITY.md").read_text(encoding="utf-8")
