# MCO — Installation Guide

This guide walks you from zero to a running MCO portal in about 15 minutes.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Kubernetes 1.28+ | Any distribution: GKE, EKS, AKS, RKE2, K3s, VKS, etc. |
| `kubectl` configured | Pointing at your target cluster |
| Helm 3.10+ | `helm version` to check |
| A storage class | Check with `kubectl get storageclass` |
| A way to expose services | LoadBalancer controller, NodePort, or an Ingress controller |
| DNS record (optional) | Point your domain at the load balancer IP after install |

> **Minimum cluster size:** 3 nodes × 4 vCPU / 16 GB RAM. The full platform runs ~28 pods plus infra (PostgreSQL, Redis, Grafana, Prometheus).

---

## Quick Start

### 1. Add the Helm repository

```bash
helm repo add mco https://eliranbarhum.github.io/vcf-ai-ops
helm repo update
```

Or install directly from source:

```bash
git clone https://github.com/eliranbarhum/ai-ops.git
cd vcf-ai-ops
```

### 2. Create your values file

```bash
cp chart/values.yaml my-values.yaml
```

Edit `my-values.yaml`. At minimum, set:

```yaml
portal:
  url: https://mco.yourdomain.com   # the URL you'll use to access the portal

auth:
  adminEmail: admin@yourdomain.com  # login email for the first user
  adminPassword: YourStrongPassword # change this — it's hashed at install time

storage:
  className: standard               # your cluster's storage class name

llm:
  provider: anthropic               # anthropic | openai | gemini | ollama
  anthropicKey: sk-ant-...          # your API key for the chosen provider
```

### 3. Install

```bash
helm install mco chart/ \
  --namespace mco \
  --create-namespace \
  --values my-values.yaml \
  --wait --timeout 10m
```

### 4. Get the portal URL

If you used `service.type: LoadBalancer` (default):

```bash
kubectl -n mco get svc oauth2-proxy
# Look for EXTERNAL-IP — this is your load balancer IP
```

Point your DNS A record at that IP. Example:

```
mco.yourdomain.com  →  <EXTERNAL-IP>
```

If you don't have DNS yet, you can test via `/etc/hosts`:

```
<EXTERNAL-IP>  mco.yourdomain.com
```

### 5. Open the portal

Navigate to `https://mco.yourdomain.com` in your browser.

> The portal uses a self-signed TLS certificate by default. Your browser will warn you — click "Advanced" → "Proceed". See [Replace the TLS certificate](#replace-the-tls-certificate) below to fix this.

Log in with the `adminEmail` and `adminPassword` you set in step 2.

---

## First Steps After Login

### Connect your VMware environment

1. Click **Settings** (gear icon, bottom-left)
2. Go to **Integrations → vCenter** and enter your vCenter hostname, username, and password
3. Repeat for SDDC Manager, NSX, and vROps if you have them
4. Click **Save** on each section

You don't need all four — vCenter alone is enough for basic analysis.

### Run your first analysis

1. Click **Analysis** (the home page)
2. Type a question: *"What is the overall health of our environment?"*
3. Click **Analyze**

The platform will collect live data from all configured VMware APIs, score your environment, and return a plain-English report in under a minute.

---

## Service Exposure Options

### Option A: LoadBalancer (default)

The `oauth2-proxy` service gets a LoadBalancer IP from your cloud or on-prem load balancer controller (MetalLB, NSX ALB, etc.).

```yaml
service:
  type: LoadBalancer
```

### Option B: NodePort

Exposes the portal on a static port on every node.

```yaml
service:
  type: NodePort
```

Access via `https://<any-node-ip>:<nodePort>`. Not recommended for production.

### Option C: Ingress

If you have an Ingress controller (nginx, Traefik):

```yaml
service:
  type: ClusterIP

ingress:
  enabled: true
  className: nginx
  host: mco.yourdomain.com
  tls: true

portal:
  url: https://mco.yourdomain.com
```

With cert-manager for automatic TLS:

```yaml
ingress:
  certManager: true
  certManagerIssuer: letsencrypt-prod
```

---

## Replace the TLS Certificate

The default self-signed cert causes browser warnings. Replace it with a real cert:

```bash
kubectl -n mco create secret tls mco-tls \
  --cert=fullchain.pem \
  --key=privkey.pem \
  --dry-run=client -o yaml | kubectl apply -f -
```

Then restart oauth2-proxy:

```bash
kubectl -n mco rollout restart deployment/oauth2-proxy
```

---

## Using Ollama (On-Prem AI, No Cloud Keys)

To run AI models locally:

```yaml
llm:
  provider: ollama
  ollamaUrl: "http://ollama:11434"

ollama:
  enabled: true
  model: llama3.2         # or mistral, phi3, etc.
  storageSize: 50Gi       # space for model weights
```

> Ollama requires at least 8 GB RAM per node to run llama3.2. Larger models need more.

---

## Enabling Active Directory Login

Configure AD so your team can log in with their domain credentials:

```yaml
activeDirectory:
  host: dc01.yourdomain.com
  domain: yourdomain.com
  bindUser: svc-mco@yourdomain.com
  bindPassword: your-service-account-password
```

AD users will appear on the Dex login page as a second login option.

---

## Scaling Down for Small Clusters

For a resource-constrained cluster (dev/lab), reduce replicas:

```yaml
replicas:
  apiGateway: 1
  orchestrator: 1
  scoringEngine: 1
  normalization: 1
  ui: 1

grafana:
  enabled: false

prometheus:
  enabled: false
```

---

## Upgrading

```bash
helm upgrade mco chart/ \
  --namespace mco \
  --values my-values.yaml \
  --wait --timeout 10m
```

---

## Uninstalling

```bash
helm uninstall mco --namespace mco
kubectl delete namespace mco
```

> This deletes all PVCs (database, backups, discovery data). Export your data first if needed.

---

## Troubleshooting

**Pod stuck in Pending:**
```bash
kubectl -n mco describe pod <pod-name>
# Usually: storage class not found, or resource limits too high for the node
```

**Portal shows a certificate error:**
Replace `mco-tls` with a real cert (see above) or add a `/etc/hosts` entry for testing.

**Login fails / redirects loop:**
Ensure `portal.url` in values.yaml exactly matches the URL you're using in the browser (including `https://` and no trailing slash).

**Analysis returns "no data":**
Verify vCenter credentials in Settings → Integrations. Check `kubectl -n mco logs deployment/collector-vcenter` for connection errors.

**Ollama model download takes too long:**
The init container pulls the model on first start. For llama3.2 (~2 GB) this takes 3-10 minutes on a fast connection. Check progress:
```bash
kubectl -n mco logs -l app=ollama -c pull-model -f
```

---

## Architecture Overview

```
Browser
  │ HTTPS 443
  ▼
oauth2-proxy  ← OIDC auth gate (Dex)
  │ HTTP
  ▼
ui (nginx)    ← React SPA
  │ /api/*
  ▼
api-gateway   ← 19 FastAPI routers
  │
  ├── orchestrator  ← coordinates full analysis pipeline
  ├── llm-gateway   ← calls Anthropic / OpenAI / Gemini / Ollama
  ├── scoring-engine ← deterministic 0-100 score
  ├── collector-*   ← pull data from VMware APIs
  ├── vks-broker    ← Kubernetes cluster visibility
  ├── discovery-engine ← nmap + Nuclei scanning
  └── config-store  ← credentials + settings
  
PostgreSQL (TimescaleDB) ← audit log, agent history, score history
Redis                    ← fleet cache, pub/sub alerts, pipeline dedup
```

All inter-service traffic stays inside the cluster. Only `oauth2-proxy` has an external endpoint.
