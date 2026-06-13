# MCO Platform — Software Bill of Materials (SBOM)

_Generated: 2026-06-13 · Serial: 3ac8b330-b754-4106-9ebe-06d8c7a3d592 · Format: CycloneDX-inspired Markdown_

All components are open-source unless noted. License column covers the declared package license.

---

## Runtime Infrastructure (Kubernetes / Container)

| Component | Version | License | Role |
|-----------|---------|---------|------|
| VMware Kubernetes Service (VKS) | 1.32.10+vmware.1-fips | Commercial | Cluster runtime |
| containerd | 1.7.29+vmware.2-fips | Apache 2.0 | Container runtime |
| Python | 3.12-slim (Debian bookworm) | PSF | Service base image |
| Node.js / nginx | nginx 1.27-alpine | BSD 2-clause | UI static serving |
| PostgreSQL (TimescaleDB) | 16 + TimescaleDB 2.x | Apache 2.0 / Timescale Community | Time-series DB |
| Redis | 7 | BSD 3-clause | Cache + pub/sub |
| Prometheus | latest | Apache 2.0 | Metrics scraping |
| Grafana | latest | AGPLv3 | Metrics dashboards |
| Ollama | latest | MIT | On-prem LLM server |
| Linkerd | 2.x | Apache 2.0 | mTLS service mesh |
| oauth2-proxy | 7.x | MIT | OIDC session proxy |
| Dex | 2.x | Apache 2.0 | OIDC identity provider |

---

## Python Backend — Shared Core

| Package | Version | License |
|---------|---------|---------|
| fastapi | 0.115.0 | MIT |
| uvicorn[standard] | 0.30.6 / 0.32.0 | BSD |
| pydantic | 2.9.2 | MIT |
| httpx | 0.27.x | BSD |
| python-dotenv | 1.0.1 | BSD |
| prometheus-fastapi-instrumentator | 7.0.0 | ISC |
| tenacity | 9.0.0 | Apache 2.0 |

## Python Backend — Service-Specific

| Package | Version | License | Used by |
|---------|---------|---------|---------|
| redis[asyncio] | 5.0.8 | MIT | api-gateway, vks-broker, discovery-engine |
| asyncpg | 0.29.0 | Apache 2.0 | api-gateway, config-store, scoring-engine |
| ldap3 | 2.9.1 | LGPL 3.0 | api-gateway, config-store (AD integration) |
| cryptography | ≥42 / 43.0.1 | Apache 2.0 + BSD | config-store, discovery-engine, vks-broker |
| pyyaml | 6.0.2 | MIT | vks-broker |
| anthropic | 0.40.0 | MIT | llm-gateway |
| openai | ≥1.40.0 | MIT | llm-gateway |
| google-generativeai | ≥0.8.0 | Apache 2.0 | llm-gateway |
| rank-bm25 | 0.2.2 | Apache 2.0 | llm-gateway (BM25 RAG over VCF docs) |
| json-repair | 0.30.3 | MIT | llm-gateway |
| pypdf | 6.12.1 | BSD | llm-gateway (VCF doc ingestion) |
| python-multipart | 0.0.9 | Apache 2.0 | api-gateway |
| urllib3 | 2.2.3 | MIT | collectors |
| aiosqlite | latest | MIT | discovery-engine |
| asyncssh | latest | LGPL 2.1 | discovery-engine |
| cachetools | 5.5.0 | MIT | tools |
| requests | latest | Apache 2.0 | token-refresher |
| kubernetes | latest | Apache 2.0 | token-refresher |

---

## Frontend (React SPA)

| Package | Version | License |
|---------|---------|---------|
| react | ^18.3.1 | MIT |
| react-dom | ^18.3.1 | MIT |
| typescript | ^5.5.3 | Apache 2.0 |
| vite | ^5.4.0 | MIT |
| tailwindcss | ^3.4.7 | MIT |
| lucide-react | ^0.417.0 | ISC |
| recharts | ^2.12.7 | MIT |
| react-markdown | ^10.1.0 | MIT |
| remark-gfm | ^4.0.1 | MIT |
| rehype-sanitize | ^6.0.0 | MIT |
| @fontsource-variable/inter | ^5.2.8 | OFL-1.1 |
| @fontsource/jetbrains-mono | ^5.2.8 | OFL-1.1 |
| postcss | ^8.4.41 | MIT |
| autoprefixer | ^10.4.19 | MIT |
| @vitejs/plugin-react | ^4.3.1 | MIT |
| @types/react | ^18.3.3 | MIT |
| @types/react-dom | ^18.3.0 | MIT |

---

## External LLM APIs (Cloud, optional)

| Service | Provider | Data sent |
|---------|----------|-----------|
| Claude (claude-sonnet-4-6 / claude-opus-4-8) | Anthropic | Analysis prompts + scored environment data |
| GPT-4o / GPT-4-turbo | OpenAI | Same (alternate provider) |
| Gemini 1.5 Pro | Google | Same (alternate provider) |

All cloud LLM calls are opt-in — the platform runs fully on-prem with Ollama when no API key is configured.

---

## VMware / Broadcom APIs Consumed (no redistribution)

| API | Protocol | Auth |
|-----|----------|------|
| vCenter REST API | HTTPS/REST | Session token (auto-refreshed) |
| vROps / Aria Operations REST API | HTTPS/REST | Bearer token |
| SDDC Manager REST API | HTTPS/REST | Bearer token |
| NSX Manager REST API | HTTPS/REST | Bearer token |
| Log Insight / Aria Logs REST API | HTTPS/REST | Bearer token |

---

## Security Notes

- No component has a known critical CVE as of 2026-06-13 (manual review)
- `cryptography` ≥42 avoids OpenSSL 3 padding-oracle CVEs backported to 41.x
- `pydantic` 2.x — V1 compatibility layer not installed (smaller attack surface)
- All backend images: `python:3.12-slim` (Debian bookworm) — minimal attack surface, no shell tools
- Frontend: `rehype-sanitize` prevents XSS in AI-generated markdown rendered in the UI
- Network isolation: only `oauth2-proxy` (LB 10.50.78.27, ports 80/443) and `dex` (LB 10.50.78.28, port 5556) are externally reachable; all backend services are ClusterIP with Linkerd mTLS

---

_Machine-readable CycloneDX JSON: `docs/sbom.cdx.json` (regenerate with `cyclonedx-py` or `syft`)_
