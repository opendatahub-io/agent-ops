# Agent Ops

Demos, guides, and getting-started material for running [OpenShell](https://docs.nvidia.com/openshell/latest/) on OpenShift.

## Guides

### [Getting Started with OpenShell on OpenShift](guides/getting-started-openshell-openshift.md)

End-to-end walkthrough covering Helm installation, Route-based gateway exposure, mTLS setup, provider registration, sandbox creation, running Claude Code inside a sandbox, and egress policy management.

### [Inference Routing with RHOAI](guides/inference-routing-rhoai.md)

Route sandbox inference traffic through a token-authenticated RHOAI-served model using the OpenShell privacy router, without exposing credentials to the sandbox.

## Demos

### [OpenCode + Vertex AI Tracing](demos/opencode-vertex-tracing/)

Run OpenCode (TypeScript AI coding agent) in an OpenShell sandbox with Jira MCP, Claude Opus via Vertex AI for inference, progressive policy unlocking, and MLflow traces via the `@mlflow/opencode` plugin.

**What it shows:**

- **Progressive policy unlocking** — Demo starts with default-deny, then selectively grants Jira and MLflow access to show enterprise network isolation
- **Vertex AI inference** — OpenCode connects to Vertex AI directly via `aiplatform.googleapis.com` using its built-in `google-vertex` provider with ADC authentication
- **Jira MCP integration** — `mcp-atlassian` server gives OpenCode read access to Jira sprints and issues
- **MLflow tracing** — `@mlflow/opencode` plugin captures all conversation turns, tool calls, and token counts as MLflow traces
- **PostgreSQL-backed gateway** — OpenShell runs as a Deployment with external PostgreSQL, not StatefulSet/SQLite

**Stack:** TypeScript, OpenCode, Vertex AI, Claude Opus, Jira MCP, OpenShell, PostgreSQL, MLflow, RHOAI

See the [OpenCode + Vertex AI Tracing README](demos/opencode-vertex-tracing/README.md) for setup and usage.
