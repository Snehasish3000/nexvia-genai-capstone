# GENAI-01 Backend

FastAPI backend for the Enterprise RAG Knowledge Platform. The API is async, uses Pydantic v2 contracts, structured JSON logs, JWT roles, Qdrant metadata filters, BM25/RRF, optional Cohere reranking, grounded OpenAI generation, LangSmith tracing and SQLite telemetry/cache storage.

## Quick start (no paid keys)

```bash
cd GENAI-01
cp .env.example .env
# Set JWT_SECRET to a private random value for any shared environment.
docker compose up --build -d
curl http://localhost:8000/health
```

OpenAPI / Swagger is served at `http://localhost:8000/docs`. The no-key profile uses deterministic feature-hash embeddings and an extractive mock generator. It is intended for deterministic tests and evaluator walkthroughs, not semantic-quality production use.

Create a demo role token:

```bash
curl -X POST http://localhost:8000/api/v1/auth/demo-token \
  -H 'Content-Type: application/json' \
  -d '{"username":"finance-reviewer","role":"finance"}'
```

Use the returned `access_token` as a Bearer token on `/api/v1/chat`. The demo token endpoint is disabled when `ENVIRONMENT=production` or `ALLOW_DEMO_AUTH=false`. Production deployments should connect this dependency to a real identity provider and set a managed, rotated JWT signing secret.

## Populate mixed-format knowledge sources

Generate a policy DOCX and download official Python/Kubernetes documentation, then ingest into the running stack:

```bash
cd GENAI-01/backend
python scripts/populate_demo_corpus.py --output-dir ../sample-data --ingest
```

To include a real SEC EDGAR 10-K filing, supply an identifying User-Agent as required by SEC fair-access expectations:

```bash
SEC_USER_AGENT='Your Organization research-team@example.com' \
python scripts/populate_demo_corpus.py --output-dir ../sample-data --include-sec --ingest
```

The SEC HTML filing is preserved, and a readable PDF text packet is generated for the PDF ingestion path. The generated internal policy document includes finance, data-protection, identity, incident-response and engineering-change controls. File upload and URL ingestion are admin-only.

## Live model providers

Set these in `.env` when live calls are available:

```dotenv
EMBEDDING_PROVIDER=openai
LLM_PROVIDER=openai
OPENAI_API_KEY=...
COHERE_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
```

Qdrant collection size must match `VECTOR_SIZE`. The default 384 dimensions are compatible with `text-embedding-3-small` when the dimensions parameter is set. The configured token cost rates are estimates for reporting; update them to the rates applicable to the model/account being benchmarked. Cohere reranking gracefully uses the hybrid candidate order when no Cohere key is configured.

## Tests

```bash
cd GENAI-01
python -m pip install -r backend/requirements.txt
pytest -q
```

The tests use deterministic embeddings, an in-memory vector-store adapter, a mock generator and a temporary SQLite database. They do not need Qdrant, OpenAI, Cohere or LangSmith credentials. The same service contracts are used by the live adapters.

## Security and operating notes

- Every Qdrant dense query carries an `allowed_roles` payload predicate. The hybrid lexical corpus is built only from a role-filtered Qdrant scroll; the filter is applied before BM25 scoring. Ingestion always adds `admin` to the document's allowed roles, while other roles are explicit grants.
- JWT authorization is checked before endpoint handlers; admin upload, URL ingestion and aggregate stats require `admin`.
- URL ingestion rejects non-HTTP(S), credential-bearing, local, private, loopback, link-local and reserved destinations, including resolved private IP addresses after redirects.
- Prompt injection patterns are blocked before model execution; email/phone/SSN patterns are redacted before generation and trace capture. Retrieved document text is treated as untrusted evidence rather than instructions.
- Cache keys are role-, retrieval-strategy-, top-k-, embedding-model- and generation-model-scoped, with semantic query matching. Successful document ingestion invalidates response cache entries.
- Token counts, configurable estimated USD cost, query latency, retrieval mode, cache hits and abstention are written to SQLite. `/metrics` exposes Prometheus counters and latency histograms.
- The current BM25 adapter materializes role-authorized chunks from Qdrant (up to 100,000 per request) for lexical scoring. For larger corpora, promote lexical retrieval to a dedicated sparse index (for example Qdrant sparse vectors or OpenSearch BM25) while retaining the same RBAC filter and RRF contract.
- SQLite is suitable for a single API instance and capstone deployment. Multi-replica production should move feedback, query telemetry and semantic cache to PostgreSQL/Redis with migrations and retention policies.
