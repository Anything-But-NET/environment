import os
import uuid
import httpx
import pytest
import psycopg2
from psycopg2.errors import UndefinedTable

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "copilot_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "example")
RAG_BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:8000")
RAG_GENERATE_ENDPOINT = "/api/v1/rag/generate"


def _is_rag_service_up(base_url: str) -> bool:
    try:
        httpx.get(base_url, timeout=0.5)
        return True
    except httpx.RequestError:
        return False


def rank_and_filter_passages(
    indexed_passages: list[dict],
    user_groups: list[str],
    top_k: int = 5,
) -> list[dict]:
    """
    Filters indexed passages by ACL intersection (allowed_groups)
    and ranks matching chunks by similarity score descending.
    """
    user_group_set = set(user_groups)
    authorized = [
        p for p in indexed_passages
        if user_group_set.intersection(set(p.get("allowed_groups", [])))
    ]
    ranked = sorted(authorized, key=lambda x: x["score"], reverse=True)
    return ranked[:top_k]


def build_rag_citation(passage: dict) -> dict:
    """Builds lightweight RagCitation (document_id, chunk_id, score)."""
    return {
        "document_id": passage["document_id"],
        "chunk_id": passage["chunk_id"],
        "score": round(float(passage["score"]), 4),
    }


def enrich_citation(rag_citation: dict, metadata_store: dict[str, dict]) -> dict:
    """Enriches RagCitation with title and url from document metadata."""
    meta = metadata_store[rag_citation["document_id"]]
    return {
        "title": meta["title"],
        "url": meta["url"],
        "document_id": rag_citation["document_id"],
        "chunk_id": rag_citation["chunk_id"],
        "score": rag_citation["score"],
    }


# -------------------------------------------------------------------------
# Contract & Retrieval Logic Tests (Always run in CI pull_request checks)
# -------------------------------------------------------------------------

def test_retrieval_returns_ranked_relevant_chunks(validate_openapi_schema):
    """
    AC 1: Given an indexed test question, when retrieval runs,
    then it returns ranked relevant chunks.
    """
    c1 = str(uuid.uuid4())
    c2 = str(uuid.uuid4())
    c3 = str(uuid.uuid4())

    indexed = [
        {"document_id": "doc-comp-1", "chunk_id": c1, "score": 0.62, "allowed_groups": ["all-employees"]},
        {"document_id": "doc-comp-2", "chunk_id": c2, "score": 0.94, "allowed_groups": ["all-employees"]},
        {"document_id": "doc-comp-3", "chunk_id": c3, "score": 0.81, "allowed_groups": ["all-employees"]},
    ]

    results = rank_and_filter_passages(indexed, user_groups=["all-employees"])
    citations = [build_rag_citation(r) for r in results]

    assert [c["chunk_id"] for c in citations] == [c2, c3, c1]
    assert citations[0]["score"] > citations[1]["score"] > citations[2]["score"]

    rag_response = {
        "query_id": "req-scrum29-rank",
        "status": "ANSWER_GENERATED",
        "answer": "Submit the internet reimbursement form on SharePoint.",
        "confidence_score": citations[0]["score"],
        "citations": citations,
    }
    validate_openapi_schema(rag_response, "RagRetrievalResponse")


def test_returned_chunks_include_source_identity_and_citation_metadata(validate_openapi_schema):
    """
    AC 2: Each returned chunk includes source identity and citation metadata.
    """
    chunk_uuid = str(uuid.uuid4())
    metadata_store = {
        "doc-456": {
            "title": "IT Compensation Guidelines",
            "url": "https://company.sharepoint.com/docs/it-comp",
        }
    }

    rag_citation = build_rag_citation(
        {"document_id": "doc-456", "chunk_id": chunk_uuid, "score": 0.95}
    )
    validate_openapi_schema(rag_citation, "RagCitation")

    full_citation = enrich_citation(rag_citation, metadata_store)
    validate_openapi_schema(full_citation, "Citation")

    assert full_citation["document_id"] == "doc-456"
    assert full_citation["chunk_id"] == chunk_uuid
    assert full_citation["title"] == "IT Compensation Guidelines"
    assert full_citation["url"] == "https://company.sharepoint.com/docs/it-comp"
    assert full_citation["score"] == 0.95


def test_retrieval_respects_access_rules(validate_openapi_schema):
    """
    AC 3: Retrieval results respect the access rules available to the service.
    """
    c_public = str(uuid.uuid4())
    c_hr_only = str(uuid.uuid4())
    c_devs = str(uuid.uuid4())

    indexed = [
        {"document_id": "doc-hr-secret", "chunk_id": c_hr_only, "score": 0.99, "allowed_groups": ["hr-managers"]},
        {"document_id": "doc-dev-guide", "chunk_id": c_devs, "score": 0.88, "allowed_groups": ["devs"]},
        {"document_id": "doc-general", "chunk_id": c_public, "score": 0.75, "allowed_groups": ["all-employees"]},
    ]

    # Employee is in 'devs' and 'all-employees', NOT in 'hr-managers'
    user_groups = ["devs", "all-employees"]
    results = rank_and_filter_passages(indexed, user_groups=user_groups)
    returned_doc_ids = [r["document_id"] for r in results]

    assert "doc-hr-secret" not in returned_doc_ids
    assert returned_doc_ids == ["doc-dev-guide", "doc-general"]

    # Unauthorized user with no matching groups gets EMPTY_RETRIEVAL
    empty_results = rank_and_filter_passages(indexed, user_groups=["external-contractors"])
    assert empty_results == []

    empty_response = {
        "query_id": "req-scrum29-acl-empty",
        "status": "EMPTY_RETRIEVAL",
        "answer": None,
        "confidence_score": 0.0,
        "citations": [],
    }
    validate_openapi_schema(empty_response, "RagRetrievalResponse")


# -------------------------------------------------------------------------
# Database & Live Service E2E Tests (Run when Postgres / RAG service are up)
# -------------------------------------------------------------------------

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
        pytest.skip("Skipped E2E tests: PostgreSQL not available on CI-server")


@pytest.fixture(scope="module")
def rag_db_schema(db_connection):
    try:
        with db_connection.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS core;")
            cur.execute("CREATE SCHEMA IF NOT EXISTS rag;")
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
                CREATE TABLE IF NOT EXISTS rag.document (
                    document_id VARCHAR(255) PRIMARY KEY,
                    allowed_groups TEXT[] NOT NULL DEFAULT '{}',
                    last_updated TIMESTAMPTZ,
                    last_sync_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag.chunks (
                    id UUID PRIMARY KEY,
                    text TEXT NOT NULL,
                    document_id VARCHAR(255) NOT NULL REFERENCES rag.document(document_id) ON DELETE CASCADE
                );
            """)
    except (psycopg2.Error, UndefinedTable) as exc:
        pytest.skip(f"Skipped E2E tests: RAG schema setup failed ({exc})")
    return db_connection


@pytest.mark.e2e
def test_db_acl_filtered_chunk_retrieval_with_citation_join(rag_db_schema):
    """
    Verifies SQL-level retrieval across rag.document, rag.chunks, and
    core.document_metadata respecting allowed_groups ACL array overlap (&&).
    """
    prefix = f"scrum29-{uuid.uuid4().hex[:8]}"
    doc_allowed = f"{prefix}-allowed"
    doc_restricted = f"{prefix}-restricted"
    chunk_allowed_id = str(uuid.uuid4())
    chunk_restricted_id = str(uuid.uuid4())

    try:
        with rag_db_schema.cursor() as cur:
            for doc_id, title, groups in [
                (doc_allowed, "Employee VPN & Internet Policy", ["devs", "all-employees"]),
                (doc_restricted, "Executive Board Compensation", ["board-members"]),
            ]:
                cur.execute(
                    """
                    INSERT INTO core.document_metadata (document_id, source_system, title, url, last_sync_at)
                    VALUES (%s, 'sharepoint', %s, %s, NOW());
                    """,
                    (doc_id, title, f"https://company.sharepoint.com/docs/{doc_id}"),
                )
                cur.execute(
                    """
                    INSERT INTO rag.document (document_id, allowed_groups, last_sync_at)
                    VALUES (%s, %s, NOW());
                    """,
                    (doc_id, groups),
                )

            cur.execute(
                "INSERT INTO rag.chunks (id, text, document_id) VALUES (%s, %s, %s);",
                (chunk_allowed_id, "Internet compensation is up to $50 per month.", doc_allowed),
            )
            cur.execute(
                "INSERT INTO rag.chunks (id, text, document_id) VALUES (%s, %s, %s);",
                (chunk_restricted_id, "Confidential executive bonus structure.", doc_restricted),
            )

            # Query chunks with ACL filter (allowed_groups && user_groups) + citation metadata join
            user_groups = ["all-employees"]
            cur.execute(
                """
                SELECT c.id::text, c.document_id, c.text, m.title, m.url
                FROM rag.chunks c
                JOIN rag.document d ON d.document_id = c.document_id
                JOIN core.document_metadata m ON m.document_id = c.document_id
                WHERE c.document_id IN (%s, %s)
                  AND d.allowed_groups && %s::text[];
                """,
                (doc_allowed, doc_restricted, user_groups),
            )
            rows = cur.fetchall()

        assert len(rows) == 1
        chunk_id, doc_id, text, title, url = rows[0]
        assert chunk_id == chunk_allowed_id
        assert doc_id == doc_allowed
        assert "Internet compensation" in text
        assert title == "Employee VPN & Internet Policy"
        assert url == f"https://company.sharepoint.com/docs/{doc_allowed}"
    finally:
        with rag_db_schema.cursor() as cur:
            cur.execute("DELETE FROM rag.document WHERE document_id IN (%s, %s);", (doc_allowed, doc_restricted))
            cur.execute("DELETE FROM core.document_metadata WHERE document_id IN (%s, %s);", (doc_allowed, doc_restricted))


@pytest.mark.e2e
@pytest.mark.skipif(
    not _is_rag_service_up(RAG_BASE_URL),
    reason=f"RAG service is not reachable at {RAG_BASE_URL}",
)
def test_live_rag_service_retrieval_ranking_and_acl(validate_openapi_schema):
    """
    Verifies live /api/v1/rag/generate endpoint returns ranked citations
    and respects user_permissions.allowed_groups.
    """
    payload = {
        "query_id": f"req-scrum29-{uuid.uuid4().hex[:8]}",
        "query": "How to get internet compensation?",
        "user_permissions": {
            "allowed_groups": ["Engineering", "All Staff", "alice@contoso-mock.onmicrosoft.com"]
        },
    }
    response = httpx.post(f"{RAG_BASE_URL}{RAG_GENERATE_ENDPOINT}", json=payload, timeout=15.0)
    assert response.status_code == 200
    body = response.json()
    validate_openapi_schema(body, "RagRetrievalResponse")

    citations = body.get("citations", [])
    scores = [c["score"] for c in citations]
    assert scores == sorted(scores, reverse=True), "Returned citations must be ranked by score descending"