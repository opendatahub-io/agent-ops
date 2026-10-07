# Workspaces and membership after user authentication is on

> **Midstream Documentation**
>
> The Keycloak commands were run on 2026-10-07 against the Red Hat build `v0.1.2-rhaiv.5` with CLI
> 0.1.2, after the [Keycloak guide](02-user-auth-keycloak.md): named workspace, membership add, list
> and remove, delegation, and the denials in the negative tests. The Microsoft Entra ID path has not
> been tested with OpenShell yet; it is included because Entra subjects behave differently and are
> easy to get wrong. Confirm commands against the OpenShell release you run.

Use this guide after the OpenShell gateways and dashboards are deployed with OIDC, either by hand ([User authentication with your own Keycloak](02-user-auth-keycloak.md)) or with the scripts ([User authentication](02-user-auth.md)). It creates logical workspaces, resolves each user's validated OpenShell subject through directory lookup, controlled enrollment, or a verification fallback, and verifies that gateway roles and workspace membership enforce the intended access.

| | |
|---|---|
| Audience | OpenShell platform administrators and delegated workspace administrators |
| Deployment | Multiple users per gateway with separate administrator and user roles |
| Examples | Mary and Cameron on Gateway A and Joe on Gateway B |
| Identity providers | Keycloak; Microsoft Entra ID (untested) |

The commands use the CLI gateway names registered in the Keycloak guide, `namespace-a` and `namespace-b`. If you used `make user-auth`, the CLI entry is named after the namespace, and `make grant`, `make revoke` and `make sync-members` do the work of this guide from Keycloak groups.

## Purpose and expected result

Platform Admins can complete workspace setup without asking every user to run a CLI command, provided the provisioning process obtains the exact OIDC subject accepted by the target gateway.

The identity provider decides whether a token contains the configured gateway administrator or user role. OpenShell then applies a second authorization layer: each ordinary user or delegated Workspace Admin must have a membership record in every logical workspace they may use. Identity provider groups do not automatically become OpenShell workspace memberships ([NVIDIA/OpenShell#4286](https://github.com/NVIDIA/OpenShell/issues/4286)). A user who has the role but no membership can sign in, and then every call fails with `not a member of workspace`.

This guide assumes Gateway A is deployed in `namespace-a` and accepts `administrators-a` and `users-a`. Gateway B is deployed in `namespace-b` and accepts `administrators-b` and `users-b`. Mary and Cameron belong to `users-a`. Joe belongs to `users-b`. These users do not need OpenShift login accounts.

Keycloak normally supports complete pre-provisioning from its immutable user ID. Microsoft Entra ID requires either an existing application-specific subject mapping or a controlled first-login enrollment because the directory object ID is not interchangeable with the token subject used by OpenShell.

```
 Keycloak: user ID from the console or Admin REST API ──┐
 Entra ID: sub captured at a controlled first login ────┼─▶ exact OIDC sub ─▶ workspace member add
 Any provider: the user runs `openshell whoami` ────────┘                     --subject <sub>

 Every request then passes two checks:
   1. the token carries this gateway's admin or user role    (identity provider)
   2. a membership record exists for the token's sub         (OpenShell, per workspace)
```

*Subject resolution and workspace membership.*

## Authorization model

| Authorization level | How it is assigned | What it permits |
|---|---|---|
| Platform Admin | OIDC token contains the value configured as `server.oidc.adminRole` | Creates and deletes workspaces, assigns Workspace Admins, manages global configuration, and bypasses workspace membership checks |
| Workspace Admin | OpenShell stores an `admin` membership for one OIDC subject in one workspace | Manages resources and ordinary members in that workspace but cannot create or delete workspaces |
| Workspace User | OpenShell stores a `user` membership for one OIDC subject in one workspace | Creates and uses sandboxes and services and reads permitted workspace resources |

The OpenShell admin role is a configuration value, not a required literal name. If Gateway A sets `adminRole` to `administrators-a`, that value is its Platform Admin role. A Workspace Admin membership is different and cannot create another workspace.

## Identity provider preparation

| Setting | Microsoft Entra ID | Keycloak |
|---|---|---|
| Role source | Application roles assigned to users or groups | Client roles on the gateway's API client, mapped to groups (realm roles also work) |
| Typical roles claim | `roles` | `resource_access.<api-client>.roles` (`realm_access.roles` for realm roles) |
| Gateway A roles | `administrators-a` and `users-a` | `administrators-a` and `users-a` |
| Gateway B roles | `administrators-b` and `users-b` | `administrators-b` and `users-b` |
| Workspace identity | Application-specific `sub` accepted by OpenShell | User ID under standard subject mapping |
| Administrative resolution | Use a stored `sub` mapping or controlled first-login enrollment | Query the user ID through the Admin REST API |

Before provisioning, verify that the Platform Admin token contains the configured administrator role. When scope enforcement is enabled, it must also contain `workspace:write` or `openshell:all`. Ordinary users need the configured user role and the scopes required for their resource operations.

OpenShell validates the token and stores its `sub` claim as the member identity; it does not query either identity provider. The administrative process must therefore resolve the exact `sub` value rather than infer it from a display name or email address.

## Choose the workspace layout

| Deployment need | Recommended action |
|---|---|
| One team workspace on a gateway | Use the automatically created `default` workspace and add members to it |
| Several teams or isolation boundaries on one gateway | Create one named workspace for each boundary and assign memberships separately |
| Hard tenant isolation or separate identity policy | Use separate gateways in addition to logical workspaces |

A logical OpenShell workspace is not an OpenShift namespace. The Kubernetes compute driver determines how OpenShell workspaces map to Kubernetes namespaces. The membership workflow in this guide remains the same for shared, managed, and operator workspace modes.

## Resolve the user subjects

`whoami` is a verification and fallback method. It is not required when the administrator or an enrollment service can obtain the exact subject safely.

### Keycloak before handoff

With Keycloak's standard subject behavior, the OIDC `sub` equals the immutable Keycloak user ID. A Keycloak administrator can read it in the console (**Users**, the user, **ID**) or query the exact user through the Admin REST API, carry the returned `UserRepresentation` `id` into the OpenShell membership command, and complete setup before handoff. Confirm that the client does not use a pairwise subject mapper or other custom subject mapping.

```
GET /admin/realms/openshell/users?username=mary&exact=true

# Carry the returned UserRepresentation id forward as <mary-subject>
```

### Microsoft Entra ID before handoff

Microsoft Entra `sub` is pairwise and application-specific. Microsoft Graph user `id` corresponds to the token `oid` claim, not `sub`, so do not preload a Graph object ID into current OpenShell membership. Use one of these paths:

- Reuse a previously captured `sub` only when it came from the exact OpenShell application, audience, tenant, and issuer.
- Prefer a controlled first-login enrollment service: record the pending workspace assignment by immutable, tenant-scoped `oid`; validate the user's token and eligibility role at first sign-in; capture `sub`; then use a protected Platform Admin service to add the membership.
- Use `whoami` as a manual fallback when no trusted mapping or enrollment service exists.

### Manual verification fallback

The user only needs to authenticate to OpenShell; no OpenShift account or cluster login is required. The `whoami` operation works before workspace membership exists.

```shell
# Mary and Cameron verify against Gateway A
openshell --gateway namespace-a whoami --output json

# Joe verifies against Gateway B
openshell --gateway namespace-b whoami --output json
```

Resolve the subject again when a deployment changes issuer, OIDC client, Keycloak subject mode, Entra tenant, or application audience. The same person can receive a different subject in a different identity context.

## Configure Gateway A

### Create or select the workspace

A member of `administrators-a` performs these commands. Create `team-a` when Gateway A needs a named workspace. Otherwise replace `team-a` with `default` and omit the create command. A workspace name is a lowercase DNS label of at most 19 characters.

```shell
openshell --gateway namespace-a workspace create --name team-a
openshell --gateway namespace-a workspace get team-a
```

### Add Mary and Cameron

The dashboard's workspace **Members** page does the same as these commands; it takes the subject and the role.

```shell
openshell --gateway namespace-a workspace member add \
  --workspace team-a \
  --subject '<mary-subject>' \
  --role user

openshell --gateway namespace-a workspace member add \
  --workspace team-a \
  --subject '<cameron-subject>' \
  --role user
```

Members of `administrators-a` do not need membership records because Platform Admins bypass workspace membership checks. Add an administrator as a member only when that identity should operate as a delegated Workspace Admin without the gateway-wide administrator role.

## Configure Gateway B

A member of `administrators-b` creates the named workspace and adds Joe. Use `default` instead when Gateway B needs only its automatically created workspace.

```shell
openshell --gateway namespace-b workspace create --name team-b

openshell --gateway namespace-b workspace member add \
  --workspace team-b \
  --subject '<joe-subject>' \
  --role user
```

## Delegate workspace administration

A Platform Admin can grant one user the Workspace Admin membership. That user still needs the gateway user role in the OIDC token. The delegated administrator can manage ordinary members and workspace resources but cannot create workspaces (`role 'administrators-a' required`) or grant another admin membership (`only platform admins can assign the workspace admin role`).

```shell
openshell --gateway namespace-a workspace member add \
  --workspace team-a \
  --subject '<delegated-admin-subject>' \
  --role admin
```

Keep `administrators-a` and `administrators-b` small. Use Workspace Admin membership when a team lead needs authority over one workspace but should not administer the entire gateway.

## Validate authorization

### Check stored membership

```shell
openshell --gateway namespace-a workspace member list --workspace team-a
openshell --gateway namespace-b workspace member list --workspace team-b
```

### Run positive tests

```shell
# Run as Mary or Cameron
openshell --gateway namespace-a workspace list
openshell --gateway namespace-a --workspace team-a sandbox list

# Run as Joe
openshell --gateway namespace-b workspace list
openshell --gateway namespace-b --workspace team-b sandbox list
```

### Run negative tests

- Before adding Cameron, confirm that his `users-a` role alone does not grant access to `team-a`.
- Confirm that Joe lacks the Gateway A user role and cannot use Gateway A.
- Confirm that Mary cannot create another workspace or assign an admin membership.
- If scope enforcement is enabled, test a token without `workspace:write` against workspace creation and membership changes.

## Operate the membership lifecycle

| Event | Platform action | Identity provider action |
|---|---|---|
| Onboard | Resolve the subject through directory lookup, enrollment, or `whoami` fallback and add membership | Assign the gateway user role |
| Change role | Remove the existing membership and add it again with the new role | Change the gateway role only if platform authority also changes |
| Move teams | Add the destination membership then remove the source membership after validation | Update group or role assignment when gateway access changes |
| Offboard | Remove every workspace membership immediately | Remove gateway roles and revoke identity provider sessions as required |
| Delete workspace | Remove contained resources then delete the workspace as Platform Admin | No change unless the related gateway role is also retired |

```shell
openshell --gateway namespace-a workspace member remove \
  --workspace team-a \
  --subject '<mary-subject>'
```

Removing an OpenShell membership affects workspace authorization independently of any access token already issued. Removing only an identity provider role may leave a previously issued token usable until it expires, so remove the OpenShell membership first during urgent workspace access revocation.

## Automate membership at scale

OpenShell does not currently convert an Entra group or Keycloak group directly into workspace membership. An external provisioner can reconcile identity provider group membership with OpenShell membership records, but it must preserve the separation between gateway roles and workspace roles. For Keycloak, [`scripts/user-auth/05-sync-members.sh`](../scripts/user-auth/05-sync-members.sh) is such a provisioner: it makes each workspace's members match the groups named `openshell-<namespace>-ws-<workspace>-users` and `-admins`.

| Provisioning concern | Required control |
|---|---|
| Provisioning record | Store the gateway, issuer, user subject, workspace, and desired membership role |
| Service identity | Use a dedicated Platform Admin service identity rather than a human token |
| Keycloak resolution | Query the immutable user ID and verify standard subject behavior |
| Entra resolution | Record the pending assignment by tenant-scoped `oid` and let controlled enrollment map it to application-specific `sub` |
| Claim safety | Do not substitute Entra `oid` for `sub` in current OpenShell; a future `oid` principal must be bound to the expected tenant issuer and restricted to user identities |
| Verification and audit | Run negative authorization tests, avoid logging tokens or secrets, and review stale memberships |

## Troubleshoot access

| Symptom | Likely cause | Check |
|---|---|---|
| User authenticates but sees no workspaces, or every call says `not a member of workspace` | No membership exists for the current token `sub` | Compare the validated token `sub` or enrollment record with `workspace member list` |
| Workspace creation is denied | Caller lacks the configured Platform Admin role or `workspace:write` scope | Inspect `whoami` roles and scopes |
| Workspace Admin cannot create a workspace | Workspace Admin is not a Platform Admin | Use a Platform Admin for workspace lifecycle operations |
| Membership exists but access is denied | Gateway user role or required operation scope is missing | Inspect token claims through `whoami` and compare gateway configuration |
| Same person works on one gateway only | The other gateway has a different role assignment or app-specific subject | Inspect each enrollment record or run `whoami` against both gateways |
| Entra membership was preloaded with Graph ID | `oid` was stored where OpenShell expects `sub` | Remove the incorrect membership and complete controlled enrollment |
| User retains access during offboarding | Only the identity provider role was removed or an old membership remains | Remove the OpenShell membership and review all assigned workspaces |

## Completion checklist

- [ ] Each gateway has distinct administrator and user roles configured in the expected claim.
- [ ] Platform Admin tokens contain the administrator role and `workspace:write` when scopes are enforced; ordinary users have the intended gateway role.
- [ ] The team selected `default` or created a named workspace intentionally.
- [ ] Every membership uses an exact subject obtained from Keycloak directory lookup, an Entra enrollment mapping, or the `whoami` fallback.
- [ ] No Entra `oid` was stored as `sub`; controlled enrollment validates the eligibility role before privileged membership creation.
- [ ] Mary and Cameron have the intended Gateway A membership and Joe has the intended Gateway B membership.
- [ ] Platform Admin identities are not added as members merely to obtain access they already have.
- [ ] Positive and negative authorization tests pass.
- [ ] Onboarding and offboarding procedures update both identity provider roles and OpenShell memberships.
- [ ] Automation uses a protected service identity and does not log tokens or secrets.

## Sources

These sources define the OpenShell authorization behavior and the identity provider role mechanisms used by this guide. Confirm commands and fields against the OpenShell release deployed in your environment.

1. [OpenShell workspaces](https://docs.nvidia.com/openshell/latest/how-it-works/workspaces)
2. [OpenShell Kubernetes access control](https://docs.nvidia.com/openshell/latest/kubernetes/access-control)
3. [Workspace authorization design (RFC 0011)](https://github.com/NVIDIA/OpenShell/tree/main/rfc/0011-multi-player-design)
4. [Entra application roles](https://learn.microsoft.com/en-us/entra/identity-platform/howto-add-app-roles-in-apps)
5. [Entra access token claims](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference)
6. [Keycloak role mappings](https://www.keycloak.org/docs/latest/server_admin/#proc-assigning-role-mappings_server_administration_guide)
7. [Keycloak Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
