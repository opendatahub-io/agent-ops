# OpenShell v0.0.85 on OpenShift - SCC-Requirements

> [!NOTE]
> Historical. These results apply to OpenShell v0.0.85. Since 0.1.0, OpenShell is
> capability-free: the gateway, supervisor, and sandbox pods run under the default
> `restricted-v2` SCC with no added capabilities (validated with `main` at `8719fc9`
> on OpenShift 4.20.27).

## Versions Tested
- OpenShell: v0.0.85
- Agent Sandbox: Red Hat build v0.9.0
- OpenShift: ROSA, OpenShift Container Platform 4.22.8, kernel 5.14.0

## Deployment Verification

✅ **Gateway deployment:**
- Gateway deploys
- TLS certificates generate
- Secrets generate and can be loaded for use in the CLI
- Can connect via OpenShift Route

✅ **Sandbox creation:**
- Sandbox creation works with custom SCC
- Sandboxes can be accessed via CLI and oc exec

---

## Capability Testing Results

### How to Read This Section

Each sandbox pod has two contexts: a **root supervisor** (UID 0) that sets up isolation, and a **sandbox user** (UID 1000840000) that is the agent's runtime environment with zero capabilities (`CapEff: 0x0`).

The matrix below shows what happens when each capability-gated operation is probed from inside the sandbox. Each operation was run from both contexts. This is about observed runtime behavior, not what the SCC grants.

### Pod Admission (SCC Gate)

In the tested OpenShell v0.0.85 configuration, the sandbox pod requests the SYS_ADMIN, NET_ADMIN, SYS_PTRACE, and SYSLOG Linux capabilities. The SCC must permit these capabilities or OpenShift rejects the pod during admission. 

| | `restricted-v2` (OpenShift baseline) | `openshell-sandbox-minimum-required` (custom SCC) |
|--|------------------------------------------|-------------------------------------------------------|
| **Does the pod start?** | 🚫 No — denies all 4 required capabilities | ✅ Yes — allows SYS_ADMIN, NET_ADMIN, SYS_PTRACE, SYSLOG |

`restricted-v2` only allows `NET_BIND_SERVICE` and requires non-root via `MustRunAsRange`. OpenShell needs the 4 capabilities listed above and must run the supervisor as UID 0.

### Runtime Capability Probes

✅ = Operation succeeds
❌ = Operation not permitted
⚠️ = Partially works (see notes)

| Capability | Operation Tested | Root context | Sandbox user |
|------------|-----------------|--------------|--------------|
| SYS_ADMIN | Mount filesystem, create namespace | ✅ | ❌ |
| NET_ADMIN | Modify network config, create veth pair | ✅ | ❌ |
| SYS_PTRACE | Read `/proc/<pid>/maps`, call `ptrace()` | ✅ | ⚠️ |
| SYSLOG | Write to syslog via `logger` | ✅ | ⚠️ see note |
| SETUID | Change user ID | ✅ | ❌ |
| SETGID | Change group ID | ✅ | ❌ |
| SETPCAP | Modify capability bounding set via `capsh` | ✅ | ⚠️ |
| CHOWN | Change file ownership | ✅ | ❌ |
| FOWNER | Bypass file ownership checks | ✅ | ❌ |

**Notes:**

- **Root context**: Has the 4 SCC-granted capabilities plus default container capabilities (CHOWN, FOWNER, SETUID, SETGID, SETPCAP, etc.) preserved by `requiredDropCapabilities: []`.
- **Sandbox user**: All `CapEff` fields are `0x0`. The supervisor drops all capabilities when spawning the sandbox user process.
- **SYS_PTRACE (⚠️)**: Can read `/proc` for basic process info (status, cmdline) without capabilities, but cannot read memory maps (`/proc/<pid>/maps`) of processes with different UID. The `ptrace()` syscall itself fails.
- **SYSLOG (⚠️)**: `logger` succeeds because it writes to a socket (`/dev/log`), not because the sandbox user has the capability. SYSLOG must be in the SCC's `allowedCapabilities` because the pod spec requests it — without it, pod admission fails. This is a pod admission requirement, not a runtime capability the sandbox user exercises.
- **SETPCAP (⚠️)**: Can read the capability bounding set (`cat /proc/self/status | grep Cap`), but cannot modify it (`capsh` fails with `Operation not permitted`).

### Required Capabilities Summary

| Capability | Why it's required |
|------------|-------------------|
| SYS_ADMIN | Network namespace creation, mount operations |
| NET_ADMIN | Veth pair creation, network configuration |
| SYS_PTRACE | Process monitoring, /proc access |
| SYSLOG | Pod spec requests it — pod admission fails without it in SCC (runtime uses socket I/O) |

Additional capabilities needed only if user namespaces are enabled (not supported in v0.0.85):
- `SETUID` — User ID manipulation for namespace mapping
- `SETGID` — Group ID manipulation for namespace mapping
- `DAC_READ_SEARCH` — Discretionary access control bypass for user namespace operations

---

## Verified Isolation Features

### ✅ Seccomp Filter Installation

- `Seccomp: 2` = Filter mode (0=disabled, 1=strict, 2=filter)
- Supervisor has 1 filter, sandbox user has 3 filters
- ✅ Seccomp is actively filtering syscalls

### ✅ Network Namespace Isolation

- ✅ Veth pair connects supervisor and sandbox network namespaces
- ✅ Each context has its own isolated network namespace
- ⚠️ nftables/iptables tools not in container (rules managed by supervisor at setup time)

### ✅ Supervisor Privilege Drop

- ✅ Supervisor retains capabilities for isolation setup (CapEff: `00000004002815fb`)
- ✅ Agent process runs with zero capabilities (CapEff: `0x0`)
- ✅ Privilege separation confirmed

### ✅ Process Visibility from Sandbox

- ✅ Can view process list and basic status
- ❌ Cannot read memory maps of processes with different UID

---

## User Namespace

**Cluster support:**
- ✅ Cluster supports user namespaces (kernel 5.14+)
- ✅ Sandbox CRD has the `hostUsers` field

**OpenShell support:**
- ❌ OpenShell v0.0.85 does NOT work with `hostUsers: false`

OpenShell v0.0.85 requires `hostUsers: true` (default). User namespace isolation is not supported in this version. The supervisor shares the host user namespace, meaning UID 0 inside the container maps to UID 0 on the host.

---

## Security Implications

The custom SCC (`openshell-sandbox-minimum-required`) is NOT "restricted" in security terms — the name reflects **capability minimization** (the bare minimum needed), not security restriction.

**What the custom SCC grants (supervisor only):**
- SYS_ADMIN — create namespaces, mount filesystems, manipulate cgroups
- NET_ADMIN — modify network config, firewall rules, routing
- SYS_PTRACE — inspect process memory, /proc access
- SYSLOG — required in pod spec for admission
- Run as root (UID 0)

**What the custom SCC blocks:**
- Host network/PID/IPC access
- Host path volumes
- Fully privileged mode

The sandbox user (agent runtime) has zero capabilities. However, user namespace isolation is not supported in v0.0.85, so the supervisor's UID 0 maps directly to the host.
