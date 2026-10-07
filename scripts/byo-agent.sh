#!/usr/bin/env bash
# Build examples/byo-agent on the cluster (OpenShift binary build into the internal registry),
# run it in a sandbox with examples/byo-agent/policy.yaml, print the agent's output, clean up.
# Uses the openshell CLI as configured (after make user-auth: OPENSHELL_GATEWAY and
# OPENSHELL_OIDC_CLIENT_SECRET for the admin entry, or a user's own login).
set -euo pipefail
NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=byo-agent
log() { echo -e "\033[0;32m[byo-agent]\033[0m $*"; }

oc -n "${NAMESPACE}" get bc "${NAME}" >/dev/null 2>&1 || \
    oc -n "${NAMESPACE}" new-build --binary --strategy=docker --name "${NAME}" >/dev/null
# The example uses Containerfile; OpenShift's docker strategy looks for Dockerfile by default.
oc -n "${NAMESPACE}" patch bc "${NAME}" --type=merge -p '{"spec":{"strategy":{"dockerStrategy":{"dockerfilePath":"Containerfile"}}}}' >/dev/null
log "building the image on the cluster"
oc -n "${NAMESPACE}" start-build "${NAME}" --from-dir "${ROOT}/examples/byo-agent" --follow --wait >/dev/null
image="image-registry.openshift-image-registry.svc:5000/${NAMESPACE}/${NAME}:latest"

openshell sandbox delete "${NAME}" >/dev/null 2>&1 || true
log "starting sandbox ${NAME} from ${image}"
openshell sandbox create --name "${NAME}" --from "${image}" --policy "${ROOT}/examples/byo-agent/policy.yaml" --no-tty </dev/null >/dev/null
openshell sandbox exec --name "${NAME}" --no-tty -- /usr/bin/python3.12 /usr/local/bin/agent.py
openshell sandbox delete "${NAME}" >/dev/null 2>&1 || true
