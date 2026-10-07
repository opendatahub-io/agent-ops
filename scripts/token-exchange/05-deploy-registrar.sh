#!/usr/bin/env bash
# Step 5: build and deploy the keycloak-registrar gateway interceptor, then register it
# with the OpenShell gateway. Safe to re-run.
#
# The OpenShell Helm chart has no interceptor values yet, so this script appends the
# interceptor to the gateway.toml ConfigMap and mounts the namespace's
# openshift-service-ca.crt ConfigMap into the gateway. A later `helm upgrade` reverts
# both; re-run this script after upgrading.
set -euo pipefail
. "$(dirname "$0")/env.sh"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${ROOT}/interceptors/keycloak-registrar"
IMAGE="image-registry.openshift-image-registry.svc:5000/${NAMESPACE}/keycloak-registrar:latest"

log "building the interceptor image on the cluster from ${SRC}"
stage="$(mktemp -d)"; trap 'rm -rf "${stage}"' EXIT
cp -R "${SRC}/Cargo.toml" "${SRC}/Cargo.lock" "${SRC}/src" "${stage}/"
cp "${SRC}/Dockerfile" "${stage}/Dockerfile"
oc -n "${NAMESPACE}" get bc keycloak-registrar >/dev/null 2>&1 || \
    oc -n "${NAMESPACE}" new-build --binary --strategy=docker --name=keycloak-registrar >/dev/null
oc -n "${NAMESPACE}" start-build keycloak-registrar --from-dir="${stage}" --wait >/dev/null

log "deploying the interceptor"
# The registrar only calls Keycloak's admin API, so it uses the in-cluster Service. Going through
# the public Route would fail whenever the Route certificate is signed by a private CA.
registrar_keycloak_url="${REALM_INTERNAL_URL%/realms/*}"
sed -e "s#image-registry.openshift-image-registry.svc:5000/openshell/keycloak-registrar:latest#${IMAGE}#" \
    -e "s#http://keycloak.openshell.svc.cluster.local#${registrar_keycloak_url}#" \
    -e "s#{name: GATEWAY_ID, value: openshell}#{name: GATEWAY_ID, value: ${RELEASE}}#" \
    -e "s#{name: KEYCLOAK_REALM, value: openshell}#{name: KEYCLOAK_REALM, value: ${REALM}}#" \
    -e "s#\"spiffe://openshell.local/ns/openshell/sa/openshell\"#\"${GATEWAY_SPIFFE_ID}\"#" \
    -e "s#\"spiffe://openshell.local\"#\"${TRUST_DOMAIN}\"#" \
    -e "s#{name: DEFAULT_CLIENT_SCOPES, value: \"\"}#{name: DEFAULT_CLIENT_SCOPES, value: \"${TARGET_API}\"}#" \
    -e "s#secretName: openshell-jwt-keys#secretName: ${RELEASE}-jwt-keys#" \
    "${SRC}/deploy/interceptor.yaml" | oc -n "${NAMESPACE}" apply -f - >/dev/null
oc -n "${NAMESPACE}" rollout restart deploy/keycloak-registrar >/dev/null
oc -n "${NAMESPACE}" rollout status deploy/keycloak-registrar --timeout=180s

log "registering the interceptor with the gateway"
config_map="${RELEASE}-config"
current="$(oc -n "${NAMESPACE}" get cm "${config_map}" -o jsonpath='{.data.gateway\.toml}')"
if ! grep -q 'name             = "keycloak-registrar"' <<<"${current}"; then
    block="$(grep -v '^#' "${SRC}/deploy/gateway-interceptor.toml" \
        | sed -e "s#keycloak-registrar.openshell.svc#keycloak-registrar.${NAMESPACE}.svc#")"
    printf '%s\n\n%s\n' "${current}" "${block}" > "${stage}/gateway.toml"
    oc -n "${NAMESPACE}" create cm "${config_map}" --from-file=gateway.toml="${stage}/gateway.toml" \
        --dry-run=client -o yaml | oc -n "${NAMESPACE}" replace -f - >/dev/null
fi
if ! oc -n "${NAMESPACE}" get sts "${RELEASE}" -o jsonpath='{.spec.template.spec.volumes[*].name}' | grep -qw service-ca; then
    oc -n "${NAMESPACE}" patch sts "${RELEASE}" --type=json -p='[
      {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"service-ca","configMap":{"name":"openshift-service-ca.crt"}}},
      {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":{"name":"service-ca","mountPath":"/etc/openshell-service-ca","readOnly":true}}
    ]' >/dev/null
fi
oc -n "${NAMESPACE}" rollout restart sts/"${RELEASE}" >/dev/null
oc -n "${NAMESPACE}" rollout status sts/"${RELEASE}" --timeout=300s

for _ in $(seq 1 90); do  # up to 3 minutes: OIDC discovery slows gateway start
    # No grep -q: it exits at the first match, oc gets SIGPIPE, and pipefail turns that into a failure.
    if oc -n "${NAMESPACE}" logs "${RELEASE}-0" 2>/dev/null | grep "gateway interceptors initialized" >/dev/null; then
        log "gateway connected to the interceptor (TLS, token, protocol, and audience checks passed)"
        exit 0
    fi
    sleep 2
done
log "ERROR: gateway did not report the interceptor; check: oc -n ${NAMESPACE} logs ${RELEASE}-0 | grep -i interceptor"
exit 1
