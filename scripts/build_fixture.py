"""Build the tiny CI fixture from the real downloaded data (deterministic samples).

The fixture is *real* OpenFEMA rows (public, already redacted by FEMA), just very few of them:
  * claims bulk: ~1 in 1,000 claims
  * claims API increment: 150 of those claims re-issued with a later asOfDate and +$100 building
    paid (simulating development), plus 5 claims that exist only in the increment
  * policies: ~1 in 10,000 policy-term rows
  * multiple-loss properties: ~1 in 500
  * CPI: the full monthly series (tiny)

Usage: python scripts/build_fixture.py   (requires data/raw from `python -m nfip extract-bulk`)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
FIX = ROOT / "tests" / "fixtures" / "raw"


def main() -> None:
    (FIX / "claims_api").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    r = RAW.as_posix()
    f = FIX.as_posix()
    con.execute(f"""
        copy (select * from read_parquet('{r}/NfipClaimsV3.parquet') where hash(id) % 1000 = 0 order by id)
        to '{f}/NfipClaimsV3.parquet' (format parquet)
    """)
    con.execute(f"""
        copy (
            select * replace (
                asOfDate + interval 1 day as asOfDate,
                amountPaidOnBuildingClaim + 100 as amountPaidOnBuildingClaim
            )
            from read_parquet('{f}/NfipClaimsV3.parquet')
            where yearOfLoss >= 2015 and amountPaidOnBuildingClaim is not null
            order by id limit 150
        )
        to '{f}/claims_api/loss_year=updates.parquet' (format parquet)
    """)
    con.execute(f"""
        copy (
            select * from read_parquet('{r}/NfipClaimsV3.parquet')
            where hash(id) % 1000 = 1 and yearOfLoss = 2024 order by id limit 5
        )
        to '{f}/claims_api/loss_year=new.parquet' (format parquet)
    """)
    con.execute(f"""
        copy (select * from read_parquet('{f}/NfipClaimsV3.parquet') limit 0)
        to '{f}/claims_api/_empty.parquet' (format parquet)
    """)
    con.execute(f"""
        copy (select * from read_parquet('{r}/NfipPoliciesV3.parquet') where hash(id) % 10000 = 0 order by id)
        to '{f}/NfipPoliciesV3.parquet' (format parquet, compression zstd)
    """)
    con.execute(f"""
        copy (select * from read_parquet('{r}/NfipMultipleLossProperties.parquet') where hash(id) % 500 = 0 order by id)
        to '{f}/NfipMultipleLossProperties.parquet' (format parquet)
    """)
    shutil.copy(RAW / "cpi_u_monthly.csv", FIX / "cpi_u_monthly.csv")
    for p in sorted(FIX.rglob("*.parquet")):
        n = con.execute(f"select count(*) from read_parquet('{p.as_posix()}')").fetchone()[0]
        print(f"{p.relative_to(FIX)}: {n:,} rows, {p.stat().st_size / 1e3:,.0f} kB")


if __name__ == "__main__":
    main()
