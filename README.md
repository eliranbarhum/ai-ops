# MCO — AI-Powered Operations Platform

MCO is an AI-powered operations platform for VMware environments. Whether you run vCenter alone, vCenter with VMware Operations, or a full VMware Cloud Foundation stack — MCO gives you a single place to understand your environment health, assess readiness, hunt for vulnerabilities, and take action.

**Key capabilities:**
- AI-generated analysis and plain-English explanations of your environment health
- Deterministic 0–100 readiness score across CPU, memory, storage, platform, hosts, and VCF compatibility
- Kubernetes cluster visibility (namespaces, pods, network policies, RBAC)
- Network discovery (nmap) and vulnerability scanning (Nuclei)
- Active Directory security overview (privileged accounts, Kerberoastable SPNs)
- Full audit log with compliance export
- Supports Anthropic Claude, OpenAI, Google Gemini, or on-prem Ollama

---

## Install with Helm

```bash
# 1. Clone the repo
git clone https://github.com/eliranbarhum/ai-ops.git
cd ai-ops

# 2. Create your values file
cp chart/values.yaml my-values.yaml
# Edit my-values.yaml — set portal.url, auth.adminPassword, and an LLM key

# 3. Install
helm install mco chart/ \
  --namespace mco \
  --create-namespace \
  --values my-values.yaml \
  --wait --timeout 10m

# 4. Get the portal IP
kubectl -n mco get svc oauth2-proxy
```

Point DNS at the EXTERNAL-IP and open `https://your-domain.com`.

**Full installation guide:** [docs/install.md](docs/install.md)

---

## What's in the box

**Observe**

| Page | What it does |
|------|-------------|
| **Fleet** | Live overview of hosts, VMs, clusters, datastores, version, and score trend |
| **Analysis** | Ask a question; get a deterministic readiness score + AI explanation in under a minute |
| **Platform Console** | Full Kubernetes visibility — pods, workloads, network policies, RBAC, secrets, PVCs |
| **Trends** | Score history charts and per-dimension sparklines over time |
| **Archive** | Browse and compare historical analysis results |

**Operate**

| Page | What it does |
|------|-------------|
| **Workspace** | Natural-language vCenter API explorer and PowerCLI script builder + executor |
| **MCP AI Agent** | Free-form AI chat with live access to your environment data and action history |
| **Bulk Ops** | Multi-VM provisioning and AD user management, gated by maintenance windows |
| **Guest** | Guest OS visibility across VMs |
| **Kubectl** | Natural-language kubectl command generator — runs against your cluster and streams output |

**Discover**

| Page | What it does |
|------|-------------|
| **Discovery** | nmap scan CIDRs; track new hosts and open ports between scans |
| **Vuln Scan** | Nuclei vulnerability scan — safe / standard / full profiles; suppress findings |
| **Directory** | AD security overview: privileged accounts, Kerberoastable SPNs, stale computers |
| **Audit Log** | Every action logged — filter by failed / after-hours / destructive / config-change |

**Platform**

| Page | What it does |
|------|-------------|
| **Alerts** | Rules engine → Slack / Teams / PagerDuty / webhook |
| **Compliance** | One-click export: audit log + vuln findings + AD overview + fleet score |
| **Settings** | Credentials, LLM provider, scoring thresholds, maintenance windows |

---

## Architecture

```
Browser
  │ HTTPS 443
  ▼
oauth2-proxy  ← TLS termination + OIDC auth (Dex)
  │
  ▼
ui (nginx)    ← React SPA (TypeScript, Tailwind, Vite)
  │ /api/*
  ▼
api-gateway   ← FastAPI, 19 routers
  │
  ├── orchestrator        ← coordinates full analysis pipeline
  ├── llm-gateway         ← multi-provider AI (Anthropic / OpenAI / Gemini / Ollama)
  ├── scoring-engine      ← deterministic 0-100 score (no AI)
  ├── normalization        ← raw VMware data → canonical schema
  ├── collector-vcenter   ─┐
  ├── collector-vrops     ─┤ pull live data from VMware APIs
  ├── collector-sddc      ─┤
  ├── collector-logs      ─┘
  ├── vks-broker          ← Kubernetes cluster visibility (ClusterRole)
  ├── discovery-engine    ← nmap + Nuclei
  ├── powercli            ← PowerShell/VMware.PowerCLI runner
  └── config-store        ← encrypted credentials on PVC

PostgreSQL (TimescaleDB) — audit log, agent history, score time-series
Redis                    — fleet cache, pub/sub alerts, pipeline dedup
```

All backend services are ClusterIP. Only `oauth2-proxy` has an external endpoint.

---

## Requirements

| | Minimum | Recommended |
|-|---------|-------------|
| Kubernetes | 1.28 | 1.30+ |
| Nodes | 3 × 4 vCPU / 16 GB | 3 × 8 vCPU / 32 GB |
| Storage class | Any RWO | Any RWO |
| Load balancer | MetalLB, cloud LB, or NodePort | MetalLB / cloud LB |
| AI provider | One of: Anthropic, OpenAI, Gemini, Ollama | Anthropic Claude Sonnet |

VMware integrations (vCenter, SDDC Manager, NSX, vROps, Log Insight) are optional at install time — they can be configured post-install in Settings.

---

## Scoring

Deterministic — no AI involved in the score calculation. The score is computed from live VMware API data and thresholds you can tune in Settings.

| Sub-Score | Weight | WARN | CRITICAL |
|-----------|--------|------|----------|
| CPU Headroom | 25 pts | >75% used | >90% used |
| Memory Headroom | 25 pts | >80% used | >92% used |
| Storage Latency | 15 pts | >10ms | >20ms |
| Platform Health | 15 pts | warning events | critical events |
| Host Health | 10 pts | degraded hosts | disconnected hosts |
| VCF Compatibility | 10 pts | 1–2 version gaps | ≥3 version gaps |

**Status:** READY ≥ 80 / WARNING ≥ 50 / NOT READY < 50

---

## Running fully on-premises with Ollama

MCO works without any cloud AI provider. Set `llm.provider: ollama` in your values file and point it at an Ollama instance — everything runs inside your network.

**Compute requirements for Ollama:**

| Model size | vCPU | RAM | GPU |
|------------|------|-----|-----|
| 7B (e.g. mistral, llama3) | 8 | 16 GB | optional — CPU inference works |
| 14B (e.g. qwen2.5:14b) | 16 | 32 GB | recommended (NVIDIA, 8 GB VRAM+) |
| 32B+ (e.g. qwen2.5:32b) | 32 | 64 GB | required (24 GB VRAM+) |

**Recommended models** (pull with `ollama pull <model>`):

| Model | Best for | Notes |
|-------|---------|-------|
| `qwen2.5:14b` | General analysis, agent, workspace | Best quality/performance balance for air-gapped environments |
| `mistral:7b` | Fast responses, low-resource nodes | Good for fleet queries and quick questions |
| `llama3.1:8b` | General purpose | Strong reasoning, widely tested |
| `codellama:13b` | PowerCLI / script generation | Tuned for code tasks |
| `nomic-embed-text` | RAG / context retrieval | Required if using the Agent with document context |

**Best practices:**
- Run Ollama on a dedicated node or VM — do not share with workload pods
- Use GPU if running 14B or larger; CPU inference on 14B takes 30–90 seconds per request
- Set `OLLAMA_NUM_PARALLEL=1` on low-memory nodes to avoid OOM
- Point MCO at Ollama via `http://ollama:11434` if running in the same cluster, or at any reachable host/IP
- All model data stays inside your network — no outbound calls

Configure in `my-values.yaml`:
```yaml
llm:
  provider: ollama
  ollamaUrl: http://ollama:11434
  ollamaModel: qwen2.5:14b
```

---

## Documentation

- [Installation Guide](docs/install.md)
- [What is MCO?](docs/what-is-this.md) — plain-English overview for new users
- [Software Bill of Materials](docs/sbom.md)
- [Analysis Engine](docs/analysis-engine.md)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs are welcome.

## Security

See [SECURITY.md](SECURITY.md) for the vulnerability reporting policy and architecture security notes.

## License

Apache 2.0 — see [LICENSE](LICENSE).
