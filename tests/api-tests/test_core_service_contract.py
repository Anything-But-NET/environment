import os

import httpx
import jsonschema
import pytest

CORE_BASE_URL = os.getenv("CORE_BASE_URL", "http://localhost:8080")
QUESTION_ENDPOINT = "/api/v1/chat/question"


def _is_service_up(base_url: str) -> bool:
    try:
        httpx.get(base_url, timeout=0.5)
        return True
    except httpx.RequestError:
        return False


class TestCoreContractSchemas:
    """
    Validates Gateway <-> Core request/response contracts directly against
    contracts/openapi.yaml (UserQuestionRequest, UserQuestionResponse, GatewayErrorResponse).
    """

    def test_valid_user_question_request(self, validate_openapi_schema):
        payload = {
            "query_id": "req-123",
            "channel": "teams",
            "user_email": "oleh.pona@company.com",
            "query": "How to get internet compensation?",
        }
        validate_openapi_schema(payload, "UserQuestionRequest")

    @pytest.mark.parametrize(
        "invalid_payload",
        [
            # Missing required query_id
            {"channel": "teams", "user_email": "oleh.pona@company.com", "query": "Hello?"},
            # Invalid channel enum (only slack, teams allowed)
            {"query_id": "req-1", "channel": "telegram", "user_email": "a@b.com", "query": "Hello?"},
            # Empty query (minLength: 1)
            {"query_id": "req-1", "channel": "slack", "user_email": "a@b.com", "query": ""},
        ],
    )
    def test_invalid_user_question_request_rejected(self, validate_openapi_schema, invalid_payload):
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "UserQuestionRequest")

    def test_user_question_response_answered(self, validate_openapi_schema):
        payload = {
            "query_id": "req-123",
            "status": "ANSWERED",
            "reply_text": "To get internet compensation, submit the reimbursement form on SharePoint.",
            "citations": [
                {
                    "title": "IT Compensation Guidelines",
                    "url": "https://company.sharepoint.com/docs/it-comp",
                    "document_id": "doc-456",
                    "chunk_id": "123e4567-e89b-12d3-a456-426614174000",
                    "score": 0.95,
                }
            ],
        }
        validate_openapi_schema(payload, "UserQuestionResponse")

    def test_user_question_response_escalated(self, validate_openapi_schema):
        payload = {
            "query_id": "req-123",
            "status": "ESCALATED_TO_EXPERT",
            "reply_text": "I couldn't find a confident answer. A support ticket was created.",
            "citations": [],
        }
        validate_openapi_schema(payload, "UserQuestionResponse")

    @pytest.mark.parametrize(
        "error_code",
        ["BAD_REQUEST", "UNAUTHORIZED", "SERVICE_UNAVAILABLE", "INTERNAL_ERROR"],
    )
    def test_gateway_error_response(self, validate_openapi_schema, error_code):
        payload = {
            "query_id": "req-123",
            "error_code": error_code,
            "user_message": "Human-friendly error message.",
        }
        validate_openapi_schema(payload, "GatewayErrorResponse")

    def test_invalid_gateway_error_code_rejected(self, validate_openapi_schema):
        invalid_payload = {
            "query_id": "req-123",
            "error_code": "UNKNOWN_CODE",
            "user_message": "Error",
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "GatewayErrorResponse")


@pytest.mark.skipif(
    not _is_service_up(CORE_BASE_URL),
    reason=f"Core service is not reachable at {CORE_BASE_URL}",
)
class TestCoreServiceLiveContract:
    """
    Live HTTP contract tests against Core service (POST /api/v1/chat/question).
    Automatically runs when Core service is up on CORE_BASE_URL.
    """

    def test_post_question_returns_valid_contract(self, validate_openapi_schema):
        payload = {
            "query_id": "req-live-001",
            "channel": "teams",
            "user_email": "alice@contoso-mock.onmicrosoft.com",
            "query": "How to get internet compensation?",
        }
        validate_openapi_schema(payload, "UserQuestionRequest")

        response = httpx.post(f"{CORE_BASE_URL}{QUESTION_ENDPOINT}", json=payload, timeout=10.0)
        assert response.status_code == 200
        body = response.json()
        validate_openapi_schema(body, "UserQuestionResponse")
        assert body["query_id"] == payload["query_id"]

    def test_post_empty_question_returns_400_error_contract(self, validate_openapi_schema):
        payload = {
            "query_id": "req-live-400",
            "channel": "slack",
            "user_email": "alice@contoso-mock.onmicrosoft.com",
            "query": "",
        }
        response = httpx.post(f"{CORE_BASE_URL}{QUESTION_ENDPOINT}", json=payload, timeout=5.0)
        assert response.status_code == 400
        body = response.json()
        validate_openapi_schema(body, "GatewayErrorResponse")
        assert body["error_code"] == "BAD_REQUEST"
