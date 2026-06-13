# MCO Platform — Deployment Guide

_Version: MCO AI-Ops · Last updated: 2026-06-10_

---

## Overview

MCO (Managed Cloud Operations) is a containerized AI operations platform for VMware Cloud Foundation.
It runs as a set of microservices in a Tanzu Kubernetes (VKS) namespace and connects to your VCF
stack over the network.

**Estimated deployment time: 45–60 minutes** (excluding TLS cert issuance).

---

## Prerequisites

### Infrastructure

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| VKS cluster | VCF 9.x | Tanzu Kubernetes workload cluster; supervisor API reachable |
| kubectl + kubeconfig | — | Workload cluster config at `~/.kube/vcf-ai-ops-cluster.kubeconfig` |
| Harbor (or any OCI registry) | 2.x | Images pushed to `ghcr.io/eliranbarhum/ai-ops/<service>:latest` |
| Storage class | — | Block-capable class that supports `RWO`; set `STORAGE_CLASS` below |
| TLS certificate | — | For the portal hostname (wildcard or specific SAN); PEM format |
| DNS A record | — | `mco.<your-domain>` → oauth2-proxy LoadBalancer IP (obtained during deploy) |
| Python 3.11+ | local | Only needed to run `scripts/deploy-auth.sh` |
| `htpasswd` | local | Part of `apache2-utils` or `httpd-tools`; used by `deploy-auth.sh` |

### VCF credentials needed

Gather these before starting — they go into Settings after first boot:

- **vCenter**: host, admin user, password
- **vROps (VCF Operations)**: host, admin user, password
- **SDDC Manager**: host, admin user, password
- **Active Directory** (optional): LDAP host, bind user, password, base DN

### API keys

- **Anthropic API key** (`sk-ant-…`) — for LLM analysis. Claude claude-sonnet-4-6 is used by default.

---

## Step 1 — Clone the repository

```bash
git clone https://github.com/<your-org>/vcf-ai-ops.git
cd vcf-ai-ops
```

---

## Step 2 — Fill in ConfigMap

Edit `k8s/configmap.yaml`. The required fields are:

```yaml
VSPHERE_SUPERVISOR_HOST: "10.x.x.x"          # Supervisor IP
VSPHERE_SUPERVISOR_USERNAME: "administrator@vsphere.local"
ALLOWED_ORIGINS: "https://mco.<your-domain>"  # Must match your DNS record exactly
VCF_TARGET_VERSION: "9.1"                     # Or "9.0"
```

All service URLs (`ORCHESTRATOR_URL`, `LLM_GATEWAY_URL`, etc.) point to in-cluster ClusterIP
services and do **not** need to change.

---

## Step 3 — Create secrets

```bash
cp deploy/manifests/02-secret.yaml.example deploy/manifests/02-secret.yaml
```

Fill in the base64-encoded values. Use `echo -n "value" | base64` to encode each one.

**Required fields:**
- `ANTHROPIC_API_KEY` — your Claude API key
- `ENCRYPTION_KEY` — generate once and keep safe:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | base64
  ```

VCF credentials (`VCENTER_HOST`, `VROPS_HOST`, etc.) can be left blank here — they are stored
encrypted in config-store and set via the Settings UI after first boot.

**Never commit `02-secret.yaml` to git.**

---

## Step 4 — Install TLS certificate

The portal certificate is stored as a K8s secret named `mco-tls` in the `vcf-ai-ops` namespace.

```bash
KUBECONFIG=~/.kube/vcf-ai-ops-cluster.kubeconfig
NS=vcf-ai-ops

kubectl create namespace $NS --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret tls mco-tls \
  --cert=<path-to-cert.pem> \
  --key=<path-to-key.pem> \
  -n $NS \
  --dry-run=client -o yaml | kubectl apply -f -
```

The certificate must cover the `mco.<your-domain>` hostname.

---

## Step 5 — Build and push images

```bash
REGISTRY="ghcr.io/eliranbarhum/ai-ops"

# Build all services
docker compose build

# Push to your registry
for svc in api-gateway llm-gateway orchestrator tools config-store scoring-engine \
            collector-vcenter collector-vrops collector-logs collector-sddc \
            discovery-engine powercli normalization ui; do
  docker tag vcf-ai-ops-${svc}:latest ${REGISTRY}/mco-${svc}:latest
  docker push ${REGISTRY}/mco-${svc}:latest
done
```

If you use a private registry without public access, create an `imagePullSecret` and add it to
each Deployment spec, or configure the VKS cluster with registry credentials.

---

## Step 6 — Apply Kubernetes manifests

```bash
KC="kubectl --kubeconfig ~/.kube/vcf-ai-ops-cluster.kubeconfig -n vcf-ai-ops"

# Ordered apply
$KC apply -f k8s/namespace.yaml
$KC apply -f k8s/configmap.yaml
$KC apply -f deploy/manifests/02-secret.yaml
$KC apply -f k8s/              # PVCs, Redis, PostgreSQL, TimescaleDB
$KC apply -f deploy/manifests/ # Core deployments + services

# RBAC
$KC apply -f k8s/linkerd-authz.yaml
$KC apply -f k8s/rbac-agent-k8s-reader.yaml
$KC apply -f k8s/rbac-k8s-visibility.yaml  # K8s Cluster Visibility page

# Observability
$KC apply -f k8s/prometheus.yaml
$KC apply -f k8s/grafana.yaml

# Backup
$KC apply -f k8s/cronjob-backup.yaml
$KC apply -f k8s/cronjob-analysis.yaml
```

Wait for all pods to reach `Running` state:

```bash
$KC get pods -w
```

Expected pod count: ~34 pods, all `2/2` (Linkerd sidecar injected).

---

## Step 7 — Deploy authentication

```bash
./scripts/deploy-auth.sh
```

This script:
1. Deploys Dex (OIDC provider) with a local admin account (`admin@local`)
2. Obtains the oauth2-proxy LoadBalancer IP
3. Deploys oauth2-proxy with TLS on port 443 using `mco-tls`
4. Configures the port-80 → HTTPS redirect
5. Converts `ui` and `api-gateway` to ClusterIP (removes direct external access)

**After the script completes**, note the oauth2-proxy LoadBalancer IP and create your DNS record:

```
mco.<your-domain>  A  <LoadBalancer-IP>
```

The local admin credentials are printed by the script. The password is also set in the script itself
if you need to retrieve it later — check `scripts/deploy-auth.sh`.

---

## Step 8 — Verify with smoke tests

```bash
MCO_HOST="mco.<your-domain>" make smoke
```

All 31 checks should pass. Common failures and their fixes:

| Failure | Fix |
|---------|-----|
| `overall=degraded` in health check | One or more collectors can't reach VCF — add credentials in Settings first |
| HTTPS cert warning | DNS not propagated yet, or wrong hostname in `ALLOWED_ORIGINS` |
| `401` on all endpoints | oauth2-proxy not yet live; wait 60s and retry |
| `423` on POST endpoints | No maintenance window configured — add one in Settings → Maintenance |

---

## First-boot checklist

Open `https://mco.<your-domain>` and log in as `admin@local`.

1. **Settings → vCenter** — add host, user, password; click Test Connection
2. **Settings → VCF Operations (vROps)** — add host, user, password
3. **Settings → SDDC Manager** — add host, user, password
4. **Settings → Active Directory** _(optional)_ — LDAP host, bind DN, base DN, password
5. **Settings → Notifications** _(optional)_ — Telegram bot token + allowed user IDs
6. **Maintenance → New Window** — create at least one maintenance window to enable write operations
7. Run your first analysis from the Fleet page

---

## Operational runbook

### Restart a stuck service

```bash
KC="kubectl --kubeconfig ~/.kube/vcf-ai-ops-cluster.kubeconfig -n vcf-ai-ops"
$KC rollout restart deployment/<service-name>
# e.g.: $KC rollout restart deployment/api-gateway
```

### Reset a stuck Ollama model pull

If the local LLM model is stuck loading (pod restart loop or very slow):

```bash
$KC delete pod -l app=vllm-server
# Pod restarts and re-pulls; progress visible in: $KC logs -l app=vllm-server -f
```

### Recover from PostgreSQL/TimescaleDB failure

```bash
# Check if the PVC is healthy
$KC get pvc timescaledb-pvc

# Force restart
$KC delete pod -l app=timescaledb

# If data is corrupted, restore from backup (see Backup section below)
```

### Restore from backup

Backups run daily at 02:00 UTC to the `mco-backup` PVC. Files:
- `pg-YYYYMMDD.sql.gz` — full pg_dump of the `mco` database
- `config-YYYYMMDD.tar.gz` — config-store PVC (Fernet-encrypted)

To restore PostgreSQL:

```bash
# Spin up a temp pod with access to both the backup PVC and the DB
$KC run restore --rm -it --image=timescale/timescaledb:latest-pg16 \
  --overrides='{"spec":{"volumes":[{"name":"backup","persistentVolumeClaim":{"claimName":"mco-backup"}}],"containers":[{"name":"restore","image":"timescale/timescaledb:latest-pg16","volumeMounts":[{"name":"backup","mountPath":"/backup"}]}]}}' \
  -- bash

# Inside the pod:
# psql -h timescaledb -U mco -d mco -c "SELECT timescaledb_pre_restore();"
# gunzip -c /backup/pg-YYYYMMDD.sql.gz | psql -h timescaledb -U mco -d mco
# psql -h timescaledb -U mco -d mco -c "SELECT timescaledb_post_restore();"
```

### Rotate the Fernet encryption key

1. Generate a new key: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Update `ENCRYPTION_KEY` in `deploy/manifests/02-secret.yaml` (base64-encode it)
3. Apply: `kubectl apply -f deploy/manifests/02-secret.yaml`
4. Re-encrypt config-store data: `kubectl rollout restart deployment/config-store`
5. **Note:** existing encrypted blobs will fail to decrypt until config-store re-encrypts them on next write.
   Use the Settings page to re-save each credential.

### Update to a new image

```bash
# Rebuild and push the changed service
docker build -t ${REGISTRY}/mco-api-gateway:latest services/api-gateway/
docker push ${REGISTRY}/mco-api-gateway:latest

# Roll out
kubectl rollout restart deployment/api-gateway -n vcf-ai-ops \
  --kubeconfig ~/.kube/vcf-ai-ops-cluster.kubeconfig
```

---

## Grafana

Grafana is available at `http://<grafana-loadbalancer-ip>:3001` (not through the portal).

Get the IP:

```bash
kubectl get svc grafana -n vcf-ai-ops --kubeconfig ~/.kube/vcf-ai-ops-cluster.kubeconfig
```

Default credentials: `admin` / `MCOGrafana2026!` — **change on first login**.

Pre-loaded dashboards:
- **MCO Platform** — pipeline run rate, LLM latency, fleet cache hit/miss, pod CPU/memory
- **Vuln Audit** — active scans, finding severity distribution, audit event rate

---

## Network exposure summary

| Service | External IP | Port | Notes |
|---------|-------------|------|-------|
| oauth2-proxy | LoadBalancer | 443 (→ 8443) | Portal entry point — TLS terminates here |
| oauth2-proxy | LoadBalancer | 80 (→ 4180) | Redirects to HTTPS |
| Dex | LoadBalancer | 5556 | OIDC issuer — plain HTTP (residual; move to HTTPS is a follow-up) |
| Grafana | LoadBalancer | 3001 | Observability — not behind auth |
| ui | ClusterIP | 80 | Internal only — direct access would bypass auth |
| api-gateway | ClusterIP | 8000 | Internal only — same reason |

---

## Security notes

- `ui` and `api-gateway` are deliberately **ClusterIP** — exposing them as LoadBalancer would allow
  auth bypass via a forged `X-Forwarded-User` header (verified pre-fix).
- All mutating endpoints check for an active maintenance window (`423 Locked` when outside one).
- `kubectl run` commands are allowlist-controlled: only `get`, `describe`, `logs`, `apply`,
  `create`, `delete`, `rollout`, `scale`, `patch`, `top`, `events`, `wait`, `version`, `explain`,
  `diff` are permitted.
- PowerCLI scripts are limited to 3 concurrent subprocesses and cannot contain destructive verbs
  without an explicit `allow_writes=true` flag.
- The local admin password lives in `scripts/deploy-auth.sh`. Treat this file as a secret.
