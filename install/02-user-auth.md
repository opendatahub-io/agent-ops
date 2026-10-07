# User authentication: log in to OpenShell and its dashboard

> **Midstream Documentation**
>
> Validated end to end on 2026-10-06, with publicly trusted and with private-CA Route certificates.
> Versions: [What was validated](../README.md#what-was-validated-and-its-support-status). Keycloak runs in
> dev mode here. Do not use in production.

The install guide runs the gateway with `allowUnauthenticatedUsers=true`: every caller is a trusted local developer, and the dashboard treats every visitor as a platform admin. This guide turns on real logins and makes **Keycloak groups the only thing an administrator manages**: put a person in a group and they can sign in and use that workspace; take them out and they lose it.

It covers people. Sandbox identity (an agent calling an API as the user) is the [token-exchange guide](03-agent-identity.md); both use the same Keycloak realm and issuer.

**Already run Keycloak?** `make user-auth` deploys its own Keycloak in dev mode. To get the same result on a Keycloak you operate, configure it by hand with [User authentication with your own Keycloak](02-user-auth-keycloak.md). [Workspaces and membership](02-user-auth-workspaces.md) goes deeper on workspace layout, finding a user's subject (Keycloak and Entra ID), delegation, and offboarding.

## How access works

Two checks decide every request, and one group membership satisfies both:

| Check | Where it lives | What satisfies it |
|---|---|---|
| 1. Does the person have this gateway's `user` or `admin` role? | Keycloak, in the token (`resource_access.openshell-api-<namespace>.roles`) | Any of this gateway's groups grants the role |
| 2. Is the person a member of the workspace? | OpenShell, a membership record per user | `make sync-members` creates and removes records to match the groups |

```
openshell-<ns>-admins               -> gateway admin (all workspaces, no membership needed)
openshell-<ns>-ws-<workspace>-users -> gateway user + member of <workspace>
openshell-<ns>-ws-<workspace>-admins-> gateway user + admin of <workspace>
```

```
 browser ─▶ dashboard Route ─▶ oauth2-proxy ──login──▶ Keycloak
                                  │  refuses users with no role for this gateway
                                  ▼
                          dashboard (loopback only) ──user's token──▶ gateway
 laptop  ─▶ openshell CLI ──browser or device-code login──────────▶ gateway
                                                    gateway checks: issuer, audience, role, membership
```

## Before you start

- `make token-exchange` has run: it deploys Keycloak and the realm that this guide extends. See [Let agents call your APIs as the user](03-agent-identity.md).
- `jq` and `curl` are installed locally. The other requirements are in the [README](../README.md#requirements).
- Run `./scripts/preflight.sh --user-auth`. It checks the above and whether the cluster's Route certificates are publicly trusted.
- **Private CA.** If preflight warns that Route certificates are not publicly trusted, save the CA that signs them as a PEM file and export `KEYCLOAK_CA_FILE` before every user-auth command. The gateway and oauth2-proxy then trust it through ConfigMap `keycloak-ca`, and the local scripts and CLI trust it too:

  ```shell
  export KEYCLOAK_CA_FILE=$PWD/ingress-ca.pem
  ```

- Pods must be able to reach the cluster's `*.apps` Routes: the gateway and oauth2-proxy call Keycloak through its Route. Sandboxes and the token-exchange demo use Keycloak's Service directly. With a private CA, both user login and [agent identity](03-agent-identity.md) are validated (2026-10-07).

## Set it up

```shell
make user-auth          # Keycloak over HTTPS, realm, gateway OIDC, dashboard login, sync, then the checks
```

| Step | Script | What it does |
|---|---|---|
| 1 | `01-keycloak-route.sh` | Route for Keycloak; Keycloak uses that URL as its issuer (the gateway requires HTTPS and one URL for everyone) |
| 2 | `02-configure-realm.sh` | Per gateway: API client with roles `admin` and `user`, groups, CLI and dashboard login clients, an automation account, a read-only sync account, user `platform-admin` |
| 3 | `03-gateway-oidc.sh` | Gateway: OIDC on, anonymous access off; CLI entry `<namespace>` for admin scripts |
| 4 | `04-dashboard-login.sh` | Dashboard behind oauth2-proxy, Route, NetworkPolicy |
| 5 | `05-sync-members.sh` | Workspace membership follows the groups |

Several gateways can share one realm: each gets its own audience, roles and groups, so a token or a group for one is useless on another.

## Day to day

```shell
make grant MEMBER=alice WS=team-a            # adds alice to openshell-<ns>-ws-team-a-users, creates team-a if needed
make grant MEMBER=bob WS=team-a ROLE=admin   # workspace admin
make revoke MEMBER=alice WS=team-a           # removes access to team-a at once
make revoke MEMBER=alice                     # removes every workspace on this gateway
make connect-info                            # the dashboard link and the one CLI command to send a new user
make sync-members DRY_RUN=true               # preview what the sync would change
```

Administrators who prefer the Keycloak console can add or remove group members there and run `make sync-members`. The sync only manages workspaces that have a group; it leaves others alone.

The user's experience: open the dashboard link and sign in with their account; for the CLI, run the command from `make connect-info` once, then `openshell gateway login <namespace>` when the session expires.

## What the checks prove (`make verify-user-auth`, 18 checks)

| Area | Check |
|---|---|
| Gateway | No token refused. A user in no group refused. `platform-admin` is admin |
| Grant | After `grant`: role `user`, can list, create and delete sandboxes in `team-a`, denied in `default` |
| Revoke | After `revoke`: denied in `team-a`, **including with a token issued before the revoke**, and refused with a new token |
| Dashboard | No session refused. Alice signs in and the dashboard acts as her. A user without a role is stopped at the proxy |
| Hardening | Only the router can reach the dashboard pod. The CLI client refuses password login. The test's temporary client is deleted afterwards |

## Security properties

- **Least privilege per client.** Every login client has Full Scope Allowed off and carries only this gateway's two roles. The sync reads Keycloak with an account limited to viewing users and groups; only the automation account can change the gateway.
- **No password login** on the CLI and dashboard clients. Checks use a temporary client that is deleted on exit.
- **Two gates in front of the dashboard.** The proxy refuses users without a role for this gateway; the gateway enforces role and workspace membership on every call.
- **The dashboard cannot be bypassed.** It listens on loopback, the NetworkPolicy admits only the OpenShift router, and it holds only the gateway CA, never a client key: it can act only with the signed-in user's token.
- **Revocation is immediate inside OpenShell.** Removing the membership denies the workspace at once; the role in an already-issued token lasts until it expires (5 minutes by default).
- **The mTLS client bundle no longer signs anyone in** once OIDC is on (`missing authorization header`); it only protects the connection.
- Secrets live only in Kubernetes Secrets and are never printed by the scripts.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Gateway pod crash-loops with `OIDC initialization failed: OIDC discovery request failed` | The gateway cannot verify Keycloak's Route certificate (private CA) or cannot reach the Route | Set `KEYCLOAK_CA_FILE` and re-run `make user-auth`; check that pods can reach `*.apps` Routes |
| A fix to the gateway configuration is not picked up | Kubernetes does not replace a StatefulSet pod that is not Ready | `deploy-openshell.sh` deletes the stuck pod for you; manually: `oc -n <ns> delete pod openshell-0` |
| Signed in, but every call says `not a member of workspace` | The user has the gateway role but no workspace group | `make grant MEMBER=<user> WS=<workspace>` |
| Signed in to the dashboard, then `403 Forbidden` from the proxy | The user is in none of this gateway's groups | `make grant ...`; the proxy only admits this gateway's `admin` and `user` roles |
| CLI: `missing authorization header` | The CLI entry uses the mTLS bundle, which no longer signs anyone in once OIDC is on | Register an OIDC entry: `make connect-info` |
| CLI: `OIDC token refresh failed: no refresh token available` | Service-account sessions expire after 5 minutes and the CLI does not log in again on its own | `openshell gateway login <name>` before commands ([NVIDIA/OpenShell#4287](https://github.com/NVIDIA/OpenShell/issues/4287)) |
| `make try-it` fails after `make user-auth` | The gateway refuses anonymous callers | Run it with `OPENSHELL_GATEWAY` and `OPENSHELL_OIDC_CLIENT_SECRET` set; see the [token-exchange guide](03-agent-identity.md) |
| Gateway logs `invalid audience` or users have no role | The login client lacks this gateway's role scope, or the user has no group | Re-run `scripts/user-auth/02-configure-realm.sh`; check the user's groups |

## Known gaps

| Gap | Status |
|---|---|
| Workspace membership has no native group mapping; the sync bridges it | Filed upstream as [NVIDIA/OpenShell#4286](https://github.com/NVIDIA/OpenShell/issues/4286) (workspace group bindings). Until then run the sync after group changes (or on a schedule) |
| Keycloak runs in dev mode; realm and users are lost on restart | Use the RHBK operator with a database for anything shared |
| oauth2-proxy and the dashboard are not product images yet | `quay.io/oauth2-proxy/oauth2-proxy`, `quay.io/gkrumbach07/openshell-dashboard:1.2.0`; ODH dashboard build in progress |
| Only Keycloak tested | Entra ID needs the same per-gateway app roles; its token `sub` is per application, so membership must use the token's `sub`, never the directory object ID |
| CLI service-account sessions expire after 5 minutes and the CLI tries to refresh instead of logging in again | Scripts run `openshell gateway login` first; seen with CLI `0.1.3-dev` (8719fc9). Filed upstream as [NVIDIA/OpenShell#4287](https://github.com/NVIDIA/OpenShell/issues/4287) |
| `make grant` and `make revoke` edit groups through the Keycloak bootstrap admin | Evaluation shortcut; in production manage groups in Keycloak or your directory and let the sync follow |
