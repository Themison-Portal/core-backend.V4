# Gemini Full Migration Playbook (Option C)

> **Scope: full removal of OpenAI _and_ Anthropic from the stack.** Replace
> embeddings, fallback LLM, and main generation model with Google Gemini
> via Vertex AI. After this migration, the only LLM provider in the
> production data path is Google.
>
> Companion to `GEMINI_MIGRATION.md` (decision brief / option B).
> If you only want to swap embeddings, read that file instead.

---

## 1. Executive summary

**Total effort: ~7–10 engineering days end-to-end.**

| Phase | Effort | Can run in parallel? |
|---|---|---|
| 0. Vertex AI / IAM / billing setup | 0.5 d | — |
| 1. Embeddings: OpenAI → Vertex `text-embedding-005` | ~4 d | — |
| 2. Generation: Anthropic Claude → Gemini 2.5 Pro | ~2 d | yes (with phase 3) |
| 3. Auxiliary LLM: `gpt-4o-mini` → Gemini 2.5 Flash | ~1 d | yes (with phase 2) |
| 4. Cleanup: drop deps, revoke keys, remove dual-provider plumbing | ~0.5 d | — |
| **Risk buffer + eval set construction** | ~1 d | — |

**Recommendation:** sequence the phases. Phase 1 has the largest blast
radius (DB schema + re-embedding); finish and validate it before touching
generation. Phases 2 and 3 can be merged into a single sprint after that.

The result is a single-cloud, EU-resident AI stack with **no outbound
calls to US providers** — a meaningful GDPR posture upgrade for tenants
processing patient data.

---

## 2. Target state

```
                   BEFORE                                AFTER
┌────────────────────────────────────┐  ┌────────────────────────────────────┐
│ Embeddings: OpenAI                 │  │ Embeddings: Vertex AI              │
│   text-embedding-3-small (1536)    │  │   text-embedding-005 (768)         │
│                                    │  │                                    │
│ Generation: Anthropic Claude       │  │ Generation: Vertex AI Gemini       │
│   claude-opus-4.x (US)             │  │   gemini-2.5-pro (europe-west1)    │
│                                    │  │                                    │
│ Aux LLM: OpenAI gpt-4o-mini        │  │ Aux LLM: Vertex AI Gemini          │
│   (agentic RAG, structured output) │  │   gemini-2.5-flash                 │
│                                    │  │                                    │
│ Auth: 2 API keys (OPENAI, ANTHRO)  │  │ Auth: 1 GCP service account        │
│ Egress: cross-Atlantic (US)        │  │ Egress: in-region (EU)             │
│ Sub-processors: Anthropic + OpenAI │  │ Sub-processors: Google only        │
└────────────────────────────────────┘  └────────────────────────────────────┘
```

---

## 3. Prerequisites

Before any code is written:

- [ ] Vertex AI API enabled on the GCP project
      (`gcloud services enable aiplatform.googleapis.com`)
- [ ] The Cloud Run runtime service accounts (`core-backend-runner@…`,
      `rag-service-runner@…`) granted `roles/aiplatform.user`
- [ ] Confirmed Vertex AI regional availability for **`europe-west1`**:
      `text-embedding-005`, `gemini-2.5-pro`, `gemini-2.5-flash` —
      verify on the live Vertex page; quotas may vary by region
- [ ] Customer's GCP billing has Vertex AI enabled and quota raised if
      backfill volume exceeds default per-minute caps
- [ ] An **eval set** of ~30–50 question/answer pairs from real protocol
      PDFs to grade quality before/after each phase (Phase 1 needs
      retrieval-only eval; Phase 2 needs end-to-end eval)
- [ ] A staging environment where you can dual-run providers without
      affecting paying tenants
- [ ] Stakeholder sign-off that the Anthropic and OpenAI contracts can be
      cancelled at the end (the savings only land when you actually stop
      paying them)

---

## 4. Phase 0 — Vertex AI setup

**Goal: have a working Vertex client in the codebase before changing any
runtime path.**

### 4.1 Add dependencies

`requirements.txt` (both `core-backend.V4` and `rag-service`):

```
google-cloud-aiplatform>=1.71.0
langchain-google-vertexai>=2.0.0
```

Remove (in phase 4): `openai`, `langchain-openai`, `anthropic`,
`langchain-anthropic`. Don't remove yet.

### 4.2 Add config

`app/config.py` — new settings:

```python
class Settings(BaseSettings):
    # … existing …
    gcp_project_id: str = ""
    gcp_region: str = "europe-west1"
    embedding_provider: Literal["openai", "vertex"] = "openai"   # flag
    generation_provider: Literal["anthropic", "vertex"] = "anthropic"
    aux_llm_provider: Literal["openai", "vertex"] = "openai"
    vertex_embedding_model: str = "text-embedding-005"
    vertex_generation_model: str = "gemini-2.5-pro"
    vertex_aux_model: str = "gemini-2.5-flash"
```

### 4.3 IAM grant (one-time)

```bash
PROJECT=braided-visitor-484216-i0

for SA in core-backend-runner@$PROJECT.iam.gserviceaccount.com \
          rag-service-runner@$PROJECT.iam.gserviceaccount.com; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA" \
    --role="roles/aiplatform.user"
done
```

No new secrets. Vertex auth uses **Application Default Credentials**
inherited from the runtime service account on Cloud Run — the SDK
auto-discovers it.

### 4.4 Verify connectivity

Add a tiny script `scripts/verify_vertex.py`:

```python
import asyncio
from langchain_google_vertexai import VertexAIEmbeddings, ChatVertexAI

async def main():
    e = VertexAIEmbeddings(model_name="text-embedding-005",
                            project="…", location="europe-west1")
    v = await asyncio.to_thread(e.embed_query, "hello world")
    print(f"embedding dim: {len(v)}")  # expect 768

    chat = ChatVertexAI(model_name="gemini-2.5-flash",
                        project="…", location="europe-west1")
    r = await chat.ainvoke([("user", "Say hi in one word.")])
    print(r.content)

asyncio.run(main())
```

Run from a Cloud Run job or locally with `gcloud auth
application-default login`. If both calls succeed, IAM is correct.

---

## 5. Phase 1 — Embeddings: OpenAI → Vertex

This is the largest phase by far because it touches the database. The
plan below mirrors the proven `migrations/upgrade_embedding_3072.sql`
pattern: dual-write → backfill → validate → cutover → drop.

### 5.1 Code changes

#### 5.1.1 Provider-agnostic embedding client

Refactor `app/core/openai.py` (rename file later to `embeddings.py`):

```python
def _build_embedding_client():
    s = get_settings()
    if s.embedding_provider == "vertex":
        from langchain_google_vertexai import VertexAIEmbeddings
        return VertexAIEmbeddings(
            model_name=s.vertex_embedding_model,
            project=s.gcp_project_id,
            location=s.gcp_region,
        )
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model=s.embedding_model,
        dimensions=s.embedding_dimensions,
        openai_api_key=s.openai_api_key,
    )

embedding_client = _build_embedding_client()
```

This is a **pure-config swap** at runtime. No call site changes — every
existing `embedding_client.aembed_query(...)` works identically.

Mirror the same in `rag-service/src/rag_service/clients/openai_client.py`
(rename to `embedding_client.py`).

#### 5.1.2 New DB columns

`app/models/chunks_docling.py`:

```python
embedding_gemini: Mapped[List[float]] = Column(Vector(768), nullable=True)
```

`app/models/semantic_cache.py`:

```python
query_embedding_gemini: Mapped[List[float]] = Column(Vector(768), nullable=True)
```

#### 5.1.3 Dual-write at ingest

`rag-service/src/rag_service/services/ingestion_service.py`:

```python
async def _insert_docling_chunks(self, document_id, document_url, chunks, embeddings):
    # … existing INSERT for `embedding` (1536 OpenAI) …

    if settings.embedding_provider == "vertex":
        # populate the 768 column for new ingestions
        gemini_embeddings = await vertex_client.aembed_documents(
            [c.page_content for c in chunks]
        )
        await self._update_gemini_column(document_id, gemini_embeddings)
```

This way, **every new document written during the migration window has
both columns populated.** Existing documents need backfill (5.4).

#### 5.1.4 Read-path switch

`rag-service/src/rag_service/services/retrieval_service.py` and the
core-backend mirror — wrap the existing pgvector query in a provider
check:

```python
column = "embedding_gemini" if settings.embedding_provider == "vertex" else "embedding"
sql = text(f"""
    SELECT pc.content, pc.page_number, pc.chunk_metadata,
           1 - (pc.{column} <=> ($1)::vector) AS similarity
      FROM document_chunks_docling pc
     WHERE pc.document_id = $2
     ORDER BY pc.{column} <=> ($1)::vector
     LIMIT $3
""")
```

`semantic_cache_service.py` — same treatment (column-name switch).

### 5.2 Database migration

New file: `migrations/add_gemini_embedding_columns.sql`:

```sql
SET search_path TO public, extensions;

-- Add the new columns alongside the existing ones (dual-column phase)
ALTER TABLE document_chunks_docling
  ADD COLUMN IF NOT EXISTS embedding_gemini vector(768);

ALTER TABLE semantic_cache_responses
  ADD COLUMN IF NOT EXISTS query_embedding_gemini vector(768);

-- HNSW index on the new column (will be empty until backfill runs;
-- index creation is fast on empty data)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_gemini_hnsw
  ON document_chunks_docling
  USING hnsw (embedding_gemini vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_semantic_cache_embedding_gemini_hnsw
  ON semantic_cache_responses
  USING hnsw (query_embedding_gemini vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Verify
SELECT column_name, data_type, character_maximum_length
  FROM information_schema.columns
 WHERE table_name IN ('document_chunks_docling', 'semantic_cache_responses')
   AND column_name LIKE '%embedding_gemini%';
```

Apply via DBeaver against the cloud DB (idempotent — safe to re-run).
Self-heal block in `app/main.py` should also be updated to add these
columns on app startup for any environment that hasn't run the SQL.

### 5.3 Truncate semantic cache

Mixing OpenAI and Gemini embeddings in the same column is meaningless —
they live in different latent spaces.

```sql
DELETE FROM semantic_cache_responses;
```

The cache rebuilds organically as users query. Brief warm-up cost only.

### 5.4 Backfill existing chunks

A one-shot script `scripts/backfill_gemini_embeddings.py`:

```python
"""Re-embed every existing chunk with Vertex text-embedding-005.

Idempotent: skips chunks that already have embedding_gemini populated.
Resumable: on failure, re-run picks up where it left off.
"""

import asyncio
from sqlalchemy import select, update
from app.db.session import async_session
from app.models.chunks_docling import DocumentChunkDocling
from langchain_google_vertexai import VertexAIEmbeddings

BATCH_SIZE = 100  # tune to Vertex per-minute quota

async def backfill():
    embedder = VertexAIEmbeddings(
        model_name="text-embedding-005",
        project="…",
        location="europe-west1",
    )

    async with async_session() as db:
        while True:
            # Fetch a batch with NULL gemini column
            rows = (await db.execute(
                select(DocumentChunkDocling)
                .where(DocumentChunkDocling.embedding_gemini.is_(None))
                .limit(BATCH_SIZE)
            )).scalars().all()
            if not rows:
                break

            texts = [r.content for r in rows]
            vectors = await asyncio.to_thread(embedder.embed_documents, texts)

            for row, vec in zip(rows, vectors):
                await db.execute(
                    update(DocumentChunkDocling)
                    .where(DocumentChunkDocling.id == row.id)
                    .values(embedding_gemini=vec)
                )
            await db.commit()
            print(f"Backfilled {len(rows)} chunks…")

asyncio.run(backfill())
```

Run from a Cloud Shell session (or a Cloud Run Job). For a tenant with
~10 protocols × ~450 chunks each = ~4,500 chunks, expect ~10–15 minutes
at default Vertex rate limits. Larger tenants may need quota raised.

### 5.5 Validation gate

Before flipping `embedding_provider=vertex` in the read path, run an
**eval harness** that:

1. Holds out 30+ question/expected-page pairs per protocol
2. Queries with both providers (OpenAI vs Vertex) using the still-dual
   embedding columns
3. Reports recall@5 and recall@20 for each
4. Compares cited-page accuracy on a sample of generated answers (this
   is a retrieval-quality gate, not a generation-quality gate yet — Claude
   is still generating)

**Pass criteria (suggested):**
- Recall@5 within 5% of the OpenAI baseline
- No regression on protocol-specific queries (e.g. inclusion criteria,
  dosing tables) — these are the queries the FE actually serves

If Vertex underperforms, options:
- Increase `output_dimensionality` to 768 (if you tried 256/512)
- Switch to `gemini-embedding-001` (3072 native dim, supports MRL down)
  but be aware it triples vector storage size
- Tweak retrieval `top_k` or rerank cutoffs

### 5.6 Cutover

Single env var flip per environment:

```bash
gcloud run services update core-backend-eu \
    --region=europe-west1 \
    --update-env-vars="EMBEDDING_PROVIDER=vertex"

gcloud run services update rag-service-eu \
    --region=europe-west1 \
    --update-env-vars="EMBEDDING_PROVIDER=vertex"
```

**Roll back:** flip back to `openai`. The OpenAI column is still
populated. Zero data loss.

### 5.7 Drop OpenAI column (deferred to Phase 4)

After 1–2 weeks of stable Vertex operation:

```sql
ALTER TABLE document_chunks_docling DROP COLUMN embedding;
ALTER TABLE document_chunks_docling RENAME COLUMN embedding_gemini TO embedding;
DROP INDEX idx_chunks_embedding_hnsw;
ALTER INDEX idx_chunks_embedding_gemini_hnsw RENAME TO idx_chunks_embedding_hnsw;
-- (same pattern for semantic_cache_responses)
```

---

## 6. Phase 2 — Generation: Claude → Gemini 2.5 Pro

This is independent of Phase 1 because generation happens **after** retrieval — the embeddings used to retrieve are decoupled from the LLM that synthesizes the answer.

### 6.1 Code changes — single service, single class

`rag-service/src/rag_service/services/generation_service.py` is the only
file with Anthropic calls in production (the local doclingRag generation
service in core-backend is dev-only). Refactor along provider boundaries:

```python
def _build_generation_client():
    s = get_settings()
    if s.generation_provider == "vertex":
        from langchain_google_vertexai import ChatVertexAI
        return ChatVertexAI(
            model_name=s.vertex_generation_model,
            project=s.gcp_project_id,
            location=s.gcp_region,
            temperature=0.0,
            max_output_tokens=4096,
        )
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=s.anthropic_model,
        anthropic_api_key=s.anthropic_api_key,
        temperature=0.0,
        max_tokens=4096,
    )
```

Replace direct `client.messages.create(...)` calls (Anthropic SDK) with
`langchain_core` invocation (or use Vertex SDK directly — pick one).

### 6.2 Prompt adaptation

This is the part that **looks small but isn't**. Claude's strengths
(strict JSON adherence, instruction following on long contexts) don't
translate 1:1 to Gemini. Specific prompt elements that need re-validation:

| Claude pattern | Gemini equivalent |
|---|---|
| `system=[{type:"text", text:"…", cache_control:{type:"ephemeral"}}]` (prompt caching) | Vertex AI **context caching** — different API, separate `cachedContents.create` call, longer minimum cache duration |
| `messages.create` with explicit `<answer><sources>` XML tags | Use Gemini's **structured output** (`response_schema` parameter) — cleaner; emit JSON natively rather than parsing XML |
| `system` prompt with strict JSON schema instructions | Tighten via `response_schema` Pydantic model + `response_mime_type="application/json"` |
| Long context (200k tokens) | Gemini 2.5 Pro supports 2M tokens — context window not a constraint |
| Tool use / function calling | Gemini supports it but the request shape differs — re-test the agentic path if used |

**Effort:** the prompt itself is the same English; the wrapper code that
formats it changes. Plan ~1 day of prompt iteration on a real eval set.

### 6.3 Validation gate (end-to-end)

Same eval set as Phase 1, but now grading **answer quality** (not just
retrieval). Suggested rubric:

- **Faithfulness** — does the answer only cite information in the
  retrieved chunks? (Gemini 2.5 Pro is generally strong here, but verify.)
- **Citation accuracy** — do the cited page numbers actually contain the
  claimed information?
- **Format compliance** — does the JSON validate against the response
  schema 100% of the time? (This was a quiet strength of Claude;
  verify Gemini matches.)
- **Latency** — Gemini 2.5 Pro is typically faster than Claude Opus
  (1.5–3s vs 5–15s for typical RAG answers). Confirm.
- **Cost** — Vertex pricing per million tokens vs Anthropic's. Run on
  representative traffic for a day to compare.

Pass criteria: ≥95% format compliance, faithfulness on par with Claude
on the eval set, no individual question regression > 1 quality grade.

### 6.4 Cutover

```bash
gcloud run services update rag-service-eu \
    --region=europe-west1 \
    --update-env-vars="GENERATION_PROVIDER=vertex"
```

Hot-swap. Roll back via the env var if issues surface.

---

## 7. Phase 3 — Auxiliary LLM: gpt-4o-mini → Gemini 2.5 Flash

`gpt-4o-mini` is used in the agentic-RAG side path (currently not on the
hot path of a typical query, but imported/loaded on app start). Three
specific call sites the code review surfaced:

- `app/core/openai.py:39–47` — `structured_llm` singleton bound to
  `DoclingRagStructuredResponse`
- `app/core/openai.py:50–54` — general-purpose `llm` used by tool calls
- `app/services/agenticRag/agent.py:13,39` — `llm.bind_tools(...)` for
  the agentic flow
- `app/services/agenticRag/tools/documents_retrieval_generation_tool.py:138`
- `app/services/indexing/document_service.py:17,36,353` — legacy
  non-Docling indexing path with `LLM_MODEL_NAME = "gpt-4o-mini"`

### 7.1 Decision: port or delete?

The `agenticRag` and `indexing` modules may be **dead code** in
production. Verify:

```bash
grep -rn "from app.services.agenticRag" app/ --include="*.py"
grep -rn "from app.services.indexing" app/ --include="*.py"
```

If neither is imported by an active route handler, **delete them**.
That's the cleanest path. Dead code that imports OpenAI is a
compliance hazard ("why does the bundle still ping OpenAI on cold
start?") regardless of runtime behaviour.

If they ARE used, port the `structured_llm` and `llm` singletons:

```python
def _build_aux_llm():
    s = get_settings()
    if s.aux_llm_provider == "vertex":
        from langchain_google_vertexai import ChatVertexAI
        return ChatVertexAI(
            model_name=s.vertex_aux_model,  # gemini-2.5-flash
            project=s.gcp_project_id,
            location=s.gcp_region,
            temperature=0.0,
        )
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model="gpt-4o-mini", api_key=s.openai_api_key, temperature=0.0)

llm = _build_aux_llm()
structured_llm = llm.with_structured_output(DoclingRagStructuredResponse)
```

`with_structured_output` works against ChatVertexAI in
`langchain_google_vertexai>=2.0.0`. Verify with a unit test.

### 7.2 Cutover

```bash
gcloud run services update core-backend-eu \
    --region=europe-west1 \
    --update-env-vars="AUX_LLM_PROVIDER=vertex"
```

---

## 8. Phase 4 — Cleanup

Once all three phases have been stable for ~1–2 weeks:

### 8.1 Drop dual-provider code

- Delete the `_build_*_client` factories' OpenAI/Anthropic branches
- Remove the `embedding_provider` / `generation_provider` /
  `aux_llm_provider` settings (hardcode Vertex)
- Drop the OpenAI `embedding` column (rename `embedding_gemini` →
  `embedding`)

### 8.2 Drop dependencies

`requirements.txt`:

```diff
- openai
- langchain-openai
- anthropic
- langchain-anthropic
+ # already added in Phase 0:
+ # google-cloud-aiplatform
+ # langchain-google-vertexai
```

### 8.3 Revoke API keys

In GCP Secret Manager:

```bash
gcloud secrets versions destroy latest --secret=OPENAI_API_KEY
gcloud secrets versions destroy latest --secret=ANTHROPIC_API_KEY
# (or just delete the secrets entirely)
gcloud secrets delete OPENAI_API_KEY --quiet
gcloud secrets delete ANTHROPIC_API_KEY --quiet
```

Cancel the OpenAI and Anthropic billing accounts. Notify procurement.

### 8.4 Update sub-processor register

Remove **OpenAI** and **Anthropic** from the customer's sub-processor
list. Update the customer's privacy notice if it named those providers.
This is a **GDPR-relevant change** that must be communicated to data
subjects per Article 28(2).

### 8.5 Update internal docs

- `SYSTEM_OVERVIEW.md` §2 — change "Anthropic — Claude (Opus class)" and
  "OpenAI — text-embedding-3-small" to a single "Google Cloud Vertex AI"
  bullet.
- `TECHNICAL_BRIEFING.md` §0, §1, §5 — same update.
- `Appendix A — GDPR Q&A` in SYSTEM_OVERVIEW: the question "What about
  data going to OpenAI and Anthropic?" can now be answered "Both have
  been removed; the only sub-processor for AI workloads is Google
  Cloud, covered under the same CDPA as the rest of the deployment."

---

## 9. Validation strategy summary

| Gate | Criterion | Block Phase X if fails |
|---|---|---|
| Phase 1 — embedding swap | Recall@5 within 5% of OpenAI baseline; no individual protocol query regression | Phase 1 cutover |
| Phase 2 — generation swap | ≥95% structured-output format compliance; faithfulness on par; latency ≤ baseline | Phase 2 cutover |
| Phase 3 — aux LLM swap | All affected unit tests pass; agentic flow (if not deleted) returns correctly-shaped tool calls | Phase 3 cutover |
| Phase 4 — cleanup | No errors in 1-week post-cutover monitoring; no OpenAI/Anthropic calls in logs | Drop deps + keys |

---

## 10. Rollback plan per phase

| Phase | If issue surfaces | Rollback action | Recovery time |
|---|---|---|---|
| 1 | Vertex retrieval recall too low | Flip `EMBEDDING_PROVIDER=openai` env var on both Cloud Run services | < 1 minute |
| 2 | Gemini answer quality regresses | Flip `GENERATION_PROVIDER=anthropic` | < 1 minute |
| 3 | Aux LLM breaks an agent | Flip `AUX_LLM_PROVIDER=openai` | < 1 minute |
| 4 (post-cleanup) | Vertex outage > 30 min | Cannot roll back — providers gone. Hard dependency on Google. | n/a — risk to accept |

This is why Phase 4 cleanup waits 1–2 weeks. Until cleanup, every phase
is reversible by toggling an env var.

---

## 11. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Vertex retrieval quality < OpenAI on niche medical queries | Medium | High (RAG returns "no info" more often) | Eval set + Phase 1 gate; fall back to gemini-embedding-001 (3072 dim) if 768 underperforms |
| Gemini structured-output less reliable than Claude | Low | Medium (downstream JSON parse errors) | Use `response_schema` Pydantic model — strict schema; validation gate at 95% |
| Vertex regional quota hit during backfill | Medium | Low (slower backfill) | Run backfill off-hours; request quota raise pre-emptively |
| Anthropic prompt caching savings lost; Vertex caching less mature | Medium | Low–Medium ($) | Measure post-cutover cost for 1 week; consider Vertex `cachedContents` API |
| Gemini 2.5 Pro changes pricing or model availability | Low | Low | Standard model-deprecation drill — same risk we accepted with Anthropic/OpenAI |
| Single-cloud lock-in | Inherent | Medium | This was already the architectural direction (GCP-native deployment). Documented trade-off. |
| Customer rejects Google as sole AI sub-processor | Low | High (deal block) | Surface during sales discovery; some EU customers prefer Google over US-only providers |

---

## 12. Files touched (final summary)

For sprint planning. Lines are approximate.

| File | Change | LOC delta |
|---|---|---|
| `app/config.py` | new settings (provider flags, GCP project) | +20 |
| `app/core/openai.py` → `embeddings.py` | factory pattern | ~50 |
| `rag-service/src/rag_service/clients/embedding_client.py` | mirror | ~50 |
| `rag-service/src/rag_service/services/generation_service.py` | factory + prompt adapt | +80 / -40 |
| `rag-service/src/rag_service/services/ingestion_service.py` | dual-write | +20 |
| `rag-service/src/rag_service/services/retrieval_service.py` | column switch | +5 |
| `app/services/cache/semantic_cache_service.py` | column switch | +5 |
| `app/services/doclingRag/rag_retrieval_service.py` | column switch | +5 |
| `app/services/agenticRag/*` | port or delete | varies |
| `app/services/indexing/document_service.py` | port or delete | varies |
| `app/models/chunks_docling.py` | new column | +1 |
| `app/models/semantic_cache.py` | new column | +1 |
| `app/main.py` | self-heal block for new columns | +20 |
| `migrations/add_gemini_embedding_columns.sql` | new file | +30 |
| `scripts/backfill_gemini_embeddings.py` | new file | +60 |
| `scripts/verify_vertex.py` | new sanity script | +20 |
| `requirements.txt` (both repos) | add Vertex, remove OpenAI/Anthropic in cleanup | +2 / -4 |
| `.github/workflows/deploy-cloud-run.yml` (both repos) | new env vars; remove API key secrets in cleanup | +3 / -4 |
| `SYSTEM_OVERVIEW.md` / `TECHNICAL_BRIEFING.md` / `CUSTOMER_*` | update provider references in cleanup | varies |

**Net code change: small.** The migration is ~80% configuration / ops /
validation, ~20% code.

---

## 13. Recommended sprint plan

| Week | Focus |
|---|---|
| **W1** | Phase 0 (Vertex setup) + Phase 1 code (factory pattern, dual-column schema, backfill script) + eval set construction |
| **W2** | Phase 1 backfill, validation, cutover. Begin Phase 2 prompt adaptation in parallel. |
| **W3** | Phase 2 cutover. Phase 3 in parallel (port or delete agentic / indexing). |
| **W4+** | Stability monitoring (1–2 weeks). Then Phase 4 cleanup, dep removal, sub-processor register update, customer-facing doc updates. |

Conservative estimate: **4 calendar weeks** with one engineer focused
~80% on the migration. Faster if the agentic and indexing modules turn
out to be deletable.

---

## 14. One-line answer for stakeholders

> "Full migration to Google Gemini takes ~7–10 engineering days spread
> across 4 weeks of calendar time. The end state has zero OpenAI and
> zero Anthropic dependencies, all AI traffic stays in `europe-west1`,
> and the customer's sub-processor list shrinks to a single name —
> Google. Each phase is independently reversible until final cleanup,
> so risk is bounded throughout."

---

## Appendix A — Useful commands

```bash
# Enable Vertex AI on the project
gcloud services enable aiplatform.googleapis.com \
    --project=braided-visitor-484216-i0

# Grant the Cloud Run runtime SA permission to call Vertex
gcloud projects add-iam-policy-binding braided-visitor-484216-i0 \
    --member="serviceAccount:core-backend-runner@braided-visitor-484216-i0.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"

# List Vertex AI models available in europe-west1
gcloud ai models list --region=europe-west1

# Smoke-test the embedding API from a Cloud Shell
curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://europe-west1-aiplatform.googleapis.com/v1/projects/braided-visitor-484216-i0/locations/europe-west1/publishers/google/models/text-embedding-005:predict" \
  -d '{"instances":[{"content":"hello world"}]}'

# Check current Vertex quotas in the project
gcloud compute project-info describe --project=braided-visitor-484216-i0 \
    --format='get(quotas)' | grep -i aiplatform
```

## Appendix B — What NOT to do

- ❌ **Don't** drop the OpenAI column before the new column is fully
  populated and validated. Once the old column is gone, rollback is no
  longer free.
- ❌ **Don't** mix OpenAI and Vertex embeddings in the same pgvector
  column — different latent spaces, cosine similarity becomes noise.
- ❌ **Don't** swap Claude for Gemini and embeddings for Vertex in the
  same deploy. Two big variables changing at once = no clean diagnosis
  if quality drops.
- ❌ **Don't** assume Anthropic-style prompt caching works on Vertex
  out of the box — Vertex `cachedContents` is a different API with a
  different cost model. Re-validate cost savings after migration.
- ❌ **Don't** skip the eval set. It's the only thing that catches
  silent quality regressions in retrieval and generation. Build it
  before Phase 1 even starts.
