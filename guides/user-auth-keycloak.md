# User authentication with your own Keycloak: architecture and setup

> **Midstream Documentation**
>
> Run end to end as written on 2026-10-16: OpenShift 4.21.0, the installer at commit `635292b`
> (`opendatahub-io/openshell` `main`) with gateway chart 0.1.3, the Red Hat build `v0.1.3-rhaiv.3`,
> dashboard chart 0.1.1 with its default image (Step 9 now pins chart 0.1.2 and its ODH build instead) and oauth2-proxy v7.15.2, CLI 0.1.2, upstream Keycloak 26.0 in dev mode, two
> gateways in one realm. Three things were not done the way a person would do them: the realm was
> configured with `kcadm.sh`, following steps 1 to 6, instead of in the Keycloak console; the
> browser logins were driven with `curl`; and the CLI signed in as a service account instead of
> opening a browser. Only Keycloak has been tested; Microsoft Entra ID has not.

The chart and image versions in Step 9 were also tested on OpenShift 4.21.4:
CLI and dashboard login, workspace membership, and sandbox creation and deletion
worked.

This guide describes how to deploy two independent OpenShell gateways and dashboards in an OpenShift cluster while using one externally managed Keycloak realm for authentication. Namespace A accepts `administrators-a` and `users-a`. Namespace B accepts `administrators-b` and `users-b`. Mary and Cameron are standard users in namespace A, and Joe is a standard user in namespace B. End users do not need OpenShift accounts.

| Deployment | Standard users | Accepted API audience | Accepted roles |
|---|---|---|---|
| `namespace-a` | Mary and Cameron | `openshell-api-a` | `users-a`, `administrators-a` |
| `namespace-b` | Joe | `openshell-api-b` | `users-b`, `administrators-b` |

**Recommended design.** Use one Keycloak realm with separate resource, dashboard, and CLI clients for each namespace. Assign Keycloak groups to the two API client roles for that gateway. Keycloak's built-in `roles` client scope adds the API client to `aud` when either permitted role is present in the access token. This leaves three clients per namespace: the API resource client, the Dashboard client, and the CLI client. The separate audiences prevent a token issued for one namespace from being replayed against the other namespace.

**Intended readers.** OpenShift platform engineers, Keycloak administrators, and OpenShell operators responsible for installation, access control, and production readiness.

**One gateway only?** Follow the namespace A steps and skip every "repeat for B" item.

## Architecture and security model

Both OpenShell deployments trust the same Keycloak realm issuer, but each gateway validates a different access-token audience and API client-role path. Dashboard and CLI clients permit only the matching API client roles. Keycloak's built-in Audience Resolve mapper adds the matching API client to `aud` when one of those roles is present in the access token.

```
            Keycloak realm "openshell": one issuer, separate clients per namespace
            ┌────────────────────────────────────────────────────────────────────┐
            │ openshell-api-a        audience; client roles users-a and          │
            │                        administrators-a                            │
            │ openshell-dashboard-a  confidential client used by oauth2-proxy    │
            │ openshell-cli-a        public client, PKCE and device code         │
            └───────▲───────────────────────▲─────────────────────────▲──────────┘
              login │         discovery and │                   login │
                    │                  JWKS │                         │
 browser ─▶ Route ─▶ oauth2-proxy           │                    openshell CLI
                    │ access token          │                         │ access token
                    ▼                       │                         │
            dashboard BFF ─── token ──▶ Gateway A ◀───────────────────┘
            (loopback only)             (namespace-a)
                                        accepts aud openshell-api-a and a role in
                                        resource_access.openshell-api-a.roles
```

*Identity and token flow for one namespace. Namespace B is the same with the `-b` clients, groups and roles: Gateway B rejects a token issued for namespace A (`InvalidAudience`), and Gateway A rejects one issued for B.*

### Authentication boundaries

- Keycloak authenticates Mary, Cameron, Joe, administrators, and other OpenShell users.
- OpenShift authenticates only cluster operators, controllers, and workload service accounts.
- oauth2-proxy owns dashboard login, sessions, token refresh, logout, and CSRF protection.
- The dashboard BFF relays the access token and never validates, exchanges, or replaces it.
- Each gateway validates token signature, issuer, audience, expiry, and its `resource_access` role path.
- Sandbox supervisors use OpenShell workload authentication rather than a human user's Keycloak token.

### Why API client roles and separate audiences matter

Gateway A accepts only tokens containing `openshell-api-a` in `aud` and `users-a` or `administrators-a` below `resource_access.openshell-api-a.roles`. Gateway B applies the corresponding B checks. A user who discovers the other gateway hostname still cannot reuse a token from the wrong namespace.

OpenShell OIDC RBAC controls API methods. Provider and sandbox records remain gateway-wide resources rather than per-subject records, so separate gateways and namespaces are still required when A and B must be isolated.

## Identity and application inventory

| Keycloak object | Namespace A | Namespace B | Purpose |
|---|---|---|---|
| Groups | `users-a`, `administrators-a` | `users-b`, `administrators-b` | Human membership |
| API resource client | `openshell-api-a` | `openshell-api-b` | Audience and client-role namespace |
| API client roles | `users-a`, `administrators-a` | `users-b`, `administrators-b` | Gateway RBAC values |
| Built-in audience resolution | Audience Resolve | Audience Resolve | Adds the API client to `aud` when a permitted client role is present |
| Dashboard client | `openshell-dashboard-a` | `openshell-dashboard-b` | Confidential oauth2-proxy client |
| CLI client | `openshell-cli-a` | `openshell-cli-b` | Public native PKCE and device client |
| Gateway hostname | `gateway-a.example.com` | `gateway-b.example.com` | External HTTPS and gRPC endpoint |
| Dashboard hostname | `dashboard-a.example.com` | `dashboard-b.example.com` | Browser entry point through proxy |

### User assignment result

| User | Keycloak group | Dashboard A | Gateway A | Dashboard B | Gateway B |
|---|---|---|---|---|---|
| Mary | `users-a` | Allowed | Standard user | Denied | Denied |
| Cameron | `users-a` | Allowed | Standard user | Denied | Denied |
| Joe | `users-b` | Denied | Denied | Allowed | Standard user |

Mary, Cameron, and Joe do not need OpenShift `User` objects, OpenShift `Group` objects, `OAuthClient` resources, project membership, or Kubernetes RBAC bindings. Their identities, group membership, and application roles exist only in Keycloak. What they do need inside OpenShell is a workspace membership, which is [Step 12](#step-12-add-workspace-members).

## Values to collect before installation

| Value | Example placeholder | Owner |
|---|---|---|
| Keycloak base URL | `https://keycloak.example.com` | Identity administrator |
| Realm and issuer | `openshell` and `KEYCLOAK_ISSUER` | Identity administrator |
| API client IDs | `openshell-api-a` and `openshell-api-b` | Identity administrator |
| Dashboard client IDs | `openshell-dashboard-a` and `openshell-dashboard-b` | Identity administrator |
| Dashboard client secrets | A file next to the installer configuration, then a Kubernetes Secret | Identity administrator |
| CLI client IDs | `openshell-cli-a` and `openshell-cli-b` | Identity administrator |
| Keycloak CA | Public trust, or a PEM file for the installer (`oidc.ca_file`) | Identity and platform teams |
| Four public DNS names under the router's domain | `gateway-a`, `dashboard-a`, `gateway-b`, `dashboard-b` | Platform engineer |
| Approved image versions | Pinned gateway, supervisor, dashboard, proxy | Platform engineer |

The chart and image versions this guide was run with are in the installer configuration in Step 9.

## Step by step setup

Steps 1 to 7 are done in Keycloak, steps 8 to 10 with the installer on the cluster, and steps 11 to 13 with the `openshell` CLI and a browser.

### Step 1: Prepare the Keycloak realm

1. Create or select a realm named `openshell`. Do not configure application clients in the `master` realm.
2. Publish the realm through HTTPS at `https://keycloak.example.com/realms/openshell` and record that exact issuer URL. The gateway only accepts an HTTPS issuer.
3. Configure production session, access-token, refresh-token, password, brute-force, MFA, and event-retention policies before onboarding users.
4. If Keycloak uses a private CA, export the CA certificate that OpenShell gateways, dashboard proxies, and laptop clients must trust.
5. Confirm that the realm discovery document and JWKS endpoints are reachable from both OpenShift namespaces and from user laptops.

```shell
curl -fsS https://keycloak.example.com/realms/openshell/.well-known/openid-configuration

KEYCLOAK_ISSUER=https://keycloak.example.com/realms/openshell
```

### Step 2: Create groups and example users

1. Create top-level groups named `users-a`, `administrators-a`, `users-b`, and `administrators-b`.
2. Create or federate Mary and add her only to `users-a`.
3. Create or federate Cameron and add him only to `users-a`.
4. Create or federate Joe and add him only to `users-b`.
5. Add administrators only to the matching administrators group. Avoid direct role grants to individual users unless an exception is documented.
6. Require verified email and the organization's MFA or WebAuthn enrollment policy where appropriate.

### Step 3: Create API resource clients and roles

1. Create an OpenID Connect client with Client ID `openshell-api-a`.
2. Disable Standard Flow, Direct Access Grants, Implicit Flow, Device Authorization Grant, and service-account use. The client represents a resource server and is not an interactive login client.
3. Under the client's **Roles** tab, create client roles `users-a` and `administrators-a`.
4. Create `openshell-api-b` with the same flow restrictions and client roles `users-b` and `administrators-b`.

The gateway never uses a Keycloak client secret. Its API client defines the `resource_access` role namespace. Keycloak adds that client ID to `aud` automatically when a permitted role from the API client is present in the access token.

### Step 4: Assign API client roles to groups

1. Open group `users-a`, choose **Role mapping**, filter by client `openshell-api-a`, and assign role `users-a`.
2. Assign `openshell-api-a` role `administrators-a` to group `administrators-a`.
3. Assign `openshell-api-b` role `users-b` to group `users-b` and `administrators-b` to group `administrators-b`.
4. Do not grant A client roles to B groups or B client roles to A groups.

| Group | Client | Client role |
|---|---|---|
| `users-a` | `openshell-api-a` | `users-a` |
| `administrators-a` | `openshell-api-a` | `administrators-a` |
| `users-b` | `openshell-api-b` | `users-b` |
| `administrators-b` | `openshell-api-b` | `administrators-b` |

### Step 5: Create the dashboard clients

1. Create OpenID Connect client `openshell-dashboard-a` with **Client authentication** enabled and Standard Flow enabled.
2. Disable Direct Access Grants, Implicit Flow, Device Authorization Grant, and service accounts.
3. Set **Valid Redirect URIs** to `https://dashboard-a.example.com/oauth2/callback`, **Valid post logout redirect URIs** to `https://dashboard-a.example.com/*`, and **Web Origins** to `https://dashboard-a.example.com`.
4. Set the PKCE method to `S256`.
5. Open **Client scopes**, then the `openshell-dashboard-a-dedicated` scope, then its **Scope** tab. Turn **Full Scope Allowed** off and assign only the `openshell-api-a` roles `users-a` and `administrators-a`. Keep the built-in `roles` client scope linked: it holds the client-role and Audience Resolve mappers, so whenever one of those two roles is in the access token, `openshell-api-a` is in `aud`. No custom scope or audience mapper is needed.
6. Copy the dashboard client secret into an approved secret manager. Do not place it in source control.
7. Repeat for `openshell-dashboard-b`, `dashboard-b.example.com`, and the `openshell-api-b` roles.

| | Namespace A | Namespace B |
|---|---|---|
| API client | `openshell-api-a` | `openshell-api-b` |
| Permitted client roles | `users-a`, `administrators-a` | `users-b`, `administrators-b` |
| Clients that carry them | `openshell-dashboard-a`, `openshell-cli-a` | `openshell-dashboard-b`, `openshell-cli-b` |

### Step 6: Create the CLI clients

1. Create OpenID Connect client `openshell-cli-a` with **Client authentication** disabled and Standard Flow enabled.
2. Enable OAuth 2.0 Device Authorization Grant and require `S256` PKCE. Disable Direct Access Grants and Implicit Flow.
3. Set **Valid Redirect URIs** to `http://127.0.0.1/*`. The CLI listens on a random port and uses `http://127.0.0.1:<port>/callback`.
4. Scope the client exactly as in Step 5 item 5: Full Scope Allowed off, only the `openshell-api-a` roles, `roles` scope kept.
5. Do not create or distribute a secret for the public CLI client.
6. Repeat for `openshell-cli-b` with only the `openshell-api-b` roles.

### Step 7: Verify tokens before deploying OpenShell

1. Use each client's **Client scopes**, **Evaluate** view with Mary, Cameron, or Joe selected and look at the generated access token.
2. For Dashboard A and CLI A, verify `aud` is `openshell-api-a` and `resource_access.openshell-api-a.roles` contains `users-a`, and that no B client role appears.
3. Verify Joe receives `users-b` and `openshell-api-b` only through the B clients, and nothing usable through the A clients.
4. Verify administrator groups produce only their matching administrators role.

```json
{
  "iss": "https://keycloak.example.com/realms/openshell",
  "aud": "openshell-api-a",
  "azp": "openshell-dashboard-a",
  "sub": "e74ab271-66c6-4328-95cf-ea037d5a3d4c",
  "preferred_username": "cameron",
  "resource_access": {
    "openshell-api-a": { "roles": ["users-a"] }
  }
}
```

The `sub` value is the user's ID in the Keycloak console; Step 12 uses it.

### Step 8: Prepare the installer

The gateway and the dashboard are installed by the OpenShell installer for OpenShift, `deploy/rhoai/install.py` in [`opendatahub-io/openshell`](https://github.com/opendatahub-io/openshell). One run installs two Helm releases into one namespace: `openshell` (the gateway) and `openshell-dashboard` (the dashboard behind oauth2-proxy). It creates the namespace when it is missing, a passthrough `Route` for the gateway, an edge `Route` for the dashboard and the dashboard's `NetworkPolicy`. The [installer README](https://github.com/opendatahub-io/openshell/blob/main/deploy/rhoai/README.md) covers prerequisites, reruns, upgrades and failures. Use this installer with the pinned 0.1.x charts and images in Step 9; the separate Getting Started guide currently installs 0.0.116.

1. On the workstation you need `uv`, Helm, `kubectl`, `jq` and a kubeconfig context with the rights to install both charts. The cluster needs Agent Sandbox and a default StorageClass, as described in the installer README.
2. Reserve two hostnames per namespace under the router's domain: one for the gateway and one for the dashboard. Nothing needs issuing: the gateway serves a certificate its chart generates, with the gateway hostname in it, and the dashboard `Route` uses the router's default certificate.
3. The gateway pods and the dashboard proxy pods must reach the Keycloak issuer over HTTPS.

```shell
git clone https://github.com/opendatahub-io/openshell.git
cd openshell
```

### Step 9: Install Gateway A and Dashboard A

Keep the configuration outside the checkout. Save the dashboard client's secret from Step 5 next to it; the installer reads the file and puts the secret into a Kubernetes Secret, so it is never in the JSON, in Helm values or on a command line:

```shell
mkdir -p "$HOME/.config/openshell-rhoai/namespace-a"
chmod 700 "$HOME/.config/openshell-rhoai/namespace-a"
(
  umask 077
  printf 'Dashboard A client secret: '; read -rs secret; echo
  printf '%s' "${secret}" > "$HOME/.config/openshell-rhoai/namespace-a/dashboard-client-secret.txt"
)
```

Create `$HOME/.config/openshell-rhoai/namespace-a/config.json`. Replace the context (`kubectl config current-context`), the two hostnames and the issuer:

```json
{
  "namespace": "namespace-a",
  "context": "REPLACE_WITH_KUBECONFIG_CONTEXT",
  "gateway": {
    "chart": "oci://ghcr.io/nvidia/openshell/helm-chart",
    "version": "0.1.3",
    "host": "gateway-a.example.com",
    "images": {
      "gateway": {"registry": "quay.io", "repository": "opendatahub/odh-openshell-gateway", "digest": "sha256:e59fea2fc97144ed2f402af69be73c6dbbf9a76739f042e86c2237f65852284e"},
      "supervisor": {"registry": "quay.io", "repository": "opendatahub/odh-openshell-supervisor", "digest": "sha256:8564613ff3b753b9cdd03fc6b936646790e8d575b882f924d83640516db40d1a"},
      "sandboxRuntime": {"registry": "quay.io", "repository": "opendatahub/odh-openshell-sandbox", "digest": "sha256:7a38a0fba5d4cf196ba45879701e43ac61aa41de80ef5d486631d0fba7553d03"}
    }
  },
  "dashboard": {
    "chart": "oci://ghcr.io/gkrumbach07/openshell-dashboard/helm-chart/openshell-dashboard",
    "version": "0.1.2",
    "host": "dashboard-a.example.com",
    "images": {
      "dashboard": {"registry": "quay.io", "repository": "opendatahub/odh-openshell-dashboard", "digest": "sha256:cfb3b592c5909c7c1a5d5622f98a20bf8c503554d53b3441abb189140254450b"}
    }
  },
  "oidc": {
    "provider": "keycloak-oidc",
    "issuer": "https://keycloak.example.com/realms/openshell",
    "client_id": "openshell-dashboard-a",
    "client_secret_file": "dashboard-client-secret.txt",
    "audience": "openshell-api-a",
    "roles_claim": "resource_access.openshell-api-a.roles",
    "admin_role": "administrators-a",
    "user_role": "users-a",
    "allowed_roles": ["openshell-api-a:users-a", "openshell-api-a:administrators-a"]
  }
}
```

The `oidc` section is where the Keycloak objects from steps 3 to 5 go:

| Installer setting | Value | What it does |
|---|---|---|
| `provider` | `keycloak-oidc` | Lets the proxy read Keycloak client roles, which `allowed_roles` needs |
| `issuer` | The realm issuer from Step 1 | Both the gateway and the proxy validate tokens against it |
| `client_id`, `client_secret_file` | The dashboard client from Step 5 | The proxy signs users in with it |
| `audience` | The API client from Step 3 | The gateway accepts only tokens with this `aud` |
| `roles_claim` | `resource_access.<API client>.roles` | Where the gateway reads the roles from |
| `admin_role`, `user_role` | The two client roles from Step 3 | Gateway administrator and standard user |
| `allowed_roles` | `<API client>:<role>`, one entry per role | The proxy refuses anyone without one of them, before the dashboard is reached |

Without `allowed_roles` every user of the realm reaches the dashboard and only the gateway refuses them. The two `images` blocks select the Red Hat builds, pinned by digest; without them the charts' own upstream images are used. The gateway's three are the `v0.1.3-rhaiv.3` build, which matches gateway chart 0.1.3. The dashboard chart is published only upstream, so `chart` stays there while `images` swaps in the ODH build of the same release, pinned by digest (ODH fork commit `ae76c31`, which is upstream dashboard 0.1.2). A newer ODH dashboard build needs the dashboard chart release it was built with. If Keycloak uses a private CA, add `"ca_file": "keycloak-ca.crt"` to `oidc` and put the PEM file next to the JSON: the installer gives it to the gateway and to the proxy.

Check the configuration, check the cluster, then install. `--check` renders both charts without contacting the cluster, and `--preflight` checks the cluster without changing it:

```shell
uv run --script --python 3.12 deploy/rhoai/install.py \
  --config "$HOME/.config/openshell-rhoai/namespace-a/config.json" --check

uv run --script --python 3.12 deploy/rhoai/install.py \
  --config "$HOME/.config/openshell-rhoai/namespace-a/config.json" --preflight

uv run --script --python 3.12 deploy/rhoai/install.py \
  --config "$HOME/.config/openshell-rhoai/namespace-a/config.json"
```

The three commands take about a minute and end with the two URLs:

```text
Dashboard: https://dashboard-a.example.com
Gateway: https://gateway-a.example.com
Register the gateway with the CLI using its CA, then log in through customer OIDC.
An administrator must add users to workspaces. Verify login, denied access, sandbox creation and terminals.
```

What the installed pair enforces, each checked on the running deployment:

- The proxy forwards the **access** token to the dashboard, and refreshes it every 2 minutes, before Keycloak's 5-minute default lifetime ends. A session was still valid 7 minutes after sign-in.
- An expired session answers the dashboard's API calls with 401 instead of a redirect to the login page.
- The proxy and the `Route` wait 120 and 300 seconds for the dashboard. Deleting a sandbox takes about 30 seconds and returns 200.
- Only the proxy port 4180 is exposed, and the `NetworkPolicy` admits only the OpenShift router to it. The dashboard itself listens on the pod's loopback address, so no client can hand it forged authentication headers.
- The dashboard pod holds the gateway's CA certificate and nothing else from the gateway's TLS secret. It can act only with the signed-in user's token.
- The gateway refuses unauthenticated callers. TLS client certificates are not used to sign anyone in.
- Signing out ends the user's Keycloak session as well as the dashboard's: the proxy calls Keycloak's logout endpoint with the session's ID token, and the next visit asks for a password. Dashboard charts before 0.1.1 end only the dashboard session.

### Step 10: Install Gateway B and Dashboard B

Repeat Step 9 with a second directory, `$HOME/.config/openshell-rhoai/namespace-b`, and these replacements in the JSON. The charts, versions, images and issuer stay the same.

| Namespace A | Namespace B |
|---|---|
| `namespace-a` | `namespace-b` |
| `gateway-a.example.com` | `gateway-b.example.com` |
| `dashboard-a.example.com` | `dashboard-b.example.com` |
| `openshell-dashboard-a` and its secret | `openshell-dashboard-b` and its secret |
| `openshell-api-a` (in `audience`, `roles_claim` and `allowed_roles`) | `openshell-api-b` |
| `administrators-a`, `users-a` | `administrators-b`, `users-b` |

### Step 11: Configure the laptop CLI

The CLI must be a 0.1.x release; a 0.0.x CLI connects but fails on most commands (see Troubleshooting). Because each gateway serves the certificate its chart generates (Step 8), the CLI needs that gateway's CA before it can connect. An administrator extracts each one once and sends the files to users; they are public certificates, not secrets:

```shell
kubectl -n namespace-a get secret openshell-client-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > gateway-a-ca.crt
kubectl -n namespace-b get secret openshell-client-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > gateway-b-ca.crt

# on the user's machine, one directory per gateway name
mkdir -p ~/.config/openshell/gateways/namespace-a/mtls ~/.config/openshell/gateways/namespace-b/mtls
cp gateway-a-ca.crt ~/.config/openshell/gateways/namespace-a/mtls/ca.crt
cp gateway-b-ca.crt ~/.config/openshell/gateways/namespace-b/mtls/ca.crt
```

Register both gateways. The CLI automatically adds `openid`; request `profile` and `email`. Keycloak derives the API audience from the permitted API client roles:

```shell
openshell gateway add https://gateway-a.example.com \
  --name namespace-a \
  --oidc-issuer https://keycloak.example.com/realms/openshell \
  --oidc-client-id openshell-cli-a \
  --oidc-audience openshell-api-a \
  --oidc-scopes "profile email"

openshell gateway add https://gateway-b.example.com \
  --name namespace-b \
  --oidc-issuer https://keycloak.example.com/realms/openshell \
  --oidc-client-id openshell-cli-b \
  --oidc-audience openshell-api-b \
  --oidc-scopes "profile email"
```

The browser login uses Authorization Code with `S256` PKCE and a random `127.0.0.1` callback. To use Device Authorization Grant on a machine without a browser, set `OPENSHELL_NO_BROWSER=1` before `gateway add` or `gateway login`.

```shell
OPENSHELL_NO_BROWSER=1 openshell gateway login namespace-a
```

Keycloak must advertise `device_authorization_endpoint` and the selected CLI client must have Device Authorization Grant enabled. A platform administrator sees `Authenticated (OIDC)` in `openshell status`. A standard user may see `Authenticated (OIDC; authorization denied)` because the status probe calls the administrator-only `GetGatewayInfo` method. The user is still authenticated; run `openshell --gateway namespace-a whoami --output json` to verify the identity before workspace membership exists.

### Step 12: Add workspace members

Being in a Keycloak group is not enough. The gateway role in the token lets a user sign in, but OpenShell applies a second check: a standard user must have a membership record in each workspace they use. Before membership, `openshell whoami` works and `workspace list` may return no rows. Workspace-scoped calls against an unjoined workspace fail with `not a member of workspace 'default'; ask a platform admin to run: openshell workspace member add ...`. Keycloak groups do not become workspace memberships on their own ([NVIDIA/OpenShell#4286](https://github.com/NVIDIA/OpenShell/issues/4286)).

A member of `administrators-a` adds each standard user, identified by the `sub` claim of their token. With Keycloak's default subject mapping that is the user's ID in the Keycloak console (**Users**, the user, **ID**); `openshell whoami --output json`, run by the user, prints the same value.

```shell
openshell --gateway namespace-a workspace member add \
  --workspace default \
  --subject '<mary-subject>' \
  --role user

openshell --gateway namespace-a workspace member add \
  --workspace default \
  --subject '<cameron-subject>' \
  --role user

openshell --gateway namespace-a workspace member list --workspace default
```

The dashboard's workspace **Members** page does the same with the subject and the role. Do the same for Joe on `namespace-b` as a member of `administrators-b`. Members of the administrators groups need no membership: the gateway admin role bypasses the check.

[Workspaces and membership](user-auth-workspaces.md) covers named workspaces, delegated workspace administrators, looking subjects up through the Keycloak Admin REST API, offboarding, and automation.

### Step 13: Validate expected and denied access

1. Sign in to Dashboard A as Mary. Confirm that the UI loads and reports a standard OpenShell user.
2. Repeat Dashboard A and Gateway A tests as Cameron. Confirm that Cameron has a distinct subject but the same `users-a` authorization as Mary.
3. Run `openshell whoami --output json` against `namespace-a` and verify the Keycloak issuer, `openshell-api-a` audience, and `users-a` role.
4. As Mary, create and delete a sandbox in her workspace.
5. Attempt Dashboard B and Gateway B as Mary and Cameron. Confirm denial at the proxy role check or the gateway B audience and role checks.
6. Repeat the positive tests for Joe against `namespace-b` and negative tests against `namespace-a`.
7. Test one `administrators-a` user and one `administrators-b` user. Verify administrative methods succeed only in the matching namespace, and that the dashboard shows the administrator role.
8. Verify that direct network access to the BFF port is impossible outside its pod and that clients can reach only oauth2-proxy.
9. Verify token expiry and refresh, dashboard logout, CLI logout, browser identity switching, and device-code login. After signing out of the dashboard, the next visit asks for a password again.

#### Expected token claims

| Test | Expected `aud` | Expected client role | Gateway result |
|---|---|---|---|
| Mary to Gateway A | `openshell-api-a` | `users-a` | Allowed as user |
| Cameron to Gateway A | `openshell-api-a` | `users-a` | Allowed as user |
| Mary or Cameron to B | A audience or no B role | no `users-b` | Denied |
| Joe to Gateway B | `openshell-api-b` | `users-b` | Allowed as user |
| Joe to Gateway A | B audience or no A role | no `users-a` | Denied |

Inspect tokens only with approved local tooling or Keycloak's Evaluate view. Do not paste production access tokens into public token-inspection websites.

## Operational and security controls

- Run Keycloak with supported replicas, a production database, tested backups, monitored health, and a documented recovery procedure.
- Use one realm issuer URL consistently. Do not expose the `master` realm to application users.
- Keep A and B clients, role scope mappings, and group role mappings separate. Retain the built-in `roles` client scope so Audience Resolve can derive `aud` from the permitted API client roles.
- Keep Full Scope Allowed off on dashboard and CLI clients. Disable unused implicit and direct-access flows.
- Use exact dashboard redirect URIs and the narrowest native loopback redirect pattern supported by the deployed Keycloak version.
- Rotate dashboard client secrets and cookie secrets on a documented schedule and store them only in Kubernetes Secrets or an approved external secret manager.
- Plan realm signing-key rotation so gateways can refresh JWKS before old verification keys are retired.
- Apply MFA, brute-force protection, session limits, audit events, and administrator separation appropriate for the organization.
- Pin OpenShell, supervisor, dashboard, oauth2-proxy, and Helm chart versions and test their compatibility together.
- Retain HTTPS between ingress and gateway. Disabling user mTLS must not disable server TLS.
- Treat provider credentials and sandbox records as gateway-wide resources. OIDC subject identity does not create per-user record ownership.
- Remove a user's workspace memberships when you remove their Keycloak group. The membership is checked on every call; the role in a token that was already issued lasts until that token expires, at most 5 minutes plus the gateway's 60-second leeway with Keycloak's defaults.

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Dashboard login redirects repeatedly | Redirect URI, proxy cookie, issuer, or forwarded-host mismatch | Match the Keycloak redirect exactly and verify reverse-proxy headers |
| Dashboard login succeeds but API returns 401 | Proxy passed an ID token or the expected API client roles are absent from the access token | Use `--pass-access-token` and verify the matching `resource_access` roles and API audience |
| Dashboard works, then after a few minutes shows the user signed in with no role and every page fails with `ExpiredSignature` | The proxy is not refreshing the access token | The installer sets `--cookie-refresh=2m`. If the realm's access tokens live less than that, set the chart's `authProxy.cookie.refresh` below their lifetime |
| A workspace-scoped call says `not a member of workspace` | The user has the gateway role but no membership in that workspace | Add the membership ([Step 12](#step-12-add-workspace-members)) |
| Standard-user `openshell status` says `Authenticated (OIDC; authorization denied)` | The status probe calls administrator-only `GetGatewayInfo` | Use `openshell whoami --output json` to verify the user's identity |
| Deleting a sandbox from the dashboard fails with `504 Gateway Time-out` after 30 seconds, yet the sandbox is gone | The delete takes about 30 seconds and the proxy or the Route timed out | The installer sets 120 s on the proxy and 300 s on the Route. Check `--upstream-timeout` and the Route's `haproxy.router.openshift.io/timeout` if they were changed |
| Signing out of the dashboard signs the same user straight back in | The dashboard chart is older than 0.1.1, which ends only the proxy session | Set `dashboard.version` in the installer configuration to `0.1.1` or later and run the installer again |
| Dashboard Route answers 503 right after the rollout | The router has not picked up the new endpoint yet | Wait a few seconds; check `kubectl -n <namespace> get endpoints openshell-dashboard` |
| Gateway reports invalid audience | Audience Resolve did not see a permitted API client role | Verify the `roles` client scope, role scope mappings, `resource_access` entry, and `aud` claim |
| Authenticated user has no OpenShell role | Group lacks API client-role mapping or role scope mapping filters it | Check group Role mapping, Full Scope Allowed, and `resource_access` claim |
| Mary or Cameron can open Dashboard B | B proxy lacks allowed-role restriction or B role was granted accidentally | Check proxy flags and remove A groups from B client roles |
| CLI reports redirect URI mismatch | The public client does not accept the random `127.0.0.1` callback | Register the native loopback pattern and test the exact released CLI |
| CLI device login is unavailable | Device Authorization Grant or discovery endpoint is disabled | Enable the grant on the CLI client and inspect discovery metadata |
| CLI: `invalid peer certificate: UnknownIssuer` | The gateway serves the chart-generated certificate and the CLI does not have its CA | Install the gateway CA for that gateway name (Step 11) |
| CLI: `failed to decode Protobuf message: ...workspace_scope: unexpected end group tag` | The CLI is older than 0.1.0 (`openshell --version`); `whoami` and `status` still work, everything with a workspace fails | Install a 0.1.x CLI |
| CLI reports `missing authorization header` | The CLI entry uses the mTLS client bundle, which no longer signs anyone in once OIDC is on | Register an OIDC entry with `gateway add --oidc-issuer ...` (Step 11) |
| Gateway cannot initialize OIDC | Issuer, CA trust, cluster egress, or discovery failure | Check the exact realm issuer, the `oidc.ca_file` setting, NetworkPolicy, and gateway logs |
| Browser shows a client certificate error | The gateway requires a client certificate | The installer turns `server.tls.enableMtls` off and keeps HTTPS on. Check the gateway release's values |
| Dashboard can be bypassed with forged headers | The dashboard is reachable without oauth2-proxy | The installer binds it to the pod's loopback address and exposes only port 4180. Check for an extra Service, Route or changed chart values |

## Production readiness checklist

- [ ] Keycloak production availability, database backups, monitoring, and recovery have named owners.
- [ ] The `openshell` realm issuer is HTTPS and reachable from gateways, proxies, and laptops.
- [ ] API A and API B use different client IDs and access-token audiences.
- [ ] A groups have only A client roles; B groups have only B client roles.
- [ ] Dashboard and CLI clients have Full Scope Allowed off, permit only their matching API client roles, and retain the built-in `roles` client scope.
- [ ] Both gateways reject unauthenticated calls and validate `resource_access` client roles.
- [ ] Gateway TLS remains enabled and OpenShift ingress supports HTTP/2 and gRPC.
- [ ] Each dashboard Route exposes only oauth2-proxy and the BFF binds to loopback.
- [ ] Every standard user has a workspace membership for their own subject, and offboarding removes it.
- [ ] Browser PKCE, CLI PKCE, device login, refresh, logout, and signing-key rollover have been tested.
- [ ] Positive and negative tests pass for Mary, Cameron, Joe, and both administrator roles.
- [ ] Secrets, certificates, image pins, token lifetimes, audit retention, and backup schedules are documented.

## References

- [OpenShell access control](https://docs.nvidia.com/openshell/latest/kubernetes/access-control)
- [OpenShell OpenShift deployment](https://docs.nvidia.com/openshell/latest/kubernetes/openshift)
- [OpenShell gateway authentication](https://docs.nvidia.com/openshell/latest/how-it-works/gateways/authentication)
- [OpenShell workspaces](https://docs.nvidia.com/openshell/latest/how-it-works/workspaces)
- [OpenShell Dashboard authentication guide](https://github.com/Gkrumbach07/openshell-dashboard/blob/main/docs/authentication.md)
- [OpenShell Dashboard authentication decision](https://github.com/Gkrumbach07/openshell-dashboard/blob/main/docs/adrs/0002-auth-relay-only-bff.md)
- [Keycloak Server Administration Guide](https://www.keycloak.org/docs/latest/server_admin/)
- [Keycloak audience support: Audience Resolve](https://www.keycloak.org/docs/latest/server_admin/#_audience_resolve)
- [Keycloak securing applications guides](https://www.keycloak.org/guides#securing-apps)
- [oauth2-proxy Keycloak OIDC provider](https://oauth2-proxy.github.io/oauth2-proxy/configuration/providers/keycloak_oidc)
- [oauth2-proxy configuration overview](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview)
