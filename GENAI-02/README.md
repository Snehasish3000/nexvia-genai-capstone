# GENAI-02 — Multi-Agent Autonomous Workflow System

**Nexvia Technologies · Generative AI / AI Engineering Internship Capstone**

GENAI-02 is a bounded, auditable agent workflow for public-company research and financial analysis. A LangGraph state machine coordinates a Planner, Researcher, Coder, and Reviewer; MCP tools provide narrowly scoped external capabilities; generated Python runs in E2B when enabled; PostgreSQL stores operational history and graph checkpoints; and a Streamlit console exposes human approval checkpoints.

The system is deliberately designed as an assistive research workflow, not an autonomous financial adviser or unrestricted computer-use agent. Search snippets are discovery leads rather than verified financial facts. The final report labels limitations and asks reviewers to validate source periods, units, and calculations.

## Phase 3 + Phase 4 capabilities

| Area | Implementation |
|---|---|
| Agent orchestration | Typed LangGraph `AgentState`; Planner → Researcher → Coder → Reviewer; conditional reviewer revision loop |
| Planning | Structured execution plan with validated task dependencies and success criteria |
| Research | Web search, SEC EDGAR company facts, source provenance, constrained Playwright fetch |
| Code execution | AST-checked generated Python; E2B remote sandbox with execution and request timeouts; mock mode never executes generated code on the host |
| MCP | Web search, SEC EDGAR, filesystem, PostgreSQL read-only query, and Playwright server modules |
| Human approval | Interrupt before risky-action simulation and before final report persistence; Approve / Edit / Reject via dashboard |
| Persistence | LangGraph PostgreSQL checkpointer plus `agent_runs` / `agent_run_events` operational tables |
| Observability | OpenTelemetry traces and metrics, Jaeger UI, Prometheus collector endpoint |
| Guardrails | Prompt-injection rejection, PII scrubbing, path containment, browser domain allowlist, SQL read-only limits, step/cycle/token/USD ceilings |
| Operator UI | Streamlit run launcher, run history, current state, approval cards, edit-report control, event timeline, and Jaeger link |
| Evaluation | 20 version-controlled task prompts; live API harness; per-task JSON/CSV/Markdown plus aggregate success, steps, latency, cost, guardrail, and approval rates |

## Quickstart with Docker Compose

Run commands from `GENAI-02/`:

```bash
cp .env.example .env
# Set a strong API_ACCESS_TOKEN before sharing a network deployment.
# For the no-paid-key demo, keep SANDBOX_MODE=mock and leave provider keys empty.
docker compose up --build -d
```

Open:

| Surface | URL |
|---|---|
| Streamlit approval dashboard | http://localhost:8501 |
| FastAPI root | http://localhost:8010 |
| Swagger / OpenAPI | http://localhost:8010/api/docs |
| OpenAPI JSON | http://localhost:8010/api/openapi.json |
| Jaeger traces | http://localhost:16686 |
| Prometheus | http://localhost:9090 |
| PostgreSQL host port | `localhost:5433` |

The dashboard defaults to the local API URL. When `API_ACCESS_TOKEN` is configured in the backend `.env`, enter the same value in the dashboard sidebar. Use a long random secret for any shared deployment; never commit `.env` files.

## Local development without Docker

Create a Python 3.11+ environment and install the API and UI requirements separately:

```bash
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r ui/requirements.txt
playwright install chromium
cp .env.example .env
```

Set `DATABASE_URL` to a reachable PostgreSQL instance for durable checkpoints and event history. Then run the API and dashboard in separate terminals:

```bash
uvicorn src.api:app --host 0.0.0.0 --port 8010
```

```bash
API_BASE_URL=http://localhost:8010 streamlit run ui/app.py --server.port 8501
```

On Windows PowerShell, set the environment variable with `$env:API_BASE_URL="http://localhost:8010"` before running Streamlit.

## Starting a workflow

The UI is the recommended operator surface. The same API can be called directly:

```bash
curl -sS http://localhost:8010/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"goal":"Research Microsoft annual results, disclosed risks, and revenue growth using primary sources","company":"Microsoft Corporation","thread_id":"msft-demo-thread-001"}'
```

Inspect current state:

```bash
curl -sS http://localhost:8010/api/v1/threads/msft-demo-thread-001
```

Approve the final report write after reviewing it:

```bash
curl -sS http://localhost:8010/api/v1/threads/msft-demo-thread-001/approve \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve","note":"Reviewed source provenance and approved workspace persistence."}'
```

To edit the report, send `{"action":"edit","note":"...","edited_report":"# Revised report..."}`. To reject, send `{"action":"reject","note":"Reason..."}`. The Edit path requires a non-empty replacement report.

A goal mentioning email dispatch or spending only creates a separate approval checkpoint when `simulate_risky_actions=true`. Even after approval, these operations are simulation-only: no email is sent, no money is spent, and no third-party mutation is invoked. A final report write is separately paused before it reaches the managed workspace.

## Approval dashboard

The Streamlit console provides:

1. API health, database persistence status, sandbox mode, and recorded-run count.
2. Workflow creation with company/subject, domain, step ceiling, USD ceiling, and simulated-risk toggle.
3. Recorded run history and persisted thread selection.
4. Current state, step/token/cost/latency metrics, errors, report, and artifact paths.
5. Approval details with Approve, Edit, and Reject actions. Editing requires replacement report text.
6. A chronological run-event timeline from the authenticated `/api/v1/runs/{run_id}/events` endpoint and a link to Jaeger.

The dashboard does not hold a privileged bypass token, automatically approve actions, or make external changes. The operator must explicitly submit every decision.

## 20-task benchmark

`evaluation/tasks.json` contains 20 deterministic benchmark prompts spanning SEC/public-source research, comparative and multi-hop research, financial calculations, uncertainty handling, sandbox code/chart tasks, prompt injection, PII minimization, final-write approval, simulated email/spend approvals, browser-source preferences, and budget guardrails.

Validate the dataset without contacting a running API:

```bash
python evaluation/run_benchmark.py --validate-only
```

Run the first three tasks as an API smoke benchmark:

```bash
python evaluation/run_benchmark.py --base-url http://localhost:8010 --limit 3
```

Run all 20 tasks:

```bash
python evaluation/run_benchmark.py --base-url http://localhost:8010
```

To exercise the approval resume path automatically, add `--resume-approvals`. This option approves checkpoints for test automation; risky email/spend nodes still only create simulation records. Use this flag only against a disposable evaluation environment because each task creates a real workflow record and approved test reports may be written into the configured workspace.

If the API has an access token configured, set it in the environment before running the harness:

```bash
export API_ACCESS_TOKEN='your-long-random-token'
python evaluation/run_benchmark.py --base-url http://localhost:8010
```

The runner writes timestamped `benchmark-*.json`, `.csv`, and `.md` outputs under `evaluation/results/`. The aggregate report includes:

- task success rate against the expected API checkpoint or rejection;
- average graph steps, latency, token estimate, and cost estimate;
- guardrail trigger rate and approval trigger rate;
- per-category task success.

**Scoring boundary:** task success is an API-contract check (for example, the expected HITL pause or a prompt-injection rejection). It is not an automated factuality grade for the generated report. Validate source accuracy, period alignment, unit consistency, and calculation correctness separately before reporting an overall system-quality score. The repository contains no fabricated benchmark numbers; a result report is generated only after execution against a running API.

## Tests

Run from `GENAI-02/`:

```bash
pytest -q
```

The test suite covers benchmark dataset integrity, checkpoint classification, metric aggregation, dashboard API client behavior through mocked HTTP transports, bearer-token propagation, error handling, approval schema validation, and execution-plan dependency validation. These tests do not require paid model or E2B credentials.

Run syntax and dataset checks independently:

```bash
python -m compileall -q src ui evaluation tests
python evaluation/run_benchmark.py --validate-only
```

Live integration still requires the relevant packages and backing services. For the complete runtime, start Postgres, Jaeger, the OTel Collector, API, and UI with Docker Compose. Use `SANDBOX_MODE=e2b` with `E2B_API_KEY` only when remote sandbox execution is intended; keep mock mode for a no-paid-key workflow demonstration.

## Benchmark interpretation and reporting

Latency and cost values come from API state metrics. In deterministic offline LLM mode, token and provider cost estimates may be zero because no paid provider call occurs; this is not evidence of free production inference. Real-provider measurements must be collected with the provider/model configuration recorded alongside the output files. Report sample size and execution mode with all metrics.

Recommended presentation practice: publish the benchmark JSON/CSV artifacts, explain the checkpoint contract, show the injection rejection, show a simulated risky-action pause, and show the report-write approval in the dashboard. Do not claim the 20 prompts prove financial accuracy; they validate control flow and measurable runtime behavior.

## Project structure

```text
GENAI-02/
├── src/
│   ├── agents/             # Planner, Researcher, Coder, Reviewer
│   ├── graph/              # typed state and guarded LangGraph workflow
│   ├── mcp_servers/        # search, SEC, workspace, Postgres, Playwright tools
│   ├── persistence/        # AsyncPostgresSaver and operational run/event store
│   ├── sandbox/            # E2B adapter and bounded execution
│   ├── security/           # injection, PII, code and action checks
│   └── telemetry/          # OpenTelemetry spans and metrics
├── ui/                     # Streamlit approval console + API client
├── evaluation/             # 20 tasks, live runner, aggregate metrics
├── tests/                  # unit and mocked API contract tests
├── docker-compose.yml      # Postgres, Jaeger, Collector, Prometheus, API, UI
└── README.md
```

## Production hardening before public launch

- Configure a strong backend bearer token and terminate TLS at the ingress/reverse proxy.
- Use managed PostgreSQL with private networking, backups, retention, and least-privilege credentials.
- Set `SANDBOX_MODE=e2b` only with a valid key and review E2B workspace/network controls.
- Put the API behind rate limiting and per-user identity/authorization; the demo bearer token is a shared service credential, not multi-tenant authentication.
- Restrict CORS and browser domains to the deployed UI and approved sources.
- Configure OTLP exporter authentication and protect Jaeger/Prometheus from public access.
- Review tool permissions and output artifacts before connecting additional MCP servers or real external mutation tools.


## Architecture and deployment documentation

- [System architecture and workflow state diagram](docs/ARCHITECTURE.md)
- [HTTP API reference](docs/API_REFERENCE.md)
- [Operations, configuration, and verification](docs/OPERATIONS.md)
- [Monorepo deployment guide](../docs/DEPLOYMENT_GUIDE.md)
- [Git strategy](../docs/GIT_STRATEGY.md)
- [Release ownership and publication gate](../docs/RELEASE_GOVERNANCE.md)
