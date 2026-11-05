import jsonschema
import pytest


class TestKafkaEventsContract:
    """
    Validates Kafka event payload contracts defined in contracts/openapi.yaml:
    1. rag.documents.ingestion.v1 (DocumentUpsertEvent, DocumentDeleteEvent, DocumentUpdateAclEvent)
    2. rag.chat.feedback.v1 (KafkaFeedbackEvent)
    3. rag.facts.parsed.v1 (KafkaFactParsedEvent)
    4. rag.sync.status.v1 (KafkaSyncStatusEvent)
    5. rag.users.acl.v1 (KafkaUserAclUpsert)
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
            "last_updated": "2026-10-26T16:00:00Z",
        }
        validate_openapi_schema(payload, "DocumentDeleteEvent")
        validate_openapi_schema(payload, "KafkaNewFileDataEvent")

    def test_document_update_acl_event_contract(self, validate_openapi_schema):
        payload = {
            "sync_id": "547f7ca3-1eaa-44c6-91ed-bd8f8e997cf8",
            "document_id": "doc-456",
            "action": "update_acl",
            "last_updated": "2026-10-26T16:00:00Z",
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

    # ------------------------------------------------------------------------
    # 4. Sync Status Event (rag.sync.status.v1)
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("status", "error_message"),
        [
            ("IN_PROGRESS", None),
            ("FAILED_FETCH", "SharePoint API connection timeout"),
            ("FAILED_PARSE", "Unsupported PDF format"),
        ],
    )
    def test_kafka_sync_status_event_contract(self, validate_openapi_schema, status, error_message):
        payload = {
            "sync_id": "547f7ca3-1eaa-44c6-91ed-bd8f8e997cf8",
            "document_id": "doc-456",
            "status": status,
            "error_message": error_message,
            "timestamp": 1730000000,
        }
        validate_openapi_schema(payload, "KafkaSyncStatusEvent")

    def test_invalid_sync_status_rejected(self, validate_openapi_schema):
        invalid_payload = {
            "sync_id": "547f7ca3-1eaa-44c6-91ed-bd8f8e997cf8",
            "document_id": "doc-456",
            "status": "UNKNOWN_CRITICAL_ERROR",  # Not in the enum
            "timestamp": 1730000000,
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(invalid_payload, "KafkaSyncStatusEvent")

    # ------------------------------------------------------------------------
    # 5. User ACL Upsert Event (rag.users.acl.v1)
    # ------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "allowed_groups",
        [
            ["Engineering", "All Staff"],
            ["oleh.pona@company.com"],
        ],
    )
    def test_kafka_user_acl_upsert_event_contract(self, validate_openapi_schema, allowed_groups):
        payload = {
            "email": "oleh.pona@company.com",
            "last_updated": "2026-11-05T10:00:00Z",
            "acl": {"allowed_groups": allowed_groups},
        }
        validate_openapi_schema(payload, "KafkaUserAclUpsert")

    @pytest.mark.parametrize("missing_field", ["email", "last_updated", "acl"])
    def test_invalid_user_acl_upsert_missing_required_fields(self, validate_openapi_schema, missing_field):
        payload = {
            "email": "oleh.pona@company.com",
            "last_updated": "2026-11-05T10:00:00Z",
            "acl": {"allowed_groups": ["Engineering"]},
        }
        del payload[missing_field]
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(payload, "KafkaUserAclUpsert")

    def test_invalid_user_acl_upsert_email_format(self, validate_openapi_schema):
        payload = {
            "email": "not-a-valid-email",
            "last_updated": "2026-11-05T10:00:00Z",
            "acl": {"allowed_groups": ["Engineering"]},
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(payload, "KafkaUserAclUpsert")

    def test_invalid_user_acl_upsert_last_updated_format(self, validate_openapi_schema):
        payload = {
            "email": "oleh.pona@company.com",
            "last_updated": "not-a-datetime",
            "acl": {"allowed_groups": ["Engineering"]},
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(payload, "KafkaUserAclUpsert")

    @pytest.mark.parametrize(
        "invalid_acl",
        [
            {"allowed_groups": []},  # minItems: 1 violation
            {"allowed_groups": [123]},  # items must be string
            {},  # missing allowed_groups
            "Engineering",  # not an object
        ],
    )
    def test_invalid_user_acl_upsert_invalid_acl(self, validate_openapi_schema, invalid_acl):
        payload = {
            "email": "oleh.pona@company.com",
            "last_updated": "2026-11-05T10:00:00Z",
            "acl": invalid_acl,
        }
        with pytest.raises(jsonschema.ValidationError):
            validate_openapi_schema(payload, "KafkaUserAclUpsert")
