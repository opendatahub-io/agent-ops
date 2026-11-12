# Agent Ops

Demos, guides, and getting-started material for running [OpenShell](https://docs.nvidia.com/openshell/latest/) on OpenShift.

## Guides

### [Getting Started with OpenShell on OpenShift](guides/getting-started-openshell-openshift.md)

End-to-end guide for installing OpenShell with Helm, exposing the gateway through an OpenShift Route, configuring mTLS, registering a provider, creating a sandbox, running Claude Code in the sandbox, and managing egress policies.

### [Running OpenShell sandboxes with Kata runtime on OpenShift](guides/openshell-with-osc.md)

Approve a Kata RuntimeClass for OpenShell workloads, inspect the guest VM and verify binary-aware network policy on OpenShift.

### [Use an RHOAI model from an OpenShell sandbox](guides/inference-routing-rhoai.md)

Call a token-authenticated RHOAI model through its native OpenAI-compatible endpoint, with provider profiles controlling access and supervisor-side credential injection.

### [OpenShell Capability Testing and Security Analysis](scc-requirements.md)

Testing results for OpenShell v0.0.85 on OpenShift, including capability behavior in the supervisor and sandbox user contexts.

## Deployment helpers

For a fresh test installation, the deployment helper selects chart `0.1.7` and
ODH runtime `v0.1.7-rhaiv.0`, loading the same
[shared Helm values](examples/openshift/values-odh.yaml) as the getting-started guide.
Install the CLI with
`scripts/install-openshell-cli.sh` and install the Agent Sandbox operator first.
Choose an unused namespace and local gateway name:

```shell
OPENSHELL_NAMESPACE=openshell-test OPENSHELL_GATEWAY_NAME=openshift-test \
  ./scripts/deploy-openshell.sh
```

The cleanup helper checks namespace ownership and prompts before deleting it:

```shell
OPENSHELL_NAMESPACE=openshell-test OPENSHELL_GATEWAY_NAME=openshift-test \
  ./scripts/cleanup-openshell.sh
```

Use the guide's cleanup for an installation created manually. The helpers retain
Agent Sandbox and other cluster-wide resources.

## Demos

### [MLflow OpenShell Tracing](demos/mlflow-openshell-tracing/)

Demonstrates how to capture MLflow traces from AI agents running in OpenShell sandboxes and send them to the managed MLflow instance on RHOAI.

**The demo includes:**

- **MLflow auto-instrumentation** — `mlflow.openai.autolog()` captures all LLM calls as traces with zero code changes
- **OpenShell inference routing** - Agent code sends requests to `inference.local` through the OpenAI SDK, and the OpenShell proxy handles model credentials
- **Environment variable injection** — Passes `MLFLOW_TRACKING_URI` by using `--env` instead of `--credential` for direct SDK access
- **Sandbox network policy** — Configures explicit network access from sandboxed workloads to the MLflow tracking server

**Stack:** Python, OpenAI SDK, MLflow, OpenShell, RHOAI

See the [MLflow OpenShell Tracing README](demos/mlflow-openshell-tracing/README.md) for setup and usage.
