# Gemini Migration Decision Brief — OpenAI → Vertex AI Embeddings

> Internal decision-support doc for the tech lead. Companion to
> `TECHNICAL_BRIEFING.md`. Scope: replace OpenAI's embedding stack
> (and the incidental `gpt-4o-mini` fallback) with Google Gemini via
> **Vertex AI**. Anthropic Claude generation is treated as out-of-scope
> for the swap but commented on in section 8.

---

## 1. Executive summary

**Effort: medium — ~4–6 engineering days end-to-end** (split: ~2 days code,
~1 day infra/IAM, ~1–2 days re-embedding + validation, ~0.5 day rollout).
The change touches **two repos** (`core-backend.V4`, `rag-service`),
**one new vector column** in Postgres, **one HNSW index** rebuild, and
a one-shot re-embedding of every chunk currently in
`document_chunks_docling` (semantic cache can be wiped — it self-heals).

**Recommendation: do it, but phase it.** Add Gemini as a second embedding
provider behind a setting flag, dual-write to a new `embedding_gemini`
column, cut reads over once quality is validated against a held-out
question set, then drop OpenAI. The codebase already has a precedent
for this pattern (`embedding_large` migration, see
`migrations/upgrade_embedding_3072.sql`).

---

## 2. What OpenAI is used for today

OpenAI is wired in at exactly one module — `app/core/openai.py` — and
reused everywhere via the singleton imports. The same shape repeats in
`rag-service`. There is **no** OpenAI use outside embeddings + a
`gpt-4o-mini` fallback for the agentic-RAG path.

| File | Purpose | Model | Dim | Notes |
|---|---|---|---|---|
| `app/core/openai.py:17–21` | Primary embedding singleton (`embedding_client`) | `text-embedding-3-small` (configurable) | 1536 | Driven by `settings.embedding_model` / `settings.embedding_dimensions` |
| `app/core/openai.py:25–29` | Migration-era small client | `text-embedding-3-small` | 1536 | Used by backfill scripts |
| `app/core/openai.py:31–35` | Migration-era large client | `text-embedding-3-large` | 2000 (HNSW limit) | Phase-3 dual-write target |
| `app/core/openai.py:39–47` | `structured_llm` for RAG structured-output | `gpt-4o-mini` | — | Bound to `DoclingRagStructuredResponse` |
| `app/core/openai.py:50–54` | `llm` general-purpose ChatOpenAI | `gpt-4o-mini` | — | Used by agentic RAG agent + retrieval-tool |
| `app/services/doclingRag/rag_ingestion_service.py:15,194,197` | Embeds chunks at ingest time | inherits singleton | 1536 | Single batched call per document |
| `app/services/doclingRag/rag_retrieval_service.py:64–66` | Embeds query for vector search | inherits singleton | 1536 | Wrapped in `run_in_thread` |
| `app/services/cache/semantic_cache_service.py:78–93` | pgvector cosine query against cached embeddings | n/a (consumes embedding) | 1536 | Threshold 0.90, scoped per `document_id` |
| `app/services/agenticRag/tools/documents_retrieval_generation_tool.py:228` | Embed-and-search inside an agent tool | inherits singleton | 1536 | Plus `llm.invoke` (gpt-4o-mini) at line 138 |
| `app/services/agenticRag/agent.py:13,39` | Tool-binding LLM | `gpt-4o-mini` via `llm` | — | `llm.bind_tools(...)` |
| `app/services/indexing/document_service.py:17,36,353` | Legacy non-Docling indexing path | inherits singleton + `LLM_MODEL_NAME = "gpt-4o-mini"` | 1536 | Older code path, still imports |
| `app/api/routes/upload.py:281–282` | Direct embedding call from upload flow | inherits singleton | 1536 | Trivial swap |
| `app/api/routes/query.py:14,66` | Wires `embedding_client` into retrieval service | inherits singleton | 1536 | DI plumbing only |
| `scripts/backfill_embeddings.py:33,90` | Existing backfill driver | `embedding_client_large` | 2000 | Useful template for re-embed script |
| **rag-service** `clients/openai_client.py` (whole file) | Mirror of core-backend client | configurable | 1536 | Same `langchain_openai.OpenAIEmbeddings` |
| **rag-service** `services/ingestion_service.py:15,162` | Embeds chunks | inherits | 1536 | Same call shape |
| **rag-service** `services/retrieval_service.py:13,42` | Embeds query | inherits | 1536 | Same call shape |

Net: **one provider abstraction** (`OpenAIEmbeddings`), two singletons
(`embedding_client` + `llm`), used in ~10 import sites. The fallback LLM
(`gpt-4o-mini`) is only on the **agentic-RAG side path**, not the main
Docling RAG flow — main RAG generation is Anthropic Claude in
`rag-service/services/generation_service.py`.

---

## 3. Gemini equivalent options

### Comparison

| Property | OpenAI `text-embedding-3-small` | Vertex AI `text-embedding-005` (recommended) | Vertex AI `text-multilingual-embedding-002` |
|---|---|---|---|
| Native dim | 1536 | 768 | 768 |
| Configurable output dim | yes (256–1536) | yes (`output_dimensionality`: 256/512/768) | yes |
| Max input tokens | 8191 | 2048 | 2048 |
| Auth | API key (`OPENAI_API_KEY`) | GCP service account (ADC) — same SA the Cloud Run service already uses | same |
| Pricing (illustrative; **confirm at sign-off**) | ~$0.02 / 1M tokens | ~$0.025 / 1M tokens (Vertex) | ~$0.025 / 1M tokens |
| Region | global (US-routed by default) | `europe-west1` available — keeps GDPR boundary | same |
| GCP integration | none — outbound HTTPS to OpenAI | native — VPC-SC, IAM, Cloud Audit Logs | same |
| Task-type aware | no | yes (`RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` / `SEMANTIC_SIMILARITY`) | yes |
| LangChain class | `langchain_openai.OpenAIEmbeddings` | `langchain_google_vertexai.VertexAIEmbeddings` | same |

### Recommendation

**`text-embedding-005`** with `output_dimensionality=768`, task-type
`RETRIEVAL_DOCUMENT` for ingestion and `RETRIEVAL_QUERY` for retrieval.
Reasons:

1. **Regional GDPR alignment.** Vertex AI in `europe-west1` keeps trial
   text inside the same region as the Postgres VM. Today, every chunk
   is shipped to OpenAI US for embedding — a known posture gap flagged
   in `TECHNICAL_BRIEFING.md`.
2. **Single GCP control plane.** Auth becomes the existing
   service-account flow — no separate API-key secret to rotate, IAM
   audit trail in Cloud Audit Logs, fits existing
   `roles/secretmanager.secretAccessor` story.
3. **Task-type awareness.** OpenAI has no notion of asymmetric
   query-vs-document embeddings; Gemini does, which typically yields a
   measurable retrieval-quality bump on RAG benchmarks.
4. **Lower dim = smaller index, faster search.** Going 1536 → 768 cuts
   index storage roughly in half and shortens HNSW cosine ops. Your
   pgvector HNSW config (`m=16, ef_construction=64`) is already
   appropriate.

**Not recommended:** Google AI Studio API (`generativelanguage.googleapis.com`).
That path uses an API key, sits outside the GCP IAM perimeter, and
loses the in-region routing guarantee. Vertex AI is the right fit for
this deployment.

---

## 4. Code changes required

Effort numbers are uninflated — straightforward swaps, mostly
mechanical, but multiplied across two repos.

### core-backend.V4

| File | Change | Hours |
|---|---|---|
| `requirements.txt` | Add `langchain-google-vertexai>=2.0.0`, `google-cloud-aiplatform>=1.60.0`. Keep `langchain_openai` until cutover, then remove. | 0.25 |
| `app/config.py:16,55–56` | Add `gcp_project_id` (already present as `gcs_project_id` — reuse), `vertex_ai_location: str = "europe-west1"`, `embedding_provider: Literal["openai","gemini"] = "openai"`, change defaults of `embedding_model` to `"text-embedding-005"` and `embedding_dimensions` to `768` once flipped. | 0.5 |
| `app/core/openai.py` | Rename to `app/core/embeddings.py`. Replace body with provider switch: if `embedding_provider == "gemini"`, instantiate `VertexAIEmbeddings(model_name=..., project=..., location=..., dimensions=768)`. Keep `embedding_client` symbol stable so importers don't change. Drop `gpt-4o-mini` ChatOpenAI singletons OR swap to `ChatVertexAI(model="gemini-2.5-flash")` for `llm` and `structured_llm`. | 3 |
| `app/services/doclingRag/rag_ingestion_service.py:15` | Update import path if file renamed. No behavioural change — `aembed_documents` exists on `VertexAIEmbeddings`. | 0.25 |
| `app/services/doclingRag/rag_retrieval_service.py:64–66` | Same — import-path-only. `embed_query` works identically. | 0.25 |
| `app/services/cache/semantic_cache_service.py` | No change. Reads/writes raw `List[float]` — provider-agnostic. | 0 |
| `app/services/agenticRag/agent.py`, `app/services/agenticRag/tools/documents_retrieval_generation_tool.py` | Replace `llm` import with the new ChatVertexAI singleton (or feature-flag both). The tool also calls `embedding_client.embed_query` — same import-path update. | 1 |
| `app/services/indexing/document_service.py:14,17,36` | Either remove if dead path, or swap `ChatOpenAI`/`OpenAIEmbeddings` to Vertex equivalents. Confirm whether this path is still reachable — `SYSTEM_OVERVIEW.md` describes only the Docling pipeline as the production path. | 1.5 |
| `app/api/routes/upload.py:281`, `app/api/routes/query.py:14` | Import path update only. | 0.25 |
| `app/models/chunks_docling.py:28,36` | Add new column `embedding_gemini: Vector(768)` + matching HNSW index. Keep existing `embedding` and `embedding_large` columns until cutover. | 0.5 |
| `app/models/semantic_cache.py:28` | Add `query_embedding_gemini: Vector(768)` + HNSW index. | 0.5 |
| `scripts/backfill_embeddings.py` | Clone to `scripts/backfill_embeddings_gemini.py` — swap client, target column, batch tuning (Vertex has different per-request limits, typically 250 inputs/request). | 2 |
| `tests/conftest.py:339`, `tests/test_rag_ingestion_service.py`, `tests/test_chunks_model.py` | `mock_embedding_client` already abstracts the provider — only fixture vector dim needs updating (1536 → 768). | 1 |
| `.github/workflows/deploy-cloud-run.yml:64` | Remove `--set-secrets="OPENAI_API_KEY=..."` once Gemini-only. Add nothing — Vertex auth uses the runtime SA. Add `--set-env-vars="GCP_PROJECT_ID=..."` + `VERTEX_AI_LOCATION=europe-west1` + `EMBEDDING_PROVIDER=gemini`. | 0.5 |

### rag-service

| File | Change | Hours |
|---|---|---|
| `requirements.txt` | Same dep additions. | 0.25 |
| `src/rag_service/config.py:13,24–25,50` | Add `gcp_project_id`, `vertex_ai_location`, `embedding_provider`. Bump default `embedding_model` to `text-embedding-005`, `embedding_dimensions` to 768 at cutover. | 0.5 |
| `src/rag_service/clients/openai_client.py` | Rename to `embedding_client.py`. Provider switch identical to core-backend. | 1.5 |
| `src/rag_service/services/retrieval_service.py:13`, `services/ingestion_service.py:15` | Import-path-only. | 0.25 |
| `src/rag_service/models/chunks.py:28,35` | Add `embedding_gemini: Vector(768)` + HNSW index. | 0.5 |
| `src/rag_service/models/semantic_cache.py:28` | Add `query_embedding_gemini: Vector(768)` + index. | 0.5 |
| Deploy workflow | Remove `OPENAI_API_KEY` secret, add Vertex env vars, ensure runtime SA has `roles/aiplatform.user`. | 0.5 |
| Tests under `tests/` | Update mocked vector dimensions. | 1 |

### gRPC contracts (`protos/` in both repos)

**No change.** Embeddings are not on the wire — they're computed on
each side from `query`/chunk text. The wire protocol stays identical.
This is the single biggest reason this migration is "medium" not
"large": no contract churn, no cross-version compatibility
choreography.

**Total code effort: ~16 hours / ~2 dev-days.**

---

## 5. Database & infrastructure changes

### Strategy: dual-column, dual-write, then drop

The repo already has the precedent in
`migrations/upgrade_embedding_3072.sql`: add a new column, dual-write,
reindex, cut over reads. Reuse the pattern.

Three tables touched:

1. `document_chunks_docling.embedding_gemini` — `Vector(768)`
2. `semantic_cache_responses.query_embedding_gemini` — `Vector(768)`
3. (Future cleanup — drop `embedding`/`embedding_large` after cutover.)

### DBeaver / `psql` script outline

```sql
-- migrations/add_embedding_gemini.sql
-- Migration: add Gemini (text-embedding-005, 768d) embedding columns
-- Run with: psql $SUPABASE_DB_URL -f migrations/add_embedding_gemini.sql

-- 1. New embedding columns (nullable so existing rows don't break)
ALTER TABLE document_chunks_docling
  ADD COLUMN IF NOT EXISTS embedding_gemini vector(768);

ALTER TABLE semantic_cache_responses
  ADD COLUMN IF NOT EXISTS query_embedding_gemini vector(768);

-- 2. HNSW indexes (CONCURRENTLY in production; plain CREATE in DBeaver
--    if a maintenance window is acceptable)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_embedding_gemini_hnsw
  ON document_chunks_docling
  USING hnsw (embedding_gemini vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_semantic_cache_embedding_gemini_hnsw
  ON semantic_cache_responses
  USING hnsw (query_embedding_gemini vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 3. Verify
SELECT
    'document_chunks_docling' AS table_name,
    COUNT(*) AS total_rows,
    COUNT(embedding) AS rows_with_openai,
    COUNT(embedding_gemini) AS rows_with_gemini
FROM document_chunks_docling
UNION ALL
SELECT
    'semantic_cache_responses',
    COUNT(*),
    COUNT(query_embedding),
    COUNT(query_embedding_gemini)
FROM semantic_cache_responses;
```

### Backfill plan

Run `scripts/backfill_embeddings_gemini.py` (clone of the existing
2000-dim backfill) **after** the column add and before flipping
`embedding_provider=gemini`. Numbers from the existing script (`scripts/backfill_embeddings.py`):

- Default batch size 100, 1s rate-limit delay.
- Vertex AI quota in `europe-west1` is typically 600 RPM per project for
  embeddings — confirm with the project's Vertex quotas page; if 600
  RPM, batch size of 250 inputs gets ~2500 chunks/min.
- For semantic cache: **don't backfill**. Truncate it.
  `TRUNCATE semantic_cache_responses` is safe — it's a self-warming
  cache; it'll re-fill from the next ~24 hours of traffic. Cross-dim
  cosine queries between OpenAI and Gemini vectors are meaningless, so
  mixing dims in the cache table during transition would produce
  garbage hits. Wipe and rebuild.

### Cutover sequence

1. Deploy code with `embedding_provider=openai` (no behaviour change).
2. Run the SQL above (idempotent, safe).
3. Run backfill script — populates `embedding_gemini` for every
   existing chunk. Idempotent and resumable per the existing script's
   pattern.
4. Switch `embedding_provider=gemini` in Cloud Run env. Reads now go to
   `embedding_gemini` column; new ingests write Gemini vectors. Update
   the SQL in `rag_retrieval_service.py` and `semantic_cache_service.py`
   to reference `embedding_gemini` (or — cleaner — alias both columns
   into the same name via a view, but a 5-line code change is simpler).
5. Truncate `semantic_cache_responses`.
6. Monitor. Roll back by flipping the env var if quality regresses
   (see section 7).
7. After 1–2 weeks of healthy traffic: drop `embedding`,
   `embedding_large`, `query_embedding` columns and their indexes.

---

## 6. Operational changes

### Auth model

| Item | Today (OpenAI) | After (Vertex AI) |
|---|---|---|
| Credential | `OPENAI_API_KEY` (from Secret Manager) | Application Default Credentials — the Cloud Run runtime service account |
| Secret rotation | Manual (key in Secret Manager) | None — IAM-managed |
| IAM role on runtime SA | none needed for OpenAI | **add `roles/aiplatform.user`** on the runtime SA in project `braided-visitor-484216-i0` |
| Audit | none — opaque | Cloud Audit Logs entries per call |

The runtime service accounts are already used for GCS access (see
`gcs_credentials_path` in `app/config.py:34`), so this is one IAM
binding per service account, not new infrastructure.

### Secret Manager

- Remove: `OPENAI_API_KEY` (in
  `.github/workflows/deploy-cloud-run.yml:64`) once cutover is final.
- Add: nothing. Vertex auth is via ADC on Cloud Run.
- Keep: `ANTHROPIC_API_KEY` (Claude generation stays).

### Region

Keep `europe-west1`. Vertex AI offers `text-embedding-005` in that
region. **Do not set `location="us-central1"`** — that would defeat
the GDPR-alignment win.

### Network egress

Cloud Run → Vertex AI traffic goes over Google's internal network
when the regions match (no external egress). This is also a small
billing improvement vs. the current outbound-to-OpenAI hops.

---

## 7. Risks & rollback plan

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Gemini retrieval quality is worse than OpenAI on clinical-trial text | Medium | High — bad answers | Hold a 50–100 question eval set with ground-truth chunks. Run both providers, compare top-K hit rate. Block cutover on parity-or-better. |
| Re-embedding cost surprise | Low | Low | At ~$0.025/1M tokens × ~750 tokens/chunk, even 100k chunks ≈ 75M tokens ≈ ~$2. Negligible. |
| Vertex quota throttling during backfill | Medium | Medium (slow, not broken) | Run backfill off-peak; pre-request a quota bump if chunk count > 100k; the script's rate-limit knob already exists. |
| Semantic cache contamination from mixed dims | High if not handled | High — wrong answers cached | **Truncate the cache** at cutover. Don't try to migrate it. |
| BM25 fallback path coverage if Vertex is unreachable | Low | Low | Hybrid search is already enabled (`hybrid_search_enabled=True` in `app/config.py:40`), so BM25 produces results even with embedding service down — but query embedding generation itself would error and 500 the request. Worth wrapping `embed_query` in a try/except and returning BM25-only on Vertex outage. ~30 min change. |
| `gpt-4o-mini` agentic-RAG path silently keeps using OpenAI | Medium | Low | Easy to miss; flagged in section 4. Either swap to `gemini-2.5-flash` or remove the OPENAI_API_KEY only after confirming all `ChatOpenAI` usages are gone. |
| `langchain-google-vertexai` API drift vs. `langchain_openai` | Low | Low | Both expose `aembed_documents` / `embed_query` / `aembed_query`. Surface is small. |

### Rollback

The dual-column design is the rollback. If quality regresses after
cutover:

1. Flip `embedding_provider=openai` in Cloud Run env (no redeploy
   needed if you wire it as a deploy-time env var; ~30s if it requires
   a redeploy).
2. Reads return to the `embedding` column.
3. Truncate semantic cache again to flush any Gemini-shaped writes.
4. New ingests go back to OpenAI. Gemini column stays as cold storage
   in case you want to retry.

The window during which rollback is cheap lasts as long as you keep
both columns. Recommend keeping them for 2 weeks post-cutover.

---

## 8. Should the generation model also move to Gemini?

Short answer: **no, not at the same time**. The two swaps are
**orthogonal** and should stay that way.

### The case for keeping Claude

- Generation quality on long-context, citation-grounded clinical text
  is a known strength of Claude Opus/Sonnet. The system already prompt-
  caches the system prompt (`TECHNICAL_BRIEFING.md` line 117),
  amortising per-query cost.
- Replacing both providers in one PR doubles the variables you have to
  hold constant when measuring retrieval quality regressions.
- Cost: Claude generation is the single highest per-query line item,
  but it's also the most quality-sensitive. Embedding swap saves
  little; generation swap saves a lot but risks the user-visible
  output.

### The case for going all-Gemini eventually

- One vendor, one billing relationship, one DPA. Cleaner contracts.
- Vertex AI hosts Anthropic's Claude models too — so the "all on
  Vertex" story can be achieved without dropping Claude. That's
  probably the right end-state: Claude-on-Vertex for generation,
  Gemini for embeddings, both authenticated via ADC.
- Gemini 2.5 Pro is competitive with Claude Sonnet on RAG benchmarks
  for many domains; medical/regulatory text is the open question.

### Are the two swaps independent?

Yes. The embedding model and the generation model interact only
through the chunk text passed to the LLM as context — neither sees the
other's vector space. As long as the column dimension is consistent
across writers and readers, you can swap embeddings today and
generation never (or vice versa). No code change in
`generation_service.py` or the prompt-caching layer is needed.

**Recommendation: ship embedding swap. Re-evaluate generation in 1–2
quarters once you have steady-state metrics on the embedding side.**

---

## 9. Recommendation

**Do it. Phase it. Start now.**

The work is bounded (~4–6 days), the architecture supports it without
contract churn, the precedent is in the repo, and the GDPR-alignment
win is real and durable. The dual-column rollback design means the
worst case is "we kept the OpenAI column for nothing" — not "we broke
the cache."

Suggested phasing:

| Phase | Scope | Day budget |
|---|---|---|
| 1. Dual-provider code | Add `embedding_provider` flag, Vertex client, new column + index in both repos. Ship behind `embedding_provider=openai` (no behavior change). | 2 |
| 2. Backfill | Run Gemini backfill script. Verify counts. | 0.5 + wall-clock |
| 3. Eval | Run held-out question set on both columns. Compare top-K recall + answer quality. Go/no-go. | 1 |
| 4. Cutover | Flip env var, truncate semantic cache, monitor for 48h. | 0.5 |
| 5. Cleanup | After 2 weeks healthy: drop OpenAI columns, drop `OPENAI_API_KEY` secret, remove `langchain_openai` from `requirements.txt`. | 0.5 |

Do **not** bundle the Claude→Gemini generation swap into this work.
That's a separate, larger decision with a different risk profile —
revisit it once embedding swap is stable.

---

## Appendix A — Files referenced

- `app/core/openai.py`
- `app/config.py:55–56`
- `app/services/doclingRag/rag_ingestion_service.py`
- `app/services/doclingRag/rag_retrieval_service.py`
- `app/services/cache/semantic_cache_service.py`
- `app/services/agenticRag/agent.py`
- `app/services/agenticRag/tools/documents_retrieval_generation_tool.py`
- `app/services/indexing/document_service.py`
- `app/api/routes/upload.py:281`
- `app/api/routes/query.py:14`
- `app/models/chunks_docling.py:28,36`
- `app/models/semantic_cache.py:28`
- `migrations/upgrade_embedding_3072.sql` (precedent)
- `migrations/add_pgvector_index.sql` (HNSW recipe)
- `scripts/backfill_embeddings.py` (template)
- `requirements.txt`
- `.github/workflows/deploy-cloud-run.yml:64`
- `C:/Projects/Themison/rag-service/src/rag_service/clients/openai_client.py`
- `C:/Projects/Themison/rag-service/src/rag_service/services/retrieval_service.py`
- `C:/Projects/Themison/rag-service/src/rag_service/services/ingestion_service.py`
- `C:/Projects/Themison/rag-service/src/rag_service/models/chunks.py`
- `C:/Projects/Themison/rag-service/src/rag_service/models/semantic_cache.py`
- `C:/Projects/Themison/rag-service/src/rag_service/config.py`
- `C:/Projects/Themison/rag-service/.github/workflows/deploy-cloud-run.yml`
