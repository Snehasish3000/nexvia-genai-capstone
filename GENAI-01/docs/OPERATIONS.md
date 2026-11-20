# GENAI-01 — Operations, Configuration, and Verification

## Local start

Run from `GENAI-01/`:

```bash
cp .env.example .env
```

Set a unique local `JWT_SECRET`, then:

```bash
docker compose up --build -d
docker compose ps
```

Open:

- React UI: `http://localhost:5173`
- Swagger: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`
- Qdrant REST: `http://localhost:6333`

Stop the stack with `docker compose down`; retain named volumes with this command. `docker compose down -v` deletes local Qdrant and API data volumes.

## Configuration groups

The authoritative defaults are in `.env.example` and `backend/app/core/config.py`.

| Group | Variables | Notes |
|---|---|---|
| Runtime | `APP_NAME`, `ENVIRONMENT`, `LOG_LEVEL`, `API_PREFIX` | `ENVIRONMENT=production` imposes security validation |
| Auth | `ALLOW_DEMO_AUTH`, `JWT_SECRET`, `JWT_ISSUER`, `JWT_EXPIRY_MINUTES` | Production requires a high-entropy 32+ character secret and demo auth disabled |
| CORS / input | `CORS_ORIGINS`, `MAX_QUERY_CHARS`, `MAX_UPLOAD_BYTES` | Set exact frontend origins; avoid wildcard CORS |
| Vector store | `VECTOR_STORE`, `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `VECTOR_SIZE` | Collection dimension must equal embedding output dimension |
| Retrieval | `DENSE_TOP_K`, `LEXICAL_TOP_K`, `FINAL_TOP_N`, `MIN_RELEVANCE_SCORE`, `RRF_K`, `PARENT_EXPANSION` | Tune against a fixed evaluation set, not intuition alone |
| Providers | `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `LLM_PROVIDER`, `OPENAI_API_KEY`, `COHERE_API_KEY` | `hash` / `mock` modes are key-free deterministic tests |
| Ops store/cache | `SQLITE_PATH`, `CACHE_TTL_SECONDS`, `SEMANTIC_CACHE_THRESHOLD`, `CACHE_ENABLED` | SQLite persistence is filesystem-bound |
| Tracing | `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | Keep tracing secrets in backend environment only |

### Provider and vector-dimension changes

The bundled hash embedding mode uses the configured `VECTOR_SIZE` default of 384. `text-embedding-3-small` returns a 1536-dimensional vector unless dimensions are explicitly constrained by the provider. Before switching providers:

1. Select the embedding model and intended dimension.
2. Set `VECTOR_SIZE` to the exact returned dimension.
3. Create a new Qdrant collection name for the new embedding space.
4. Re-ingest every source document.
5. Run the evaluation suite and compare retrieval metrics before switching the active collection.

Never mix vectors from different embedding spaces in one collection.

## Corpus population

```bash
cd backend
python scripts/populate_demo_corpus.py
```

The script supports the local sample corpus and documented dataset flags. SEC EDGAR requests require a descriptive `SEC_USER_AGENT`. Treat downloaded SEC filings and official documentation as source material; do not ingest private company data without authorization.

## Tests and evaluation

From `GENAI-01/`:

```bash
python -m pip install -r backend/requirements.txt
pytest -q
python evaluation/generate_dataset.py
python evaluation/run_ragas.py --help
```

The evaluation runner needs the live API/providers and RAGAS dependencies for scored runs. Do not report placeholder or unexecuted scores as measured results. Keep the generated JSON/CSV/Markdown reports versioned only when they are actual, reproducible runs and do not contain restricted source content.

## Health and metrics

- `GET /health` checks the configured vector store.
- `GET /metrics` exposes Prometheus counters/histograms for query volume and latency.
- Each response exposes a request ID and process time.
- LangSmith tracing is opt-in and requires a backend API key.
- Admin stats include document count, chunk count, feedback volume, query count, average latency, estimated cost, and cache entries.

Protect `/metrics`, `/docs`, and administrative endpoints at an ingress layer when deployed beyond a controlled evaluation environment.

## Recovery and retention

- Qdrant is the system of record for dense vectors and payload metadata; take backups before destructive collection migrations.
- SQLite contains operational metadata, feedback, metrics, and semantic cache. Back up its file while the service is stopped or use a SQLite-consistent backup mechanism.
- The application cache is an optimization only. It must never be used to bypass role isolation; the current cache key includes role and cache scope.
- After role policy changes, consider clearing cache and re-running access-isolation tests.

## Pre-release checks

- [ ] `ENVIRONMENT=production` and `ALLOW_DEMO_AUTH=false` once a real identity flow is implemented.
- [ ] Replace demo-token authentication with a trusted identity provider and server-side role provisioning before public access.
- [ ] Set high-entropy JWT, Qdrant, LLM, Cohere, and LangSmith secrets outside Git.
- [ ] Configure exact HTTPS CORS origins and TLS.
- [ ] Confirm Qdrant payload filters are enforced before dense and lexical result exposure.
- [ ] Test negative RBAC cases for every role and document type.
- [ ] Set provider spend limits and request-rate limits.
- [ ] Test citation faithfulness, unanswerable questions, and stale-cache invalidation.
- [ ] Provide durable storage for SQLite or implement a tested PostgreSQL repository adapter.
- [ ] Back up Qdrant and operational data; rehearse restore.
