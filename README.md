# Agent Ops

Demos, guides, and getting-started material for running [OpenShell](https://docs.nvidia.com/openshell/latest/) on OpenShift.

## Guides

### [Getting Started with OpenShell on OpenShift](guides/getting-started-openshell-openshift.md)

End-to-end guide for installing OpenShell with Helm, exposing the gateway through an OpenShift Route, configuring mTLS, registering an OpenAI provider, creating a sandbox, running the Codex CLI in the sandbox, and managing egress policies.

### [Running OpenShell sandboxes with Kata runtime on OpenShift](guides/openshell-with-osc.md)

Configure OpenShell sandboxes to use a Kata-backed `RuntimeClass` in `sidecar` topology on OpenShift, then verify the VM isolation boundary and network policy enforcement.

### [Inference Routing with RHOAI](guides/inference-routing-rhoai.md)

Route sandbox inference traffic through a token-authenticated RHOAI-served model using the OpenShell privacy router, without exposing credentials to the sandbox.

### [OpenShell Capability Testing and Security Analysis](scc-requirements.md)

Testing results for OpenShell v0.0.85 on OpenShift, including capability behavior in the supervisor and sandbox user contexts.

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
