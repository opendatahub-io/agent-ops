#!/bin/bash
#
# Manual test: Knowledge Agent (A2A) with Keycloak JWTs
#
# Prerequisites:
#   - MCP server deployed:           make deploy-mcp
#   - Knowledge agent deployed:      make deploy-knowledge-agent
#   - Keycloak configured:           make setup-keycloak
#   - Documents seeded in PGVector (RAG pipeline run)
#   - Port-forward the agent:        oc port-forward svc/redbank-knowledge-agent 8002:8002
#
# Usage:
#   bash scripts/test-knowledge-agent.sh
#
# Environment variables:
#   AGENT_URL      Agent endpoint        (default: http://localhost:8002)
#   KEYCLOAK_URL   Keycloak base URL     (auto-detected from oc route)
#   JOHN_PASSWORD  Password for john     (default: john123)
#   JANE_PASSWORD  Password for jane     (default: jane123)

set -euo pipefail

AGENT_URL="${AGENT_URL:-http://localhost:8002}"
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

function a2a_send() {
  local message="$1" bearer="${2:-}"
  local headers=(
    -H "Content-Type: application/json"
  )
  [[ -n "${bearer}" ]] && headers+=(-H "Authorization: Bearer ${bearer}")

  local id
  id=$(uuidgen 2>/dev/null || echo "msg-$$-${RANDOM}")

  echo "  User: ${message}"
  echo ""

  local resp
  resp=$(curl -sk "${AGENT_URL}" "${headers[@]}" -d "{
    \"jsonrpc\": \"2.0\",
    \"id\": \"${id}\",
    \"method\": \"message/send\",
    \"params\": {
      \"message\": {
        \"role\": \"user\",
        \"parts\": [{\"kind\": \"text\", \"text\": \"${message}\"}],
        \"messageId\": \"${id}\"
      }
    }
  }" 2>&1)

  echo "  Agent response:"
  echo "${resp}" | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
try:
    data = json.loads(raw)
    result = data.get('result', data)
    # Extract text from A2A response
    if isinstance(result, dict):
        artifacts = result.get('artifacts', [])
        for a in artifacts:
            for part in a.get('parts', []):
                if 'text' in part:
                    print('  ' + part['text'])
                    break
        status = result.get('status', {})
        msg = status.get('message', {})
        if msg:
            for part in msg.get('parts', []):
                if 'text' in part:
                    print('  ' + part['text'])
        if not artifacts and not msg:
            print(json.dumps(result, indent=2))
    else:
        print(raw[:500])
except Exception as e:
    print(f'  (raw response): {raw[:500]}')
" 2>&1 || echo "  (parse error — raw): ${resp:0:500}"
}

# ---------------------------------------------------------------

_out "Checking agent card"
curl -sk "${AGENT_URL}/.well-known/agent-card.json" | python3 -c "
import sys, json
card = json.loads(sys.stdin.read())
print(f\"  Name:    {card['name']}\")
print(f\"  Version: {card['version']}\")
for s in card.get('skills', []):
    print(f\"  Skill:   {s['id']} — {s['name']}\")
    print(f\"  Tags:    {', '.join(s.get('tags', []))}\")
"

# ---------------------------------------------------------------

_out "Fetching Keycloak tokens"

echo "Getting Jane's token (admin)..."
JANE_TOKEN=$(get_token jane "${JANE_PASSWORD}")
echo "  Token: ${JANE_TOKEN:0:20}..."

echo "Getting John's token (user)..."
JOHN_TOKEN=$(get_token john "${JOHN_PASSWORD}")
echo "  Token: ${JOHN_TOKEN:0:20}..."

# ---------------------------------------------------------------

_out "TEST 1: Jane (admin) — knowledge query"
echo "  Should route to search_knowledge and return docs from ALL collections"
a2a_send "How do I reset my password?" "${JANE_TOKEN}"

# ---------------------------------------------------------------

_out "TEST 2: John (user) — knowledge query"
echo "  Should route to search_knowledge and return docs from 'user' collection ONLY"
a2a_send "How do I reset my password?" "${JOHN_TOKEN}"

# ---------------------------------------------------------------

_out "TEST 3: Jane (admin) — customer data query"
echo "  Admin should see any customer's data"
a2a_send "What is the account summary for customer 1?" "${JANE_TOKEN}"

# ---------------------------------------------------------------

_out "TEST 4: John (user) — own customer data"
echo "  John (customer_id=5) should see his own data"
a2a_send "What is my account balance? My email is john@redbank.demo" "${JOHN_TOKEN}"

# ---------------------------------------------------------------

_out "TEST 5: John (user) — cannot see other customers"
echo "  RLS should block access — agent should say no data found"
a2a_send "Show me the account summary for customer 1" "${JOHN_TOKEN}"

# ---------------------------------------------------------------

_out "TEST 6: John (user) — write tools should NOT be available"
echo "  Agent should refuse or not have update/create tools"
a2a_send "Update customer 5 phone number to 555-9999" "${JOHN_TOKEN}"

# ---------------------------------------------------------------

_out "All manual tests complete"
echo ""
echo "Review the output above to verify:"
echo "  1. Jane sees knowledge docs from all collections"
echo "  2. John sees knowledge docs from user collection only"
echo "  3. Jane can look up any customer"
echo "  4. John can see his own account data"
echo "  5. John cannot see other customers (RLS)"
echo "  6. Write operations are refused (allow-list)"
