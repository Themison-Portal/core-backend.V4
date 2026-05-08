# Themison Clinical Trial Portal — Customer Overview

A short, honest description of the system for evaluating customers. Covers what's
implemented today, how it deploys, where data lives, and what's needed before running
with regulated patient data in production.

---

## 1. What the system does

The Themison portal is a clinical trial operations platform with an integrated
AI assistant. It helps trial sites manage:

- **Trials**, **patients**, **visits**, and **enrolment** workflows
- **Documents** — protocols, ICFs, brochures, regulatory submissions, etc.
- **AI-assisted Q&A over documents** (Retrieval-Augmented Generation) — site staff
  can ask questions like *"What are the inclusion criteria for male patients aged
  50–65?"* and get cited answers from the trial's protocol PDF
- **Tasks**, **activities**, and **archive** features for everyday trial operations
- **Multi-organisation tenancy** with role-based access (admin / staff / superadmin / editor / viewer / reader)

Authentication is handled by **Auth0** (a managed identity provider). Members are
scoped to organisations and trials.

---

## 2. Architecture at a glance

Three deployable services plus shared infrastructure.

```
                 ┌───────────────────┐
                 │  Next.js Frontend │   (Cloud Run)
                 └─────────┬─────────┘
                           │ HTTPS REST + Auth0 JWT
                           ▼
                 ┌───────────────────┐
                 │  Core Backend     │   (Cloud Run, FastAPI)
                 │  - Auth, CRUD     │
                 │  - File uploads   │
                 │  - Orchestration  │
                 └─┬─────┬───────────┘
                   │     │ gRPC (HTTP/2, TLS)
                   │     ▼
                   │  ┌───────────────────┐
                   │  │  RAG Service      │   (Cloud Run, Python gRPC)
                   │  │  - PDF ingestion  │
                   │  │  - Vector search  │
                   │  │  - LLM generation │
                   │  └───┬─────────┬─────┘
                   │      │         │
                   ▼      ▼         ▼
        ┌──────────────────┐  ┌──────────┐  ┌────────────┐
        │  PostgreSQL +    │  │  Redis   │  │  GCS       │
        │  pgvector        │  │  (cache, │  │  (PDFs &   │
        │  (GCE VM,        │  │  queues) │  │  patient   │
        │  europe-west1)   │  │          │  │  files)    │
        └──────────────────┘  └──────────┘  └────────────┘
```

External services called from the cloud deployment:

- **Anthropic** — Claude (Opus class) for answer generation
- **OpenAI** — `text-embedding-3-small` for embeddings
- **Auth0** — identity & JWT issuance
- **SendGrid** — invitation emails (optional)
- **Hugging Face Hub** — model weights for the Docling PDF parser (downloaded once at rag-service startup)

### Communication

| Path | Protocol |
|---|---|
| Frontend → Backend | HTTPS REST (`Authorization: Bearer <Auth0 JWT>`) |
| Backend → RAG service | gRPC over HTTP/2 (TLS in cloud, plaintext on Docker network) |
| Backend → PostgreSQL | `asyncpg` over the GCP private network |
| Backend → Redis | Async Redis client over the GCP private network |
| Backend → GCS | Google Cloud SDK with signed-URL generation |

---

## 3. Project structure

The codebase is split across three Git repositories.

### `core-backend` (FastAPI)

```
app/
  api/
    routes/
      auth.py, upload.py, query.py, local_files.py, storage/...
      api/                           ← business endpoints
        organizations.py, members.py, invitations.py, roles.py
        trials.py, trial_members.py, trial_documents.py
        patients.py, trial_patients.py, patient_visits.py, patient_documents.py
        chat_sessions.py, chat_messages.py
        archive.py, qa_repository.py
        tasks.py, activities.py, visit_activities.py, complete_visit.py
  models/                            ← SQLAlchemy ORM (one file per table)
  contracts/                         ← Pydantic request/response schemas
  services/
    doclingRag/                      ← in-process RAG (used in dev)
    cache/                           ← Redis-backed semantic + response cache
    storage/                         ← GCS / local file storage abstraction
    jobs/                            ← background-job status tracking
    indexing/, reranking/, highlighting/, contextual/
  dependencies/                      ← DI: auth, db, storage, redis, trial-access
  db/                                ← async SQLAlchemy engine & session
  core/                              ← OpenAI/embedding clients, config
docker/
  init.sql                           ← full schema, runs on first `docker-compose up`
  seed.sql                           ← test data for local dev
migrations/
  add_*.sql, create_*.sql            ← additive migration files
.github/workflows/
  deploy-cloud-run.yml               ← CI/CD to Cloud Run
```

### `rag-service` (gRPC microservice)

```
src/rag_service/
  services/
    ingestion_service.py             ← PDF → chunks → embeddings → DB
    retrieval_service.py             ← vector + BM25 hybrid search
    generation_service.py            ← Claude prompt construction & call
    highlighting_service.py          ← bbox extraction for citations
  cache/                             ← semantic cache implementation
  clients/                           ← Anthropic, Postgres, Redis
  protos/                            ← gRPC contract (.proto)
```

### `frontend` (Next.js)

- Next.js 16 + React 19 + TanStack React Query
- Auth0 SPA SDK for sign-in
- `react-pdf` for in-browser PDF viewing
- Radix UI + Tailwind CSS

---

## 4. Running locally with Docker

The whole stack starts with a single command. Everything is wired up to talk to
each other on the Docker network.

### Prerequisites

- Docker Desktop
- A `.env` file at the repo root with at least:

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
UPLOAD_API_KEY=any-string-you-pick     # used as X-API-Key for /upload/upload-pdf
AUTH_DISABLED=true                     # bypass Auth0 for local dev
ALLOW_ALL_ORIGINS=true                 # permissive CORS for local FE
```

### Start the stack

```bash
docker compose up --build
```

What you get:

| Service | Port (host) | Description |
|---|---|---|
| `themison-db` | `54322` | PostgreSQL 16 with pgvector pre-installed |
| `themison-redis` | `6379` | Redis 7 |
| `themison-rag-service` | `50051` | gRPC RAG microservice |
| `themison-backend` | `8080` | FastAPI REST API |

On the first boot, `docker/init.sql` creates the full schema (~27 tables) and
`docker/seed.sql` inserts test fixtures so you can hit the API immediately.

The backend's startup hook (`app/main.py`) runs idempotent **self-healing
migrations** — if you pull new code and a column was added, it appears at
startup without you running anything.

### Try the API

```bash
# With AUTH_DISABLED=true the backend uses the seeded test member automatically:
curl http://localhost:8080/api/trials/
```

The frontend (separate repo) points at `http://localhost:8080` by default.

---

## 5. Running on Google Cloud

The reference deployment runs entirely in **`europe-west1` (Belgium)** for
GDPR data residency.

### Topology

| Component | GCP service | Notes |
|---|---|---|
| Frontend | Cloud Run | Public, scales to zero |
| Core backend | Cloud Run | `--min-instances=1` to avoid cold-start CORS issues |
| RAG service | Cloud Run | gRPC + HTTP/2; `--memory 8Gi --cpu 4 --timeout 3600` for Docling PDF parsing |
| PostgreSQL + Redis | Compute Engine VM (`themison-db-vm-eu`) | Single VM running official `pgvector/pgvector:pg16` and `redis:7-alpine` Docker images, internal IP only |
| File storage | Cloud Storage (GCS) | Two buckets: trial-documents and patient-documents |
| Secrets | Secret Manager | `DATABASE_URL`, API keys, Auth0 credentials, `UPLOAD_API_KEY` |
| Container images | Artifact Registry | `themison-repo-eu` |
| CI/CD | GitHub Actions | One workflow per service, deploys on push to `main` |

### What the deploy workflow does

`.github/workflows/deploy-cloud-run.yml` (in each backend repo):

1. Authenticates to GCP via a service-account key stored as a GitHub secret.
2. Builds the Docker image, tags with the commit SHA, pushes to Artifact Registry.
3. Calls `gcloud run deploy` with environment variables and Secret Manager bindings.
4. Cloud Run fronts the new revision, drains the old one — zero-downtime rollout.

### Customer-controlled deployment

A customer can run the same stack in **their own GCP project** by:

1. Forking the three repositories.
2. Provisioning: a Compute Engine VM for Postgres+Redis, two GCS buckets, three
   Cloud Run services, an Artifact Registry repo, Secret Manager entries, an
   Auth0 tenant.
3. Updating the `PROJECT_ID`, `REGION`, and `ARTIFACT_REGISTRY` constants at
   the top of each workflow file.
4. Adding a `GCP_SA_KEY` secret to each GitHub repo.

The `DATABASE_HANDOFF.md`, `PRODUCTION_SETUP.md`, and
`EUROPE-MIGRATION-GUIDE.md` files in the backend repo walk through this in detail.

### Self-hosted (no GCP) options

The same stack runs anywhere Docker runs. To self-host on AWS, Azure, or
on-prem hardware, replace:

| Cloud component | On-prem equivalent |
|---|---|
| Cloud Run | Docker Compose, Kubernetes, or any container host |
| GCS | S3-compatible storage (MinIO works), or local file storage (already supported via `LocalStorageService`) |
| Secret Manager | HashiCorp Vault, Kubernetes Secrets, or `.env` files |
| Compute Engine VM | Any VM running Docker |
| Auth0 | Keycloak, Authentik, or any OIDC-compliant provider |

The only non-portable piece is the GCP-specific deploy workflow; the
application code is cloud-agnostic.

---

## 6. Data security & PII handling

This section is **deliberately honest**. Read carefully if you plan to use the
system with patient data.

### What the system does today

- **Transport security:** all customer-facing traffic is HTTPS (Cloud Run fronts).
  Backend↔database and backend↔Redis traffic uses GCP's private network, no
  public IPs.
- **Authentication:** every API call (except `/upload/upload-pdf`, which uses
  an API key) requires a valid Auth0 JWT. Backend resolves the JWT to a
  `Member` record scoped to a single organisation.
- **Authorisation:** trial-level access is enforced by the
  `get_trial_with_access` dependency: members of one organisation cannot read
  documents or data belonging to another organisation, and non-admin members
  must be explicit `TrialMember`s of a trial to access it.
- **Secrets management:** all credentials in production live in Secret Manager,
  never in source code or container images.
- **Data residency:** when deployed in `europe-west1`, customer data never
  leaves the EU for storage purposes.
- **Object storage:** PDF files in GCS are private; the FE only ever receives
  short-lived (1 hour) signed URLs generated on demand.

### What the system does NOT do today

This is the part to flag clearly:

- **No automatic PII redaction** before content is sent to the LLM. The current
  RAG flow forwards retrieved document chunks verbatim to Claude. If your
  protocol PDFs or patient documents contain identifying information, that
  information goes to Anthropic's API as-is.
- **No field-level encryption** for patient PII columns
  (`first_name`, `last_name`, `date_of_birth`, contact info, medical history).
  Data is stored in PostgreSQL in plaintext, protected only by network and
  database-user controls.
- **No immutable audit log** for read/write actions on regulated data.
- **No data-loss-prevention scanning** during upload to detect accidental PII
  in documents.

### Recommended hardening before processing real patient data

If you're evaluating the system for a regulated workflow (HIPAA, GxP, similar),
the practical checklist is:

1. **Add a PII redaction layer** at the chunk-storage boundary. Two integration
   points work well:
   - **Pre-LLM filter** in the rag-service generation step
     (`generation_service.py`): replace identifiable spans with placeholder
     tokens (`[PATIENT_NAME]`, `[DOB]`) before constructing the Claude prompt.
     Open-source libraries like Microsoft Presidio or AWS Comprehend Medical
     can be wired in via a simple async filter.
   - **Pre-storage filter** at ingestion time (`ingestion_service.py`):
     redact PII before chunks ever land in PostgreSQL. Stricter, but loses
     the ability to display original text in the UI.
2. **Enable Customer-Managed Encryption Keys (CMEK)** on GCS, GCE persistent
   disks, and Cloud SQL (if you migrate Postgres there).
3. **Add column-level encryption** for patient PII fields (PostgreSQL has
   built-in `pgcrypto` for this).
4. **Add audit logging** — every mutation to clinical data writes to an
   append-only `audit_log` table or to GCP Cloud Audit Logs.
5. **Implement explicit data-subject-rights endpoints** (export, delete) to
   support GDPR Article 15 / 17 requests.

---

## 7. Compliance posture

What you inherit from the platform layer vs. what you must add yourself.

### Inherited from Google Cloud

Google Cloud holds independent third-party attestations and certifications
that apply to the underlying infrastructure your workloads run on:

- **ISO/IEC 27001, 27017, 27018, 27701** — information security, cloud security,
  cloud privacy, privacy information management
- **SOC 1, SOC 2, SOC 3** — service organisation controls
- **HIPAA-eligible** services list — Cloud Run, GCS, Compute Engine, Secret
  Manager, Cloud SQL, and others are covered under GCP's Business Associate
  Agreement
- **GDPR** — Google publishes a standard contractual-clauses-based DPA;
  data stays in the region you deploy to (we use `europe-west1`)
- **FedRAMP**, **PCI DSS**, **FIPS 140-2** validated cryptographic modules,
  and additional regional certifications

The current public list is at
[https://cloud.google.com/security/compliance](https://cloud.google.com/security/compliance).

### What this means for your deployment

GCP's certifications cover the **infrastructure** (data centres, network,
hypervisor, managed services). They do **not** automatically extend to your
application — that's your responsibility. Specifically:

- **Auth0** has its own SOC 2 / ISO 27001 / HIPAA attestations that apply to
  the identity layer.
- **Anthropic and OpenAI** require enterprise contracts (with BAAs and ZDR
  options) before you can claim HIPAA coverage end-to-end. The default
  consumer API tiers do **not** include a BAA.
- **The Themison application code** has no independent compliance attestation
  today. Whether your deployment is HIPAA-, GxP-, or 21 CFR Part 11–compliant
  depends entirely on the controls you add (audit logging, validated change
  control, electronic signatures, retention policies, PII redaction, etc.).

### Practical compliance summary

| Concern | Status with default deployment |
|---|---|
| **GDPR data residency (EU)** | ✅ Met when deployed to `europe-west1` |
| **Encryption in transit** | ✅ HTTPS / TLS / GCP private network |
| **Encryption at rest (managed services)** | ✅ Default GCS, Cloud Run, Artifact Registry encryption |
| **Encryption at rest (customer DB on GCE VM)** | ⚠️ Disk-level only — no column-level encryption |
| **Auth & authorisation** | ✅ Auth0 + org-scoped + trial-scoped |
| **HIPAA technical safeguards** | ⚠️ Architecture supports but BAAs + audit log + access logs must be added |
| **GxP / 21 CFR Part 11 (electronic records)** | ❌ Not designed for this — no validated change control, no electronic signatures |
| **Right-to-be-forgotten (GDPR Art. 17)** | ⚠️ Soft-delete exists for some entities; full cascade-delete not implemented |
| **Data export (GDPR Art. 15/20)** | ❌ No dedicated endpoint |
| **Audit log** | ❌ Not implemented in app; GCP Cloud Audit Logs cover infra layer only |

We recommend a **gap-analysis engagement** for any customer planning to
process regulated data — typically 2–4 weeks to scope the additional controls
and a custom development effort to implement them.

---

## 8. Data ownership & maintenance

### Where customer data lives

- **Structured data** (trials, patients, documents metadata, chat history,
  RAG chunks with embeddings) → PostgreSQL on a single Compute Engine VM.
- **Files** (PDFs) → two GCS buckets, owned by the customer's GCP project.
- **Caches & job state** → Redis on the same VM (volatile, can be wiped at
  any time without data loss).
- **Auth records** (users, sessions) → Auth0 tenant, owned by the customer.

When a customer self-hosts, **all of this lives in their cloud account**.
Themison has no operational access by default.

### Backups

- The codebase ships **no automated backup**. Customers are expected to
  configure one of:
  - **GCE persistent-disk snapshots** on a schedule (most common — point-in-time
    recovery, near-zero ops)
  - **`pg_dump` cron job** to a GCS backup bucket with object versioning
  - Migration to **Cloud SQL** (managed Postgres with automated backups;
    requires installing the pgvector extension on the instance)
- For object storage, enable **GCS bucket versioning** so deleted/overwritten
  PDFs can be recovered.

### Schema migrations

- New schema changes ship as **idempotent SQL files** in `migrations/` plus an
  entry in `app/main.py`'s self-heal block.
- On first start of a new revision, the app applies any missing columns/tables
  automatically. No manual migration step is required for additive changes.
- For destructive migrations (rare), a manual `psql` step is documented in
  the migration file.

### Data deletion (today)

- `DELETE /api/trial-documents/{id}` — hard-deletes the document row, the
  GCS file, and (via FK cascade) any RAG chunks.
- `DELETE /api/chat-messages/{id}` — soft delete (sets `deleted_at`).
- `DELETE /api/archive/folders/{id}` — soft delete.
- Patients can be marked inactive (`is_active=false`) but no cascade-delete
  endpoint is provided.

For full GDPR Article 17 ("right to erasure") coverage, customers
typically need a small custom endpoint that walks all tables containing a
given subject's identifiers and deletes/anonymises the rows.
This is straightforward to add — typically a half day of work — but is **not** in
the codebase as shipped.

### Data export

There is no public export endpoint. For tenant migrations, the
`scripts/migration/export_data.py` and `import_data.py` utilities can dump and
restore an organisation's data, but these are operator tools, not customer
self-service.

For Article 15 / 20 ("right of access" / "right to portability"), a small
custom endpoint that emits a JSON or CSV dump of a user's records is
recommended — same effort profile as the deletion endpoint.

---

## 9. Customisation & extension points

The architecture is designed so customers can tailor the system without
forking the core. The most common extension points:

| Customisation | Where to plug in |
|---|---|
| **Replace the LLM** (e.g., self-hosted Llama, Azure OpenAI) | `rag-service/src/rag_service/services/generation_service.py` — single class, swap the client |
| **Replace the embedding model** | `app/core/openai.py` and the rag-service's mirror |
| **Add PII redaction** | Pre-LLM hook in `generation_service.py` or pre-storage hook in `ingestion_service.py` |
| **Add audit logging** | FastAPI middleware in `app/main.py` + a new `audit_log` table |
| **Replace Auth0** | `app/dependencies/auth.py` — JWT verification helper, swap for any OIDC provider |
| **Replace GCS** | `app/services/storage/` already has `LocalStorageService` and `GCSStorageService`; adding S3 is a third class |
| **Branding / theming** | Frontend (Next.js + Tailwind) |
| **New entity types** | Add SQLAlchemy model, contract, and route module — the existing structure is the template |

---

## 10. What we recommend for a new customer engagement

1. **Discovery** (1 week) — understand the customer's regulatory regime
   (HIPAA, GDPR, GxP, …), data sensitivity, and integration needs.
2. **Compliance gap analysis** (1–2 weeks) — map current controls against the
   customer's requirements; prioritise PII redaction, audit logging,
   data-subject-rights endpoints.
3. **Provisioning** (1 week) — set up the customer's GCP project (or
   alternative cloud), Auth0 tenant, BAAs with Anthropic/OpenAI/Google.
4. **Custom implementation** (2–6 weeks depending on gap analysis) — add the
   prioritised compliance controls; load any tenant-specific reference data.
5. **UAT + go-live** (1–2 weeks) — customer-led acceptance testing; cutover
   plan; runbook handover.

After go-live, customers typically retain a small support engagement for
schema migrations, dependency upgrades, and feature requests.

---

## Appendix A: GDPR & Google Cloud — meeting Q&A

This appendix anticipates likely questions on GCP's GDPR posture and how our
deployment maps to the platform's compliance features. Keep in mind the
recurring theme: **compliance is a shared-responsibility model.** GCP secures
the infrastructure ("of the cloud"); the customer is responsible for how
services are configured and how data is handled ("in the cloud").

### Glossary of terms used below

Three acronyms come up repeatedly in this section. Brief definitions so
nothing in the Q&A is opaque if a non-technical stakeholder reads it.

#### CMEK — Customer-Managed Encryption Keys

By default, every GCP service encrypts data at rest using keys that **Google
generates and manages**. The customer never sees these keys but the data is
encrypted automatically. **CMEK** ("Customer-Managed Encryption Keys") is the
upgrade where the customer creates and owns the encryption keys themselves
via **Cloud KMS** (Google's Key Management Service); GCP services then use
those customer-owned keys to encrypt the customer's data.

**Why it matters for compliance:**
- The customer can **rotate** keys on their own schedule, which some regulated
  regimes require.
- The customer can **revoke** keys instantly — sometimes called the "kill
  switch." Once a key is destroyed or disabled, all data encrypted with it
  becomes unreadable, even by Google's own support staff. This is a strong
  technical guarantee that satisfies many sovereignty and right-to-erasure
  arguments.
- It produces an auditable key-use trail (who decrypted what, when) in Cloud
  Audit Logs, which helps demonstrate "Technical and Organisational Measures"
  (TOMs) under GDPR Article 32.

**Where it applies in our stack:** GCS buckets (PDF storage), GCE persistent
disks (the VM running Postgres + Redis), Artifact Registry (container
images), and Cloud SQL if used. Each can be configured to use a customer-owned
KMS key during creation.

**Trade-off:** if the customer destroys a CMEK key, their data is unrecoverable
— including from any backup that was encrypted with the same key. This is a
feature for compliance and a foot-gun if mishandled. Customers using CMEK
typically also implement strict KMS-key access controls and key-versioning
policies.

**Status in our deployment: ❌ NOT implemented.** All GCP services in our
reference deployment use Google-managed encryption keys (the default). Data
is still encrypted at rest, but Google holds the keys, not the customer.
Enabling CMEK is a per-resource configuration change (GCS buckets, GCE
persistent disks, Artifact Registry) and requires a Cloud KMS keyring +
service-account permissions to be set up first. We recommend enabling it
for any tenant processing regulated data; effort is roughly half a day for
the initial setup plus a few minutes per resource.

#### CDPA — Cloud Data Processing Addendum

The **Cloud Data Processing Addendum** is the legal contract between Google
and the customer that governs how Google processes the customer's personal
data on the customer's behalf. It's the document that makes the relationship
GDPR-compliant from a contractual standpoint, by formalising Google's role as
a **data processor** (Art. 28 GDPR) and the customer's role as the **data
controller**.

**What the CDPA commits Google to:**
- Process customer personal data **only** on the customer's documented
  instructions
- Maintain prescribed security measures (encryption, access controls, etc.)
- Use sub-processors transparently and notify the customer of changes
- Assist the customer with data-subject-rights requests, breach notifications,
  and data-protection impact assessments
- Cooperate with EU supervisory authorities on inquiries
- Include **Standard Contractual Clauses** for any data transfer outside the
  EEA

**Where it lives:** the CDPA is a separate legal document, accessible via the
GCP Console under the customer's billing account or organisation. It is not
active by default — a customer's representative with appropriate authority
must explicitly **review and sign it in the Console**. Until that is done,
the customer technically lacks the Article 28 contract that GDPR requires
when using a processor.

**Practical note:** signing the CDPA is free and takes a few minutes, but it
is a piece of paperwork that often gets overlooked during initial cloud
onboarding. We typically include "verify CDPA is signed" as a go-live
checklist item for every customer.

**Status in our deployment: ⚠️ Customer-tenant action — not something we can
verify or do on the customer's behalf.** The CDPA is signed inside the
customer's own GCP organisation (or billing account) by a person with
authority to bind that organisation legally. For our internal demo
environment we have signed it; for any new customer this must be confirmed
during onboarding. **Mark this as a required go-live checklist item for
every prospect.** If they say "we already use Google Cloud," still verify
explicitly — having a GCP account does not imply the CDPA has been signed.

#### GKE — Google Kubernetes Engine

**Google Kubernetes Engine** is GCP's managed Kubernetes service — the
"orchestrate many containers across many machines" platform. Customers who
want fine-grained control over scaling, networking, and pod placement
typically deploy on GKE. It's powerful but operationally heavier (you manage
clusters, node pools, manifests, networking policies, etc.).

**Cloud Run vs. GKE — why we use Cloud Run:**

| Aspect | Cloud Run (our choice) | GKE |
|---|---|---|
| Operational overhead | Near-zero — fully managed, scales to zero | High — clusters, nodes, manifests, upgrades |
| Cost model | Per-request billing; idle cost = €0 | Per-node billing 24/7 |
| Cold-start latency | Seconds (mitigated with `--min-instances=1`) | None when warm pods are running |
| Container isolation | gVisor sandbox per revision (built-in) | Configurable (default isolation, GKE Sandbox, etc.) |
| Suitable for | Stateless HTTP/gRPC services like ours | Workloads with persistent volumes, sidecars, custom networking |

For the Themison portal — three stateless services, low-to-moderate
throughput, and a strong preference for low operational burden — **Cloud Run
is the right fit**. GKE-specific security features mentioned in the GCP
documentation (GKE Sandbox, Shielded GKE Nodes, Binary Authorization on GKE)
**do not apply to our deployment** because we don't run a Kubernetes cluster.

If a customer specifically requires GKE — for example, because they have an
existing Kubernetes operations team or they need to co-locate the Themison
services with other workloads on the same cluster — porting is
straightforward (just standard Kubernetes manifests for three Docker images),
but it would change the operational model and cost profile.

**Status in our deployment: ❌ NOT used (intentional architectural choice).**
The Themison portal runs on **Cloud Run** for all three services (frontend,
backend, RAG service). No GKE cluster exists. As a direct consequence,
GKE-specific security features — **GKE Sandbox**, **Shielded GKE Nodes**,
**Binary Authorization on GKE**, **Workload Identity for GKE** — do not
apply to our deployment, and we should not claim them in compliance
narratives. The protections we *do* get from Cloud Run (gVisor sandboxing
per revision, automatic patching of the underlying container runtime, no
exposed node OS) achieve a similar isolation outcome via different
mechanisms; we recommend describing it in those terms rather than
GKE-equivalent terms.

### Combined implementation-status snapshot

For quick reference in the meeting:

| Concept | Status in current reference deployment | Owner of any future change |
|---|---|---|
| **CMEK** (Customer-Managed Encryption Keys) | ❌ Not enabled — Google-managed keys used by default | Themison engineering (configure on resources) + customer (own the KMS keyring) |
| **CDPA** (Cloud Data Processing Addendum) | ⚠️ Signed for our demo tenant; **per-customer action** for any new prospect | Customer's GCP org admin (legal sign-off required) |
| **GKE** (Google Kubernetes Engine) | ❌ Not used by design — Cloud Run is the chosen platform | N/A — no current plan to change; would require re-architecture if a customer mandates it |

### Q: Is Google Cloud GDPR-compliant?

**Yes — the platform is fully capable of GDPR-compliant operation.** Google
Cloud is not "GDPR-certified" in a stamp-on-the-product sense (no such
certification exists), but it provides the contractual, technical, and
auditing instruments customers need to meet their own GDPR obligations.

The four pillars of Google's GDPR posture:

1. **Contractual commitments — Cloud Data Processing Addendum (CDPA).**
   This is the legal document where Google commits, as the **data processor**,
   to:
   - Only process customer data per the customer's instructions
   - Maintain robust technical and organisational security measures
   - Disclose its own sub-processors transparently
   - The CDPA must be signed in the GCP Console — it is not active by default.

2. **Lawful international data transfers.** When data leaves the EU (e.g.,
   to the US), Google relies on:
   - **Standard Contractual Clauses (SCCs)** approved by the European
     Commission
   - **EU–U.S. Data Privacy Framework** (active since 2023) as an
     "Alternative Transfer Solution"
   - **Mitigation in our deployment:** by deploying in `europe-west1`, customer
     data does not transit out of the EU at the storage tier in the first
     place.

3. **Built-in compliance tools customers can invoke:**
   - **Data Residency** — choose EU regions (Belgium, Frankfurt, Paris,
     Madrid, etc.). Data physically stays in those data centres.
   - **Encryption** — at rest and in transit by default; **CMEK**
     (Customer-Managed Encryption Keys) for tenants that need to hold their
     own keys.
   - **Cloud Data Loss Prevention (DLP) API** — automatic discovery and
     redaction of PII (names, emails, IDs, payment info, medical record
     numbers) in datasets and logs.
   - **IAM (Identity and Access Management)** — granular role-based access
     control over every GCP resource.

4. **Independent third-party certifications relevant to GDPR:**
   - **ISO/IEC 27018** — protection of PII in public clouds (specifically
     designed to align with GDPR/EU privacy law)
   - **ISO/IEC 27701** — privacy information management standard that maps
     directly to GDPR articles
   - **SOC 2 / SOC 3** — service-organisation controls covering security,
     availability, and confidentiality

### Q: Is running Docker on Google Cloud secure and GDPR-compliant in the EU?

**Yes — when configured correctly.** Our reference deployment runs Docker
images on **Cloud Run** (managed, serverless containers) with Postgres+Redis
on a **Compute Engine VM**. Both compute layers in the EU. The same shared-responsibility split applies:
Google secures the platform; the customer is responsible for what is
inside their containers and how they wire access.

**Container-level security features Google Cloud provides:**

| Feature | What it does | Status in our setup |
|---|---|---|
| **Artifact Registry** | Stores container images privately; auto-scans for known CVEs | ✅ Used (`themison-repo-eu`) |
| **Vulnerability scanning** (Container Analysis API) | Continuous scan results on stored images | ⚠️ Available — recommend enabling on the customer's project |
| **Binary Authorization** | Deploy-time gate: only signed/approved images can run | ⚠️ Optional add-on; not enabled by default |
| **Cloud Run service-level isolation** | Each revision runs in its own gVisor sandbox | ✅ Inherent to Cloud Run |
| **Shielded VM** (Compute Engine) | Hardware-rooted boot integrity for the DB VM | ⚠️ Recommend enabling on the GCE VM running Postgres+Redis |
| **GKE Sandbox / Shielded GKE Nodes** | Container isolation on GKE | N/A — we use Cloud Run, not GKE |

**GDPR-specific configurations to verify on the customer's project:**

| Action | Status with our reference deployment |
|---|---|
| Deploy services in EU regions only | ✅ All in `europe-west1` (Belgium) |
| Sign the **Cloud Data Processing Addendum** in the GCP Console | ⚠️ Customer-tenant action — **must be done explicitly** |
| **Assured Workloads for EU** (sovereign-controls bundle, e.g., EU-resident support staff only) | ❌ Not enabled — discuss whether the customer's regime requires it |
| **Customer-Managed Encryption Keys (CMEK)** on GCS, GCE disks, Cloud SQL (if used) | ❌ Not enabled by default — recommend for any deployment with regulated data |
| **Cloud DLP** scanning before logs / databases ingest user-supplied content | ❌ Not wired in — listed as a hardening item in §6 |
| Ensure application **logs do not emit PII** (names, emails, free-text patient data) | ⚠️ Customer-tenant audit — current logging is light, but should be reviewed |
| **IAM least-privilege** on production resources | ⚠️ Customer-tenant configuration |

### Mapping to our deployment

| GDPR control area | Currently met by our reference deployment | Customer-tenant action items |
|---|---|---|
| EU data residency | ✅ `europe-west1` for all services | Confirm deployment region matches data-protection requirements |
| Transport encryption | ✅ HTTPS / TLS / GCP private network | None |
| Encryption at rest (GCP-managed) | ✅ Default for Cloud Run, GCS, Artifact Registry, GCE disks | None for default protection |
| Encryption at rest (customer-controlled) | ❌ CMEK not enabled | Enable CMEK on GCS buckets, GCE persistent disks, and any Cloud SQL instance |
| CDPA signed | ❌ Per-tenant action | Sign in Google Cloud Console under the customer's billing/organisation node |
| Sub-processor transparency | ✅ Google publishes its sub-processor list | Customer adds Anthropic / OpenAI / Auth0 / SendGrid to their own sub-processor register |
| International transfer mechanism | ✅ SCCs + EU-U.S. DPF (covered by signing CDPA) | None additional unless using non-default region for backups |
| PII redaction before LLM call | ❌ Not implemented | Add Cloud DLP API call in the rag-service generation step (§6 of this doc) |
| Audit logging | ⚠️ GCP Cloud Audit Logs cover infra; application-level audit log not implemented | Implement application audit log; configure Cloud Audit Logs retention |
| Data-subject-rights (Art. 15, 17, 20) endpoints | ❌ Not implemented | Custom endpoint work — typically a half-day per right |
| Vulnerability scanning of Docker images | ⚠️ Available, not verified on the customer project | Enable Container Analysis on Artifact Registry |
| Binary Authorization | ❌ Not enabled | Optional — recommended for production-grade tenants |

### Likely follow-up questions and short answers

**"Does using Google Cloud automatically make us GDPR compliant?"**
No. GCP gives customers the tools and contractual guarantees to be compliant,
but customers must (1) sign the CDPA, (2) configure services correctly
(region, encryption, IAM, logging), (3) have a lawful basis for processing
their users' data, (4) provide privacy notices to data subjects, and (5)
support data-subject rights. Compliance is the **outcome** of doing all of
these together.

**"Where exactly does our data sit?"**
- Database (PostgreSQL) and cache (Redis): inside a Compute Engine VM in
  `europe-west1` (Belgium). Internal IP only — not reachable from the public
  internet.
- PDFs and patient files: GCS buckets in `europe-west1`.
- Backend & RAG-service container images: Artifact Registry in `europe-west1`.
- Authentication tokens: Auth0 (the customer's chosen tenant region — Auth0
  offers EU regional tenants).
- LLM API calls: cross-border to Anthropic and OpenAI (US-based providers).
  See the next question for how to mitigate.

**"What about data going to OpenAI and Anthropic?"**
This is the most material cross-border transfer in the system. Both providers
offer enterprise contracts with **Standard Contractual Clauses**,
**Zero-Data-Retention (ZDR)** options, and **Business Associate Agreements**
(for HIPAA — useful as a tighter signal of operational maturity even if HIPAA
isn't the customer's regime). Default consumer-tier API usage does **not**
include these guarantees. Before processing real PII through the LLM:
1. Move both keys to enterprise / ZDR tiers
2. Sign their respective DPAs
3. Add the providers to the customer's sub-processor register
4. **Pair this with PII redaction at the application layer** (§6) so the
   amount of PII reaching the providers is minimised regardless of contract

**"Can we use Assured Workloads for EU sovereignty?"**
Yes — Assured Workloads is a paid GCP add-on that enforces stricter sovereign
controls (EU-resident support personnel, restricted data-access pathways,
additional audit signals). It's the strongest signal for customers whose
regulators require explicit sovereignty (some German Länder, French health-data
hosting requirements, etc.). It adds operational cost and constrains some
service availability — typically only worth enabling when the customer's
regulator requires it.

**"What's the absolute minimum to be GDPR-defensible from day one?"**
1. Deployment in `europe-west1` ✅ (already done)
2. CDPA signed in the customer's GCP console ❗ (customer action)
3. Auth0 tenant in EU region ❗ (customer action when provisioning Auth0)
4. PII redaction layer added before LLM calls (~3 days of work)
5. Application audit log table + middleware (~2 days of work)
6. Customer-facing privacy notice & DPA between Themison and the customer
7. The customer registers Themison + Anthropic + OpenAI + Auth0 + Google as
   sub-processors

Items 1–3 and 6–7 are configuration and paperwork; only items 4 and 5 are
engineering work. Total effort: ~1 week of dev plus the customer's legal
processes.

### One-line summary for the meeting

> **GCP runs an EU-resident, certified-compliant infrastructure (ISO 27018,
> ISO 27701, SOC 2) and signs a Data Processing Addendum that satisfies the
> processor-level GDPR requirements. Our reference deployment is built on top
> of that — `europe-west1`, encryption by default, IAM-based access — but the
> code-as-shipped does not yet include PII redaction or an application audit
> log. Both are scoped, well-understood additions (~1 week of engineering)
> that we'd recommend completing before any go-live with real patient data.**

---

## Appendix B: Quick reference

### Default ports

| Service | Port |
|---|---|
| Backend (Cloud Run / Docker) | `8080` |
| RAG service (Cloud Run / Docker) | `50051` (gRPC) |
| PostgreSQL (Docker host) | `54322` |
| Redis (Docker host) | `6379` |

### Required environment variables (production)

| Name | Source |
|---|---|
| `DATABASE_URL` | Secret Manager → injected by Cloud Run |
| `REDIS_URL` | Secret Manager |
| `OPENAI_API_KEY` | Secret Manager |
| `ANTHROPIC_API_KEY` | Secret Manager |
| `UPLOAD_API_KEY` | Secret Manager |
| `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET` | Secret Manager |
| `GCS_BUCKET_TRIAL_DOCUMENTS`, `GCS_BUCKET_PATIENT_DOCUMENTS` | Cloud Run env var |
| `RAG_SERVICE_ADDRESS`, `USE_GRPC_RAG=true` | Cloud Run env var |
| `FRONTEND_URL`, `ALLOW_ALL_ORIGINS=false` | Cloud Run env var |

### Key files for evaluators

| Question | File |
|---|---|
| Database schema | `docker/init.sql` (479 lines, full schema) |
| Backend entry point | `app/main.py` |
| Route inventory | `app/api/routes/api/` (one file per resource) |
| RAG generation prompt | `rag-service/src/rag_service/services/generation_service.py` |
| Authorisation logic | `app/dependencies/trial_access.py` |
| Cloud deployment | `.github/workflows/deploy-cloud-run.yml` |
| Compliance docs | `DATABASE_HANDOFF.md`, `EUROPE-MIGRATION-GUIDE.md` |
