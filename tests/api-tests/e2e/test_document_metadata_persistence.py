import json
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
import psycopg2
import pytest
from kafka import KafkaProducer
from psycopg2.errors import UndefinedTable

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
INGESTION_TOPIC = os.getenv("KAFKA_TOPIC", "rag.documents.ingestion.v1")
SYNC_STATUS_TOPIC = os.getenv("KAFKA_SYNC_STATUS_TOPIC", "rag.sync.status.v1")

CORE_BASE_URL = os.getenv("CORE_BASE_URL", "http://localhost:8080")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "copilot_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "example")

VALID_SYNC_STATUSES = {
    "IN_PROGRESS",
    "COMPLETED",
    "FAILED_FETCH",
    "FAILED_PARSE",
    "FAILED_STORAGE",
}


def _is_service_up(base_url: str, path: str = "/actuator/health") -> bool:
    try:
        httpx.get(f"{base_url}{path}", timeout=1.5)
        return True
    except httpx.RequestError:
        return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_utc_epoch_seconds(value: datetime | str) -> int:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def validate_metadata_record(
    row: tuple | None,
    expected_id: str,
    expected_title: str,
    expected_url: str,
    expected_source_system: str = "sharepoint",
    expected_last_updated: str | datetime | None = None,
) -> None:
    """Validates a core.document_metadata row returned from PostgreSQL."""
    assert row is not None, f"Document {expected_id} was not found in DB"
    doc_id, source_system, title, url, last_updated, last_sync_at = row
    assert doc_id == expected_id, f"Expected document_id '{expected_id}', got '{doc_id}'"
    assert (
        source_system == expected_source_system
    ), f"Expected source_system '{expected_source_system}', got '{source_system}'"
    assert title == expected_title, f"Expected title '{expected_title}', got '{title}'"
    assert url == expected_url, f"Expected url '{expected_url}', got '{url}'"
    assert last_updated is not None, "Expected last_updated timestamp to be persisted"
    if expected_last_updated is not None:
        assert _to_utc_epoch_seconds(last_updated) == _to_utc_epoch_seconds(
            expected_last_updated
        ), f"Expected last_updated '{expected_last_updated}', got '{last_updated}'"
    assert last_sync_at is not None, "Expected last_sync_at timestamp to be persisted"


def _build_document_upsert_event(
    *,
    sync_id: str,
    document_id: str,
    action: str,
    title: str,
    url: str,
    raw_content: str,
    source_system: str = "sharepoint",
    last_updated: str | None = None,
    allowed_groups: list[str] | None = None,
) -> dict:
    return {
        "sync_id": sync_id,
        "document_id": document_id,
        "action": action,
        "source_system": source_system,
        "title": title,
        "url": url,
        "raw_content": raw_content,
        "last_updated": last_updated or _utc_now_iso(),
        "acl": {"allowed_groups": allowed_groups or ["Engineering", "All Staff"]},
    }


def _build_sync_status_event(
    *,
    sync_id: str,
    document_id: str,
    status: str,
    error_message: str | None = None,
    timestamp: int | None = None,
) -> dict:
    return {
        "sync_id": sync_id,
        "document_id": document_id,
        "status": status,
        "error_message": error_message,
        "timestamp": timestamp if timestamp is not None else int(time.time()),
    }


def _publish_document_upsert(
    producer: KafkaProducer,
    validate_kafka_schema,
    *,
    sync_id: str,
    document_id: str,
    action: str,
    title: str,
    url: str,
    raw_content: str,
    source_system: str = "sharepoint",
    last_updated: str | None = None,
    allowed_groups: list[str] | None = None,
) -> dict:
    event = _build_document_upsert_event(
        sync_id=sync_id,
        document_id=document_id,
        action=action,
        title=title,
        url=url,
        raw_content=raw_content,
        source_system=source_system,
        last_updated=last_updated,
        allowed_groups=allowed_groups,
    )
    validate_kafka_schema(event, "DocumentUpsertEvent")
    producer.send(INGESTION_TOPIC, key=document_id.encode("utf-8"), value=event).get(timeout=10)
    return event


def _publish_sync_status(
    producer: KafkaProducer,
    validate_kafka_schema,
    *,
    sync_id: str,
    document_id: str,
    status: str,
    error_message: str | None = None,
    timestamp: int | None = None,
) -> dict:
    event = _build_sync_status_event(
        sync_id=sync_id,
        document_id=document_id,
        status=status,
        error_message=error_message,
        timestamp=timestamp,
    )
    validate_kafka_schema(event, "KafkaSyncStatusEvent")
    producer.send(SYNC_STATUS_TOPIC, key=document_id.encode("utf-8"), value=event).get(timeout=10)
    return event


@pytest.fixture(scope="module")
def require_java_service():
    if not _is_service_up(CORE_BASE_URL, "/actuator/health"):
        pytest.skip(f"Skipped E2E tests: java-service is not reachable at {CORE_BASE_URL}")


@pytest.fixture(scope="module")
def kafka_producer(require_java_service):
    try:
        producer = KafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=1,
        )
        yield producer
        producer.close()
    except Exception as exc:
        pytest.skip(f"Skipped E2E tests: Kafka not available ({exc})")


@pytest.fixture(scope="module")
def db_connection(require_java_service):
    """
    Connects to the real PostgreSQL instance and verifies that the actual
    Flyway-managed core schema tables exist (never creates test tables or enums).
    """
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            connect_timeout=3,
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM core.document_metadata LIMIT 1;")
            cur.execute("SELECT status::text FROM core.document_sync_runs LIMIT 1;")
        yield conn
        conn.close()
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Skipped E2E tests: PostgreSQL not available ({exc})")
    except UndefinedTable as exc:
        pytest.skip(f"Skipped E2E tests: Required schema/table missing ({exc})")


@pytest.fixture
def tracked_docs(db_connection):
    """Tracks created document IDs and cleans up test rows after each test."""
    created_ids: list[str] = []
    yield created_ids
    if created_ids:
        with db_connection.cursor() as cur:
            cur.execute(
                "DELETE FROM core.document_sync_runs WHERE document_id = ANY(%s);",
                (created_ids,),
            )
            cur.execute(
                "DELETE FROM core.document_metadata WHERE document_id = ANY(%s);",
                (created_ids,),
            )
            cur.execute("SELECT to_regclass('rag.document');")
            if cur.fetchone()[0] is not None:
                cur.execute(
                    "DELETE FROM rag.document WHERE document_id = ANY(%s);",
                    (created_ids,),
                )


def _fetch_document_metadata(conn, document_id: str) -> tuple | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, source_system, title, url, last_updated, last_sync_at
            FROM core.document_metadata
            WHERE document_id = %s;
            """,
            (document_id,),
        )
        return cur.fetchone()


def _fetch_sync_runs_for_doc(conn, document_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, document_id, status::text, error_message, started_at, finished_at
            FROM core.document_sync_runs
            WHERE document_id = %s
            ORDER BY started_at ASC;
            """,
            (document_id,),
        )
        return cur.fetchall()


def _fetch_sync_run_by_id(conn, sync_id: str) -> tuple | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, document_id, status::text, error_message, started_at, finished_at
            FROM core.document_sync_runs
            WHERE id = %s::uuid;
            """,
            (sync_id,),
        )
        return cur.fetchone()


def _wait_for_document_metadata(
    conn,
    document_id: str,
    *,
    expected_title: str | None = None,
    timeout: float = 20.0,
) -> tuple:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = _fetch_document_metadata(conn, document_id)
        if row is not None and (expected_title is None or row[2] == expected_title):
            return row
        time.sleep(0.4)
    raise AssertionError(
        f"Timed out after {timeout}s waiting for core.document_metadata row for '{document_id}'"
        + (f" with title '{expected_title}'" if expected_title else "")
    )


def _wait_for_sync_run(
    conn,
    sync_id: str,
    document_id: str,
    *,
    expected_status: str | None = None,
    timeout: float = 20.0,
) -> tuple:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = _fetch_sync_run_by_id(conn, sync_id)
        if row is not None and (expected_status is None or row[2] == expected_status):
            return row
        time.sleep(0.4)
    raise AssertionError(
        f"Timed out after {timeout}s waiting for core.document_sync_runs row for "
        f"document_id='{document_id}', sync_id='{sync_id}'"
        + (f" with status='{expected_status}'" if expected_status else "")
    )


def _publish_and_wait_sync_status(
    producer: KafkaProducer,
    conn,
    validate_kafka_schema,
    *,
    sync_id: str,
    document_id: str,
    status: str,
    error_message: str | None = None,
) -> tuple:
    """
    Publishes a KafkaSyncStatusEvent to rag.sync.status.v1 and waits for java-service
    to persist the status in core.document_sync_runs. If java-service requires an
    existing sync run row initiated by DocumentUpsertEvent, creates the run first
    and re-publishes the status event.
    """
    _publish_sync_status(
        producer,
        validate_kafka_schema,
        sync_id=sync_id,
        document_id=document_id,
        status=status,
        error_message=error_message,
    )
    deadline = time.time() + 6.0
    while time.time() < deadline:
        row = _fetch_sync_run_by_id(conn, sync_id)
        if row is not None and row[2] == status:
            return row
        time.sleep(0.3)

    if _fetch_sync_run_by_id(conn, sync_id) is None:
        _publish_document_upsert(
            producer,
            validate_kafka_schema,
            sync_id=sync_id,
            document_id=document_id,
            action="create",
            title=f"Status Probe {document_id}",
            url=f"https://company.sharepoint.com/docs/{document_id}",
            raw_content=f"Sync status probe content for {document_id}.",
        )
        _wait_for_sync_run(conn, sync_id, document_id, timeout=15.0)
        settle_deadline = time.time() + 3.0
        while time.time() < settle_deadline:
            row = _fetch_sync_run_by_id(conn, sync_id)
            if row is not None and row[5] is not None:
                break
            time.sleep(0.3)
        _publish_sync_status(
            producer,
            validate_kafka_schema,
            sync_id=sync_id,
            document_id=document_id,
            status=status,
            error_message=error_message,
        )

    return _wait_for_sync_run(
        conn,
        sync_id,
        document_id,
        expected_status=status,
        timeout=15.0,
    )


def test_sync_statuses_match_shared_contract(shared_spec):
    """Verifies local status constant matches SyncStatus enum in contracts/shared.yaml."""
    contract_statuses = set(shared_spec["components"]["schemas"]["SyncStatus"]["enum"])
    assert VALID_SYNC_STATUSES == contract_statuses


def test_kafka_event_builders_match_contract(validate_kafka_schema):
    """Validates ingestion and sync status event payloads against contracts/openapi-kafka.yaml."""
    doc_id = "doc-contract-100"
    sync_id = str(uuid.uuid4())

    for action in ("create", "update"):
        upsert_event = _build_document_upsert_event(
            sync_id=sync_id,
            document_id=doc_id,
            action=action,
            title="Contract Test Doc",
            url=f"https://company.sharepoint.com/docs/{doc_id}",
            raw_content="Sample plain text content",
        )
        validate_kafka_schema(upsert_event, "DocumentUpsertEvent")
        validate_kafka_schema(upsert_event, "KafkaNewFileDataEvent")

    for status, error_msg in (
        ("IN_PROGRESS", None),
        ("COMPLETED", None),
        ("FAILED_FETCH", "403 Forbidden"),
        ("FAILED_PARSE", "Corrupted PDF"),
        ("FAILED_STORAGE", "DB write timeout"),
    ):
        status_event = _build_sync_status_event(
            sync_id=sync_id,
            document_id=doc_id,
            status=status,
            error_message=error_msg,
        )
        validate_kafka_schema(status_event, "KafkaSyncStatusEvent")


def test_metadata_validator_detects_mismatched_fields():
    """Unit check for metadata assertion helper (runs in CI contract suite)."""
    ts = datetime(2026, 11, 16, 0, 0, 0, tzinfo=timezone.utc)
    sample_row = ("doc-100", "sharepoint", "Actual Title", "https://company.local/doc-100", ts, ts)
    validate_metadata_record(
        sample_row,
        "doc-100",
        "Actual Title",
        "https://company.local/doc-100",
        expected_source_system="sharepoint",
        expected_last_updated=ts,
    )

    with pytest.raises(AssertionError, match="was not found in DB"):
        validate_metadata_record(None, "doc-100", "Actual Title", "https://company.local/doc-100")

    with pytest.raises(AssertionError, match="Expected title"):
        validate_metadata_record(sample_row, "doc-100", "Wrong Title", "https://company.local/doc-100")

    with pytest.raises(AssertionError, match="Expected url"):
        validate_metadata_record(sample_row, "doc-100", "Actual Title", "https://company.local/other")

    with pytest.raises(AssertionError, match="Expected last_updated"):
        validate_metadata_record(
            sample_row,
            "doc-100",
            "Actual Title",
            "https://company.local/doc-100",
            expected_last_updated="2026-11-16T01:00:00Z",
        )


@pytest.mark.e2e
def test_new_document_persists_metadata_and_status(
    kafka_producer,
    db_connection,
    validate_kafka_schema,
    tracked_docs,
):
    """
    AC 1: Publishing a DocumentUpsertEvent (create) to Kafka causes java-service to
    persist the document's metadata in core.document_metadata and record a sync run
    in core.document_sync_runs.
    """
    sync_id = str(uuid.uuid4())
    doc_id = f"scrum115-new-{uuid.uuid4().hex[:12]}"
    tracked_docs.append(doc_id)
    title = "Employee Onboarding Guide"
    url = f"https://company.sharepoint.com/docs/{doc_id}"
    last_updated = "2026-11-23T10:00:00Z"

    _publish_document_upsert(
        kafka_producer,
        validate_kafka_schema,
        sync_id=sync_id,
        document_id=doc_id,
        action="create",
        source_system="sharepoint",
        title=title,
        url=url,
        raw_content=f"Employee onboarding policy and benefits overview for {doc_id}.",
        last_updated=last_updated,
        allowed_groups=["All Staff"],
    )

    metadata_row = _wait_for_document_metadata(db_connection, doc_id)
    validate_metadata_record(
        metadata_row,
        expected_id=doc_id,
        expected_title=title,
        expected_url=url,
        expected_source_system="sharepoint",
        expected_last_updated=last_updated,
    )

    run_row = _wait_for_sync_run(db_connection, sync_id, doc_id)
    run_id, run_doc_id, status, _, started_at, _ = run_row
    assert run_id == sync_id, f"Expected sync_run id '{sync_id}', got '{run_id}'"
    assert run_doc_id == doc_id
    assert status in VALID_SYNC_STATUSES
    assert started_at is not None


@pytest.mark.e2e
def test_reingesting_same_source_id_updates_without_duplicate(
    kafka_producer,
    db_connection,
    validate_kafka_schema,
    tracked_docs,
):
    """
    AC 2: Re-ingesting the same document_id (action='update') updates the existing
    record in core.document_metadata without creating a duplicate row.
    """
    doc_id = f"scrum115-upsert-{uuid.uuid4().hex[:12]}"
    tracked_docs.append(doc_id)

    sync_id_v1 = str(uuid.uuid4())
    url_v1 = f"https://company.sharepoint.com/docs/{doc_id}/v1"
    ts_v1 = "2026-11-23T10:00:00Z"

    _publish_document_upsert(
        kafka_producer,
        validate_kafka_schema,
        sync_id=sync_id_v1,
        document_id=doc_id,
        action="create",
        title="Security Policy v1",
        url=url_v1,
        raw_content=f"Security policy v1 guidelines for {doc_id}.",
        last_updated=ts_v1,
    )
    first_row = _wait_for_document_metadata(db_connection, doc_id, expected_title="Security Policy v1")
    first_last_sync_at = first_row[5]

    sync_id_v2 = str(uuid.uuid4())
    url_v2 = f"https://company.sharepoint.com/docs/{doc_id}/v2"
    ts_v2 = "2026-11-23T11:00:00Z"

    _publish_document_upsert(
        kafka_producer,
        validate_kafka_schema,
        sync_id=sync_id_v2,
        document_id=doc_id,
        action="update",
        title="Security Policy v2",
        url=url_v2,
        raw_content=f"Security policy v2 updated guidelines for {doc_id}.",
        last_updated=ts_v2,
    )
    updated_row = _wait_for_document_metadata(db_connection, doc_id, expected_title="Security Policy v2")

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM core.document_metadata WHERE document_id = %s;",
            (doc_id,),
        )
        count = cur.fetchone()[0]

    assert count == 1, f"Expected exactly 1 record for {doc_id}, found {count}"
    validate_metadata_record(
        updated_row,
        expected_id=doc_id,
        expected_title="Security Policy v2",
        expected_url=url_v2,
        expected_source_system="sharepoint",
        expected_last_updated=ts_v2,
    )
    assert updated_row[5] >= first_last_sync_at

    latest_run = _wait_for_sync_run(db_connection, sync_id_v2, doc_id)
    assert latest_run[0] == sync_id_v2
    runs = _fetch_sync_runs_for_doc(db_connection, doc_id)
    assert {r[0] for r in runs} >= {sync_id_v1, sync_id_v2}


@pytest.mark.e2e
def test_success_and_failure_statuses_are_persisted_and_queryable(
    kafka_producer,
    db_connection,
    validate_kafka_schema,
    tracked_docs,
):
    """
    AC 3: Success and failure statuses published on rag.sync.status.v1 are persisted
    by java-service in core.document_sync_runs and can be queried.
    """
    prefix = f"scrum115-status-{uuid.uuid4().hex[:8]}"
    cases = [
        (f"{prefix}-ok", str(uuid.uuid4()), "COMPLETED", None),
        (f"{prefix}-fail-fetch", str(uuid.uuid4()), "FAILED_FETCH", "403 Forbidden from Graph API"),
        (f"{prefix}-fail-parse", str(uuid.uuid4()), "FAILED_PARSE", "Corrupted PDF header"),
        (f"{prefix}-fail-storage", str(uuid.uuid4()), "FAILED_STORAGE", "pgvector write timeout"),
    ]
    tracked_docs.extend(doc_id for doc_id, _, _, _ in cases)

    for doc_id, sync_id, status, error_message in cases:
        _publish_and_wait_sync_status(
            kafka_producer,
            db_connection,
            validate_kafka_schema,
            sync_id=sync_id,
            document_id=doc_id,
            status=status,
            error_message=error_message,
        )

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, status::text, error_message, finished_at
            FROM core.document_sync_runs
            WHERE document_id LIKE %s AND status = 'COMPLETED';
            """,
            (f"{prefix}%",),
        )
        completed_rows = cur.fetchall()

        cur.execute(
            """
            SELECT document_id, status::text, error_message, finished_at
            FROM core.document_sync_runs
            WHERE document_id LIKE %s
              AND status IN ('FAILED_FETCH', 'FAILED_PARSE', 'FAILED_STORAGE')
            ORDER BY document_id;
            """,
            (f"{prefix}%",),
        )
        failed_rows = cur.fetchall()

    assert len(completed_rows) == 1
    assert completed_rows[0][:3] == (f"{prefix}-ok", "COMPLETED", None)
    assert completed_rows[0][3] is not None

    assert len(failed_rows) == 3
    failed_by_doc = {row[0]: (row[1], row[2], row[3]) for row in failed_rows}
    for doc_id, _, expected_status, expected_err in cases[1:]:
        actual_status, actual_err, finished_at = failed_by_doc[doc_id]
        assert actual_status == expected_status
        assert actual_err == expected_err
        assert finished_at is not None
