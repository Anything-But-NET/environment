import jsonschema
import pytest


def valid_escalation_request():
    return {
        "question_id": "123e4567-e89b-12d3-a456-426614174000",
        "correlation_id": "req-123",
        "question": {
            "text": "How do I request access to the finance system?",
            "channel": "teams",
            "asked_at": "2026-11-17T14:20:00Z",
        },
        "requester": {"user_email": "alex@example.com"},
        "user_confirmation": {
            "confirmed": True,
            "confirmed_at": "2026-11-17T14:22:00Z",
            "confirmed_by": "alex@example.com",
            "confirmed_via": "teams",
        },
        "answer_context": {
            "answer_text": None,
            "confidence_score": 0.22,
            "source_references": [],
        },
        "escalation_reason": "NO_RELEVANT_CONTEXT",
        "escalation_reason_details": "No permitted source answered the question.",
    }


def valid_escalation_response():
    return {
        "escalation_id": "987e6543-e21b-12d3-a456-426614174000",
        "question_id": "123e4567-e89b-12d3-a456-426614174000",
        "correlation_id": "req-123",
        "status": "PENDING_DESTINATION",
        "destination_reference": None,
        "created_at": "2026-11-17T14:22:01Z",
    }


def test_valid_escalation_create_request(validate_openapi_schema):
    validate_openapi_schema(valid_escalation_request(), "EscalationCreateRequest")


def test_user_question_response_requires_stable_question_id(validate_openapi_schema):
    payload = {
        "query_id": "req-123",
        "question_id": "123e4567-e89b-12d3-a456-426614174000",
        "status": "ANSWERED",
        "reply_text": "The access request is submitted through the internal portal.",
        "citations": [],
    }
    validate_openapi_schema(payload, "UserQuestionResponse")
    payload.pop("question_id")
    with pytest.raises(jsonschema.ValidationError):
        validate_openapi_schema(payload, "UserQuestionResponse")


def test_escalation_request_requires_explicit_confirmation(validate_openapi_schema):
    payload = valid_escalation_request()
    payload["user_confirmation"]["confirmed"] = False

    with pytest.raises(jsonschema.ValidationError):
        validate_openapi_schema(payload, "EscalationCreateRequest")


def test_escalation_request_rejects_destination_specific_fields(validate_openapi_schema):
    payload = valid_escalation_request()
    payload["destination"] = "some-ticketing-vendor"

    with pytest.raises(jsonschema.ValidationError):
        validate_openapi_schema(payload, "EscalationCreateRequest")


def test_escalation_request_rejects_invalid_confidence(validate_openapi_schema):
    payload = valid_escalation_request()
    payload["answer_context"]["confidence_score"] = 1.5

    with pytest.raises(jsonschema.ValidationError):
        validate_openapi_schema(payload, "EscalationCreateRequest")


def test_escalation_request_accepts_source_references(validate_openapi_schema):
    payload = valid_escalation_request()
    payload["answer_context"]["source_references"] = [
        {
            "document_id": "doc-456",
            "chunk_id": "123e4567-e89b-12d3-a456-426614174001",
            "title": "Finance access policy",
            "url": "https://example.com/finance-access",
            "score": 0.71,
        }
    ]

    validate_openapi_schema(payload, "EscalationCreateRequest")


@pytest.mark.parametrize("status", ["PENDING_DESTINATION", "SUBMITTED", "DELIVERY_FAILED"])
def test_valid_escalation_create_response(validate_openapi_schema, status):
    payload = valid_escalation_response()
    payload["status"] = status
    validate_openapi_schema(payload, "EscalationCreateResponse")


def test_escalation_error_response(validate_openapi_schema):
    payload = {
        "error_code": "IDEMPOTENCY_KEY_REUSED",
        "message": "Idempotency-Key was already used for a different request.",
        "correlation_id": "req-123",
        "timestamp": "2026-11-17T14:22:01Z",
    }

    validate_openapi_schema(payload, "EscalationErrorResponse")


def test_escalation_operation_defines_idempotency_and_expected_responses(openapi_spec):
    operation = openapi_spec["paths"]["/api/v1/escalations"]["post"]
    parameters = operation["parameters"]
    idempotency_header = next(
        parameter
        for parameter in parameters
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )

    assert idempotency_header["required"] is True
    assert {"200", "201", "400", "409", "422", "503"}.issubset(operation["responses"])
