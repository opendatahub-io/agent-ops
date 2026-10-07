# Agent Ops

Setup guides and demos for running [OpenShell](https://docs.nvidia.com/openshell/latest/) with OpenShift AI.

> [!IMPORTANT]
> **OpenShell runs on Red Hat product-built images** (`quay.io/opendatahub/odh-openshell-*:v0.1.2-rhaiv.5`).
> Two images are not product builds yet: the registrar interceptor (built with a community
> Rust builder image) and the optional dashboard. See
> [Images used in this repository](#images-used-in-this-repository).

## Quick start

On an OpenShift cluster with the [Red Hat build of Agent Sandbox](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.12/html/deploying_red_hat_build_of_agent_sandbox/index) installed:

```shell
make preflight    # checks the cluster first; nothing is installed
make cli          # OpenShell CLI
make deploy       # gateway, Route, and CLI connection
openshell sandbox create -- echo "hello from a sandbox"
```

Then let agents call your APIs as the user, with no credentials in the sandbox
([guide](install/03-agent-identity.md)):

```shell
make token-exchange   # about 4 minutes; safe to re-run
make try-it           # a sandbox calls a protected API as user "alice"
```

Then turn on real logins for people, with access managed by Keycloak groups
([guide](install/02-user-auth.md)):

```shell
make user-auth                         # Keycloak over HTTPS, OIDC on the gateway, dashboard behind a login
make grant MEMBER=alice WS=team-a      # alice can sign in and use workspace team-a
make connect-info                      # what to send her: dashboard link and one CLI command
```

Run `make help` for all targets. Each target wraps a script in `scripts/`, so you can
also run the steps one at a time.

## Requirements

Everything the guides and scripts depend on, in one place. `./scripts/preflight.sh` checks the rows marked **yes**; add `--token-exchange` or `--user-auth` for those guides.

| Requirement | Needed for | Details | Preflight |
|---|---|---|---|
| OpenShift 4.19.35, 4.20.26, 4.21.21 or 4.22.2 or later in the same minor | Everything | Red Hat build of Agent Sandbox minimums. OpenShell also needs 4.19 or later: RHCOS kernels in 4.16 to 4.18 lack Landlock | yes |
| `cluster-admin` | Everything | Operators, cluster-scoped SPIFFE objects | yes |
| A default StorageClass with dynamic provisioning | Everything | Gateway database and sandbox workspaces | yes |
| Red Hat build of Agent Sandbox 0.9 (channel `preview-0.9`) | Everything | Software Catalog | yes |
| Worker kernel 5.19 or later, or an OpenShell build with [#4150](https://github.com/NVIDIA/OpenShell/pull/4150) | Python HTTPS and local servers in sandboxes | RHCOS 9 ships 5.14; see Known issues | warns |
| Zero Trust Workload Identity Manager 1.1.1 (channel `stable-v1`) with a trust domain | Token exchange | Software Catalog | yes |
| Pull access to `registry.redhat.io` | Token exchange | Red Hat build of Keycloak image; the default global pull secret is enough | no |
| Publicly trusted Route certificates, or `KEYCLOAK_CA_FILE` | User authentication | The gateway requires an HTTPS issuer it can verify | warns |
| Pods can reach `*.apps` Routes | User authentication | The gateway and oauth2-proxy call Keycloak through its Route | no |
| Local tools: `oc`, `helm` 3 or 4, `openssl`, `jq`, `curl`, `openshell` | Everything (`jq`, `curl`: token exchange and user authentication) | Validated with `oc` 4.20, Helm 4.2.0, CLI `0.1.3-dev` (8719fc9) | yes |
| Network egress, or mirrors of: `quay.io`, `ghcr.io`, `registry.redhat.io`, `registry.access.redhat.com`, `docker.io`, `github.com` | Installation | Product and dashboard images, oauth2-proxy (`quay.io`); Helm chart (`ghcr.io`); Keycloak; UBI images; the Rust builder for the interceptor (`docker.io`); the CLI installer (`github.com`) | no |
| A browser | User authentication | People sign in through Keycloak; the CLI can use a device code instead | no |

## What was validated, and its support status

This table is the single source of validated versions; the guides link here.

Everything in the setup guides was run end to end on
this stack. Status as of 2026-10-06.

| Component | Validated | Latest available | Support status |
|---|---|---|---|
| OpenShift | 4.20.27 (RHCOS 9.6, kernel 5.14) | | GA |
| OpenShell | Red Hat build `v0.1.2-rhaiv.5` (upstream `main` at `e7fdd6b`, chart `0.0.0-dev.e7fdd6beef98f7f92d86271a169fdd4d3be44cf3`); CLI built at `8719fc9` | `odh-stable` (upstream `12cec59`, includes the kernel 5.14 fix) | Developer Preview in Red Hat OpenShift AI 3.5; Technology Preview targeted for 3.6 |
| Red Hat build of Agent Sandbox | 0.9.0, channel `preview-0.9` | upstream v1.0.5 (serves `v1beta1`, compatible) | Technology Preview, shipped with OpenShift sandboxed containers 1.12 |
| Zero Trust Workload Identity Manager | 1.1.1, channel `stable-v1` | 1.1.1 | GA (since 1.0.0); see [known issue](reference/known-issues/ztwim-oidc-discovery-provider.md) |
| Red Hat build of Keycloak | 26.6.7 | 26.6.7 (upstream Keycloak 26.8.0) | GA; **SPIFFE client authentication is Technology Preview** (`--features=spiffe`). Federated client auth with Kubernetes service accounts or OIDC is supported in 26.6 |
| OpenShell gateway interceptors | in `e7fdd6b` | | Part of OpenShell; no Helm chart values yet |
| [OpenShell dashboard](install/01-install.md#optional-the-openshell-dashboard) | 1.2.0 (supports gateways 0.1.0 to 0.1.2) | | Standalone UI, not yet downstreamed; ODH image builds are in progress |
| [User login](install/02-user-auth.md) (OIDC, oauth2-proxy, group-based workspace access) | oauth2-proxy v7.15.5; 18 checks pass | | Gateway OIDC is part of OpenShell; group-to-workspace sync is this repository's script (no native support upstream) |
| [keycloak-registrar](interceptors/keycloak-registrar/) interceptor | this repository | | Prototype, not supported |
| Security context | default `restricted-v2` SCC, no added capabilities, no privileged grant | | |

## Known issues

| Issue | Workaround |
|---|---|
| RHCOS kernels before 5.19 (OpenShift 4.x, RHCOS 9): Python HTTPS fails, and servers that read the peer address on `accept()` fail ([OpenShell #4058](https://github.com/NVIDIA/OpenShell/issues/4058)). **Fixed upstream by [#4150](https://github.com/NVIDIA/OpenShell/pull/4150)** (merged 2026-10-06), verified on RHCOS 5.14 with `odh-stable`; not yet in a versioned Red Hat build | Python shim in the [install guide](install/01-install.md#known-limitations) until a build after `v0.1.2-rhaiv.5` ships the fix |
| [ZTWIM 1.1.1 OIDC discovery provider never becomes ready](reference/known-issues/ztwim-oidc-discovery-provider.md) when the operator is installed into an `openshift-*` namespace | Install ZTWIM into its suggested namespace, `zero-trust-workload-identity-manager`; otherwise `scripts/token-exchange/01-fix-ztwim-oidc.sh` |
| `helm upgrade` resets the gateway's interceptor registration | `make token-exchange` re-registers it; or re-run step 5 |
| Stored user tokens stop working when the user's Keycloak session expires | Step 4 sets a 10-hour session; refresh with `openshell provider update` |
| With OIDC on, the CLI's mTLS client bundle no longer signs anyone in (`missing authorization header`) | Use an OIDC CLI entry: `make connect-info` for people, the `<namespace>` entry for scripts |
| CLI service-account sessions expire after 5 minutes and the CLI tries to refresh instead of logging in again (CLI `0.1.3-dev`) | Run `openshell gateway login <name>` before commands; the scripts do this. Tracked upstream: [NVIDIA/OpenShell#4287](https://github.com/NVIDIA/OpenShell/issues/4287) |

Many of these, and several setup steps, are workarounds for missing product pieces. [Workarounds and what removes them](reference/workarounds.md) lists each one with the upstream issue or Jira that tracks the fix, or says that one is needed.

## Images used in this repository

| Image | Default in this repository | Replace with |
|---|---|---|
| OpenShell gateway, supervisor, sandbox runtime | `quay.io/opendatahub/odh-openshell-{gateway,supervisor,sandbox}:v0.1.2-rhaiv.5` (Red Hat build, Konflux) with chart `0.0.0-dev.e7fdd6b…` | Already product builds. To change version, set `ODH_IMAGE_TAG` and the matching `OPENSHELL_HELM_VERSION` (the upstream dev chart for the commit the build was synced from). `ODH_IMAGE_TAG=` uses upstream `ghcr.io/nvidia/openshell/*` images instead. For a custom build, also set `ODH_IMAGE_REGISTRY` and `ODH_IMAGE_REPO_PREFIX` (for example `quay.io/<you>` and `openshell-`). On a running install, `make switch-images` upgrades and re-registers the interceptor. `odh-stable` works but reports version `0.0.0`, so the dashboard cannot check compatibility. |
| keycloak-registrar interceptor | Built on your cluster from [`interceptors/keycloak-registrar`](interceptors/keycloak-registrar/) using `docker.io/library/rust` as the builder | A product-built interceptor image once one exists |
| OpenShell dashboard (optional) | `quay.io/gkrumbach07/openshell-dashboard:1.2.0` | The Red Hat build (`quay.io/opendatahub/odh-openshell-dashboard`) once it is released |
| Red Hat build of Keycloak | `registry.redhat.io/rhbk/keycloak-rhel9` 26.6.7 | Already a product image; use the RHBK operator in production |
| Agent and demo images (sandbox workloads, `whoami-api`) | `registry.access.redhat.com/ubi9/*` | Your agent's image: start from `ubi9/ubi-minimal` and follow [Bring your own agent image](install/04-agent-images.md). The OpenClaw reference image `quay.io/opendatahub/odh-openshell-sandbox-openclaw` is in progress (pull-request builds only so far) |

## Set up OpenShell with OpenShift AI

The platform track. Each step has a `make` target and needs step 1. Step 2 extends the Keycloak that step 3 deploys, so run `make token-exchange` before `make user-auth`. Steps 4 and 5 are independent.

![What agent-ops sets up for OpenShell with OpenShift AI: operators, the OpenShell gateway, Keycloak, the dashboard, sandboxes, and the keycloak-registrar interceptor. Circled numbers map to the steps below.](install/images/setup-architecture.png)

The circled numbers map to the steps below. Source: [`install/images/setup-architecture.drawio`](install/images/setup-architecture.drawio).

| Step | Guide | What you get | `make` |
|---|---|---|---|
| 1 | [Install OpenShell on OpenShift](install/01-install.md) | Gateway on the product images, Route, CLI connected, a first sandbox; optional dashboard | `deploy` |
| 2 | [User authentication](install/02-user-auth.md)<br>By hand: [with your own Keycloak](install/02-user-auth-keycloak.md), [workspaces and membership](install/02-user-auth-workspaces.md) | People log in with Keycloak; access managed by groups; dashboard behind a login | `user-auth`, `grant`, `revoke`, `connect-info` |
| 3 | [Agent identity: call your APIs as the user](install/03-agent-identity.md) | Each sandbox gets its own SPIFFE identity; the user's token is exchanged outside the sandbox (keycloak-registrar interceptor) | `token-exchange`, `try-it` |
| 4 | [Bring your own agent image](install/04-agent-images.md) | What an agent image needs and a `ubi9/ubi-minimal` example | `byo-agent` |
| 5 | [Kata runtime](install/05-kata-runtime.md) (optional) | Sandboxes in a Kata VM through a `RuntimeClass` (not re-validated with the current build) | |

## Demos

Each demo assumes step 1 and says what else it needs.

| Demo | Shows |
|---|---|
| [Claude Code in a sandbox](demos/claude-code-vertex/) | Inference through the gateway with Vertex AI credentials the agent never sees; an egress policy blocking and then allowing a request; live policy events in `openshell term` |
| [Call an API as the user](install/03-agent-identity.md#step-6-try-it) | `make try-it`: a sandbox calls a protected API and the API sees the user and the sandbox's identity |
| [Inference routing with RHOAI](demos/inference-routing-rhoai/) | Sandbox inference through a token-authenticated RHOAI-served model, credentials outside the sandbox |
| [MLflow OpenShell tracing](demos/mlflow-openshell-tracing/) | Traces from agents in sandboxes sent to the managed MLflow on RHOAI (`mlflow.openai.autolog()`, `inference.local`, sandbox network policy) |

## Reference

- [Workarounds and what removes them](reference/workarounds.md): each workaround in this repository, the fix that removes it, and its tracking issue
- [Known issue: ZTWIM 1.1.1 OIDC discovery provider](reference/known-issues/ztwim-oidc-discovery-provider.md)
- [OpenShell capability testing and security analysis](reference/scc-requirements.md): historical results for OpenShell v0.0.85; since 0.1.0 OpenShell runs under `restricted-v2` without added capabilities
