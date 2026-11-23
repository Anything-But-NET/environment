import os

import httpx
import jsonschema
import pytest

CORE_BASE_URL = os.getenv("CORE_BASE_URL", "http://localhost:8080")
RAG_BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:8000")
GATEWAY_BASE_URL = os.getenv("GATEWAY_BASE_URL", "http://localhost:5224")


def _is_service_up(base_url: str, path: str = "/health") -> bool:
    try:
        httpx.get(f"{base_url}{path}", timeout=5.0)
        return True
    except httpx.RequestError:
        return False


class TestHealthCheckContractSchemas:
    """
    Validates SCRUM-97 health check schemas in contracts/openapi.yaml
    (ServiceHealthResponse, RagHealthResponse, CoreHealthResponse, GatewayHealthResponse).
    """

    @pytest.mark.parametrize("overall_status", ["UP", "DEGRADED", "DOWN"])
    def test_valid_rag_health_response(self, validate_openapi_schema, overall_status):
        payload = {
            "status": overall_status,
            "components": {
                "db": {"status": "UP"},
                "vectorizer": {"status": "UP"},
                "kafka": {"status": "DOWN" if overall_status != "UP" else "UP", "details": {"reason": "broker offline"}},
                "llm": {"status": "UP", "details": {"configured": True}},
            },
        }
        validate_openapi_schema(payload, "ServiceHealthResponse")
        validate_openapi_schema(payload, "RagHealthResponse")

    def test_invalid_rag_health_missing_component_rejected(self, validate_openapi_schema):
        payload = {
            "status": "UP",
            "components": {
                "db": {"status": "UP"},
                "vectorizer": {"status": "UP"},
                "kafka": {"status": "UP"},
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(payload, "RagHealthResponse")

    @pytest.mark.parametrize("overall_status", ["UP", "DEGRADED", "DOWN"])
    def test_valid_core_health_response(self, validate_openapi_schema, overall_status):
        payload = {
            "status": overall_status,
            "components": {
                "db": {"status": "UP", "details": {"database": "PostgreSQL"}},
                "kafka": {"status": "UP"},
                "pythonService": {"status": overall_status},
                "ping": {"status": "UP"},
            },
        }
        validate_openapi_schema(payload, "ServiceHealthResponse")
        validate_openapi_schema(payload, "CoreHealthResponse")

    def test_invalid_core_health_missing_python_service_rejected(self, validate_openapi_schema):
        payload = {
            "status": "UP",
            "components": {
                "db": {"status": "UP"},
                "kafka": {"status": "UP"},
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(payload, "CoreHealthResponse")

    @pytest.mark.parametrize("overall_status", ["UP", "DEGRADED", "DOWN"])
    def test_valid_gateway_health_response(self, validate_openapi_schema, overall_status):
        payload = {
            "status": overall_status,
            "components": {
                "javaCore": {"status": "UP"},
                "kafka": {"status": "UP"},
                "slack": {"status": "UP"},
                "graph": {"status": "UP"},
            },
        }
        validate_openapi_schema(payload, "ServiceHealthResponse")
        validate_openapi_schema(payload, "GatewayHealthResponse")

    def test_invalid_health_status_rejected(self, validate_openapi_schema):
        payload = {
            "status": "OK",
            "components": {
                "db": {"status": "UP"},
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(payload, "ServiceHealthResponse")

    def test_openapi_spec_declares_health_paths(self, openapi_spec):
        paths = openapi_spec["paths"]
        assert "/health" in paths
        assert "get" in paths["/health"]
        assert "/actuator/health" in paths
        assert "get" in paths["/actuator/health"]


@pytest.mark.skipif(
    not _is_service_up(RAG_BASE_URL, "/health"),
    reason=f"RAG service is not reachable at {RAG_BASE_URL}/health",
)
class TestRagHealthLiveContract:
    def test_rag_health_matches_contract(self, validate_openapi_schema):
        response = httpx.get(f"{RAG_BASE_URL}/health", timeout=5.0)
        assert response.status_code in (200, 503)
        body = response.json()
        validate_openapi_schema(body, "RagHealthResponse")


@pytest.mark.skipif(
    not _is_service_up(CORE_BASE_URL, "/actuator/health"),
    reason=f"Core service is not reachable at {CORE_BASE_URL}/actuator/health",
)
class TestCoreHealthLiveContract:
    def test_core_health_matches_contract(self, validate_openapi_schema):
        response = httpx.get(f"{CORE_BASE_URL}/actuator/health", timeout=5.0)
        assert response.status_code in (200, 503)
        body = response.json()
        validate_openapi_schema(body, "CoreHealthResponse")


@pytest.mark.skipif(
    not _is_service_up(GATEWAY_BASE_URL, "/health"),
    reason=f"Gateway service is not reachable at {GATEWAY_BASE_URL}/health",
)
class TestGatewayHealthLiveContract:
    def test_gateway_health_matches_contract(self, validate_openapi_schema):
        response = httpx.get(f"{GATEWAY_BASE_URL}/health", timeout=5.0)
        assert response.status_code in (200, 503)
        body = response.json()
        validate_openapi_schema(body, "GatewayHealthResponse")
