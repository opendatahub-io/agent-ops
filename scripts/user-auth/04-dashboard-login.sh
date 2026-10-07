#!/usr/bin/env bash
# Step 4: serve the dashboard behind a login.
#
#   browser -> Route (TLS) -> oauth2-proxy :4180 -> dashboard BFF 127.0.0.1:8080 -> gateway (user's token)
#
# oauth2-proxy signs the user in at Keycloak (client openshell-dashboard), keeps the session in a
# cookie, refreshes the access token before it expires, and forwards it to the BFF in
# X-Forwarded-Access-Token. The BFF relays that token to the gateway, which decides what the user
# may do. The proxy also refuses users without this gateway's admin or user role, so a person from
# another gateway's groups never reaches the dashboard. The BFF listens on loopback, a NetworkPolicy
# admits only the OpenShift router to the proxy, and the BFF gets only the gateway CA: no client
# certificate, so it cannot act as anyone but the signed-in user.
set -euo pipefail
. "$(dirname "$0")/env.sh"

# Private CA: oauth2-proxy trusts it in addition to the system roots (ConfigMap from step 3).
CA_ARGS=""; CA_MOUNT=""; CA_VOLUME=""
if [[ -n "${KEYCLOAK_CA_FILE}" ]]; then
    CA_ARGS=$'\n            - --provider-ca-file=/etc/keycloak-ca/ca.crt\n            - --use-system-trust-store=true'
    CA_MOUNT=$'\n          volumeMounts: [{name: keycloak-ca, mountPath: /etc/keycloak-ca, readOnly: true}]'
    CA_VOLUME=$'\n        - name: keycloak-ca\n          configMap: {name: keycloak-ca}'
fi

DASHBOARD_IMAGE="${DASHBOARD_IMAGE:-quay.io/gkrumbach07/openshell-dashboard:1.2.0}"
OAUTH2_PROXY_IMAGE="${OAUTH2_PROXY_IMAGE:-quay.io/oauth2-proxy/oauth2-proxy:v7.15.5}"

# Replace an evaluation install (`make dashboard`, port 8080, no login) rather than merging into it.
if [[ "$(oc -n "${NAMESPACE}" get svc openshell-dashboard -o jsonpath='{.spec.ports[0].port}' 2>/dev/null)" == "8080" ]]; then
    oc -n "${NAMESPACE}" delete deploy/openshell-dashboard svc/openshell-dashboard --ignore-not-found >/dev/null
fi

oc -n "${NAMESPACE}" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: openshell-dashboard
  labels: {app: openshell-dashboard}
spec:
  replicas: 1
  selector: {matchLabels: {app: openshell-dashboard}}
  template:
    metadata: {labels: {app: openshell-dashboard}}
    spec:
      containers:
        - name: oauth2-proxy
          image: ${OAUTH2_PROXY_IMAGE}
          args:
            - --provider=keycloak-oidc
            - --oidc-issuer-url=${ISSUER}
            - --client-id=${DASHBOARD_CLIENT}
            - --allowed-role=${API_CLIENT}:admin
            - --allowed-role=${API_CLIENT}:user
            - --redirect-url=https://${DASHBOARD_HOST}/oauth2/callback
            - --upstream=http://127.0.0.1:8080/
            - --http-address=0.0.0.0:4180
            - --scope=openid profile email
            - --code-challenge-method=S256
            - --pass-access-token=true
            - --pass-user-headers=true
            - --email-domain=*
            - --reverse-proxy=true
            - --skip-provider-button=true
            - --api-route=^/api/
            - --cookie-secure=true
            - --cookie-samesite=lax
            - --cookie-name=_openshell_${NAMESPACE}
            - --cookie-refresh=4m${CA_ARGS}
          env:
            - name: OAUTH2_PROXY_CLIENT_SECRET
              valueFrom: {secretKeyRef: {name: openshell-dashboard-oidc, key: client-secret}}
            - name: OAUTH2_PROXY_COOKIE_SECRET
              valueFrom: {secretKeyRef: {name: openshell-dashboard-oidc, key: cookie-secret}}
          ports: [{containerPort: 4180, name: http}]${CA_MOUNT}
          readinessProbe: {httpGet: {path: /ping, port: 4180}, periodSeconds: 5}
          securityContext: &restricted
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
            runAsNonRoot: true
            seccompProfile: {type: RuntimeDefault}
        - name: dashboard
          image: ${DASHBOARD_IMAGE}
          env:
            - {name: LISTEN_ADDRESS, value: "127.0.0.1"}
            - {name: OPENSHELL_GATEWAY_URL, value: "https://openshell.${NAMESPACE}.svc:8080"}
            - {name: GATEWAY_CA_CERT, value: /etc/openshell/gateway-ca/ca.crt}
            - {name: AUTH_USER_HEADER, value: x-forwarded-user}
            - {name: ADMIN_ROLE, value: admin}
          volumeMounts: [{name: gateway-ca, mountPath: /etc/openshell/gateway-ca, readOnly: true}]
          securityContext: *restricted
      volumes:
        - name: gateway-ca
          secret: {secretName: openshell-client-tls, items: [{key: ca.crt, path: ca.crt}]}${CA_VOLUME}
---
apiVersion: v1
kind: Service
metadata: {name: openshell-dashboard}
spec:
  selector: {app: openshell-dashboard}
  ports: [{name: http, port: 4180, targetPort: 4180}]
---
apiVersion: route.openshift.io/v1
kind: Route
metadata: {name: openshell-dashboard}
spec:
  host: ${DASHBOARD_HOST}
  to: {kind: Service, name: openshell-dashboard}
  port: {targetPort: http}
  tls: {termination: edge, insecureEdgeTerminationPolicy: Redirect}
---
# Only the OpenShift router may open connections to the dashboard pod (the proxy port).
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: openshell-dashboard}
spec:
  podSelector: {matchLabels: {app: openshell-dashboard}}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: {policy-group.network.openshift.io/ingress: ""}
      ports: [{port: 4180, protocol: TCP}]
EOF
oc -n "${NAMESPACE}" rollout status deploy/openshell-dashboard --timeout=180s
log "dashboard: https://${DASHBOARD_HOST} (users need a group: grant.sh <user> <workspace>)"
