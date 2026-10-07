#!/usr/bin/env bash
# Step 3 (test clusters): run a Red Hat build of Keycloak 26.6 instance with SPIFFE
# client authentication enabled. Dev mode, in-memory database: for evaluation only.
# In production, use the RHBK operator (channel stable-v26.6 or later) and set the
# same features and hostname on the Keycloak CR.
set -euo pipefail
. "$(dirname "$0")/env.sh"

# Product image from the rhbk-operator stable-v26.6 bundle (26.6.7).
KEYCLOAK_IMAGE="${KEYCLOAK_IMAGE:-registry.redhat.io/rhbk/keycloak-rhel9@sha256:b55a3fdf5c836cca9d6b754dd27adbd8de28a27ca46e7d205ceb6c0d7ae35ab9}"

oc create namespace "${KEYCLOAK_NAMESPACE}" --dry-run=client -o yaml | oc apply -f - >/dev/null
if ! oc -n "${KEYCLOAK_NAMESPACE}" get secret keycloak-admin >/dev/null 2>&1; then
    oc -n "${KEYCLOAK_NAMESPACE}" create secret generic keycloak-admin \
        --from-literal=password="$(openssl rand -hex 16)" >/dev/null
    log "admin password stored in Secret ${KEYCLOAK_NAMESPACE}/keycloak-admin"
fi

oc -n "${KEYCLOAK_NAMESPACE}" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
spec:
  replicas: 1
  selector:
    matchLabels: {app: keycloak}
  template:
    metadata:
      labels: {app: keycloak}
    spec:
      containers:
        - name: keycloak
          image: ${KEYCLOAK_IMAGE}
          args:
            - start-dev
            - --features=spiffe,client-auth-federated
            - --hostname=${KEYCLOAK_URL}
            - --http-port=8080
            - --proxy-headers=xforwarded   # behind an edge-terminated Route (scripts/user-auth)
            # RHBK 26.6 trusts the OpenShift service CA by default, which lets it fetch the
            # SPIRE JWKS. On 26.4, add:
            #   --truststore-paths=/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt
          env:
            - name: KC_BOOTSTRAP_ADMIN_USERNAME
              value: admin
            - name: KC_BOOTSTRAP_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef: {name: keycloak-admin, key: password}
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet: {path: /realms/master, port: 8080}
            initialDelaySeconds: 20
            periodSeconds: 5
          resources:
            requests: {cpu: 250m, memory: 768Mi}
            limits: {memory: 1536Mi}
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak
spec:
  selector: {app: keycloak}
  ports:
    - name: http
      port: 80
      targetPort: 8080
EOF
oc -n "${KEYCLOAK_NAMESPACE}" rollout status deploy/keycloak --timeout=300s
log "Keycloak is running at ${KEYCLOAK_URL}"
