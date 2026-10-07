#!/usr/bin/env bash
# Step 5: make workspace membership follow Keycloak groups. Safe to re-run; DRY_RUN=true prints the plan.
#
# For every group named ${GROUP_PREFIX}-ws-<workspace>-users or -admins, the workspace exists and
# its members are exactly the enabled users of those groups (admin wins over user). Members of a
# managed workspace who are in neither group are removed: removing someone from the group is the
# offboarding step. Workspaces without any such group are not touched.
#
# Keycloak is read with ${SYNC_CLIENT} (view/query users and groups only). The gateway is changed
# with ${AUTOMATION_CLIENT}, the gateway admin service account. Neither token is printed.
set -euo pipefail
. "$(dirname "$0")/env.sh"
DRY_RUN="${DRY_RUN:-false}"

secret() { oc -n "${NAMESPACE}" get secret "$1" -o jsonpath='{.data.client-secret}' | base64 -d; }
kc_token=$(curl -sf -m 20 "${ISSUER}/protocol/openid-connect/token" -d grant_type=client_credentials \
    -d client_id="${SYNC_CLIENT}" --data-urlencode "client_secret=$(secret openshell-member-sync-oidc)" | jq -r .access_token)
kc() { curl -sf -m 30 -H "Authorization: Bearer ${kc_token}" "${KEYCLOAK_URL}/admin/realms/${REALM}$1"; }

export OPENSHELL_GATEWAY="${CLI_GATEWAY_NAME}"
export OPENSHELL_OIDC_CLIENT_SECRET="$(secret openshell-automation-oidc)"
# Client-credentials tokens have no refresh token; log in again rather than let the CLI try to refresh.
openshell gateway login "${CLI_GATEWAY_NAME}" >/dev/null 2>&1

groups=$(kc "/groups?search=${GROUP_PREFIX}-ws-&max=1000&briefRepresentation=true" | jq -r '.[] | "\(.id)\t\(.name)"')
ws_re="^${GROUP_PREFIX}-ws-([a-z0-9]([-a-z0-9]*[a-z0-9])?)-(users|admins)$"
# Managed workspaces come from group names, so an emptied group still removes its last member.
managed=$(echo "${groups}" | while IFS=$'\t' read -r _ name; do [[ "${name}" =~ ${ws_re} ]] && echo "${BASH_REMATCH[1]}"; done | sort -u)
# desired: workspace<TAB>subject<TAB>role<TAB>username, from the workspace groups
desired=$(echo "${groups}" |
while IFS=$'\t' read -r id name; do
    [[ "${name}" =~ ${ws_re} ]] || continue
    ws=${BASH_REMATCH[1]}; role=user; [[ ${BASH_REMATCH[3]} == admins ]] && role=admin
    kc "/groups/${id}/members?max=10000&briefRepresentation=true" |
        jq -r --arg ws "$ws" --arg role "$role" '.[] | select(.enabled) | "\($ws)\t\(.id)\t\($role)\t\(.username)"'
done | sort -t$'\t' -k1,1 -k2,2 -k3,3 | awk -F'\t' '{k=$1"\t"$2} !(k in r) || $3=="admin" {r[k]=$3; u[k]=$4} END {for (k in r) print k"\t"r[k]"\t"u[k]}' | sort)

changes=0
run() { changes=$((changes+1)); echo "  $*"; [[ "${DRY_RUN}" == true ]] || "$@" >/dev/null; }
for ws in ${managed}; do
    echo "workspace ${ws}"
    openshell workspace get "${ws}" >/dev/null 2>&1 || run openshell workspace create --name "${ws}"
    current=$(openshell workspace member list --workspace "${ws}" -o json 2>/dev/null | jq -r '.members[]? | "\(.subject)\t\(.role)"' || true)
    want=$(echo "${desired}" | awk -F'\t' -v ws="$ws" '$1==ws {print $2"\t"$3"\t"$4}')
    while IFS=$'\t' read -r sub role user; do
        [[ -n "${sub}" ]] || continue
        have=$(echo "${current}" | awk -F'\t' -v s="$sub" '$1==s {print $2}')
        if [[ -z "${have}" ]]; then
            run openshell workspace member add --workspace "${ws}" --subject "${sub}" --role "${role}"   # ${user}
        elif [[ "${have}" != "${role}" ]]; then
            run openshell workspace member remove --workspace "${ws}" --subject "${sub}"
            run openshell workspace member add --workspace "${ws}" --subject "${sub}" --role "${role}"
        fi
    done <<< "${want}"
    while IFS=$'\t' read -r sub _; do
        [[ -n "${sub}" ]] || continue
        echo "${want}" | cut -f1 | grep -x "${sub}" >/dev/null || run openshell workspace member remove --workspace "${ws}" --subject "${sub}"
    done <<< "${current}"
done
log "${changes} change(s)$([[ "${DRY_RUN}" == true ]] && echo ' planned (dry run)')"
