# Testing & Configuration Reference

Manual testing walkthrough, automated tests, and per-component environment variable reference for the RedBank demo.

See [README.md](README.md) for project overview and deployment instructions.

## Table of Contents

- [Manual Testing](#manual-testing)
  - [Step 1 — Verify database seed data](#step-1--verify-database-seed-data)
  - [Step 2 — Verify RLS scoping](#step-2--verify-rls-scoping)
  - [Step 3 — Initialize an MCP session](#step-3--initialize-an-mcp-session)
  - [Step 4 — List tools](#step-4--list-tools)
  - [Step 5 — Get Keycloak tokens](#step-5--get-keycloak-tokens)
  - [Step 6 — Admin read](#step-6--admin-read)
  - [Step 7 — User read (RLS scoped)](#step-7--user-read-rls-scoped)
  - [Step 8 — User write (blocked)](#step-8--user-write-blocked)
  - [Step 9 — Admin write (allowed)](#step-9--admin-write-allowed)
  - [Step 10 — Test the Knowledge Agent (A2A)](#step-10--test-the-knowledge-agent-a2a)
  - [Step 11 — Verify Kagenti enrollment](#step-11--verify-kagenti-enrollment)
- [Automated Tests](#automated-tests)
- [Per-Component Environment Variables](#per-component-environment-variables)
  - [MCP Server](#mcp-server)
  - [Banking Agent](#banking-agent)
  - [Knowledge Agent](#knowledge-agent)
  - [Orchestrator Agent](#orchestrator-agent)
  - [Playground](#playground)
  - [Production Configuration](#production-configuration)

## Manual Testing

### Prerequisites

- The demo is deployed (`make deploy`)
- Port-forward is active in a separate terminal:

```bash
oc port-forward svc/redbank-mcp-server 8000:8000
```

### Step 1 — Verify database seed data

Run from your local terminal (not inside the pod):

```bash
oc rsh deployment/postgresql psql -U user -d db -c "
  SELECT set_config('app.current_role', 'admin', false);
  SELECT set_config('app.current_user_email', 'jane@redbank.demo', false);
  SELECT count(*) FROM customers;
  SELECT count(*) FROM statements;
  SELECT count(*) FROM transactions;
  SELECT count(*) FROM user_accounts;
"
```

Expected: 5 customers, 13 statements, 27 transactions, 2 user_accounts.

Verify RLS is enabled and forced:

```bash
oc rsh deployment/postgresql psql -U user -d db -c "
  SELECT set_config('app.current_role', 'admin', false);
  SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class
  WHERE relname IN ('customers', 'statements', 'transactions');
"
```

Expected: all rows show `t` / `t`.

### Step 2 — Verify RLS scoping

Switch to John's user context and confirm he can only see his own data:

```bash
oc rsh deployment/postgresql psql -U user -d db -c "
  SELECT set_config('app.current_role', 'user', false);
  SELECT set_config('app.current_user_email', 'john@redbank.demo', false);
  SELECT customer_id, name FROM customers;
  SELECT count(*) FROM transactions;
"
```

Expected: only customer_id=5 (John Doe), and only John's transactions (8 from seed data).

### Step 3 — Initialize an MCP session

The MCP server uses FastMCP's Streamable HTTP transport, which requires a session. All curl commands need these headers:

```
Content-Type: application/json
Accept: application/json, text/event-stream
```

Initialize a session and capture the session ID:

```bash
SESSION_ID=$(curl -si http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl-test","version":"1.0"}}}' \
  2>&1 | grep -i 'mcp-session-id' | tr -d '\r' | awk '{print $2}')

echo "Session: $SESSION_ID"
```

### Step 4 — List tools

```bash
curl -s http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expected: 6 tools (`get_customer`, `get_customer_transactions`, `get_account_summary`, `search_knowledge`, `update_account`, `create_transaction`).

### Step 5 — Get Keycloak tokens

Fetch real tokens from Keycloak for the demo users. Requires `make setup-keycloak` to have been run first (this is included in `make deploy`).

```bash
KEYCLOAK_URL="https://$(oc get route keycloak -n keycloak -o jsonpath='{.spec.host}')"

# John (regular user)
JOHN_JWT=$(curl -sf "${KEYCLOAK_URL}/realms/redbank/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=redbank-mcp" \
  -d "username=john" \
  -d "password=john123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Jane (admin)
JANE_JWT=$(curl -sf "${KEYCLOAK_URL}/realms/redbank/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=redbank-mcp" \
  -d "username=jane" \
  -d "password=jane123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

### Step 6 — Admin read

```bash
curl -s http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -H "Authorization: Bearer $JANE_JWT" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_customer","arguments":{"email":"alice.johnson@email.com"}}}'
```

Expected: Alice Johnson's full customer record.

### Step 7 — User read (RLS scoped)

John can see his own data:

```bash
curl -s http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -H "Authorization: Bearer $JOHN_JWT" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_customer","arguments":{"email":"john@redbank.demo"}}}'
```

Expected: John Doe's customer record (customer_id 5).

John cannot see other customers:

```bash
curl -s http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -H "Authorization: Bearer $JOHN_JWT" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_customer","arguments":{"email":"bob.smith@email.com"}}}'
```

Expected: empty `{}` — RLS blocks access to Bob's record.

### Step 8 — User write (blocked)

```bash
curl -s http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -H "Authorization: Bearer $JOHN_JWT" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"update_account","arguments":{"customer_id":5,"phone":"555-0000"}}}'
```

Expected: `"isError": true` with `"This operation requires admin privileges"`.

### Step 9 — Admin write (allowed)

```bash
curl -s http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -H "Authorization: Bearer $JANE_JWT" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"update_account","arguments":{"customer_id":5,"phone":"555-9999"}}}'
```

Expected: updated customer record with `phone: "555-9999"`.

### Step 10 — Test the Knowledge Agent (A2A)

The Knowledge Agent test script sends A2A requests with Keycloak JWTs and verifies RAG search, customer data access, RLS scoping, and write tool blocking.

**Automated (with port-forward):**

```bash
make test-knowledge-agent
```

This port-forwards the agent, runs 6 tests, and cleans up. The tests verify:

1. **Jane (admin) — knowledge search**: returns docs from all collections
2. **John (user) — knowledge search**: returns docs from `user` collection only
3. **Jane (admin) — customer data**: can see any customer's account
4. **John (user) — own data**: sees his own balance
5. **John (user) — other customers**: blocked by RLS ("No data was found")
6. **John (user) — write tools**: blocked by allow-list ("I don't have permission")

**Manual (port-forward yourself):**

```bash
oc port-forward svc/redbank-knowledge-agent 8002:8002
bash scripts/test-knowledge-agent.sh
```

### Step 11 — Verify Kagenti enrollment

```bash
oc get agentruntime
# expect: all four runtimes in Active phase

oc get deployment redbank-mcp-server -o jsonpath='{.metadata.labels.kagenti\.io/type}'
# expect: tool (set by operator)

oc get svc redbank-mcp-server -o jsonpath='{.metadata.labels.protocol\.kagenti\.io/mcp}'
# expect: true
```

## Automated Tests

Integration tests cover tool discovery, admin reads, user RLS scoping, write enforcement, and Keycloak token acquisition.

### Prerequisites

- MCP server deployed and running
- Port-forward active: `oc port-forward svc/redbank-mcp-server 8000:8000`
- Keycloak realm configured (included in `make deploy`)

### Run

```bash
pip install requests pytest
pytest tests/test_mcp_rls.py -v
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_URL` | `http://localhost:8000/mcp` | MCP server endpoint |
| `KEYCLOAK_URL` | cluster route | Keycloak base URL |
| `KEYCLOAK_REALM` | `redbank` | Keycloak realm |
| `KEYCLOAK_CLIENT` | `redbank-mcp` | Keycloak client ID |
| `JOHN_PASSWORD` | `john123` | Password for john |
| `JANE_PASSWORD` | `jane123` | Password for jane |
| `USE_FAKE_JWT` | `false` | Set `true` to use unsigned JWTs (for `JWT_VERIFY=false` mode) |

By default, tests fetch real access tokens from Keycloak. Set `USE_FAKE_JWT=true` for local dev without Keycloak.

## Per-Component Environment Variables

These are the env vars each component reads at runtime (set automatically by the deploy scripts and Helm charts from the top-level `.env`).

### MCP Server

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_DATABASE` | `db` | Database name |
| `POSTGRES_USER` | `user` | Database user |
| `POSTGRES_PASSWORD` | `pass` | Database password |
| `POSTGRES_PORT` | `5432` | Database port |
| `JWT_VERIFY` | `false` | `false` = trust AuthBridge sidecar; `true` = verify JWT via JWKS |
| `JWT_ALGORITHMS` | `RS256` | Comma-separated JWT algorithms |
| `JWKS_URL` | (empty) | Keycloak JWKS endpoint (required when `JWT_VERIFY=true`) |
| `JWT_AUDIENCE` | (empty) | Expected JWT `aud` claim |
| `ADMIN_ROLE_CLAIM` | `admin` | Role name that grants admin access |
| `DEFAULT_ROLE` | `admin` | Fallback role when no Bearer token present |
| `DEFAULT_EMAIL` | `jane@redbank.demo` | Fallback email when no Bearer token present |
| `PGVECTOR_USER` | `app` | Database user for PGVector connections |
| `PGVECTOR_PASSWORD` | `app` | Password for PGVector database user |
| `EMBEDDING_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | HuggingFace embedding model for `search_knowledge` |

### Banking Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8001` | Bind port |
| `MCP_SERVER_URL` | `http://redbank-mcp-server:8000/mcp` | MCP server endpoint (in-cluster service) |
| `LLM_BASE_URL` | (required) | vLLM or OpenAI API base URL |
| `LLM_MODEL` | (required) | Model name |
| `OPENAI_API_KEY` | (required) | API key for the LLM endpoint |
| `MLFLOW_TRACKING_URI` | (optional) | MLflow tracking endpoint |
| `AGENT_URL` | `http://redbank-banking-agent:8001` | Agent's own URL (used in agent card) |

### Knowledge Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8002` | Bind port |
| `MCP_SERVER_URL` | `http://redbank-mcp-server:8000/mcp` | MCP server endpoint (in-cluster service) |
| `LLM_BASE_URL` | (required) | vLLM or OpenAI API base URL |
| `LLM_MODEL` | (required) | Model name |
| `OPENAI_API_KEY` | (required) | API key for the LLM endpoint |
| `MLFLOW_TRACKING_URI` | (optional) | MLflow tracking endpoint |
| `AGENT_URL` | `http://redbank-knowledge-agent:8002` | Agent's own URL (used in agent card) |

### Orchestrator Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Bind port |
| `OPENAI_API_KEY` | (required) | API key for the LLM endpoint |
| `LLM_BASE_URL` | (required) | LLM base URL (auto-appends `/v1` if missing) |
| `LLM_MODEL` | (required) | Model name |
| `MLFLOW_TRACKING_URI` | (optional) | MLflow tracking endpoint |
| `MLFLOW_EXPERIMENT_NAME` | `default-agent-experiment` | MLflow experiment name |
| `AGENT_PUBLIC_URL` | `http://localhost:{PORT}` | Public URL for agent card |
| `LOG_LEVEL` | `INFO` | Logging level |

### Playground

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Bind port |
| `ORCHESTRATOR_URL` | `http://localhost:8000` | URL of the orchestrator backend |
| `KEYCLOAK_URL` | (optional) | Keycloak base URL — enables OIDC auth when set with `KEYCLOAK_REALM` and `KEYCLOAK_CLIENT_ID` |
| `KEYCLOAK_REALM` | (optional) | Keycloak realm name |
| `KEYCLOAK_CLIENT_ID` | (optional) | Keycloak client ID |
| `LOG_LEVEL` | `INFO` | Logging level |

### Production Configuration

**With AuthBridge sidecar** (standard Kagenti deployment) — the sidecar validates and exchanges tokens upstream. The MCP server decodes the trusted token without re-verifying the signature:

```yaml
- name: JWT_VERIFY
  value: "false"
- name: JWT_AUDIENCE
  value: "redbank-mcp"   # AuthBridge token exchange sets this audience
- name: DEFAULT_ROLE
  value: "user"           # fail-safe: no token = restricted access
```

**Standalone deployment** (no AuthBridge, e.g. dev cluster) — the MCP server verifies JWT signatures directly via JWKS:

```yaml
- name: JWT_VERIFY
  value: "true"
- name: JWKS_URL
  value: "https://keycloak.example.com/realms/redbank/protocol/openid-connect/certs"
- name: JWT_AUDIENCE
  value: "account"        # or "redbank-mcp" if audience mapper is configured
- name: DEFAULT_ROLE
  value: "user"           # fail-safe: no token = restricted access
```
