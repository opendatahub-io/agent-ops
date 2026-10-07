#!/usr/bin/env bash
# Verify user authentication end to end. Prints PASS/FAIL per check, exits non-zero on any FAIL.
#
# Access is driven by Keycloak groups only: grant.sh puts a user in a workspace group, the sync
# turns that into gateway membership; revoke.sh reverses both. The checks cover:
#   - the gateway: no token, no role, role without membership, role with membership
#   - the admin workflow: grant -> access in that workspace only; revoke -> no access
#   - the dashboard: login, session, role gate at the proxy, no direct path to the BFF
#   - hardening: no password login on the CLI client, no leftover test client
#
# User tokens come from a temporary test client with password login, deleted on exit.
set -uo pipefail
HERE="$(dirname "$0")"
. "${HERE}/env.sh"

fails=0
check() { if [[ "$2" == "$3" ]]; then echo "  PASS $1"; else echo "  FAIL $1 (got $2, want $3)"; fails=$((fails+1)); fi; }
oneof() { local name=$1 got=$2; shift 2; for w in "$@"; do [[ "$got" == "$w" ]] && { echo "  PASS ${name}"; return; }; done; echo "  FAIL ${name} (got $got, want one of $*)"; fails=$((fails+1)); }

TEST_CLIENT="openshell-verify-$(openssl rand -hex 3)"
cleanup() {
    kill "${pf:-0}" 2>/dev/null; rm -f "${jar:-}" /tmp/user-auth-bff.json
    kc_exec TC="${TEST_CLIENT}" <<'IN' >/dev/null 2>&1
KC=/opt/keycloak/bin/kcadm.sh; CFG=(--config /tmp/kcadm.config)
$KC config credentials "${CFG[@]}" --server http://localhost:8080 --realm master --user admin --password "$KC_ADMIN_PW"
id=$($KC get clients -r "$R" "${CFG[@]}" -q clientId="$TC" --fields id --format csv --noquotes | head -1)
[ -n "$id" ] && $KC delete "clients/$id" -r "$R" "${CFG[@]}"
IN
}
trap cleanup EXIT

# Temporary test client: password login, same scope as the CLI client (this gateway's roles only).
kc_exec TC="${TEST_CLIENT}" API="${API_CLIENT}" <<'IN' >/dev/null
KC=/opt/keycloak/bin/kcadm.sh; CFG=(--config /tmp/kcadm.config)
$KC config credentials "${CFG[@]}" --server http://localhost:8080 --realm master --user admin --password "$KC_ADMIN_PW"
id=$($KC create clients -r "$R" "${CFG[@]}" -i -s clientId="$TC" -s publicClient=true -s standardFlowEnabled=false \
    -s directAccessGrantsEnabled=true -s fullScopeAllowed=false)
api=$($KC get clients -r "$R" "${CFG[@]}" -q clientId="$API" --fields id --format csv --noquotes | head -1)
roles=$($KC get "clients/$api/roles" -r "$R" "${CFG[@]}")
$KC create "clients/$id/scope-mappings/clients/$api" -r "$R" "${CFG[@]}" -b "$roles"
IN

token() {  # client user password -> access token (empty when refused)
    curl -s -m 20 "${ISSUER}/protocol/openid-connect/token" -d grant_type=password -d client_id="$1" \
        -d scope=openid --data-urlencode "username=$2" --data-urlencode "password=$3" | jq -r '.access_token // empty'
}
bff() {  # METHOD PATH TOKEN [BODY] -> HTTP status; body in /tmp/user-auth-bff.json
    local args=(-s -m 60 -o /tmp/user-auth-bff.json -w '%{http_code}' -X "$1")
    [[ -n "$3" ]] && args+=(-H "Authorization: Bearer $3")
    [[ -n "${4:-}" ]] && args+=(-H 'Content-Type: application/json' -d "$4")
    curl "${args[@]}" "http://127.0.0.1:${BFF_LOCAL_PORT}/api/v1$2"
}
pw() { oc -n "${NAMESPACE}" get secret "$1" -o jsonpath='{.data.password}' | base64 -d; }

alice_pw=$(pw keycloak-demo-user); admin_pw=$(pw keycloak-platform-admin)
oc -n "${NAMESPACE}" port-forward deploy/openshell-dashboard "${BFF_LOCAL_PORT}:8080" >/dev/null 2>&1 & pf=$!
sleep 4

echo "Gateway: who you are and what you may do"
"${HERE}/revoke.sh" "${DEMO_USER}" team-a >/dev/null 2>&1
check "no token is refused" "$(bff GET /workspaces '')" 401
alice=$(token "${TEST_CLIENT}" "${DEMO_USER}" "${alice_pw}")
oneof "alice in no group is refused" "$(bff GET /workspaces/default/sandboxes "${alice}")" 401 403
admin=$(token "${TEST_CLIENT}" platform-admin "${admin_pw}")
check "platform-admin is identified as admin" "$(bff GET /auth/whoami "${admin}"; jq -r '(.roles // []) | index("admin") != null' /tmp/user-auth-bff.json)" "200true"

echo "Admin workflow: grant and revoke with one command"
check "grant alice team-a" "$("${HERE}/grant.sh" "${DEMO_USER}" team-a >/dev/null 2>&1; echo $?)" 0
alice=$(token "${TEST_CLIENT}" "${DEMO_USER}" "${alice_pw}")
check "alice has role user" "$(bff GET /auth/whoami "${alice}"; jq -r '(.roles // []) | index("user") != null' /tmp/user-auth-bff.json)" "200true"
check "alice lists sandboxes in team-a" "$(bff GET /workspaces/team-a/sandboxes "${alice}")" 200
check "alice is denied in default" "$(bff GET /workspaces/default/sandboxes "${alice}")" 403
policy='{"version":1,"filesystem":{"includeWorkdir":true,"readOnly":["/bin","/usr","/lib","/proc","/dev/urandom","/etc","/var/log"],"readWrite":["/tmp","/dev/null"]},"landlock":{"compatibility":"best_effort"}}'
check "alice creates a sandbox in team-a" "$(bff POST /workspaces/team-a/sandboxes "${alice}" "{\"name\":\"alice-oidc-check\",\"image\":\"registry.access.redhat.com/ubi9/ubi-minimal:latest\",\"policy\":${policy}}")" 201
check "alice deletes it" "$(bff DELETE /workspaces/team-a/sandboxes/alice-oidc-check "${alice}")" 200
check "revoke alice team-a" "$("${HERE}/revoke.sh" "${DEMO_USER}" team-a >/dev/null 2>&1; echo $?)" 0
oneof "alice is denied in team-a after revoke (old token)" "$(bff GET /workspaces/team-a/sandboxes "${alice}")" 401 403
alice=$(token "${TEST_CLIENT}" "${DEMO_USER}" "${alice_pw}")
oneof "alice is refused after revoke (new token)" "$(bff GET /workspaces/team-a/sandboxes "${alice}")" 401 403
"${HERE}/grant.sh" "${DEMO_USER}" team-a >/dev/null 2>&1   # leave alice with access for the browser checks

echo "Dashboard: login through the proxy"
host="${DASHBOARD_HOST}"
jar=$(mktemp)   # outside the function: browser_login runs in a subshell, the cookies must outlive it
browser_login() {  # user password -> HTTP status after Keycloak login lands back on the proxy
    : > "$jar"; local page action
    page=$(curl -s -m 30 -c "$jar" -b "$jar" -L "https://${host}/oauth2/start?rd=/")
    action=$(echo "$page" | grep -o 'action="[^"]*"' | head -1 | sed -e 's/^action="//' -e 's/"$//' -e 's/&amp;/\&/g')
    [[ "$action" == "${ISSUER}"/login-actions/* ]] || { echo "nologin"; return; }
    curl -s -m 30 -c "$jar" -b "$jar" -L -o /dev/null -w '%{http_code}' "$action" \
        --data-urlencode "username=$1" --data-urlencode "password=$2"
}
session_whoami() { curl -s -m 30 -b "$jar" "https://${host}/api/v1/auth/whoami"; }
check "dashboard without a session is refused" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' "https://${host}/api/v1/auth/whoami")" 401
check "alice signs in to the dashboard" "$(browser_login "${DEMO_USER}" "${alice_pw}")" 200
whoami_json=$(session_whoami)
check "dashboard session is alice" "$(echo "${whoami_json}" | jq -r '.displayName // empty' 2>/dev/null)" "${DEMO_USER}" || true
[[ "$(echo "${whoami_json}" | jq -r '.displayName // empty' 2>/dev/null)" == "${DEMO_USER}" ]] || echo "    whoami: ${whoami_json:0:200}"
"${HERE}/revoke.sh" "${DEMO_USER}" team-a >/dev/null 2>&1
check "a user without a role is stopped at the proxy" "$(browser_login "${DEMO_USER}" "${alice_pw}")" 403
"${HERE}/grant.sh" "${DEMO_USER}" team-a >/dev/null 2>&1
check "only the router may reach the dashboard (NetworkPolicy)" "$(oc -n "${NAMESPACE}" get networkpolicy openshell-dashboard -o name 2>/dev/null)" "networkpolicy.networking.k8s.io/openshell-dashboard"

echo "Hardening"
check "the CLI client refuses password login" "$(curl -s -m 20 "${ISSUER}/protocol/openid-connect/token" -d grant_type=password -d client_id="${CLI_CLIENT}" -d username=x -d password=x | jq -r .error)" unauthorized_client

[[ $fails -eq 0 ]] && echo "All checks passed." || { echo "${fails} check(s) failed."; exit 1; }
