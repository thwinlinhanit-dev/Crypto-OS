"""Spec section 4.4: manifest content, dataset_version, storage round trip.

The frozen golden here is the reproducibility promise of AC1/AC2: same inputs,
identical dataset_version (PD-6). Changing it needs a PR note (section 14.1).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from apps.api.settings import get_settings
from engine.data.manifest import (
    BAR_SEMANTICS_VERSION,
    INSTRUMENT_METADATA_VERSION,
    TIMEZONE_POLICY_VERSION,
    DatasetManifest,
    SourceFile,
    build_manifest,
    get_manifest,
    save_manifest,
)
from engine.versions import DATASET_SCHEMA_VERSION

#: Frozen golden (section 14.1): identical inputs must always yield this version.
GOLDEN_DATASET_VERSION = "12ace598e83cfb160e3cb5bc8a5926d02279cdb93c8a4f0880689800554ee732"

INSTRUMENT_A = "11111111-1111-1111-1111-111111111111"
INSTRUMENT_B = "22222222-2222-2222-2222-222222222222"

FILE_A = SourceFile(
    path="normalized/venue=BINANCE/market_type=SPOT/symbol=BTCUSDT/tf=1D/year=2024/"
    "part-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.parquet",
    content_sha256="a" * 64,
    rows=366,
)
FILE_B = SourceFile(
    path="normalized/venue=BINANCE/market_type=SPOT/symbol=ETHUSDT/tf=1D/year=2024/"
    "part-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.parquet",
    content_sha256="b" * 64,
    rows=365,
)


def _manifest_kwargs() -> dict[str, object]:
    return {
        "venue": "BINANCE",
        "market_type": "SPOT",
        "instruments": (INSTRUMENT_A, INSTRUMENT_B),
        "timeframes": ("1D", "4H", "1H"),
        "start_time": datetime(2024, 1, 1, tzinfo=UTC),
        "end_time": datetime(2025, 1, 1, tzinfo=UTC),
        "source_files": (FILE_A, FILE_B),
        "row_counts": {f"{INSTRUMENT_A}:1D": 366, f"{INSTRUMENT_B}:1D": 365},
        "missing_ranges": [],
        "quality_summary": {"state": "GREEN", "completeness": 1.0},
    }


def _build(**overrides: object) -> DatasetManifest:
    payload = _manifest_kwargs()
    payload.update(overrides)
    return build_manifest(**payload)  # type: ignore[arg-type]


def test_dataset_version_matches_the_frozen_golden() -> None:
    assert _build().dataset_version == GOLDEN_DATASET_VERSION


def test_source_file_order_does_not_change_the_version() -> None:
    reordered = _build(source_files=(FILE_B, FILE_A))
    assert reordered.dataset_version == _build().dataset_version


def test_version_is_sensitive_to_every_field_it_claims_to_cover() -> None:
    baseline = _build().dataset_version
    variants = {
        "dataset_schema_version": _build(dataset_schema_version="2").dataset_version,
        "bar_semantics_version": _build(bar_semantics_version="closed-bars-utc-v2").dataset_version,
        "timezone_policy_version": _build(timezone_policy_version="utc-v2").dataset_version,
        "instrument_metadata_version": _build(
            instrument_metadata_version="binance-spot-exchangeinfo-v2"
        ).dataset_version,
        "path": _build(source_files=(SourceFile("normalized/other.parquet", FILE_A.content_sha256, FILE_A.rows), FILE_B)).dataset_version,
        "content": _build(source_files=(SourceFile(FILE_A.path, "c" * 64, FILE_A.rows), FILE_B)).dataset_version,
        "rows": _build(source_files=(SourceFile(FILE_A.path, FILE_A.content_sha256, 1), FILE_B)).dataset_version,
    }
    for name, value in variants.items():
        assert value != baseline, f"{name} does not change dataset_version"
    # Fields the spec does NOT hash must not move the version (scope/quality are
    # assessments of the same bytes and are stored, not versioned).
    assert _build(quality_summary={"state": "RED"}).dataset_version == baseline
    assert _build(row_counts={"x": 1}).dataset_version == baseline


def test_version_constants_are_the_spec_values() -> None:
    manifest = _build()
    assert manifest.dataset_schema_version == DATASET_SCHEMA_VERSION
    assert manifest.bar_semantics_version == "closed-bars-utc-v1"
    assert manifest.timezone_policy_version == "utc-v1"
    assert BAR_SEMANTICS_VERSION == "closed-bars-utc-v1"
    assert TIMEZONE_POLICY_VERSION == "utc-v1"
    assert manifest.instrument_metadata_version == INSTRUMENT_METADATA_VERSION


def test_build_manifest_stringifies_instruments_and_defaults_created_at() -> None:
    from uuid import UUID

    manifest = _build(instruments=(UUID(INSTRUMENT_A), UUID(INSTRUMENT_B)))
    assert manifest.instruments == (INSTRUMENT_A, INSTRUMENT_B)
    before = datetime.now(UTC) - timedelta(seconds=5)
    assert before <= manifest.created_at <= datetime.now(UTC) + timedelta(seconds=5)
    assert manifest.dataset_version == _build().dataset_version


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def tx(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    conn.begin()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_manifest_round_trips_through_postgres(tx: Connection) -> None:
    manifest = _build()
    save_manifest(tx, manifest)
    assert get_manifest(tx, manifest.dataset_version) == manifest


def test_saving_the_same_version_again_is_idempotent(tx: Connection) -> None:
    first = _build()
    save_manifest(tx, first)
    later = _build(
        quality_summary={"state": "YELLOW", "completeness": 0.99},
        created_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert later.dataset_version == first.dataset_version
    save_manifest(tx, later)

    rows = tx.execute(
        text("SELECT count(*) FROM market.dataset_manifest WHERE dataset_version = :v"),
        {"v": first.dataset_version},
    ).scalar_one()
    assert rows == 1

    reloaded = get_manifest(tx, first.dataset_version)
    assert reloaded is not None
    # The assessment may be recomputed; the registration time of a dataset_version
    # and everything hashed into its identity stay put.
    assert reloaded.quality_summary == {"state": "YELLOW", "completeness": 0.99}
    assert reloaded.created_at == first.created_at


def test_unknown_dataset_version_is_absent_not_guessed(tx: Connection) -> None:
    assert get_manifest(tx, "no-such-version") is None
