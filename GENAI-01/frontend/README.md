# GENAI-01 Frontend — Enterprise RAG Knowledge Platform

React 19 + TypeScript 7 + Vite 8 + Tailwind CSS 3 (LTS) client for the Nexvia Enterprise RAG API. The interface includes a role-aware chat workspace, SSE response rendering, source evidence inspection, query feedback, retrieval-strategy selection, and an admin ingestion/metrics console.

## Features

- **Authenticated role demo:** obtains a signed JWT from `POST /api/v1/auth/demo-token`; role changes request a new token. Available roles are `admin`, `finance`, `engineering`, and `guest`.
- **Streaming chat:** consumes the backend `text/event-stream` contract at `POST /api/v1/chat/stream`, renders token events, and hydrates the final structured `ChatResponse` from the `complete` event.
- **Evidence inspection:** each citation opens a dialog showing source name, page, excerpt, evidence chunk ID, and retrieval score.
- **Retrieval comparison:** switch between `dense`, `hybrid`, and `hybrid_rerank`; choose the requested number of source passages.
- **Feedback loop:** persists thumbs-up/thumbs-down ratings through `POST /api/v1/feedback` using the response request ID.
- **Admin console:** upload PDF/DOCX or ingest a public URL, assign `finance`, `engineering`, and/or `guest` access, and inspect source, chunk, latency, feedback, cache, and cost metrics. The API automatically retains the `admin` grant.
- **Responsive and accessible UI:** keyboard-submittable chat composer, labelled controls, focus states, semantic status messages, and a compact mobile layout.

## Local development

Start the backend and Qdrant from `GENAI-01/` first:

```bash
cp .env.example .env
# Set a unique JWT_SECRET before exposing the API.
docker compose up -d qdrant api
```

Then run the frontend in a second terminal:

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api/*` and `/health` to `http://127.0.0.1:8000`; set `VITE_DEV_API_PROXY` to change the destination. To point the browser directly at a separately hosted API, set `VITE_API_BASE_URL=https://your-api.example.com` and configure the backend's `CORS_ORIGINS` accordingly.

## Full Docker Compose experience

From `GENAI-01/`:

```bash
cp .env.example .env
# Set JWT_SECRET to a high-entropy value.
docker compose up --build -d
```

Open `http://localhost:5173`. Nginx serves the compiled SPA and reverse-proxies `/api/` and `/health` to the FastAPI service, so the browser uses one origin and does not need a permissive CORS configuration. The API docs remain at `http://localhost:8000/docs`.

## Build and type-check

```bash
cd frontend
npm install
npm run typecheck
npm run build
npm run preview
```

The production build is emitted to `dist/` and served by the included Nginx container image.

## API contract

The client is intentionally aligned to the Pydantic v2 API schemas in `backend/app/schemas.py`:

| Capability | Method and endpoint | Auth |
|---|---|---|
| Demo JWT | `POST /api/v1/auth/demo-token` | No bearer; disabled in production |
| Chat stream | `POST /api/v1/chat/stream` | Bearer JWT |
| Feedback | `POST /api/v1/feedback` | Bearer JWT |
| File ingestion | `POST /api/v1/admin/documents/upload` | Admin bearer JWT; multipart |
| URL ingestion | `POST /api/v1/admin/documents/url` | Admin bearer JWT |
| Admin metrics | `GET /api/v1/admin/stats` | Admin bearer JWT |
| Health | `GET /health` | No bearer |

The frontend does not make authorization decisions on behalf of the API. Hiding the admin tools for non-admins is a usability measure; the backend independently rejects unauthorized admin calls and applies document-role filters before retrieval.

## Security and deployment notes

- The role switcher is an evaluation-only convenience, not a production authentication system. `ALLOW_DEMO_AUTH` must be `false` in production; integrate the UI with the organization's OIDC/SSO provider and provision the backend's verified JWT claims.
- The browser stores the short-lived demo access token in `localStorage` for a reload-persistent capstone session. A production deployment should prefer secure, `HttpOnly`, `Secure`, `SameSite` cookies or a backend-for-frontend session pattern, plus a Content Security Policy and a managed identity provider.
- No API keys or model credentials are included in the frontend bundle. OpenAI, Cohere, Qdrant and LangSmith secrets remain backend-only.
- File type and size are checked client-side for fast feedback, then validated again by the backend. URL SSRF protections are enforced by the backend parser.
- The visible demo session identity is `nexvia-reviewer`; changing role obtains a newly signed token for that same demo subject.

## Notes on streaming

The API currently produces a completed grounded answer before emitting SSE token events, then sends a final `complete` event carrying the authoritative `ChatResponse`. The UI supports this contract and does not treat a partial stream as a successful result unless the completion event arrives. Replace the backend transport with model-token streaming later without changing the citation, metrics, or feedback response contract.
