# OpenCode with Vertex AI tracing in OpenShell on RHOAI

> **Warning:** OpenShell on OpenShift is experimental. This install path requires a privileged Security Context Constraint (SCC) and runs with TLS disabled on the gateway. Do not use it in production.

Run [OpenCode](https://github.com/anomalyco/opencode) (open-source TypeScript AI coding agent) inside an [OpenShell](https://docs.nvidia.com/openshell/latest) sandbox on OpenShift. The agent queries Jira via MCP, uses Claude Opus via Vertex AI for inference, and sends traces to the MLflow instance managed by Red Hat OpenShift AI (RHOAI) via the `@mlflow/opencode` plugin.

This guide covers the full path: deploying PostgreSQL for the OpenShell gateway, installing OpenShell as a Deployment, configuring Vertex AI inference, launching OpenCode with Jira MCP, and progressively unlocking sandbox network policies to demonstrate default-deny isolation. The narrative follows a five-act progression: **from locked down to fully observable**.

## Recordings

- [OpenCode + Vertex AI in OpenShell sandbox with MLflow traces on OpenShift demo at Red Hat AI Agentic Office Hours — 2026/07/27](https://drive.google.com/file/d/1_YjasZwsF7q9LefXuuMWkm5ca6aqs0QK/view) — Full end-to-end demo recording
- [OpenCode + Vertex AI in OpenShell sandbox with MLflow traces on OpenShift demo at Agentic / AI Eng Tools Demos — 2026/07/27](https://drive.google.com/file/d/1OhcpBMcSPMZzZDjTJ_Gprexv64F7d7ts/view) — Full end-to-end demo recording

## Architecture

![OpenCode + OpenShell + MLflow on OpenShift Architecture](OpenCode-OpenShell-MLflow-on-OpenShift.png)

**Key integration points:**

- **Vertex AI (direct)** — OpenCode uses its built-in `google-vertex` provider to connect to Vertex AI directly via `aiplatform.googleapis.com`. Authentication uses Application Default Credentials (ADC) uploaded to the sandbox. Network access is granted via sandbox policy.
- **Jira MCP** — The [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) server runs locally inside the sandbox, giving OpenCode read access to Jira sprints and issues. Jira credentials are passed via `--env` and read automatically by `mcp-atlassian`.
- **MLflow tracing** — The [`@mlflow/opencode`](https://mlflow.org/docs/latest/genai/tracing/integrations/listing/opencode/) plugin captures all conversation turns, tool usage, and token counts as MLflow traces. Configured via `"plugin": ["@mlflow/opencode"]` in `opencode.json`.
- **PostgreSQL gateway** — OpenShell runs as a Deployment (not StatefulSet) with an external PostgreSQL database. Inference provider credentials are stored in PostgreSQL, not ephemeral SQLite.
- **Network policy** — Sandbox egress is denied by default. Endpoints are granted incrementally via `openshell policy update --add-endpoint`. Each endpoint rule requires a `--binary` flag specifying which executable is authorized to use it.

## Prerequisites

- An OpenShift cluster running version 4.19 or later is available.
- You have `cluster-admin` access to the cluster.
- RHOAI is installed on the cluster. For installation, see the [RHOAI documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed).
- The MLflow Tracking Server is deployed as part of RHOAI.
- A Google Cloud project with Vertex AI API enabled and access to Claude models (Opus, Sonnet).
- `gcloud` CLI authenticated with Application Default Credentials (`gcloud auth application-default login`).
- A Jira API token for [redhat.atlassian.net](https://redhat.atlassian.net) (Settings → Security → API token).
- The OpenShift CLI (`oc`) is installed and authenticated to the cluster.
- The OpenShell CLI (`openshell`) version 0.0.85 is installed locally. For installation, see the [OpenShell quickstart](https://docs.nvidia.com/openshell/latest/get-started/quickstart).
- The Helm CLI (`helm`) is installed.

## 1. Install Agent Sandbox Custom Resource Definitions (CRDs)

> **Note:** Steps 1–5 install OpenShell on OpenShift with PostgreSQL. If OpenShell is already running on your cluster, skip to [step 6](#6-configure-opencode). This guide uses `server.disableTls=true` with a local port-forward rather than the mTLS-over-Route approach in the [OpenShell getting-started guide](https://docs.nvidia.com/openshell/latest/get-started/quickstart) — a simpler setup for demos that avoids certificate management.

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

## 2. Deploy PostgreSQL

OpenShell defaults to SQLite (StatefulSet with PVC). This demo uses PostgreSQL so the gateway runs as a Deployment — stateless, scalable, no per-pod PVC dependency.

Create the namespace and apply the PostgreSQL manifests:

```bash
# Run locally
oc create ns openshell

oc adm policy add-scc-to-user privileged \
  -z openshell-sandbox -n openshell

oc apply -f k8s/postgresql.yaml
```

Wait for PostgreSQL to be ready:

```bash
# Run locally
oc -n openshell rollout status deployment/postgresql
```

Create the connection URI secret that OpenShell will use:

```bash
# Run locally
kubectl create secret generic pg-credentials -n openshell \
  --from-literal=uri="postgresql://openshell:openshell@postgresql:5432/openshell"
```

The PostgreSQL manifests (`k8s/postgresql.yaml`) deploy:

| Resource | Purpose |
|----------|---------|
| `Secret/postgresql-credentials` | PostgreSQL user, password, and database name |
| `PersistentVolumeClaim/postgres-pvc` | 1 Gi persistent storage for database data |
| `Deployment/postgresql` | Single-replica PostgreSQL 16 pod |
| `Service/postgresql` | Cluster-internal service on port 5432 |

To inspect the database with a local client (DBeaver, psql, etc.), port-forward the service:

```bash
# Run locally — keep running while the client is connected
oc -n openshell port-forward svc/postgresql 5432:5432
```

Then connect with host `localhost`, port `5432`, database `openshell`, user `openshell`, password `openshell`.

## 3. Install OpenShell as a Deployment

> **Warning:** This step disables TLS on the gateway and allows unauthenticated access. These settings are acceptable here because the gateway is accessed only through a local port-forward (`localhost:8080`), not through an externally exposed Route. If you need a production-grade install with mTLS over a Route, see the [OpenShell getting-started guide](https://docs.nvidia.com/openshell/latest/get-started/quickstart).

Install the OpenShell Helm chart with PostgreSQL backend and Deployment mode:

```bash
# Run locally
helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.0.85 \
  --namespace openshell \
  --set workload.kind=deployment \
  --set server.externalDbSecret=pg-credentials \
  --set server.disableTls=true \
  --set podSecurityContext.fsGroup=null \
  --set securityContext.runAsUser=null \
  --set server.auth.allowUnauthenticatedUsers=true
```

Wait for the gateway to be ready:

```bash
# Run locally
oc -n openshell rollout status deployment/openshell
```

Set up local port-forwarding and register the gateway:

```bash
# Run locally
oc -n openshell port-forward svc/openshell 8080:8080 2>/dev/null &
PORT_FORWARD_PID=$!
openshell gateway add http://127.0.0.1:8080 --local --name openshift
```

Verify that the gateway is registered and reachable:

```bash
# Run locally
openshell gateway list
```

You should see the `openshift` gateway listed with a `connected` status.

## 4. Prepare Vertex AI credentials

OpenCode connects to Vertex AI directly via `aiplatform.googleapis.com` using its built-in `google-vertex` provider. The sandbox needs Application Default Credentials (ADC) and network access to the Vertex AI API.

Ensure you have ADC configured locally:

```bash
# Run locally
gcloud auth application-default login
```

The ADC file at `~/.config/gcloud/application_default_credentials.json` will be uploaded to the sandbox in step 7.

## 5. Expose MLflow via a reencrypt route

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

## 6. Configure OpenCode

Clone this repository and change to the demo directory:

```bash
# Run locally
git clone https://github.com/opendatahub-io/agent-ops.git
cd agent-ops/demos/opencode-vertex-tracing
```

The demo includes:

| File | Purpose |
|------|---------|
| `opencode.json` | OpenCode configuration: `google-vertex` provider for Vertex AI inference, Jira MCP server via `mcp-atlassian` |
| `k8s/postgresql.yaml` | PostgreSQL manifests for the OpenShell gateway database |

> **Note:** OpenCode uses its built-in `google-vertex` provider to connect to Vertex AI directly. The `opencode.json` sets the Vertex AI region and default model. ADC credentials and the GCP project ID are passed via environment variables and an uploaded credentials file. Edit `opencode.json` to match your environment — update the `location` to your Vertex AI region and the `model` to a Claude model available in your GCP project's Model Garden.

## 7. Create the sandbox with OpenCode

Create the sandbox with GCP credentials, Jira credentials, MLflow environment variables, and the OpenCode config file:

```bash
# Run locally — from the opencode-vertex-tracing/ directory
openshell sandbox create \
  --name opencode-demo \
  --env GOOGLE_CLOUD_PROJECT=<your-gcp-project-id> \
  --env GOOGLE_APPLICATION_CREDENTIALS=/sandbox/.gcloud/adc.json \
  --env JIRA_URL=https://redhat.atlassian.net \
  --env JIRA_USERNAME=<your-username>@redhat.com \
  --env JIRA_API_TOKEN=<your-jira-api-token> \
  --env MLFLOW_TRACKING_URI=https://$MLFLOW_ROUTE \
  --env MLFLOW_TRACKING_TOKEN=$(oc whoami -t) \
  --env MLFLOW_EXPERIMENT_ID=<experiment-id> \
  --env MLFLOW_WORKSPACE=default \
  --env MLFLOW_TRACKING_INSECURE_TLS=true \
  --upload opencode.json:/sandbox/opencode.json \
  --upload ~/.config/gcloud/application_default_credentials.json:/sandbox/.gcloud/adc.json
```

> **Note:** The ADC file is uploaded to the sandbox so the `@ai-sdk/google-vertex` provider can exchange the refresh token for an access token via `oauth2.googleapis.com`. The Jira token is passed via `--env` and read automatically by `mcp-atlassian`.

Verify that the sandbox is running:

```bash
# Run locally
openshell sandbox list
```

Enter the sandbox to launch OpenCode:

```bash
# Run locally
openshell term
```

Select `opencode-demo` from the list, then run:

```
# Inside the sandbox
sandbox@opencode-demo:~$ opencode
```

## 8. Grant Vertex AI access and launch OpenCode

OpenCode connects to Vertex AI directly via `aiplatform.googleapis.com`. Grant network access to the Vertex AI and OAuth2 endpoints:

```bash
# Run locally
openshell policy update opencode-demo \
  --add-endpoint aiplatform.googleapis.com:443 \
  --add-endpoint oauth2.googleapis.com:443 \
  --binary /usr/lib/node_modules/opencode-ai/bin/.opencode \
  --wait
```

> **Note:** The binary path is `/usr/lib/node_modules/opencode-ai/bin/.opencode` (not `/usr/bin/node`). OpenCode's actual executable is a wrapper script at this path. The `--binary` flag must match the process that makes outbound connections.

Enter the sandbox and launch OpenCode:

```bash
# Run locally
openshell term
```

Select `opencode-demo`, then run `opencode`. Select **Claude Opus 4.5** from the "Vertex (Anthropic)" section in the model picker.

## 9. Default-deny — observe network isolation

OpenCode connects to Vertex AI for reasoning. Ask it to query Jira:

```
# Inside the sandbox (OpenCode prompt)
What is AgentOps (RAG + Vector DB) team working on in Sprint 8 in RHAIENG in Jira?
```

**Expected result:** The Jira MCP call **fails** — the sandbox has no network policy allowing egress to `redhat.atlassian.net`. You will see a connection error or 403 from the Jira API.

**Message:** "The agent can reason via Vertex AI (Claude Opus), but cannot reach any external service like Jira. This is default-deny."

## 10. Grant Jira access

Add `redhat.atlassian.net` to the sandbox network policy. The `--binary` flag is required — it specifies which executable is authorized to use the endpoint:

```bash
# Run locally
openshell policy update opencode-demo \
  --add-endpoint redhat.atlassian.net:443 \
  --binary /sandbox/.uv/python/cpython-3.14.3-linux-x86_64-gnu/bin/python3 \
  --wait
```

> **Note:** Without `--binary`, the endpoint is added to the policy but no process is authorized to use it. The `--binary` value must match the executable that makes outbound connections — in this case, the Python interpreter used by `mcp-atlassian`. See the [policy documentation](https://docs.nvidia.com/openshell/latest/sandboxes/policies#iterate-on-a-running-sandbox) for details.

Ask the same question again:

```
# Inside the sandbox (OpenCode prompt)
What is AgentOps (RAG + Vector DB) team working on in Sprint 8 in RHAIENG in Jira?
```

**Expected result:** The Jira MCP call **succeeds** — the agent retrieves sprint items and summarizes the team's work.

**Message:** "We selectively granted Jira access. The agent can now query your backlog — nothing more."

## 11. Add MLflow tracing

The agent ran, but there is no visibility into what it did or what data it accessed. Add the MLflow route to the sandbox network policy so the `@mlflow/opencode` plugin can send traces:

```bash
# Run locally
openshell policy update opencode-demo \
  --add-endpoint $MLFLOW_ROUTE:443 \
  --binary /usr/lib/node_modules/opencode-ai/bin/.opencode \
  --wait
```

Install the `@mlflow/opencode` plugin inside the sandbox. The npm registry URL for scoped packages contains encoded slashes (`@mlflow%2fopencode`) which the sandbox proxy rejects, so build a complete `node_modules` bundle locally and upload it:

```bash
# Run locally — build plugin bundle with all dependencies
cd /tmp && rm -rf mlflow-bundle && mkdir mlflow-bundle && cd mlflow-bundle
npm init -y > /dev/null
npm install @mlflow/opencode

# Create tarball and upload to sandbox
tar czf /tmp/mlflow-node-modules.tar.gz node_modules/
openshell sandbox upload opencode-demo /tmp/mlflow-node-modules.tar.gz /tmp/mlflow-node-modules.tar.gz

# Extract inside sandbox
openshell sandbox exec --name opencode-demo -- sh -c \
  'cd /sandbox && tar xzf /tmp/mlflow-node-modules.tar.gz'
```

Verify the plugin loads:

```bash
# Run locally
openshell sandbox exec --name opencode-demo -- node -e \
  'require("@mlflow/opencode"); console.log("Plugin loaded OK")'
```

> **Note:** The plugin is installed to `/sandbox/node_modules/` (not `/usr/lib/`) because `/usr` is read-only in the sandbox. Node.js resolves modules from the working directory's `node_modules/` automatically. The bundle includes ~92 packages (~22 MB compressed) including `@mlflow/core` and its dependencies.

Restart OpenCode. After each conversation turn, traces are automatically sent to MLflow.

## 12. Verify traces in MLflow

### Via the MLflow UI

1. Open the RHOAI dashboard and navigate to **MLflow** — or open the MLflow route directly in your browser (`https://$MLFLOW_ROUTE`). Note that the RHOAI dashboard URL may differ from the API route (e.g., `rh-ai.apps.<cluster>/mlflow`).
2. Select the **default** workspace from the dropdown (top-left)
3. Click **Experiments** in the left sidebar, then select **opencode-vertex-demo**
4. Click **Traces** under Observability to see individual traces with request/response pairs, token counts, and execution times

The traces should show the full agent reasoning chain: query → Jira MCP call → LLM summary.

### Programmatically

```bash
# Run locally
export MLFLOW_TRACKING_URI=https://$(oc -n redhat-ods-applications get route mlflow -o jsonpath='{.spec.host}')
export MLFLOW_TRACKING_TOKEN=$(oc whoami -t)
export MLFLOW_TRACKING_INSECURE_TLS=true

curl -sk -H "Authorization: Bearer $MLFLOW_TRACKING_TOKEN" \
  -H "X-MLflow-Workspace: default" \
  "$MLFLOW_TRACKING_URI/api/2.0/mlflow/experiments/get-by-name?experiment_name=opencode-vertex-demo"
```

## Cleanup

To remove the resources created by this guide:

```bash
# Run locally

# Delete the sandbox
openshell sandbox delete opencode-demo

# Delete the MLflow route from the RHOAI namespace
oc -n redhat-ods-applications delete route mlflow

# Uninstall OpenShell
helm uninstall openshell -n openshell

# Delete PostgreSQL resources
oc -n openshell delete deployment/postgresql svc/postgresql pvc/postgres-pvc secret/postgresql-credentials secret/pg-credentials

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
| Vertex AI `policy_denied` / unable to connect | Network policy missing for Vertex AI | Run `openshell policy update <sandbox> --add-endpoint aiplatform.googleapis.com:443 --add-endpoint oauth2.googleapis.com:443 --binary /usr/lib/node_modules/opencode-ai/bin/.opencode --wait` (step 8) |
| Jira MCP call blocked / 403 Forbidden | Network policy missing or no `--binary` | Run `openshell policy update <sandbox> --add-endpoint redhat.atlassian.net:443 --binary /sandbox/.uv/python/cpython-3.14.3-linux-x86_64-gnu/bin/python3 --wait` (step 10) |
| Policy update says "unchanged" but endpoint still blocked | Wrong `--binary` path | OpenCode binary is `/usr/lib/node_modules/opencode-ai/bin/.opencode` (not `/usr/bin/node`). Jira MCP binary is the Python interpreter. |
| `MLFLOW_TRACKING_URI` empty inside sandbox | Variable not passed | Use `--env MLFLOW_TRACKING_URI=...` on `openshell sandbox create` (step 7) |
| No traces in MLflow | Network policy missing for MLflow | Run `openshell policy update <sandbox> --add-endpoint <mlflow-route>:443 --binary /usr/lib/node_modules/opencode-ai/bin/.opencode --wait` (step 11) |
| `502 Bad Gateway` on MLflow route | Wrong route type | Use a **reencrypt** route, not edge or passthrough — MLflow uses TLS internally (step 5) |
| `ConnectionResetError` to MLflow | Passthrough route + proxy conflict | Switch to a reencrypt route with `--dest-ca-cert` (step 5) |
| `Publisher model not found` on Vertex AI | Wrong region or model ID | Verify model availability in your GCP project's Model Garden; select a model from OpenCode's model picker |
| `mcp-atlassian` auth error inside sandbox | Wrong env var names | Use `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN` — these are read automatically by `mcp-atlassian` |
| GCP auth fails inside sandbox | ADC file not uploaded | Ensure `--upload ~/.config/gcloud/application_default_credentials.json:/sandbox/.gcloud/adc.json` is in the sandbox create command (step 7) |
| PostgreSQL pod pending | PVC not bound | Check storage class availability: `oc -n openshell get pvc` |
| OpenShell gateway CrashLoopBackOff | pg-credentials secret missing or wrong URI | Verify: `oc -n openshell get secret pg-credentials -o jsonpath='{.data.uri}' \| base64 -d` |

## Environment variables reference

| Variable | Injection | Description |
|----------|-----------|-------------|
| `GOOGLE_CLOUD_PROJECT` | `--env` | GCP project ID with Vertex AI API enabled and Claude model access |
| `GOOGLE_APPLICATION_CREDENTIALS` | `--env` | Path to ADC file inside sandbox (`/sandbox/.gcloud/adc.json`) |
| `JIRA_URL` | `--env` | Jira instance URL — read by `mcp-atlassian` automatically |
| `JIRA_USERNAME` | `--env` | Jira username (email) — read by `mcp-atlassian` automatically |
| `JIRA_API_TOKEN` | `--env` | Jira API token — read by `mcp-atlassian` automatically |
| `MLFLOW_TRACKING_URI` | `--env` | MLflow server URL — read by `@mlflow/opencode` plugin |
| `MLFLOW_TRACKING_TOKEN` | `--env` | OpenShift bearer token (`oc whoami -t`) for MLflow authentication |
| `MLFLOW_EXPERIMENT_ID` | `--env` | MLflow experiment ID — create via API or UI first |
| `MLFLOW_WORKSPACE` | `--env` | MLflow workspace name (default: `default`) — required for RHOAI multi-tenancy |
| `MLFLOW_TRACKING_INSECURE_TLS` | `--env` | Set to `true` to skip TLS verification for self-signed certs |
