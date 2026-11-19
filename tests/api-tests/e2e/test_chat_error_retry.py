import os
import uuid

import httpx
import psycopg2
from psycopg2.errors import UndefinedTable
import pytest

CORE_BASE_URL = os.getenv("CORE_BASE_URL", "http://localhost:8080")
NET_BASE_URL = os.getenv("NET_BASE_URL", "http://localhost:5224")
RAG_BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:8000")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "copilot_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "example")

SENSITIVE_MARKERS = (
    "Exception",
    "Traceback",
    "at com.",
    "at System.",
    "psycopg2",
    "POSTGRES",
    "xoxb-",
)


def _require_service(url: str, name: str) -> None:
    try:
        httpx.get(url, timeout=1.5)
    except httpx.RequestError:
        pytest.skip(f"Skipped E2E test: {name} is not reachable at {url}")


def _assert_no_internal_leakage(text: str) -> None:
    for marker in SENSITIVE_MARKERS:
        assert marker not in text, f"Internal detail leaked to user-facing message: {marker}"


@pytest.fixture(scope="module")
def db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM core.generation_diagnostics LIMIT 1")
        yield conn
        conn.close()
    except psycopg2.OperationalError:
        pytest.skip("Skipped E2E tests: PostgreSQL is not reachable")
    except UndefinedTable:
        pytest.skip("Skipped E2E tests: Table core.generation_diagnostics does not exist")


def _count_diagnostics(conn, query_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT query_id, outcome, error_category
            FROM core.generation_diagnostics
            WHERE query_id = %s
            ORDER BY created_at ASC
            """,
            (query_id,),
        )
        return cur.fetchall()


@pytest.mark.e2e
def test_net_service_slack_rejects_empty_question_with_safe_fallback(validate_openapi_schema):
    _require_service(f"{NET_BASE_URL}/api/documents/Doc", "net-service")

    payload = {
        "type": "event_callback",
        "event_id": f"slack-empty-{uuid.uuid4()}",
        "event": {
            "type": "app_mention",
            "user": "U12345",
            "channel": "C12345",
            "text": "<@U0123BOT>    ",
        },
    }

    response = httpx.post(f"{NET_BASE_URL}/api/slack/events", json=payload, timeout=5.0)

    assert response.status_code == 400
    body = response.json()
    validate_openapi_schema(body, "GatewayErrorResponse")
    assert body["error_code"] == "BAD_REQUEST"
    _assert_no_internal_leakage(body["user_message"])


@pytest.mark.e2e
def test_net_service_slack_accepts_valid_event_and_returns_fast_ack():
    _require_service(f"{NET_BASE_URL}/api/documents/Doc", "net-service")

    payload = {
        "type": "event_callback",
        "event_id": f"slack-e2e-{uuid.uuid4()}",
        "event": {
            "type": "app_mention",
            "user": "U12345",
            "channel": "C12345",
            "text": "<@BOT_ID> What is our vacation policy?",
        },
    }

    response = httpx.post(f"{NET_BASE_URL}/api/slack/events", json=payload, timeout=5.0)
    assert response.status_code == 200


@pytest.mark.e2e
def test_chat_retry_does_not_create_duplicate_diagnostics_or_escalations(
    db_connection,
    validate_openapi_schema,
):
    _require_service(CORE_BASE_URL, "java-service")

    query_id = f"e2e-chat-{uuid.uuid4()}"
    request_payload = {
        "query_id": query_id,
        "channel": "slack",
        "user_email": "vitalii.peleshko.shi.2025@lpnu.ua",
        "query": "What is the reimbursement process for home office hardware?",
    }
    validate_openapi_schema(request_payload, "UserQuestionRequest")

    first = httpx.post(
        f"{CORE_BASE_URL}/api/v1/chat/question",
        json=request_payload,
        timeout=65.0,
    )

    if first.status_code == 200:
        first_body = first.json()
        validate_openapi_schema(first_body, "UserQuestionResponse")
        assert first_body["query_id"] == query_id
        _assert_no_internal_leakage(first_body["reply_text"])
    else:
        assert first.status_code in (500, 503)
        first_err = first.json()
        validate_openapi_schema(first_err, "GatewayErrorResponse")
        assert first_err["error_code"] in ("SERVICE_UNAVAILABLE", "INTERNAL_ERROR")
        _assert_no_internal_leakage(first_err["user_message"])

    rows_after_first = _count_diagnostics(db_connection, query_id)
    assert len(rows_after_first) == 1, f"Expected 1 diagnostics record, got {len(rows_after_first)}"

    retry = httpx.post(
        f"{CORE_BASE_URL}/api/v1/chat/question",
        json=request_payload,
        timeout=15.0,
    )

    assert retry.status_code in (400, 500, 503)
    retry_body = retry.json()
    validate_openapi_schema(retry_body, "GatewayErrorResponse")
    _assert_no_internal_leakage(retry_body["user_message"])

    rows_after_retry = _count_diagnostics(db_connection, query_id)
    assert len(rows_after_retry) == 1, "Retry must not create duplicate diagnostics/escalation rows"


@pytest.mark.e2e
def test_chat_validation_error_returns_safe_fallback(validate_openapi_schema):
    _require_service(CORE_BASE_URL, "java-service")

    invalid_payload = {
        "query_id": f"e2e-chat-invalid-{uuid.uuid4()}",
        "channel": "slack",
        "user_email": "vitalii.peleshko.shi.2025@lpnu.ua",
        "query": "",
    }

    response = httpx.post(
        f"{CORE_BASE_URL}/api/v1/chat/question",
        json=invalid_payload,
        timeout=10.0,
    )

    assert response.status_code == 400
    body = response.json()
    validate_openapi_schema(body, "GatewayErrorResponse")
    assert body["error_code"] == "BAD_REQUEST"
    _assert_no_internal_leakage(body["user_message"])