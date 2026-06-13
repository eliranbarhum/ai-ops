# MCO — Pod Reference

All pods run in the `vcf-ai-ops` namespace on the VKS cluster (`~/.kube/vcf-ai-ops-cluster.kubeconfig`).

_Last updated: 2026-06-13 — telegram-bot removed; llm-gateway /chat endpoint added; pipeline dedup lock fixed; netpol YAML generator rewritten_

**External exposure:** only `oauth2-proxy` (10.50.78.27:80/443, TLS on 443) and `dex` (10.50.78.28:5556) are LoadBalancer services. `ui` and `api-gateway` are ClusterIP — exposing them directly allows auth bypass via forged `X-Forwarded-User`.

---

## User-Facing

| Pod | Replicas | Port | CPU req/lim | Mem req/lim | What it does |
|-----|----------|------|-------------|-------------|--------------|
| `ui` | 2 | 3000 | 100m / 500m | 128Mi / 256Mi | React SPA served by nginx with gzip + asset caching. **17 pages:** Analysis, Fleet, Workspace, Agent, Archive, Trends, Discovery, VulnScan, Directory (AD), Audit, Alerts, Compliance, Bulk Provisioning, Guest Access, Kubectl, Settings, **Platform Console** (`#/platform`). nginx also proxies `/api/` → api-gateway, forwarding `X-Forwarded-User` from oauth2-proxy. |
| `api-gateway` | 2 | 8000 | 100m / 2000m | 256Mi / 1Gi | REST entry point (`/api/v1/*`). **19 FastAPI routers**: `analysis`, `config`, `fleet`, `workspace`, `ollama`, `kubectl`, `agent`, `bulk`, `guest`, `mcp`, `discovery`, `health`, `audit`, `ad`, `alerts`, `maintenance`, `compliance`, `upgrade`, `vks`. Fleet cache → **Redis** (60s TTL). Shared httpx pool (lifespan). Background **alerter** subscribes to `mco:events` on Redis and fires webhooks on matched rules. Auth guard requires `X-Forwarded-User` (set `REQUIRE_AUTH=false` in ConfigMap to disable). |
| `vks-broker` | 1 | 8012 | 100m / 1000m | 256Mi / 512Mi | **VKS Platform Console backend** — dedicated microservice (not a router in api-gateway) with its own blast radius and NetworkPolicy. Fetches kubeconfig secrets from the VCF supervisor (10.50.78.6), then calls each workload cluster's API server directly. Provides: cluster list, overview, nodes, namespaces, workloads (deploy/sts/ds/cron/job), pods, services, ingresses, PVCs, configmaps, secrets, events, RBAC, HPAs, PDBs, networkpolicies, serviceaccounts, images, orphans, quota/limitranges, TLS certs, affinity coverage, restart timeline, PVC analysis, security audit, namespace labels/PSA, fleet health. 30 cluster-scoped + 6 fleet-scoped endpoints. API proxied by api-gateway at `/api/v1/vks/*`. |

---

## AI Pipeline

| Pod | Replicas | Port | CPU req/lim | Mem req/lim | What it does |
|-----|----------|------|-------------|-------------|--------------|
| `orchestrator` | 2 | 8001 | 300m / 2000m | 512Mi / 1Gi | Pipeline coordinator — fans out tool calls in parallel, calls scoring engine, proxies LLM token stream to api-gateway |
| `llm-gateway` | 1 | 8008 | 200m / 2000m | 512Mi / 1Gi | LLM interface — split into 5 routers (`explain`, `generate`, `agent_routes`, `mcp_routes`, `health`) + `providers.py` for all 4 LLM backends. BM25 RAG retrieval over VCF 9.1 docs, builds prompts, streams tokens. `/chat` endpoint added for single-turn free-form prompts (used by vks-broker for NetworkPolicy explanation). |
| `vllm-server` | 1 | 11434 | 2 / 8 | 14Gi / 16Gi | Ollama container running the active on-prem LLM — model files persist on a 100Gi PVC (`vllm-model-cache`) |
| `scoring-engine` | 2 | 8007 | 200m / 1000m | 256Mi / 512Mi | Deterministic readiness scorer (no LLM) — writes history to **TimescaleDB** (`scoring_history` hypertable). Produces 0–100 score + 6 sub-scores: cpu, memory, storage, platform, hosts, hcl |

---

## Data Collection & Tools

| Pod | Replicas | Port | CPU req/lim | Mem req/lim | What it does |
|-----|----------|------|-------------|-------------|--------------|
| `tools` | 2 | 8002 | 100m / 1000m | 256Mi / 512Mi | Tool service — 8 tools + **inlined normalization** (`normalizers.py` imported directly, no HTTP hop). Tools: `get_vcenter_inventory`, `get_cluster_capacity`, `get_esxi_metrics`, `get_vrops_metrics`, `query_logs`, `check_vcf_compatibility`, `check_broadcom_interop`, `get_sddc_health` |
| `collector-vcenter` | 1 | 8003 | 100m / 1000m | 256Mi / 512Mi | Pulls inventory and metrics from the vCenter REST API |
| `collector-vrops` | 1 | 8004 | 100m / 1000m | 256Mi / 512Mi | Pulls performance metrics from vRealize Operations (vROps) |
| `collector-logs` | 1 | 8005 | 100m / 1000m | 256Mi / 512Mi | Collects log data from the environment (Log Insight / Aria Logs) |
| `collector-sddc` | 1 | 8011 | 100m / 1000m | 256Mi / 512Mi | Pulls domain, lifecycle, and upgrade-readiness data from SDDC Manager |

---

## Infrastructure

| Pod | Replicas | Port | CPU req/lim | Mem req/lim | What it does |
|-----|----------|------|-------------|-------------|--------------|
| `config-store` | 1 | 8009 | 200m / 1000m | 256Mi / 512Mi | Encrypted credential store — Fernet on PVC. Agent **conversations backed by PostgreSQL** (`agent_conversations` table), falls back to JSON file if DB unreachable. All services fetch live config from `/config/raw`. |
| `powercli` | 1 | 8010 | 200m / 1000m | 512Mi / 1Gi | PowerCLI execution service — runs VMware PowerShell scripts from the Workspace page |
| `discovery-engine` | 1 | 8010 | 100m / 500m | 128Mi / 512Mi | Network discovery via nmap. SSE scan events published via **Redis pub/sub** (`scan:{id}` channels), falls back to in-memory asyncio.Queue. **NOT meshed** (`linkerd.io/inject: disabled`): a transparent proxy fakes connect() opens and swallows raw SYN-ACKs — scans return garbage when meshed. Phase-1 ping results stored immediately as placeholder rows (`ports='[]'`); phase 2 enriches in place. `/scans/{id}/diff` (new/missing vs previous scan) and `/scans/{id}/export` (CSV). |
| `postgresql` | 1 (StatefulSet) | 5432 | 100m / 1000m | 256Mi / 1Gi | **TimescaleDB** (PostgreSQL 16). Stores `scoring_history` hypertable and `agent_conversations`. PVC: 10Gi on `vcf-sp`. |
| `redis` | 1 | 6379 | 50m / 500m | 64Mi / 256Mi | Redis 7 with AOF persistence. Used for fleet cache (api-gateway) and scan event pub/sub (discovery-engine). PVC: 2Gi on `vcf-sp`. |
| `oauth2-proxy` | 1 | 8443 (https) + 4180 (http→https redirect) | 50m / 200m | 64Mi / 128Mi | OIDC session gateway fronting the UI — **terminates TLS** with the `mco-tls` cert (LB 10.50.78.27:443). Authenticates via Dex, sets `X-Forwarded-User`/`X-Forwarded-Email` headers passed through nginx to api-gateway. `--cookie-secure=true`. Sidecar `http-redirect` (nginx:1.27-alpine, 1 worker) serves port 4180 → 301 https, since oauth2-proxy can't serve plain HTTP alongside TLS. |
| `dex` | 1 | 5556 | — | — | OIDC identity provider — issued tokens for oauth2-proxy. Accessed via nginx `/dex/` proxy. |

---

## K8s Secrets & Config

| Name | Type | Contents |
|------|------|---------|
| `mco-secrets` | Secret | Bootstrap credentials seeded into config-store at startup |
| `postgresql-secret` | Secret | `POSTGRES_PASSWORD` + `POSTGRES_URL` (injected into scoring-engine and config-store pods) |
| `mco-config` | ConfigMap | All service URLs + `REDIS_URL`, `REQUIRE_AUTH`, `ALLOWED_ORIGINS` |
| `mco-tls` | Secret (TLS) | TLS cert + key (self-signed, SAN `mco.example.com` + `*.example.com`, valid to 2036). Mounted into oauth2-proxy for TLS termination. |
| `oauth2proxy-redirect` | ConfigMap | nginx.conf for the `http-redirect` sidecar (port 80 → 301 https). Manifest: `k8s/auth/oauth2proxy-redirect-configmap.yaml` |
| `dex-config` | Secret | Dex OIDC config: issuer, staticClients (redirectURIs incl. https callback), LDAP connector, local admin |
| `oauth2proxy-secret` | Secret | OIDC client id/secret, cookie secret, `OAUTH2_PROXY_REDIRECT_URL` (https) |

---

## Request Flow

```
Browser (https://mco.example.com)
  └─► oauth2-proxy :8443   (TLS termination + OIDC session; port 80 → 301 https via sidecar)
        └─► ui :3000 nginx (serves SPA; proxies /api/ + /dex/, forwards X-Forwarded-User)
  └─► api-gateway          (REST + SSE proxy)
        ├─► Redis           (fleet cache: GET/SET fleet:cache key, 60s TTL)
        ├─► orchestrator    (analysis pipeline)
        │     ├─► tools ×N  (parallel tool calls)
        │     ├─► scoring-engine ──► TimescaleDB (write scoring_history)
        │     └─► llm-gateway /explain ──► vllm-server (Ollama)
        ├─► llm-gateway /agent/chat  (AI Agent SSE stream)
        └─► config-store    (conversations ──► PostgreSQL)

discovery-engine
  ├─► nmap                  (network scan subprocess)
  └─► Redis pub/sub         (publish scan events to scan:{id} channel)
        └─► SSE clients     (subscribe via Redis, fallback to asyncio.Queue)
```

**Tools call chain:**
```
tools ──► collector-vcenter   (inventory, datastores, networks)
      ──► collector-vrops     (metrics, host details, fleet appliances)
      ──► collector-logs      (log events / anomalies)
      ──► collector-sddc      (domain health, upgrade readiness)
      ──► normalizers.py      (inlined — direct Python call, no HTTP)
```

---

## UI Pages

| Page | Route | Description |
|------|-------|-------------|
| Analysis | `#/` | VCF readiness analysis form + score + AI explanation |
| Fleet | `#/fleet` | Full environment view: management plane, clusters, hosts, storage, network |
| Workspace | `#/workspace` | Natural-language vCenter API calls + PowerCLI script builder/executor |
| Agent | `#/agent` | Free-form AI chat with conversation history (stored in PostgreSQL) |
| Archive | `#/archive` | History of past analysis runs |
| Trends | `#/trends` | Score history charts, sub-score sparklines, risk factor trend |
| Discovery | `#/discovery` | Network discovery via nmap — scan CIDRs, view host inventory |
| VulnScan | `#/vulnscan` | Nuclei vulnerability scanner — safe/standard/full scope profiles, finding suppression, verify-fix re-scan |
| Directory | `#/directory` | Active Directory — users, computers, groups, privileged accounts, Kerberoastable accounts, CSV exports |
| Audit | `#/audit` | Audit log viewer — filter chips: failed / after-hours / destructive / config-changes |
| Alerts | `#/alerts` | Alert channels (Slack/Teams/webhook/PagerDuty) and rule engine with event matching |
| Compliance | `#/compliance` | One-click compliance export — tar.gz bundle of audit log, vuln findings, AD overview, fleet scoring |
| Bulk | `#/bulk` | Bulk VM provisioning + AD user management (gated by maintenance window) |
| Guest | `#/guest` | Guest access / simplified analysis view |
| Kubectl | `#/kubectl` | Natural-language kubectl command generator + terminal output (gated by maintenance window) |
| Settings | `#/settings` | Integration credentials, LLM provider selection, threshold config, maintenance windows |
| Platform Console | `#/platform` | VKS cluster management console — cluster picker, namespace/workload/node/network/storage/security views. Backed by `vks-broker`. Mirrors VCF ICP UX with an AI layer. |

---

## Changing the Active Model

Models are managed through the Settings page or directly via:

```
POST /api/v1/ollama/pull    { "model": "qwen2.5:7b" }
DELETE /api/v1/ollama/model { "model": "qwen2.5:7b" }
POST /api/v1/ollama/pull/reset   # clears stuck pull state
```

Model files survive pod restarts — stored on the `vllm-model-cache` PVC.
