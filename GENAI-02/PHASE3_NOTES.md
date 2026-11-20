# GENAI-02 — Phase 3 Core Implementation Notes

## Included subsystems

- **Planner, Researcher, Coder, Reviewer:** four typed agent services with structured plan validation, source provenance, reproducible Python generation, and evidence-focused review.
- **LangGraph workflow:** typed `AgentState`, guarded transitions, reviewer-to-coder revision loop, cycle detection, step/token/USD caps, interrupt-based risky-action approval, and a durable pre-finalization checkpoint.
- **MCP tools:** independent stdio servers for web search, SEC EDGAR, workspace filesystem, read-only PostgreSQL run-history queries, and Playwright browser retrieval. The FastAPI workflow calls these same servers through the official MCP v2 in-process client/dispatcher (not direct service-method shortcuts), while external MCP hosts can launch each stdio entry point. The four requested tool classes are present; SEC EDGAR is an additional primary-source server.
- **Sandbox:** generated Python is AST-checked and executed only through E2B when `SANDBOX_MODE=e2b`. `SANDBOX_MODE=mock` deliberately does not execute generated code on the host; it emits a deterministic fixture result.
- **Persistence:** LangGraph `AsyncPostgresSaver` checkpoint context plus an operational `agent_runs` / `agent_run_events` store. In non-production, unavailable Postgres dependencies or connectivity fall back to in-memory graph checkpoints and degraded run-history storage. Production does not silently fall back.
- **Telemetry:** OpenTelemetry spans and metrics are exported over OTLP HTTP. Docker Compose includes Jaeger, an OTel Collector, and Prometheus.

## Local run

From this directory:

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY / TAVILY_API_KEY / E2B_API_KEY as needed.
# Keep SANDBOX_MODE=mock for a no-paid-key smoke run.
docker compose up --build
```

API and operational surfaces:

- API root: `http://localhost:8010`
- Swagger UI: `http://localhost:8010/api/docs`
- OpenAPI JSON: `http://localhost:8010/api/openapi.json`
- Jaeger: `http://localhost:16686`
- Prometheus: `http://localhost:9090`
- PostgreSQL is exposed on host port `5433` (container port `5432`).

For direct local Python execution (outside Docker), install `requirements.txt`, install Playwright Chromium (`playwright install chromium`), set `DATABASE_URL` to a reachable Postgres instance, and run:

```bash
uvicorn src.api:app --host 0.0.0.0 --port 8010
```

## Workflow API

Start a run:

```bash
curl -sS http://localhost:8010/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"goal":"Research Microsoft latest annual financial performance and calculate revenue growth","company":"Microsoft","thread_id":"microsoft-financial-demo-01"}'
```

Inspect a persisted thread:

```bash
curl -sS http://localhost:8010/api/v1/threads/microsoft-financial-demo-01
```

Approve final report persistence:

```bash
curl -sS http://localhost:8010/api/v1/threads/microsoft-financial-demo-01/approve \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve","note":"Reviewed the report and approved workspace persistence."}'
```

A request with `"simulate_risky_actions": true` and goal text such as “draft and send an email” will pause at a separate external-action approval interrupt. The workflow only records an approved simulation; it does **not** send email, spend money, or call a real mutation endpoint. After that approval, it pauses again before writing the final report.

The final report is written under `workspace/reports/`. Charts returned by E2B are copied into `workspace/artifacts/` after final approval.

## MCP stdio servers

Run each server from this directory in its own process/terminal. The MCP host should launch the corresponding module as a stdio subprocess:

```bash
python -m src.mcp_servers.web_search_server
python -m src.mcp_servers.sec_server
python -m src.mcp_servers.filesystem_server
python -m src.mcp_servers.postgres_server
python -m src.mcp_servers.playwright_server
```

Tool scope is intentionally narrow:

- `web_search`: public search discovery; excerpts are leads, not verified facts.
- `sec_edgar_company_research`: issuer resolution, recent official filing metadata, and selected annual US-GAAP companyfacts.
- `read_workspace_file`, `write_workspace_file`, `list_workspace_files`: workspace-confined file access.
- `query_run_history`: read-only SELECT/CTE access to `agent_runs` and `agent_run_events`, with statement and row limits.
- `browser_fetch`: HTTPS browser extraction for the configured allowlisted domains only.

## Configuration and safety

- **API:** set `API_ACCESS_TOKEN` to a long random secret before production deployment.
- **LLM:** `OPENAI_API_KEY` enables the OpenAI-compatible LangChain adapter; otherwise the agents use deterministic offline planning/coding/review behavior.
- **Search:** `TAVILY_API_KEY` enables Tavily; Wikipedia OpenSearch and SEC EDGAR are used as public fallbacks/primary-source sources. `SEC_USER_AGENT` should be customized to identify the operator in accordance with SEC fair-access guidance.
- **E2B:** set `SANDBOX_MODE=e2b` and `E2B_API_KEY` to enable remote code execution. The SDK call has both sandbox and request timeouts; sandbox teardown is attempted in `finally`.
- **Postgres checkpoint encryption:** optionally set `LANGGRAPH_AES_KEY` to a valid PyCryptodome AES key in the format supported by LangGraph's `EncryptedSerializer`. Keep this value out of source control.
- **Budgets:** `MAX_STEPS`, `MAX_REVISIONS`, `MAX_TOKENS_PER_RUN`, and `MAX_COST_USD_PER_RUN` are hard ceilings. Token and cost values are estimates when provider usage metadata is unavailable; they are not billing statements.
- **Browser:** `BROWSER_ALLOWED_DOMAINS` is a JSON array. Browser requests and redirects are checked against the allowlist.
- **External actions:** all requested external mutations are approval-gated and are simulated only. No real dispatch integration is configured in this project phase.

## Verification boundary

Static compilation, schema/security smoke checks, SQL allowlist checks, workspace path checks, mock-sandbox checks, and YAML parsing are run as part of this phase build. Full LangGraph/MCP/PostgreSQL/E2B live integration and Docker image build require installing the pinned dependencies and running the backing services; the current build environment cannot reach package registries, so those network-dependent checks are not claimed as completed.
