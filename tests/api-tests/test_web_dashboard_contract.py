import jsonschema
import pytest


def _resolve_param(param: dict, openapi_spec: dict) -> dict:
    if "$ref" in param:
        name = param["$ref"].split("/")[-1]
        return openapi_spec["components"]["parameters"][name]
    return param


class TestWebDashboardAuthContracts:
    """Validates Web Dashboard auth schemas in contracts/openapi.yaml."""

    def test_valid_login_request(self, validate_openapi_schema):
        payload = {"email": "oleh.pona@company.com", "password": "securepassword123"}
        validate_openapi_schema(payload, "LoginRequest")

    @pytest.mark.parametrize(
        "invalid_payload",
        [
            {"email": "not-an-email", "password": "secret"},
            {"email": "oleh.pona@company.com", "password": ""},
            {"email": "oleh.pona@company.com"},
        ],
    )
    def test_invalid_login_request_rejected(self, validate_openapi_schema, invalid_payload):
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "LoginRequest")

    def test_valid_refresh_token_request(self, validate_openapi_schema):
        validate_openapi_schema({"refresh_token": "eyJhbGciOiJIUzI1NiJ9.abc.xyz"}, "RefreshTokenRequest")

    def test_valid_auth_tokens_response(self, validate_openapi_schema):
        payload = {
            "access_token": "eyJhbGciOiJIUzI1NiJ9.access",
            "access_expires_in": 900,
            "refresh_token": "eyJhbGciOiJIUzI1NiJ9.refresh",
            "refresh_expires_in": 604800,
        }
        validate_openapi_schema(payload, "AuthTokensResponse")

    @pytest.mark.parametrize(
        "invalid_payload",
        [
            {
                "access_token": "abc",
                "access_expires_in": 0,
                "refresh_token": "def",
                "refresh_expires_in": 604800,
            },
            {
                "access_token": "abc",
                "access_expires_in": 900,
                "refresh_token": "def",
                "refresh_expires_in": 0,
            },
        ],
    )
    def test_invalid_auth_expires_in_rejected(self, validate_openapi_schema, invalid_payload):
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "AuthTokensResponse")


class TestWebDashboardDataContracts:
    """Validates paginated logs, documents, users, and operators schemas."""

    @pytest.mark.parametrize(
        ("status", "finished_at", "error_message"),
        [
            ("IN_PROGRESS", None, None),
            ("COMPLETED", "2026-11-13T12:05:00Z", None),
            ("FAILED_FETCH", "2026-11-13T12:05:00Z", "403 Forbidden from Graph API"),
            ("FAILED_PARSE", "2026-11-13T12:05:00Z", "Corrupted PDF"),
            ("FAILED_STORAGE", "2026-11-13T12:05:00Z", "pgvector connection lost"),
        ],
    )
    def test_valid_ingestion_logs_page(self, validate_openapi_schema, status, finished_at, error_message):
        payload = {
            "items": [
                {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "document_id": "doc-456",
                    "status": status,
                    "error_message": error_message,
                    "started_at": "2026-11-13T12:00:00Z",
                    "finished_at": finished_at,
                }
            ],
            "page": 0,
            "size": 20,
            "total_elements": 1,
            "total_pages": 1,
        }
        validate_openapi_schema(payload, "IngestionLogsPage")

    @pytest.mark.parametrize(
        ("outcome", "reason_code", "error_category", "confidence_score"),
        [
            ("ANSWERED", None, None, 0.91),
            ("SAFE_NO_ANSWER", "LOW_CONFIDENCE", None, 0.31),
            ("ESCALATED", None, None, 0.24),
            ("FAILURE", None, "INTERNAL_CONNECTION_ERROR", None),
            ("FAILURE", None, "LLM_PROVIDER_ERROR", 0.0),
            ("FAILURE", None, "RETRIEVAL_TIMEOUT", 0.25),
        ],
    )
    def test_valid_generation_logs_page(
            self, validate_openapi_schema, outcome, reason_code, error_category, confidence_score
    ):
        payload = {
            "items": [
                {
                    "id": "123e4567-e89b-12d3-a456-426614174001",
                    "query_id": "req-123",
                    "outcome": outcome,
                    "reason_code": reason_code,
                    "error_category": error_category,
                    "confidence_score": confidence_score,
                    "created_at": "2026-11-13T12:00:00Z",
                }
            ],
            "page": 0,
            "size": 20,
            "total_elements": 1,
            "total_pages": 1,
        }
        validate_openapi_schema(payload, "GenerationLogsPage")

    def test_valid_documents_page(self, validate_openapi_schema):
        payload = {
            "items": [
                {
                    "document_id": "doc-456",
                    "source_system": "sharepoint",
                    "title": "IT Compensation Guidelines",
                    "url": "https://company.sharepoint.com/docs/it-comp",
                    "last_updated": "2026-11-10T10:00:00Z",
                    "last_sync_at": "2026-11-13T12:00:00Z",
                }
            ],
            "page": 0,
            "size": 20,
            "total_elements": 1,
            "total_pages": 1,
        }
        validate_openapi_schema(payload, "DocumentsPage")

    def test_valid_users_page_allows_empty_groups(self, validate_openapi_schema):
        payload = {
            "items": [
                {
                    "email": "oleh.pona@company.com",
                    "allowed_groups": [],
                    "last_updated": None,
                    "last_sync_at": "2026-11-13T12:00:00Z",
                }
            ],
            "page": 0,
            "size": 20,
            "total_elements": 1,
            "total_pages": 1,
        }
        validate_openapi_schema(payload, "UsersPage")

    def test_valid_operators_page_and_status_update(self, validate_openapi_schema):
        operator = {
            "id": "123e4567-e89b-12d3-a456-426614174002",
            "email": "admin@company.com",
            "authorities": ["LOGS", "METADATA", "USERS", "OPERATORS"],
            "status": "ACTIVE",
            "last_refreshed_at": "2026-11-13T12:00:00Z",
            "last_updated_at": "2026-11-13T12:00:00Z",
            "created_at": "2026-11-01T08:00:00Z",
        }
        validate_openapi_schema(
            {"items": [operator], "page": 0, "size": 20, "total_elements": 1, "total_pages": 1},
            "OperatorsPage",
        )
        validate_openapi_schema({"status": "BLOCKED"}, "UpdateOperatorStatusRequest")

    def test_operator_item_rejects_password_hash_leak(self, validate_openapi_schema):
        leaked_operator = {
            "id": "123e4567-e89b-12d3-a456-426614174002",
            "email": "admin@company.com",
            "authorities": ["OPERATORS"],
            "status": "ACTIVE",
            "password_hash": "$2a$10$secret",
            "last_refreshed_at": "2026-11-13T12:00:00Z",
            "last_updated_at": "2026-11-13T12:00:00Z",
            "created_at": "2026-11-01T08:00:00Z",
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(leaked_operator, "OperatorItem")

    @pytest.mark.parametrize("invalid_status", ["active", "blocked", "DELETED"])
    def test_update_operator_status_rejects_invalid_enum(self, validate_openapi_schema, invalid_status):
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema({"status": invalid_status}, "UpdateOperatorStatusRequest")

    def test_dashboard_error_response(self, validate_openapi_schema):
        validate_openapi_schema(
            {
                "error_code": "FORBIDDEN",
                "message": "Operator account is blocked",
                "timestamp": "2026-11-13T12:00:00Z",
            },
            "DashboardErrorResponse",
        )

    def test_valid_escalations_page_and_detail(self, validate_openapi_schema):
        item = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "query_id": "req-123",
            "requester_email": "oleh.pona@company.com",
            "question_text": "How to get internet compensation?",
            "status": "PENDING",
            "channel": "slack",
            "reason_code": "LOW_CONFIDENCE",
            "confidence_score": 0.31,
            "created_at": "2026-11-23T10:15:30Z",
            "updated_at": "2026-11-23T10:15:30Z",
            "resolved_at": None,
        }
        validate_openapi_schema(
            {"items": [item], "page": 0, "size": 20, "total_elements": 1, "total_pages": 1},
            "EscalationsPage",
        )
        detail = {
            **item,
            "status": "RESOLVED",
            "resolved_at": "2026-11-23T11:00:00Z",
            "source_references": [
                {
                    "title": "IT Compensation Guidelines",
                    "url": "https://company.sharepoint.com/docs/it-comp",
                    "document_id": "doc-456",
                    "chunk_id": "123e4567-e89b-12d3-a456-426614174000",
                    "score": 0.42,
                }
            ],
            "status_history": [
                {
                    "id": "660e8400-e29b-41d4-a716-446655440001",
                    "from_status": None,
                    "to_status": "PENDING",
                    "changed_by_operator_id": None,
                    "changed_at": "2026-11-23T10:15:30Z",
                },
                {
                    "id": "660e8400-e29b-41d4-a716-446655440002",
                    "from_status": "PENDING",
                    "to_status": "RESOLVED",
                    "changed_by_operator_id": "123e4567-e89b-12d3-a456-426614174002",
                    "changed_at": "2026-11-23T11:00:00Z",
                },
            ],
        }
        validate_openapi_schema(detail, "EscalationDetail")
        validate_openapi_schema({"status": "IN_PROGRESS"}, "UpdateEscalationStatusRequest")

    def test_valid_escalation_with_recorded_response_and_save_request(self, validate_openapi_schema):
        operator_id = "123e4567-e89b-12d3-a456-426614174002"
        item_with_response = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "query_id": "req-123",
            "requester_email": "oleh.pona@company.com",
            "question_text": "How to get internet compensation?",
            "status": "RESOLVED",
            "resolution_text": "Submit the IT reimbursement form in the Finance portal.",
            "resolution_updated_at": "2026-11-23T11:00:00Z",
            "resolved_by_operator_id": operator_id,
            "channel": "slack",
            "reason_code": "LOW_CONFIDENCE",
            "confidence_score": 0.31,
            "created_at": "2026-11-23T10:15:30Z",
            "updated_at": "2026-11-23T11:00:00Z",
            "resolved_at": "2026-11-23T11:00:00Z",
        }
        validate_openapi_schema(
            {"items": [item_with_response], "page": 0, "size": 20, "total_elements": 1, "total_pages": 1},
            "EscalationsPage",
        )
        validate_openapi_schema(
            {
                **item_with_response,
                "source_references": [],
                "status_history": [
                    {
                        "id": "660e8400-e29b-41d4-a716-446655440002",
                        "from_status": "IN_PROGRESS",
                        "to_status": "RESOLVED",
                        "changed_by_operator_id": operator_id,
                        "changed_at": "2026-11-23T11:00:00Z",
                    }
                ],
            },
            "EscalationDetail",
        )
        validate_openapi_schema(
            {"resolution_text": "Submit the IT reimbursement form in the Finance portal."},
            "SaveEscalationResponseRequest",
        )
        validate_openapi_schema(
            {
                "resolution_text": "Submit the IT reimbursement form in the Finance portal.",
                "status": "RESOLVED",
            },
            "SaveEscalationResponseRequest",
        )

    @pytest.mark.parametrize(
        "invalid_payload",
        [
            {},
            {"resolution_text": ""},
            {"resolution_text": "Answer", "status": "UNKNOWN"},
            {"resolution_text": "Answer", "extra": "not-allowed"},
        ],
    )
    def test_save_escalation_response_rejects_invalid_payload(self, validate_openapi_schema, invalid_payload):
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "SaveEscalationResponseRequest")

    def test_update_escalation_status_rejects_invalid_enum(self, validate_openapi_schema):
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema({"status": "REOPENED"}, "UpdateEscalationStatusRequest")

    def test_valid_question_outcomes_analytics_response(self, validate_openapi_schema):
        payload = {
            "from": "2026-11-18T00:00:00Z",
            "to": "2026-11-21T00:00:00Z",
            "total_questions": 100,
            "outcome_counts": [
                {"outcome": "ANSWERED", "count": 72},
                {"outcome": "SAFE_NO_ANSWER", "count": 15},
                {"outcome": "ESCALATED", "count": 10},
                {"outcome": "FAILURE", "count": 3},
            ],
            "avg_confidence_score": 0.78,
            "confidence_buckets": {
                "high": 65,
                "medium": 20,
                "low": 12,
            },
            "avg_latency_ms": 1420.5,
            "p95_latency_ms": 3180.0,
            "reason_code_counts": [
                {"reason_code": "LOW_CONFIDENCE", "count": 12},
                {"reason_code": "EMPTY_RETRIEVAL", "count": 8},
            ],
            "error_category_counts": [
                {"error_category": "RETRIEVAL_TIMEOUT", "count": 2},
                {"error_category": "LLM_PROVIDER_ERROR", "count": 1},
            ],
            "channel_counts": [
                {"channel": "slack", "count": 92},
                {"channel": "teams", "count": 8},
            ],
            "daily_trend": [
                {
                    "date": "2026-11-18",
                    "total_questions": 30,
                    "outcome_counts": [
                        {"outcome": "ANSWERED", "count": 22},
                        {"outcome": "SAFE_NO_ANSWER", "count": 5},
                        {"outcome": "ESCALATED", "count": 2},
                        {"outcome": "FAILURE", "count": 1},
                    ],
                },
                {
                    "date": "2026-11-19",
                    "total_questions": 40,
                    "outcome_counts": [
                        {"outcome": "ANSWERED", "count": 28},
                        {"outcome": "SAFE_NO_ANSWER", "count": 6},
                        {"outcome": "ESCALATED", "count": 5},
                        {"outcome": "FAILURE", "count": 1},
                    ],
                },
                {
                    "date": "2026-11-20",
                    "total_questions": 30,
                    "outcome_counts": [
                        {"outcome": "ANSWERED", "count": 22},
                        {"outcome": "SAFE_NO_ANSWER", "count": 4},
                        {"outcome": "ESCALATED", "count": 3},
                        {"outcome": "FAILURE", "count": 1},
                    ],
                },
            ],
        }
        validate_openapi_schema(payload, "QuestionOutcomesAnalyticsResponse")

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "schema_version": "1.0",
                "query_id": "req-123",
                "channel": "slack",
                "timestamp": "2026-11-19T14:22:10Z",
                "outcome": "ANSWERED",
                "confidence_score": 0.91,
                "latency_ms": 1150,
            },
            {
                "schema_version": "1.0",
                "query_id": "Ev0CHR36TQHZ",
                "channel": "slack",
                "timestamp": "2026-11-19T14:25:03Z",
                "outcome": "SAFE_NO_ANSWER",
                "escalation_id": None,
                "resolution_status": None,
                "reason_code": "LOW_CONFIDENCE",
                "confidence_score": 0.31,
                "latency_ms": 1420,
                "prompt_version_id": "550e8400-e29b-41d4-a716-446655440000",
            },
            {
                "schema_version": "1.0",
                "query_id": "req-456",
                "channel": "teams",
                "timestamp": "2026-11-19T14:26:41Z",
                "outcome": "ESCALATED",
                "escalation_id": "ESC-2026-00419",
                "resolution_status": "PENDING",
                "reason_code": None,
                "confidence_score": 0.24,
                "latency_ms": 1890,
            },
            {
                "schema_version": "1.0",
                "query_id": "req-789",
                "timestamp": "2026-11-19T14:30:00Z",
                "outcome": "FAILURE",
            },
        ],
    )
    def test_valid_question_outcome_event(self, validate_shared_schema, payload):
        validate_shared_schema(payload, "QuestionOutcomeEvent")

    @pytest.mark.parametrize(
        "invalid_payload",
        [
            {
                "schema_version": "1.0",
                "query_id": "req-123",
                "timestamp": "2026-11-19T14:22:10Z",
                "outcome": "ANSWERED",
                "query": "raw user question text",
            },
            {
                "schema_version": "1.0",
                "query_id": "req-123",
                "timestamp": "2026-11-19T14:22:10Z",
                "outcome": "SUCCESS",
            },
            {
                "schema_version": "2.0",
                "query_id": "req-123",
                "timestamp": "2026-11-19T14:22:10Z",
                "outcome": "ANSWERED",
            },
        ],
    )
    def test_invalid_question_outcome_event_rejected(self, validate_shared_schema, invalid_payload):
        with pytest.raises(jsonschema.ValidationError):
            validate_shared_schema(invalid_payload, "QuestionOutcomeEvent")

    def test_valid_prompt_versions_page_and_detail(self, validate_openapi_schema):
        page_payload = {
            "items": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "created_at": "2026-11-19T14:25:03Z",
                }
            ],
            "page": 0,
            "size": 20,
            "total_elements": 1,
            "total_pages": 1,
        }
        validate_openapi_schema(page_payload, "PromptVersionsPage")

        detail_payload = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "prompt_text": "You are a helpful internal IT assistant. Answer strictly using the provided context.",
            "metadata": {"version": "v1.2", "author": "oleh"},
            "created_at": "2026-11-19T14:25:03Z",
        }
        validate_openapi_schema(detail_payload, "PromptVersionDetail")

    def test_valid_token_introspect_contracts(self, validate_openapi_schema):
        validate_openapi_schema({"token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}, "TokenIntrospectRequest")
        validate_openapi_schema(
            {
                "active": True,
                "operator_id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "oleh.pona@company.com",
                "status": "ACTIVE",
                "authorities": ["PROMPTS", "ANALYTICS"],
            },
            "TokenIntrospectResponse",
        )
        validate_openapi_schema(
            {
                "active": False,
                "operator_id": None,
                "email": None,
                "status": None,
                "authorities": [],
            },
            "TokenIntrospectResponse",
        )


class TestWebDashboardEndpointSpecs:
    """Verifies query parameters (pagination & log date filters) and security rules in openapi.yaml."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/dash/logs/ingestion",
            "/api/v1/dash/logs/generation",
            "/api/v1/dash/prompts",
            "/api/v1/dash/documents",
            "/api/v1/dash/users",
            "/api/v1/dash/operators",
            "/api/v1/dash/escalations",
        ],
    )
    def test_list_endpoints_have_spring_pagination_and_bearer_auth(self, openapi_spec, path):
        op = openapi_spec["paths"][path]["get"]
        assert op.get("security") == [{"bearerAuth": []}]

        params = {_resolve_param(p, openapi_spec)["name"]: _resolve_param(p, openapi_spec) for p in op["parameters"]}
        assert params["page"]["in"] == "query"
        assert params["page"]["schema"]["default"] == 0
        assert params["size"]["in"] == "query"
        assert params["size"]["schema"]["default"] == 20

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/dash/logs/ingestion",
            "/api/v1/dash/logs/generation",
            "/api/v1/dash/analytics/outcomes",
            "/api/v1/dash/analytics/prompts/{prompt_version_id}/outcomes",
            "/api/v1/dash/escalations",
        ],
    )
    def test_log_endpoints_support_from_and_to_filters(self, openapi_spec, path):
        op = openapi_spec["paths"][path]["get"]
        params = {_resolve_param(p, openapi_spec)["name"]: _resolve_param(p, openapi_spec) for p in op["parameters"]}
        assert params["from"]["in"] == "query"
        assert params["from"]["schema"]["format"] == "date-time"
        assert params["to"]["in"] == "query"
        assert params["to"]["schema"]["format"] == "date-time"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/dash/auth/login",
            "/api/v1/dash/auth/refresh",
        ],
    )
    def test_auth_endpoints_are_public_with_matching_error_examples(self, openapi_spec, path):
        op = openapi_spec["paths"][path]["post"]
        assert "security" not in op
        assert op["responses"]["401"]["content"]["application/json"]["example"]["error_code"] == "UNAUTHORIZED"
        assert op["responses"]["403"]["content"]["application/json"]["example"]["error_code"] == "FORBIDDEN"

    @pytest.mark.parametrize(
        ("response_name", "expected_error_code"),
        [
            ("DashboardBadRequest", "BAD_REQUEST"),
            ("DashboardUnauthorized", "UNAUTHORIZED"),
            ("DashboardForbidden", "FORBIDDEN"),
            ("DashboardNotFound", "NOT_FOUND"),
        ],
    )
    def test_dashboard_error_responses_have_matching_examples(
        self, openapi_spec, response_name, expected_error_code
    ):
        resp = openapi_spec["components"]["responses"][response_name]
        example = resp["content"]["application/json"]["example"]
        assert example["error_code"] == expected_error_code
