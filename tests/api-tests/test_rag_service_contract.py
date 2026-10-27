import os

import httpx
import jsonschema
import pytest

RAG_BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:8000")
RAG_GENERATE_ENDPOINT = "/api/v1/rag/generate"


def _is_service_up(base_url: str) -> bool:
    try:
        httpx.get(base_url, timeout=0.5)
        return True
    except httpx.RequestError:
        return False


class TestRagContractSchemas:
    """
    Validates Core <-> RAG request/response contracts directly against
    contracts/openapi.yaml (RagRetrievalRequest, RagRetrievalResponse, GenerationError).
    """

    def test_valid_rag_retrieval_request(self, validate_openapi_schema):
        payload = {
            "query_id": "req-123",
            "query": "How to get internet compensation?",
            "user_permissions": {
                "allowed_groups": ["devs", "all-employees", "oleh.pona@company.com"]
            },
        }
        validate_openapi_schema(payload, "RagRetrievalRequest")

    @pytest.mark.parametrize(
        "invalid_payload",
        [
            # Empty allowed_groups (minItems: 1)
            {
                "query_id": "req-123",
                "query": "Valid query",
                "user_permissions": {"allowed_groups": []},
            },
            # Empty query (minLength: 1)
            {
                "query_id": "req-123",
                "query": "",
                "user_permissions": {"allowed_groups": ["all-staff"]},
            },
            # Missing user_permissions
            {
                "query_id": "req-123",
                "query": "Valid query",
            },
        ],
    )
    def test_invalid_rag_retrieval_request_rejected(self, validate_openapi_schema, invalid_payload):
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "RagRetrievalRequest")

    def test_rag_retrieval_response_answer_generated(self, validate_openapi_schema):
        payload = {
            "query_id": "req-123",
            "status": "ANSWER_GENERATED",
            "answer": "To get compensation, fill out the reimbursement form...",
            "confidence_score": 0.89,
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
        validate_openapi_schema(payload, "RagRetrievalResponse")

    def test_rag_retrieval_response_empty_retrieval(self, validate_openapi_schema):
        payload = {
            "query_id": "req-123",
            "status": "EMPTY_RETRIEVAL",
            "answer": None,
            "confidence_score": 0.0,
            "citations": [],
        }
        validate_openapi_schema(payload, "RagRetrievalResponse")

    def test_rag_retrieval_response_low_confidence(self, validate_openapi_schema):
        payload = {
            "query_id": "req-123",
            "status": "LOW_CONFIDENCE",
            "answer": "Partial match found...",
            "confidence_score": 0.35,
            "citations": [],
        }
        validate_openapi_schema(payload, "RagRetrievalResponse")

    @pytest.mark.parametrize(
        "error_status",
        [
            "VALIDATION_ERROR",
            "RETRIEVAL_TIMEOUT",
            "VECTOR_DB_UNAVAILABLE",
            "LLM_PROVIDER_ERROR",
            "INTERNAL_ERROR",
        ],
    )
    def test_rag_generation_error(self, validate_openapi_schema, error_status):
        payload = {
            "query_id": "req-123",
            "status": error_status,
            "message": "Error summary",
            "description": "Detailed diagnostic message",
            "timestamp": 1730000000,
        }
        validate_openapi_schema(payload, "GenerationError")

    def test_invalid_rag_generation_error_rejected(self, validate_openapi_schema):
        invalid_payload = {
            "query_id": "req-123",
            "status": "NOT_A_VALID_STATUS",
            "message": "Error",
            "timestamp": 1730000000,
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "GenerationError")


@pytest.mark.skipif(
    not _is_service_up(RAG_BASE_URL),
    reason=f"RAG service is not reachable at {RAG_BASE_URL}",
)
class TestRagServiceLiveContract:
    """
    Live HTTP contract tests against RAG service (POST /api/v1/rag/generate).
    Automatically runs when RAG service is up on RAG_BASE_URL.
    """

    def test_post_rag_generate_returns_valid_contract(self, validate_openapi_schema):
        payload = {
            "query_id": "req-rag-live-001",
            "query": "How to get internet compensation?",
            "user_permissions": {
                "allowed_groups": ["Engineering", "All Staff", "alice@contoso-mock.onmicrosoft.com"]
            },
        }
        validate_openapi_schema(payload, "RagRetrievalRequest")

        response = httpx.post(f"{RAG_BASE_URL}{RAG_GENERATE_ENDPOINT}", json=payload, timeout=15.0)
        assert response.status_code == 200
        body = response.json()
        validate_openapi_schema(body, "RagRetrievalResponse")
        assert body["query_id"] == payload["query_id"]

    def test_post_rag_invalid_payload_returns_error_contract(self, validate_openapi_schema):
        payload = {
            "query_id": "req-rag-live-err",
            "query": "",
            "user_permissions": {"allowed_groups": []},
        }
        response = httpx.post(f"{RAG_BASE_URL}{RAG_GENERATE_ENDPOINT}", json=payload, timeout=5.0)
        assert response.status_code in (400, 422)
        body = response.json()
        validate_openapi_schema(body, "GenerationError")
        assert body["status"] == "VALIDATION_ERROR"
