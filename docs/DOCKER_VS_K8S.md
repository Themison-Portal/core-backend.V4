# Docker / Cloud Run vs. Kubernetes (GKE) — for the Themison Portal

Audience: tech lead preparing a customer meeting answer to "Cloud Run vs. GKE — what's the difference for *this* product?"

This document is project-specific. For the architecture diagram and the Google Cloud security model see `TECHNICAL_BRIEFING.md` §1 and §5. For the high-level Docker / Cloud topology see `SYSTEM_OVERVIEW.md` §4 and §5.

---

## 1. TL;DR

Themison Portal is three stateless containers (Next.js FE, FastAPI BE, gRPC RAG) plus one stateful GCE VM (Postgres + Redis). On that shape, **Cloud Run wins on every axis that matters to us**: zero cluster ops, scale-to-zero economics for the FE, per-request billing, gVisor isolation by default, and a deploy step that fits in twenty lines of YAML (see `.github/workflows/deploy-cloud-run.yml`). **Stay on Cloud Run unless** a specific customer mandates Kubernetes (existing K8s ops team, service-mesh / mTLS-between-pods requirement, or a need for sidecars and sticky sessions). Migration to GKE would not change the application code or the Docker images — only the deploy substrate.

---

## 2. The three layers being compared

These three things are often conflated. They sit at different levels.

**Docker.** The container runtime and image format. Every option below uses Docker images built from `Dockerfile` (backend) and `../rag-service/Dockerfile` (RAG). Docker manages: a single container's filesystem, processes, and resource limits on a single host. **Docker Compose** (see `docker-compose.yml`) is a thin orchestrator on top — it runs a small set of containers on one host with a shared network. We use it for local dev (db + redis + rag-service + backend, four containers).

**Cloud Run.** Google's managed serverless container platform. You hand it a Docker image and it gives you back an HTTPS URL. Cloud Run manages: provisioning, autoscaling (including scale-to-zero), TLS certificates, request routing, gVisor sandboxing, log shipping, and revision rollout. You do not see or manage a host, a node, or a cluster. Billing is per request and per CPU-second of execution. This is the current production substrate for all three Themison services in `europe-west1`.

**Kubernetes (GKE).** A cluster orchestrator that schedules pods (groups of containers) onto a fleet of nodes (VMs). Google Kubernetes Engine is the managed flavour — Google runs the control plane; in **Standard** mode you still run and pay for nodes, in **Autopilot** mode Google runs nodes as well. Kubernetes manages: pod scheduling, rolling updates, service discovery, horizontal autoscaling (HPA), ingress, configmaps, secrets, network policies, and a long tail of extensions. You author and apply YAML manifests describing the desired state.

---

## 3. Side-by-side comparison for the Themison stack

| Aspect | Docker Compose (local dev) | Cloud Run (current prod) | GKE (alternative) |
|---|---|---|---|
| **Local dev loop** | `docker compose up --build` brings up db, redis, rag, backend; ~30s warm rebuild. Self-healing migrations in `app/main.py`. | Not used for local dev. | Not used for local dev. (`kind` / `minikube` possible but heavier.) |
| **Production deploy mechanism** | n/a | GitHub Actions → `gcloud run deploy` per service (~3 min end-to-end). 70 lines of YAML in `.github/workflows/deploy-cloud-run.yml`. | GitHub Actions → `docker push` + `kubectl apply -f manifests/` (or Argo CD pulling Git). Hundreds of lines of manifests across services. |
| **Who patches the host OS** | You (your laptop). | Google. Zero work for us. | Google patches the control plane. **You** drain and upgrade node pools (Standard mode). Autopilot: Google does it. |
| **Who manages cluster upgrades** | n/a | n/a — there is no cluster. | You schedule and validate Kubernetes minor-version upgrades roughly every 3-4 months. |
| **Scaling model** | Fixed: one container per service. | Scale-to-zero (FE today; BE pinned at `--min-instances=1` per the deploy workflow); scale-to-N on concurrency. RAG: `--memory 8Gi --cpu 4`. | HPA targets CPU / memory / custom metrics; **always-on minimum** of N pods (you pay for the nodes even if idle). Cluster Autoscaler adds/removes nodes. |
| **Cold-start behaviour** | n/a (always running). | Cold start ~3-30s for the BE (Python + asyncpg + SQLAlchemy import); RAG service ~30-60s (Docling/transformer model load). Mitigated with `--min-instances=1` on BE. | Warm pods serve requests in <100ms. New pods scheduled by HPA take 30-90s (image pull + container start), same image-load cost as Cloud Run. |
| **Cost model** | Free (your laptop). | Per-request: vCPU-second + GiB-second + requests. FE/BE idle ≈ €0; RAG idle ≈ €0 because no `--min-instances` set. With BE pinned to 1 instance, ~€15-30/mo for low traffic. | Per-node, billed 24/7. Smallest sane production cluster (3 × `e2-standard-2`) ≈ €120-150/mo *before* any workload. Autopilot bills per pod request — closer but not equal to Cloud Run. |
| **Networking & service-to-service auth** | Docker bridge network, plaintext, hostnames `db`, `redis`, `rag-service`. | Each service has an HTTPS endpoint. BE → RAG via gRPC over HTTPS (`rag-service-eu-768873408671.europe-west1.run.app:443`). Internal-only ingress is available; we currently use `--allow-unauthenticated` and rely on the Auth0 JWT layer. | ClusterIP services with DNS (`rag-service.default.svc.cluster.local`), plaintext gRPC inside the cluster by default. mTLS requires Istio / Linkerd / GKE managed Cloud Service Mesh. NetworkPolicy resources control pod-to-pod access. |
| **Logging & monitoring** | `docker logs`, stdout. | Cloud Logging automatic (stdout/stderr → log entries with severity). Cloud Trace + Error Reporting. No agent to install. | Cloud Logging via the GKE log agent (managed). Add Prometheus / Managed Service for Prometheus for metrics. More moving parts; more flexibility. |
| **Secret management** | `.env` file at repo root. | Secret Manager mounted at deploy time: `--set-secrets="DATABASE_URL=database-url:latest"` for DATABASE_URL, OPENAI_API_KEY, ANTHROPIC_API_KEY, UPLOAD_API_KEY, REDIS_URL, AUTH0_*. Versioned, IAM-scoped. | Two options: (a) Kubernetes `Secret` resources (base64, etcd-encrypted) populated from Secret Manager via External Secrets Operator or CSI driver; (b) direct Secret Manager mount via the Secret Manager CSI driver. Both add a moving piece compared to Cloud Run. |
| **Security primitives** | Whatever your laptop has. | gVisor user-space kernel sandbox per revision (built-in, non-optional). Auto-managed TLS. Immutable revisions. No SSH access — debugging via Cloud Logging only. See `TECHNICAL_BRIEFING.md` §5. | Default: shared kernel between pods on a node. Opt-in: **GKE Sandbox** (gVisor for selected node pools), **Shielded GKE Nodes** (secure boot + integrity monitoring), **Binary Authorization** (signed-image admission), **Workload Identity** (pods → GCP IAM without keys). All require explicit configuration. |
| **Multi-tenancy / pod isolation options** | None. | Each Cloud Run revision is its own gVisor-sandboxed instance — already isolated at the OS level. | Namespaces, NetworkPolicy, ResourceQuota, PodSecurityAdmission, taints/tolerations, dedicated node pools. Powerful but you build it. |
| **Persistent state handling** | Compose volumes (`postgres_data`, `redis_data`, `backend_uploads`). | **None** — Cloud Run has no persistent disk. We deliberately put Postgres + Redis on a separate GCE VM (`themison-db-vm-eu`, internal IP `10.132.0.2`). Uploads go to GCS. | PersistentVolumeClaims backed by Persistent Disk / Filestore. Could host Postgres in-cluster via an operator (CloudNativePG, Zalando), or keep the GCE VM as today. |
| **Request timeout cap** | None. | **60 minutes max** (Cloud Run hard cap). RAG deploy uses `--timeout 3600` to take the full hour for big PDFs. | None at the platform level — limited by your ingress/proxy config. |
| **Memory cap per instance** | Whatever the host has. | **32 GiB max** per Cloud Run instance. RAG runs at 8Gi today, plenty of headroom. | Limited by node size. `n2-highmem-32` = 256 GiB and up. |
| **Sidecars** | Multiple containers per service in Compose if needed. | One container per service (Cloud Run multi-container is in preview; we don't use it). | First-class. Pods routinely have Vault Agent, Envoy, OTel collector, log shippers as sidecars. |
| **Sticky sessions** | n/a | Best-effort via session affinity (preview) — not relied on. Our app is stateless; sessions live in Redis. | Native via `sessionAffinity: ClientIP` on Service or via Ingress controller. |

---

## 4. What would actually change if we moved to GKE

A file-by-file rundown. Concrete, not hypothetical.

### New (would not exist today)

- **Cluster provisioning.** Either Terraform (`google_container_cluster` + `google_container_node_pool`) or a one-shot `gcloud container clusters create themison-eu --region=europe-west1 ...`. Plus a node pool sized for our peak (RAG wants 8 GiB / 4 vCPU; recommend `n2-standard-4` minimum, 3 nodes for HA = ~€220/mo before workloads).
- **Kubernetes manifests** per service (`core-backend`, `rag-service`, `frontend`):
  - `Deployment` — image, replicas, env, resource requests/limits.
  - `Service` (ClusterIP for internal RAG calls, or LoadBalancer/Ingress for public BE/FE).
  - `Ingress` (or Gateway API) fronted by GCE Load Balancer + managed cert for `*.themison.app`.
  - `HorizontalPodAutoscaler` — replaces Cloud Run's automatic concurrency-based scaling. Has to be tuned per service.
  - `ConfigMap` for non-secret env (`USE_GRPC_RAG`, `ALLOW_ALL_ORIGINS`, `RAG_SERVICE_ADDRESS`, `GCS_PROJECT_ID`, `FRONTEND_URL`).
  - `Secret` (or ExternalSecret) for Secret Manager values currently passed via `--set-secrets`.
  - `ServiceAccount` per service, bound to a GCP service account via **Workload Identity** to keep our `roles/secretmanager.secretAccessor` and GCS scopes.
- **Deploy tooling.** Pick one of: (a) `kubectl apply` directly from CI; (b) Argo CD / Flux (pull-based GitOps from a manifests repo); (c) Helm chart (one chart, three values files). Argo is the modern default but adds a controller to maintain.
- **Observability glue** — Prometheus / Grafana or Managed Service for Prometheus, plus dashboards. Cloud Run gave us latency, errors, instance count out of the box; GKE we wire it.

### Modified

- **`.github/workflows/deploy-cloud-run.yml`** in both `core-backend.V4` and `Themison/rag-service`. The "Build and Push" step stays. The "Deploy to Cloud Run" step (the `gcloud run deploy ...` block, lines 46-71 of the BE workflow) gets replaced by:
  ```
  gcloud container clusters get-credentials themison-eu --region europe-west1
  kubectl set image deployment/core-backend core-backend=$IMAGE_PATH
  kubectl rollout status deployment/core-backend
  ```
  Or the equivalent Helm / Argo CD sync step.
- **The 60-minute `--timeout 3600` and `--use-http2 --port 50051` flags** on RAG's deploy disappear; instead, those go into the Deployment manifest (`containers[].ports[].containerPort: 50051`) and the Ingress timeout setting.

### Removed

- **Cloud Run-specific flags** in the workflow: `--min-instances=1`, `--allow-unauthenticated`, `--cpu-boost` (if used), `--use-http2`, `--memory`, `--cpu`, `--timeout`, `--set-env-vars`, `--set-secrets`. Their semantic equivalents move into manifests.
- The pinned `--min-instances=1` line on BE — replaced by `replicas: 2` (or HPA `minReplicas`) in the Deployment.

### Unchanged

- **`Dockerfile`** (backend) and `../rag-service/Dockerfile` — same images, same `python:3.11-slim` base, same `EXPOSE 8080` / `EXPOSE 50051`.
- **All FastAPI route code, all gRPC service code, all SQLAlchemy models.**
- **`docker-compose.yml`** — local dev does not change.
- **The GCE VM** with Postgres + pgvector + Redis. GKE pods would still talk to `10.132.0.2:5432` and `10.132.0.2:6379` over the same VPC; no migration of stateful data needed.
- **GCS buckets, Secret Manager entries, Auth0 tenant, Artifact Registry repo.** Same.

In short: moving to GKE is a **substrate swap**, not a rewrite. Two CI workflow files change; everything below the deploy step stays.

---

## 5. When Kubernetes would actually be a better fit

Real triggers, not "we'd outgrow Cloud Run someday." If a customer states one of these in a meeting, GKE becomes the right answer:

- **The customer requires service mesh / mTLS between services.** Cloud Run does HTTPS at the edge; pod-to-pod mTLS with SPIFFE identities is a Kubernetes / Istio feature. Some pharma security teams insist on this.
- **The customer has an existing GKE platform** they want our services to live on (single pane of glass, single ops team, single billing line item).
- **A required sidecar.** Vault Agent for dynamic DB credentials, an OpenTelemetry collector configured by the customer, a Falco runtime-security agent, an egress proxy. Cloud Run is one container per service; sidecars are first-class on K8s.
- **Sticky sessions** for a future feature (e.g., long-running WebSocket sessions for live editing). We do not have this need today — sessions are JWT-based and chat is request/response — but if it landed, K8s handles it more cleanly.
- **Cross-cluster failover / multi-region active-active** with a custom traffic-shifting policy. Cloud Run regional. Multi-region Cloud Run is doable with Global Load Balancer + serverless NEGs but less expressive than K8s federation.
- **Workloads beyond HTTP/gRPC.** Long-running batch jobs, Spark, ML training, anything that doesn't fit the request/response model and exceeds Cloud Run's 60-minute cap.
- **Requirement for `hostNetwork`, custom CNI, or eBPF tooling** — exotic, but real for some on-prem-derived security stacks.

If the customer says "we need Kubernetes because [reason]" and the reason fits one of the above — port. If the reason is "Kubernetes is the standard," that is not a technical trigger; it is a procurement preference, and worth pushing back on once.

---

## 6. When Cloud Run is the right choice (= our shape)

The Themison portal hits every Cloud Run sweet spot:

- **Three stateless services.** No persistent disk requirement; uploads go to GCS, state lives in Postgres + Redis on a separate VM.
- **Request/response workloads.** All BE traffic is HTTP; all RAG traffic is gRPC. No batch jobs that exceed 60 minutes (the longest path is RAG ingestion of a large PDF, well under an hour).
- **Bursty, low-baseline traffic.** A typical clinical-trial site has tens of concurrent users at most. Scale-to-zero on the FE saves real money.
- **Tiny ops team.** No dedicated SRE / platform engineer. Cloud Run requires zero cluster maintenance — no node upgrades, no etcd, no certificate rotation, no CNI tuning.
- **No sidecars needed today.** Logging, tracing, TLS — all built in.
- **Low memory ceiling** (RAG at 8 GiB, well under the 32 GiB Cloud Run cap).

**Rough cost (typical traffic, single tenant, `europe-west1`).** FE: ~€0-5/mo (scales to zero, low requests). BE: ~€15-30/mo (pinned `--min-instances=1`). RAG: ~€5-20/mo (scales to zero, bursts on ingestion). VM (DB + Redis): ~€25/mo (e2-small or e2-medium). GCS + Secret Manager + Artifact Registry: ~€5/mo. **Total: roughly €50-90/mo.** GKE Standard equivalent starts ~€150/mo for the cluster alone, before workloads.

**Headcount to operate Cloud Run setup: 0 dedicated.** A backend engineer pushing to `main` is the entire operation. GKE realistically needs 0.2-0.5 of an engineer who knows Kubernetes, especially for upgrade cycles.

*(Numbers are order-of-magnitude estimates from public GCP list pricing. Validate against actual billing before quoting in writing.)*

---

## 7. The hybrid option: GKE Autopilot

GKE has two modes. **Standard** is the classic "you manage nodes" experience. **Autopilot** is a managed mode where Google runs the nodes too — you only author manifests, Google decides what hardware your pods land on, you pay per pod-request (vCPU-seconds + GiB-seconds), much closer to Cloud Run's billing.

When Autopilot is the right middle ground:

- Customer mandates Kubernetes-shaped manifests (e.g., they have a GitOps pipeline that already speaks K8s) but does not want to operate node pools.
- You need Kubernetes features (sidecars, NetworkPolicy, custom controllers) but the workload is still mostly HTTP/gRPC.
- You want a path to Standard later if the workload grows beyond Autopilot's constraints.

When Autopilot is **not** the right answer:

- You want scale-to-zero on per-service granularity (Autopilot has a non-trivial floor cost — still cheaper than Standard but not zero).
- You want zero ops (still need to manage manifests, RBAC, ingress, etc. — just not nodes).
- The application is a clean fit for Cloud Run anyway.

For Themison's profile, Autopilot would be considered only if a customer demands K8s manifests but accepts a managed cluster — niche. Default answer remains Cloud Run.

---

## 8. Recommendation

**We use Cloud Run** because:
1. Our three services are stateless containers behind HTTP/gRPC — Cloud Run's exact target shape.
2. Our request budget is well under the 60-minute / 32-GiB caps.
3. Scale-to-zero matches a small / bursty trial-site traffic profile.
4. We have no dedicated platform team — managed-everything is the only sustainable model.
5. gVisor sandboxing per revision gives us per-tenant isolation with no extra config; equivalent on GKE requires GKE Sandbox + Shielded Nodes + Binary Authorization, all opt-in (`TECHNICAL_BRIEFING.md` §5).

**We would consider GKE** if a specific customer requires:
- Service-mesh / mTLS-between-pods (Istio).
- Sidecar containers (Vault Agent, OTel collector, Falco) that are integral to their compliance posture.
- Co-location with an existing K8s platform under unified ops.
- Sticky sessions or long-running connections beyond Cloud Run's 60-minute cap.

The migration itself is mechanical: same Docker images, new manifests, new deploy step. The application code and the database tier do not move.

---

## 9. Quick verbal answers (flashcards)

**"Why don't you use Kubernetes?"**
> Our three services are stateless containers running HTTP and gRPC — that's exactly what Cloud Run is built for. We get scale-to-zero, gVisor sandboxing, managed TLS, and a deploy step that fits in twenty lines of YAML. Running a Kubernetes cluster for three services would add €100+/mo of always-on node cost and a part-time platform engineer's worth of upgrade work, with no functional gain.

**"What if we have an existing K8s platform?"**
> Then we port. The Docker images don't change, the application code doesn't change, the database tier doesn't change. We swap the `gcloud run deploy` step for `kubectl apply` against your cluster. We'd need Deployments, Services, an Ingress, HPAs, and Secrets wired through ExternalSecrets or Workload Identity — call it a few days of work plus a hardening pass. We'd recommend Autopilot or a managed control plane if you're flexible.

**"How would you migrate?"**
> Build the same images, apply Kubernetes manifests for each of the three services, point the Ingress at the new endpoints, cut DNS over. The Postgres+Redis VM and GCS buckets stay where they are. Rollback is just "cut DNS back." We'd run both stacks in parallel for a week before switching the customer-facing host.

**"What's the cost difference?"**
> Cloud Run today is roughly €50-90/mo for our typical traffic. GKE Standard starts at ~€150/mo for the cluster nodes alone before any workload runs, plus the engineer time to operate it. GKE Autopilot is closer to Cloud Run on raw compute cost but still adds Kubernetes ops surface. Order of magnitude: 2-3× the monthly bill on GKE Standard, plus headcount.

---

## Appendix — facts to validate before quoting in a meeting

- **Cost numbers** in §6 and §9 are list-price estimates. Pull a 30-day Cloud Billing export before stating them publicly.
- **Cold-start ranges** (3-30s BE, 30-60s RAG) are typical for Python images of this size but have not been benchmarked on the current revisions; spot-check before quoting.
- **GKE Standard floor cost** (~€150/mo for 3 × e2-standard-2 in `europe-west1`) is from public list pricing — confirm against the calculator for the exact node shape and committed-use discounts.
- **`--min-instances=1` is set on the BE today** (workflow line 52); confirm with the live Cloud Run console before claiming this in production. The RAG service has no `--min-instances` flag in the workflow, so it scales to zero — first-request cold start applies.
- **Cloud Run multi-container** is in preview at time of writing; do not promise sidecars on Cloud Run.
- **Autopilot pricing** changes occasionally; verify against the current GCP pricing page.
