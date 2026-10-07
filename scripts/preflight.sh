#!/usr/bin/env bash
# Checks that a cluster is ready for OpenShell before anything is installed.
# Usage: ./scripts/preflight.sh [--token-exchange] [--user-auth]
#   --token-exchange  also check the prerequisites of install/03-agent-identity.md
#   --user-auth       also check install/02-user-auth.md (implies --token-exchange)
# Exits non-zero if a required check fails. Warnings do not fail the run.
set -uo pipefail

TOKEN_EXCHANGE=false; USER_AUTH=false
for arg in "$@"; do
    case "${arg}" in
        --token-exchange) TOKEN_EXCHANGE=true ;;
        --user-auth) TOKEN_EXCHANGE=true; USER_AUTH=true ;;
        *) echo "unknown option ${arg}" >&2; exit 2 ;;
    esac
done

failures=0
if [[ -t 1 ]]; then G='\033[0;32m' Y='\033[1;33m' R='\033[0;31m' N='\033[0m'; else G='' Y='' R='' N=''; fi
pass() { echo -e "  ${G}PASS${N} $*"; }
warn() { echo -e "  ${Y}WARN${N} $*"; }
fail() { echo -e "  ${R}FAIL${N} $*"; failures=$((failures + 1)); }

version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }

echo "Tools"
for tool in oc helm openssl; do
    command -v "$tool" >/dev/null && pass "$tool found" || fail "$tool not found on PATH"
done
for tool in jq curl; do   # used by the token-exchange and user-auth scripts
    if command -v "$tool" >/dev/null; then pass "$tool found"
    elif [[ "${TOKEN_EXCHANGE}" == true ]]; then fail "$tool not found on PATH"
    else warn "$tool not found on PATH; the token-exchange and user-auth scripts need it"; fi
done
command -v openshell >/dev/null && pass "openshell CLI found" || warn "openshell CLI not found; run ./scripts/install-openshell-cli.sh"
if ! oc whoami >/dev/null 2>&1; then
    fail "not logged in to a cluster (oc login)"
    echo; echo "Preflight failed: ${failures} check(s)."; exit 1
fi

echo "Cluster"
ocp=$(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null)
minor=$(cut -d. -f1-2 <<<"${ocp}")
case "${minor}" in
    4.19) min=4.19.35 ;; 4.20) min=4.20.26 ;; 4.21) min=4.21.21 ;; 4.22) min=4.22.2 ;; *) min="" ;;
esac
if [[ -z "${min}" ]]; then
    warn "OpenShift ${ocp}: not covered by the Red Hat build of Agent Sandbox support table (4.19 to 4.22)"
elif version_ge "${ocp}" "${min}"; then
    pass "OpenShift ${ocp} (Agent Sandbox minimum for ${minor} is ${min})"
else
    fail "OpenShift ${ocp} is below the Agent Sandbox minimum ${min} for ${minor}"
fi
oc auth can-i '*' '*' --all-namespaces >/dev/null 2>&1 && pass "cluster-admin permissions" \
    || fail "cluster-admin permissions are required to install operators and cluster-scoped resources"
default_sc=$(oc get storageclass -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{end}')
[[ -n "${default_sc}" ]] && pass "default storage class: ${default_sc}" || fail "no default storage class; sandboxes and the gateway need PVCs"
kernel=$(oc get nodes -l node-role.kubernetes.io/worker -o jsonpath='{.items[0].status.nodeInfo.kernelVersion}' 2>/dev/null)
if version_ge "${kernel%%-*}" "5.19"; then
    pass "worker kernel ${kernel}"
else
    warn "worker kernel ${kernel} is before Linux 5.19: sandboxes run in legacy read-only mode (Python HTTPS and some servers need workarounds; see install/01-install.md)"
fi

echo "Red Hat build of Agent Sandbox"
if oc get crd sandboxes.agents.x-k8s.io -o jsonpath='{.spec.versions[?(@.served==true)].name}' 2>/dev/null | grep -qw v1beta1; then
    pass "Sandbox CRD serves agents.x-k8s.io/v1beta1"
else
    fail "Agent Sandbox not installed; install it from the Software Catalog (Agent Sandbox, channel preview-0.9)"
fi
csv=$(oc get csv -A --no-headers 2>/dev/null | awk '/agent-sandbox-operator/{print $2; exit}')
[[ -n "${csv}" ]] && pass "operator ${csv}" || warn "Agent Sandbox CRDs found but not the Red Hat operator; an upstream install works but is not the supported build"

if [[ "${TOKEN_EXCHANGE}" == true ]]; then
    echo "Token exchange (SPIFFE + Keycloak)"
    td=$(oc get zerotrustworkloadidentitymanager cluster -o jsonpath='{.spec.trustDomain}' 2>/dev/null)
    if [[ -n "${td}" ]]; then
        pass "Zero Trust Workload Identity Manager configured, trust domain ${td}"
        oc get csidriver csi.spiffe.io >/dev/null 2>&1 && pass "SPIFFE CSI driver installed" || fail "SPIFFE CSI driver missing; create the SpiffeCSIDriver CR"
        ready=$(oc get spireoidcdiscoveryprovider cluster -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
        if [[ "${ready}" == "True" ]]; then
            pass "SPIRE OIDC discovery provider Ready"
        else
            warn "SPIRE OIDC discovery provider not Ready (known ZTWIM 1.1.1 issue); scripts/token-exchange/01-fix-ztwim-oidc.sh fixes it"
        fi
    else
        fail "Zero Trust Workload Identity Manager not configured; install it from the Software Catalog (channel stable-v1) and create the ZeroTrustWorkloadIdentityManager CR"
    fi
    if oc get packagemanifest rhbk-operator -n openshift-marketplace >/dev/null 2>&1; then
        latest=$(oc get packagemanifest rhbk-operator -n openshift-marketplace -o jsonpath='{.status.channels[?(@.name=="stable-v26.6")].currentCSV}')
        [[ -n "${latest}" ]] && pass "Red Hat build of Keycloak 26.6 available (${latest})" \
            || warn "Red Hat build of Keycloak channel stable-v26.6 not in the catalog; SPIFFE client auth needs 26.4 or later"
    else
        warn "rhbk-operator not in the catalog; step 3 only needs registry.redhat.io pull access"
    fi
fi

if [[ "${USER_AUTH}" == true ]]; then
    echo "User authentication"
    domain=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}' 2>/dev/null)
    if curl -s -m 10 -o /dev/null "https://console-openshift-console.${domain}/"; then
        pass "Route certificates on *.${domain} are publicly trusted"
    else
        warn "Route certificates on *.${domain} are not publicly trusted: set KEYCLOAK_CA_FILE to the CA that signs them before make user-auth (see install/02-user-auth.md)"
    fi
fi

echo
if [[ "${failures}" -eq 0 ]]; then
    echo "Preflight passed."
else
    echo "Preflight failed: ${failures} check(s)."
    exit 1
fi
