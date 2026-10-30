# OpenShell sandboxes with ZTWIM workload identity: SPIFFE token grant and token exchange

> **Warning:** OpenShell on OpenShift is experimental. This demo path runs the gateway with TLS disabled and unauthenticated access. Do not use it in production.

Give AI agent sandboxes cryptographic workload identities instead of static credentials. The [Zero Trust Workload Identity Manager (ZTWIM)](https://docs.redhat.com/en/documentation/openshift_container_platform/html/security_and_compliance/zero-trust-workload-identity-manager) — the OpenShift operator for [SPIFFE](https://spiffe.io/)/[SPIRE](https://spiffe.io/spire/) — issues short-lived SPIFFE identities to the OpenShell gateway and every sandbox. Sandboxes then obtain scoped OAuth access tokens by **proving who they are**, in two ways covered by this guide:

- **Token grant** — the sandbox presents its SPIFFE JWT-SVID as a client assertion and receives an access token scoped to the endpoint it calls (RFC 6749 `client_credentials` with a SPIFFE client).
- **Token exchange** — a stored user subject token is exchanged for endpoint-scoped access tokens through a gateway-intermediated RFC 8693 exchange, so the user's token never reaches the sandbox.

Both flows run against in-cluster demo services (`alpha`/`beta`) that verify the token's audience, scope, and — the zero-trust part — the caller's SPIFFE identity.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  OpenShift cluster (trust domain = cluster domain)                       │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  ZTWIM operator                                                    │  │
│  │   SPIRE server · SPIRE agents · spiffe-csi-driver · OIDC discovery │  │
│  │   default ClusterSPIFFEID: spiffe://<td>/ns/<ns>/sa/<sa>           │  │
│  └────────────┬───────────────────────────────────────────────────────┘  │
│               │ csi.spiffe.io Workload API socket                        │
│  ┌────────────▼───────────────────────────────────────────────────────┐  │
│  │  OpenShell (namespace openshell)                                   │  │
│  │   gateway ── os-supervisor ── sandbox (curl)                       │  │
│  │   identities issued by ZTWIM's SPIRE, not a bundled SPIRE          │  │
│  └────────────┬───────────────────────────────────────────────────────┘  │
│               │ ① SPIFFE JWT-SVID (client assertion)                     │
│               ▼    ② RFC 8693 token exchange (gateway-intermediated)     │
│  ┌──────────────────────────────┐    ┌─────────────────────────────┐     │
│  │  token-issuer /              │───►│ alpha · beta (protected)    │     │
│  │  token-exchange-issuer       │    │ verify aud + scope + azp    │     │
│  │  verify SVID via SPIRE JWKS  │    └─────────────────────────────┘     │
│  └──────────────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────────────┘
```

**Key integration points:**

- **`csi.spiffe.io` socket** — the OpenShell chart mounts the SPIFFE Workload API socket into the gateway and sandbox pods through the CSI driver that ZTWIM provides. OpenShell runs on ZTWIM's SPIRE; no bundled SPIRE is deployed.
- **Default ClusterSPIFFEID** — ZTWIM registers every workload outside its own namespace as `spiffe://<trust-domain>/ns/<namespace>/sa/<serviceaccount>`. The gateway pod and each sandbox get their own identity.
- **`providerTokenGrants.spiffe`** — the chart flag that enables dynamic provider token grants from gateway- and sandbox-scoped JWT-SVIDs.
- **Provider profile `token_grant`** — declares the token endpoint, the JWT-SVID audience, and per-endpoint audience/scope overrides. The OpenShell proxy resolves the credential and exchanges the SVID when the sandbox calls a protected endpoint.
- **SPIRE JWKS** — the demo issuers verify JWT-SVID signatures against ZTWIM's OIDC discovery provider (a managed Route with a publicly trusted certificate).

## Prerequisites

- An OpenShift cluster running version 4.19 or later (RHCOS kernels with Landlock) — this guide was run on OpenShift 4.22.
- You have `cluster-admin` access to the cluster.
- The Red Hat build of Agent Sandbox operator (v0.9.0) is installed — OpenShell's Kubernetes driver depends on it. Verify with `oc get csv -n openshift-operators | grep agent-sandbox`.
- The OpenShift CLI (`oc`), Helm 3.x, `kubectl`, `curl`, `python3`, `openssl`, and `nc` are installed locally.
- The OpenShell CLI (`openshell`) version 0.1.x is installed locally. For installation, see the [OpenShell quickstart](https://docs.nvidia.com/openshell/latest/get-started/quickstart).
- A CNI that enforces ingress and egress `NetworkPolicy` (the OpenShell sandbox network policies are only as good as the CNI behind them).

## 1. Install the ZTWIM operator

The demo ships the setup scripts it was validated with. Run the operator install (from the OpenShift OperatorHub catalog, channel `stable-v1`):

```bash
# Run locally — from demos/ztwim-openshell-spiffe-tokens/
KUBECONFIG=~/.kube/config bash setup/install-ztwim.sh
```

The output should show the CSV `Succeeded`:

```
NAME                                               DISPLAY                               VERSION   REPLACES   PHASE
zero-trust-workload-identity-manager.v1.1.2        Zero Trust Workload Identity Manager  1.1.2                Succeeded
```

> **Note:** the subscription's package name is `openshift-zero-trust-workload-identity-manager`; the CSV inside is `zero-trust-workload-identity-manager.vX.Y.Z`. The script handles both names.

## 2. Configure the SPIRE platform

Register the trust domain (the cluster domain) and deploy the SPIRE server, agents, CSI driver, and OIDC discovery provider:

```bash
# Run locally
KUBECONFIG=~/.kube/config bash setup/configure-ztwim.sh
```

The script applies five custom resources (`ZeroTrustWorkloadIdentityManager`, `SpireServer`, `SpireAgent`, `SpiffeCSIDriver`, `SpireOIDCDiscoveryProvider`) and waits for the platform:

```
[ztwim] Cluster domain: apps.example.com
[ztwim] Trust domain:   apps.example.com
[ztwim] SPIRE server: ready
[ztwim] SPIRE agents: ready
[ztwim] SPIFFE CSI driver: ready
[ztwim] ZTWIM platform is ready.
```

Verify the pods (server 2/2, one agent and one CSI driver pod per node, one OIDC discovery provider):

```bash
# Run locally
oc get pods -n zero-trust-workload-identity-manager
```

```
NAME                                                              READY   STATUS
spire-agent-6xkvm                                                 1/1     Running
spire-server-0                                                    2/2     Running
spire-spiffe-csi-driver-2wlgt                                     2/2     Running
spire-spiffe-oidc-discovery-provider-b599f9dd9-qjm5v              1/1     Running
zero-trust-workload-identity-manager-controller-manager-7bwnpfg   1/1     Running
```

> **Trust domain note:** the SPIRE trust domain is the cluster's ingress domain (e.g. `apps.example.com`), so SPIFFE IDs look like `spiffe://apps.example.com/ns/openshell/sa/openshell`. The demo scripts detect the domain from the cluster, so you do not need to substitute anything.

## 3. Deploy OpenShell on ZTWIM's SPIRE

> **Warning:** this step disables TLS on the gateway and allows unauthenticated access. These settings are acceptable here because the gateway is reached only through a local port-forward, never through an exposed Route.

OpenShell v0.1.6 or later is required — the SPIFFE provider token grant features shipped in v0.1.0, and the Red Hat build images (v0.0.116) predate them.

```bash
# Run locally
oc create namespace openshell

helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.6 \
  --namespace openshell \
  --set podSecurityContext.fsGroup=null \
  --set securityContext.runAsUser=null \
  --set server.disableTls=true \
  --set server.auth.allowUnauthenticatedUsers=true \
  --set server.providerTokenGrants.spiffe.enabled=true \
  --set server.providerTokenGrants.spiffe.workloadApiSocketPath=/run/spire/sockets/spire-agent.sock
```

| Override | Reason |
|---|---|
| `podSecurityContext.fsGroup=null` / `securityContext.runAsUser=null` | Let OpenShift's SCC admission assign UIDs from the namespace range (the chart defaults to 1000, which restricted-v2 rejects). |
| `server.disableTls=true` + `server.auth.allowUnauthenticatedUsers=true` | Demo path — the gateway serves plaintext HTTP behind a local port-forward. |
| `server.providerTokenGrants.spiffe.enabled=true` | Mount the SPIFFE Workload API socket into the gateway and sandbox pods (via `csi.spiffe.io`) for dynamic provider token grants. |
| `server.providerTokenGrants.spiffe.workloadApiSocketPath=/run/spire/sockets/spire-agent.sock` | ZTWIM's CSI driver publishes the node agent socket under this path inside pods. |

Wait for the gateway:

```bash
# Run locally
oc -n openshell rollout status statefulset/openshell
```

## 4. Verify the workload identities

The gateway pod should already be registered with ZTWIM's SPIRE. Show its SPIRE entry:

```bash
# Run locally
oc -n zero-trust-workload-identity-manager exec spire-server-0 -c spire-server \
  -- /spire-server entry show | grep -A7 "ns/openshell"
```

```
SPIFFE ID        : spiffe://apps.example.com/ns/openshell/sa/openshell
Parent ID        : spiffe://apps.example.com/spire/agent/k8s_psat/prelude/<node-uuid>
Selector         : k8s:pod-uid:b9a6e783-1d44-48cb-b11e-19357f3d67d3
```

The SPIFFE ID binds the gateway to its Kubernetes namespace and service account. Every sandbox gets its own identity the same way — you will see it in the token claims later (`azp`).

## 5. Clone the demo files

Clone this repository and change to the demo directory:

```bash
# Run locally
git clone https://github.com/opendatahub-io/agent-ops.git
cd agent-ops/demos/ztwim-openshell-spiffe-tokens
```

The demo includes:

| File | Purpose |
|------|---------|
| `k8s/token-grant/` | `token-issuer` (returns demo access tokens for SPIFFE client assertions) and `alpha`/`beta` protected services |
| `k8s/token-exchange/` | `token-exchange-issuer` (issues a demo subject token, performs the gateway intermediate and supervisor final exchanges) and `alpha-exchange`/`beta-exchange` protected services |
| `provider-profiles/token-grant.yaml` | OpenShell provider profile with a dynamic `token_grant` credential |
| `provider-profiles/token-exchange.yaml` | OpenShell provider profile with a stored `subject_token` and an RFC 8693 `token_exchange` credential |
| `setup/` | ZTWIM install/config/cleanup scripts |
| `demo-token-grant.sh`, `demo-token-exchange.sh` | Automated end-to-end scripts |

The manifests carry a `__CLUSTER_DOMAIN__` placeholder that the scripts substitute with the target cluster's ingress domain, so nothing is hard-coded to one cluster.

## 6. Run the token grant demo

The grant flow: the sandbox supervisor fetches a SPIFFE JWT-SVID from ZTWIM's SPIRE, presents it as a client assertion at the `token-issuer`, and injects the returned access token into the sandbox's outbound HTTP call. Each protected endpoint gets its own audience and scope from the profile's `audience_overrides`.

Run the automated script:

```bash
# Run locally — creates a sandbox from the curl image and deletes it on exit
KUBECONFIG=~/.kube/config bash demo-token-grant.sh
```

Or step through it manually. Deploy the workloads (the secret is generated at runtime):

```bash
# Run locally
ACCESS_TOKEN_SECRET="$(openssl rand -hex 32)"
kubectl -n default create secret generic openshell-spiffe-token-demo \
  --from-literal=access-token-secret="$ACCESS_TOKEN_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

CLUSTER_DOMAIN="$(oc get ingresses.config/cluster -o jsonpath='{.spec.domain}')"
mkdir -p /tmp/token-grant && cp -r k8s/token-grant/. /tmp/token-grant/
sed -i "s/__CLUSTER_DOMAIN__/${CLUSTER_DOMAIN}/g" /tmp/token-grant/workloads.yaml
kubectl apply -k /tmp/token-grant
kubectl -n default rollout status deployment/token-issuer --timeout=180s
kubectl -n default rollout status deployment/alpha --timeout=180s
kubectl -n default rollout status deployment/beta --timeout=180s
```

Register the profile and provider, then create the sandbox (from the `curlimages/curl` image — the default sandbox runtime has no curl):

```bash
# Run locally
oc -n openshell port-forward svc/openshell 8097:8080 2>/dev/null &
export GATEWAY=http://127.0.0.1:8097

openshell --gateway-endpoint "$GATEWAY" profile import -f provider-profiles/token-grant.yaml
openshell --gateway-endpoint "$GATEWAY" provider create \
  --name spiffe-token-demo --type spiffe-token-demo --runtime-credentials
openshell --gateway-endpoint "$GATEWAY" sandbox create \
  --name spiffe-token-demo --provider spiffe-token-demo --no-tty \
  --from docker.io/curlimages/curl:latest
```

> **Note:** no trailing command on `sandbox create` — a completing command would end the sandbox before `sandbox exec` can use it.

Call the protected services from the sandbox:

```bash
# Run locally
openshell --gateway-endpoint "$GATEWAY" sandbox exec \
  --name spiffe-token-demo --no-tty -- curl -sS http://alpha.default.svc.cluster.local/
```

Expected output — the token carries the `alpha` audience/scope and the sandbox's ZTWIM-issued SPIFFE ID:

```
alpha called with path /:
  sub: 0dc6acf3b01ec9cfb993ae05372bc895
  aud: alpha, account
  iss: http://token-issuer.default.svc.cluster.local
  scope: alpha profile email
  azp: spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
  client_id: spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
```

> **Why `azp` matters:** the authorized party is the sandbox's SPIFFE ID — cryptographic proof of *which workload* called the endpoint, issued by ZTWIM's SPIRE and verified by the issuer against SPIRE's JWKS.

The protected services write proof-of-life logs:

```bash
# Run locally
kubectl -n default logs -l app=alpha --tail=5
```

```
alpha accepted request path=/ aud="alpha, account" scope="alpha profile email" client_id=spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
```

## 7. Run the token exchange demo

The exchange flow adds a user identity: a stored `subject_token` (the demo user) is exchanged for endpoint-scoped access tokens through a gateway-intermediated RFC 8693 exchange. The subject token is stored gateway-side only — it is never injected into the sandbox environment.

1. The supervisor fetches its SPIFFE JWT-SVID.
2. The gateway verifies the supervisor SVID, fetches its own gateway JWT-SVID, and exchanges the stored `subject_token` at the `token-exchange-issuer` — the intermediate audience is the supervisor's SPIFFE ID.
3. The supervisor exchanges the intermediate token for the final alpha/beta access token.
4. The final token is injected into the outbound call.

Run the automated script:

```bash
# Run locally
KUBECONFIG=~/.kube/config bash demo-token-exchange.sh
```

Or step through it manually. Deploy the exchange workloads and fetch a demo subject token:

```bash
# Run locally
ACCESS_TOKEN_SECRET="$(openssl rand -hex 32)"
kubectl -n default create secret generic openshell-spiffe-token-exchange-demo \
  --from-literal=access-token-secret="$ACCESS_TOKEN_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

CLUSTER_DOMAIN="$(oc get ingresses.config/cluster -o jsonpath='{.spec.domain}')"
mkdir -p /tmp/token-exchange && cp -r k8s/token-exchange/. /tmp/token-exchange/
sed -i "s/__CLUSTER_DOMAIN__/${CLUSTER_DOMAIN}/g" /tmp/token-exchange/workloads.yaml
kubectl apply -k /tmp/token-exchange
kubectl -n default rollout status deployment/token-exchange-issuer --timeout=180s
kubectl -n default rollout status deployment/alpha-exchange --timeout=180s
kubectl -n default rollout status deployment/beta-exchange --timeout=180s

oc -n default port-forward svc/token-exchange-issuer 18080:80 2>/dev/null &
SUBJECT_TOKEN="$(curl -fsS http://127.0.0.1:18080/demo-subject-token |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')"
```

Register the exchange profile (the subject token is stored as a provider credential) and create the sandbox — note the sandbox name must stay at 19 characters or fewer (the Kubernetes driver's pod-naming limit), while the provider/profile ids match the profile yaml:

```bash
# Run locally
openshell --gateway-endpoint "$GATEWAY" profile import -f provider-profiles/token-exchange.yaml
openshell --gateway-endpoint "$GATEWAY" provider create \
  --name spiffe-token-exchange-demo --type spiffe-token-exchange-demo \
  --credential "subject_token=${SUBJECT_TOKEN}"
openshell --gateway-endpoint "$GATEWAY" sandbox create \
  --name spiffe-exchange --provider spiffe-token-exchange-demo --no-tty \
  --from docker.io/curlimages/curl:latest
```

Call the protected services:

```bash
# Run locally
openshell --gateway-endpoint "$GATEWAY" sandbox exec \
  --name spiffe-exchange --no-tty -- curl -sS http://alpha-exchange.default.svc.cluster.local/
```

Expected output — `sub` is the demo user from the exchanged subject token, `azp`/`client_id` the sandbox's SPIFFE ID:

```
alpha called with path /:
  sub: demo-user
  aud: alpha, account
  iss: http://token-exchange-issuer.default.svc.cluster.local
  scope: alpha profile email
  azp: spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
  client_id: spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
```

The token issuer logs both exchange phases — the intermediate token's audience is the supervisor's SPIFFE ID:

```bash
# Run locally
kubectl -n default logs -l app=token-exchange-issuer --tail=10
```

```
issued intermediate token for user=demo-user audience=spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
issued final token for user=demo-user audience=alpha client=spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
issued final token for user=demo-user audience=beta client=spiffe://apps.example.com/ns/openshell/sa/openshell-sandbox
```

## 8. Inspect the sandbox identity (optional)

Show how ZTWIM registered the sandbox workload — the SPIRE entries appear per pod UID:

```bash
# Run locally (with a sandbox running)
oc -n zero-trust-workload-identity-manager exec spire-server-0 -c spire-server \
  -- /spire-server entry show | grep -c "spiffe://"
```

## Cleanup

Remove the demo workloads, sandbox, provider, and profile (keeps OpenShell and ZTWIM):

```bash
# Run locally
KUBECONFIG=~/.kube/config bash setup/cleanup.sh
```

Remove everything, including OpenShell and ZTWIM:

```bash
# Run locally
KUBECONFIG=~/.kube/config bash setup/cleanup.sh --all
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Gateway pod `FailedCreate`: `runAsUser: Invalid value: 1000` | Chart's default UID rejected by restricted-v2 | Install with `podSecurityContext.fsGroup=null --set securityContext.runAsUser=null` (step 3) |
| Subscription `ConstraintsNotSatisfiable` | Wrong package name | The catalog package is `openshift-zero-trust-workload-identity-manager` (step 1) |
| `sandbox ... is not ready (phase: Completed)` | The create command's trailing command finished, ending the sandbox | Create without a trailing command (step 6) |
| `curl: command not found` in the sandbox | The default sandbox runtime image has no curl | Create the sandbox with `--from docker.io/curlimages/curl:latest` (step 6) |
| `name exceeds maximum length (N > 19)` | Sandbox name over the driver's pod-naming limit | Keep the sandbox name ≤ 19 chars (step 7) |
| `unknown tls value` on `profile lint` | The profile schema changed — `tls` is automatic now | Omit the `tls` field from profile endpoints |
| `provider profile '<id>' not found` on `provider create` | `--type` does not match the profile yaml's `id` | Pass the profile yaml's `id` to `--type` (step 7) |
| `JWT-SVID subject was not an OpenShell sandbox SPIFFE ID` | `TRUST_DOMAIN_PREFIX` does not match the cluster's SPIFFE IDs | The manifests substitute `__CLUSTER_DOMAIN__` at apply time — verify the sed step ran (step 6) |
| `no JWKS key for kid ...` / `JWKS fetch failed` | The OIDC discovery Route is not up | Check `oc get route -n zero-trust-workload-identity-manager` and the provider pod |
| Sandbox `transport error` on exec | Port-forward not ready | Wait for `nc -z 127.0.0.1 8097` before using the CLI |

## References

- [ZTWIM — Zero Trust Workload Identity Manager (OpenShift docs)](https://docs.redhat.com/en/documentation/openshift_container_platform/html/security_and_compliance/zero-trust-workload-identity-manager)
- [OpenShell documentation](https://docs.nvidia.com/openshell/latest) — [OpenShift install](https://docs.nvidia.com/openshell/latest/kubernetes/openshift), [SPIFFE provider token grants](https://github.com/NVIDIA/OpenShell/tree/main/examples/spiffe-token-grant-demo), [SPIFFE token exchange](https://github.com/NVIDIA/OpenShell/tree/main/examples/spiffe-token-exchange-demo)
- [SPIFFE](https://spiffe.io/) / [SPIRE](https://spiffe.io/spire/) — the workload identity standard and its runtime; [RFC 8693](https://datatracker.ietf.org/doc/html/rfc8693) — OAuth 2.0 token exchange
- **User authentication for the gateway** — this demo covers *workload* identity. For *user* identity (Keycloak OIDC, workspace membership, role-based access to gateways and dashboards), see [guides/user-auth-keycloak.md](../../guides/user-auth-keycloak.md).
