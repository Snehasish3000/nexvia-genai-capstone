# GENAI-02 — Phase 4 Delivery Notes

## Delivered

- Streamlit operator dashboard with workflow launch, recorded run selection, thread snapshots, approval details, Approve/Edit/Reject actions, generated report and artifact views, and a durable event timeline.
- Synchronous API client with bearer-token support, URL validation, bounded timeouts, encoded path parameters, and surfaced HTTP/network errors.
- Authenticated `GET /api/v1/runs/{run_id}/events` API route and bounded Postgres event query in `RunStore` to support the dashboard timeline.
- Docker Compose UI service on host port `8501`.
- Versioned 20-task benchmark dataset; pydantic validation; live async API runner; optional approval resume mode; JSON/CSV/Markdown output; aggregate task-success, steps, latency, token estimate, USD cost estimate, guardrail rate, and approval rate.
- Tests for task data integrity, checkpoint classification, summary calculations, API client behavior through mocked HTTP, workflow schema contracts, step/cycle/token/cost guardrails, risky-action approval, and non-executing mock sandbox behavior.

## Verification performed in this build environment

- `python -m compileall -q src ui evaluation tests` — passed.
- `python evaluation/run_benchmark.py --validate-only` — passed; 20 tasks validated, no API calls made.
- `pytest -q` — **20 passed**.
- Docker Compose YAML parsed and checked for Postgres, Jaeger, OTel Collector, Prometheus, API, and UI services — passed.
- External-action benchmark prompts checked against the backend trigger phrases — passed.
- Event-history API and Postgres helper presence checked — passed.

## Verification not claimed

- Streamlit is not installed in the build runtime, so the dashboard was syntax-compiled but not started interactively.
- Docker CLI is unavailable in the build runtime, so a Docker image build / container smoke test was not performed.
- The LangGraph/MCP/E2B/Postgres live workflow was not executed in this phase; it requires the Phase 3 runtime dependencies and backing services. The E2B SDK and MCP SDK are not installed in this build runtime.
- The benchmark was dataset-validated only. No live API benchmark measurements or performance claims have been produced. Run `python evaluation/run_benchmark.py` against a started API to generate report files.

The included unit suite uses mock transports and deterministic local fixtures; no paid model, E2B, or hosted database key is needed to run the tests.
