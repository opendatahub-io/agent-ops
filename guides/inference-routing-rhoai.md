# Use an RHOAI model from an OpenShell sandbox

Call a token-authenticated Red Hat OpenShift AI (RHOAI) model using its native
OpenAI-compatible API. With OpenShell `0.1.7` and ODH runtime
`v0.1.7-rhaiv.0`, a provider profile defines the allowed endpoint, client binaries
and credential injection. The client calls the real model URL; the supervisor
injects the token without exposing its value to the sandbox.

## Prerequisites

- An OpenShift cluster with RHOAI installed is available.
- A model is deployed via RHOAI model serving, with the `InferenceService` in the `Ready` state. For deployment, see [Deploying models](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/deploying_models/deploying_models) or [Enabling the model serving platform](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/configuring_your_model-serving_platform/configuring_model_servers#enabling-the-model-serving-platform_rhoai-admin).
  - Authentication is enabled on the model (`security.opendatahub.io/enable-auth: "true"`).
  - An OpenShift `Route` is created for the model.
  - The model uses a vLLM backend exposing the OpenAI-compatible `/v1/models` and `/v1/chat/completions` APIs.
- Complete the [getting-started guide](getting-started-openshell-openshift.md) with upstream CLI/chart `0.1.7` and ODH runtime `v0.1.7-rhaiv.0`. The gateway is connected locally (`openshell status` shows `Connected`).
- The OpenShift CLI (`oc`) is installed and authenticated to the cluster.
- Bring a workload image containing `curl`, Python 3 and a shell, accessible to the cluster. Python is used only for the denied-client check. The default NVIDIA Ubuntu workload image does not include these clients.

Run commands locally from a checkout of this repository. The examples assume
`rhoai`, `rhoai-openai` and `inference-demo` are unused names in your workspace.

## 1. Gather the model endpoint and a token

Gather the model namespace, name, and route, then export an authentication token:

```shell
NAMESPACE='<your-model-namespace>'
MODEL_NAME='<your-model-name>'
MODEL_ROUTE=$(oc get route "$MODEL_NAME" -n "$NAMESPACE" -o jsonpath='{.spec.host}')
export OPENAI_API_KEY=$(oc whoami -t)
```

Verify that the model requires authentication:

```shell
curl -s "https://${MODEL_ROUTE}/v1/models"
# Unauthorized
```

OpenShift access tokens expire according to cluster configuration. For production
use, create a dedicated ServiceAccount with the minimum permissions needed to
call the model route, and use its token instead of `oc whoami -t`.

The Route certificate must be trusted by the supervisor. If the Route uses a
private CA, configure that trust before continuing. Chart `0.1.7` supports
`supervisor.network.additionalCaConfigMapName`, referencing a ConfigMap in the
gateway namespace with the CA certificates in its `ca.crt` key. Configure it
before creating the sandbox. Disabling verification in a local client does not
configure the supervisor's upstream trust.

## 2. Import a profile and register the provider

Copy the [RHOAI profile example](../examples/rhoai/openai-compatible.yaml) and
substitute the Route hostname:

```shell
sed "s/model.example.com/${MODEL_ROUTE}/" \
  examples/rhoai/openai-compatible.yaml > /tmp/rhoai-profile.yaml
```

The profile permits `/usr/bin/curl` to reach this host on port 443 and injects
`OPENAI_API_KEY` as a bearer token. Set `binaries` to the canonical path of curl
in your workload image (resolve symlinks with `readlink -f`). If an application
uses an SDK, allow its actual executable or interpreter instead. Do not add the
Python interpreter used for the denied-client check below.

Import the profile before creating the provider:

```shell
openshell profile lint -f /tmp/rhoai-profile.yaml
openshell profile import -f /tmp/rhoai-profile.yaml
openshell provider create \
  --name rhoai \
  --type rhoai-openai \
  --credential OPENAI_API_KEY
unset OPENAI_API_KEY
```

Only the credential key name appears in the command arguments. The CLI reads its
value from the local environment and stores it at the gateway. The sandbox
receives a placeholder that the supervisor resolves on allowed requests.

To refresh an expired token, export a new value locally and update the provider:

```shell
export OPENAI_API_KEY=$(oc whoami -t)
openshell provider update rhoai --credential OPENAI_API_KEY
unset OPENAI_API_KEY
```

## 3. Create a sandbox

Set your workload image and attach the provider. `OPENAI_BASE_URL` is the native
model endpoint:

```shell
WORKLOAD_IMAGE='<your-registry/your-workload-image:tag>'
openshell sandbox create \
  --name inference-demo \
  --from "$WORKLOAD_IMAGE" \
  --provider rhoai \
  --env "OPENAI_BASE_URL=https://${MODEL_ROUTE}/v1" \
  --detach -- sleep infinity
```

The provider profile supplies endpoint policy and credential injection.

## 4. Discover the served model ID

List the models from inside the sandbox:

```shell
openshell sandbox exec inference-demo -- sh -c '
  curl --fail --show-error --silent --max-time 30 \
    "${OPENAI_BASE_URL}/models" \
    -H "Authorization: Bearer ${OPENAI_API_KEY}"
'
```

Use the `id` returned in `data` as `MODEL_ID`. The served model ID can differ from
the Kubernetes resource name. Every chat request must include it explicitly.

## 5. Test the flow

### An allowed client receives a model response

Replace `<served-model-id>` with the ID discovered above:

```shell
openshell sandbox exec inference-demo -- sh -c '
  MODEL_ID="<served-model-id>"
  curl --fail --show-error --silent --max-time 120 \
    "${OPENAI_BASE_URL}/chat/completions" \
    -H "Authorization: Bearer ${OPENAI_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL_ID}\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"max_tokens\":100}"
'
```

Expect an OpenAI-compatible JSON response containing `choices`. The real token
is injected by the supervisor; the authorization header above contains only the
sandbox's placeholder.

### An unapproved binary is blocked

Try the same host with Python, which is absent from the profile's `binaries`:

```shell
openshell sandbox exec inference-demo -- python3 -c '
import os
import urllib.request
urllib.request.urlopen(os.environ["OPENAI_BASE_URL"] + "/models", timeout=10)
'
```

Expect a connection or permission error. Confirm a policy denial for the Python
binary in the audit logs below; an HTTP 401 alone would only establish missing
credentials at a reachable endpoint. An allowed curl request to the native Route
is expected to succeed.

## 6. Observe OCSF audit events

Inspect supervisor audit events:

```shell
openshell logs inference-demo --source sandbox -n 300
```

Look for allowed curl activity and denied Python activity for your model Route,
including the executable identity and destination. The native endpoint is visible
to the client and in audit logs; the token value stays outside the sandbox.

## Cleanup

Delete the sandbox, provider and custom profile when finished:

```shell
openshell sandbox delete inference-demo
openshell provider delete rhoai
openshell profile delete rhoai-openai
rm /tmp/rhoai-profile.yaml
```
