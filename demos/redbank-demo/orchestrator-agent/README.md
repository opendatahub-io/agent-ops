# RedBank Orchestrator Agent

Multi-agent orchestrator for the RedBank demo. Classifies user intent and routes queries to specialist agents via the A2A protocol.

## Architecture

```
User / Chat UI
    │
    ▼
Orchestrator (this agent)
    │
    ├── A2A ──▶ Knowledge Agent (B)
    │           • Document / policy queries (PGVector RAG)
    │           • Account data retrieval (PostgreSQL MCP, read-only, RLS-scoped)
    │
    └── A2A ──▶ Banking Operations Agent (C)
                • Account updates, transaction creation (PostgreSQL MCP, admin-only)
```

The LLM classifies each user message and calls the appropriate tool. Each tool is an A2A client call (`message/send` JSON-RPC) to a downstream agent. The orchestrator itself is also A2A-callable — it exposes `/.well-known/agent-card.json` and `POST /` via the `a2a-sdk`.

## Auth Token Propagation

The orchestrator extracts the `Authorization` header from incoming requests and forwards it through the A2A call chain. This enables:

- **Agent-level gating** — Banking Agent rejects non-admin tokens before executing write operations
- **Data scoping** — Knowledge Agent returns RLS-scoped results based on the user's JWT identity

## Configuration

All deployment configuration is managed via the top-level `.env` file. See the [project README](../README.md) for full setup instructions.

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | LLM API key |
| `LLM_BASE_URL` | Yes | LLM API endpoint (e.g. `https://vllm.example.com/v1`) |
| `LLM_MODEL` | Yes | LLM model identifier (e.g. `llama-scout-17b`) |
| `NAMESPACE` | No | Target K8s/OpenShift namespace (defaults to `redbank-demo`) |
| `PORT` | No | Server port (default: `8080` in container) |

In-cluster, peer agents are discovered automatically via kagenti `AgentCard` CRDs. Re-discovery runs every 15 seconds so new agents are picked up without restarting.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat/completions` | OpenAI-compatible chat completions (JSON + SSE) |
| `POST` | `/` | A2A JSON-RPC `message/send` (for inter-agent calls) |
| `GET` | `/.well-known/agent-card.json` | A2A agent card for discovery |
| `GET` | `/health` | Health check |

## Deployment (OpenShift)

Deployment is driven from the top-level Makefile:

```bash
# From the project root (redbank-demo-2/)
make deploy-orchestrator
```

This builds the container image in-cluster via `oc new-build`, then deploys via `helm upgrade --install` with the shared chart and `protocol.kagenti.io/a2a` label for peer discovery.

### Verify

```bash
# Health check
curl -s https://$(oc get route redbank-orchestrator -o jsonpath='{.spec.host}')/health

# Agent card
curl -s https://$(oc get route redbank-orchestrator -o jsonpath='{.spec.host}')/.well-known/agent-card.json

# Chat
curl -s https://$(oc get route redbank-orchestrator -o jsonpath='{.spec.host}')/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is my account balance?"}]}'
```

## Local Development

### Setup

```bash
cd orchestrator-agent
python3 -m venv .venv && source .venv/bin/activate
uv sync --python 3.12
cp .env.example .env   # edit with your LLM config
source .venv/bin/activate && set -a && source .env && set +a
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Testing with Mock Agents

The `examples/mock_agents.py` script starts two mock A2A agents locally for end-to-end testing:

```bash
# Terminal 1 — start mock Knowledge Agent (8001) + Banking Agent (8002)
uv run python examples/mock_agents.py

# Terminal 2 — start the orchestrator
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 3 — test
curl -s http://localhost:8000/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What is my account balance?"}]}'

curl -s http://localhost:8000/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Transfer 500 to account 12345"}]}'
```

### Run Tests

```bash
uv run --extra dev python -m pytest tests/
```
