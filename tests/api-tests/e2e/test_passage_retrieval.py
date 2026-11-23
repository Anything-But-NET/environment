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
USER_ACL_TOPIC = os.getenv("KAFKA_USER_ACL_TOPIC", "rag.users.acl.v1")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "copilot_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "example")

RAG_BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:8000")
RAG_GENERATE_ENDPOINT = "/api/v1/rag/generate"

CORE_BASE_URL = os.getenv("CORE_BASE_URL", "http://localhost:8080")
CORE_QUESTION_ENDPOINT = "/api/v1/chat/question"


def _is_service_up(base_url: str) -> bool:
    try:
        httpx.get(base_url, timeout=1.0)
        return True
    except httpx.RequestError:
        return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(scope="module")
def kafka_producer():
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
def db_connection():
    """
    Connects to the real PostgreSQL instance and verifies that the actual
    Alembic/Flyway-managed tables exist (never creates fake test tables).
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
            for table in (
                "rag.document",
                "rag.chunks",
                "rag.vector_nodes",
                "rag.vector_chunk_association",
                "core.document_metadata",
                "core.users",
            ):
                cur.execute(f"SELECT 1 FROM {table} LIMIT 1;")
        yield conn
        conn.close()
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Skipped E2E tests: PostgreSQL not available ({exc})")
    except UndefinedTable as exc:
        pytest.skip(f"Skipped E2E tests: Required schema/table missing ({exc})")


def _publish_document_upsert(
    producer: KafkaProducer,
    validate_kafka_schema,
    *,
    document_id: str,
    title: str,
    url: str,
    raw_content: str,
    allowed_groups: list[str],
) -> None:
    event = {
        "sync_id": str(uuid.uuid4()),
        "document_id": document_id,
        "action": "create",
        "source_system": "sharepoint",
        "title": title,
        "url": url,
        "raw_content": raw_content,
        "last_updated": _utc_now_iso(),
        "acl": {"allowed_groups": allowed_groups},
    }
    validate_kafka_schema(event, "DocumentUpsertEvent")
    producer.send(INGESTION_TOPIC, key=document_id.encode("utf-8"), value=event).get(timeout=10)


def _publish_user_acl(
    producer: KafkaProducer,
    validate_kafka_schema,
    *,
    email: str,
    allowed_groups: list[str],
) -> None:
    event = {
        "email": email,
        "last_updated": _utc_now_iso(),
        "acl": {"allowed_groups": allowed_groups},
    }
    validate_kafka_schema(event, "KafkaUserAclUpsert")
    producer.send(USER_ACL_TOPIC, key=email.encode("utf-8"), value=event).get(timeout=10)


def _wait_for_document_indexed(conn, document_id: str, timeout: float = 30.0) -> None:
    """Waits until a document is indexed across Core metadata and RAG vector/chunk tables."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM core.document_metadata WHERE document_id = %s),
                    (SELECT COUNT(*) FROM rag.document WHERE document_id = %s),
                    (SELECT COUNT(*) FROM rag.chunks WHERE document_id = %s),
                    (SELECT COUNT(*) FROM rag.vector_nodes WHERE document_id = %s AND embedding IS NOT NULL)
                """,
                (document_id, document_id, document_id, document_id),
            )
            core_cnt, rag_doc_cnt, chunk_cnt, vec_cnt = cur.fetchone()
            if core_cnt >= 1 and rag_doc_cnt >= 1 and chunk_cnt >= 1 and vec_cnt >= 1:
                return
        time.sleep(0.5)
    raise AssertionError(
        f"Timed out after {timeout}s waiting for '{document_id}' to be indexed in Core and RAG DB tables."
    )


def _wait_for_user_acl(conn, email: str, expected_groups: list[str], timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    expected_set = set(expected_groups)
    while time.time() < deadline:
        with conn.cursor() as cur:
            cur.execute("SELECT allowed_groups FROM core.users WHERE email = %s;", (email,))
            row = cur.fetchone()
            if row and set(row[0] or []) == expected_set:
                return
        time.sleep(0.5)
    raise AssertionError(f"Timed out after {timeout}s waiting for user ACL '{email}' in core.users.")


def _cleanup_test_data(conn, document_ids: list[str], user_emails: list[str] | None = None) -> None:
    with conn.cursor() as cur:
        if document_ids:
            cur.execute("DELETE FROM rag.document WHERE document_id = ANY(%s);", (document_ids,))
            cur.execute("DELETE FROM core.document_metadata WHERE document_id = ANY(%s);", (document_ids,))
        if user_emails:
            cur.execute("DELETE FROM core.users WHERE email = ANY(%s);", (user_emails,))


def _fetch_chunk_owner(conn, chunk_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT document_id FROM rag.chunks WHERE id = %s::uuid;", (chunk_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _post_rag_generate(
    validate_openapi_schema,
    *,
    query: str,
    allowed_groups: list[str],
) -> dict:
    payload = {
        "query_id": f"req-e2e-rag-{uuid.uuid4().hex[:8]}",
        "query": query,
        "user_permissions": {"allowed_groups": allowed_groups},
    }
    validate_openapi_schema(payload, "RagRetrievalRequest")

    response = httpx.post(f"{RAG_BASE_URL}{RAG_GENERATE_ENDPOINT}", json=payload, timeout=20.0)
    assert response.status_code == 200, f"Unexpected status {response.status_code}: {response.text}"
    body = response.json()
    validate_openapi_schema(body, "RagRetrievalResponse")
    assert body["query_id"] == payload["query_id"]
    return body


def _post_core_question(
    validate_openapi_schema,
    *,
    user_email: str,
    query: str,
) -> dict:
    payload = {
        "query_id": f"req-e2e-core-{uuid.uuid4().hex[:8]}",
        "channel": "teams",
        "user_email": user_email,
        "query": query,
    }
    validate_openapi_schema(payload, "UserQuestionRequest")

    response = httpx.post(f"{CORE_BASE_URL}{CORE_QUESTION_ENDPOINT}", json=payload, timeout=25.0)
    assert response.status_code == 200, f"Unexpected status {response.status_code}: {response.text}"
    body = response.json()
    validate_openapi_schema(body, "UserQuestionResponse")
    assert body["query_id"] == payload["query_id"]
    return body


@pytest.mark.e2e
def test_e2e_ingestion_populates_real_chunks_and_vectors(
    kafka_producer,
    db_connection,
    validate_kafka_schema,
):
    """
    Verifies that publishing a DocumentUpsertEvent to Kafka populates the real
    rag.document, rag.chunks, rag.vector_nodes (with embeddings),
    rag.vector_chunk_association, and core.document_metadata tables.
    """
    run_id = uuid.uuid4().hex[:8]
    doc_id = f"scrum29-ingest-{run_id}"
    expected_title = f"Zephyr Policy {run_id}"
    expected_url = f"https://company.sharepoint.com/docs/{doc_id}"
    expected_groups = [f"grp-ingest-{run_id}"]

    try:
        _publish_document_upsert(
            kafka_producer,
            validate_kafka_schema,
            document_id=doc_id,
            title=expected_title,
            url=expected_url,
            raw_content=(
                f"Zephyr Orbital Stipend {run_id}: Employees receive $120 per quarter "
                "for home telemetry equipment by submitting Form ZO-42."
            ),
            allowed_groups=expected_groups,
        )
        _wait_for_document_indexed(db_connection, doc_id)

        with db_connection.cursor() as cur:
            cur.execute(
                """
                SELECT d.allowed_groups, m.title, m.url, COUNT(DISTINCT c.id), COUNT(DISTINCT v.id)
                FROM rag.document d
                JOIN core.document_metadata m ON m.document_id = d.document_id
                JOIN rag.chunks c ON c.document_id = d.document_id
                JOIN rag.vector_chunk_association vca ON vca.chunk_id = c.id
                JOIN rag.vector_nodes v ON v.id = vca.vector_node_id
                WHERE d.document_id = %s
                  AND v.embedding IS NOT NULL
                GROUP BY d.allowed_groups, m.title, m.url;
                """,
                (doc_id,),
            )
            row = cur.fetchone()

        assert row is not None, "Expected joined chunk/vector/metadata rows for ingested document"
        allowed_groups, title, url, chunk_count, vector_count = row
        assert set(allowed_groups) == set(expected_groups)
        assert title == expected_title
        assert url == expected_url
        assert chunk_count >= 1
        assert vector_count >= 1
    finally:
        _cleanup_test_data(db_connection, [doc_id])


@pytest.mark.e2e
@pytest.mark.skipif(
    not _is_service_up(RAG_BASE_URL),
    reason=f"RAG service is not reachable at {RAG_BASE_URL}",
)
def test_e2e_rag_retrieval_ranking_and_relevance(
    kafka_producer,
    db_connection,
    validate_kafka_schema,
    validate_openapi_schema,
):
    """
    Seeds a relevant document and an unrelated distractor via Kafka, then verifies
    POST /api/v1/rag/generate returns ANSWER_GENERATED, non-empty citations
    ranked by score descending, real chunk IDs from Postgres, and the relevant
    document ranked at the top.
    """
    run_id = uuid.uuid4().hex[:8]
    group = f"grp-rank-{run_id}"
    doc_relevant = f"scrum29-rel-{run_id}"
    doc_distractor = f"scrum29-dist-{run_id}"

    try:
        _publish_document_upsert(
            kafka_producer,
            validate_kafka_schema,
            document_id=doc_relevant,
            title=f"Zephyr Orbital Stipend Guide {run_id}",
            url=f"https://company.sharepoint.com/docs/{doc_relevant}",
            raw_content=(
                f"To claim the Zephyr Orbital Stipend ({run_id}), submit Form ZO-42 "
                "in the Nebula Reimbursement Portal within 30 days of purchase."
            ),
            allowed_groups=[group],
        )
        _publish_document_upsert(
            kafka_producer,
            validate_kafka_schema,
            document_id=doc_distractor,
            title=f"Cafeteria Menu {run_id}",
            url=f"https://company.sharepoint.com/docs/{doc_distractor}",
            raw_content="Tuesday lunch features roasted tomato soup, sourdough bread, and seasonal fruit salad.",
            allowed_groups=[group],
        )

        _wait_for_document_indexed(db_connection, doc_relevant)
        _wait_for_document_indexed(db_connection, doc_distractor)

        body = _post_rag_generate(
            validate_openapi_schema,
            query=f"How do I claim the Zephyr Orbital Stipend {run_id}?",
            allowed_groups=[group],
        )

        assert body["status"] == "ANSWER_GENERATED"
        assert isinstance(body["answer"], str) and len(body["answer"].strip()) > 0
        assert body["confidence_score"] > 0.0

        citations = body["citations"]
        assert len(citations) > 0, "Expected at least one citation for a seeded relevant document"

        scores = [c["score"] for c in citations]
        assert all(0.0 <= s <= 1.0 for s in scores)
        assert scores == sorted(scores, reverse=True), "Citations must be ranked by score descending"

        returned_doc_ids = [c["document_id"] for c in citations]
        assert doc_relevant in returned_doc_ids, "Relevant seeded document was not retrieved"
        assert citations[0]["document_id"] == doc_relevant, (
            f"Expected relevant document '{doc_relevant}' to rank first, got {citations[0]['document_id']}"
        )

        for citation in citations:
            owner_doc_id = _fetch_chunk_owner(db_connection, citation["chunk_id"])
            assert owner_doc_id == citation["document_id"], (
                f"Returned chunk_id {citation['chunk_id']} does not match document {citation['document_id']} in DB"
            )
    finally:
        _cleanup_test_data(db_connection, [doc_relevant, doc_distractor])


@pytest.mark.e2e
@pytest.mark.skipif(
    not _is_service_up(RAG_BASE_URL),
    reason=f"RAG service is not reachable at {RAG_BASE_URL}",
)
def test_e2e_rag_acl_filtering_enforced(
    kafka_producer,
    db_connection,
    validate_kafka_schema,
    validate_openapi_schema,
):
    """
    Seeds two equally relevant documents with distinct allowed_groups and verifies:
    1. Authorized user receives only the permitted document, never the restricted one.
    2. User with the restricted group receives the restricted document (proving it is indexed).
    3. Unauthorized user receives neither document.
    """
    run_id = uuid.uuid4().hex[:8]
    allowed_group = f"grp-allowed-{run_id}"
    restricted_group = f"grp-restricted-{run_id}"
    unrelated_group = f"grp-unrelated-{run_id}"

    doc_allowed = f"scrum29-acl-allow-{run_id}"
    doc_restricted = f"scrum29-acl-deny-{run_id}"
    query = f"What is the Aurora Quantum Allowance {run_id} procedure?"

    try:
        _publish_document_upsert(
            kafka_producer,
            validate_kafka_schema,
            document_id=doc_allowed,
            title=f"Aurora Quantum Allowance Standard {run_id}",
            url=f"https://company.sharepoint.com/docs/{doc_allowed}",
            raw_content=(
                f"Aurora Quantum Allowance ({run_id}) standard procedure: "
                "standard employees receive $75 monthly upon manager sign-off."
            ),
            allowed_groups=[allowed_group],
        )
        _publish_document_upsert(
            kafka_producer,
            validate_kafka_schema,
            document_id=doc_restricted,
            title=f"Aurora Quantum Allowance Executive {run_id}",
            url=f"https://company.sharepoint.com/docs/{doc_restricted}",
            raw_content=(
                f"Aurora Quantum Allowance ({run_id}) executive procedure: "
                "board members receive $500 monthly via private payroll."
            ),
            allowed_groups=[restricted_group],
        )

        _wait_for_document_indexed(db_connection, doc_allowed)
        _wait_for_document_indexed(db_connection, doc_restricted)

        # 1. User in allowed_group sees doc_allowed and NEVER doc_restricted
        allowed_resp = _post_rag_generate(
            validate_openapi_schema,
            query=query,
            allowed_groups=[allowed_group],
        )
        assert allowed_resp["status"] == "ANSWER_GENERATED"
        assert len(allowed_resp["citations"]) > 0
        allowed_doc_ids = {c["document_id"] for c in allowed_resp["citations"]}
        assert doc_allowed in allowed_doc_ids
        assert doc_restricted not in allowed_doc_ids

        # 2. User in restricted_group sees doc_restricted and NEVER doc_allowed
        restricted_resp = _post_rag_generate(
            validate_openapi_schema,
            query=query,
            allowed_groups=[restricted_group],
        )
        assert restricted_resp["status"] == "ANSWER_GENERATED"
        assert len(restricted_resp["citations"]) > 0
        restricted_doc_ids = {c["document_id"] for c in restricted_resp["citations"]}
        assert doc_restricted in restricted_doc_ids
        assert doc_allowed not in restricted_doc_ids

        # 3. User with no matching group sees neither document
        unauth_resp = _post_rag_generate(
            validate_openapi_schema,
            query=query,
            allowed_groups=[unrelated_group],
        )
        unauth_doc_ids = {c["document_id"] for c in unauth_resp["citations"]}
        assert doc_allowed not in unauth_doc_ids
        assert doc_restricted not in unauth_doc_ids
        if unauth_resp["status"] == "EMPTY_RETRIEVAL":
            assert unauth_resp["answer"] is None
            assert unauth_resp["confidence_score"] == 0.0
            assert unauth_resp["citations"] == []
    finally:
        _cleanup_test_data(db_connection, [doc_allowed, doc_restricted])


@pytest.mark.e2e
@pytest.mark.skipif(
    not (_is_service_up(CORE_BASE_URL) and _is_service_up(RAG_BASE_URL)),
    reason=f"Core ({CORE_BASE_URL}) and/or RAG ({RAG_BASE_URL}) service is not reachable",
)
def test_e2e_core_chat_enriches_citation_metadata_and_enforces_acl(
    kafka_producer,
    db_connection,
    validate_kafka_schema,
    validate_openapi_schema,
):
    """
    End-to-end test through Core (POST /api/v1/chat/question):
    Verifies Core resolves user ACL groups from core.users, delegates retrieval
    to RAG, filters out restricted documents, and enriches returned citations with
    title and url from core.document_metadata.
    """
    run_id = uuid.uuid4().hex[:8]
    allowed_group = f"grp-core-ok-{run_id}"
    restricted_group = f"grp-core-no-{run_id}"
    unrelated_group = f"grp-core-none-{run_id}"

    auth_email = f"alice.{run_id}@company.test"
    unauth_email = f"bob.{run_id}@company.test"

    doc_allowed = f"scrum29-core-ok-{run_id}"
    doc_restricted = f"scrum29-core-no-{run_id}"
    expected_title = f"Helios Travel Per Diem {run_id}"
    expected_url = f"https://company.sharepoint.com/docs/{doc_allowed}"
    query = f"What is the Helios Travel Per Diem {run_id}?"

    try:
        _publish_user_acl(
            kafka_producer,
            validate_kafka_schema,
            email=auth_email,
            allowed_groups=[allowed_group],
        )
        _publish_user_acl(
            kafka_producer,
            validate_kafka_schema,
            email=unauth_email,
            allowed_groups=[unrelated_group],
        )
        _publish_document_upsert(
            kafka_producer,
            validate_kafka_schema,
            document_id=doc_allowed,
            title=expected_title,
            url=expected_url,
            raw_content=(
                f"Helios Travel Per Diem ({run_id}): employees on business trips "
                "receive $65 daily for meals."
            ),
            allowed_groups=[allowed_group],
        )
        _publish_document_upsert(
            kafka_producer,
            validate_kafka_schema,
            document_id=doc_restricted,
            title=f"Helios Executive Travel {run_id}",
            url=f"https://company.sharepoint.com/docs/{doc_restricted}",
            raw_content=(
                f"Helios Travel Per Diem ({run_id}): executives receive $250 daily "
                "with corporate card."
            ),
            allowed_groups=[restricted_group],
        )

        _wait_for_user_acl(db_connection, auth_email, [allowed_group])
        _wait_for_user_acl(db_connection, unauth_email, [unrelated_group])
        _wait_for_document_indexed(db_connection, doc_allowed)
        _wait_for_document_indexed(db_connection, doc_restricted)

        # Authorized user gets enriched citation metadata and no restricted docs
        auth_resp = _post_core_question(
            validate_openapi_schema,
            user_email=auth_email,
            query=query,
        )
        assert auth_resp["status"] == "ANSWERED"
        assert len(auth_resp["reply_text"].strip()) > 0

        citations = auth_resp["citations"]
        assert len(citations) > 0, "Expected enriched citations from Core"

        scores = [c["score"] for c in citations]
        assert scores == sorted(scores, reverse=True)

        returned_doc_ids = [c["document_id"] for c in citations]
        assert doc_allowed in returned_doc_ids
        assert doc_restricted not in returned_doc_ids

        allowed_citations = [c for c in citations if c["document_id"] == doc_allowed]
        assert allowed_citations[0]["title"] == expected_title
        assert allowed_citations[0]["url"] == expected_url
        assert _fetch_chunk_owner(db_connection, allowed_citations[0]["chunk_id"]) == doc_allowed

        # Unauthorized user must not see either document
        unauth_resp = _post_core_question(
            validate_openapi_schema,
            user_email=unauth_email,
            query=query,
        )
        unauth_doc_ids = {c["document_id"] for c in unauth_resp["citations"]}
        assert doc_allowed not in unauth_doc_ids
        assert doc_restricted not in unauth_doc_ids
    finally:
        _cleanup_test_data(db_connection, [doc_allowed, doc_restricted], [auth_email, unauth_email])
