# What Is MCO? A Plain-English Guide

MCO is an AI-powered operations dashboard for VMware Cloud Foundation (VCF) environments. It connects to your vCenter, SDDC Manager, and NSX, then gives you a single place to understand what's happening, what's at risk, and what to do next — without needing to dig through six different VMware consoles.

---

## The Problem It Solves

Running a VCF environment means juggling vCenter, SDDC Manager, vROps, NSX, Log Insight, and your Kubernetes clusters — each with its own UI, its own alerts, and its own idea of what "healthy" means. When something goes wrong, or when you want to know "are we ready to upgrade?", the answer lives across all of them and requires expert interpretation.

MCO pulls everything together, scores your environment, explains what it finds in plain English, and lets you take action directly from the same screen.

---

## The Main Pages

### Analysis (Home)
This is the core feature. Type a question like _"Is our environment ready to upgrade to VCF 9.1?"_ or _"What are our biggest capacity risks?"_ and the platform:
1. Collects live data from all your VMware APIs simultaneously
2. Scores your environment on a 0–100 scale across six dimensions (CPU headroom, memory, storage, platform health, host health, VCF compatibility)
3. Sends the scored data to an AI model that writes a plain-English explanation of what it found and what to do

You get a readiness score, the specific blockers, and an action plan — in under a minute.

### Fleet
A live overview of your entire VMware environment: how many hosts, VMs, clusters, and datastores you have; the SDDC version; network and storage health; and a score trend line so you can see if things are getting better or worse over time. No manual data gathering — it refreshes automatically.

### Platform Console
A Kubernetes management view for your VKS (VMware Kubernetes Service) clusters. Shows all namespaces, pods, deployments, services, storage volumes, network policies, and security findings — the "inside the container" view that vCenter doesn't give you. Built-in AI explains any NetworkPolicy in plain English.

### Workspace
Type a plain-English description of what you want to do in vCenter — "create a VM with 4 vCPUs and 16 GB RAM in the production cluster" — and the platform generates and executes the API call for you. Also supports PowerCLI script generation for more complex tasks.

### Agent
A free-form AI chat assistant that knows your environment. Ask anything: "Why did the score drop last Tuesday?", "Which hosts are closest to capacity?", "What does this NSX error mean?". Conversation history is saved so you can pick up where you left off.

### Discovery
Runs an nmap network scan across CIDRs you specify, maps every live host and open port, and tracks changes between scans (new hosts, closed ports, etc.). Useful for finding shadow IT or verifying your network inventory is accurate.

### VulnScan
Runs a Nuclei vulnerability scan against your environment. Three levels: safe (read-only checks), standard (active but non-destructive), and full (comprehensive). Findings are grouped by severity, can be suppressed with a reason, and can be re-verified after a fix is applied.

### Directory (Active Directory)
Pulls your AD structure and highlights security-relevant findings: privileged accounts, Kerberoastable service accounts, computers not seen recently, group membership changes. Useful for security reviews and access audits without needing to run PowerShell against your domain controllers.

### Audit
Every action taken through the platform is logged — who did it, when, what the request was, and what the result was. Filter buttons surface the most interesting events: failed requests, after-hours activity, destructive operations, and configuration changes.

### Alerts
Define rules that fire when specific events happen (e.g., "score drops below 60" or "a destructive API call is made after 10pm"). Routes notifications to Slack, Teams, PagerDuty, or any webhook. Powered by a Redis pub/sub event bus so alerts fire in real time.

### Compliance
One click exports a complete compliance bundle: the full audit log, vulnerability findings, AD security overview, and fleet scoring data — all in a single timestamped tar.gz. Useful for security reviews, SOC audits, or regulatory checkpoints.

### Trends
Charts the score history over time, with per-dimension sparklines (CPU, memory, storage, etc.) so you can see which specific area is degrading. If the score dropped last Thursday, you'll see exactly which sub-score pulled it down.

### Bulk Operations
Provision multiple VMs at once from a template, or manage AD users in bulk (create, disable, reset passwords). Operations are gated by maintenance windows — the system won't let you make changes outside your defined maintenance schedule unless you explicitly override.

### Kubectl
Type what you want to do in Kubernetes ("show me all pods that are not running") and the platform generates the `kubectl` command, runs it against your cluster, and streams the output back. Also gated by maintenance windows for write operations.

### Settings
Where you configure everything: vCenter, SDDC Manager, NSX, and vROps credentials; which AI provider to use (Anthropic Claude, OpenAI, Google Gemini, or a local Ollama model running on-prem); scoring thresholds; and maintenance windows.

---

## How AI Is Used

AI is used in two ways:

**Explaining scored data** — After the platform collects and scores your environment, it sends the score and the raw evidence to an AI model. The model writes a narrative explanation: what the numbers mean, what's causing each risk factor, and what to do about it. The scoring itself is deterministic (no AI) — the AI only adds the explanation layer.

**Answering questions** — The Agent tab gives you a chat interface to the same AI model, with access to your environment data. It can look up your current scores, fleet state, and audit history to answer questions directly.

You choose the AI provider in Settings. The platform works fully on-prem with a local Ollama model if you don't want to send data to a cloud provider.

---

## Architecture in One Paragraph

The platform runs as 28 containers on a Kubernetes cluster. A React web app sits behind an OIDC login gate (oauth2-proxy + Dex), which authenticates you and forwards your identity to the backend. The backend is a FastAPI service that routes requests to specialized microservices: collectors that pull data from VMware APIs, a scoring engine that produces the 0–100 score, an orchestrator that coordinates the full analysis pipeline, and an LLM gateway that calls the AI provider you've configured. All environment data is kept within your cluster — nothing leaves unless you've configured a cloud AI provider.

---

## Who Is It For

- **Platform / VCF administrators** who manage the VMware infrastructure and want a faster way to assess upgrade readiness and track health over time
- **Security teams** who need a unified view of network exposure, vulnerability findings, AD hygiene, and a tamper-evident audit log
- **Operations teams** who respond to incidents and want AI-assisted root cause analysis without switching between five consoles
- **Management** who need a point-in-time readiness score and a downloadable compliance bundle for review meetings
