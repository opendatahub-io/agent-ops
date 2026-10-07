#!/usr/bin/env bash
# Step 6: end-to-end check. Deploys a small "whoami" API that verifies Keycloak tokens
# (JWKS, audience ${TARGET_API}), creates an OpenShell provider holding the demo user's
# token, starts a sandbox, and calls the API from inside it without any credential.
# Requires the openshell CLI connected to the gateway (see install/01-install.md).
set -euo pipefail
. "$(dirname "$0")/env.sh"
SANDBOX="${SANDBOX_NAME:-token-exchange-demo}"
PROFILE_ID="${TARGET_API}-keycloak"
PROVIDER="${TARGET_API}-${DEMO_USER}"
API_HOST="${TARGET_API}.${NAMESPACE}.svc.cluster.local"
work="$(mktemp -d)"; trap 'rm -rf "${work}"' EXIT

log "deploying the ${TARGET_API} demo API"
oc -n "${NAMESPACE}" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${TARGET_API}
data:
  server.py: |
    import json, os, jwt
    from http.server import BaseHTTPRequestHandler, HTTPServer
    ISSUER = os.environ["ISSUER"]; AUDIENCE = os.environ["AUDIENCE"]
    JWKS = jwt.PyJWKClient(os.environ["JWKS_URL"])
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            auth = self.headers.get("Authorization", "")
            try:
                token = auth.removeprefix("Bearer ")
                claims = jwt.decode(token, JWKS.get_signing_key_from_jwt(token).key, algorithms=["RS256"],
                                    audience=AUDIENCE, issuer=ISSUER)
                code, body = 200, {k: claims.get(k) for k in ("preferred_username", "azp", "aud", "iss")}
            except Exception as e:
                code, body = 401, {"error": type(e).__name__ + ": " + str(e)}
            data = json.dumps(body, indent=2).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", 8080), H).serve_forever()
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${TARGET_API}
spec:
  replicas: 1
  selector: {matchLabels: {app: ${TARGET_API}}}
  template:
    metadata: {labels: {app: ${TARGET_API}}}
    spec:
      containers:
        - name: api
          image: registry.access.redhat.com/ubi9/python-312:latest
          command: ["sh", "-c", "pip install -q --disable-pip-version-check 'pyjwt[crypto]' && exec python3 -u /app/server.py"]
          env:
            - {name: ISSUER, value: "${ISSUER}"}
            - {name: AUDIENCE, value: "${TARGET_API}"}
            - {name: JWKS_URL, value: "${REALM_INTERNAL_URL}/protocol/openid-connect/certs"}
          ports: [{containerPort: 8080}]
          volumeMounts: [{name: app, mountPath: /app}]
      volumes: [{name: app, configMap: {name: ${TARGET_API}}}]
---
apiVersion: v1
kind: Service
metadata:
  name: ${TARGET_API}
spec:
  selector: {app: ${TARGET_API}}
  ports: [{name: http, port: 80, targetPort: 8080}]
EOF
oc -n "${NAMESPACE}" rollout status deploy/"${TARGET_API}" --timeout=240s >/dev/null

# Re-runs: remove the previous demo in dependency order (a profile cannot be deleted
# while a provider attached to a sandbox uses it).
openshell sandbox delete "${SANDBOX}" >/dev/null 2>&1 || true
openshell provider delete "${PROVIDER}" >/dev/null 2>&1 || true
openshell profile delete "${PROFILE_ID}" >/dev/null 2>&1 || true

log "importing provider profile ${PROFILE_ID}"
cat > "${work}/profile.yaml" <<EOF
id: ${PROFILE_ID}
display_name: ${TARGET_API} through Keycloak token exchange
category: data
credentials:
  - name: subject_token
    description: User access token, used by the gateway only and never injected into the sandbox
    required: true
  - name: access_token
    auth_style: bearer
    header_name: Authorization
    token_grant:
      grant_type: token_exchange
      token_endpoint: ${REALM_INTERNAL_URL}/protocol/openid-connect/token
      audience: ${TARGET_API}
      jwt_svid_audience: ${ISSUER}
      client_assertion_type: urn:ietf:params:oauth:client-assertion-type:jwt-spiffe
      cache_ttl_seconds: 60
      subject_token:
        source: provider_credential
        credential: subject_token
        subject_token_type: urn:ietf:params:oauth:token-type:access_token
endpoints:
  - host: ${API_HOST}
    port: 80
    protocol: rest
    access: read-only
    enforcement: enforce
binaries: [/usr/bin/curl]
EOF
openshell profile import -f "${work}/profile.yaml" >/dev/null

log "creating provider ${PROVIDER} with ${DEMO_USER}'s token"
demo_pw="$(oc -n "${NAMESPACE}" get secret keycloak-demo-user -o jsonpath='{.data.password}' | base64 -d)"
user_token="$(oc -n "${NAMESPACE}" exec deploy/"${TARGET_API}" -- curl -fsS "${REALM_INTERNAL_URL}/protocol/openid-connect/token" \
    -d grant_type=password -d client_id=openshell-user -d username="${DEMO_USER}" \
    --data-urlencode "password=${demo_pw}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
openshell provider create --name "${PROVIDER}" --type "${PROFILE_ID}" \
    --credential "subject_token=${user_token}" >/dev/null

log "starting sandbox ${SANDBOX} and calling ${API_HOST} from inside it"
openshell sandbox create --name "${SANDBOX}" --from registry.access.redhat.com/ubi9/ubi-minimal:latest \
    --provider "${PROVIDER}" --no-auto-providers --detach >/dev/null
openshell sandbox exec --name "${SANDBOX}" --no-tty -- curl -sS "http://${API_HOST}/"
echo
log "expected: preferred_username=${DEMO_USER}, azp=${TRUST_DOMAIN}/openshell/sandbox/${NAMESPACE}.os-supervisor-<sandbox-id>.<sandbox-id>"
log "clean up with: openshell sandbox delete ${SANDBOX}"
