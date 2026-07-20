# Getting Started with OpenShell on OpenShift

> **Midstream Documentation**
>
> The OpenShift install path should be treated as experimental and not used in production.

A walkthrough for installing OpenShell on an OpenShift cluster, exposing the gateway through a `Route`, and running your first sandboxed agent session with network policies controlling what it can reach.

New to OpenShell? Read [How OpenShell Works](https://docs.nvidia.com/openshell/latest/about/how-it-works) first for a quick tour of the architecture: the CLI, the gateway, and the supervisor.

## Prerequisites

- An OpenShift cluster, v4.21+
- The OpenShell CLI v0.0.85 installed locally:

```shell
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | OPENSHELL_VERSION=v0.0.85 sh
```

- The Red Hat build of Agent Sandbox v0.9.0, installed on the cluster via the Software Catalog.
- Credentials for an LLM provider. This guide uses an Anthropic Claude model served through Google Vertex AI as the example, but any provider works just as well. Check the [Supported Provider Types](https://docs.nvidia.com/openshell/latest/sandboxes/manage-providers#supported-provider-types) table to use something else (Anthropic direct, OpenAI, NVIDIA API Catalog, AWS Bedrock, and more are all supported).



## Namespace Setup

Pre-create the namespace so the SCC binding can be applied before the chart installs:

```shell
oc create ns openshell
oc adm policy add-scc-to-user privileged -z openshell-sandbox -n openshell
```



## Helm Installation

See the [Helm chart README](https://github.com/NVIDIA/OpenShell/blob/main/deploy/helm/openshell/README.md) for full chart details.

```shell
helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.0.85 \
  --namespace openshell \
  --set podSecurityContext.fsGroup=null \
  --set securityContext.runAsUser=null \
  --set server.auth.allowUnauthenticatedUsers=true
```



## Use a Route to Communicate with the Gateway

Expose the `openshell-gateway` through an OpenShift `Route`, so the CLI can reach it directly from your laptop.

Determine the Route hostname from the cluster's apps domain:

```shell
DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
ROUTE_HOST="openshell-openshell.${DOMAIN}"
echo "$ROUTE_HOST"
```

Create a `passthrough` Route so TLS/mTLS terminates at the gateway pod:

```shell
oc create route passthrough openshell \
  --service=openshell \
  --port=8080 \
  --hostname="${ROUTE_HOST}" \
  -n openshell
```



### Register the Gateway

Register the gateway endpoint with your local CLI so it knows where to send commands:

```shell
openshell gateway add "https://${ROUTE_HOST}" --local --name openshift
```



### Install the TLS Client Bundle

The OpenShell Helm chart auto-generates a mutual TLS (mTLS) certificate bundle during installation. The commands below extract that bundle from the cluster so the `openshell` CLI on your local machine can establish a trusted TLS connection to the gateway over the Route.

```shell
mkdir -p ~/.config/openshell/gateways/openshift/mtls

kubectl -n openshell get secret openshell-client-tls \
  -o jsonpath='{.data.ca\.crt}'  | base64 -d > ~/.config/openshell/gateways/openshift/mtls/ca.crt

kubectl -n openshell get secret openshell-client-tls \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > ~/.config/openshell/gateways/openshift/mtls/tls.crt

kubectl -n openshell get secret openshell-client-tls \
  -o jsonpath='{.data.tls\.key}' | base64 -d > ~/.config/openshell/gateways/openshift/mtls/tls.key
```



## Status Check

Verify the CLI can reach the gateway and the connection is healthy:

```shell
openshell status
```

```text
Server Status

  Gateway: openshift
  Server: https://<ROUTE_HOST>
  Status: Connected
  Version: 0.0.85
```

`Connected` means the CLI completed a full mTLS handshake with the gateway running in your cluster. Everything from here on talks to that gateway, not to Kubernetes directly.

## Provider Creation

Register the LLM provider credentials with the gateway so sandboxes can use them for inference. This guide uses Google Vertex AI with Application Default Credentials as the example:

```shell
openshell provider create \
  --name <provider-name> \
  --type google-vertex-ai \
  --from-gcloud-adc \
  --config VERTEX_AI_PROJECT_ID=<gcp-project-id> \
  --config VERTEX_AI_REGION=<gcp-region>
```

The gateway now holds these credentials on your behalf. Sandboxes never see them directly, they're injected at the network layer.

Using a different provider? See the [Supported Provider Types](https://docs.nvidia.com/openshell/latest/sandboxes/manage-providers#supported-provider-types) reference for the full list. Anthropic, OpenAI, NVIDIA API Catalog, AWS Bedrock, GitHub Copilot, and others are all supported, each with its own `--type` and credential shape.

## Inference Settings

Enable the v2 provider pipeline so the gateway supports credential injection and inference routing, then configure which model the `inference.local` endpoint routes to inside sandboxes:

```shell
openshell settings set --global --key providers_v2_enabled --value true --yes

openshell inference set --provider <provider-name> --model <model-name>
```



## Sandbox Creation

```shell
openshell sandbox create --name my-sandbox
```

This starts a sandbox pod in the `openshell` namespace. Once it's ready, you'll land directly in an interactive shell session inside the sandbox. By that point, the supervisor has already wired up everything you need: the agent process, inference routing, audit logging, the policy proxy, and the OPA policy engine.

## Claude Execution (Inside the Sandbox)

From the shell you just landed in, launch Claude Code:

```shell
ANTHROPIC_BASE_URL="https://inference.local" \
ANTHROPIC_API_KEY=unused \
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1 \
claude --bare
```

This routes model traffic through the gateway instead of Anthropic directly, so it can inject your real Vertex AI credentials. `--bare` skips login since auth is already handled by the provider. `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` prevents Claude Code from sending beta headers that the proxy doesn't yet pass through cleanly. Other agents follow the same pattern; see [Supported Agents](https://docs.nvidia.com/openshell/latest/about/supported-agents).

## Egress Policy Update

Ask Claude, or your agent of choice, to curl `https://github.com`. The default policy blocks it:

```text
Output: The curl command failed with exit code 56 and a 403 Forbidden error.
```

Add a policy to allow access:

```shell
openshell policy update my-sandbox --add-endpoint github.com:443:read-only:rest:enforce --binary /usr/bin/curl --wait
```

This allows `/usr/bin/curl` to reach `github.com:443` with read-only REST access (GET, HEAD, OPTIONS). `--wait` blocks until the sandbox confirms the policy is live.

Ask it to curl GitHub again, and this time it succeeds:

```text
Output: This time it worked! The curl successfully retrieved the GitHub homepage.
```



## OpenShell Terminal

Everything you just did left a trail. The `openshell term` TUI is where you can watch it live: every request the agent made, every policy decision, and every change you pushed.

```shell
openshell term
```

This opens on the dashboard, listing your gateways and sandboxes. Select `my-sandbox` and press `Enter` to open its detail view, then press `l` to switch to its live logs:



After the policy update, the same log view shows the request going through instead:



Switch over to the policy view to see the rule you added earlier, alongside everything else currently enforced on the sandbox:



Alternatively, you can use the CLI:

```shell
openshell policy get my-sandbox --full
```



## Known Limitations

**Privileged SCC requirement.** The sandbox pod runs with the `privileged` Security Context Constraint. This is needed because the supervisor sets up its own network namespace, nftables rules, and Landlock LSM policies for the agent process. Operations that require elevated kernel capabilities. This is a meaningful security exposure; for GA, we plan to replace it with a custom, narrowly-scoped permission set or similar. Until then, treat this install path as experimental and do not use it in production.

## Uninstallation

You're done. Here's how to clean up when you're finished exploring:

```shell
helm uninstall openshell -n openshell
```
