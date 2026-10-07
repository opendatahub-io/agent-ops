# Workarounds in this repository, and what removes them

The setup guides work today, but parts of them exist only because a product
piece is missing. This page lists each workaround, what would make it
unnecessary, and where that is tracked. Upstream OpenShell issues are on GitHub;
Red Hat product work is in Jira.

Status as of 2026-10-06.

## What any version of this setup needs

Some admin work is permanent, and no fix removes it:

- **Install OpenShell.** It needs cluster-admin. Today this is `make deploy`; later it is a chart or operator.
- **Connect the gateway to your identity provider, once.** The gateway must trust an issuer, and the provider must know the application (roles, dashboard redirect, CLI client). Each API that agents call must be registered once.
- **Decide who gets access.** In the target setup, this is group membership in your identity provider.

Everything below is extra work on top of those three.

## OpenShell upstream

| Workaround here | Why it exists | What removes it | Tracking |
|---|---|---|---|
| `make grant`, `make revoke`, `make sync-members` copy Keycloak groups into workspace membership | The gateway authorizes workspaces only from its own member list and cannot read groups from the token. Without the copy, users get "not a member of workspace" | Gateway maps a groups claim to workspace roles (for example `openshell-<ns>-ws-team-a-users` to `team-a` as `user`) | [#4286](https://github.com/NVIDIA/OpenShell/issues/4286) (filed 2026-10-07: workspace group bindings from a groups claim). Background: [RFC 0011](https://github.com/NVIDIA/OpenShell/tree/main/rfc/0011-multi-player-design) keeps workspace roles as records keyed by `sub`; related [#3542](https://github.com/NVIDIA/OpenShell/issues/3542), [#2613](https://github.com/NVIDIA/OpenShell/issues/2613) (closed as not planned) |
| keycloak-registrar interceptor creates one Keycloak client per sandbox | The token exchange needs each sandbox to be a known client | Built-in sandbox identity, or one client per OpenShell service account with gateway-issued tokens | [PR #2772](https://github.com/NVIDIA/OpenShell/pull/2772) (delegated identity, open). Service accounts: **RFC needed** |
| Users fetch a special token and paste it into a provider; calls return 502 when their session expires | The stored subject token must have the gateway's audience, and the CLI login token does not | Gateway exchanges from the user's own login session and refreshes it | `provider create --from-oidc-token` already copies the user's login token (but not its refresh token, and the Keycloak audience still has to include the gateway's exchange client). Refresh from the user's session: [PR #2772](https://github.com/NVIDIA/OpenShell/pull/2772) (open; our use case added 2026-10-07) |
| Token-grant failures show up in the sandbox as a bare 502 | The supervisor collapses grant errors | Typed, actionable token-grant errors | [#3319](https://github.com/NVIDIA/OpenShell/issues/3319) (open) |
| `03-gateway-oidc.sh` adds the OpenShift service CA to the OIDC CA bundle | `server.oidc.caConfigMapName` sets `SSL_CERT_FILE`, which replaces all of the gateway's trust roots | OIDC CA applies only to OIDC discovery and JWKS | [#3672](https://github.com/NVIDIA/OpenShell/issues/3672) (open) |
| People must use OIDC CLI entries; the mTLS client bundle stops working once OIDC is on | The gateway treats OIDC and client certificates as alternatives | Client certificates accepted alongside OIDC | [#3564](https://github.com/NVIDIA/OpenShell/issues/3564) (open) |
| Scripts run `openshell gateway login` before every command | CLI service-account sessions expire after 5 minutes and the CLI tries to refresh instead of re-running client credentials | CLI re-runs client credentials when the token expires | [#4287](https://github.com/NVIDIA/OpenShell/issues/4287) (filed 2026-10-07). The SDKs already renew this way (#2907) |
| `make token-exchange` and `make switch-images` re-register the interceptor | `helm upgrade` resets the gateway's interceptor registration; interceptors have no chart values | Interceptor configuration in the Helm chart | [#3060](https://github.com/NVIDIA/OpenShell/issues/3060) and [PR #3384](https://github.com/NVIDIA/OpenShell/pull/3384) (chart `gatewayConfig` rendered as TOML, open). Our first request was a duplicate ([#4068](https://github.com/NVIDIA/OpenShell/issues/4068), closed). The CA mount needs no patch: use `server.extraVolumes` and `server.extraVolumeMounts` |
| Python shim for HTTPS and local servers (install guide) | RHCOS 9 kernel 5.14 forced legacy read-only mode | Native `accept` and `getpeername` | [#4058](https://github.com/NVIDIA/OpenShell/issues/4058), fixed by [PR #4150](https://github.com/NVIDIA/OpenShell/pull/4150). Waiting for a Red Hat build after `v0.1.2-rhaiv.5` |
| Add `/dev/ptmx` and `/dev/pts` to `read_write` in the sandbox policy for agents that open a terminal (`tmux`, `openpty`) | The default filesystem policy allows writing only `/tmp` and `/dev/null`, so opening `/dev/ptmx` fails with `Permission denied`. Verified on RHCOS 9 (kernel 5.14) on 2026-10-07: with the two paths allowed, `openpty` works. It is not a kernel limitation | A default or documented policy preset for terminal-based agents | None filed; a docs or default-policy request at most |

## Red Hat products and builds

| Workaround here | Why it exists | What removes it | Tracking |
|---|---|---|---|
| `make token-exchange` deploys a dev-mode Keycloak (realm in memory; a restart wipes it) | We cannot assume an identity provider is set up | Customers bring their own Red Hat build of Keycloak, Keycloak or Entra ID, documented per provider | [RHOAIENG-98882](https://redhat.atlassian.net/browse/RHOAIENG-98882) (OIDC provider docs). Entra ID is not validated yet |
| `scripts/token-exchange/01-fix-ztwim-oidc.sh` | The ZTWIM 1.1.1 OIDC discovery provider never becomes ready when the operator is installed into an `openshift-*` namespace: the operator tells SPIRE to ignore `openshift-*` namespaces, including its own | Install ZTWIM into its suggested namespace (`zero-trust-workload-identity-manager`); a ZTWIM fix would cover the other case | [OCPBUGS-129473](https://redhat.atlassian.net/browse/OCPBUGS-129473) (filed 2026-10-07); details on the [known-issue page](known-issues/ztwim-oidc-discovery-provider.md) |
| `04-dashboard-login.sh` hand-wires the dashboard, oauth2-proxy, Route and NetworkPolicy | The dashboard has no Helm chart and is not downstreamed | A dashboard Helm chart, plus an install script that installs the gateway and dashboard charts together | [RHOAIENG-98850](https://redhat.atlassian.net/browse/RHOAIENG-98850) (dashboard Helm chart, in progress) under [RHOAIENG-98849](https://redhat.atlassian.net/browse/RHOAIENG-98849); installer script [RHOAIENG-97764](https://redhat.atlassian.net/browse/RHOAIENG-97764) under [RHOAIENG-97763](https://redhat.atlassian.net/browse/RHOAIENG-97763) |
| Dashboard image from a personal Quay repository | No product build yet | ODH dashboard image; repository moved to a neutral organization | Downstream image in [RHOAIENG-98849](https://redhat.atlassian.net/browse/RHOAIENG-98849); neutral-org move not started |
| keycloak-registrar built on the cluster with a community Rust builder image | No product image | A product-built interceptor image, or no registrar at all (see upstream table) | None |
| `odh-stable` reports version `0.0.0`, so the dashboard cannot check compatibility | The rolling tag has no version | Versioned `rhaiv` builds after each sync | AIPCC (`wg-aipcc-openshell`) |
