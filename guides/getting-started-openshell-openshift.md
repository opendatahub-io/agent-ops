# Get started with OpenShell on OpenShift

> **Midstream Documentation**
>
> The OpenShift install path should be treated as experimental and not used in production.

A walkthrough for installing OpenShell on an OpenShift cluster, exposing the gateway through a `Route`, and running your first sandboxed agent session with network policies controlling what it can reach. By the end you will have a sandboxed agent running against your LLM provider, with an egress policy you control. The guide takes around 15 minutes.

New to OpenShell? Read [How OpenShell Works](https://docs.nvidia.com/openshell/latest/about/how-it-works) first for a quick tour of the architecture: the CLI, the gateway, and the supervisor.

Unless noted otherwise, run all commands on your local machine.

## Prerequisites

- You have access to a test OpenShift cluster running version 4.21 or later.
- You have cluster administrator permissions.
- You have installed the OpenShift CLI (`oc`) locally.
- You have Helm installed locally.
- The Red Hat build of Agent Sandbox v0.9.0 is installed on the cluster in the `openshift-operators` namespace via the Software Catalog.
- You have an [OpenAI API key](https://platform.openai.com/api-keys) and access to a model supported by the Codex CLI through the OpenAI API. This guide uses OpenAI with API key authentication. OpenShell also supports other provider types; configuration requirements differ by provider. Check the [Supported Provider Types](https://docs.nvidia.com/openshell/latest/sandboxes/manage-providers#supported-provider-types) table for details.

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

From a checked-out copy of this repository, run the installer wrapper. The
wrapper downloads `install.sh` from the immutable upstream commit for OpenShell
v0.1.2 and verifies its pinned SHA-256 checksum before execution:

```shell
./scripts/install-openshell-cli.sh
```

## Create the OpenShell namespace

Create the namespace before installing the OpenShell Helm chart. OpenShell 0.1.2 uses OpenShift's assigned non-root UID:

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

See the [OpenShell Helm chart README.md file](https://github.com/NVIDIA/OpenShell/blob/main/deploy/helm/openshell/README.md) for full chart details.

Set the tag shared by the Red Hat gateway, supervisor, and sandbox runtime images:

```shell
ODH_IMAGE_TAG=v0.1.2-rhaiv.0
```

Choose a database backend before installing. OpenShell supports SQLite (the default) and external PostgreSQL. Choose **one** of the two options below.

> [!WARNING]
> Both examples below set `allowUnauthenticatedUsers=true`. This bypasses user authentication and treats every API request as a trusted local developer. It is a convenience shortcut for single-user test clusters and should not be used on shared clusters.

### Option A: SQLite (default)

> [!NOTE]
> SQLite is not recommended for production environments. It is suitable for testing purposes and quick, easy-setup scenarios.

SQLite stores data in a file on a per-pod `PVC` and runs the gateway as a `StatefulSet`. An external database is not required:

```shell
helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.2 \
  --namespace openshell \
  --set global.image.registry=quay.io/opendatahub \
  --set global.image.tag="${ODH_IMAGE_TAG}" \
  --set gateway.image.repository=odh-openshell-gateway \
  --set supervisor.image.repository=odh-openshell-supervisor \
  --set sandboxRuntime.image.repository=odh-openshell-sandbox \
  --set podSecurityContext.fsGroup=null \
  --set securityContext.runAsUser=null \
  --set server.auth.allowUnauthenticatedUsers=true \
  --set "pkiInitJob.serverDnsNames[0]=${ROUTE_HOST}"
```


### Option B: External PostgreSQL database

Use external PostgreSQL database when you need multi-replica gateways or a database managed outside this chart. The OpenShell Helm chart does not deploy a database; it is recommended to deploy a PostgreSQL instance separately with your own configuration.

To test OpenShell with PostgreSQL for **testing purposes**, apply the following manifest. It creates a `Secret` with the database credentials and connection URI, a `PVC` for data persistence, a single-replica PostgreSQL `Deployment`, and a `Service` in the `openshell` namespace:

```shell
oc apply -f https://raw.githubusercontent.com/opendatahub-io/agent-ops/main/common/postgresql.yaml
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
  --version 0.1.2 \
  --namespace openshell \
  --set global.image.registry=quay.io/opendatahub \
  --set global.image.tag="${ODH_IMAGE_TAG}" \
  --set gateway.image.repository=odh-openshell-gateway \
  --set supervisor.image.repository=odh-openshell-supervisor \
  --set sandboxRuntime.image.repository=odh-openshell-sandbox \
  --set workload.kind=deployment \
  --set server.externalDbSecret=postgresql-credentials \
  --set podSecurityContext.fsGroup=null \
  --set securityContext.runAsUser=null \
  --set server.auth.allowUnauthenticatedUsers=true \
  --set "pkiInitJob.serverDnsNames[0]=${ROUTE_HOST}"
```

`workload.kind=deployment` lets you run multiple gateway replicas that all connect to the same external database. Option A uses `statefulset` instead because each pod needs its own persistent volume for the SQLite file.

### Verify the installation

For either option, the `pkiInitJob.serverDnsNames` value adds the `Route` hostname to the TLS certificate's Subject Alternative Names (SANs). Without it, the CLI rejects the connection because the certificate is not valid for the external hostname.

```shell
oc get pods -n openshell
```

Verify the gateway pod is `Running` before continuing. If it is stuck in `CreateContainerConfigError` or `Pending`, inspect its events and confirm the chart's Agent Sandbox API preflight passed.

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

### Register the gateway

Register the gateway endpoint with the `openshell` CLI so it knows where to send commands. The `--local` option requires the TLS bundle above to exist first:

```shell
openshell gateway add "https://${ROUTE_HOST}" --local --name openshift
openshell gateway select openshift
```

The `--name` value matches the directory name under `~/.config/openshell/gateways/` used for the TLS bundle.



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
  Version: 0.1.2-rhaiv.0
```

`Connected` means the `openshell` CLI completed a full mTLS handshake with the gateway running in your cluster. Everything from here on talks to that gateway, not to Kubernetes directly.

## Configure an inference provider

This guide uses OpenAI with the Codex CLI. OpenShell 0.1.2 uses an imported provider profile and a provider attached to the sandbox. Download and review the release-pinned OpenAI profile, then replace its binary list with the Codex executable paths used by the example sandbox image:

```shell
curl -fsSLo openai.yaml \
  https://raw.githubusercontent.com/NVIDIA/OpenShell/6648bd0c290efbc41ba131ee9831ee45cd431f94/providers/openai.yaml
sed '/^binaries:/d' openai.yaml > openai-codex.yaml
cat >> openai-codex.yaml <<'EOF'
binaries:
  - /usr/bin/codex
  - /usr/lib/node_modules/@openai/**/codex
EOF
openshell profile lint -f openai-codex.yaml --global
openshell profile import -f openai-codex.yaml --global

read -rsp 'OpenAI API key: ' OPENAI_API_KEY
echo
export OPENAI_API_KEY
openshell provider create \
  --name my-openai \
  --type openai \
  --global-profile \
  --credential OPENAI_API_KEY
unset OPENAI_API_KEY
```

Run the key prompt in Bash. The CLI reads `OPENAI_API_KEY` from your local environment and stores the credential with the gateway. Sandboxes receive an `OPENAI_API_KEY` placeholder, not the real key. OpenShell substitutes the real key only on requests to `api.openai.com` authorized by the profile. The binary glob covers the native Codex executable installed by npm for the image's architecture.

OpenShell 0.1.2 removed `openshell inference set` and the `inference.local` endpoint. Configure Codex to call the OpenAI API and select the model in the client, as shown below.

Using a different provider? See the [provider profiles](https://docs.nvidia.com/openshell/how-it-works/providers/profiles) reference for the available examples and their credential shapes.

## Create a sandbox

```shell
openshell sandbox create --name my-sandbox \
  --from ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e \
  --provider my-openai
```

This starts a sandbox pod in the `openshell` namespace from the pinned [OpenShell Community base image](https://github.com/NVIDIA/OpenShell-Community/tree/fffb6b2248ff6ba585f50517f3711b08122089f2/sandboxes/base), which includes the Codex CLI. When the sandbox is ready, the command opens an interactive shell in the sandbox. The supervisor configures credential routing, audit logging, policy enforcement, and the associated Open Policy Agent (OPA) policy engine.

## Run the Codex CLI in the sandbox

From the shell you just landed in, authenticate Codex with the OpenShell-issued placeholder and launch it. Replace `<openai-model-id>` with a model ID supported by Codex and available to your OpenAI API project:

```shell
printenv OPENAI_API_KEY | codex login --with-api-key
codex --model "<openai-model-id>" \
  -c model_providers.openai.supports_websockets=false \
  --sandbox danger-full-access \
  --ask-for-approval on-request
```

The login command saves the placeholder for API key authentication. Disabling WebSocket transport keeps inference on HTTP requests covered by the profile's REST enforcement. `--sandbox danger-full-access` lets OpenShell enforce filesystem and network access for Codex's shell commands; `--ask-for-approval on-request` keeps Codex's approval prompts. Use these settings inside the OpenShell sandbox. See the [Codex authentication guide](https://developers.openai.com/codex/auth) and [CLI reference](https://developers.openai.com/codex/cli/reference) for details.

Ask Codex to reply with `OK` to verify model access before continuing. A successful response checks the provider attachment, binary policy, credential substitution, and OpenAI model access. Provider creation alone does not verify model access.

## Update the egress policy

Ask Codex to run `/usr/bin/curl https://github.com`. The default policy blocks this request:

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
NET:OPEN [MED] DENIED /usr/bin/curl(43) -> github.com:443 [policy:- engine:opa] [reason:endpoint github.com:443 is not allowed by any policy]
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



## Troubleshooting

- **Status shows Disconnected.** Verify the `Route` exists (`oc get route -n openshell`) and that the TLS bundle directory name matches the `--name` used in `gateway add`.
- **Certificate validation error.** The `pkiInitJob.serverDnsNames` value may not match the `Route` hostname. Uninstall and reinstall the Helm chart with the correct value.
- **Gateway pod not running.** Inspect its events with `oc describe pod <gateway-pod-name> -n openshell` and confirm the Agent Sandbox controller serves a supported API. If needed, delete the pod to trigger a restart.
- **Sandbox pod not running.** Inspect its events with `oc describe pod <sandbox-pod-name> -n openshell` and check the Agent Sandbox controller logs. If no pod was created, check namespace events with `oc get events -n openshell --sort-by=.metadata.creationTimestamp`. The example image must be pullable by the cluster, and the node must permit unprivileged Landlock and seccomp user notification.

## Uninstallation

Remove the OpenShell installation and local configuration when you are finished exploring:

```shell
openshell sandbox delete my-sandbox
helm uninstall openshell -n openshell
oc delete ns openshell
openshell gateway remove openshift
rm -rf ~/.config/openshell/gateways/openshift
```
