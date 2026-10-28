import jsonschema
import pytest


class TestKafkaEventsContract:
    """
    Validates Kafka event payload contracts defined in contracts/openapi.yaml:
    1. rag.documents.ingestion.v1 (DocumentUpsertEvent, DocumentDeleteEvent, DocumentUpdateAclEvent)
    2. rag.chat.feedback.v1 (KafkaFeedbackEvent)
    3. rag.facts.parsed.v1 (KafkaFactParsedEvent)
    """

    # ------------------------------------------------------------------------
    # 1. Ingestion Events (rag.documents.ingestion.v1)
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize("action", ["create", "update"])
    def test_document_upsert_event_contract(self, validate_openapi_schema, action):
        payload = {
            "sync_id": "547f7ca3-1eaa-44c6-91ed-bd8f8e997cf8",
            "document_id": "doc-456",
            "action": action,
            "source_system": "sharepoint",
            "title": "IT Compensation Guidelines",
            "url": "https://company.sharepoint.com/docs/it-comp",
            "raw_content": "Full plain text payload for chunking and embedding.",
            "last_updated": "2026-10-26T16:00:00Z",
            "acl": {"allowed_groups": ["Engineering", "All Staff"]},
        }
        validate_openapi_schema(payload, "DocumentUpsertEvent")
        validate_openapi_schema(payload, "KafkaNewFileDataEvent")

    def test_document_delete_event_contract(self, validate_openapi_schema):
        payload = {
            "sync_id": "547f7ca3-1eaa-44c6-91ed-bd8f8e997cf8",
            "document_id": "doc-456",
            "action": "delete",
        }
        validate_openapi_schema(payload, "DocumentDeleteEvent")
        validate_openapi_schema(payload, "KafkaNewFileDataEvent")

    def test_document_update_acl_event_contract(self, validate_openapi_schema):
        payload = {
            "sync_id": "547f7ca3-1eaa-44c6-91ed-bd8f8e997cf8",
            "document_id": "doc-456",
            "action": "update_acl",
            "acl": {"allowed_groups": ["Engineering - Backend"]},
        }
        validate_openapi_schema(payload, "DocumentUpdateAclEvent")
        validate_openapi_schema(payload, "KafkaNewFileDataEvent")

    def test_invalid_ingestion_event_rejected(self, validate_openapi_schema):
        invalid_payload = {
            "sync_id": "547f7ca3-1eaa-44c6-91ed-bd8f8e997cf8",
            "document_id": "doc-456",
            "action": "unknown_action",
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "KafkaNewFileDataEvent")

    # ------------------------------------------------------------------------
    # 2. Chat Feedback Event (rag.chat.feedback.v1)
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("rating", "comment"),
        [
            ("thumbs_up", "Very helpful answer!"),
            ("thumbs_down", None),
        ],
    )
    def test_kafka_feedback_event_contract(self, validate_openapi_schema, rating, comment):
        payload = {
            "query_id": "req-123",
            "user_email": "oleh.pona@company.com",
            "query": "How to get internet compensation?",
            "rating": rating,
            "comment": comment,
            "timestamp": 1730000000,
        }
        validate_openapi_schema(payload, "KafkaFeedbackEvent")

    def test_invalid_feedback_rating_rejected(self, validate_openapi_schema):
        invalid_payload = {
            "query_id": "req-123",
            "user_email": "oleh.pona@company.com",
            "query": "How to get internet compensation?",
            "rating": "five_stars",
            "timestamp": 1730000000,
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "KafkaFeedbackEvent")

    # ------------------------------------------------------------------------
    # 3. Parsed Facts Event (rag.facts.parsed.v1)
    # ------------------------------------------------------------------------

    def test_kafka_fact_parsed_event_contract(self, validate_openapi_schema):
        payload = {
            "file_id": "doc-456",
            "chunk_hash": "a1b2c3d4e5f6",
            "entity": "Vacation",
            "attribute": "duration",
            "value": "24",
            "conditions": ["contract:b2b", "role:senior"],
        }
        validate_openapi_schema(payload, "KafkaFactParsedEvent")
        # Contract business rule: conditions MUST be sorted alphabetically
        assert payload["conditions"] == sorted(payload["conditions"])
