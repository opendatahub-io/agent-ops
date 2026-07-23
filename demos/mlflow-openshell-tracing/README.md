# MLflow tracing from OpenShell sandboxes on RHOAI

> **Warning:** OpenShell on OpenShift is experimental. This install path requires a privileged Security Context Constraint (SCC) and runs with TLS disabled on the gateway. Do not use it in production.

Capture MLflow traces from AI agents running in [OpenShell](https://docs.nvidia.com/openshell/latest) sandboxes. Traces are stored in the MLflow instance managed by Red Hat OpenShift AI (RHOAI), so no separate tracking server is required.

This guide covers the full path: installing Agent Sandbox and OpenShell, configuring inference routing, exposing MLflow through a reencrypt route, and running a traced agent inside a sandbox.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  OpenShift Cluster                                               │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  OpenShell Sandbox                                          │ │
│  │                                                             │ │
│  │   agent.py                                                  │ │
│  │     │                                                       │ │
│  │     ├── OpenAI SDK ──► inference.local ──► LLM Provider     │ │
│  │     │                   (OpenShell proxy)    (MaaS / vLLM)  │ │
│  │     │                                                       │ │
│  │     └── mlflow.openai.autolog()                             │ │
│  │              │                                              │ │
│  └──────────────┼──────────────────────────────────────────────┘ │
│                 │  MLFLOW_TRACKING_URI (via --env)                │
│                 ▼                                                 │
│  ┌────────────────────────────┐                                  │
│  │  MLflow Tracking Server    │◄── reencrypt Route               │
│  │  (RHOAI managed)           │                                  │
│  │  redhat-ods-applications   │                                  │
│  └────────────────────────────┘                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Key integration points:**

- **inference.local** — OpenShell intercepts OpenAI SDK calls and routes them through the configured inference provider. The agent code never touches model credentials directly.
- **mlflow.openai.autolog()** — MLflow auto-instruments all OpenAI SDK calls, capturing request/response pairs as traces with zero code changes.
- **MLFLOW_TRACKING_URI** — Passed into the sandbox via `--env` so the MLflow SDK can send traces to the tracking server.
- **Network policy** — Sandbox egress is denied by default. Use `openshell policy update` to allow traffic to the MLflow route.

## Prerequisites

- An OpenShift cluster running version 4.19 or later is available.
- You have `cluster-admin` access to the cluster.
- RHOAI is installed on the cluster. For installation, see the [RHOAI documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed).
- The MLflow Tracking Server is deployed as part of RHOAI.
- An inference model provider is configured (MaaS, vLLM, or another OpenAI-compatible endpoint).
- The OpenShift CLI (`oc`) is installed and authenticated to the cluster.
- The OpenShell CLI (`openshell`) version 0.0.85 is installed locally. For installation, see the [OpenShell quickstart](https://docs.nvidia.com/openshell/latest/get-started/quickstart).
- The Helm CLI (`helm`) is installed.

## 1. Install Agent Sandbox Custom Resource Definitions (CRDs)

> **Note:** Steps 1–3 install OpenShell on OpenShift. If OpenShell is already running on your cluster, skip to [step 4](#4-expose-mlflow-via-a-reencrypt-route). This guide uses `server.disableTls=true` with a local port-forward rather than the mTLS-over-Route approach in the [OpenShell getting-started guide](https://docs.nvidia.com/openshell/latest/get-started/quickstart) — a simpler setup for demos that avoids certificate management.

OpenShell requires the [Agent Sandbox](https://agent-sandbox.sigs.k8s.io) Kubernetes SIG project. Install the CRDs and controller before the OpenShell chart:

```bash
# Run locally — applies upstream SIG manifests to the cluster
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.5.2/sandbox.yaml
```

Confirm that the controller is running:

```bash
# Run locally
oc -n agent-sandbox-system get pods
```

> **Note:** `kubectl apply` is used above because this is an upstream Kubernetes SIG artifact. All subsequent commands use `oc`.

## 2. Deploy OpenShell on OpenShift

> **Warning:** This step disables TLS on the gateway and allows unauthenticated access. These settings are acceptable here because the gateway is accessed only through a local port-forward (`localhost:8080`), not through an externally exposed Route. If you need a production-grade install with mTLS over a Route, see the [OpenShell getting-started guide](https://docs.nvidia.com/openshell/latest/get-started/quickstart).

Create the namespace and grant the required Security Context Constraint (SCC):

```bash
# Run locally
oc create ns openshell

oc adm policy add-scc-to-user privileged \
  -z openshell-sandbox -n openshell
```

Install the OpenShell Helm chart with OpenShift overrides:

```bash
# Run locally
helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.0.85 \
  --namespace openshell \
  --set server.disableTls=true \
  --set podSecurityContext.fsGroup=null \
  --set securityContext.runAsUser=null \
  --set server.auth.allowUnauthenticatedUsers=true
```

Wait for the gateway to be ready:

```bash
# Run locally
oc -n openshell rollout status statefulset/openshell
```

Set up local port-forwarding and register the gateway:

```bash
# Run locally
oc -n openshell port-forward svc/openshell 8080:8080 &
PORT_FORWARD_PID=$!
openshell gateway add http://127.0.0.1:8080 --local --name openshift
```

Verify that the gateway is registered and reachable:

```bash
# Run locally
openshell gateway list
```

You should see the `openshift` gateway listed with a `connected` status.

## 3. Configure inference routing

Enable the v2 provider pipeline:

```bash
# Run locally
openshell settings set --global --key providers_v2_enabled --value true --yes
```

Register your model provider. Example using a direct vLLM or OpenAI-compatible endpoint:

```bash
# Run locally
openshell provider create \
  --name my-provider \
  --type openai \
  --credential OPENAI_API_KEY=<your-api-key> \
  --config OPENAI_BASE_URL=<endpoint>/v1

openshell inference set --provider my-provider --model <model-name>
```

> **Note:** The `--type openai` flag specifies the OpenAI-compatible API protocol, not the OpenAI vendor. Use it for any provider that serves the OpenAI chat completions API (MaaS, vLLM, TGI, etc.).

Verify that inference works:

```bash
# Run locally — creates a temporary sandbox
openshell sandbox create -- python3 -c "
from openai import OpenAI
client = OpenAI(api_key='unused', base_url='https://inference.local/v1')
r = client.chat.completions.create(model='router', messages=[{'role':'user','content':'hello'}])
print(r.choices[0].message.content)
"
```

> **Note:** `model='router'` is an OpenShell routing alias. The gateway maps it to whichever model you configured with `openshell inference set`. You do not need to substitute your model name here.

## 4. Expose MLflow via a reencrypt route

The MLflow service on RHOAI uses TLS internally (port 8443 with a service-serving certificate). Sandboxes access it through the OpenShell proxy, which terminates outbound TLS — this requires a **reencrypt** route so the OpenShift router re-encrypts traffic to the backend service.

Get the service-signing CA certificate:

```bash
# Run locally
oc get configmap -n openshift-service-ca signing-cabundle \
  -o jsonpath='{.data.ca-bundle\.crt}' > /tmp/mlflow-ca.crt
```

Create the reencrypt route:

```bash
# Run locally
oc -n redhat-ods-applications create route reencrypt mlflow \
  --service=mlflow \
  --port=8443 \
  --dest-ca-cert=/tmp/mlflow-ca.crt \
  --insecure-policy=Redirect
```

Note the route hostname:

```bash
# Run locally
export MLFLOW_ROUTE=$(oc -n redhat-ods-applications get route mlflow -o jsonpath='{.spec.host}')
echo "https://$MLFLOW_ROUTE"
```

Verify that the route works:

```bash
# Run locally
curl -sk -H "Authorization: Bearer $(oc whoami -t)" \
  -H "X-MLflow-Workspace: default" \
  "https://$MLFLOW_ROUTE/api/2.0/mlflow/experiments/search?max_results=10"
```

> **Why reencrypt?** A passthrough route preserves the service's self-signed TLS certificate, which the OpenShell proxy cannot validate during its CONNECT tunnel. An edge route strips TLS but cannot re-encrypt to the backend's 8443 port. A reencrypt route handles both sides: the router presents a trusted certificate to clients and re-encrypts to the backend using the service CA. See [Troubleshooting](#troubleshooting) if you encounter `ConnectionResetError` or `502 Bad Gateway`.

## 5. Clone the demo files

Clone this repository and change to the demo directory:

```bash
# Run locally
git clone https://github.com/opendatahub-io/agent-ops.git
cd agent-ops/demos/mlflow-openshell-tracing
```

The demo includes:

| File | Purpose |
|------|---------|
| `agent.py` | Demo agent that uses `mlflow.openai.autolog()` to capture all OpenAI SDK calls as MLflow traces. Calls `inference.local` for LLM access and injects the `X-MLflow-Workspace` header for RHOAI multi-tenancy. |
| `pyproject.toml` | Python dependencies (openai, mlflow) |
| `sandbox-policy.yaml` | Reference network policy (prefer `openshell policy update` for live sandboxes) |
| `verify-traces.py` | Post-run trace verification script |

## 6. Create the sandbox with MLflow environment variables

Pass `MLFLOW_TRACKING_URI` and authentication credentials into the sandbox using `--env`:

```bash
# Run locally — from the mlflow-openshell-tracing/ directory
openshell sandbox create \
  --name mlflow-demo \
  --env MLFLOW_TRACKING_URI=https://$MLFLOW_ROUTE \
  --env MLFLOW_TRACKING_TOKEN=$(oc whoami -t) \
  --env MLFLOW_EXPERIMENT_NAME=openshell-tracing-demo \
  --env MLFLOW_TRACKING_INSECURE_TLS=true \
  --env MLFLOW_WORKSPACE=default \
  --upload agent.py:/sandbox/agent.py
```

> **Note:** `MLFLOW_TRACKING_INSECURE_TLS=true` disables TLS certificate validation (CWE-295). This is acceptable for a demo where the route certificate is signed by the cluster's ingress CA, which is not in the system trust store. In production, use `MLFLOW_TRACKING_SERVER_CERT_PATH` with the ingress CA bundle instead.

Verify that the sandbox is running:

```bash
# Run locally
openshell sandbox list
```

> **Why `--env` and not `--credential`?**
>
> OpenShell's `--credential` flag injects opaque placeholder tokens that are resolved only in HTTP request headers, query parameters, and URL paths by the proxy. The MLflow Python SDK reads `MLFLOW_TRACKING_URI` to make direct TCP connections to the tracking server — it needs the real URL value, not a proxy placeholder. Use `--env` for any environment variable that the application reads directly.

## 7. Add MLflow network policy

Sandbox egress is denied by default. Add the MLflow route to the sandbox's network policy so that the agent can send traces to the tracking server:

```bash
# Run locally
openshell policy update mlflow-demo \
  --add-endpoint $MLFLOW_ROUTE:443 \
  --binary /sandbox/.venv/bin/python3 \
  --wait
```

Verify that the endpoint was added:

```bash
# Run locally
openshell policy get mlflow-demo
```

> **Note:** `inference.local` traffic is handled automatically by the OpenShell proxy and does not need a network policy entry. PyPI access for `uv pip install` is allowed by default. See [Troubleshooting](#troubleshooting) if you get `403 Forbidden` or `ProxyError` when the agent tries to reach MLflow.

## 8. Run the agent

Install dependencies inside the sandbox:

```bash
# Run locally
openshell sandbox exec --name mlflow-demo -- \
  uv pip install openai "mlflow>=3.11.1"
```

### Option A: One-shot run

```bash
# Run locally
openshell sandbox exec --name mlflow-demo -- python3 /sandbox/agent.py
```

### Option B: Interactive sandbox shell

Launch the OpenShell terminal UI:

```bash
# Run locally
openshell term
```

In the terminal UI, select the `mlflow-demo` sandbox from the list and press Enter to open a shell session. Then run the agent:

```
# Inside the sandbox
sandbox@mlflow-demo:~$ python3 /sandbox/agent.py
```

For more detail on navigating the terminal UI, see the [OpenShell quickstart](https://docs.nvidia.com/openshell/latest/get-started/quickstart).

### Expected output

```
[...] MLflow tracing enabled — https://mlflow-...com (experiment: openshell-tracing-demo, workspace: default)
[...] Sending chat completion request via inference.local ...
[...] HTTP Request: POST https://inference.local/v1/chat/completions "HTTP/1.1 200 OK"

--- Agent Response ---
Container sandboxes enhance AI agent security by isolating the agent's
execution environment from the host system, preventing unauthorized access
to sensitive data or system resources. ...

Model: qwen3-14b
Tokens: 36 prompt, 344 completion
[...] Trace sent to MLflow. Check the experiment UI to verify.
```

## 9. Verify traces in MLflow

### Via the MLflow UI

1. Open the MLflow route in your browser (`https://$MLFLOW_ROUTE`)
2. Select the **default** workspace from the dropdown (top-left)
3. Click **Experiments** in the left sidebar, then select **openshell-tracing-demo**
4. Click **Traces** under Observability to see individual traces with request/response pairs, token counts, and execution times

### Programmatically

Run the verification script from outside the sandbox (from the `mlflow-openshell-tracing/` directory):

```bash
# Run locally — from the mlflow-openshell-tracing/ directory
export MLFLOW_TRACKING_URI=https://$(oc -n redhat-ods-applications get route mlflow -o jsonpath='{.spec.host}')
export MLFLOW_TRACKING_TOKEN=$(oc whoami -t)
export MLFLOW_TRACKING_INSECURE_TLS=true

uv run verify-traces.py --experiment openshell-tracing-demo --workspace default
```

Expected output:

```
Experiment: openshell-tracing-demo (ID: 1)

Found 1 trace(s):

  Request ID     : tr-12fd15fd068d71414c1b75a4800009c5
  Status         : OK
  Timestamp      : 1784639093629
  Duration       : 9704ms

Traces verified successfully.
```

## Cleanup

To remove the resources created by this guide:

```bash
# Run locally

# Delete the sandbox
openshell sandbox delete mlflow-demo

# Delete the MLflow route from the RHOAI namespace
oc -n redhat-ods-applications delete route mlflow

# Uninstall OpenShell
helm uninstall openshell -n openshell

# Remove the SCC binding and namespace
oc adm policy remove-scc-from-user privileged -z openshell-sandbox -n openshell
oc delete ns openshell

# Remove the Agent Sandbox CRDs
kubectl delete -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.5.2/sandbox.yaml

# Stop the background port-forward
kill "$PORT_FORWARD_PID" 2>/dev/null || true
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MLFLOW_TRACKING_URI` empty inside sandbox | Variable not passed | Use `--env MLFLOW_TRACKING_URI=...` on `openshell sandbox create` (step 6) |
| `403 Forbidden` / `ProxyError` to MLflow | Network policy missing | Run `openshell policy update <sandbox> --add-endpoint <mlflow-route>:443 --wait` (step 7) |
| `502 Bad Gateway` on MLflow route | Wrong route type | Use a **reencrypt** route, not edge or passthrough — MLflow uses TLS internally (step 4) |
| `ConnectionResetError` to MLflow | Passthrough route + proxy conflict | Switch to a reencrypt route with `--dest-ca-cert` (step 4) |
| `Workspace context is required` | Missing workspace header | Set `MLFLOW_WORKSPACE=default` env var — `agent.py` injects the header automatically |
| `UNAUTHENTICATED` from MLflow | Missing bearer token | Pass `--env MLFLOW_TRACKING_TOKEN=$(oc whoami -t)` (step 6) |
| `inference.local` connection refused | Inference not configured | Run `openshell inference set --provider <name> --model <model>` (step 3) |
| `ModuleNotFoundError: mlflow` | Dependencies not installed | Run `uv pip install mlflow>=3.11.1` inside the sandbox (step 8) |

## Environment variables reference

| Variable | Required | Description |
|----------|----------|-------------|
| `MLFLOW_TRACKING_URI` | Yes | MLflow reencrypt route URL |
| `MLFLOW_TRACKING_TOKEN` | Yes | OpenShift bearer token (`oc whoami -t`) for MLflow authentication |
| `MLFLOW_EXPERIMENT_NAME` | No | Experiment name (default: `openshell-tracing-demo`) |
| `MLFLOW_TRACKING_INSECURE_TLS` | No | Set to `true` to skip TLS verification |
| `MLFLOW_WORKSPACE` | No | MLflow workspace / K8s namespace (default: `default`) |
