# New Frontend (Vite + Express + tRPC) — Local Docker Setup

This document explains how the experimental new frontend at
`c:\Projects\Demo-New-Frontend-codex-langextract-spike\Demo-New-Frontend-codex-langextract-spike\`
is wired into the existing `core-backend.V4` Docker stack so all the
moving pieces (db, redis, rag-service, backend, **new frontend**, mysql)
start from a single `docker compose up`.

---

## 1. What runs and where

After `docker compose up --build` from `core-backend.V4/`:

| Service | Container | Host port | Purpose |
|---|---|---|---|
| `db` | `themison-db` | `54322` | PostgreSQL 16 + pgvector — used by core-backend & rag-service |
| `redis` | `themison-redis` | `6379` | Redis 7 — caches + ingestion job queue |
| `rag-service` | `themison-rag-service` | `50051` | Python gRPC RAG microservice |
| `backend` | `themison-backend` | `8080` | FastAPI core API |
| `db_mysql` | `themison-mysql` | `3306` | MySQL 8.4 — Drizzle schema for the new FE |
| **`new_frontend`** | `themison-new-frontend` | **`3000`** | Vite SPA + Express + tRPC server |
| `studio` | `themison-studio` | `3001` | Optional Supabase Studio UI for the Postgres DB |

Open the new frontend at <http://localhost:3000>.

---

## 2. Required `.env` entries (in `core-backend.V4/`)

The `docker-compose.yml` reads these from your `.env` file:

```env
# Existing (already required by the original stack)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
UPLOAD_API_KEY=any-string-you-pick
AUTH_DISABLED=true
ALLOW_ALL_ORIGINS=true

# New (only needed if you run the new frontend service)
JWT_SECRET=local-dev-secret-replace-me
VITE_OAUTH_PORTAL_URL=http://localhost:9000/oauth
VITE_APP_ID=themison-fe-spike
OAUTH_SERVER_URL=http://localhost:9000
OPENAI_MODEL=gpt-4o-mini       # optional, defaults to gpt-4o-mini
GEMINI_API_KEY=                # optional
RAG_PROVIDER=internal          # optional — see §5 below
```

If you don't want to run the new frontend, you can stop just that
service (`docker compose stop new_frontend db_mysql`) — the rest of the
stack is unaffected.

---

## 3. Repo layout assumed

The compose file uses a relative build context:
`../Demo-New-Frontend-codex-langextract-spike/Demo-New-Frontend-codex-langextract-spike`.

That means the two repo folders must sit next to each other:

```
C:\Projects\
├── core-backend.V4\                                            ← compose lives here
└── Demo-New-Frontend-codex-langextract-spike\
    └── Demo-New-Frontend-codex-langextract-spike\              ← FE source
```

If the new FE moves elsewhere, update the `build.context` path in
`docker-compose.yml` accordingly.

---

## 4. How the new frontend talks to other services

### 4.1 Internal Docker network (what the FE's server sees)

Inside the Docker bridge network, services reach each other by their
service name:

| Target | URL the FE's Express server uses |
|---|---|
| Core backend (FastAPI) | `http://backend:8080` |
| RAG service (gRPC) | `rag-service:50051` |
| MySQL | `db_mysql:3306` (already encoded in `DATABASE_URL`) |

The new frontend's **browser-side code** does NOT reach the backend
directly — all API calls go to `/api/trpc` on the same host (`:3000`),
served by the Express gateway. That gateway then makes the outbound
calls to MySQL / OpenAI / etc.

### 4.2 Auth model (independent)

The new FE uses its own OAuth portal flow — separate from the
production frontend's Auth0 setup. A user logged into the production FE
at `localhost:8000` is NOT logged into the new FE at `localhost:3000`.
This is intentional for the spike phase. Service-to-service calls from
the new FE's Express to core-backend (when added — see §5) use the
shared `UPLOAD_API_KEY` rather than user JWTs.

---

## 5. Important: RAG integration is NOT functional yet

This is the **most important section to read** before you assume
end-to-end RAG works through the new FE.

The compose file passes `EXTERNAL_RAG_API_URL=http://backend:8080` and
`EXTERNAL_RAG_API_KEY=${UPLOAD_API_KEY}` into the new frontend
container. **These environment variables are pre-wired for future
integration but are NOT consumed by the new frontend's code today.**

### What's actually true

The new frontend has a `RAG_PROVIDER` flag (values `internal` |
`external`). Searching the codebase shows:

- `RAG_PROVIDER` is **read** in 5 places (`documentAIRouter.ts`,
  `documentsRouter.ts`, `trialsRouter.ts`,
  `_core/aiIntelligence.ts`, `_core/unifiedQuery.ts`).
- In every case, setting `RAG_PROVIDER=external` only **disables**
  fallback paths (e.g., the OpenAI Assistants retrieval); it does NOT
  redirect anything to `EXTERNAL_RAG_API_URL`.
- `ENV.externalRagApiUrl` and `ENV.externalRagApiKey` are exported from
  `server/_core/env.ts` but **never read by any consumer**.

### What this means in practice

Today the new frontend has two RAG paths:

1. **Local protocol context** (always active) — uses its own MySQL
   `protocols` + `protocol_chunks` tables and calls OpenAI / Gemini
   directly via `server/_core/llm.ts`. No core-backend involvement.
2. **OpenAI Assistants fallback** — used for documents that aren't in
   the local protocol context. Requires OpenAI Assistants config; can
   be disabled by setting `RAG_PROVIDER=external`.

**Neither path calls `core-backend` or `rag-service`.** The two stacks
coexist on the network but currently do not talk to each other for RAG.

### Future work to enable real integration

Wiring the new FE through `core-backend`'s `/query` and
`/upload/upload-pdf` endpoints requires actual code:

1. Create a typed HTTP client in
   `server/_core/coreBackendClient.ts` that calls core-backend's REST
   endpoints (using `EXTERNAL_RAG_API_URL` and `EXTERNAL_RAG_API_KEY`).
2. Update the five `USES_EXTERNAL_RAG` branches to actually invoke that
   client instead of just disabling local fallbacks.
3. Reconcile data shapes — core-backend returns chunks scoped to its own
   `trial_documents` table; the new FE's `documentAI` flow expects its
   own `protocols` / `protocol_chunks` rows. Either:
   - Map between the two, OR
   - Use core-backend purely for query/answer and keep ingestion local

Estimated effort: ~2–3 days. Not in scope for the local Docker
containerisation work.

---

## 6. Common operations

### Start the full stack

```bash
cd C:\Projects\core-backend.V4
docker compose up --build
```

First boot pulls images and builds the backend + new-frontend images
(~5–10 minutes). Subsequent boots reuse cached layers.

### Just the new frontend (rest already running)

```bash
docker compose up --build new_frontend
```

### Run Drizzle migrations on first boot

The new frontend doesn't auto-migrate. After the stack is up:

```bash
docker exec -it themison-new-frontend pnpm db:push
```

(Or run from the host with `DATABASE_URL=mysql://root:themison@localhost:3306/themison_fe pnpm db:push` from the new FE's repo.)

### Tail logs

```bash
docker compose logs -f new_frontend
docker compose logs -f backend
```

### Stop everything (keep volumes)

```bash
docker compose down
```

### Stop everything (wipe data)

```bash
docker compose down -v
```

This deletes Postgres, Redis, MySQL, and uploads. Use only when you
genuinely want a fresh start.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `new_frontend` container restart-loops on boot | MySQL still starting up; `depends_on.condition: service_healthy` should handle this but if MySQL takes >30s the start-period elapses | `docker compose logs db_mysql` — wait for "ready for connections", then `docker compose restart new_frontend` |
| New FE shows "no protocols loaded" | Drizzle migrations haven't been run | `docker exec -it themison-new-frontend pnpm db:push` |
| Backend can't reach RAG service | rag-service is still loading Docling models on first start (~5 min) | `docker compose logs -f rag-service` and wait for "RAG Service is ready to serve requests" |
| New FE logs `[Env] OPENAI_API_KEY missing` | `.env` doesn't have `OPENAI_API_KEY` set | Add to `.env`, then `docker compose restart new_frontend` |
| Port 3000 already in use | Another process (Next.js dev server, etc.) is bound to 3000 | Stop the conflicting process or change the published port in `docker-compose.yml` (e.g., `"3010:3000"`) |
| Can log in to one frontend but not the other | Expected — auth is independent (see §4.2) | Log in separately to each frontend |

---

## 8. Files modified for this integration

| File | Repo | Purpose |
|---|---|---|
| `Dockerfile` | new FE | Multi-stage Node 20 build for Vite + esbuild |
| `.dockerignore` | new FE | Keeps build context lean |
| `docker-compose.yml` | core-backend.V4 | Added `db_mysql` and `new_frontend` services, `mysql_data` volume |
| `docs/NEW_FRONTEND_SETUP.md` | core-backend.V4 | This document |

No code changes were made to either repo's application source — purely
infrastructure plumbing.
