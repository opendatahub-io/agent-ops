#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# End-to-end SPIFFE token grant demo on OpenShell + ZTWIM (OpenShift).
# Applies the demo workloads, registers the provider profile, creates a
# sandbox, and curls the alpha/beta protected services. The sandbox supervisor
# fetches a SPIFFE JWT-SVID from ZTWIM's SPIRE, exchanges it at the demo
# token-issuer, and injects the returned access token into the outbound call.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="${SCRIPT_DIR}/k8s/token-grant"
PROFILE_FILE="${SCRIPT_DIR}/provider-profiles/token-grant.yaml"

SANDBOX_NAME="${SANDBOX_NAME:-spiffe-token-demo}"
PROVIDER_NAME="${PROVIDER_NAME:-spiffe-token-demo}"
PROFILE_ID="${PROFILE_ID:-spiffe-token-demo}"
WORKLOAD_NAMESPACE="${WORKLOAD_NAMESPACE:-default}"
PORT_FORWARD_PORT="${PORT_FORWARD_PORT:-8097}"
GATEWAY_ENDPOINT="${GATEWAY_ENDPOINT:-http://127.0.0.1:${PORT_FORWARD_PORT}}"
KEEP_SANDBOX="${KEEP_SANDBOX:-0}"
ACCESS_TOKEN_SECRET="${ACCESS_TOKEN_SECRET:-$(openssl rand -hex 32)}"

TEMP_CONFIG_HOME=""
TEMP_K8S_DIR=""
PF_PID=""

cleanup() {
    if [[ "$KEEP_SANDBOX" != "1" ]]; then
        openshell --gateway-endpoint "$GATEWAY_ENDPOINT" sandbox delete "$SANDBOX_NAME" >/dev/null 2>&1 || true
    fi
    if [[ -n "$PF_PID" ]]; then
        kill "$PF_PID" >/dev/null 2>&1 || true
    fi
    if [[ -n "$TEMP_K8S_DIR" ]]; then
        rm -rf "$TEMP_K8S_DIR"
    fi
    if [[ -n "$TEMP_CONFIG_HOME" ]]; then
        rm -rf "$TEMP_CONFIG_HOME"
    fi
}
trap cleanup EXIT

run() {
    printf "\n$ %s\n" "$*"
    "$@"
}

wait_for_port_forward() {
    for _ in $(seq 1 60); do
        if nc -z 127.0.0.1 "$PORT_FORWARD_PORT" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.25
    done
    printf "gateway port-forward did not become ready\n" >&2
    exit 1
}

assert_contains() {
    local haystack="$1"
    local needle="$2"
    if [[ "$haystack" != *"$needle"* ]]; then
        printf "expected output to contain: %s\n" "$needle" >&2
        printf "actual output:\n%s\n" "$haystack" >&2
        exit 1
    fi
}

sandbox_curl_until() {
    local label="$1"
    local url="$2"
    local expected="$3"
    local output=""

    for attempt in $(seq 1 12); do
        printf "\n$ openshell sandbox exec %s curl (attempt %s)\n" "$label" "$attempt"
        if output=$(openshell --gateway-endpoint "$GATEWAY_ENDPOINT" sandbox exec \
            --name "$SANDBOX_NAME" --no-tty -- curl -sS --max-time 10 "$url" 2>&1); then
            printf "%s\n" "$output"
            if [[ "$output" == *"$expected"* ]]; then
                SANDBOX_CURL_OUTPUT="$output"
                return 0
            fi
        else
            printf "%s\n" "$output"
        fi
        sleep 2
    done

    printf "timed out waiting for %s to return expected output\n" "$label" >&2
    printf "last output:\n%s\n" "$output" >&2
    exit 1
}

: "${KUBECONFIG:?KUBECONFIG must point at the target cluster}"

CLUSTER_DOMAIN="$(oc get ingresses.config/cluster -o jsonpath='{.spec.domain}')"
[[ -n "$CLUSTER_DOMAIN" ]] || { printf "failed to detect the cluster domain\n" >&2; exit 1; }
printf "cluster domain (ZTWIM trust domain): %s\n" "$CLUSTER_DOMAIN"

# Substitute the cluster domain into a temp copy of the manifests so the
# committed workloads stay cluster-independent.
TEMP_K8S_DIR="$(mktemp -d)"
cp -r "$K8S_DIR"/. "$TEMP_K8S_DIR"/
sed -i "s/__CLUSTER_DOMAIN__/${CLUSTER_DOMAIN}/g" "$TEMP_K8S_DIR/workloads.yaml"

# Isolated CLI config so the demo does not touch existing gateway registrations.
TEMP_CONFIG_HOME="$(mktemp -d)"
export XDG_CONFIG_HOME="$TEMP_CONFIG_HOME"

printf "\n$ kubectl -n %s create secret generic openshell-spiffe-token-demo --from-literal=access-token-secret=*** --dry-run=client -o yaml | kubectl apply -f -\n" "$WORKLOAD_NAMESPACE"
kubectl -n "$WORKLOAD_NAMESPACE" create secret generic openshell-spiffe-token-demo \
    --from-literal=access-token-secret="$ACCESS_TOKEN_SECRET" \
    --dry-run=client \
    -o yaml | kubectl apply -f -

run kubectl apply -k "$TEMP_K8S_DIR"
run kubectl -n "$WORKLOAD_NAMESPACE" rollout restart deployment/token-issuer deployment/alpha deployment/beta
run kubectl -n "$WORKLOAD_NAMESPACE" rollout status deployment/token-issuer --timeout=180s
run kubectl -n "$WORKLOAD_NAMESPACE" rollout status deployment/alpha --timeout=180s
run kubectl -n "$WORKLOAD_NAMESPACE" rollout status deployment/beta --timeout=180s

kubectl -n openshell port-forward svc/openshell "${PORT_FORWARD_PORT}:8080" >/dev/null 2>&1 &
PF_PID=$!
wait_for_port_forward

openshell --gateway-endpoint "$GATEWAY_ENDPOINT" sandbox delete "$SANDBOX_NAME" >/dev/null 2>&1 || true
openshell --gateway-endpoint "$GATEWAY_ENDPOINT" provider delete "$PROVIDER_NAME" >/dev/null 2>&1 || true
openshell --gateway-endpoint "$GATEWAY_ENDPOINT" profile delete "$PROFILE_ID" >/dev/null 2>&1 || true

run openshell --gateway-endpoint "$GATEWAY_ENDPOINT" profile lint -f "$PROFILE_FILE"
run openshell --gateway-endpoint "$GATEWAY_ENDPOINT" profile import -f "$PROFILE_FILE"
run openshell --gateway-endpoint "$GATEWAY_ENDPOINT" provider create --name "$PROVIDER_NAME" --type "$PROFILE_ID" --runtime-credentials
# The default sandbox runtime image has no curl; create the sandbox from the
# curl image (its /usr/local/bin/curl is in the profile's binaries allowlist).
# No trailing command: a completing command would end the sandbox before exec.
run openshell --gateway-endpoint "$GATEWAY_ENDPOINT" sandbox create --name "$SANDBOX_NAME" --provider "$PROVIDER_NAME" --no-tty --from docker.io/curlimages/curl:latest

sandbox_curl_until "alpha" "http://alpha.default.svc.cluster.local/" "alpha called with path /:"
ALPHA_OUTPUT="$SANDBOX_CURL_OUTPUT"
assert_contains "$ALPHA_OUTPUT" "alpha called with path /:"
assert_contains "$ALPHA_OUTPUT" "aud: alpha, account"
assert_contains "$ALPHA_OUTPUT" "scope: alpha profile email"
assert_contains "$ALPHA_OUTPUT" "azp: spiffe://${CLUSTER_DOMAIN}/ns/"

sandbox_curl_until "beta" "http://beta.default.svc.cluster.local/" "beta called with path /:"
BETA_OUTPUT="$SANDBOX_CURL_OUTPUT"
assert_contains "$BETA_OUTPUT" "beta called with path /:"
assert_contains "$BETA_OUTPUT" "aud: beta, account"
assert_contains "$BETA_OUTPUT" "scope: beta profile email"
assert_contains "$BETA_OUTPUT" "azp: spiffe://${CLUSTER_DOMAIN}/ns/"

printf "\n$ kubectl -n %s logs -l app=alpha --tail=20\n" "$WORKLOAD_NAMESPACE"
kubectl -n "$WORKLOAD_NAMESPACE" logs -l app=alpha --tail=20 --prefix=true | sed 's/^/alpha> /' || true
printf "\n$ kubectl -n %s logs -l app=beta --tail=20\n" "$WORKLOAD_NAMESPACE"
kubectl -n "$WORKLOAD_NAMESPACE" logs -l app=beta --tail=20 --prefix=true | sed 's/^/beta> /' || true

printf "\nSPIFFE token grant demo passed.\n"
