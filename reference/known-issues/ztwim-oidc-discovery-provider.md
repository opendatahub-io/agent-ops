# ZTWIM 1.1.1: SPIRE OIDC discovery provider never becomes ready

**Status:** reported to the Zero Trust Workload Identity Manager team as [OCPBUGS-129473](https://redhat.atlassian.net/browse/OCPBUGS-129473) (2026-10-07).
Workaround: install ZTWIM into its suggested namespace, or run `scripts/token-exchange/01-fix-ztwim-oidc.sh`.

## Summary

**Scope (verified 2026-10-07):** this only happens when the operator is installed into a namespace whose name starts with `openshift-`. The operator's suggested namespace is `zero-trust-workload-identity-manager`, which is not affected. On our test cluster the operator had been installed into `openshift-ztwim`. If you install ZTWIM into its suggested namespace, you do not need the workaround below.

With Zero Trust Workload Identity Manager (ZTWIM) 1.1.1, the SPIRE OIDC discovery provider
pod never receives a SPIFFE identity and crash-loops indefinitely. The operator-generated
SPIRE controller-manager configuration ignores `openshift-*` namespaces, and the glob also
matches ZTWIM's own namespace, `openshift-ztwim`. The `ClusterSPIFFEID` that the operator
creates for the OIDC discovery provider therefore never selects its pod.

## Impact

- The OIDC discovery endpoint (`/.well-known/openid-configuration`, `/keys`) is unavailable,
  so nothing can verify JWT-SVIDs over HTTPS. Keycloak's SPIFFE identity provider, and any
  other OIDC relying party that federates with SPIRE, cannot authenticate workloads.
- `ZeroTrustWorkloadIdentityManager` reports `Ready=False`
  (`Waiting for operands: [SpireOIDCDiscoveryProvider(reconciling)]`), which hides real
  failures from monitoring.
- X.509-SVID and JWT-SVID issuance through the Workload API is not affected, so consumers
  that verify SVIDs only through the Workload API (for example the OpenShell gateway) keep
  working, which makes the problem easy to miss.

## Environment

| Item | Value |
|---|---|
| OpenShift | 4.20.27 |
| ZTWIM operator | `zero-trust-workload-identity-manager.v1.1.1`, channel `stable-v1`, installed into `openshift-ztwim` (not the suggested `zero-trust-workload-identity-manager`) |
| SPIRE controller-manager | 0.6.4 |
| SPIRE OIDC discovery provider | 1.14.7 |
| `ZeroTrustWorkloadIdentityManager` spec | only `trustDomain`, `clusterName`, `bundleConfigMap` set (defaults otherwise) |

## Symptoms

```shell
$ oc -n openshift-ztwim get pods | grep oidc
spire-spiffe-oidc-discovery-provider-7b45f86f78-g9tn7   0/1   CrashLoopBackOff   8902   36d
spire-spiffe-oidc-discovery-provider-d7cdb9f9f-t8mpt    0/1   CrashLoopBackOff   10211  42d

$ oc -n openshift-ztwim logs deploy/spire-spiffe-oidc-discovery-provider
level=warning msg="Failed to fetch JWKS from the Workload API" error="rpc error: code = PermissionDenied desc = no identity issued"
```

`SpireOIDCDiscoveryProvider/cluster` reports `DeploymentAvailable=False`.

## Root cause

The operator writes this controller-manager configuration
(`ConfigMap openshift-ztwim/spire-controller-manager`):

```yaml
ignoreNamespaces:
- kube-system
- kube-public
- local-path-storage
- openshift-*
```

The operator also creates
`ClusterSPIFFEID/zero-trust-workload-identity-manager-spire-oidc-discovery-provider`, whose
`namespaceSelector` selects only `openshift-ztwim`. Because the controller-manager skips
ignored namespaces, the ClusterSPIFFEID never registers the pod:

```yaml
status:
  stats:
    namespacesIgnored: 1
    namespacesSelected: 1
    podsSelected: 0
    entriesToSet: 0
```

With no registration entry, the SPIRE agent refuses the pod's Workload API request
(`no identity issued`). The provider needs its own SVID to read the trust bundle, so it exits
and restarts.

## Steps to reproduce

1. Install ZTWIM 1.1.1 from the Software Catalog (channel `stable-v1`) into a namespace named `openshift-<anything>` instead of the suggested `zero-trust-workload-identity-manager`.
2. Create `ZeroTrustWorkloadIdentityManager`, `SpireServer`, `SpireAgent`, `SpiffeCSIDriver`,
   and `SpireOIDCDiscoveryProvider` CRs with defaults.
3. Observe the OIDC discovery provider pod in `CrashLoopBackOff` and the ClusterSPIFFEID
   status above.

**Expected:** the OIDC discovery provider receives
`spiffe://<trust-domain>/ns/openshift-ztwim/sa/spire-spiffe-oidc-discovery-provider` and
serves the JWKS. **Actual:** no SVID, permanent crash loop.

## Workaround

Register the OIDC discovery provider with `ClusterStaticEntry` resources, which the
controller-manager reconciles regardless of `ignoreNamespaces`. One entry is needed per SPIRE
agent, because the parent ID includes the node UID:

```shell
./scripts/token-exchange/01-fix-ztwim-oidc.sh
```

Equivalent manifest, per worker node:

```yaml
apiVersion: spire.spiffe.io/v1alpha1
kind: ClusterStaticEntry
metadata:
  name: oidc-discovery-provider-<node-uid-prefix>
spec:
  className: zero-trust-workload-identity-manager-spire
  parentID: spiffe://<trust-domain>/spire/agent/k8s_psat/<cluster-name>/<node-uid>
  spiffeID: spiffe://<trust-domain>/ns/openshift-ztwim/sa/spire-spiffe-oidc-discovery-provider
  selectors:
    - k8s:ns:openshift-ztwim
    - k8s:sa:spire-spiffe-oidc-discovery-provider
  dnsNames:
    - spire-spiffe-oidc-discovery-provider.openshift-ztwim.svc.cluster.local
```

Then delete the OIDC discovery provider pod. Within a minute it runs, serves the JWKS, and
`ZeroTrustWorkloadIdentityManager` reports `Ready=True`. Editing the controller-manager
ConfigMap does not work: the operator reverts it.

Caveats: re-run the script after adding or replacing worker nodes (new node UIDs), and
remove the entries once a fixed ZTWIM release is installed:

```shell
oc delete clusterstaticentry -l agent-ops/workaround=ztwim-ignore-namespaces
```

## Suggested fix

Either exclude the operator's own namespace from the generated ignore list (for example list
the platform namespaces explicitly instead of `openshift-*`), or have the operator register the
OIDC discovery provider with a `ClusterStaticEntry` instead of a `ClusterSPIFFEID`.
