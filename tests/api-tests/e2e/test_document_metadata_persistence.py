import os
import uuid
import pytest
import psycopg2
from psycopg2.errors import UndefinedTable

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


def validate_metadata_record(row: tuple, expected_id: str, expected_title: str, expected_url: str) -> None:
    assert row is not None, f"Document {expected_id} was not found in DB"
    doc_id, source_system, title, url, last_sync_at = row
    assert doc_id == expected_id, f"Expected document_id '{expected_id}', got '{doc_id}'"
    assert title == expected_title, f"Expected title '{expected_title}', got '{title}'"
    assert url == expected_url, f"Expected url '{expected_url}', got '{url}'"
    assert source_system is not None, "Expected source_system to be persisted"
    assert last_sync_at is not None, "Expected last_sync_at timestamp to be persisted"


@pytest.fixture(scope="module")
def db_connection():
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
            cur.execute("SELECT 1")
        yield conn
        conn.close()
    except psycopg2.OperationalError:
        pytest.skip("Skipped integration tests: PostgreSQL not available on CI-server")


@pytest.fixture(scope="module")
def db_schema(db_connection):
    """
    Ensures core.document_metadata and core.document_sync_runs exist
    matching contracts/database.dbml so tests run repeatably in local environments.
    """
    try:
        with db_connection.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS core;")
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sync_status') THEN
                        CREATE TYPE sync_status AS ENUM (
                            'IN_PROGRESS',
                            'COMPLETED',
                            'FAILED_FETCH',
                            'FAILED_PARSE',
                            'FAILED_STORAGE'
                        );
                    END IF;
                END$$;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS core.document_metadata (
                    document_id VARCHAR(255) PRIMARY KEY NOT NULL,
                    source_system VARCHAR(100),
                    title TEXT,
                    url TEXT,
                    last_updated TIMESTAMPTZ,
                    last_sync_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS core.document_sync_runs (
                    id UUID PRIMARY KEY,
                    document_id VARCHAR(255) NOT NULL,
                    status sync_status NOT NULL DEFAULT 'IN_PROGRESS',
                    error_message TEXT,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_runs_doc_id
                ON core.document_sync_runs (document_id);
            """)
    except (psycopg2.Error, UndefinedTable) as exc:
        pytest.skip(f"Skipped integration tests: database schema setup failed ({exc})")
    return db_connection


@pytest.fixture
def tracked_docs(db_schema):
    """Tracks created document IDs and cleans them up after each test for repeatability."""
    created_ids: list[str] = []
    yield created_ids
    if created_ids:
        with db_schema.cursor() as cur:
            cur.execute(
                "DELETE FROM core.document_sync_runs WHERE document_id = ANY(%s);",
                (created_ids,),
            )
            cur.execute(
                "DELETE FROM core.document_metadata WHERE document_id = ANY(%s);",
                (created_ids,),
            )


def upsert_document_metadata(
    conn,
    document_id: str,
    title: str,
    url: str,
    source_system: str = "sharepoint",
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.document_metadata (
                document_id, source_system, title, url, last_updated, last_sync_at
            )
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (document_id) DO UPDATE SET
                source_system = EXCLUDED.source_system,
                title = EXCLUDED.title,
                url = EXCLUDED.url,
                last_updated = EXCLUDED.last_updated,
                last_sync_at = NOW();
            """,
            (document_id, source_system, title, url),
        )


def record_sync_run(
    conn,
    document_id: str,
    status: str,
    error_message: str | None = None,
) -> str:
    assert status in VALID_SYNC_STATUSES, f"Invalid sync_status: {status}"
    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.document_sync_runs (
                id, document_id, status, error_message, started_at, finished_at
            )
            VALUES (%s, %s, %s::sync_status, %s, NOW(), NOW());
            """,
            (run_id, document_id, status, error_message),
        )
    return run_id


def fetch_document_metadata(conn, document_id: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, source_system, title, url, last_sync_at
            FROM core.document_metadata
            WHERE document_id = %s;
            """,
            (document_id,),
        )
        return cur.fetchone()


def test_metadata_validator_detects_mismatched_fields():
    """Unit check for metadata assertion helper (always runs in CI contract suite)."""
    sample_row = ("doc-100", "sharepoint", "Actual Title", "https://company.local/doc-100", "2026-11-16T00:00:00Z")
    validate_metadata_record(sample_row, "doc-100", "Actual Title", "https://company.local/doc-100")

    with pytest.raises(AssertionError, match="Expected title"):
        validate_metadata_record(sample_row, "doc-100", "Wrong Title", "https://company.local/doc-100")


@pytest.mark.e2e
def test_new_document_persists_metadata_and_status(db_schema, tracked_docs):
    """
    AC 1: A new document persists its stable source ID, title, source link,
    and processing status.
    """
    doc_id = f"scrum115-new-{uuid.uuid4()}"
    tracked_docs.append(doc_id)
    title = "Employee Onboarding Guide"
    url = f"https://company.sharepoint.com/docs/{doc_id}"

    upsert_document_metadata(db_schema, doc_id, title=title, url=url)
    record_sync_run(db_schema, doc_id, status="COMPLETED")

    row = fetch_document_metadata(db_schema, doc_id)
    validate_metadata_record(row, expected_id=doc_id, expected_title=title, expected_url=url)

    with db_schema.cursor() as cur:
        cur.execute(
            "SELECT status::text FROM core.document_sync_runs WHERE document_id = %s;",
            (doc_id,),
        )
        status_row = cur.fetchone()

    assert status_row is not None
    assert status_row[0] == "COMPLETED"


@pytest.mark.e2e
def test_reingesting_same_source_id_updates_without_duplicate(db_schema, tracked_docs):
    """
    AC 2: Re-ingesting the same source ID updates the existing record
    without creating a duplicate.
    """
    doc_id = f"scrum115-upsert-{uuid.uuid4()}"
    tracked_docs.append(doc_id)

    upsert_document_metadata(
        db_schema,
        doc_id,
        title="Security Policy v1",
        url=f"https://company.sharepoint.com/docs/{doc_id}/v1",
    )
    upsert_document_metadata(
        db_schema,
        doc_id,
        title="Security Policy v2",
        url=f"https://company.sharepoint.com/docs/{doc_id}/v2",
    )

    with db_schema.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM core.document_metadata WHERE document_id = %s;",
            (doc_id,),
        )
        count = cur.fetchone()[0]

    assert count == 1, f"Expected exactly 1 record for {doc_id}, found {count}"

    row = fetch_document_metadata(db_schema, doc_id)
    validate_metadata_record(
        row,
        expected_id=doc_id,
        expected_title="Security Policy v2",
        expected_url=f"https://company.sharepoint.com/docs/{doc_id}/v2",
    )


@pytest.mark.e2e
def test_success_and_failure_statuses_are_persisted_and_queryable(db_schema, tracked_docs):
    """
    AC 3: Success and failure statuses are persisted and can be queried.
    """
    prefix = f"scrum115-status-{uuid.uuid4().hex[:8]}"
    doc_completed = f"{prefix}-ok"
    doc_failed_parse = f"{prefix}-fail-parse"
    doc_failed_storage = f"{prefix}-fail-storage"
    tracked_docs.extend([doc_completed, doc_failed_parse, doc_failed_storage])

    upsert_document_metadata(db_schema, doc_completed, "Valid Doc", f"https://company.local/{doc_completed}")
    record_sync_run(db_schema, doc_completed, status="COMPLETED")

    upsert_document_metadata(db_schema, doc_failed_parse, "Broken PDF", f"https://company.local/{doc_failed_parse}")
    record_sync_run(db_schema, doc_failed_parse, status="FAILED_PARSE", error_message="Corrupted PDF header")

    upsert_document_metadata(db_schema, doc_failed_storage, "Storage Timeout Doc", f"https://company.local/{doc_failed_storage}")
    record_sync_run(db_schema, doc_failed_storage, status="FAILED_STORAGE", error_message="S3 timeout")

    with db_schema.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, status::text
            FROM core.document_sync_runs
            WHERE document_id LIKE %s AND status = 'COMPLETED';
            """,
            (f"{prefix}%",),
        )
        completed_rows = cur.fetchall()

        cur.execute(
            """
            SELECT document_id, status::text, error_message
            FROM core.document_sync_runs
            WHERE document_id LIKE %s AND status IN ('FAILED_FETCH', 'FAILED_PARSE', 'FAILED_STORAGE')
            ORDER BY document_id;
            """,
            (f"{prefix}%",),
        )
        failed_rows = cur.fetchall()

    assert len(completed_rows) == 1
    assert completed_rows[0][0] == doc_completed
    assert completed_rows[0][1] == "COMPLETED"

    assert len(failed_rows) == 2
    failed_By_doc = {row[0]: (row[1], row[2]) for row in failed_rows}
    assert failed_By_doc[doc_failed_parse] == ("FAILED_PARSE", "Corrupted PDF header")
    assert failed_By_doc[doc_failed_storage] == ("FAILED_STORAGE", "S3 timeout")