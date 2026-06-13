# Contributing to MCO

Thank you for your interest in contributing.

## Getting Started

1. Fork the repo and clone your fork
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make changes and test locally (see below)
4. Open a pull request against `main`

## Project Structure

```
services/          # Python backend microservices (FastAPI)
  api-gateway/     # Main entry point — 19 routers
  orchestrator/    # Pipeline coordinator
  llm-gateway/     # AI provider abstraction
  scoring-engine/  # VCF health scoring (0-100)
  normalization/   # Raw data → normalized schema
  config-store/    # Persistent configuration + credentials
  collector-*/     # VMware API collectors (vCenter, SDDC, NSX, vROps, Logs)
  tools/           # Shared helper tools (nmap, nuclei wrappers)
  discovery-engine/ # Network discovery + vulnerability scanning
  vks-broker/      # Kubernetes cluster visibility
  powercli/        # PowerCLI script runner (PowerShell subprocess)
ui/                # React SPA (TypeScript, Tailwind, Vite)
chart/             # Helm chart for deployment
docs/              # Documentation
.github/workflows/ # CI — build/push images + Helm lint
```

## Local Development

### Backend service

```bash
cd services/api-gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### UI

```bash
cd ui
npm install
npm run dev          # Vite dev server on :5173
```

The UI expects the API at `http://localhost:8000` by default (configured in `ui/vite.config.ts`).

## Testing

```bash
# Run smoke tests against a live cluster
bash scripts/smoke-test.sh

# Lint the Helm chart
helm lint chart/
```

## Pull Request Guidelines

- One feature or fix per PR
- Include a short description of what changed and why
- If you're adding a new API endpoint, update `docs/pods.md`
- If you're changing auth or secrets handling, tag a maintainer for review

## Code Style

- Python: no strict formatter enforced, but follow existing style (no type annotations in function bodies, short docstrings)
- TypeScript/React: Prettier default config (run `npm run format` in `ui/`)
- No AI-generated commit messages — write what changed and why in plain English

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
