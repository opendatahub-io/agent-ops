#!/usr/bin/env bash
# Step 1: work around the ZTWIM 1.1.1 OIDC discovery provider crash loop.
#
# ZTWIM 1.1.1 configures the SPIRE controller-manager to ignore openshift-* namespaces,
# which includes its own openshift-ztwim namespace. The OIDC discovery provider never
# gets an SVID, so it crash-loops and Keycloak has no JWKS to verify SVIDs.
# ClusterStaticEntry objects are not subject to that ignore list, so registering the
# provider with one entry per SPIRE agent (one per worker node) fixes it.
# Safe to re-run. Re-run after adding or replacing worker nodes.
set -euo pipefail
. "$(dirname "$0")/env.sh"

cluster_name=$(oc get zerotrustworkloadidentitymanager cluster -o jsonpath='{.spec.clusterName}')
provider_id="${TRUST_DOMAIN}/ns/openshift-ztwim/sa/spire-spiffe-oidc-discovery-provider"

while read -r node uid; do
    [[ -z "${uid}" ]] && continue
    log "registering OIDC discovery provider under the SPIRE agent on ${node}"
    oc apply -f - >/dev/null <<EOF
apiVersion: spire.spiffe.io/v1alpha1
kind: ClusterStaticEntry
metadata:
  name: oidc-discovery-provider-${uid:0:8}
  labels:
    agent-ops/workaround: ztwim-ignore-namespaces
spec:
  className: zero-trust-workload-identity-manager-spire
  parentID: ${TRUST_DOMAIN}/spire/agent/k8s_psat/${cluster_name}/${uid}
  spiffeID: ${provider_id}
  selectors:
    - k8s:ns:openshift-ztwim
    - k8s:sa:spire-spiffe-oidc-discovery-provider
  dnsNames:
    - spire-spiffe-oidc-discovery-provider.openshift-ztwim.svc.cluster.local
EOF
done < <(oc get nodes -l node-role.kubernetes.io/worker \
    -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.uid}{"\n"}{end}')

ready=$(oc get spireoidcdiscoveryprovider cluster -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
if [[ "${ready}" != "True" ]]; then
    log "restarting the OIDC discovery provider"
    oc -n openshift-ztwim delete pod -l app.kubernetes.io/name=spiffe-oidc-discovery-provider --wait=false >/dev/null
    oc -n openshift-ztwim rollout status deploy/spire-spiffe-oidc-discovery-provider --timeout=180s
fi

oc wait zerotrustworkloadidentitymanager/cluster --for=condition=Ready --timeout=180s >/dev/null
log "ZTWIM is Ready; SPIRE JWKS is served at ${SPIRE_JWKS}"
