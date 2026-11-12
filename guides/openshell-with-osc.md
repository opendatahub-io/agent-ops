# Run OpenShell sandboxes with Kata on OpenShift

> **Midstream Documentation**
>
> Treat the OpenShift installation path as experimental. Do not use it in production.

Configure an OpenShell workload to use a Kata RuntimeClass, verify the guest VM,
and check binary-aware network policy. This guide uses upstream CLI/chart
`0.1.7` with ODH runtime `v0.1.7-rhaiv.0`.

## Prerequisites

- Complete the [getting-started guide](getting-started-openshell-openshift.md) through the status check, including the Red Hat build of Agent Sandbox 0.9.0 prerequisite. `openshell status` must show `Connected`.
- Install the OpenShift sandboxed containers Operator and configure a Ready Kata node and RuntimeClass.
- Have cluster administrator access to approve the RuntimeClass for OpenShell use.
- Bring a workload image containing curl, Python 3, a shell and `sleep`, accessible to the cluster. The default NVIDIA Ubuntu workload image lacks the clients used here.

Run commands locally from a checkout of this repository. The examples use the
`openshell` namespace, Helm release `openshell`, local gateway `openshift` and
workspace `default` from the getting-started guide.

Check the available runtime classes and the Operator rollout:

```shell
oc get runtimeclass
oc get kataconfig
oc get nodes
```

Choose the RuntimeClass installed for your environment:

```shell
KATA_RUNTIMECLASS=kata
```

The RuntimeClass's `scheduling.nodeSelector` identifies its eligible nodes:

```shell
oc get runtimeclass "$KATA_RUNTIMECLASS" -o yaml
```

Wait for the Kata rollout and eligible nodes to be ready before proceeding.
Changing `KataConfig` can reboot nodes.

See the [OpenShift sandboxed containers documentation](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13) for runtime provisioning, including peer pod configuration.

## 1. Approve the RuntimeClass

OpenShell requires an approval label on the RuntimeClass. An ownership
annotation records approvals added by this installation, including across shell
sessions. Set the gateway namespace to the one used during installation:

```shell
OPENSHELL_NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
(
  set -eu
  KATA_APPROVAL_OWNER=$(oc get namespace "$OPENSHELL_NAMESPACE" -o jsonpath='{.metadata.uid}')
  CURRENT_KATA_LABEL=$(oc get runtimeclass "$KATA_RUNTIMECLASS" \
    -o jsonpath='{.metadata.labels.openshell\.ai/sandbox-attachable}')
  CURRENT_KATA_OWNER=$(oc get runtimeclass "$KATA_RUNTIMECLASS" \
    -o jsonpath='{.metadata.annotations.agent-ops\.opendatahub\.io/kata-guide-owner}')
  if [ -n "$CURRENT_KATA_OWNER" ] && [ "$CURRENT_KATA_OWNER" != "$KATA_APPROVAL_OWNER" ]; then
    echo 'Approval is owned by another installation; ask its administrator.' >&2
    exit 1
  elif [ -z "$CURRENT_KATA_LABEL" ]; then
    oc annotate runtimeclass "$KATA_RUNTIMECLASS" \
      "agent-ops.opendatahub.io/kata-guide-owner=$KATA_APPROVAL_OWNER" &&
    oc label runtimeclass "$KATA_RUNTIMECLASS" openshell.ai/sandbox-attachable=true
  elif [ "$CURRENT_KATA_LABEL" != true ]; then
    echo 'RuntimeClass has a conflicting approval label; ask its administrator.' >&2
    exit 1
  fi
)
```

Continue only if the label is `true` and any ownership annotation belongs to
this installation. Repeating the step preserves ownership. The approval is
shared by gateways using the default resource admission policy.

## 2. Set the default runtime class

Retain the images, security settings and Route certificate configuration from
the getting-started installation:

```shell
helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.7 \
  --namespace "$OPENSHELL_NAMESPACE" \
  --reuse-values \
  --set server.defaultRuntimeClassName="$KATA_RUNTIMECLASS" \
  --wait --timeout 5m
openshell status
```

This setting applies to newly created sandboxes. OpenShell runs the workload
and supervisor in separate pods. The workload pod uses the selected Kata
RuntimeClass; the supervisor retains its own runtime configuration. This guide
covers the gateway-wide default. Per-sandbox RuntimeClass selection requires
separate driver configuration and is outside this procedure.

## 3. Create and inspect a Kata-backed sandbox

Set your workload image and start a sandbox that stays running for the checks:

```shell
WORKLOAD_IMAGE='<your-registry/your-workload-image:tag>'
openshell sandbox create --name kata-demo \
  --from "$WORKLOAD_IMAGE" --policy examples/policies/deny-network.yaml \
  --detach -- sleep infinity
```

In the default workspace, the workload pod is named `default--kata-demo`:

```shell
POD_NAME=default--kata-demo
oc wait --for=condition=Ready "pod/$POD_NAME" -n "$OPENSHELL_NAMESPACE" --timeout=300s
oc get pod "$POD_NAME" -n "$OPENSHELL_NAMESPACE" \
  -o jsonpath='{.spec.runtimeClassName}{"\n"}{.status.phase}{"\n"}'
openshell sandbox exec kata-demo -- uname -r
```

Expect the chosen RuntimeClass, a `Running` workload and a guest kernel version.
Use the actual workload pod name if you selected another workspace.

### Inspect the VM boundary

Read the guest's DMI information through Kubernetes exec:

```shell
oc exec -n "$OPENSHELL_NAMESPACE" "$POD_NAME" -c agent -- cat \
  /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name
```

For additional context, compare the guest kernel with the node kernel:

```shell
NODE_NAME=$(oc get pod "$POD_NAME" -n "$OPENSHELL_NAMESPACE" -o jsonpath='{.spec.nodeName}')
oc get node "$NODE_NAME" -o jsonpath='{.status.nodeInfo.kernelVersion}{"\n"}'
```

The sandbox filesystem policy can restrict access to DMI paths, so these
optional diagnostics use Kubernetes exec. DMI values vary by hypervisor and guest
image. Use the workload's RuntimeClass and successful execution alongside these
diagnostics to verify the intended runtime. DMI output alone is not an isolation
security assessment.

## 4. Verify network policy

Inspect the explicit base policy and effective policy first:

```shell
openshell policy get kata-demo --base
openshell policy get kata-demo --full
```

Confirm `network_policies` is empty. A gateway-global policy must not add grants
that bypass these checks.

### Check the initial denial

```shell
openshell sandbox exec kata-demo -- curl -I --max-time 20 https://github.com
openshell logs kata-demo --source sandbox -n 200
```

Expect curl to fail. Confirm a `DENIED` event for curl and `github.com:443` in
the audit logs; a connection failure alone does not establish policy enforcement.

### Allow curl and keep Python blocked

Resolve curl's canonical executable path in your image and allow it to reach
GitHub:

```shell
CURL_BINARY=$(openshell sandbox exec kata-demo -- sh -c 'readlink -f "$(command -v curl)"')
openshell policy update kata-demo \
  --add-endpoint github.com:443:read-only:rest:enforce \
  --binary "$CURL_BINARY" \
  --wait
openshell sandbox exec kata-demo -- curl -I --max-time 20 https://github.com
openshell sandbox exec kata-demo -- python3 -c \
  "import urllib.request; urllib.request.urlopen('https://github.com', timeout=10)"
openshell logs kata-demo --source sandbox -n 300
```

Expect a successful HTTP response for curl and a connection or permission error
for Python. Match the audit events to the canonical binary paths and destination:
curl should be `ALLOWED`, while Python should be `DENIED` for `github.com:443`.

## Security model and scope

Kata runs the workload behind a guest kernel and VM boundary. OpenShell's
supervisor enforces the workload's endpoint, HTTP and executable identity policy.
The workload remains subject to the guest kernel support required by OpenShell's
seccomp and filesystem controls.

Validation passed on OpenShift `4.22.22`, OpenShift sandboxed containers Operator
`1.13.1` and Red Hat build of Agent Sandbox `0.9.0`, using the `kata` RuntimeClass.
The workload ran under `restricted-v2` without added capabilities. Peer pods
(`kata-remote`), GPU runtimes and other guest kernel combinations need separate
validation.

## Troubleshooting

- **`external resource not admitted by required labels`:** verify the RuntimeClass approval label and the gateway's resource admission configuration.
- **Workload stays Pending:** inspect pod events, RuntimeClass scheduling constraints, node readiness and PVC provisioning with `oc describe pod "$POD_NAME" -n "$OPENSHELL_NAMESPACE"`.
- **Runtime startup fails:** inspect workload, supervisor and Kata operator logs. Confirm guest kernel support for the runtime's required seccomp and Landlock operations.
- **Python succeeds after the curl policy update:** inspect `openshell policy get kata-demo --full` for another rule that permits the Python binary to reach the destination.
- **RuntimeClass change has no effect:** create a new sandbox after the Helm upgrade and inspect that workload pod's `spec.runtimeClassName`.

## Cleanup

Delete the sandbox and restore the getting-started default runtime. If using a
new shell, set `KATA_RUNTIMECLASS` and `OPENSHELL_NAMESPACE` to the values used
above. Cleanup refuses to proceed when either is missing:

```shell
(
  set -eu
  : "${KATA_RUNTIMECLASS:?Set the RuntimeClass used by this guide}"
  : "${OPENSHELL_NAMESPACE:?Set the gateway namespace used by this guide}"
  openshell sandbox delete kata-demo
  if helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
    --version 0.1.7 \
    --namespace "$OPENSHELL_NAMESPACE" \
    --reuse-values \
    --set server.defaultRuntimeClassName="" \
    --wait --timeout 5m; then
    KATA_APPROVAL_OWNER=$(oc get namespace "$OPENSHELL_NAMESPACE" -o jsonpath='{.metadata.uid}')
    CURRENT_KATA_OWNER=$(oc get runtimeclass "$KATA_RUNTIMECLASS" \
      -o jsonpath='{.metadata.annotations.agent-ops\.opendatahub\.io/kata-guide-owner}')
    if [ "$CURRENT_KATA_OWNER" = "$KATA_APPROVAL_OWNER" ]; then
      oc label runtimeclass "$KATA_RUNTIMECLASS" openshell.ai/sandbox-attachable- &&
      oc annotate runtimeclass "$KATA_RUNTIMECLASS" agent-ops.opendatahub.io/kata-guide-owner-
    fi
  else
    echo 'Runtime restore failed; approval is retained. Retry the Helm upgrade before removing it.' >&2
    exit 1
  fi
)
```

Remove only the approval label added by this procedure. Leave the operators,
RuntimeClass and node configuration in place. To remove the gateway too, follow
the getting-started guide's cleanup.
