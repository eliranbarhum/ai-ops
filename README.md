# MCO — Mission Control Operations

MCO (Mission Control Operations) is an AI-powered platform for infrastructure, security, and IT operations teams. One portal for your whole team — fleet visibility, vulnerability scanning, Active Directory security, audit logging, compliance exports, AI-assisted scripting, and more.

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
| **Fleet** | See every host, VM, cluster, and datastore in one view — CPU headroom, memory pressure, storage latency, version drift, and a live readiness score. Know the state of your environment before your monitoring tool tells you something broke. |
| **Analysis** | Ask any question about your environment in plain English and get back a scored readiness report with AI-generated findings, risks, and recommended actions — in under a minute. Covers capacity, platform health, HCL compatibility, host state, and more. |
| **Platform Console** | Full Kubernetes visibility without leaving the platform — browse namespaces, pods, workloads, network policies, RBAC roles, secrets, PVCs, and resource quotas. Spot misconfigurations, orphaned resources, and security gaps across your clusters. |
| **Trends** | Track your readiness score and every sub-dimension over time. See whether your environment is improving or degrading week over week, and correlate score drops to specific changes. |
| **Archive** | Every analysis is saved. Go back to any historical result, compare it to today, and show auditors or management exactly what the environment looked like at a specific point in time. |

**Operate**

| Page | What it does |
|------|-------------|
| **Workspace** | Describe what you want to do in plain English — MCO generates the vCenter API call or PowerCLI script, explains what it will do, and executes it on your behalf. Automate routine tasks without writing a single line of PowerShell from scratch. |
| **MCP AI Agent** | A conversational AI that has live access to your environment data, action history, and VMware knowledge base. Ask it to investigate an issue, suggest a fix, or walk you through a procedure — it knows your specific environment, not just generic documentation. |
| **Bulk Ops** | Provision multiple VMs, manage AD users, or apply configuration changes across groups of machines — all gated by configurable maintenance windows so nothing runs at the wrong time. |
| **Guest** | Visibility into guest OS details across your VM fleet — operating system, version, tools status, and configuration — without needing to log into each machine individually. |
| **Kubectl** | Describe what you want to do with your Kubernetes cluster in plain English. MCO generates the kubectl command, explains it, and streams the output back — making cluster operations accessible to the whole ops team, not just Kubernetes specialists. |

**Discover**

| Page | What it does |
|------|-------------|
| **Discovery** | Scan any CIDR range with nmap and automatically track what appears or disappears between scans. Find unmanaged devices, shadow IT, and unexpected open ports before attackers do. |
| **Vuln Scan** | Run Nuclei vulnerability scans against your environment in safe, standard, or full profiles. Review findings, suppress known-good results, and track remediation over time — without standing up a separate scanning tool. |
| **Directory** | Get an instant AD security posture — privileged group membership, accounts with Kerberoastable SPNs, stale computer accounts, and password policy gaps. Surface the most common Active Directory attack paths before a red team does. |
| **Audit Log** | Every action taken through MCO is logged with user, timestamp, and result. Filter by failed operations, after-hours activity, destructive actions, or configuration changes. Ready for compliance reviews without any additional tooling. |

**Platform**

| Page | What it does |
|------|-------------|
| **Alerts** | Define rules on any metric or event and route notifications to Slack, Microsoft Teams, PagerDuty, or any webhook. Get notified when readiness drops below a threshold, a scan finds a critical vulnerability, or a host goes into an unexpected state. |
| **Compliance** | Generate a single export that bundles your audit log, vulnerability findings, AD security overview, and fleet readiness score. Hand it to auditors, include it in a change advisory board submission, or attach it to an incident report. |
| **Settings** | Configure VMware credentials, choose your AI provider and model, tune scoring thresholds to match your environment's risk tolerance, and set maintenance windows that gate destructive operations. |

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
