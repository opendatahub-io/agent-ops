# Agent Ops

Demos, guides, and getting-started material for running [OpenShell](https://docs.nvidia.com/openshell/latest/) on OpenShift.

## Guides

### [Getting Started with OpenShell on OpenShift](guides/getting-started-openshell-openshift.md)

End-to-end walkthrough covering Helm installation, Route-based gateway exposure, mTLS setup, provider registration, sandbox creation, running Claude Code inside a sandbox, and egress policy management.

### [Inference Routing with RHOAI](guides/inference-routing-rhoai.md)

Route sandbox inference traffic through a token-authenticated RHOAI-served model using the OpenShell privacy router, without exposing credentials to the sandbox.

## Demos

### [MLflow OpenShell Tracing](demos/mlflow-openshell-tracing/)

Enablement content showing how to capture MLflow traces from AI agents running in OpenShell sandboxes into the managed MLflow instance on RHOAI.

**What it shows:**

- **MLflow auto-instrumentation** — `mlflow.openai.autolog()` captures all LLM calls as traces with zero code changes
- **OpenShell inference routing** — Agent code calls `inference.local` via the OpenAI SDK; the OpenShell proxy handles model credentials
- **Environment variable injection** — `MLFLOW_TRACKING_URI` passed via `--env` (not `--credential`) for direct SDK access
- **Sandbox network policy** — Explicit network access to the MLflow tracking server from sandboxed workloads

**Stack:** Python, OpenAI SDK, MLflow, OpenShell, RHOAI

See the [MLflow OpenShell Tracing README](demos/mlflow-openshell-tracing/README.md) for setup and usage.
