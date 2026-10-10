"""Export the star schema for Power BI (or Tableau / Looker Studio).

Writes ``exports/powerbi/<table>.parquet`` (+ ``.csv`` for the small tables) straight from DuckDB
with ``COPY ... TO`` - rows never pass through pandas, so memory stays flat even for the
2.7M-row claim fact. See docs/POWERBI_GUIDE.md for relationships and DAX measures.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb

from . import config

log = logging.getLogger(__name__)

# table -> (source relation, also write CSV?)
STAR_TABLES: dict[str, tuple[str, bool]] = {
    "fact_claims": ("marts.fact_claims", False),
    "fact_policy_exposure": ("marts.fact_policy_exposure", False),
    "dim_date": ("marts.dim_date", True),
    "dim_geography": ("marts.dim_geography", True),
    "dim_flood_zone": ("marts.dim_flood_zone", True),
    "dim_event": ("marts.dim_event", True),
    "dim_occupancy": ("marts.dim_occupancy", True),
    "dim_cause_of_damage": ("marts.dim_cause_of_damage", True),
    "mart_loss_experience_state_year": ("marts.mart_loss_experience_state_year", True),
    "mart_event_losses": ("marts.mart_event_losses", True),
    "mart_loss_cost_relativity": ("marts.mart_loss_cost_relativity", True),
}


def export_star(db_path: Path, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=True)
    counts: dict[str, int] = {}
    for name, (rel, csv) in STAR_TABLES.items():
        target = (out_dir / f"{name}.parquet").as_posix()
        con.execute(f"copy (select * from {rel}) to '{target}' (format parquet, compression zstd)")
        if csv:
            con.execute(f"copy (select * from {rel}) to '{(out_dir / f'{name}.csv').as_posix()}' (header, delimiter ',')")
        counts[name] = con.execute(f"select count(*) from {rel}").fetchone()[0]
        log.info("exported %s (%s rows)", name, f"{counts[name]:,}")
    con.close()
    (out_dir / "_manifest.json").write_text(json.dumps(counts, indent=2))
    return counts


def main() -> None:
    p = config.paths()
    export_star(p.warehouse, p.exports / "powerbi")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
