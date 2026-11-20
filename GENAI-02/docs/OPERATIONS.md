# GENAI-02 — Operations, Configuration, and Verification

## Local Docker Compose

Run from `GENAI-02/`:

```bash
cp .env.example .env
```

For the no-paid-key local reviewer path, keep `SANDBOX_MODE=mock` and leave model/E2B keys empty. Set a long random `API_ACCESS_TOKEN` before sharing the service beyond localhost. Then:

```bash
docker compose up --build -d
docker compose ps
```

| Surface | Local address |
|---|---|
| Streamlit operator console | `http://localhost:8501` |
| FastAPI | `http://localhost:8010` |
| Swagger | `http://localhost:8010/api/docs` |
| OpenAPI JSON | `http://localhost:8010/api/openapi.json` |
| Jaeger UI | `http://localhost:16686` |
| Prometheus | `http://localhost:9090` |
| PostgreSQL host port | `localhost:5433` |

The Compose network uses service DNS names. The API reaches PostgreSQL at `postgres:5432` and OTLP at `otel-collector:4318`; the Streamlit service reaches the API at `app:8010`.

## Configuration reference

The authoritative values are `.env.example` and `src/config.py`.

| Group | Variables | Notes |
|---|---|---|
| Runtime | `APP_ENV`, `LOG_LEVEL`, `HOST`, `PORT` | Production mode requires `API_ACCESS_TOKEN` |
| API access | `API_ACCESS_TOKEN` | Shared bearer secret for current demo; not a per-user identity system |
| Postgres | `DATABASE_URL`, `PERSISTENCE_ENABLED`, `DATABASE_CONNECT_TIMEOUT` | Used by both `AsyncPostgresSaver` and asyncpg run store |
| Checkpoint encryption | `LANGGRAPH_AES_KEY` | Optional checkpoint serializer encryption; use a supported 32-byte key representation per installed serializer version |
| Model | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` | Empty key enables deterministic offline behavior |
| Search / SEC | `TAVILY_API_KEY`, `SEC_USER_AGENT` | Without search key, web lookup uses the configured fallback; SEC requests should identify the client |
| Sandbox | `E2B_API_KEY`, `SANDBOX_MODE`, `SANDBOX_TIMEOUT_SECONDS`, `SANDBOX_REQUEST_TIMEOUT_SECONDS` | `mock` never executes generated code on host; `e2b` requires an E2B key |
| Workspace / browser | `WORKSPACE_DIR`, `WORKSPACE_MAX_FILE_BYTES`, `BROWSER_ALLOWED_DOMAINS`, `BROWSER_NAVIGATION_TIMEOUT_MS` | Artifacts are local workspace files; browser hosts are explicitly allowlisted |
| Run budgets | `MAX_STEPS`, `MAX_REVISIONS`, `MAX_TOKENS_PER_RUN`, `MAX_COST_USD_PER_RUN`, `MAX_INPUT_CHARS`, `MAX_REPORT_CHARS` | Server budgets cannot be raised by user input above schema caps |
| Observability | `OTEL_ENABLED`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` | Local Compose uses the Collector; external environments need a reachable OTLP endpoint |

## PostgreSQL setup and persistence

The runtime creates the operational tables and calls `AsyncPostgresSaver.setup()` when persistence is enabled. Use a dedicated database and least-privilege role. For cloud Postgres:

- use the provider's current connection string and TLS settings;
- prefer a direct/session connection for checkpoint operations rather than transaction-pooling semantics that may not support session behavior;
- limit API replicas according to available database connections (the current run store opens a pool of up to six connections per process);
- enable backups/restore according to the provider's plan and verify restore before relying on checkpoints.

When PostgreSQL is unavailable in non-production mode, the run store can degrade and the checkpoint adapter can fall back to in-memory if dependencies/connection are unavailable. Production mode must not be treated as durable unless health and startup logs confirm the database/checkpointer are connected.

## Sandbox modes

### Mock mode

`SANDBOX_MODE=mock` is for unit tests, CI, and no-key demos. It does not execute generated Python on the API host. Its success is a control-flow demonstration, not proof that generated financial code ran in E2B.

### E2B mode

Set `SANDBOX_MODE=e2b` and provide `E2B_API_KEY` only in a protected server environment. Confirm the configured execution timeout and request timeout. Inspect workspace and network policies in the E2B account. Do not pass provider credentials into generated code; give the sandbox only the minimum data and capabilities needed for a task.

## Benchmark and tests

Run from `GENAI-02/`:

```bash
python -m pip install -r requirements.txt
python -m pip install -r ui/requirements.txt
pytest -q
python -m compileall -q src ui evaluation tests
python evaluation/run_benchmark.py --validate-only
```

For a live benchmark against the local API:

```bash
python evaluation/run_benchmark.py --base-url http://localhost:8010 --limit 3
python evaluation/run_benchmark.py --base-url http://localhost:8010
```

For the evaluation harness's automated approval/resume path, use `--resume-approvals` only against a disposable test environment. The runner writes timestamped JSON/CSV/Markdown files under `evaluation/results/`.

The benchmark validates expected API/control-flow checkpoints and reports success rate, average steps, latency, token/cost estimates, guardrail rate, and approval rate. It is not a complete factuality benchmark for company research. Validate financial statements, units, periods, calculations, and primary-source references manually or with a separately scored golden set before claiming factual accuracy.

## Telemetry

Local OpenTelemetry flow: API SDK → Collector OTLP HTTP on `4318` → Jaeger trace storage and Prometheus exporter. Prometheus scrapes the Collector exporter on `9464`. Do not expose Collector, Jaeger, or Prometheus dashboards publicly without authentication and network controls. In a cloud free-tier profile, set `OTEL_ENABLED=false` unless a reachable hosted OTLP endpoint is configured; `localhost` inside a hosted container is not your laptop.

## Operational risks and recovery

- PostgreSQL is the durable source for graph checkpoints and operational run events.
- The workspace volume in Docker Compose is persistent locally. Many free PaaS instances have ephemeral filesystems; a cloud restart can remove workspace artifacts. Store valuable outputs in an external object store or use a platform persistent disk.
- `API_ACCESS_TOKEN` is a shared service secret. Rotate it after reviewer access and do not paste it into screenshots, public CI logs, benchmark reports, or Git history.
- The browser tool uses an allowlist. Review allowed domains before adding broader web targets.
- The Postgres tool is intended for read-only analysis. Never point it at production business databases with write credentials.
- Simulated risky actions are inert records. Adding real email/spend integrations would require a new threat model, least-privilege credentials, idempotency, approval audit trail, and independent authorization checks.

## Pre-release checklist

- [ ] `APP_ENV=production` and high-entropy `API_ACCESS_TOKEN` set outside Git.
- [ ] `PERSISTENCE_ENABLED=true`; production startup logs confirm Postgres and checkpoint saver connected.
- [ ] Managed database backups and connection limits reviewed.
- [ ] `SANDBOX_MODE=e2b` only when live code execution is intended and E2B tenant controls are reviewed; otherwise explicitly label mock mode.
- [ ] `WORKSPACE_DIR` is persistent or artifacts are exported to durable object storage.
- [ ] Browser allowlist is minimal and SEC User-Agent is descriptive.
- [ ] OTLP endpoint is reachable, authenticated, and protected; Jaeger and Prometheus not public.
- [ ] API is behind HTTPS, rate limiting, and user-level authorization if multiple reviewers/users are admitted.
- [ ] HITL Approve/Edit/Reject behavior is exercised end-to-end against a persisted thread.
- [ ] Test max-steps, revision, token, and USD circuit breakers with deliberate adversarial cases.
- [ ] Review actual benchmark output, execution mode, sample size, and provider model before publishing metrics.
