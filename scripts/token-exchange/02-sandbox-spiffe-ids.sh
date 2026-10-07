#!/usr/bin/env bash
# Step 2: give every OpenShell sandbox supervisor its own SPIFFE ID:
#   <trust-domain>/openshell/sandbox/<namespace>.<pod-name>.<sandbox-id>
# The namespace and pod name come from the pod itself, so a pod that only copies
# the openshell.ai/sandbox-id annotation gets a different ID. Credit: Gordon Sim,
# who proposed this form upstream in NVIDIA/OpenShell#3100 and flagged that a
# sandbox ID alone is spoofable. The registrar registers exactly this ID.
# The gateway keeps the ZTWIM default <trust-domain>/ns/<namespace>/sa/<release>.
set -euo pipefail
. "$(dirname "$0")/env.sh"

oc apply -f - <<EOF
apiVersion: spire.spiffe.io/v1alpha1
kind: ClusterSPIFFEID
metadata:
  name: openshell-sandboxes-${NAMESPACE}
spec:
  className: zero-trust-workload-identity-manager-spire
  spiffeIDTemplate: 'spiffe://{{ .TrustDomain }}/openshell/sandbox/{{ .PodMeta.Namespace }}.{{ .PodMeta.Name }}{{ with (index .PodMeta.Annotations "openshell.ai/sandbox-id") }}.{{ . }}{{ end }}'
  jwtTtl: 5m
  namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: ${NAMESPACE}
  podSelector:
    matchLabels:
      openshell.ai/managed-by: openshell
      openshell.ai/component: supervisor
EOF
log "sandbox supervisors in ${NAMESPACE} get ${TRUST_DOMAIN}/openshell/sandbox/<namespace>.<pod-name>.<sandbox-id>"
