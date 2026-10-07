# Demo: Claude Code in a sandbox, with inference through the gateway

> **Midstream Documentation**
>
> Demo. Assumes OpenShell is installed and the CLI is connected: [Install OpenShell on OpenShift](../../install/01-install.md).

Run Claude Code inside an OpenShell sandbox with model traffic going through the gateway, which injects your provider credentials, then watch the egress policy block and allow a request. It uses an Anthropic Claude model served through Google Vertex AI; other providers work with their own `--type`.

Additional prerequisite: the Google Cloud CLI (`gcloud`), authenticated with Application Default Credentials (`gcloud auth application-default login`).

## Configure an inference provider

Register the LLM provider credentials with the gateway, enable the v2 provider pipeline, and configure which model the `inference.local` endpoint routes to inside sandboxes. This guide uses Google Vertex AI with Application Default Credentials as the example:

```shell
openshell provider create \
  --name <provider-name> \
  --type google-vertex-ai \
  --from-gcloud-adc \
  --config VERTEX_AI_PROJECT_ID=<gcp-project-id> \
  --config VERTEX_AI_REGION=<gcp-region>

openshell settings set --global --key providers_v2_enabled --value true --yes

openshell inference set --provider <provider-name> --model <model-name>
```

The gateway stores the provider credentials and applies them when routing inference requests, rather than exposing the credentials as sandbox environment variables.

Using a different provider? See the [Supported Provider Types](https://docs.nvidia.com/openshell/latest/sandboxes/manage-providers#supported-provider-types) reference for the full list. Anthropic, OpenAI, NVIDIA API Catalog, AWS Bedrock, GitHub Copilot, and others are all supported, each with its own `--type` and credential shape.

To route inference to a model served by RHOAI rather than an external provider, see [Inference Routing with RHOAI via OpenShell](../inference-routing-rhoai/).

## Create a sandbox

```shell
openshell sandbox create --name my-sandbox
```

This starts a sandbox pod in the `openshell` namespace. When the sandbox is ready, the command opens an interactive shell in the sandbox. The supervisor configures inference routing, audit logging, policy enforcement, and the associated Open Policy Agent (OPA) policy engine.

## Run Claude Code in the sandbox

From the shell you just landed in, launch Claude Code:

```shell
ANTHROPIC_BASE_URL="https://inference.local" \
ANTHROPIC_API_KEY=unused \
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1 \
claude --bare
```

This routes model traffic through the gateway instead of Anthropic directly, so it can inject your real Vertex AI credentials. `--bare` skips login since auth is already handled by the provider. `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` prevents Claude Code from sending beta headers that the OpenShell proxy does not yet pass through. Without it, the proxy rejects requests with unrecognised headers. Configuration differs for other supported agents. For more information, see [Supported Agents](https://docs.nvidia.com/openshell/latest/about/supported-agents).

## Update the egress policy

Ask Claude, or your agent of choice, to curl `https://github.com`. The default policy blocks it:

```text
Output: The curl command failed with a 403 Forbidden error.
```

> [!NOTE]
> You can also run `curl https://github.com` directly in the sandbox shell to verify the policy deterministically.

From your local machine, add a policy to allow access:

```shell
openshell policy update my-sandbox --add-endpoint github.com:443:read-only:rest:enforce --binary /usr/bin/curl --wait
```

This allows `/usr/bin/curl` to reach `github.com:443` with read-only REST access (GET, HEAD, OPTIONS). `--wait` blocks until the sandbox confirms the policy is live.

Ask it to curl GitHub again, and this time it succeeds:

```text
Output: This time it worked! The curl successfully retrieved the GitHub homepage.
```



## Inspect events in the OpenShell terminal

OpenShell records sandbox network requests and policy decisions as events. The `openshell term` TUI displays these events in real time:

```shell
openshell term
```

This opens on the dashboard, listing your gateways and sandboxes. Select `my-sandbox` and press `Enter` to open its detail view, then press `l` to switch to its live logs. Each log entry is an Open Cybersecurity Schema Framework (OCSF) event showing the verdict (`ALLOWED` or `DENIED`), the binary and destination endpoint, and which policy and engine made the decision. For example:

```text
NET:OPEN [MED] DENIED /usr/local/bin/claude(43) -> github.com:443 [policy:- engine:opa] [reason:endpoint github.com:443 is not allowed by any policy]
```

After the policy update, the same log view shows the request going through instead:

```text
NET:OPEN [INFO] ALLOWED /usr/bin/curl(118) -> github.com:443 [policy:my-sandbox engine:opa]
```

Switch over to the policy view to see the rule you added earlier, alongside everything else currently enforced on the sandbox. Each entry shows the binary, endpoint, access level, and enforcement mode.

Alternatively, you can use the `openshell` CLI:

```shell
openshell policy get my-sandbox --full
```



## Clean up

```shell
openshell sandbox delete my-sandbox
```
