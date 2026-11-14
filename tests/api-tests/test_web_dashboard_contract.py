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
        ("outcome", "error_category", "confidence_score"),
        [
            ("SUCCESS", None, 0.91),
            ("FAILURE", "INTERNAL_CONNECTION_ERROR", None),
            ("FAILURE", "LLM_PROVIDER_ERROR", 0.0),
            ("FAILURE", "RETRIEVAL_TIMEOUT", 0.25),
        ],
    )
    def test_valid_generation_logs_page(
        self, validate_openapi_schema, outcome, error_category, confidence_score
    ):
        payload = {
            "items": [
                {
                    "id": "123e4567-e89b-12d3-a456-426614174001",
                    "query_id": "req-123",
                    "outcome": outcome,
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


class TestWebDashboardEndpointSpecs:
    """Verifies query parameters (pagination & log date filters) and security rules in openapi.yaml."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/dash/logs/ingestion",
            "/api/v1/dash/logs/generation",
            "/api/v1/dash/documents",
            "/api/v1/dash/users",
            "/api/v1/dash/operators",
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
