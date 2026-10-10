"""Regenerate dbt reference seeds from authoritative sources.

* ref_states / ref_counties: U.S. Census Bureau 2020 FIPS code files (public domain).
* ref_occupancy_types / ref_cause_of_damage: code lists copied from the OpenFEMA
  NFIP Claims v3 / Policies v3 data dictionary (OpenFemaDataSetFields API).

Usage: python scripts/build_reference_seeds.py   (needs network; outputs are committed)
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import requests

from nfip import config

SEEDS = Path(__file__).resolve().parents[1] / "dbt" / "seeds"

OCCUPANCY = [
    (1, "Single family residence", "Single-family"),
    (2, "2 to 4 unit residential building", "Multi-family 2-4"),
    (3, "Residential building with more than 4 units", "Multi-family 5+"),
    (4, "Non-residential building", "Non-residential"),
    (6, "Non-residential business", "Non-residential"),
    (11, "Single-family residential building (excl. mobile home / unit in multi-unit building)", "Single-family"),
    (12, "Residential non-condo building with 2-4 units, all units insured", "Multi-family 2-4"),
    (13, "Residential non-condo building with 5+ units, all units insured", "Multi-family 5+"),
    (14, "Residential mobile/manufactured home", "Manufactured home"),
    (15, "Residential condo association (building with one or more units)", "Condo association"),
    (16, "Single residential unit within a multi-unit building", "Residential unit"),
    (17, "Non-residential mobile/manufactured home", "Non-residential"),
    (18, "Non-residential building", "Non-residential"),
    (19, "Non-residential unit within a multi-unit building", "Non-residential"),
]

CAUSES = [
    ("0", "Other causes", "Other"),
    ("1", "Tidal water overflow", "Coastal / tidal"),
    ("2", "Stream, river, or lake overflow", "Riverine"),
    ("3", "Alluvial fan overflow", "Riverine"),
    ("4", "Accumulation of rainfall or snowmelt", "Rainfall / pluvial"),
    ("7", "Erosion-demolition", "Erosion / earth movement"),
    ("8", "Erosion-removal", "Erosion / earth movement"),
    ("9", "Earth movement, landslide, land subsidence, sinkholes", "Erosion / earth movement"),
    ("A", "Closed basin lake", "Riverine"),
    ("B", "Expedited claim handling without site inspection", "Expedited handling (cause not recorded)"),
    ("C", "Expedited claim handling, follow-up site inspection", "Expedited handling (cause not recorded)"),
    ("D", "Expedited claim handling, remote adjustment pilot", "Expedited handling (cause not recorded)"),
]


def _get(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return resp.text


def main() -> None:
    SEEDS.mkdir(parents=True, exist_ok=True)
    states = list(csv.DictReader(io.StringIO(_get(config.CENSUS_STATE_URL)), delimiter="|"))
    with (SEEDS / "ref_states.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["state_abbr", "state_fips", "state_name"])
        for r in states:
            w.writerow([r["STATE"], r["STATEFP"], r["STATE_NAME"]])

    counties = list(csv.DictReader(io.StringIO(_get(config.CENSUS_COUNTY_URL)), delimiter="|"))
    with (SEEDS / "ref_counties.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["state_abbr", "state_fips", "county_fips3", "county_fips", "county_name"])
        for r in counties:
            w.writerow([r["STATE"], r["STATEFP"], r["COUNTYFP"], r["STATEFP"] + r["COUNTYFP"], r["COUNTYNAME"]])

    with (SEEDS / "ref_occupancy_types.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["occupancy_code", "occupancy_description", "occupancy_group"])
        w.writerows(OCCUPANCY)

    with (SEEDS / "ref_cause_of_damage.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cause_code", "cause_description", "cause_group"])
        w.writerows(CAUSES)
    print(f"wrote seeds to {SEEDS}: {len(states)} states, {len(counties)} counties")


if __name__ == "__main__":
    main()
