# RedBank Demo — Kagenti Edition

PostgreSQL database, MCP server, and A2A agents for the RedBank multi-agent banking demo, adapted for Kagenti deployment with Row-Level Security (RLS).

Part of RHAISTRAT-1459 / RHAIENG-4555 (Epic) / RHAIENG-4556 (MCP Server) / RHAIENG-4558 (Knowledge Agent) / RHAIENG-4559 (Banking Agent).

### Tested with Kagenti v0.6.0

| Component | Version |
|-----------|---------|
| kagenti (platform chart) | `appVersion: 0.6.0` |
| kagenti-operator | `0.2.0-rc.6` |
| All OAuth secret images | `v0.6.0` |
| SPIFFE IdP setup | `v0.6.0` |

## Table of Contents

- [Directory Layout](#directory-layout)
- [How It Works](#how-it-works)
- [RAG Pipeline (LangChain + PGVector)](#rag-pipeline-langchain--pgvector)
- [Deployment](#deployment)
- [Using the Playground](#using-the-playground)
- [Testing & Configuration Reference](TESTING.md)

## Directory Layout

```
redbank-demo-2/
├── .env.example                  Consolidated deployment config template
├── Makefile                      Single Makefile for all deployment targets
├── postgres-db/                  PostgreSQL schema, seed data, RLS policies
│   ├── init.sql                  Schema + RLS + seed data
│   ├── init-db.sh                Startup init script
│   ├── postgres.yaml             Secret + Deployment + Service
│   └── kustomization.yaml
├── langchain-pgvector/           LangChain + PGVector RAG pipeline
│   ├── tests/                    Schema + RLS tests (testcontainers)
│   ├── pipeline/                 KFP ingestion pipeline
│   ├── notebook/                 Query notebook (admin vs user RLS demo)
│   └── requirements.txt
├── mcp-server/                   FastMCP server with auth-aware tools
│   ├── redbank-mcp/
│   │   ├── mcp_server.py         Tool definitions + JWT auth
│   │   ├── database_manager.py   Connection pool + RLS context
│   │   └── logger.py
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── mcp-server.yaml           Deployment + Service
│   ├── agentruntime.yaml         AgentRuntime CR (type: tool)
│   └── deploy.sh                 OpenShift build + deploy
├── banking-agent/                A2A Banking Operations Agent (Agent C — admin CRUD)
│   ├── src/banking_agent/
│   │   ├── __main__.py           A2A server startup, agent card, MLflow init
│   │   ├── agent.py              LangGraph ReAct agent + MCP client setup
│   │   └── agent_executor.py     A2A <-> LangGraph bridge with token propagation
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── banking-agent.yaml        Deployment + Service
│   ├── agentruntime.yaml         AgentRuntime CR (type: agent)
│   └── deploy.sh                 OpenShift build + deploy
├── knowledge-agent/              A2A Knowledge Agent (Agent B — read-only RAG + data)
│   ├── src/knowledge_agent/
│   │   ├── __main__.py           A2A server startup, agent card, MLflow init
│   │   ├── agent.py              LangGraph ReAct agent + allow-list filter
│   │   └── agent_executor.py     A2A <-> LangGraph bridge with token propagation
│   ├── tests/                    Unit tests (mocked, no infra needed)
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── knowledge-agent.yaml      Deployment + Service
│   ├── agentruntime.yaml         AgentRuntime CR (type: agent)
│   └── deploy.sh                 OpenShift build + deploy
├── orchestrator-agent/           A2A Orchestrator Agent (Agent A — intent routing)
│   ├── src/redbank_orchestrator/
│   │   ├── server.py             Starlette app, A2A + /chat/completions + re-discovery
│   │   ├── agent.py              LangGraph agent builder from discovered peers
│   │   ├── discovery.py          Peer discovery via K8s AgentCard CRDs
│   │   ├── k8s_discovery.py      Kubernetes API client for AgentCard lookup
│   │   ├── a2a_client.py         A2A message sender with token forwarding
│   │   ├── tools.py              Dynamic tool creation from peer agent cards
│   │   └── tracing.py            MLflow autolog configuration
│   ├── charts/agent/             Helm chart for deployment
│   ├── examples/mock_agents.py   Mock agents for local testing
│   ├── tests/                    Unit tests
│   ├── pyproject.toml + uv.lock
│   ├── Dockerfile
│   └── deploy.sh                 OpenShift build + Helm deploy
├── playground/                   Standalone chat UI for the orchestrator
│   ├── playground/templates/     HTML chat interface
│   ├── server.py                 Starlette proxy (auth, chat, health)
│   ├── charts/agent/             Helm chart for deployment
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── deploy.sh                 OpenShift build + Helm deploy
├── scripts/
│   ├── setup-keycloak.sh         Provision Keycloak realm, client, users, audience mapper
│   ├── test-knowledge-agent.sh   Manual A2A agent test with Keycloak JWTs
│   ├── test-search-knowledge.sh  Manual MCP tool test with Keycloak JWTs
│   └── cleanup.sh                Tear down deployed workloads + Keycloak realm
├── tests/
│   └── test_mcp_rls.py           MCP-level integration tests (pytest)
└── README.md
```

## How It Works

### Overview

The MCP server is a [FastMCP](https://github.com/jlowin/fastmcp) application that exposes banking data tools over the MCP Streamable HTTP transport. It sits between Kagenti agents and a PostgreSQL database, enforcing access control at two levels:

1. **Application-level gating** — Write tools (`update_account`, `create_transaction`) are decorated with `@admin_only` and reject non-admin callers before any SQL runs.
2. **Database-level Row-Level Security (RLS)** — PostgreSQL policies filter query results based on session variables, so even if application logic has a bug, users can only see their own data.

### Request Flow

```
Agent (A2A/MCP client)
  │
  │  Authorization: Bearer <JWT>
  ▼
┌──────────────────────────────────────────────┐
│  AuthBridge Sidecar (Envoy + go-processor)   │
│                                              │
│  1. Validate JWT (signature, exp, issuer)    │
│  2. Token exchange (RFC 8693) for tool aud   │
│  3. Forward with exchanged Bearer token      │
└──────────────────┬───────────────────────────┘
                   │
                   │  Authorization: Bearer <exchanged-JWT>
                   ▼
┌──────────────────────────────────────────────┐
│  FastMCP HTTP Server (:8000/mcp)             │
│                                              │
│  1. Verify JWT (JWKS) or decode (trusted)    │
│  2. Extract email + role from claims         │
│  3. Check @admin_only (write tools)          │
│  4. Open pooled DB connection                │
│  5. SET app.current_role, app.current_email  │
│  6. Execute query (RLS filters rows)         │
│  7. Return structured result                 │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  PostgreSQL 16                               │
│                                              │
│  RLS policies on: customers, statements,     │
│  transactions                                │
│                                              │
│  Admin: sees all rows, can INSERT/UPDATE     │
│  User:  sees only own customer_id (SELECT)   │
└──────────────────────────────────────────────┘
```

### AuthBridge Integration

In a Kagenti deployment, the AuthBridge sidecar (Envoy + go-processor) handles JWT validation and RFC 8693 token exchange automatically. The flow is:

1. Caller authenticates with Keycloak and receives a JWT
2. Caller sends the request with `Authorization: Bearer <JWT>` to the MCP server
3. The AuthBridge Envoy sidecar intercepts the request, validates the JWT (signature, expiration, issuer) via JWKS, and exchanges the token for an audience-scoped token targeting this tool
4. The exchanged token reaches the MCP server container on the `Authorization` header

The MCP server operates in two modes:

**AuthBridge trusted mode** (`JWT_VERIFY=false`, default) — The sidecar has already validated the token. The server decodes the JWT without signature verification to extract identity claims. This is the standard Kagenti deployment model.

**Standalone mode** (`JWT_VERIFY=true`) — No sidecar present. The server fetches signing keys from `JWKS_URL` (Keycloak JWKS endpoint) and verifies the JWT itself. Use for dev clusters without Kagenti or as defense-in-depth.

Identity is extracted from Keycloak JWT claims:
- **email**: `claims.email` → `claims.preferred_username` → `claims.sub` (fallback chain)
- **role**: `"admin"` if the `ADMIN_ROLE_CLAIM` value (default `"admin"`) appears in `realm_access.roles`, `resource_access.account.roles`, or `scope`

When no Bearer token is present, the server falls back to `DEFAULT_ROLE` and `DEFAULT_EMAIL` environment variables. In production with AuthBridge, unauthenticated requests are rejected by the sidecar before they reach the MCP server.

### Row-Level Security

RLS is enabled and forced (`FORCE ROW LEVEL SECURITY`) on `customers`, `statements`, `transactions`, and `embeddings`. The table owner (`$POSTGRESQL_USER`) is the same role the MCP server and RAG pipeline connect as, so `FORCE` ensures policies apply even to the owner. All tables use the same session-variable RLS pattern via `app.current_role`.

Before each query, the `@authenticated` decorator opens a connection from the pool and sets two session variables inside a transaction:

```sql
SELECT set_config('app.current_role', 'admin', true);
SELECT set_config('app.current_user_email', 'jane@redbank.demo', true);
```

The `true` parameter scopes these to the current transaction, so they're automatically cleared when the connection returns to the pool.

RLS policies then filter based on these variables:
- **Admin policies** (`FOR ALL`): allow full read/write when `app.current_role = 'admin'`
- **User policies** (`FOR SELECT`): restrict to rows matching the `customer_id` mapped in the `user_accounts` table for the current email

### MCP Tools

**Read tools** (all roles):
| Tool | Description |
|------|-------------|
| `get_customer` | Look up a customer by email or phone |
| `get_customer_transactions` | List transactions with optional date range filter |
| `get_account_summary` | Customer info + statement count + latest balance |
| `search_knowledge` | Semantic similarity search across role-scoped document collections |

**Write tools** (admin only):
| Tool | Description |
|------|-------------|
| `update_account` | Update customer phone, address, or account type |
| `create_transaction` | Insert a new transaction on the latest statement |

The `search_knowledge` tool uses `PGVectorStore` (from `langchain-postgres`) to query the `embeddings` table. It selects the admin or user store based on the caller's JWT role, so RLS scoping is enforced automatically. The embedding model (`nomic-ai/nomic-embed-text-v1.5`) is baked into the MCP server container image.

### Security Model

| Role | Read access | Write access |
|------|-------------|--------------|
| `user` | Own customer record, statements, transactions only (RLS) | None (rejected by `@admin_only`) |
| `admin` | All records | `update_account`, `create_transaction` |

### Demo Users

| Keycloak identity | Role | Customer record |
|-------------------|------|-----------------|
| `john@redbank.demo` | user | John Doe (customer_id 5) |
| `jane@redbank.demo` | admin | All customers (no customer_id binding) |

Seed data includes 5 customers (Alice, Bob, Carol, David, John), 13 statements, and 27 transactions.

### Kagenti Integration

Each workload is enrolled into the Kagenti platform via an `AgentRuntime` custom resource (`agent.kagenti.dev/v1alpha1`). The `AgentRuntime` references the Deployment via `targetRef` — the operator then manages `kagenti.io/type` labels, sets a `kagenti.io/config-hash` annotation for rollout coordination, and enables AuthBridge sidecar injection at Pod admission.

| Workload | AgentRuntime | `spec.type` | Protocol label |
|----------|-------------|-------------|----------------|
| `redbank-mcp-server` | `redbank-mcp-server-runtime` | `tool` | `protocol.kagenti.io/mcp: "true"` (Service) |
| `redbank-banking-agent` | `redbank-banking-agent-runtime` | `agent` | `protocol.kagenti.io/a2a: ""` (Deployment + Service) |
| `redbank-knowledge-agent` | `redbank-knowledge-agent-runtime` | `agent` | `protocol.kagenti.io/a2a: ""` (Deployment + Service) |
| `redbank-orchestrator` | via Helm chart `agentruntime.yaml` | `agent` | `protocol.kagenti.io/a2a: ""` (Deployment) |

The `kagenti.io/type` label on Deployments is managed by the operator — do not set it manually. Protocol labels on Services (`protocol.kagenti.io/a2a`, `protocol.kagenti.io/mcp`) remain in the Service manifests since they drive AgentCard sync and tool discovery independently.

### Banking Operations Agent (Agent C)

The Banking Operations Agent is an A2A service built with LangGraph that provides admin-level CRUD access to the RedBank customer database. It connects to the MCP server via `MultiServerMCPClient` from `langchain-mcp-adapters`.

**Architecture:**
- **Protocol**: A2A (Agent-to-Agent) — exposes `/.well-known/agent-card.json` for Kagenti discovery
- **Agent framework**: LangGraph `create_react_agent` with a system prompt for banking operations
- **MCP client**: `MultiServerMCPClient` connected to the PostgreSQL MCP server over HTTP
- **LLM**: Configurable — vLLM (default) or OpenAI via `ChatOpenAI` with `base_url` override
- **Observability**: MLflow LangChain autolog (`mlflow.langchain.autolog()`)
- **Auth**: Trusts AuthBridge sidecar for Tier 1 admin gating. Propagates the incoming Bearer JWT to the MCP server so RLS policies apply.

**Token flow:**
1. Caller sends A2A request with `Authorization: Bearer <JWT>`
2. AuthBridge sidecar validates the token and rejects non-admin users (Tier 1)
3. Agent extracts the Bearer token from the incoming request
4. Agent passes the token as a header to `MultiServerMCPClient`
5. MCP server applies RLS based on the JWT claims (Tier 2)

### Knowledge Agent (Agent B)

The Knowledge Agent is a read-only A2A service built with LangGraph that provides semantic document search (RAG) and customer data retrieval. It routes queries between the `search_knowledge` tool for policy/FAQ questions and the customer data tools for account lookups.

**Architecture:**
- **Protocol**: A2A — exposes `/.well-known/agent-card.json` on port 8002
- **Agent framework**: LangGraph `create_react_agent` with routing guidance in the system prompt
- **MCP client**: `MultiServerMCPClient` connected to the PostgreSQL MCP server over HTTP
- **Tool allow-list**: Only `get_customer`, `get_customer_transactions`, `get_account_summary`, and `search_knowledge` — write tools are filtered out so they cannot be invoked even if the LLM attempts to call them
- **LLM**: Configurable via `LLM_BASE_URL` and `LLM_MODEL`
- **Observability**: MLflow LangChain autolog

### Orchestrator Agent (Agent A)

The Orchestrator Agent is the entry point for user interactions. It classifies user intent and routes queries to specialist agents (Knowledge or Banking) via A2A protocol. It discovers peers dynamically via Kubernetes AgentCard CRDs and rebuilds its routing graph every 15 seconds when peers change.

**Architecture:**
- **Protocol**: A2A + OpenAI-compatible `/chat/completions` HTTP endpoint (with SSE streaming)
- **Agent framework**: LangGraph `create_agent` with dynamic tools built from discovered peer agent cards
- **Discovery**: Queries `AgentCard` CRDs (`agent.kagenti.dev/v1alpha1`) with `protocol.kagenti.io/a2a` labels — no manual configuration needed
- **LLM**: Configurable via `LLM_BASE_URL`, `LLM_MODEL`, `OPENAI_API_KEY`
- **Observability**: MLflow LangChain autolog (optional via `MLFLOW_TRACKING_URI`)
- **State**: Module-level `MemorySaver` checkpointer preserves conversation context across graph rebuilds
- **Deployment**: Helm chart (`charts/agent/`) with `values.yaml` for configuration

### Playground UI

The Playground is a standalone Starlette web app that provides a chat interface for the orchestrator. It handles Keycloak OIDC authentication directly and proxies all `/chat/completions` requests to the orchestrator backend.

**Features:**
- HTML chat interface served at `/`
- Keycloak OIDC auth (`/auth/config`, `/auth/token`) — server-side proxy avoids CORS issues
- Proxies `/chat/completions` and `/health` to the orchestrator (supports SSE streaming)
- Configurable via `ORCHESTRATOR_URL`, `KEYCLOAK_*` env vars

## RAG Pipeline (LangChain + PGVector)

A document ingestion pipeline using LangChain + PGVector for retrieval-augmented generation (RAG) with role-scoped access. Admin documents and user documents are ingested into separate collections in the same `embeddings` table, and PostgreSQL RLS ensures each role sees only its authorized documents.

This reuses the **existing PostgreSQL instance** deployed via `postgres-db/`. The pgvector extension, `embeddings` table, and role-based RLS policies are all defined in `postgres-db/init.sql`.

### Embedding Model

Uses `nomic-ai/nomic-embed-text-v1.5` via **sentence-transformers** (`langchain-huggingface`). Produces 768-dimensional vectors and runs locally — no external embedding API endpoint needed.

### Document Source

6 RedBank PDF documents hosted on GitHub, fetched by the pipeline at runtime:

- **Admin** (`admin/`): `redbank_compliance_procedures.pdf`, `redbank_transaction_operations.pdf`, `redbank_user_management.pdf`
- **User** (`user/`): `redbank_account_selfservice.pdf`, `redbank_password_and_security.pdf`, `redbank_payments_and_transfers.pdf`

### Pipeline

`langchain-pgvector/pipeline/pgvector_rag_pipeline.py` is a KFP pipeline that:

1. Downloads PDFs from GitHub via `base_url` + `filenames`
2. Loads and chunks documents with `RecursiveCharacterTextSplitter`
3. Embeds with `HuggingFaceEmbeddings` (nomic-embed-text)
4. Stores in PGVector via `PGVectorStore` with collection-scoped access

Admin and user document sets are ingested in parallel as separate pipeline tasks.

Compile the pipeline: `make compile-pipeline`

## Deployment

All deployment is driven through a single top-level `Makefile` and `.env` file. There are no individual Makefiles in subdirectories — each component has a `deploy.sh` script called by the Makefile.

### Prerequisites

- OpenShift cluster with `oc` CLI authenticated
- OpenShift AI > 3.4 installed
- Keycloak deployed (the setup script provisions a realm and demo users)
- `helm` CLI installed (for orchestrator and playground)
- An OpenAI-compatible LLM endpoint (e.g. vLLM)

### Quick Start

```bash
# 1. Create .env from template
make init

# 2. Edit .env with your configuration (see below)
vi .env

# 3. Deploy everything (Keycloak setup + DB + MCP + agents + playground)
make deploy

# 4. Follow the steps outlined in this doc to setup AI Pipelines in the redbank-demo namespace
# https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.4/html/working_with_ai_pipelines/managing-ai-pipelines_ai-pipelines#configuring-a-pipeline-server_ai-pipelines

# 5. Ingest documents into PGVector (compile + run KFP pipeline)
make compile-pipeline
# Upload and run pgvector_rag_pipeline.yaml in OpenShift AI: AI Pipelines
# Create a pipeline run to populate the knowledge agent's database with documentation

# 6. Verify
oc get pods
oc get agentruntimes
oc get agentcards
```

### Environment Variables (.env)

All configuration lives in a single `.env` file at the project root. Run `make init` to create it from `.env.example`.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NAMESPACE` | yes | `redbank-demo` | OpenShift namespace for all workloads |
| `OPENAI_API_KEY` | yes | | API key for the LLM endpoint (vLLM or OpenAI) |
| `LLM_BASE_URL` | yes | | LLM base URL (e.g. `https://vllm.example.com/v1`) |
| `LLM_MODEL` | yes | | Model name (e.g. `llama-scout-17b`) |
| `KEYCLOAK_ADMIN` | yes | `admin` | Keycloak admin username (for setup and cleanup) |
| `KEYCLOAK_PASSWORD` | yes | | Keycloak admin password |
| `KEYCLOAK_HOST` | no | auto-detected | Keycloak hostname for MCP server JWKS URL |
| `PGVECTOR_USER` | no | `app` | PGVector database user |
| `PGVECTOR_PASSWORD` | no | `app` | PGVector database password |
| `ORCHESTRATOR_URL` | no | auto-derived | Playground → orchestrator URL (derived from NAMESPACE) |
| `KEYCLOAK_URL` | no | | Playground Keycloak OIDC base URL |
| `KEYCLOAK_REALM` | no | | Playground Keycloak realm |
| `KEYCLOAK_CLIENT_ID` | no | | Playground Keycloak client ID |
| `MLFLOW_TRACKING_URI` | no | auto-detected | MLflow tracking endpoint |

### Makefile Targets

| Target | Description |
|--------|-------------|
| `make help` | Show all available targets |
| `make init` | Create `.env` from `.env.example` |
| `make deploy` | Deploy everything (Keycloak + DB + MCP + all agents + playground) |
| `make setup-keycloak` | Provision Keycloak realm, client, audience mapper, roles, and demo users |
| `make deploy-db` | Create namespace and apply Kustomize (Secret + ConfigMap + Deployment + Service) |
| `make deploy-mcp` | Build MCP server image via `oc new-build` and deploy |
| `make deploy-banking` | Build and deploy Banking Operations Agent |
| `make deploy-knowledge` | Build and deploy Knowledge Agent |
| `make deploy-orchestrator` | Build and deploy Orchestrator Agent (Helm) |
| `make deploy-playground` | Build and deploy Playground UI (Helm) |
| `make clean` | Tear down all workloads + Keycloak realm (keeps namespace and build configs) |
| `make test-pgvector` | Run pgvector schema + RLS tests (requires Podman) |
| `make test-knowledge-agent` | Run A2A tests against Knowledge Agent with Keycloak JWTs |
| `make compile-pipeline` | Compile the KFP pipeline to YAML |

`make deploy` runs the targets in order: `setup-keycloak` → `deploy-db` → `deploy-mcp` → `deploy-banking` → `deploy-knowledge` → `deploy-orchestrator` → `deploy-playground`.

### Cleanup

```bash
make clean
```

This removes:
- All AgentRuntime CRs
- Agent deployments and services
- Helm releases (orchestrator + playground)
- PostgreSQL deployment, service, and PVC
- Secrets and configmaps
- Keycloak `redbank` realm (including users, client, roles)

The namespace and OpenShift build configs are retained.

> **Note:** Tier 1 access gating (non-admin rejection at the network level) requires the Kagenti AuthBridge sidecar, which is injected by the Kagenti operator. Without it, the MCP server's `@admin_only` decorator still enforces write restrictions at the tool level (Tier 2), and RLS enforces read scoping at the database level.

## Using the Playground

Get the playground URL:

```bash
oc get route redbank-playground -n redbank-demo -o jsonpath='https://{.spec.host}{"\n"}'
```

Open the URL in a browser and log in with one of the demo users:

| User | Username | Password | Role |
|------|----------|----------|------|
| Jane | `jane` | `jane123` | admin — full access to all customers and write operations |
| John | `john` | `john123` | regular user — can only see own data, read-only |

### Known Limitations

Most MCP tools require a `customer_id` (integer primary key) rather than a customer name. To query account summaries, transactions, or perform updates, you need to know the ID. Use `get_customer` with an email or phone number first to retrieve the customer ID, then pass it to subsequent tools.

| customer_id | Name | Email |
|-------------|------|-------|
| 1 | Alice Johnson | alice.johnson@email.com |
| 2 | Bob Smith | bob.smith@email.com |
| 3 | Carol Williams | carol.williams@email.com |
| 4 | David Brown | david.brown@email.com |
| 5 | John Doe | john@redbank.demo |

### Example Queries

**As Jane (admin):**
- "Get the account summary for customer 1"
- "Show me the transactions for customer 2 in January 2025"
- "Update the phone number for customer 5 to 555-1234"
- "Create a $500 credit deposit for customer 1"
- "Look up the customer with email alice.johnson@email.com"
- "What is the process for reversing a transaction?"
- "How do I create or deactivate a user account?"

**As John (regular user):**
- "Look up the customer with email john@redbank.demo"
- "Get the account summary for customer 5"
- "Show me the transactions for customer 5"
- "How do I make a payment or transfer?"
- "What are the password and security requirements?"
- "Look up the customer with email alice.johnson@email.com" (RLS returns nothing — John can only see his own data)
- "What are RedBank's compliance procedures?" (knowledge search returns nothing — compliance docs are admin-only)

## Testing & Configuration

For manual testing walkthroughs, automated test instructions, per-component environment variable reference, and production configuration examples, see **[TESTING.md](TESTING.md)**.
