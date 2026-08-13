# Run sandboxed AI agents on Fedora with Podman and Vertex AI

> **Midstream Documentation**
>
> This guide covers a Red Hat internal setup using RH-internal credentials and images. It is not part of the upstream OpenShell project.

Run AI coding agents (Claude Code, opencode) inside OpenShell sandboxes on a Fedora or Red Hat Enterprise Linux system, using Podman as the compute driver and Google Vertex AI as the inference provider. By the end you will have a sandboxed agent running with policy-controlled network access, with GCP credentials managed entirely by the gateway.

New to OpenShell? Read [How OpenShell Works](https://docs.nvidia.com/openshell/latest/about/how-it-works) first for a quick tour of the architecture.

## Prerequisites

- You are running Fedora or RHEL on your workstation or in a VM (Silverblue works).
- Podman is installed and your user can run rootless containers (`podman ps` succeeds without `sudo`).
- The Google Cloud CLI (`gcloud`) is installed and authenticated with Application Default Credentials. If you have already set up Claude Code using the RH internal guide, these are already configured. To verify:
  ```bash
  gcloud auth application-default print-access-token
  ```
- You know your team's GCP project ID. You can find it by running `gcloud config get-value project`, or by checking the [internal project spreadsheet](https://docs.google.com/spreadsheets/d/1qWoCx3i5jZ-t6BUD-2AIdutk9sMmkytoXqjBXh2oi4U/edit?gid=0#gid=0) under the column matching your upline manager.

## 1. Install OpenShell

```bash
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
```

On Fedora and RHEL, this installs the `openshell` and `openshell-gateway` RPMs and registers a systemd user service for the gateway.

## 2. Start the gateway

```bash
systemctl --user start openshell-gateway
```

The service generates TLS certificates and configures the CLI automatically on first start. It is enabled by default, so it starts automatically on future logins.

Verify the CLI is connected:

```bash
openshell status
```

You should see `Status: Connected` and `Authentication: Authenticated (mTLS transport)`.

## 3. Create the Vertex AI provider

Replace `GCP_PROJECT_ID` with your team's project ID.

```bash
openshell settings set --global --key providers_v2_enabled --value true --yes

```

This enables provider endpoint injection, which automatically includes the Vertex AI network endpoints in sandbox policies so the agent can reach them without manual policy editing.

```bash
openshell provider create \
  --name rh-vertex \
  --type google-vertex-ai \
  --from-gcloud-adc \
  --config VERTEX_AI_PROJECT_ID=GCP_PROJECT_ID \
  --config VERTEX_AI_REGION=global
```

`--from-gcloud-adc` reads your existing gcloud Application Default Credentials — no service account key file is needed. The gateway manages token refresh automatically.

## 4. Configure inference routing

```bash
openshell inference set \
  --provider rh-vertex \
  --model claude-sonnet-4-6 \
  --no-verify
```

`--no-verify` is required with `VERTEX_AI_REGION=global` because the validation probe does not match the rawPredict path for the global endpoint.

## 5. Create a sandbox and launch an agent

### Claude Code

```bash
openshell sandbox create \
  --provider rh-vertex \
  --env ANTHROPIC_BASE_URL="https://inference.local" \
  --env ANTHROPIC_API_KEY="unused" \
  -- claude
```

This uses the gateway's default sandbox image, which includes the `claude` binary.

### OpenCode

```bash
openshell sandbox create \
  --provider rh-vertex \
  --from quay.io/aipcc/base-images/agentic/opencode:latest \
  --env ANTHROPIC_BASE_URL="https://inference.local/v1" \
  --env ANTHROPIC_API_KEY="unused" \
  -- opencode
```

> **Note:** OpenCode requires `/v1` in `ANTHROPIC_BASE_URL`. Without it, requests are sent to the wrong path and denied by the gateway.

> **Warning:** Do not set `CLAUDE_CODE_USE_VERTEX=1` inside the sandbox. That flag makes the agent connect directly to Vertex AI and attempt GCP credential discovery, which fails because the sandbox does not expose GCP credentials. Use `ANTHROPIC_BASE_URL="https://inference.local"` instead — the gateway handles Vertex AI authentication transparently.

## 6. Approve network policy requests

When the agent tries to reach an endpoint not in its policy, the gateway blocks it and surfaces a request in the terminal UI:

```bash
openshell sandbox list   # find your sandbox name
openshell term <sandbox-name>
```

Review each endpoint request before approving. For common Vertex AI endpoints and guidance on whether to approve them, see the [Google Vertex AI provider documentation](https://docs.nvidia.com/openshell/latest/providers/google-vertex-ai#policy-proposals).

## Next steps

- To add GitHub access for the agent, create a GitHub provider and attach it at sandbox creation with a second `--provider` flag.
- To write a reusable policy file instead of approving endpoints interactively, see [Policies](https://docs.nvidia.com/openshell/latest/sandboxes/policies).
- To monitor sandbox logs, run `openshell logs <sandbox-name>`.
- To stop the gateway, run `systemctl --user stop openshell-gateway`.
