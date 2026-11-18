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

    def test_valid_evaluation_run_create_request(self, validate_openapi_schema):
        payload = {
            "prompt_version_id": "123e4567-e89b-12d3-a456-426614174000",
            "dataset": {
                "dataset_id": "mvp-golden-set",
                "dataset_version": "v1.0.0",
                "dataset_path": "datasets/golden_set_v1.json",
                "known_limitations": ["Small sample of conflicting source cases"],
            },
            "thresholds": {
                "decision_status": "PROPOSED",
                "relevance_min_score": 0.75,
                "factual_grounding_min_score": 0.85,
                "citation_correctness_min_score": 1.0,
                "target_pass_rate": 0.85,
            },
        }
        validate_openapi_schema(payload, "EvaluationRunCreateRequest")

    def test_invalid_evaluation_thresholds_rejected(self, validate_openapi_schema):
        invalid_payload = {
            "prompt_version_id": "123e4567-e89b-12d3-a456-426614174000",
            "dataset": {"dataset_id": "mvp-golden-set", "dataset_version": "v1.0.0"},
            "thresholds": {
                "decision_status": "SILENTLY_ACCEPTED",
                "relevance_min_score": 1.5,
                "factual_grounding_min_score": 0.85,
                "citation_correctness_min_score": 1.0,
                "target_pass_rate": 0.85,
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "EvaluationRunCreateRequest")

    def test_valid_evaluation_run_response(self, validate_openapi_schema):
        check = {
            "applicable": True,
            "passed": True,
            "score": 0.9,
            "threshold": 0.75,
            "calculation_method": "token_overlap_v1",
            "details": None,
        }
        payload = {
            "id": "123e4567-e89b-12d3-a456-426614174001",
            "prompt_version_id": "123e4567-e89b-12d3-a456-426614174000",
            "status": "completed",
            "created_at": "2026-11-18T18:00:00Z",
            "metadata": {
                "dataset": {
                    "dataset_id": "mvp-golden-set",
                    "dataset_version": "v1.0.0",
                    "dataset_path": None,
                    "known_limitations": [],
                },
                "thresholds": {
                    "decision_status": "PROPOSED",
                    "relevance_min_score": 0.75,
                    "factual_grounding_min_score": 0.85,
                    "citation_correctness_min_score": 1.0,
                    "target_pass_rate": 0.85,
                },
                "total_cases": 1,
                "passed_cases": 1,
                "failed_cases": 0,
                "overall_pass_rate": 1.0,
                "dimension_summaries": [
                    {
                        "dimension": "relevance",
                        "evaluated_cases_count": 1,
                        "passed_cases_count": 1,
                        "observed_pass_rate": 1.0,
                        "observed_mean_score": 0.9,
                        "proposed_threshold_score": 0.75,
                        "proposed_target_pass_rate": 0.85,
                        "threshold_decision_status": "PROPOSED",
                        "meets_proposed_threshold": True,
                    }
                ],
                "followup_jira_issues": [],
                "case_results": [
                    {
                        "case_id": "GS-001",
                        "query": "How does CI pipeline run?",
                        "expected_outcome": "ANSWER_EXPECTED",
                        "expected_document_ids": ["doc-ci-01"],
                        "actual_status": "ANSWER_GENERATED",
                        "actual_answer": "CI runs on GitHub Actions.",
                        "actual_confidence_score": 0.92,
                        "actual_citations": [
                            {
                                "document_id": "doc-ci-01",
                                "chunk_id": "123e4567-e89b-12d3-a456-426614174099",
                                "score": 0.92,
                            }
                        ],
                        "passed": True,
                        "checks": {
                            "relevance": check,
                            "factual_grounding": check,
                            "citation_correctness": check,
                            "safe_no_answer": {**check, "applicable": False, "score": 1.0, "threshold": 1.0},
                        },
                        "failure_reasons": [],
                    }
                ],
            },
        }
        validate_openapi_schema(payload, "EvaluationRunResponse")


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

    def test_get_non_existent_evaluation_run_returns_404_error_contract(self, validate_openapi_schema):
        non_existent_id = "00000000-0000-0000-0000-000000000099"
        response = httpx.get(
            f"{RAG_BASE_URL}/api/v1/rag/evaluations/runs/{non_existent_id}",
            timeout=5.0,
        )
        assert response.status_code == 404
        body = response.json()
        validate_openapi_schema(body, "GenerationError")

