#!/bin/bash
#
# Manual test: search_knowledge tool with Keycloak JWTs
#
# Prerequisites:
#   - MCP server deployed and port-forwarded: oc port-forward svc/redbank-mcp-server 8000:8000
#   - Keycloak deployed with redbank realm:   make setup-keycloak
#   - Documents seeded in PGVector (RAG pipeline run)
#
# Usage:
#   bash scripts/test-search-knowledge.sh
#
# Environment variables:
#   MCP_URL        MCP endpoint          (default: http://localhost:8000/mcp)
#   KEYCLOAK_URL   Keycloak base URL     (auto-detected from oc route)
#   JOHN_PASSWORD  Password for john     (default: john123)
#   JANE_PASSWORD  Password for jane     (default: jane123)

set -euo pipefail

MCP_URL="${MCP_URL:-http://localhost:8000/mcp}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-redbank}"
KEYCLOAK_CLIENT="${KEYCLOAK_CLIENT:-redbank-mcp}"
JOHN_PASSWORD="${JOHN_PASSWORD:-john123}"
JANE_PASSWORD="${JANE_PASSWORD:-jane123}"

# Auto-detect Keycloak URL
if [[ -z "${KEYCLOAK_URL:-}" ]]; then
  KEYCLOAK_HOST=$(oc get route keycloak -n keycloak -o jsonpath='{.spec.host}' 2>/dev/null || true)
  if [[ -n "${KEYCLOAK_HOST}" ]]; then
    KEYCLOAK_URL="https://${KEYCLOAK_HOST}"
  else
    echo "ERROR: KEYCLOAK_URL not set and could not auto-detect. Export KEYCLOAK_URL." >&2
    exit 1
  fi
fi

TOKEN_URL="${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token"

function _out() {
  echo ""
  echo "================================================================"
  echo "  $*"
  echo "================================================================"
}

function get_token() {
  local username="$1" password="$2"
  curl -sk "${TOKEN_URL}" \
    -d "grant_type=password" \
    -d "client_id=${KEYCLOAK_CLIENT}" \
    -d "username=${username}" \
    -d "password=${password}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"
}

function init_session() {
  local bearer="$1"
  local headers=(-H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")
  [[ -n "${bearer}" ]] && headers+=(-H "Authorization: Bearer ${bearer}")

  local resp
  resp=$(curl -sk -D - "${MCP_URL}" "${headers[@]}" -d '{
    "jsonrpc": "2.0",
    "id": "init",
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": {"name": "manual-test", "version": "1.0"}
    }
  }')

  echo "${resp}" | grep -i "mcp-session-id" | tr -d '\r' | awk -F': ' '{print $2}'
}

function tool_call() {
  local session_id="$1" tool_name="$2" arguments="$3" bearer="${4:-}"
  local headers=(
    -H "Content-Type: application/json"
    -H "Accept: application/json, text/event-stream"
    -H "Mcp-Session-Id: ${session_id}"
  )
  [[ -n "${bearer}" ]] && headers+=(-H "Authorization: Bearer ${bearer}")

  curl -sk "${MCP_URL}" "${headers[@]}" -d "{
    \"jsonrpc\": \"2.0\",
    \"id\": \"$(uuidgen 2>/dev/null || echo test-$$)\",
    \"method\": \"tools/call\",
    \"params\": {
      \"name\": \"${tool_name}\",
      \"arguments\": ${arguments}
    }
  }" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('data: '):
        line = line[6:]
    try:
        data = json.loads(line)
        print(json.dumps(data.get('result', data), indent=2))
        break
    except: pass
"
}

# ---------------------------------------------------------------

_out "Fetching Keycloak tokens"

echo "Getting Jane's token (admin)..."
JANE_TOKEN=$(get_token jane "${JANE_PASSWORD}")
echo "  Jane token: ${JANE_TOKEN:0:20}..."

echo "Getting John's token (user)..."
JOHN_TOKEN=$(get_token john "${JOHN_PASSWORD}")
echo "  John token: ${JOHN_TOKEN:0:20}..."

# ---------------------------------------------------------------

_out "TEST 1: Tool discovery"

SESSION=$(init_session "${JANE_TOKEN}")
echo "Session: ${SESSION}"
curl -sk "${MCP_URL}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: ${SESSION}" \
  -H "Authorization: Bearer ${JANE_TOKEN}" \
  -d '{"jsonrpc":"2.0","id":"tools","method":"tools/list"}' | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('data: '): line = line[6:]
    try:
        data = json.loads(line)
        tools = [t['name'] for t in data.get('result',{}).get('tools',[])]
        print('Available tools:', sorted(tools))
        assert 'search_knowledge' in tools, 'search_knowledge NOT found!'
        print('  ✓ search_knowledge is available')
        break
    except json.JSONDecodeError: pass
"

# ---------------------------------------------------------------

_out "TEST 2: Jane (admin) — search_knowledge"
echo "Query: 'password reset'"

JANE_SESSION=$(init_session "${JANE_TOKEN}")
tool_call "${JANE_SESSION}" "search_knowledge" '{"query": "password reset", "k": 3}' "${JANE_TOKEN}"

echo ""
echo "  → Admin should see results from ALL collections (admin + user)"

# ---------------------------------------------------------------

_out "TEST 3: John (user) — search_knowledge"
echo "Query: 'password reset'"

JOHN_SESSION=$(init_session "${JOHN_TOKEN}")
tool_call "${JOHN_SESSION}" "search_knowledge" '{"query": "password reset", "k": 3}' "${JOHN_TOKEN}"

echo ""
echo "  → User should see results from 'user' collection ONLY"

# ---------------------------------------------------------------

_out "TEST 4: John (user) — get own account"
echo "John (customer_id=5) should see his own data"

tool_call "${JOHN_SESSION}" "get_account_summary" '{"customer_id": 5}' "${JOHN_TOKEN}"

# ---------------------------------------------------------------

_out "TEST 5: John (user) — cannot see Alice (customer_id=1)"

tool_call "${JOHN_SESSION}" "get_customer" '{"email": "alice.johnson@email.com"}' "${JOHN_TOKEN}"

echo ""
echo "  → Should return empty {} (RLS blocks access)"

# ---------------------------------------------------------------

_out "TEST 6: Jane (admin) — sees all customers"

tool_call "${JANE_SESSION}" "get_account_summary" '{"customer_id": 1}' "${JANE_TOKEN}"

echo ""
echo "  → Admin should see Alice's full account summary"

# ---------------------------------------------------------------

_out "All manual tests complete"
echo "Review the output above to verify:"
echo "  1. search_knowledge appears in tool list"
echo "  2. Jane (admin) sees docs from all collections"
echo "  3. John (user) sees only 'user' collection docs"
echo "  4. John sees his own account data"
echo "  5. John cannot see Alice's data"
echo "  6. Jane can see any customer's data"
