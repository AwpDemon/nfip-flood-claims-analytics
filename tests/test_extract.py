import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

from nfip import extract

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, chunks=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self._chunks = chunks or []

    def json(self):
        return self._payload

    def iter_content(self, chunk_size):
        yield from self._chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def __call__(self):
        return self.now


def make_client(responses, **kw):
    clock = FakeClock()
    sess = FakeSession(responses)
    client = extract.PoliteSession(session=sess, sleep=clock.sleep, clock=clock, **kw)
    return client, sess, clock


def test_user_agent_is_descriptive():
    client, sess, _ = make_client([])
    assert "AwpDemon" in sess.headers["User-Agent"]


def test_throttle_enforces_min_interval():
    client, _, clock = make_client([FakeResponse(), FakeResponse(), FakeResponse()], min_interval_s=1.5)
    for _ in range(3):
        client.get("http://x")
    assert clock.sleeps == [1.5, 1.5]
    assert client.request_count == 3


def test_retries_on_429_honouring_retry_after_then_succeeds():
    client, sess, clock = make_client(
        [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(503), FakeResponse(200, payload={"ok": 1})],
        min_interval_s=0,
        backoff_base_s=2,
    )
    resp = client.get("http://x")
    assert resp.json() == {"ok": 1}
    assert 7.0 in clock.sleeps and 4.0 in clock.sleeps  # Retry-After, then 2 * 2**1 backoff
    assert len(sess.calls) == 3


def test_gives_up_after_max_retries():
    client, _, _ = make_client([FakeResponse(500)] * 3, min_interval_s=0, max_retries=2)
    with pytest.raises(requests.HTTPError):
        client.get("http://x")


def test_iter_claim_pages_pages_until_count_reached():
    pages = [
        FakeResponse(payload={"metadata": {"count": 5}, "NfipClaims": [{"id": 1}, {"id": 2}]}),
        FakeResponse(payload={"metadata": {}, "NfipClaims": [{"id": 3}, {"id": 4}]}),
        FakeResponse(payload={"metadata": {}, "NfipClaims": [{"id": 5}]}),
    ]
    client, sess, _ = make_client(pages, min_interval_s=0)
    got = [r["id"] for page in extract.iter_claim_pages(client, 2024, page_size=2) for r in page]
    assert got == [1, 2, 3, 4, 5]
    params = [c[1]["params"] for c in sess.calls]
    assert [p["$skip"] for p in params] == [0, 2, 4]
    assert params[0]["$filter"] == "yearOfLoss eq 2024" and params[0]["$orderby"] == "id"
    assert "$count" in params[0] and "$count" not in params[1]


def test_conform_casts_api_json_to_bulk_schema():
    schema = pa.schema(
        [
            ("id", pa.int64()),
            ("dateOfLoss", pa.date32()),
            ("asOfDate", pa.timestamp("us", tz="UTC")),
            ("amountPaidOnBuildingClaim", pa.decimal128(12, 2)),
            ("state", pa.string()),
            ("missingCol", pa.int16()),
        ]
    )
    records = [
        {
            "id": 5,
            "dateOfLoss": "2016-10-08T00:00:00.000Z",
            "asOfDate": "2026-09-08T00:00:00.000Z",
            "amountPaidOnBuildingClaim": 95649.74,
            "state": "VA",
        }
    ]
    t = extract.conform(records, schema)
    assert t.schema == schema
    row = t.to_pylist()[0]
    assert row["dateOfLoss"] == dt.date(2016, 10, 8)
    assert row["amountPaidOnBuildingClaim"] == Decimal("95649.74")
    assert row["missingCol"] is None


def test_conform_against_real_claims_schema():
    schema = extract.claims_reference_schema(FIXTURE_RAW)
    t = extract.conform([{"id": 1, "dateOfLoss": "2024-09-27T00:00:00.000Z", "state": "GA", "yearOfLoss": 2024}], schema)
    assert t.num_rows == 1 and t.schema == schema


def test_download_file_streams_and_hashes(tmp_path):
    chunks = [b"abc", b"def"]
    client, _, _ = make_client([FakeResponse(chunks=chunks, headers={"Last-Modified": "x"})], min_interval_s=0)
    entry = extract.download_file(client, "http://x/file.parquet", tmp_path / "f.parquet")
    assert (tmp_path / "f.parquet").read_bytes() == b"abcdef"
    assert entry["sha256"] == hashlib.sha256(b"abcdef").hexdigest()
    assert entry["bytes"] == 6 and entry["last_modified"] == "x"
    assert not (tmp_path / "f.parquet.part").exists()


def test_extract_claims_increment_writes_partition_and_state(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    pq.write_table(pq.read_table(FIXTURE_RAW / "NfipClaimsV3.parquet").slice(0, 1), raw / "NfipClaimsV3.parquet")
    pages = [
        FakeResponse(
            payload={
                "metadata": {"count": 2},
                "NfipClaims": [
                    {"id": 10, "yearOfLoss": 2025, "state": "TX", "dateOfLoss": "2025-06-01T00:00:00.000Z"},
                    {"id": 11, "yearOfLoss": 2025, "state": "TX", "dateOfLoss": "2025-06-02T00:00:00.000Z"},
                ],
            }
        )
    ]
    client, _, _ = make_client(pages, min_interval_s=0)
    counts = extract.extract_claims_increment([2025], raw, client=client, page_size=10)
    assert counts == {2025: 2}
    t = pq.read_table(raw / "claims_api" / "loss_year=2025.parquet")
    assert t.column("id").to_pylist() == [10, 11]
    state = json.loads((raw / "claims_api" / "_state.json").read_text())
    assert state["runs"][-1]["rows"] == {"2025": 2}


def test_rolling_loss_years():
    assert extract.rolling_loss_years(2, today=dt.date(2026, 10, 10)) == [2024, 2025, 2026]
