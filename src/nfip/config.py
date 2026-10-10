"""Central configuration: paths, endpoints, and polite-client settings.

Everything path-like can be overridden with environment variables so the same
code runs against the full download (``data/raw``) or the tiny CI fixture
(``tests/fixtures/raw``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

USER_AGENT = "AwpDemon portfolio aliaskari.georgia@gmail.com (nfip-flood-claims-analytics)"

# OpenFEMA bulk files (version 3 of the NFIP datasets; v2 "FimaNfip*" endpoints were
# deprecated by FEMA with removal scheduled for 2026-10-15).
OPENFEMA_BULK = {
    "claims": "https://www.fema.gov/about/reports-and-data/openfema/v3/NfipClaimsV3.parquet",
    "policies": "https://www.fema.gov/about/reports-and-data/openfema/v3/NfipPoliciesV3.parquet",
    "multiple_loss": "https://www.fema.gov/api/open/v1/NfipMultipleLossProperties.parquet",
}
BULK_FILENAMES = {
    "claims": "NfipClaimsV3.parquet",
    "policies": "NfipPoliciesV3.parquet",
    "multiple_loss": "NfipMultipleLossProperties.parquet",
}
OPENFEMA_CLAIMS_API = "https://www.fema.gov/api/open/v3/NfipClaims"
OPENFEMA_PAGE_SIZE = 10_000  # OpenFEMA's documented maximum $top

# CPI-U, all items, seasonally adjusted (BLS series CUSR0000SA0), served by FRED as CPIAUCSL.
FRED_CPI_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
CENSUS_COUNTY_URL = "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt"
CENSUS_STATE_URL = "https://www2.census.gov/geo/docs/reference/codes2020/national_state2020.txt"


@dataclass(frozen=True)
class Paths:
    raw: Path = field(default_factory=lambda: Path(os.environ.get("NFIP_RAW_DIR", REPO_ROOT / "data" / "raw")))
    warehouse: Path = field(
        default_factory=lambda: Path(os.environ.get("NFIP_DUCKDB_PATH", REPO_ROOT / "data" / "warehouse" / "nfip.duckdb"))
    )
    docs: Path = REPO_ROOT / "docs"
    figures: Path = REPO_ROOT / "docs" / "figures"
    exports: Path = field(default_factory=lambda: Path(os.environ.get("NFIP_EXPORT_DIR", REPO_ROOT / "exports")))

    @property
    def api_increments(self) -> Path:
        return self.raw / "claims_api"


def paths() -> Paths:
    return Paths()
