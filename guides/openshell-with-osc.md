# Running OpenShell sandboxes with Kata runtime on OpenShift

> **Midstream Documentation**
>
> Treat the OpenShift installation path as experimental. Do not use it in production.

This guide builds on [Getting Started with OpenShell on OpenShift](getting-started-openshell-openshift.md). You configure new sandboxes to use a Kata-backed RuntimeClass, enable sidecar topology in OpenShell, and verify the virtual machine (VM) boundary and OpenShell policy enforcement. By the end of this guide, you can run a Kata-backed sandbox and verify how this topology applies runtime and network controls.

Perform the following steps:

1. Configure the OpenShell gateway to use a Kata runtime class.
2. Optional: If you use `kata` runtime class, patch the Kata initramfs.
3. Create a Kata-backed sandbox.
4. Optional: Verify the VM isolation boundary.
5. Verify policy enforcement in the Kata-backed sandbox.

Run all commands on your local machine unless otherwise specified.

## Prerequisites

- You have completed the [Getting Started with OpenShell on OpenShift](getting-started-openshell-openshift.md) guide through the **Configure an inference provider** section.
- You have installed the OpenShift sandboxed containers Operator on the cluster.
- A Kata `RuntimeClass` is available on the cluster.
- If you use the `kata-remote` runtime, a valid `peer-pods-cm` ConfigMap exists on the cluster.

##### **ADDITIONAL RESOURCES**

* [Deploying OpenShift sandboxed containers on bare-metal servers](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_openshift_sandboxed_containers_on_bare-metal_servers/index)
* [Deploying OpenShift sandboxed containers on AWS](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_openshift_sandboxed_containers_on_aws/index)

## 1. Update the OpenShell gateway to use Kata

Configure the OpenShell gateway to use `sidecar` topology and binary identity check. You can also set a default runtime class for new sandboxes.

> **Important**
>
> Choose how you want to apply the Kata runtime class:
> - To use Kata by default for all new sandboxes, include `--set server.defaultRuntimeClassName="${KATA_RUNTIMECLASS}"` in the Helm command.
> - To specify the runtime class separately for each sandbox, omit this option. You can set the runtime class when you create the sandbox.

### Procedure

1. Set `KATA_RUNTIMECLASS` to the value that matches your environment:
- Use `kata` on bare-metal clusters or clusters with nested virtualization support.
- Use `kata-remote` on clusters that use peer pods such as Red Hat OpenShift Service on AWS (ROSA).
```shell
KATA_RUNTIMECLASS=<runtime-class-name>
```

2. Update the OpenShell Helm chart to set the runtime class, sidecar topology, and binary-aware network policy:
```shell
helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.2 \
  --namespace openshell \
  --reuse-values \
  --set supervisor.topology=sidecar \
  --set supervisor.sidecar.processBinaryAwareNetworkPolicy=true \
  --set server.defaultRuntimeClassName="${KATA_RUNTIMECLASS}"
```

`server.defaultRuntimeClassName` sets the default `RuntimeClass` for new sandboxes unless a sandbox specifies a different runtime class. `supervisor.topology=sidecar` configures OpenShell to enforce network policy from a dedicated sidecar container. With this topology, the agent container runs without additional Linux capabilities.

### Verification

Wait for the OpenShell gateway pod to return to `Running` before you continue:

```shell
oc get pods -n openshell
```

The OpenShell gateway is ready when the pod status is `Running`.

## 2. Patch the Kata initramfs

The default Kata initramfs shipped with OpenShift sandboxed containers Operator does not include `nf_tables` kernel modules. OpenShell `sidecar` topology requires `nf_tables` for traffic interception inside the Kata guest VM. Without these modules, sandbox creation fails.

Skip this step if you use `kata-remote`. Peer pod VMs use a Red Hat Enterprise Linux image that includes this module.

### Procedure

1. Grant the `kata-install` service account privileged access. The patch DaemonSet runs privileged containers with host filesystem access. On bare-metal clusters, the Operator does not create the required SCC automatically:

```shell
oc adm policy add-scc-to-user privileged -z kata-install -n openshift-sandboxed-containers-operator
```

2. Apply the nftables patch DaemonSet to add `nf_tables` and its dependencies to the Kata initramfs on all Kata worker nodes:

```shell
oc apply -f https://raw.githubusercontent.com/opendatahub-io/agent-ops/main/manifests/kata-nftables-patch-job.yaml
```

3. Wait for all patch pods to reach `Running` with `1/1` ready. Each pod patches one node and then sleeps:

```shell
oc get pods -n openshift-sandboxed-containers-operator -l app=kata-nftables-patch
```

4. Verify the nftables patch by checking the logs:

```shell
oc logs -n openshift-sandboxed-containers-operator -l app=kata-nftables-patch
```

Each pod should report `[+] Patch complete. Sleeping.`

5. Clean up the patch DaemonSet and revoke the privileged SCC:

```shell
oc delete -f https://raw.githubusercontent.com/opendatahub-io/agent-ops/main/manifests/kata-nftables-patch-job.yaml
oc adm policy remove-scc-from-user privileged -z kata-install -n openshift-sandboxed-containers-operator
```

> **Important**
>
> The patch is removed when a worker node reboots because the `kata-osbuilder-generate` service regenerates the initramfs from the unpatched dracut configuration. Reapply the patch after a worker node reboot or after a `KataConfig` change that causes the worker nodes to roll out.

## 3. Create a Kata-backed sandbox

Create a sandbox that uses the Kata runtime class that you configured earlier. You can use the gateway-wide default runtime class or specify the runtime class when you create the sandbox.

### Procedure

Run the command that matches how you configured the runtime class.

If you configured a gateway-wide default runtime class, create the sandbox by running the following command:

```shell
openshell sandbox create --name kata-demo \
  --from ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e \
  --provider my-openai \
  -- sleep 1
```

If you want to override the gateway default and specify the runtime class for this sandbox only, run the following command:

```shell
openshell sandbox create --name kata-demo \
  --from ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e \
  --provider my-openai \
  --driver-config-json "{\"kubernetes\":{\"pod\":{\"runtime_class_name\":\"${KATA_RUNTIMECLASS}\"}}}" \
  -- sleep 1
```

The per-sandbox `runtime_class_name` value overrides the OpenShell gateway default for this sandbox.

### Verification

Verify that the sandbox pod is configured to use the expected Kata runtime class by running the following command:

```shell
oc get pod -n openshell kata-demo -o jsonpath='{.spec.runtimeClassName}'
```

The command returns the runtime class assigned to the sandbox pod. Verify that the output is `kata` for deployments that use bare-metal/nested virtualization or `kata-remote` for deployments that use peer pods.

## 4. Verify the VM isolation boundary

The runtime class verification in section 3 confirms that the sandbox pod is configured to use the `kata` or `kata-remote` runtime. OpenShift Sandboxed Containers uses the Kata runtime to run the workload in a dedicated virtual machine (VM).

You can perform the following DMI checks to gather additional information about the VM environment and confirm that it uses the expected hypervisor. These checks provide supplementary diagnostic information and are not required to verify that the sandbox is configured to use Kata.

1. Verify that the sandbox is running in the expected virtual machine environment by checking the system vendor and product name:

```shell
oc exec -n openshell kata-demo -c openshell-supervisor-network -- cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name
```

Example output on a ROSA cluster:
```text
Amazon EC2
t3.medium
```

Example output on a bare-metal cluster:
```text
Red Hat
KVM
```

2. Compare the sandbox VM information with the host node by running the following commands:

```shell
NODE_NAME=$(oc get pod -n openshell kata-demo -o jsonpath='{.spec.nodeName}')
oc debug node/"${NODE_NAME}" -- chroot /host cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name
```

Compare the host output with the values returned from the sandbox in the previous step. On bare-metal clusters, the host typically reports the physical hardware vendor, such as Dell Inc., while the sandbox reports values associated with the virtualized guest environment.

On peer pod deployments, both environments might report cloud-provider information, but the sandbox product name should reflect the peer pod instance type configured in the peer-pods-cm ConfigMap.

## 5. Verify policy enforcement in the Kata-backed sandbox

The following checks confirm that OpenShell still enforces network policy in a Kata-backed pod.

### Confirm that network egress is blocked by default

1. Connect to the `kata-demo` sandbox by running the following command:

```shell
openshell sandbox connect kata-demo
```

2. From the sandbox shell, verify that outbound network access is blocked by attempting to connect to GitHub:

```shell
curl https://github.com
```

Expected output:

```text
curl: (56) CONNECT tunnel failed, response 403
```

This result shows that sandbox egress still flows through the OpenShell network policy path. Running the pod behind Kata does not bypass policy enforcement.

### Allow network access for one binary

1. From your local machine, update the sandbox policy to allow only `/usr/bin/curl` to connect to GitHub:

```shell
openshell policy update kata-demo \
  --add-endpoint github.com:443:read-only:rest:enforce \
  --binary /usr/bin/curl \
  --wait
```

2. From the sandbox shell, verify that the policy allows curl but continues to block other binaries by running the following commands:

```shell
curl -I https://github.com
python3 -c "import urllib.request; urllib.request.urlopen('https://github.com')"
```

`curl` should succeed, while `python3` should still fail with a tunnel connection failure. This result confirms that the sidecar still matches on binary identity, not only on destination.

3. From your local machine, open the OpenShell terminal to view the corresponding network audit events:

```shell
openshell term
```

4. Select `kata-demo`, and then press `l` to open the live log view. Verify that the log shows `ALLOWED` events for requests from `/usr/bin/curl` and `DENIED` events for requests from `python3` to the same destination.

## Security model

OpenShell `sidecar` topology and a Kata `RuntimeClass` apply controls at different layers. OpenShell enforces network policy for sandbox workloads, while Kata provides an additional isolation boundary by running the pod inside a guest virtual machine (VM).

The `combined` topology also enforces OpenShell network policy, but `sidecar` moves that enforcement into a dedicated sidecar so the long-running agent container can run without added Linux capabilities.

| Layer | Scope |
| --- | --- |
| OpenShell network sidecar (binary-aware, default) | Runs as user ID (UID) 0 with `SYS_PTRACE` and `DAC_READ_SEARCH` to resolve workload binary identity through `/proc`. Enforces endpoint, Layer 7 (L7), and binary-aware network policy for sandbox egress. Setting `processBinaryAwareNetworkPolicy=false` runs the sidecar as a non-root UID and drops both capabilities, but downgrades enforcement to endpoint and L7 matching only. |
| Agent container in `sidecar` mode | Runs as the sandbox UID and group ID, with `allowPrivilegeEscalation: false` and dropped Linux capabilities. |
| Kata `RuntimeClass` | Runs the pod behind a guest kernel and VM boundary instead of relying only on the host kernel boundary. |

OpenShell and Kata address different concerns. OpenShell controls outbound access from the workload. Kata changes the runtime isolation boundary by running the pod in a guest virtual machine.

##### **ADDITIONAL RESOURCES**

* [OpenShell Kubernetes Topology documentation](https://github.com/NVIDIA/OpenShell/blob/main/docs/kubernetes/topology.mdx)

## Known limitations

- In `sidecar` topology, OpenShell does not run the full enforcement path. The process supervisor runs in `network-only` mode. OpenShell still enforces network policy through the sidecar, and Landlock filesystem policy plus child seccomp filters still apply where supported. However, root-to-sandbox privilege dropping and supervisor identity mount isolation do not run in the agent container.
- `gVisor` is not supported with `sidecar` topology because sidecar mode requires pod-local `nftables` setup in the init container.
- Kata adds a guest virtual machine boundary, but it does not replace the additional controls provided by combined topology.
- Creating or changing the cluster `KataConfig` can reboot worker nodes while the Kata runtime rolls out. Wait for the `RuntimeClass` to appear and for worker nodes to return before you test sandboxes.

## Troubleshooting

- **The sandbox pod does not use Kata.** Confirm that the runtime class name in `server.defaultRuntimeClassName` or `runtime_class_name` exactly matches one of the values returned by `oc get runtimeclass`, and then recreate the sandbox.
- **`python3` is allowed after the `curl` policy update.** Verify that `supervisor.sidecar.processBinaryAwareNetworkPolicy=true`. Setting it to `false` downgrades `sidecar` network policy to endpoint and L7 matching only.
- **The OpenShell gateway pod does not recover after the Helm upgrade.** Check `oc get pods -n openshell` and confirm that the Helm values still include the Route hostname, `sidecar` topology, and the expected runtime class setting.
- **No Kata runtime class is available.** Wait for the OpenShift sandboxed containers Operator rollout to finish, or verify that the Operator and `KataConfig` are installed correctly.

## Cleanup

1. To remove the kata-demo sandbox, run the following command:

```shell
openshell sandbox delete kata-demo
```

2. If you want to keep OpenShell installed but return the gateway to the configuration used in the getting-started guide, run the following command:

```shell
helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.2 \
  --namespace openshell \
  --reuse-values \
  --set supervisor.topology=combined \
  --set server.defaultRuntimeClassName=""
```

If you no longer need the OpenShell installation, remove the gateway and associated resources by running the following commands:

```shell
helm uninstall openshell -n openshell
oc delete ns openshell
openshell gateway remove openshift
rm -rf ~/.config/openshell/gateways/openshift
```
