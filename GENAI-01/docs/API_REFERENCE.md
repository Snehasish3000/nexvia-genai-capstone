# GENAI-01 — HTTP API Reference

Base URL in local Docker Compose: `http://localhost:8000`  
API prefix: `/api/v1`  
Interactive contract: `GET /docs` and `GET /openapi.json`.

Every protected route requires `Authorization: Bearer <JWT>`. The API derives the user's role from the signed token; a role value supplied by the browser is never authoritative.

## Authentication

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/demo-token` | Development/demo only | Issue a short-lived demo JWT for a named test user and one of `admin`, `finance`, `engineering`, `guest` |

Example request:

```bash
curl -sS http://localhost:8000/api/v1/auth/demo-token \
  -H 'Content-Type: application/json' \
  -d '{"username":"reviewer","role":"guest"}'
```

The endpoint returns `access_token`, `token_type`, `expires_in`, and `role`. It returns `404` when `ENVIRONMENT=production` or `ALLOW_DEMO_AUTH=false`.

## Knowledge queries

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/api/v1/chat` | Any valid role | Non-streaming grounded answer |
| POST | `/api/v1/chat/stream` | Any valid role | Server-Sent Events: metadata, token, completion |

Request model:

```json
{
  "question": "What does the supplied policy say about expense approval?",
  "retrieval_mode": "hybrid_rerank",
  "top_k": 6,
  "conversation_id": null
}
```

`retrieval_mode` is one of `dense`, `hybrid`, or `hybrid_rerank`; `top_k` is bounded from 1 to 20. Unknown request fields are rejected.

Response fields include `answer`, `citations`, `confidence`, `abstained`, `retrieval_mode`, `latency_ms`, `usage`, `cache_hit`, and `request_id`. A citation includes `chunk_id`, `source`, optional `page`, `quote`, and `score`.

SSE event names:

- `metadata`: request ID and confidence.
- `token`: one display fragment.
- `complete`: final `ChatResponse` JSON object.

The server currently computes the full answer before emitting SSE fragments. The event protocol is stable for the UI, but upstream model token streaming is not implemented.

## Feedback

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/api/v1/feedback` | Any valid role | Record `up` / `down` rating against a request ID |

Example:

```bash
curl -sS http://localhost:8000/api/v1/feedback \
  -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"request-id-from-chat","rating":"up"}'
```

Optional comment text is length-limited and inspected by the input guardrail. The response is `{ "status": "recorded" }`.

## Administration / ingestion

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/api/v1/admin/documents/upload` | Admin only | Upload supported PDF/DOCX file with comma-separated `allowed_roles` form field |
| POST | `/api/v1/admin/documents/url` | Admin only | Ingest an HTTP(S) URL with JSON `url` and `allowed_roles` |
| GET | `/api/v1/admin/stats` | Admin only | Document/chunk counts, feedback count, query latency/cost, cache entries |

Example file upload:

```bash
curl -sS http://localhost:8000/api/v1/admin/documents/upload \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -F 'allowed_roles=finance,engineering,guest' \
  -F 'file=@./sample-policy.docx'
```

The backend adds `admin` to the allowed role set so administrators retain access to uploaded material. Do not make this endpoint available to non-admin users.

## Operations

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/health` | Public | Vector store health |
| GET | `/metrics` | Public in current local implementation; protect at ingress in shared deployments | Prometheus text exposition |
| GET | `/docs` | Public | Swagger UI |
| GET | `/redoc` | Public | ReDoc |
| GET | `/openapi.json` | Public | OpenAPI schema |

## Common status codes

| Code | Meaning |
|---|---|
| 200 | Request succeeded |
| 401 | Missing, invalid, or expired bearer JWT |
| 403 | Authenticated principal lacks admin role |
| 404 | Demo authentication disabled or route/resource absent |
| 413 | Uploaded file exceeds configured maximum |
| 422 | Schema, URL-ingestion, or guardrail validation failure |
| 503 | Health dependency unavailable |

## Request correlation

The API accepts `X-Request-ID`; when absent, it generates one. Responses include `X-Request-ID` and `X-Process-Time-MS`. Propagate request IDs to logs and support tickets, but do not use them as credentials.
