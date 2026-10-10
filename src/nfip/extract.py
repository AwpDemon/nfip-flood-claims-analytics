"""Extract layer: bulk downloads + incremental OpenFEMA API pulls + CPI.

Design notes (see docs/INTERVIEW_GUIDE.md, "Extract"):

* **Bulk first.** OpenFEMA publishes each NFIP dataset as a single Parquet file.
  Downloading it is far cheaper (for FEMA and for us) than paging 2.7M claims
  through the REST API, so the initial load streams the bulk file to disk in
  8 MB chunks (never held in memory) and records a SHA-256 manifest.
* **Incremental via the API.** OpenFEMA's ``asOfDate`` is stamped on *every*
  record at each refresh, so it is not a change-data-capture watermark. Claims do
  keep developing (re-opened, supplemental payments) for a few years after the
  loss, so the incremental strategy is a *rolling loss-year window*: re-pull the
  last N loss years, overwrite those partitions, and let dbt de-duplicate on the
  claim ``id`` keeping the newest version.
* **Polite client.** Descriptive User-Agent, a minimum interval between requests,
  bounded retries with exponential backoff that honour ``Retry-After``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from . import config

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class PoliteSession:
    """``requests`` wrapper that rate-limits and retries."""

    def __init__(
        self,
        min_interval_s: float = 1.0,
        max_retries: int = 5,
        backoff_base_s: float = 2.0,
        session: requests.Session | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Encoding": "gzip"})
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.request_count = 0

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval_s - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", 120)
        for attempt in range(self.max_retries + 1):
            self._throttle()
            self.request_count += 1
            try:
                resp = self.session.get(url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise
                delay = self.backoff_base_s * 2**attempt
                log.warning("network error %s; retrying in %.1fs", exc, delay)
                self._sleep(delay)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff_base_s * 2**attempt
                log.warning("HTTP %s from %s; retrying in %.1fs", resp.status_code, url, delay)
                self._sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")  # pragma: no cover


# --------------------------------------------------------------------------- bulk


def _manifest_path(raw_dir: Path) -> Path:
    return raw_dir / "manifest.json"


def _update_manifest(raw_dir: Path, name: str, entry: dict[str, Any]) -> None:
    path = _manifest_path(raw_dir)
    manifest = json.loads(path.read_text()) if path.exists() else {}
    manifest[name] = entry
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def download_file(client: PoliteSession, url: str, dest: Path, chunk_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Stream ``url`` to ``dest`` atomically; return a manifest entry."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    sha = hashlib.sha256()
    n_bytes = 0
    resp = client.get(url, stream=True)
    with tmp.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=chunk_bytes):
            fh.write(chunk)
            sha.update(chunk)
            n_bytes += len(chunk)
    tmp.replace(dest)
    return {
        "url": url,
        "file": dest.name,
        "bytes": n_bytes,
        "sha256": sha.hexdigest(),
        "last_modified": resp.headers.get("Last-Modified"),
        "retrieved_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }


def extract_bulk(datasets: list[str], raw_dir: Path, client: PoliteSession | None = None) -> None:
    client = client or PoliteSession()
    for name in datasets:
        dest = raw_dir / config.BULK_FILENAMES[name]
        log.info("downloading %s -> %s", name, dest)
        entry = download_file(client, config.OPENFEMA_BULK[name], dest)
        _update_manifest(raw_dir, name, entry)
        log.info("%s: %.1f MB sha256=%s", name, entry["bytes"] / 1e6, entry["sha256"][:12])
    ensure_empty_increment(raw_dir)


def download_cpi(raw_dir: Path, client: PoliteSession | None = None) -> Path:
    client = client or PoliteSession()
    dest = raw_dir / "cpi_u_monthly.csv"
    entry = download_file(client, config.FRED_CPI_URL, dest)
    _update_manifest(raw_dir, "cpi", entry)
    return dest


# --------------------------------------------------------------------------- API increments


def claims_reference_schema(raw_dir: Path) -> pa.Schema:
    """Schema of the bulk claims file; API pages are conformed to it."""
    return pq.read_schema(raw_dir / config.BULK_FILENAMES["claims"])


def ensure_empty_increment(raw_dir: Path) -> None:
    """Write a zero-row increment so the dbt source glob always resolves."""
    inc = raw_dir / "claims_api"
    inc.mkdir(parents=True, exist_ok=True)
    schema = claims_reference_schema(raw_dir)
    pq.write_table(schema.empty_table(), inc / "_empty.parquet")


def iter_claim_pages(client: PoliteSession, loss_year: int, page_size: int = config.OPENFEMA_PAGE_SIZE) -> Iterator[list[dict]]:
    """Yield pages of claim records for one loss year, ordered by id."""
    skip = 0
    total: int | None = None
    while True:
        params = {
            "$filter": f"yearOfLoss eq {loss_year}",
            "$orderby": "id",
            "$top": page_size,
            "$skip": skip,
            "$format": "json",
        }
        if total is None:
            params["$count"] = "true"
        payload = client.get(config.OPENFEMA_CLAIMS_API, params=params).json()
        if total is None:
            total = int(payload.get("metadata", {}).get("count", 0))
            log.info("loss year %s: %s records reported by API", loss_year, total)
        rows = payload.get("NfipClaims", [])
        if not rows:
            return
        yield rows
        skip += len(rows)
        if len(rows) < page_size or (total and skip >= total):
            return


def conform(records: list[dict], schema: pa.Schema) -> pa.Table:
    """Cast API JSON records to the bulk-file schema (dates, decimals, ints)."""
    df = pd.DataFrame.from_records(records)
    for f in schema:
        if f.name not in df.columns:
            df[f.name] = None
    df = df[[f.name for f in schema]]
    arrays = []
    for f in schema:
        col = df[f.name]
        if pa.types.is_date(f.type):
            col = pd.to_datetime(col, utc=True, errors="coerce").dt.date
        elif pa.types.is_timestamp(f.type):
            col = pd.to_datetime(col, utc=True, errors="coerce")
        elif pa.types.is_decimal(f.type):
            from decimal import Decimal

            scale = f.type.scale
            col = col.map(lambda v, s=scale: None if v is None or pd.isna(v) else round(Decimal(str(v)), s))
        arrays.append(pa.array(col.tolist(), type=f.type, from_pandas=True))
    return pa.Table.from_arrays(arrays, schema=schema)


def extract_claims_increment(
    loss_years: list[int],
    raw_dir: Path,
    client: PoliteSession | None = None,
    page_size: int = config.OPENFEMA_PAGE_SIZE,
) -> dict[int, int]:
    """Re-pull whole loss-year partitions from the API (idempotent overwrite)."""
    client = client or PoliteSession()
    schema = claims_reference_schema(raw_dir)
    out_dir = raw_dir / "claims_api"
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[int, int] = {}
    for year in loss_years:
        tables = [conform(page, schema) for page in iter_claim_pages(client, year, page_size)]
        table = pa.concat_tables(tables) if tables else schema.empty_table()
        dest = out_dir / f"loss_year={year}.parquet"
        tmp = dest.with_suffix(".parquet.part")
        pq.write_table(table, tmp)
        tmp.replace(dest)
        counts[year] = table.num_rows
        log.info("loss year %s: wrote %s rows -> %s", year, table.num_rows, dest.name)
    state_path = out_dir / "_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"runs": []}
    state["runs"].append(
        {
            "run_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "loss_years": loss_years,
            "rows": {str(k): v for k, v in counts.items()},
            "http_requests": client.request_count,
        }
    )
    state_path.write_text(json.dumps(state, indent=2))
    return counts


def rolling_loss_years(lookback: int, today: dt.date | None = None) -> list[int]:
    """Current loss year plus ``lookback`` prior years (claims still developing)."""
    year = (today or dt.date.today()).year
    return list(range(year - lookback, year + 1))
