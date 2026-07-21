# MLflow Tracing from OpenShell Sandboxes on RHOAI

Capture MLflow traces from AI agents running in [OpenShell](https://docs.nvidia.com/openshell/latest) sandboxes, with traces stored in the managed MLflow instance on Red Hat OpenShift AI (RHOAI).

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

| Component | Requirement |
|-----------|-------------|
| **OpenShift** | 4.14+ cluster with cluster-admin access |
| **RHOAI** | Installed with MLflow Tracking Server deployed ([install guide](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed)) |
| **OpenShell** | CLI v0.0.85 installed locally ([install docs](https://docs.nvidia.com/openshell/latest/get-started/quickstart)) |
| **Inference** | Model provider configured (MaaS, vLLM, or other OpenAI-compatible endpoint) |
| **Tools** | `oc`, `helm`, `openshell` CLIs |

## 1. Install Agent Sandbox CRDs

OpenShell requires the [Agent Sandbox](https://agent-sandbox.sigs.k8s.io) Kubernetes SIG project. Install the CRDs and controller before the OpenShell chart:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/latest/download/sandbox.yaml
```

Confirm the controller is running:

```bash
kubectl -n agent-sandbox-system get pods
```

## 2. Deploy OpenShell on OpenShift

> **Note:** OpenShell on OpenShift is experimental. It requires a privileged SCC and runs with TLS disabled on the gateway.

Create the namespace and grant the required security context:

```bash
oc create ns openshell

oc adm policy add-scc-to-user privileged \
  -z openshell-sandbox -n openshell
```

Install the OpenShell Helm chart with OpenShift overrides:

```bash
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
oc -n openshell rollout status statefulset/openshell
```

Set up local port-forwarding and register the gateway:

```bash
oc -n openshell port-forward svc/openshell 8080:8080 &
openshell gateway add http://127.0.0.1:8080 --local --name openshift
```

## 3. Configure Inference Routing

Enable the v2 provider pipeline:

```bash
openshell settings set --global --key providers_v2_enabled --value true --yes
```

Register your model provider. Example using a direct vLLM/OpenAI-compatible endpoint:

```bash
openshell provider create \
  --name my-provider \
  --type openai \
  --credential OPENAI_API_KEY=<your-api-key> \
  --config OPENAI_BASE_URL=<endpoint>/v1

openshell inference set --provider my-provider --model <model-name>
```

Verify inference works:

```bash
openshell sandbox create -- python3 -c "
from openai import OpenAI
client = OpenAI(api_key='unused', base_url='https://inference.local/v1')
r = client.chat.completions.create(model='router', messages=[{'role':'user','content':'hello'}])
print(r.choices[0].message.content)
"
```

## 4. Expose MLflow via a Reencrypt Route

The MLflow service on RHOAI uses TLS internally (port 8443 with a service-serving certificate). Sandboxes access it through the OpenShell proxy, which terminates outbound TLS — this requires a **reencrypt** route so the OpenShift router re-encrypts traffic to the backend service.

Get the service-signing CA certificate:

```bash
oc get configmap -n openshift-service-ca signing-cabundle \
  -o jsonpath='{.data.ca-bundle\.crt}' > /tmp/mlflow-ca.crt
```

Create the reencrypt route:

```bash
oc -n redhat-ods-applications create route reencrypt mlflow \
  --service=mlflow \
  --port=8443 \
  --dest-ca-cert=/tmp/mlflow-ca.crt \
  --insecure-policy=Redirect
```

Note the route hostname:

```bash
export MLFLOW_ROUTE=$(oc -n redhat-ods-applications get route mlflow -o jsonpath='{.spec.host}')
echo "https://$MLFLOW_ROUTE"
```

Verify the route works:

```bash
curl -sk -H "Authorization: Bearer $(oc whoami -t)" \
  -H "X-MLflow-Workspace: default" \
  "https://$MLFLOW_ROUTE/api/2.0/mlflow/experiments/search?max_results=10"
```

> **Why reencrypt?** A passthrough route preserves the service's self-signed TLS certificate, which the OpenShell proxy cannot validate during its CONNECT tunnel. An edge route strips TLS but cannot re-encrypt to the backend's 8443 port. Reencrypt handles both sides: the router presents a trusted certificate to clients and re-encrypts to the backend using the service CA.

## 5. Create the Sandbox with MLflow Environment Variables

Pass `MLFLOW_TRACKING_URI` and authentication credentials into the sandbox using `--env`:

```bash
openshell sandbox create \
  --name mlflow-demo \
  --env MLFLOW_TRACKING_URI=https://$MLFLOW_ROUTE \
  --env MLFLOW_TRACKING_TOKEN=$(oc whoami -t) \
  --env MLFLOW_EXPERIMENT_NAME=openshell-tracing-demo \
  --env MLFLOW_TRACKING_INSECURE_TLS=true \
  --env MLFLOW_WORKSPACE=default \
  --upload agent.py:/sandbox/agent.py
```

> **Why `--env` and not `--credential`?**
>
> OpenShell's `--credential` flag injects opaque placeholder tokens that are resolved only in HTTP request headers, query parameters, and URL paths by the proxy. The MLflow Python SDK reads `MLFLOW_TRACKING_URI` to make direct TCP connections to the tracking server — it needs the real URL value, not a proxy placeholder. Use `--env` for any environment variable that the application reads directly.

## 6. Add MLflow Network Policy

Sandbox egress is denied by default. Add the MLflow route to the sandbox's network policy:

```bash
openshell policy update mlflow-demo \
  --add-endpoint $MLFLOW_ROUTE:443 \
  --binary /sandbox/.uv/python/cpython-3.14.3-linux-x86_64-gnu/bin/python3.14 \
  --wait
```

> **Note:** `inference.local` traffic is handled automatically by the OpenShell proxy and does not need a network policy entry. PyPI access for `uv pip install` is allowed by default.

## 7. Run the Agent

Install dependencies inside the sandbox:

```bash
openshell sandbox exec --name mlflow-demo -- \
  uv pip install openai "mlflow>=3.11.1"
```

### Option A: One-shot run

```bash
openshell sandbox exec --name mlflow-demo -- python3 /sandbox/agent.py
```

### Option B: Interactive sandbox shell

Launch the OpenShell terminal UI and connect to the sandbox interactively:

```bash
openshell term
```

Then inside the sandbox shell:

```
sandbox@mlflow-demo:~$ ls
agent.py  lost+found
sandbox@mlflow-demo:~$ python3 /sandbox/agent.py
```

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

## 8. Verify Traces in MLflow

### Via the MLflow UI

1. Open the MLflow route in your browser (`https://$MLFLOW_ROUTE`)
2. Select the **default** workspace from the dropdown (top-left)
3. Click **Experiments** in the left sidebar, then select **openshell-tracing-demo**
4. Click **Traces** under Observability to see individual traces with request/response pairs, token counts, and execution times

### Programmatically

Run the verification script from outside the sandbox (from the `mlflow-openshell-tracing/` directory):

```bash
export MLFLOW_TRACKING_URI=https://$(oc -n redhat-ods-applications get route mlflow -o jsonpath='{.spec.host}')
export MLFLOW_TRACKING_TOKEN=$(oc whoami -t)
export MLFLOW_TRACKING_INSECURE_TLS=true

uv run verify-traces.py --experiment openshell-tracing-demo --workspace default
```

Expected output:

```
Experiment: openshell-tracing-demo (ID: 2)

Found 3 trace(s):

  Request ID     : tr-12fd15fd068d71414c1b75a4800009c5
  Status         : OK
  Timestamp      : 1784639093629
  Duration       : 9704ms

  ...

Traces verified successfully.
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MLFLOW_TRACKING_URI` empty inside sandbox | Variable not passed | Use `--env MLFLOW_TRACKING_URI=...` on `openshell sandbox create` |
| `403 Forbidden` / `ProxyError` to MLflow | Network policy missing | Run `openshell policy update <sandbox> --add-endpoint <mlflow-route>:443 --wait` |
| `502 Bad Gateway` on MLflow route | Wrong route type | Use a **reencrypt** route, not edge or passthrough (MLflow uses TLS internally) |
| `ConnectionResetError` to MLflow | Passthrough route + proxy conflict | Switch to reencrypt route with `--dest-ca-cert` |
| `Workspace context is required` | Missing workspace header | Set `MLFLOW_WORKSPACE=default` env var (agent.py injects the header automatically) |
| `UNAUTHENTICATED` from MLflow | Missing bearer token | Pass `--env MLFLOW_TRACKING_TOKEN=$(oc whoami -t)` |
| `inference.local` connection refused | Inference not configured | Run `openshell inference set --provider <name> --model <model>` |
| `ModuleNotFoundError: mlflow` | Dependencies not installed | Run `uv pip install mlflow>=3.11.1` inside the sandbox |

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `MLFLOW_TRACKING_URI` | Yes | MLflow reencrypt route URL |
| `MLFLOW_TRACKING_TOKEN` | Yes | OpenShift bearer token (`oc whoami -t`) for MLflow authentication |
| `MLFLOW_EXPERIMENT_NAME` | No | Experiment name (default: `openshell-tracing-demo`) |
| `MLFLOW_TRACKING_INSECURE_TLS` | No | Set to `true` to skip TLS verification |
| `MLFLOW_WORKSPACE` | No | MLflow workspace / K8s namespace (default: `default`) |

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Demo agent — MLflow-traced inference via `inference.local` |
| `pyproject.toml` | Python dependencies (openai, mlflow) |
| `sandbox-policy.yaml` | Reference network policy (prefer `openshell policy update` for live sandboxes) |
| `verify-traces.py` | Post-run trace verification script |
