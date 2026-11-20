# GENAI-02 — HTTP API Reference

Local base URL: `http://localhost:8010`  
API prefix: `/api/v1`  
Swagger UI: `/api/docs`  
OpenAPI JSON: `/api/openapi.json`  

## Authentication

When `API_ACCESS_TOKEN` is configured, every route requires:

```http
Authorization: Bearer <API_ACCESS_TOKEN>
```

The check uses constant-time comparison. `APP_ENV=production` requires a configured token. In local development, an empty token leaves the API open; never expose that configuration on a public interface.

## Routes

| Method | Path | Purpose | Request / response |
|---|---|---|---|
| GET | `/api/v1/health` | Service and persistence/sandbox health | `HealthResponse` |
| POST | `/api/v1/runs` | Create and start a workflow | `WorkflowRequest` → `WorkflowResponse` (202) |
| GET | `/api/v1/runs?limit=50` | List recent operational run records | JSON array, limit capped at 100 |
| GET | `/api/v1/runs/{run_id}` | Read operational run record | Run history object |
| GET | `/api/v1/runs/{run_id}/events?limit=100` | Read bounded chronological run events | JSON array, limit capped at 500 |
| GET | `/api/v1/threads/{thread_id}` | Read current persisted graph state | `WorkflowResponse` |
| POST | `/api/v1/threads/{thread_id}/approve` | Resume interrupt with decision | `ApprovalDecision` → `WorkflowResponse` |

## Start a workflow

`WorkflowRequest` fields:

| Field | Type | Constraints / default |
|---|---|---|
| `goal` | string | 12–8,000 characters |
| `task_domain` | string | default `market_research_financial_analysis` |
| `company` | string or null | optional, max 160 characters |
| `thread_id` | string | generated UUID by default; 8–80 characters; must be unique for new run |
| `simulate_risky_actions` | boolean | default `false` |
| `max_steps` | integer or null | optional 3–50; also bounded by server config |
| `max_cost_usd` | number or null | optional >0 and ≤100; also bounded by server config |

Example:

```bash
curl -sS http://localhost:8010/api/v1/runs \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -d '{
    "goal":"Research Microsoft annual results, disclosed risks, and revenue growth using primary sources",
    "company":"Microsoft Corporation",
    "thread_id":"msft-review-2026-001",
    "simulate_risky_actions":false
  }'
```

A thread ID already used by the graph returns `409`. Use a new ID for a new run; use the existing ID to inspect or resume an approval checkpoint.

## Approval / edit / reject

`ApprovalDecision`:

```json
{
  "action": "approve",
  "note": "Reviewed source provenance and approved report persistence."
}
```

Allowed `action` values: `approve`, `edit`, `reject`. For `edit`, `edited_report` is required and must be non-empty. The `note` field is optional and capped at 2,000 characters. Edited report is capped at 30,000 characters.

Approve:

```bash
curl -sS http://localhost:8010/api/v1/threads/msft-review-2026-001/approve \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -d '{"action":"approve","note":"Approved after review."}'
```

Edit:

```bash
curl -sS http://localhost:8010/api/v1/threads/msft-review-2026-001/approve \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -d '{"action":"edit","note":"Corrected reporting period wording.","edited_report":"# Reviewed report\n\nRevised content."}'
```

Reject:

```bash
curl -sS http://localhost:8010/api/v1/threads/msft-review-2026-001/approve \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -d '{"action":"reject","note":"Insufficient evidence for the requested conclusion."}'
```

The API rejects approval when there is no pending interrupt/checkpoint (`409`). Missing threads return `404`.

## Response shape

`WorkflowResponse` contains:

- `run_id`, `thread_id`, and `status` (`queued`, `running`, `awaiting_approval`, `completed`, `rejected`, `failed`);
- `current_step`, `approval_required`, and `approval_payload`;
- `report`, `artifacts`, `metrics`, and `errors`.

The health response includes service status, database status, sandbox mode, and timestamp.

## Limits and error semantics

The API applies server-side request-length checks, workflow step limits, revision limits, recursion limits, token/USD budget breakers, and guardrails. Expected error codes include `400` for rejected input, `401` for missing/invalid bearer token, `404` for unknown run/thread, `409` for a duplicate thread or absent approval checkpoint, and `500` for unexpected workflow execution failure. Operational details are logged server-side; the API returns exception class rather than raw traceback.
