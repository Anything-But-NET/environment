# SCRUM-77 — Destination-neutral escalation record contract

**Status:** Draft contract for review before SCRUM-36/SCRUM-37 implementation
**Related decision:** [SCRUM-83 — Ticket destination decision](../architecture/decisions/SCRUM-83-ticket-destination.md)
**Contract source:** `contracts/openapi.yaml` — `POST /api/v1/escalations`

## Purpose and boundary

This contract defines how Core records a user-confirmed request for human assistance. It is intentionally independent of the eventual ticketing/service-desk destination.

The HTTP contract is the Core-facing record-creation shape. Keep Core-owned persistence separate from the outbound destination port. The internal abstractions should be equivalent to:

```text
EscalationRecordService.create(
    request: EscalationCreateRequest,
    idempotencyKey: string
) -> EscalationCreateResponse

EscalationDestinationPort.submit(
    record: EscalationRecord
) -> DestinationSubmissionResult

DestinationSubmissionResult = {
    accepted: boolean,
    destinationReference?: string,  # opaque to Core
    retryable: boolean
}
```

`EscalationRecordService` validates and persists the generic record. `EscalationDestinationPort` is an outbound port implemented later by an adapter selected under SCRUM-83. A missing adapter/configuration maps to `PENDING_DESTINATION`; an adapter acceptance maps to `SUBMITTED`; an unsuccessful adapter attempt maps to `DELIVERY_FAILED`, with `retryable` controlling whether Core schedules/recommends another attempt. Neither interface exposes a vendor SDK, project key, queue name, ticket type, vendor URL, or provider-specific custom fields. The adapter is not part of SCRUM-77 and cannot be selected by assumption.

## Question flow

1. Gateway receives a question and sends it to Core. Core creates a stable `question_id` and returns it in `UserQuestionResponse`; the existing Gateway/Core `query_id` remains the `correlation_id` for the initial integration.
2. Core runs retrieval/generation. If an answer cannot be provided or the user asks for a person, Core marks that expert review is needed. This state alone does not create an escalation record or an external ticket.
3. Gateway asks the user whether the question may be sent for expert review and retains the returned `question_id`.
4. Only after an explicit affirmative user action does the caller submit `POST /api/v1/escalations`, supplying that `question_id`. `user_confirmation.confirmed` must be `true`, and the confirmation actor/time/channel must be included.
5. Core validates and stores the destination-neutral escalation record and returns its stable `escalation_id`. If no destination has been approved/configured, the response status is `PENDING_DESTINATION`.
6. A later approved adapter may move the record to `SUBMITTED` or `DELIVERY_FAILED`. `SUBMITTED` means the adapter accepted the hand-off; it does not mean that an expert has responded or resolved the question.

The confirmation interaction is implemented under SCRUM-36; persistence/record creation is implemented under SCRUM-37. SCRUM-77 defines the contract only.

## Identifier relationships

| Identifier | Created by | Meaning and lifecycle |
|---|---|---|
| `question_id` | Core, when the logical question is first accepted | Stable UUID for one logical question. Reused for the answer, confirmation, escalation record, and follow-up. It is not an idempotency key. |
| `correlation_id` | Gateway/Core request flow | End-to-end trace identifier. For the initial integration, copy the existing `query_id` into this field so current logs can be correlated. It must not be used as the stable question identity. |
| `escalation_id` | Core, on first successful create | Stable UUID of the persisted escalation record. Returned unchanged for idempotent retries. |
| `Idempotency-Key` | Calling client, once per confirmation submission | Identifies one create command. The client must reuse it with the same payload for retries, including timeout retries. |

## Request payload

The request is defined by `EscalationCreateRequest` in `contracts/openapi.yaml`.

| Field | Required | Validation / meaning |
|---|---:|---|
| `question_id` | Yes | UUID; stable across the complete question lifecycle. |
| `correlation_id` | Yes | Non-empty trace ID; initially mapped from `query_id`. |
| `question.text` | Yes | Non-empty, up to 4,000 characters. |
| `question.channel` | Yes | Current supported intake channel: `slack` or `teams`. |
| `question.asked_at` | Yes | ISO-8601 date-time when the original question was asked. |
| `requester.user_email` | Yes | Email of the authenticated requester. |
| `user_confirmation.confirmed` | Yes | Must be `true`. A missing/false confirmation is rejected; Core must not infer consent from low confidence or silence. |
| `user_confirmation.confirmed_at` | Yes | ISO-8601 date-time of the affirmative action. |
| `user_confirmation.confirmed_by` | Yes | Email of the user who confirmed. |
| `user_confirmation.confirmed_via` | Yes | Channel where the confirmation was made (`slack` or `teams`). |
| `answer_context.answer_text` | Yes | Partial/generated answer or `null` when no answer exists. |
| `answer_context.confidence_score` | Yes | Number from 0 to 1 or `null` when unavailable. |
| `answer_context.source_references` | Yes | List of source references; may be empty if retrieval found no usable source. |
| `escalation_reason` | Yes | `LOW_CONFIDENCE`, `NO_RELEVANT_CONTEXT`, `USER_REQUESTED_HUMAN`, `GENERATION_ERROR`, or `OTHER`. |
| `escalation_reason_details` | No | Optional diagnostic explanation; do not include secrets or unrelated personal data. |

Do not include ACL group membership, model prompts, API credentials, or unrelated conversation history. Include only context needed by an expert to understand the question and the reason for hand-off.

## Response payload and status

The response is defined by `EscalationCreateResponse`.

- `escalation_id` is generated by Core and remains stable for this record.
- `question_id` and `correlation_id` echo the request identifiers.
- `created_at` is assigned by Core when the record is durably persisted.
- `destination_reference` is an opaque optional value. Its shape and meaning are not defined until a destination is selected.
- `status` is destination-neutral:
  - `PENDING_DESTINATION` — Core has persisted the record; no approved/configured destination is available.
  - `SUBMITTED` — an approved adapter accepted the hand-off. This is not proof of expert response/resolution.
  - `DELIVERY_FAILED` — an adapter attempt failed; the Core record remains available for retry/manual handling.

`201 Created` means that a new Core escalation record was persisted. `200 OK` means an idempotent replay of the prior result. Neither response, by itself, proves that a vendor ticket exists.

## Idempotency and duplicate behavior

1. `Idempotency-Key` is required and scoped to the authenticated caller/operation.
2. Persist the key with the escalation record for at least the lifetime of that record.
3. Same key + same canonical payload: return `200` with the original `escalation_id`, status and creation timestamp; do not create another record or dispatch a second hand-off.
4. Same key + different payload: return `409` with `IDEMPOTENCY_KEY_REUSED`; do not mutate the original record.
5. Concurrent requests with the same key must converge on one record and one `escalation_id`.
6. A timeout retry must reuse the original key. A new key represents a new explicit confirmation submission; question-level de-duplication across different keys is not part of this contract.

## Error behavior

| HTTP | Error code | Meaning |
|---:|---|---|
| 400 | `MALFORMED_REQUEST` | Malformed JSON or missing/invalid `Idempotency-Key`. |
| 409 | `IDEMPOTENCY_KEY_REUSED` | Existing key was supplied with a different canonical payload. |
| 422 | `VALIDATION_ERROR` / `CONFIRMATION_REQUIRED` | Payload is invalid or explicit affirmative confirmation is absent. |
| 503 | `ESCALATION_SERVICE_UNAVAILABLE` | Core could not durably accept the record. The caller may retry with the same key. |

If a destination adapter fails after Core has persisted the record, do not return a transport error that encourages creation of a second record. Keep the record and report `DELIVERY_FAILED` (or `PENDING_DESTINATION` when no adapter has been configured). Provider failure is data about the record lifecycle, not a reason to lose the accepted request.

## Example

```json
{
  "question_id": "123e4567-e89b-12d3-a456-426614174000",
  "correlation_id": "req-123",
  "question": {
    "text": "How do I request access to the finance system?",
    "channel": "teams",
    "asked_at": "2026-11-17T14:20:00Z"
  },
  "requester": {
    "user_email": "alex@example.com"
  },
  "user_confirmation": {
    "confirmed": true,
    "confirmed_at": "2026-11-17T14:22:00Z",
    "confirmed_by": "alex@example.com",
    "confirmed_via": "teams"
  },
  "answer_context": {
    "answer_text": null,
    "confidence_score": 0.22,
    "source_references": []
  },
  "escalation_reason": "NO_RELEVANT_CONTEXT",
  "escalation_reason_details": "No permitted source answered the question."
}
```

Example response when no destination has been approved/configured:

```json
{
  "escalation_id": "987e6543-e21b-12d3-a456-426614174000",
  "question_id": "123e4567-e89b-12d3-a456-426614174000",
  "correlation_id": "req-123",
  "status": "PENDING_DESTINATION",
  "destination_reference": null,
  "created_at": "2026-11-17T14:22:01Z"
}
```

## Review / implementation gate

This is a destination-neutral contract proposal and must be reviewed by the Core/Gateway owners before SCRUM-36 and SCRUM-37 implementation begins. SCRUM-83 remains open until the accountable decision owner selects and records a destination. That decision gates only a destination-specific adapter, not this generic contract.
