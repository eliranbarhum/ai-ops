# VKS Console — Design Plan

A standalone container-platform app inside vcf-ai-ops. Graphical, full-lifecycle
management of VKS (vSphere Kubernetes Service / Tanzu) clusters without kubectl —
the OpenShift-console / VCF-ICP experience, plus an AI layer ICP and OCP don't have.

Status: **design — not yet implemented.** Target: VCF 9.x supervisor at 10.50.78.6.
Reference UI: VCF ICP (`screenshots/1.png`–`3.png`).

---

## 1. Product shape

VKS Console is its **own app** mounted inside the MCO shell at `#/platform`. It has
its own left-rail navigation that mirrors ICP and is cluster-scoped:

```
All clusters
  └ <cluster picker>  ──►  Overview · Namespaces · Workloads · Networking ·
                            Storage · Config · Observability · Events
```

It is not "another tab on the Fleet page." It links *out* to things we already
built (Grafana/Prometheus for Observability, the AI Agent, the Audit Log) but owns
its own routing, state, and backend. A platform engineer should be able to live in
`#/platform` all day and never touch a terminal.

What it is **not** in v1: a Marketplace/catalog (link to Helm later), a CNI/NSX
policy editor (read-only views only), or a cluster-*provisioning* wizard (manage
existing clusters first; create-cluster is a later phase).

---

## 2. Architecture

### 2.1 New service: `vks-broker`

A dedicated microservice — **not** a router bolted onto api-gateway. Rationale:
it holds admin credentials to every tenant cluster, so it gets its own blast
radius, its own ServiceAccount, its own NetworkPolicy, and its own audit hooks.
api-gateway proxies to it the same way it proxies the collectors.

```
browser ──► api-gateway (/api/v1/vks/*) ──► vks-broker ──┬─► supervisor API (list clusters, read kubeconfig secrets)
                                                          ├─► workload cluster A API server
                                                          ├─► workload cluster B API server
                                                          └─► …
```

Responsibilities:
- **Cluster discovery** — list VKS clusters from the supervisor.
- **Per-cluster kubeconfig resolution + cache** — fetch each cluster's admin
  kubeconfig, build a cached httpx client per cluster.
- **Typed resource proxy** — generic read/write for core/apps/batch/networking
  resources, namespaced by cluster.
- **Action endpoints** — scale, rollout-restart, cordon, drain, delete, logs (SSE).
- **Audit emission** — every mutating call publishes to `mco:events` (the existing
  audit/alert bus) before it executes.

> ⚠️ **Linkerd**: like discovery-engine, vks-broker talks to many external API
> servers over TLS with client certs. Start it **un-meshed** (`linkerd.io/inject:
> disabled`) to avoid the mesh interfering with mTLS to tenant clusters; revisit
> if we want mesh policy on it. (See [[pods.md]] discovery-engine note for the
> meshing-breaks-raw-traffic precedent.)

### 2.2 How cluster auth actually works (VCF 9.x / CAPI)

This is the part that makes or breaks the project, so it's concrete:

1. Each VKS cluster is a Cluster-API `Cluster` object living in a **supervisor
   namespace** (e.g. `bynet` in the screenshots). Enumerate via the supervisor:
   `GET /apis/cluster.x-k8s.io/v1beta1/clusters` (and/or the VKS-specific
   `vmware.com` cluster kind) using the supervisor kubeconfig we already mount.
2. For each cluster, CAPI publishes an **admin kubeconfig as a Secret** named
   `<cluster>-kubeconfig` in that same supervisor namespace. The broker reads it
   (`GET /api/v1/namespaces/<ns>/secrets/<cluster>-kubeconfig`), base64-decodes
   `data.value`, and now has a working kubeconfig for that workload cluster. This
   is exactly what ICP's "Download kubeconfig" button returns.
3. Cache the resulting per-cluster httpx client (TTL ~10 min; refresh on 401).

No new credentials to provision — we inherit the supervisor's authority. That is
also why the broker is security-sensitive: supervisor-admin ≈ all-tenant-admin.

### 2.3 Typed resource proxy

One generic handler covers most kinds instead of N hand-written endpoints:

```
GET    /api/v1/vks/{cluster}/{group}/{version}/{kind}                # list (all ns)
GET    /api/v1/vks/{cluster}/ns/{ns}/{group}/{version}/{kind}        # list (ns)
GET    /api/v1/vks/{cluster}/ns/{ns}/{group}/{version}/{kind}/{name} # get
POST   /api/v1/vks/{cluster}/ns/{ns}/{group}/{version}/{kind}        # create (apply YAML)
PUT    /api/v1/vks/{cluster}/ns/{ns}/{group}/{version}/{kind}/{name} # replace
PATCH  /api/v1/vks/{cluster}/ns/{ns}/{group}/{version}/{kind}/{name} # strategic-merge
DELETE /api/v1/vks/{cluster}/ns/{ns}/{group}/{version}/{kind}/{name} # delete
```

A **kind allowlist** (mirroring the kubectl-subcommand allowlist we already ship)
constrains which resources the console can touch — start with: Namespace,
Deployment, StatefulSet, DaemonSet, ReplicaSet, Pod, Job, CronJob, Service,
Ingress, ConfigMap, Secret(redacted), PVC, ServiceAccount, HPA, NetworkPolicy.
Cluster-scoped/privileged kinds (ClusterRole, CRDs, nodes-write) are **off** in v1
except specific node actions below.

### 2.4 Action endpoints (verbs, not just CRUD)

Higher-level operations the UI buttons map to (each one audited, destructive ones
require a confirm token — see §4):

```
POST /api/v1/vks/{cluster}/ns/{ns}/deployments/{name}/scale        {replicas}
POST /api/v1/vks/{cluster}/ns/{ns}/deployments/{name}/restart      # rollout restart
POST /api/v1/vks/{cluster}/ns/{ns}/pods/{name}/delete
GET  /api/v1/vks/{cluster}/ns/{ns}/pods/{name}/logs?container=&follow=  # SSE stream
POST /api/v1/vks/{cluster}/nodes/{name}/cordon | /uncordon | /drain
POST /api/v1/vks/{cluster}/apply                                   # Import YAML (multi-doc)
GET  /api/v1/vks/{cluster}/{ns}/events                             # event feed
GET  /api/v1/vks/clusters                                          # cluster picker
GET  /api/v1/vks/{cluster}/kubeconfig                              # Download kubeconfig
```

`logs` and `events` stream over SSE through the same heartbeat pattern the kubectl
broadcast and discovery scan streams already use.

---

## 3. UI

Standalone app shell under `#/platform`, lazy-loaded like the other pages.

### 3.1 Screens (mapped to the screenshots)

| Screen | Screenshot | Contents |
|---|---|---|
| **Cluster picker** | left rail "All clusters → lidor-vks-cluster01" | cards: name, status, K8s version, node count, namespace; click to enter |
| **Overview** | ICP Overview | health summary, node capacity, workload counts, recent events, quick links to Grafana |
| **Namespaces** | ICP Namespaces | list + create/delete; per-ns resource quota & usage |
| **Workloads** | shot 1 | tabs Deployments/StatefulSets/DaemonSets/Jobs/CronJobs/Pods; table (name, status, ready, restarts, node, age) with row actions **View logs / Edit YAML / Scale / Restart / Delete**; **Create** + **Import YAML** buttons |
| **Create workload** | shots 2–3 | Type/Namespace/Name/Replicas/Description + tabbed container editor (General, Networking, Resources, Health Check, Security Context, Environment), multi-container `+ Add Container`, **Edit as YAML** toggle |
| **Networking** | ICP Networking | Services, Ingresses, NetworkPolicies (read + basic edit) |
| **Storage** | ICP Storage | PVCs, StorageClasses, capacity |
| **Config** | ICP Config | ConfigMaps, Secrets (values masked, reveal-on-demand, audited) |
| **Events** | ICP Events | live cluster event stream, filterable by ns/severity |
| **Observability** | ICP Observability | **link out** to existing Grafana/Prometheus dashboards, not rebuilt |

### 3.2 Reused building blocks

- **YAML editor**: add Monaco (`@monaco-editor/react`) for "Edit YAML" / "Edit as
  YAML" / "Import YAML". One new dependency; worth it.
- **Logs viewer**: the KubectlPage terminal-pane component generalizes into a
  pod-logs streamer.
- **Create-form ⇄ YAML**: the form builds a manifest object; the "Edit as YAML"
  toggle round-trips through the same object so the two views never diverge.
- **Confirm dialog + focus trap**: reuse the `ConfirmDialog` we built for kubectl.
- **Primitives**: `PageHeader`, `Skeleton`, `EmptyState` from `ui.tsx`.

### 3.3 The AI layer (the differentiator — step 4)

This is why it's not just an ICP clone. Threaded through every screen:

1. **NL → manifest in the create form.** "nginx with 3 replicas, 256Mi limit,
   readiness probe on /healthz" → the agent fills the form / YAML. We already have
   `/generate/kubectl`; add a `/generate/manifest` sibling.
2. **One-click diagnose.** On any failed/crashlooping pod: an **"Explain"** button
   that feeds describe + last logs + events to the agent and returns root cause +
   fix, reusing the kubectl `/explain` path.
3. **NL actions.** A command bar in the console: "scale frontend to 5 in bynet",
   "restart the deployment that's crashlooping" → agent resolves to a typed action
   call, shows a confirm with the concrete diff, then executes.
4. **Inline risk notes.** Before a destructive confirm, the agent annotates blast
   radius ("this Deployment fronts Service X with an external IP; deleting it drops
   3 endpoints").

OCP and ICP cannot do any of this. It's the reason to build ours rather than buy.

---

## 4. Security model (decision: confirm + audit, no maintenance window)

Interactive cluster management is **not** a maintenance activity, so the
maintenance-window gate that guards kubectl writes is explicitly **not** applied
to VKS Console. Instead:

1. **Identity** from oauth2-proxy (`X-Forwarded-User`). The broker records it on
   every call. A future role map (`viewer` / `operator` / `admin`) can gate verbs;
   v1 ships `operator` for all authenticated users with an audit trail.
2. **Confirm tokens on destructive ops.** delete / drain / scale-to-zero /
   secret-reveal require a two-step: the UI requests a short-lived confirm token
   describing the exact target, the user confirms, the action replays the token.
   Prevents accidental and replayed deletes.
3. **Full audit.** Every mutating call → `mco:events` before execution, with user,
   cluster, namespace, kind, name, verb, and (for scale/patch) the diff. Surfaces
   in the existing Audit Log page and heatmap.
4. **Kind allowlist** (§2.3) + **secret redaction** by default (reveal is a
   distinct, audited action).
5. **Broker isolation**: own ServiceAccount, NetworkPolicy limiting egress to the
   supervisor + tenant API servers, un-meshed, credentials never logged.
6. **Read-only toggle** (config flag) to ship viewing-only to cautious sites.

---

## 5. Phasing

| Phase | Scope | Est. | Risk |
|---|---|---|---|
| **A. Read layer** | `vks-broker` skeleton, cluster discovery + kubeconfig resolution, per-cluster Overview/Namespaces/Workloads/Pods/Services/Storage **read-only**, cluster picker, Events feed | 1 wk | low |
| **B. Actions** | scale / restart / cordon / drain / delete / live-edit YAML, confirm tokens, audit wiring, pod logs (SSE) | 1 wk | med (write path) |
| **C. Create** | tabbed create-workload form ⇄ YAML, Import YAML, multi-container, ConfigMap/Secret create | 1–2 wk | med |
| **D. AI layer** | `/generate/manifest`, pod diagnose button, NL command bar, destructive-op risk notes | 1 wk | low (reuses agent) |

Phases A→B→C→D each ship independently and usefully. A is a credible demo on its
own; D is what makes leadership care.

---

## 6. Open questions / risks

- **VKS cluster CRD specifics on this build.** Need to confirm the exact group/kind
  for cluster enumeration on 9.1 (`cluster.x-k8s.io` CAPI vs a VKS-native kind) and
  the kubeconfig secret naming — verify against 10.50.78.6 before coding Phase A.
- **Scale of clusters.** Per-cluster httpx client cache is fine for tens of
  clusters; revisit if hundreds.
- **Secret handling.** Showing/editing Secrets through a web UI is a real exposure;
  default-masked + audited reveal is the floor, consider a site flag to disable
  entirely.
- **Supervisor-admin authority.** The broker is effectively cluster-admin on every
  tenant. NetworkPolicy + un-meshed + its own SA are necessary; an eventual
  per-user RBAC passthrough (impersonation) would be the stronger long-term model.
- **Create-cluster / node-pool lifecycle** is deliberately out of v1 — it's a
  separate, heavier workstream (CAPI machine deployments, node images).

---

## 7. Reuse map (what we don't rebuild)

| Need | Existing asset |
|---|---|
| supervisor + workload kubeconfig plumbing | `kubectl.py` router |
| SSE streaming w/ heartbeat | kubectl broadcast, discovery scan events |
| NL → kubectl / explain | llm-gateway `/generate/kubectl`, `/explain` |
| audit bus + heatmap | `mco:events`, Audit Log page |
| confirm dialog + focus trap | kubectl `ConfirmDialog` |
| UI primitives, motion, palette | `ui.tsx`, tailwind motion system |
| Observability | existing Grafana/Prometheus (link, don't rebuild) |
| read-only single-cluster views | `K8sPage` components (generalize to multi-cluster) |
