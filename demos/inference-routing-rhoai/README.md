# Inference Routing with RHOAI via OpenShell

Route sandbox inference traffic through a token-authenticated Red Hat OpenShift AI (RHOAI)-served model using the OpenShell privacy router, without exposing credentials to the sandbox. Complete the following steps to configure the routing.

## Prerequisites

- An OpenShift cluster with RHOAI installed is available.
- A model is deployed via RHOAI model serving, with the `InferenceService` in the `Ready` state. For deployment, see [Deploying models](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/deploying_models/deploying_models) or [Enabling the model serving platform](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/configuring_your_model-serving_platform/configuring_model_servers#enabling-the-model-serving-platform_rhoai-admin).
  - Authentication is enabled on the model (`security.opendatahub.io/enable-auth: "true"`).
  - An OpenShift `Route` is created for the model.
- The OpenShell gateway is deployed on the cluster and connected locally (`openshell status` shows `Connected`).
- The OpenShift CLI (`oc`) is installed and authenticated to the cluster.
- The `openshell` CLI is installed.

## 1. Gather the model endpoint and a token

Gather the model namespace, name, and route, then export an authentication token:

```bash
NAMESPACE=<your-model-namespace>
MODEL_NAME=<your-model-name>
MODEL_ROUTE=$(oc get route "$MODEL_NAME" -n "$NAMESPACE" -o jsonpath='{.spec.host}')
export OPENAI_API_KEY=$(oc whoami -t)
```

Verify that the model requires authentication:

```bash
curl -s https://$MODEL_ROUTE/v1/models
# Unauthorized
```

> OpenShift access tokens expire after 24 hours by default.
> To refresh the token stored in the provider when it expires, run the following command:
> ```bash
> openshell provider update rhoai --credential OPENAI_API_KEY
> ```
>
> For production use, create a dedicated ServiceAccount with the minimum permissions needed to call the model route, and use its token instead of `oc whoami -t`. Personal OAuth tokens are short-lived and tied to your user session.

## 2. Register the RHOAI model as an OpenShell provider

Register the model as a provider, passing the credential key name only. OpenShell reads the value from the environment, so the token is not exposed in process arguments:

```bash
openshell provider create \
  --name rhoai \
  --type openai \
  --credential OPENAI_API_KEY \
  --config "OPENAI_BASE_URL=https://${MODEL_ROUTE}/v1"
```

The gateway stores the token and never exposes it to sandboxes.

## 3. Configure inference routing

Set the provider and model for inference routing:

```bash
openshell inference set \
  --provider rhoai \
  --model "$MODEL_NAME" \
  --no-verify
```

> `--no-verify` disables TLS verification between the OpenShell gateway and the RHOAI model route. This is required when the gateway pod does not yet trust the OpenShift service CA. For production deployments, mount the OpenShift service CA bundle into the gateway pod and omit this flag. See the [OpenShift service CA documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/latest/html/security_and_compliance/certificate-types-and-descriptions#service-ca-certificates_ocp-certificates) for details.

Confirm the routing configuration:

```bash
openshell inference get
# Route:    inference.local
# Provider: rhoai
# Model:    <your-model-name>
```

## 4. Create a sandbox

Create a sandbox that routes inference through the provider:

```bash
openshell sandbox create \
  --name inference-demo \
  --provider rhoai \
  --env OPENAI_BASE_URL=https://inference.local/v1 \
  -- sleep 300
```

The `--env OPENAI_BASE_URL` override routes all OpenAI SDK calls through the privacy router rather than directly to the model route.

## 5. Test the flow

### Inference through `inference.local` succeeds

Send an inference request through the privacy router:

```bash
openshell sandbox exec -n inference-demo -- \
  curl -sf https://inference.local/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }'
```

The privacy router intercepts the request, strips any sandbox-supplied credentials, injects the real RHOAI token, and forwards the request to the model route. The sandbox receives the model response without ever holding the token.

### Direct route access is blocked

Attempt to reach the model route directly from the sandbox:

```bash
openshell sandbox exec -n inference-demo -- \
  curl --max-time 5 https://$MODEL_ROUTE/v1/models
# curl: (56) CONNECT tunnel failed, response 403
```

Exit code 56 with a proxy 403 confirms a network policy denial, not an authentication failure against a reachable endpoint.

## 6. Observe OCSF audit events

The OpenShell supervisor emits structured OCSF `HttpActivity` events for every network action.

View the sandbox-side events to see what the sandbox attempted:

```bash
openshell logs inference-demo --source sandbox | grep "NET:"
# NET:OPEN ALLOWED inference.local:443
```

View the gateway-side events to see the actual upstream destination, with credentials stripped:

```bash
openshell logs inference-demo --source gateway | grep "openshell_router"
# [openshell_router] routing proxy inference request endpoint=https://...
```

Sandbox inference requests pass through the privacy router and appear in OCSF audit logs; the real model endpoint and token remain hidden from the sandbox.

## Cleanup

Delete the sandbox and provider when you are finished:

```bash
openshell sandbox delete inference-demo
openshell provider delete rhoai
```
