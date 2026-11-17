# SCRUM-83 — Ticket destination decision and integration assumptions

**Decision status:** OPEN — no destination selected
**Decision owner:** TBD — the project/product owner must nominate the accountable decision-maker. The Jira reporter and current assignee state are not treated as approval.
**Recorded:** 2026-11-17
**Scope:** Human-escalation destination for the MVP

## Decision

No ticketing or service-desk product is approved by this record. The platform must keep the escalation contract independent of any destination until an accountable owner records an explicit decision.

The destination decision is required before implementing a destination-specific adapter. It does **not** block the generic escalation payload, the destination-neutral interface, or the user-confirmation flow.

## Options to evaluate

The project evidence available for this decision does not identify a confirmed vendor shortlist. The following are capability-level options to validate with the team; they are not vendor recommendations or approvals:

1. **Existing organizational ITSM/service-desk queue.** Validate whether the organization already has an approved service desk, its API/webhook capabilities, ownership, licensing, and support workflow.
2. **Existing issue-tracking platform with a support-request workflow.** Validate whether an existing issue tracker is approved for end-user support, rather than assuming that the tool used for software-project planning is also the support destination.
3. **Core-managed expert queue/manual hand-off.** Keep the escalation record in Core and route it through an internally owned queue or a documented manual process while no external destination is approved.

No product name, vendor, endpoint, credential model, ticket type, project key, or destination-specific field is approved at this stage.

## Decision ownership and required inputs

| Item | Recorded value |
|---|---|
| Accountable decision owner | TBD — must be named by the project/product team |
| Technical evaluator | TBD |
| Operational owner of the expert queue | TBD |
| Approved destination | None |
| Approved vendor-specific adapter | None |
| Decision trigger | Before work starts on a destination-specific adapter |

The decision owner should confirm: existing organizational tooling and licenses; who receives and triages requests; required SLAs and priority handling; authentication and service-account ownership; API/webhook support; attachment and source-link support; audit/history requirements; data retention/residency constraints; rate limits and failure/retry behavior; and operational ownership.

## Verified current-state observations

- The current Core `RagGenerationOrchestrator` returns `ESCALATED_TO_EXPERT` and a static reply string when RAG does not return `ANSWER_GENERATED`.
- That code path does not invoke a ticketing API or destination adapter. The response text claiming that a support ticket has been created therefore overstates the verified behavior.
- The existing Gateway-to-Core OpenAPI example also used the phrase “ticket created”; this contract update replaces it with a confirmation question.
- No destination product or named decision owner was provided in the issue context reviewed for this record.

These observations describe the repository state reviewed on 2026-11-17; they are not a statement that no such organizational system exists outside the repository.

## Integration assumptions until the decision is made

- Core owns the escalation record and stable `escalation_id`.
- The generic request contains question data, explicit user confirmation, relevant answer/context, source references, reason, timestamps, and correlation identifiers.
- A destination is hidden behind a destination-neutral port. The adapter is selected/configured only after the decision is approved.
- The generic contract contains no provider-specific project, queue, ticket-type, URL, authentication, or custom-field names.
- A successful Core response means the escalation record was accepted. It must not be presented as proof that an external ticket was created.
- If no destination is configured, the Core record remains `PENDING_DESTINATION`; absence of a destination is not silently mapped to a vendor.
- Retries use the same `Idempotency-Key` and return the same `escalation_id` rather than creating duplicate records.

## Consequences and follow-up

- SCRUM-77 can be reviewed and completed without waiting for a destination choice.
- SCRUM-36 and SCRUM-37 may implement confirmation and the generic record contract, but must not implement a vendor-specific adapter by assumption.
- The destination-specific adapter remains gated by the explicit decision in SCRUM-83.
- Replace any user-facing “ticket created” wording unless the adapter has actually confirmed submission. The confirmation and record-creation work belongs to SCRUM-36/SCRUM-37.
- Once the owner and evidence are available, update this record with the selected option, rationale, date, approver, and adapter consequences. Until then the decision remains OPEN.
