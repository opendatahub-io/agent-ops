# MLflow Tracing from OpenShell Sandboxes on RHOAI

Capture MLflow traces from AI agents running in [OpenShell](https://docs.openshell.dev) sandboxes, with traces stored in the managed MLflow instance on Red Hat OpenShift AI (RHOAI).

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
│                 │  MLFLOW_TRACKING_URI                            │
│                 ▼                                                 │
│  ┌────────────────────────────┐                                  │
│  │  MLflow Tracking Server    │                                  │
│  │  (RHOAI managed)           │                                  │
│  │  redhat-ods-applications   │                                  │
│  └────────────────────────────┘                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Key integration points:**

- **inference.local** — OpenShell intercepts OpenAI SDK calls and routes them through the configured inference provider. The agent code never touches model credentials directly.
- **mlflow.openai.autolog()** — MLflow auto-instruments all OpenAI SDK calls, capturing request/response pairs as traces with zero code changes.
- **MLFLOW_TRACKING_URI** — Passed into the sandbox via `--env` so the MLflow SDK can send traces to the tracking server.

## Prerequisites

| Component | Requirement |
|-----------|-------------|
| **OpenShift** | 4.14+ cluster with cluster-admin access |
| **RHOAI** | Installed with MLflow Tracking Server deployed ([install guide](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed)) |
| **OpenShell** | CLI installed locally ([install docs](https://docs.openshell.dev/getting-started/installation)) |
| **Inference** | Model provider configured (MaaS, vLLM, or other OpenAI-compatible endpoint) |
| **Tools** | `oc`, `helm`, `openshell` CLIs |

## 1. Deploy OpenShell on OpenShift

> **Note:** OpenShell on OpenShift is experimental. It requires a privileged SCC and TLS disabled on the gateway.

Create the namespace and grant the required security context:

```bash
oc create ns openshell

oc adm policy add-scc-to-user privileged \
  -z openshell-sandbox -n openshell
```

Install the OpenShell Helm chart with OpenShift overrides:

```bash
helm install openshell oci://ghcr.io/open-shell/openshell/helm/openshell \
  -n openshell \
  --set server.disableTls=true \
  --set podSecurityContext.fsGroup=null \
  --set securityContext.runAsUser=null
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

## 2. Configure Inference Routing

Set up a model provider (example using MaaS):

```bash
openshell provider create \
  --name maas \
  --type openai \
  --credential OPENAI_API_KEY=<your-api-key> \
  --config OPENAI_BASE_URL=<maas-endpoint>/v1

openshell inference set --provider maas --model <model-name>
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

## 3. Find the MLflow Tracking Endpoint

Locate the MLflow service or route on your RHOAI cluster:

```bash
# Check for an internal service
oc get svc -n redhat-ods-applications | grep mlflow

# Check for an external route
oc get route -n redhat-ods-applications | grep mlflow
```

The tracking URI will be one of:

| Access Method | Example URI |
|--------------|-------------|
| **Internal service** | `https://mlflow.redhat-ods-applications.svc:8443` |
| **OpenShift route** | `https://mlflow-redhat-ods-applications.apps.<cluster-domain>` |

Export it for use in later steps:

```bash
export MLFLOW_TRACKING_URI=<uri-from-above>
```

## 4. Create the Sandbox with MLflow Environment Variables

Pass `MLFLOW_TRACKING_URI` into the sandbox using `--env`:

```bash
openshell sandbox create \
  --env MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI \
  --env MLFLOW_EXPERIMENT_NAME=openshell-tracing-demo \
  --policy sandbox-policy.yaml \
  --upload agent.py:/sandbox/agent.py \
  --upload pyproject.toml:/sandbox/pyproject.toml \
  -- bash
```

> **Why `--env` and not `--credential`?**
>
> OpenShell's `--credential` flag injects opaque placeholder tokens that are resolved only in HTTP request headers, query parameters, and URL paths by the proxy. The MLflow Python SDK reads `MLFLOW_TRACKING_URI` to make direct TCP connections to the tracking server — it needs the real URL value, not a proxy placeholder. Use `--env` for any environment variable that the application reads directly.

## 5. Configure Sandbox Network Policy

The sandbox needs explicit network access to reach the MLflow tracking server. The included `sandbox-policy.yaml` allows:

- **PyPI** — for installing Python dependencies inside the sandbox
- **MLflow tracking server** — for sending traces

Edit `sandbox-policy.yaml` to match your cluster's MLflow endpoint:

```yaml
network_policies:
  mlflow:
    name: MLflow Tracking Server
    endpoints:
      - host: mlflow.redhat-ods-applications.svc    # adjust to your endpoint
        port: 8443
    binaries:
      - path: /usr/bin/python3.13
```

If using the OpenShift route instead of the internal service, change the host and port accordingly.

> **Note:** `inference.local` traffic is handled automatically by the OpenShell proxy and does not need a network policy entry.

## 6. Run the Agent

Inside the sandbox:

```bash
# Install dependencies
pip install openai "mlflow>=3.11.1"

# Run the agent
python3 /sandbox/agent.py
```

Expected output:

```
[...] MLflow tracing enabled — https://mlflow...svc:8443 (experiment: openshell-tracing-demo)
[...] Sending chat completion request via inference.local ...

--- Agent Response ---
Container sandboxes improve AI agent security by ...

Model: <model-name>
Tokens: 42 prompt, 128 completion
[...] Trace sent to MLflow. Check the experiment UI to verify.
```

## 7. Verify Traces in MLflow

### Via the MLflow UI

1. Open the MLflow UI on RHOAI (navigate to the MLflow route in your browser or through the RHOAI dashboard)
2. Select the **openshell-tracing-demo** experiment
3. Confirm that traces appear with request/response data from the agent run

### Programmatically

Run the verification script from outside the sandbox (anywhere with access to the MLflow endpoint):

```bash
export MLFLOW_TRACKING_URI=<your-mlflow-uri>
python verify-traces.py --experiment openshell-tracing-demo
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MLFLOW_TRACKING_URI` empty inside sandbox | Variable not passed | Use `--env MLFLOW_TRACKING_URI=...` on `openshell sandbox create` |
| `ConnectionRefusedError` to MLflow | Network policy missing | Add MLflow host/port to `sandbox-policy.yaml` |
| TLS certificate errors | Self-signed cert on internal service | Set `MLFLOW_TRACKING_INSECURE_TLS=true` via `--env`, or use the route instead |
| Traces not appearing | Async logging delay | Wait 10-15 seconds after the script exits, then refresh the MLflow UI |
| `inference.local` connection refused | Inference not configured | Run `openshell inference set --provider <name> --model <model>` |
| `ModuleNotFoundError: mlflow` | Dependencies not installed | Run `pip install mlflow>=3.11.1` inside the sandbox |

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `MLFLOW_TRACKING_URI` | Yes | MLflow tracking server URL (service or route) |
| `MLFLOW_EXPERIMENT_NAME` | No | Experiment name (default: `openshell-tracing-demo`) |
| `MLFLOW_TRACKING_INSECURE_TLS` | No | Set to `true` to skip TLS verification for self-signed certs |

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Demo agent — MLflow-traced inference via `inference.local` |
| `pyproject.toml` | Python dependencies (openai, mlflow) |
| `sandbox-policy.yaml` | Network policy allowing PyPI + MLflow access |
| `verify-traces.py` | Post-run trace verification script |
