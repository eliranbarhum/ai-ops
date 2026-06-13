# We Built an AI-Powered Platform for the Entire IT Operations Team

Most IT teams are drowning in tools. The infrastructure admin has vCenter. The security team has a scanner. The sysadmin has a SIEM. The network engineer has their own console. The CISO wants a compliance report that pulls from all of them. Nobody talks to each other, and the data certainly doesn't.

We built **MCO — Mission Control Operations** to fix that. It's an AI-powered operations platform built for the whole team — infrastructure, security, and operations — in a single place. One portal, one audit trail, one AI that actually knows your environment.

This is the story of what we built and why.

---

## Who It's For

We didn't build MCO for a specific job title. We built it for the problems that show up across every IT team:

- The **infrastructure admin** who needs to know the health of their fleet at a glance and take action without logging into five different consoles
- The **CISO** who wants a single export showing vulnerability findings, AD security posture, and audit log — not a PowerPoint assembled from four different tools the night before a review
- The **sysadmin** who knows what they want to do but has to write the PowerShell or kubectl command from scratch every time
- The **security engineer** who wants to know about open ports, stale AD accounts, and exploitable SPNs without running three separate products
- The **operations manager** who wants to know what actions were taken, by whom, and when — and to gate changes behind maintenance windows so nothing runs at 2am by accident

MCO gives all of them one platform with a shared data model, a shared audit log, and a shared AI layer.

---

## What's Inside

### Fleet View

The starting point for any infrastructure admin. A live dashboard showing every host, VM, cluster, and datastore — CPU headroom, memory pressure, storage latency, version drift, and a readiness score. It auto-refreshes, it's fast, and it gives you the state of the environment without needing to open vCenter.

The score is deterministic and logged over time, so you can see whether the environment is improving or degrading week over week. Score drops correlate to real changes — not to the AI having a different opinion on Tuesday than it had on Monday.

### AI Analysis

Click analyze and in under a minute you get a scored readiness report with AI-generated findings and recommended actions in plain English. The AI layer explains *why* the score is what it is — which host is the most constrained, which datastore is approaching capacity, what the top three risks are and what to do about them.

The analysis is grounded in real inventory data from your environment, not generic documentation. If your specific host is at 94% memory utilization, the report says so by name.

### Network Discovery and Vulnerability Scanning

Built on **nmap** and **Nuclei**. Define a CIDR range, run a scan, get back open ports, service fingerprints, and CVE-level findings. Scans run in safe, standard, or full profiles. Schedule them to run automatically. Suppress known-good results with a reason. Re-scan a specific target after patching to verify the fix.

Between scans, MCO tracks what appeared and disappeared. That's how you find shadow IT, unexpected devices, and open ports on hosts that shouldn't have any.

### Active Directory Security

Point MCO at a domain controller and it enumerates privileged group memberships, identifies accounts with Kerberoastable SPNs, flags stale computer accounts, and cross-references stale accounts that are still in privileged groups. Everything exports to CSV.

This is the kind of analysis a red team does in the first hour of an engagement. Now your blue team can run it on a schedule.

### Audit Log

Every action taken through MCO is logged — user, IP, timestamp, operation, and result. Stored in TimescaleDB with a one-year retention policy. Filter by failed operations, after-hours activity, destructive actions, or configuration changes. No extra tooling needed for a basic compliance audit trail.

### Compliance Export

One button. Bundles the audit log, vulnerability findings, AD security posture, and fleet readiness score into a single export. Hand it to auditors, attach it to a change advisory board submission, include it in an incident report. The data is already there — MCO just assembles it.

### Workspace

Describe what you want to do in plain English. MCO generates the API call or PowerShell script, explains what it will do, and executes it. Automate routine tasks without writing code from scratch every time.

### Kubectl in Plain English

Describe what you want to do with a Kubernetes cluster in plain English. MCO generates the kubectl command, explains it, and streams the output. Cluster operations become accessible to the whole ops team — not just the one person who remembers the flags.

### MCP AI Agent

A conversational AI with live access to your actual environment data. Not a generic chatbot that quotes documentation — an agent that knows your specific clusters, your recent audit events, your AD security findings. Ask it to investigate an issue, suggest a fix, or walk you through a procedure.

### Bulk Operations

Provision multiple VMs, manage AD users, or apply configuration changes across groups of machines — all gated by configurable maintenance windows. If a window isn't active, the operation is blocked at the API level. No more changes running at the wrong time because someone forgot.

### Alerts

Define rules on any metric or event. Route notifications to Slack, Teams, PagerDuty, or any webhook. Get notified when a readiness score drops below a threshold, a scan finds a critical vulnerability, or a host enters an unexpected state.

---

## How It's Built

The backend is Python (FastAPI), the frontend is React with TypeScript, and it all runs on Kubernetes. 16 services, each with a clear responsibility.

| Service | Role |
|---------|------|
| `api-gateway` | Single entry point; 18 routers; auth enforcement; audit logging |
| `orchestrator` | Coordinates multi-step analysis pipelines |
| `tools` | Infrastructure API calls and data normalization |
| `collector-vcenter` | vCenter inventory and health |
| `collector-vrops` | Metrics and alarms from VMware Operations |
| `collector-sddc` | SDDC Manager domain data |
| `collector-logs` | Log aggregation |
| `scoring-engine` | Deterministic 0–100 scoring; history in TimescaleDB |
| `llm-gateway` | Claude / OpenAI / Gemini / Ollama abstraction layer |
| `config-store` | Encrypted credential storage; conversation history |
| `discovery-engine` | nmap + Nuclei; scan scheduling; live output streaming |
| `powercli` | Containerized PowerShell execution |
| `ui` | React SPA served by nginx |
| `postgresql` | TimescaleDB for time-series data and conversations |
| `redis` | Fleet cache; pub/sub for scan output; alert debounce |

### Authentication

Dex OIDC + oauth2-proxy — both run as pods alongside the application. Dex handles identity (static accounts + Active Directory LDAP connector). oauth2-proxy validates tokens and injects the user identity into every request. When you save AD settings in MCO, the platform automatically updates the Dex LDAP connector and restarts Dex — AD users can log in immediately without touching Kubernetes.

### AI Layer

The LLM gateway abstracts over four providers:

- **Anthropic Claude** — default for analysis and agent tasks
- **OpenAI** — GPT-4o class models
- **Google Gemini** — alternative for large-context tasks
- **Ollama** — for fully on-prem, air-gapped deployments

You pick the provider in Settings. Rotating API keys doesn't require a restart.

For teams that need everything on-prem, Ollama works well. Recommended setup: 32 GB RAM minimum, dedicated GPU. Models that perform well for this use case:

| Use case | Model |
|----------|-------|
| Analysis + agent (best quality) | `qwen2.5:14b` |
| Fast responses | `mistral:7b` |
| General purpose | `llama3.1:8b` |
| Script generation | `codellama:13b` |

### Observability

Prometheus metrics from every service, Grafana dashboards for request rates, latency, and error rates. Daily `pg_dump` backup CronJob with 14-day retention. 31 smoke tests that run on every deploy.

---

## Things We Learned

**The audit trail is the most underrated feature.** We built it because compliance requires it. It turned out to be one of the most useful things in the platform — not for auditors, but for the ops team. "Who changed that config?" and "what happened between 2am and 3am last night?" are questions that come up constantly, and having a complete answer immediately changes how the team operates.

**Maintenance windows belong in the platform, not in the calendar.** Every team has a change management process. Most of it lives in a spreadsheet or a ticketing system that has no connection to the tools that actually make changes. Putting maintenance windows in MCO and having them gate operations at the API level means the policy is enforced, not just documented.

**AI is most useful when it knows your specific environment.** A generic LLM that answers questions about infrastructure is moderately useful. An agent that has your actual host names, your current AD stale accounts, and your last three audit events is a different thing entirely. The value compounds with the data.

**Microservices are the right call for this, but own the operational complexity.** 16 services means 16 images, 16 health checks, and 16 places to look when something breaks. The benefit is that pushing a new scanner or router doesn't touch auth, the AI layer, or the UI. That independence is what made it possible to iterate quickly. But don't underestimate the maintenance surface.

**Auth headers are subtle and wrong defaults are expensive.** We had two weeks of audit logs showing a long base64 string instead of a username because oauth2-proxy sets `X-Forwarded-Preferred-Username` to the OIDC sub claim when `preferred_username` is absent. One line of code — check email header first — fixed it. The lesson: test your auth stack end-to-end before you trust what shows up in logs.

---

## What's Next

- **Helm chart for general distribution** — so any team can install this in their own Kubernetes cluster
- **Agent RAG** — giving the AI agent access to knowledge bases and runbooks for richer recommendations
- **Multi-tenant support** — separate namespaces per team or customer environment
- **TanStack Query migration** — better client-side caching and background refresh in the UI

---

## Try It

MCO is open source. If your team wants a single platform for infrastructure visibility, security scanning, AD analysis, and AI-assisted operations — give it a try.

**[github.com/eliranbarhum/ai-ops](https://github.com/eliranbarhum/ai-ops)**

The repo includes a Helm chart and a full installation guide. You'll need a Kubernetes cluster, access to your infrastructure APIs, and either an LLM API key or an Ollama instance for on-prem deployments.

Issues and contributions are welcome.
