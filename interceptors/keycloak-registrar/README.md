# Keycloak Registrar Interceptor

This OpenShell gateway interceptor implements the `openshell.gateway_interceptor.v1.GatewayInterceptor`
service. It registers each sandbox's SPIFFE ID as a Keycloak federated client
when the sandbox is created and removes it when the sandbox is deleted, so
provider token-exchange profiles work with per-sandbox SPIFFE identities.

Keycloak matches a SPIFFE JWT-SVID to a client by exact subject. With one SPIFFE
ID per sandbox, every sandbox needs its own client, and the gateway's client
needs an audience for it. This interceptor creates them from the gateway's own
API events, keyed on the OpenShell sandbox ID, so it works with every compute
driver.

## What It Does

| Gateway event | Phase | Keycloak change |
|---|---|---|
| `CreateSandbox` | `post_commit` | Creates or updates the federated client `<SPIFFE ID>` (`federated-jwt`, standard token exchange), adds the configured default client scopes, creates the audience client scope `<SPIFFE ID>`, and adds it to the gateway client. |
| `DeleteSandbox` | `post_commit` | Removes the audience scope from the gateway client, then deletes the scope and the client. |

The SPIFFE ID comes from `SPIFFE_ID_TEMPLATE` (default
`{trustDomain}/openshell/sandbox/{namespace}.{podName}.{sandboxID}`). It must
equal the ClusterSPIFFEID template that issues supervisor SVIDs. The default
matches the pod-qualified form Gordon Sim proposed upstream in
[NVIDIA/OpenShell#3100](https://github.com/NVIDIA/OpenShell/pull/3100): a
sandbox ID alone is spoofable, because any pod that copies the
`openshell.ai/sandbox-id` annotation would receive the same ID. Allowed
placeholders are `{trustDomain}`, `{namespace}` (from `SANDBOX_NAMESPACE`),
`{podName}` (the supervisor pod, `os-supervisor-<lowercased sandbox ID>`) and
`{sandboxID}`; all of them are known for both `CreateSandbox` and
`DeleteSandbox`.

Object names match [perilinkle](https://github.com/grs/perilinkle), so either
can manage the same realm.

Every evaluation returns `allowed = true` with `keycloak_registrar.spiffe_id`
and `keycloak_registrar.outcome` log annotations, which appear in the gateway's
interceptor logs.

## Security

- The gateway calls the interceptor over HTTPS. The interceptor verifies the
  gateway extension token on every RPC, including `Describe`: `typ` is
  `openshell-ext+jwt`, the algorithm is pinned to EdDSA, the signature verifies
  against the gateway public key, `aud` equals `INTERCEPTOR_AUDIENCE`, `iss` is
  `openshell-gateway:<GATEWAY_ID>`, and `caller_kind` is `gateway`.
- `Describe` returns `expected_audience`, so the gateway refuses to start if its
  registration uses a different audience.
- Keycloak access uses a `client_credentials` service account. It needs the
  realm-management roles `manage-clients`, `view-clients`, and `query-clients`.
- Mount only `public.pem` from the gateway's JWT key Secret.

## Configuration

| Variable | Required | Default |
|---|---|---|
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | Yes | |
| `GATEWAY_PUBLIC_KEY_FILE` | Yes | |
| `GATEWAY_ID` | Yes | |
| `INTERCEPTOR_AUDIENCE` | No | `urn:openshell:extension:interceptor:keycloak-registrar` |
| `SPIFFE_TRUST_DOMAIN` | Yes | |
| `SPIFFE_ID_TEMPLATE` | No | `{trustDomain}/openshell/sandbox/{namespace}.{podName}.{sandboxID}` |
| `SANDBOX_NAMESPACE` | When the template uses `{namespace}` | Namespace of the sandbox supervisor pods; the manifest sets the registrar's own namespace (Shared workspace mode) |
| `KEYCLOAK_URL`, `KEYCLOAK_REALM` | Yes | |
| `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET` | Yes | |
| `KEYCLOAK_IDENTITY_PROVIDER` | No | `spiffe` |
| `GATEWAY_CLIENT_ID` | Yes | |
| `DEFAULT_CLIENT_SCOPES` | No | Comma-separated client scope names |
| `LISTEN_ADDR` | No | `0.0.0.0:8443` |

## Build and Deploy

The easiest path is `scripts/token-exchange/05-deploy-registrar.sh`, which builds
the image on your cluster, deploys it, and registers it with the gateway. See
[the token-exchange guide](../../guides/spiffe-token-exchange-keycloak.md).

To do it by hand, build the image on the cluster from this directory (the
`Dockerfile` compiles from source; no local Rust toolchain is needed):

```shell
oc new-build --binary --strategy=docker --name=keycloak-registrar
oc start-build keycloak-registrar --from-dir=. --follow
```

Then deploy `deploy/interceptor.yaml` next to the gateway, add
`deploy/gateway-interceptor.toml` to the gateway's `gateway.toml`, and mount the
CA that signed the interceptor certificate at `tls_ca_cert_path`. The Helm chart
does not yet expose interceptor settings. Start the interceptor before
restarting the gateway.

The OpenShell crates are pinned by git revision in `Cargo.toml` to the commit
this repository validates (`8719fc9`). The builder stage uses the community
`docker.io/library/rust` image; replace it with a product-built image for
customer use.

## Test

```shell
cargo test
```

The tests cover token verification, the SPIFFE ID template, configuration, the
Keycloak admin calls against an in-memory mock, and the `Describe` and
`Evaluate` contracts.

## Limitations

- `post_commit` bindings must be `fail_open`. If the interceptor is unavailable
  when a sandbox is created, that sandbox is not registered and its credentialed
  requests fail closed until it is recreated. A reconcile loop over
  `ListSandboxes` would close this gap.
- The gateway calls `Describe` at startup and does not start while the
  interceptor is unreachable.
- Registration changes require a gateway restart.
