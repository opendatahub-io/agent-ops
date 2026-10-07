# Bring your own agent image

> **Midstream Documentation**
>
> Validated on 2026-10-06; versions in [What was validated](../README.md#what-was-validated-and-its-support-status).
> Do not use in production.

An OpenShell sandbox runs whatever container image you give it. OpenShell adds the isolation (filesystem, process and network policy, credential injection) around your agent; the image only has to contain the agent. This guide shows what such an image needs, builds a small example, and runs it.

## What the image needs

| Needs | Does not need |
|---|---|
| A shell (`/bin/sh` or `/bin/bash`) | Any OpenShell component: the Kubernetes driver injects the sandbox runtime with an init container |
| The agent and its runtime, under `/usr` (or another path you allow in the policy) | `iproute2`, `nftables` or `nsenter` (required by older OpenShell builds, not by current ones) |
| Files it writes under `/sandbox` or `/tmp` (the default writable paths) | A `/sandbox` directory: the driver creates it |
| | A specific user: on OpenShift the pod runs as a UID from the namespace range under the default `restricted-v2` SCC |

Start from **`registry.access.redhat.com/ubi9/ubi-minimal`**. It has a shell and `microdnf` to add what the agent needs, and it is in the Red Hat OpenShift AI additional images, so a disconnected mirror picks it up.

The sandbox policy names binaries by their exact path in the image. If the policy allows `/usr/bin/python3.12`, the agent must run that binary: a symlink or a virtual environment elsewhere is a different path. In `ubi9/python-*` images, `python3` is a virtual environment under `/opt/app-root`, which the default filesystem policy does not allow; run `/usr/bin/python3.12` or add `/opt/app-root` to the policy.

Agents that open a terminal themselves (`tmux`, `screen`, Python `pty`, `os.openpty()`) need `/dev/ptmx` and `/dev/pts` under `read_write` in the policy; the default policy allows writing only `/tmp` and `/dev/null`, so opening a terminal fails with `Permission denied`. `openshell sandbox exec --tty` and `openshell sandbox connect` do not need this: OpenShell allocates that terminal outside the agent's policy.

## Build and run the example

The example in [`examples/byo-agent`](../examples/byo-agent/) is a Python agent that makes one HTTPS call the policy allows and one it does not.

| File | Purpose |
|---|---|
| `Containerfile` | `ubi9/ubi-minimal` plus Python 3.12 and the agent |
| `agent.py` | Calls `api.github.com` (allowed) and `example.com` (denied) |
| `policy.yaml` | Lets `/usr/bin/python3.12` read from `api.github.com`; nothing else leaves |
| `sitecustomize.py` | Python HTTPS workaround for RHCOS 9 until your gateway runs a build with [OpenShell #4150](https://github.com/NVIDIA/OpenShell/pull/4150) (after `v0.1.2-rhaiv.5`); delete it after that |

Build it on the cluster and run it in a sandbox:

```shell
make byo-agent      # OpenShift binary build into the internal registry, then a sandbox from that image
```

Or with your own registry:

```shell
podman build -t quay.io/<you>/byo-agent:latest examples/byo-agent
podman push quay.io/<you>/byo-agent:latest
openshell sandbox create --name byo-agent --from quay.io/<you>/byo-agent:latest \
  --policy examples/byo-agent/policy.yaml --no-tty
openshell sandbox exec --name byo-agent --no-tty -- /usr/bin/python3.12 /usr/local/bin/agent.py
```

Expected output:

```text
allowed  https://api.github.com/zen  -> HTTP 200
denied   https://example.com/        -> blocked (URLError)
```

Private registries need a pull secret the sandbox's service account can use.

## Reference images

| Image | What it is | Status |
|---|---|---|
| `registry.access.redhat.com/ubi9/ubi-minimal` | Recommended base for your own images | Available; used throughout these guides |
| `quay.io/opendatahub/odh-openshell-sandbox-openclaw` | OpenClaw as an OpenShell sandbox image, built by ODH Konflux on `ubi9/nodejs-24-minimal` | In progress ([opendatahub-io/openshell#80](https://github.com/opendatahub-io/openshell/pull/80)); only pull-request builds exist as of 2026-10-06 |
| `quay.io/aipcc/base-images/agentic/{openclaw,codex,goose,opencode,pi}` | Harness images built by AIPCC on Hummingbird | Not public |

Upstream, [NVIDIA/OpenShell#3967](https://github.com/NVIDIA/OpenShell/pull/3967) aligns the bring-your-own-container example (`examples/bring-your-own-container`) with the current runtime requirements listed above.
