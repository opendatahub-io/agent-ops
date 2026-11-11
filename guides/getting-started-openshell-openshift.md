# Get started with OpenShell on OpenShift

> **Midstream Documentation**
>
> The OpenShift install path should be treated as experimental and not used in production.

A walkthrough for installing OpenShell on an OpenShift cluster, exposing the gateway through a `Route`, and running your first sandboxed agent session with network policies controlling what it can reach. By the end you will have a sandboxed agent running against your LLM provider, with an egress policy you control. The gateway setup takes around 15 minutes, excluding provider prerequisites.

New to OpenShell? Read [How OpenShell Works](https://docs.nvidia.com/openshell/latest/about/architecture) first for a quick tour of the architecture: the CLI, the gateway, and the supervisor.

Unless noted otherwise, run all commands on your local machine.
Run them from a checkout of this repository. The example assumes the `openshell`
namespace and local `openshift` gateway registration are unused; choose distinct
names throughout if you already have an installation.

## Prerequisites

- You have access to an isolated test OpenShift cluster. This guide was validated on ROSA with OpenShift 4.19.55 and Red Hat build of Agent Sandbox 0.9.0.
- You have cluster administrator permissions.
- You have installed the OpenShift CLI (`oc`) locally.
- You have Helm installed locally.
- The Red Hat build of Agent Sandbox v0.9.0 is installed on the cluster via the Software Catalog.
- You have obtained credentials for a supported inference provider. This guide uses an Anthropic Claude model served through Google Vertex AI as the example. OpenShell also supports other provider types; configuration requirements differ by provider. See [Provider profiles](https://docs.nvidia.com/openshell/latest/how-it-works/providers/profiles) for the access and credential contract.
- The Google Cloud CLI (`gcloud`) is installed and authenticated with Application Default Credentials (`gcloud auth application-default login`). Only required when using the Vertex AI example provider.

> [!NOTE]
> OpenShell requires a default storage class that supports dynamic volume provisioning. The gateway and sandbox pods use PersistentVolumeClaims (PVCs) for database storage and workspace data.
>
> Managed OpenShift clusters, such as Red Hat OpenShift Service on AWS (ROSA), provide a default storage class. For self-managed clusters, verify that a default storage class is available by running:
>
> ```shell
> oc get storageclass
> ```
>
> The default storage class is marked with the `(default)` annotation. If your cluster does not have a default storage class that supports dynamic provisioning, see [OpenShift Container Platform Storage](https://docs.redhat.com/en/documentation/openshift_container_platform/4.21/html/storage/storage-overview).

## Install the OpenShell CLI

Run the repository's installer script. It downloads the upstream installer from
an immutable commit, verifies its SHA-256 checksum, and installs CLI `v0.1.7`:

```shell
./scripts/install-openshell-cli.sh
openshell --version
```

Expect `openshell 0.1.7`.

## Create the OpenShell namespace

Create a dedicated namespace for OpenShell:

```shell
oc create ns openshell
```

## Determine the route hostname

Determine the `Route` hostname from the cluster's apps domain. This variable is needed during installation so the gateway's TLS certificate includes the external hostname:

```shell
DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
ROUTE_HOST="openshell-openshell.${DOMAIN}"
echo "$ROUTE_HOST"
```

## Install OpenShell with Helm

This guide pairs upstream CLI `0.1.7` and chart `0.1.7` with ODH runtime
build `v0.1.7-rhaiv.0`.
See the [release chart README](https://github.com/NVIDIA/OpenShell/blob/5d25211b25a8fc887e8d5c606d57d6ab22f1d76c/deploy/helm/openshell/README.md)
for the chart's configuration surface.

Both Helm commands below explicitly select the ODH gateway, supervisor and
trusted sandbox runtime images from `quay.io/opendatahub`, using the shared
release tag `v0.1.7-rhaiv.0`. The OpenShift security settings let `restricted-v2`
assign the gateway's UID/GID, and unauthenticated user access stays disabled.

```shell
ODH_IMAGE_TAG=v0.1.7-rhaiv.0
```

The sandbox runtime supplies OpenShell's workload boundary. Select the
application image containing Claude Code in the workload setup below.

Choose **one** database backend. Both installation options use the same
runtime images and Route certificate configuration.

### Option A: SQLite (default)

> [!NOTE]
> SQLite is not recommended for production environments. It is suitable for testing purposes and quick, easy-setup scenarios.

SQLite stores data in a file on a per-pod `PVC` and runs the gateway as a `StatefulSet`. An external database is not required:

```shell
helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.7 \
  --namespace openshell \
  --set gateway.image.registry=quay.io \
  --set gateway.image.repository=opendatahub/odh-openshell-gateway \
  --set gateway.image.tag="${ODH_IMAGE_TAG}" \
  --set supervisor.image.registry=quay.io \
  --set supervisor.image.repository=opendatahub/odh-openshell-supervisor \
  --set supervisor.image.tag="${ODH_IMAGE_TAG}" \
  --set sandboxRuntime.image.registry=quay.io \
  --set sandboxRuntime.image.repository=opendatahub/odh-openshell-sandbox \
  --set sandboxRuntime.image.tag="${ODH_IMAGE_TAG}" \
  --set podSecurityContext=null \
  --set securityContext.runAsUser=null \
  --set server.auth.allowUnauthenticatedUsers=false \
  --set "pkiInitJob.serverDnsNames[0]=${ROUTE_HOST}" \
  --wait --timeout 5m
```

### Option B: External PostgreSQL database

Use external PostgreSQL database when you need multi-replica gateways or a database managed outside this chart. The OpenShell Helm chart does not deploy a database; it is recommended to deploy a PostgreSQL instance separately with your own configuration.

To test OpenShell with PostgreSQL for **testing purposes**, apply the following manifest. It creates a `Secret` with the database credentials and connection URI, a `PVC` for data persistence, a single-replica PostgreSQL `Deployment`, and a `Service` in the `openshell` namespace:

```shell
oc apply -f common/postgresql.yaml
oc rollout status deployment/postgresql -n openshell --timeout=180s
```

The manifest creates a `postgresql-credentials` `Secret` that includes a `uri` key OpenShell can read directly.

If you are connecting to your own PostgreSQL instance, create a `Secret` with a `uri` key containing your connection string:

```shell
oc create secret generic postgresql-credentials -n openshell \
  --from-literal=uri="postgresql://user:pass@host:5432/dbname"
```

Install the OpenShell Helm chart pointing at the `Secret`:

```shell
helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.7 \
  --namespace openshell \
  --set gateway.image.registry=quay.io \
  --set gateway.image.repository=opendatahub/odh-openshell-gateway \
  --set gateway.image.tag="${ODH_IMAGE_TAG}" \
  --set supervisor.image.registry=quay.io \
  --set supervisor.image.repository=opendatahub/odh-openshell-supervisor \
  --set supervisor.image.tag="${ODH_IMAGE_TAG}" \
  --set sandboxRuntime.image.registry=quay.io \
  --set sandboxRuntime.image.repository=opendatahub/odh-openshell-sandbox \
  --set sandboxRuntime.image.tag="${ODH_IMAGE_TAG}" \
  --set podSecurityContext=null \
  --set securityContext.runAsUser=null \
  --set server.auth.allowUnauthenticatedUsers=false \
  --set workload.kind=deployment \
  --set server.externalDbSecret=postgresql-credentials \
  --set "pkiInitJob.serverDnsNames[0]=${ROUTE_HOST}" \
  --wait --timeout 5m
```

`workload.kind=deployment` lets you run multiple gateway replicas that all connect to the same external database. Option A uses `statefulset` instead because each pod needs its own persistent volume for the SQLite file.

### Verify the installation

For either option, the `pkiInitJob.serverDnsNames` value adds the `Route` hostname to the TLS certificate's Subject Alternative Names (SANs). Without it, the CLI rejects the connection because the certificate is not valid for the external hostname.

```shell
oc get pods -n openshell
```

Verify the gateway pod is Ready before continuing. If it is Pending or reports an error, inspect pod events, storage provisioning and SCC admission before changing permissions.

## Expose the gateway

Expose the gateway through an OpenShift `Route`, so the `openshell` CLI can reach it from your local machine.

Create a passthrough `Route` so TLS and mutual TLS (mTLS) terminate at the gateway pod:

```shell
oc create route passthrough openshell \
  --service=openshell \
  --port=8080 \
  --hostname="${ROUTE_HOST}" \
  -n openshell
```

## Connect the OpenShell CLI

### Register the gateway

Register the gateway endpoint with the `openshell` CLI so it knows where to send commands:

```shell
openshell gateway add "https://${ROUTE_HOST}" --local --name openshift
```

The `--name` value determines the directory name under `~/.config/openshell/gateways/`. The TLS bundle extraction below uses `openshift` to match. Registration can import a local package-managed bundle, so extract the cluster bundle after registering. If registration requires certificates first, extract them, register again, then extract them once more.

### Install the TLS client bundle

The OpenShell Helm chart automatically generates an mTLS certificate bundle during installation. The commands below extract that bundle from the cluster so the `openshell` CLI on your local machine can establish a trusted TLS connection to the gateway over the `Route`.

```shell
umask 077
mkdir -p ~/.config/openshell/gateways/openshift/mtls

oc -n openshell get secret openshell-client-tls \
  -o jsonpath='{.data.ca\.crt}'  | base64 -d > ~/.config/openshell/gateways/openshift/mtls/ca.crt

oc -n openshell get secret openshell-client-tls \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > ~/.config/openshell/gateways/openshift/mtls/tls.crt

oc -n openshell get secret openshell-client-tls \
  -o jsonpath='{.data.tls\.key}' | base64 -d > ~/.config/openshell/gateways/openshift/mtls/tls.key

chmod 700 ~/.config/openshell/gateways/openshift/mtls
chmod 600 ~/.config/openshell/gateways/openshift/mtls/tls.key
```

### Verify the connection

Verify the `openshell` CLI can reach the gateway and the connection is healthy:

```shell
openshell status
```

```text
Server Status

  Gateway: openshift
  Server: https://<ROUTE_HOST>
  Status: Connected
  Authentication: Authenticated (mTLS transport)
  Version: 0.1.7-rhaiv.0
```

`Connected` means the `openshell` CLI completed a full mTLS handshake with the gateway running in your cluster. Everything from here on talks to that gateway, not to Kubernetes directly.

## Prepare the Claude Code workload

The default workload, `nvcr.io/nvidia/base/ubuntu:24.04`, does not include
Claude Code or curl. Bring your own workload image containing Claude Code,
curl and Python, built for your cluster's worker architecture. Store it in a
registry your cluster can pull from and set its image reference. The Claude
commands below were validated with Claude Code `2.1.312`; use a version that
supports the `--bare` flag:

```shell
WORKLOAD_IMAGE=registry.example.com/your-team/claude-workload:your-tag
```

The application workload is separate from the ODH runtime images. Do not bake
provider credentials into it; OpenShell supplies credential placeholders when
you attach the provider.

## Configure the Vertex AI provider profile

A profile defines the allowed endpoints, client binaries and credential
refresh behavior. A provider instance stores credentials for that profile.
Attaching the provider grants the sandbox that access. A new gateway loads no
example profiles automatically.

The supplied profile is the release's `google-vertex-ai` example narrowed to
`/usr/local/bin/claude`. Edit `binaries` in the profile to match the canonical
Claude executable path in your workload image before importing it. Import the
profile before creating the provider:

```shell
openshell profile lint -f examples/claude-vertex/google-vertex-ai.yaml
openshell profile import -f examples/claude-vertex/google-vertex-ai.yaml
VERTEX_PROJECT_ID=your-gcp-project
openshell provider create \
  --name vertex-local \
  --type google-vertex-ai \
  --from-gcloud-adc \
  --config "VERTEX_AI_PROJECT_ID=${VERTEX_PROJECT_ID}" \
  --config VERTEX_AI_REGION=global
```

Provider creation configures gateway-managed refresh and mints the first access
token. The ADC file and refresh token remain outside the sandbox. Configure a
model and location enabled in your project. This example uses `global`.

Claude calls Vertex's native API. OpenShell authorizes the request and replaces
its credential placeholder at the approved endpoint.
See [OpenShell Google providers](https://docs.nvidia.com/openshell/latest/how-it-works/providers/google)
and [Claude Code on Vertex AI](https://code.claude.com/docs/en/google-vertex-ai)
for the native provider behavior.

## Create a sandbox

Attach the provider and explicitly select the Claude workload:

```shell
openshell sandbox create --name my-sandbox \
  --from "$WORKLOAD_IMAGE" --provider vertex-local
```

When ready, the command opens a sandbox shell. The provider projects the
project, location and an opaque `GOOGLE_VERTEX_AI_TOKEN` placeholder into new
processes. The real access token remains outside the workload.

## Run Claude Code in the sandbox

From the sandbox shell, use native Vertex mode and send the placeholder as the
Authorization header. OpenShell handles the real token and refresh, so Claude
does not need an ADC file or a gcloud installation in the workload:

```shell
export CLAUDE_CODE_USE_VERTEX=1
export CLAUDE_CODE_SKIP_VERTEX_AUTH=1
export ANTHROPIC_CUSTOM_HEADERS="Authorization: Bearer ${GOOGLE_VERTEX_AI_TOKEN}"
claude --bare --model claude-sonnet-4-6 --print \
  "Reply with exactly VERTEX_OK and nothing else."
```

Expect `VERTEX_OK`. A real model response establishes that the provider,
profile, workload and native endpoint work together. To continue interactively,
run `claude --bare --model claude-sonnet-4-6` in the same shell.
`--model` selects the model used by the client. Use a model ID enabled in your
project.

## Update the egress policy

Ask Claude, or your agent of choice, to curl `https://github.com`. The default policy blocks unrelated destinations such as GitHub. Run the request directly so the result is deterministic:

```shell
curl -I --max-time 20 https://github.com
```

Expect curl to fail. Confirm an OCSF `DENIED` event for the curl binary and
`github.com:443` in the audit logs.

From your local machine, resolve curl's canonical executable path inside your
workload image and allow that binary:

```shell
CURL_BINARY=$(openshell sandbox exec my-sandbox -- sh -c 'readlink -f "$(command -v curl)"')
openshell policy update my-sandbox \
  --add-endpoint github.com:443:read-only:rest:enforce \
  --binary "$CURL_BINARY" --wait
```

This allows the resolved curl executable to reach `github.com:443` with
read-only REST access (GET, HEAD, OPTIONS). `--wait` blocks until the sandbox
confirms the policy is live.

From the sandbox shell, repeat the request. Expect HTTP 200:

```shell
curl -I --max-time 20 https://github.com
```

A different binary should remain denied:

```shell
python3 -c "import urllib.request; urllib.request.urlopen('https://github.com', timeout=20)"
```

## Inspect audit events

OpenShell records network requests and policy decisions as OCSF events.
Inspect the CLI logs and effective policy:

```shell
openshell logs my-sandbox --source sandbox -n 300
openshell policy get my-sandbox --full
```

Match the destination and binary across the initial denial, allowed curl
request and denied Python request. The logs distinguish policy denial from a
remote service error.

You can also inspect the sandbox interactively with `openshell term`.

## Troubleshooting

- **Status shows Disconnected.** Verify the `Route` exists (`oc get route -n openshell`) and that the TLS bundle directory name matches the `--name` used in `gateway add`.
- **Certificate validation error.** The `pkiInitJob.serverDnsNames` value may not match the `Route` hostname. Uninstall and reinstall the Helm chart with the correct value.
- **Gateway pod not running.** Inspect pod events and PVC provisioning before changing SCC permissions.
- **Unknown provider type.** Import the matching profile before creating the provider.
- **Vertex credential errors.** Check provider refresh status and the actual model/project permissions; do not put the ADC file in the sandbox.
- **Claude missing.** Use the explicit workload image; the trusted sandbox runtime does not install applications.

## Known limitations

This is an experimental OpenShift example. Validate the exact CLI, chart,
runtime images, Agent Sandbox build, workload and model combination you use.
A successful gateway install is not a model or policy-enforcement test.

## Uninstallation

Remove the OpenShell installation and local configuration when you are finished exploring:

```shell
openshell sandbox delete my-sandbox
openshell provider delete vertex-local
openshell profile delete google-vertex-ai
helm uninstall openshell -n openshell --wait
oc delete ns openshell
openshell gateway remove openshift
```
