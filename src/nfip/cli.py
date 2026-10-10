"""Command-line entry point: ``python -m nfip <command>``."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import config, extract


def run_dbt(dbt_args: list[str], p: config.Paths) -> int:
    """Invoke dbt with absolute paths so DuckDB views over external files resolve from anywhere."""
    import os
    import shutil
    import subprocess
    import sys

    env = os.environ.copy()
    env["NFIP_RAW_DIR"] = str(p.raw.resolve()).replace("\\", "/")
    env["NFIP_DUCKDB_PATH"] = str(p.warehouse.resolve()).replace("\\", "/")
    env.setdefault("DBT_PROFILES_DIR", str(config.REPO_ROOT / "dbt"))
    p.warehouse.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt") or "dbt"
    return subprocess.call([exe, *dbt_args], cwd=config.REPO_ROOT / "dbt", env=env)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nfip", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("extract-bulk", help="stream OpenFEMA bulk parquet files + CPI to data/raw")
    b.add_argument("--datasets", nargs="+", default=["claims", "policies", "multiple_loss"], choices=list(config.BULK_FILENAMES))
    b.add_argument("--skip-cpi", action="store_true")

    i = sub.add_parser("extract-increment", help="re-pull recent loss years from the OpenFEMA REST API")
    i.add_argument("--lookback", type=int, default=2, help="prior loss years to refresh (default 2)")
    i.add_argument("--years", type=int, nargs="*", help="explicit loss years (overrides --lookback)")
    i.add_argument("--min-interval", type=float, default=1.0, help="seconds between API requests")

    d = sub.add_parser("dbt", help="run dbt with repo-relative paths resolved (e.g. `nfip dbt build`)")
    d.add_argument("dbt_args", nargs=argparse.REMAINDER)

    sub.add_parser("quality", help="write docs/DATA_QUALITY.md from the warehouse")
    sub.add_parser("analyze", help="build figures + results tables from the marts")
    sub.add_parser("model", help="fit and validate the severity model")
    sub.add_parser("export-powerbi", help="write the Power BI star schema (parquet + csv)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = config.paths()

    if args.cmd == "extract-bulk":
        extract.extract_bulk(args.datasets, p.raw)
        if not args.skip_cpi:
            extract.download_cpi(p.raw)
    elif args.cmd == "extract-increment":
        years = args.years or extract.rolling_loss_years(args.lookback)
        client = extract.PoliteSession(min_interval_s=args.min_interval)
        extract.extract_claims_increment(years, p.raw, client=client)
    elif args.cmd == "dbt":
        raise SystemExit(run_dbt(args.dbt_args, p))
    elif args.cmd == "quality":
        from . import quality

        quality.main()
    elif args.cmd == "analyze":
        from . import analysis

        analysis.main()
    elif args.cmd == "model":
        from . import model

        model.main()
    elif args.cmd == "export-powerbi":
        from . import export

        export.main()


if __name__ == "__main__":  # pragma: no cover
    main()
