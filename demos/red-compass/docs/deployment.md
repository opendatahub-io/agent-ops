# Install and operate Red Compass

## Requirements

Use an amd64 OpenShift cluster (latest fresh-install validation: 4.22.9), an administrator `oc` login, Python 3, `uv` and Helm. The cluster needs an enabled image registry, a default storage class, enforced NetworkPolicy, Red Hat operator catalog/pull credentials and network access for dependencies, images, CPU embeddings and your model endpoint.  No Docker, Podman or persistent port-forward is required.

## OpenShell packages and images

The installer calls Helm directly against `oci://ghcr.io/nvidia/openshell/helm-chart` and installs the published `openshell` Python package with pip. Versions are recorded in [openshell-release.json](../deploy/openshell-release.json): chart and SDK 0.1.7. No source checkout or protocol generation is required. The OpenShell CLI is useful for manual administration; the installer uses Helm and the SDK.

OpenShell images are explicitly overridden in [openshell-values.yaml](../deploy/openshell-values.yaml) and [dashboard.yaml](../deploy/dashboard.yaml):

| Component | Image |
| --- | --- |
| Gateway | `quay.io/opendatahub/odh-openshell-gateway:v0.1.6-rhaiv.3` |
| Supervisor | `quay.io/opendatahub/odh-openshell-supervisor:v0.1.6-rhaiv.3` |
| Sandbox runtime | `quay.io/opendatahub/odh-openshell-sandbox:v0.1.6-rhaiv.3` |
| Dashboard | `quay.io/opendatahub/odh-openshell-dashboard:9a68df0ee2ced156b8c7948a55e6247c35ef38d8` |

The equivalent OpenShell deployment command is:

```sh
helm upgrade --install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.7 --namespace openshell --create-namespace \
  -f deploy/openshell-values.yaml
```

`./launch.sh` also handles operator readiness, model-provider configuration, application deployment and authenticated dashboard/MLflow Routes. The SDK comes from the package index in both the worker image and deployment environment.

## Install

```sh
python3 scripts/init_env.py
# Edit .env: ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL
./launch.sh
```

The endpoint must support Anthropic Messages, tool calling and forced tool selection. For endpoints supporting it, `ANTHROPIC_THINKING_MODE=disabled` requests shorter responses without extended thinking. The default `default` omits this option. Keep `.env` private; it is excluded from source builds. Initialization generates missing application secrets and preserves existing configuration.

The launcher uses your current `oc` context and fixed namespaces `red-compass`, `openshell` and `agent-sandbox-system`. It:

1. Checks tools and release configuration, then prepares `.deploy-venv`.
2. Reuses the Agent Sandbox controller or installs Red Hat Agent Sandbox Operator v0.9.0 through OLM. It approves only an install plan containing that pinned version and stops if unavailable.
3. Builds six UBI-based images on OpenShift with source allowlists, reusing successful builds with unchanged inputs.
4. Deploys PostgreSQL/pgvector, creates its UTF-8 database if absent, and deploys the API and portal.
5. Installs the published OpenShell Helm chart, configures the model provider and worker TLS credentials, and grants workload image-pull access. Its temporary setup port-forward closes automatically.
6. Indexes the policy corpus and queues six requests across Alex and Sam. Stable seed IDs prevent duplicates and preserve existing decisions. Bootstrap Jobs expire after one day.
7. Deploys MLflow and waits for readiness **before starting the review worker**. Seeding itself performs no reviews or model calls.
8. Publishes the actual OpenShell dashboard behind OpenShift OAuth and prints all three Routes.

MLflow's PVC is created before its Deployment. The initial pod is not immediately restarted, and its memory limit is 2 GiB. Other cluster applications are not reset.

```sh
./launch.sh --check     # Read-only preflight, no installation or local setup
./launch.sh --no-seed   # Install policies and services without initial requests
./launch.sh --rebuild   # Rebuild images regardless of source hashes
```

Rerun `./launch.sh` after an interruption or source change. It preserves database data, application credentials and model-provider credentials; it does not rotate the existing model key. Successful completed requests are not replayed and do not acquire traces retrospectively. Submit a new request after upgrading instrumentation.

## Verify

```sh
.deploy-venv/bin/python scripts/verify_deployment.py --routes-only
.deploy-venv/bin/python scripts/verify_deployment.py
```

The first command checks portal HTTPS and login protection on both operator viewers. The full check also waits for the six seeds, compares their decisions with fixture expectations and checks cross-customer isolation. It creates no additional requests. Model verdicts are nondeterministic; a wrong verdict can fail this evaluation even when deployment is healthy. The missing-evidence case has been incorrectly approved in previous runs.

## Present and inspect

```sh
oc -n red-compass get routes
oc -n openshell get route openshell-dashboard
```

Use Alex or Sam in the customer portal, submit a proposal and watch its review sandbox in the OpenShell dashboard. The customer login is intentionally fictional demo authentication. The actual dashboard and MLflow viewer use OpenShift OAuth:

| Viewer | Required operator permission |
| --- | --- |
| OpenShell dashboard | Get Secret `openshell-client-tls` in `openshell` |
| MLflow | Get ConfigMap `application` in `red-compass` |

These are shared operator views, not per-customer authorization. OAuth proxy images come from the cluster release. Callback hosts are derived from the ingress domain; cookie secrets are generated and retained. Only the OAuth proxy containers mount their projected service-account tokens. The dashboard backend listens on loopback behind its proxy.

In MLflow, select `red-compass-reviews` and open Traces. Filter by `ticket_id`, `attempt_id` or `sandbox`. Each normally completed attempt records model/tool durations, token counts, policy/evidence IDs and outcome. Prompts, applicant narratives, search queries, raw HTTP bodies, credentials and model reasoning are omitted. Export is best effort; abrupt termination can lose a trace. API audit events remain independent.

The public MLflow Service targets OAuth. Its separate internal tracking Service exposes port 5000 only to the `openshell` namespace through NetworkPolicy; OpenShell additionally restricts each sandbox's network destinations and calling executable. This internal demo ingestion path uses namespace isolation, not per-ticket MLflow authorization. The server receives no model key or application Secret.

Avoid dumping sandbox specifications: they can contain scoped credentials. Model credentials stay in the gateway; the agent receives a provider placeholder and a short-lived ticket token. It receives no database credentials or Kubernetes token.

## Failure handling and retry

Reviews have a 30-turn limit and a five-minute deadline, with a 90-second model-call timeout. The model SDK does not retry internally; the worker owns the configured review retry. Sandbox error states trigger recovery immediately; status lookup failures retain lease-based recovery. Infrastructure failures leave requests pending/interrupted rather than issuing a construction denial.

Old attempts are revoked before cleanup. Cleanup references are preserved across gateway failures, and a retry cannot launch until the prior sandbox reference is cleared. Completed and exhausted failed sandboxes are retained for the configured inspection period (ten minutes by default). Decisions and traces outlive sandbox cleanup. Iteration-limit fallback denials retain their origin in audit events and an API warning; customers see the insufficient-evidence explanation.

After correcting an operational cause, retry an interrupted request without changing its proposal or past attempts:

```sh
oc -n red-compass exec -i deployment/worker -- python3.12 - --ticket-id TICKET_ID < scripts/requeue_review.py
```

This accepts only an interrupted request without a decision, records an operator retry and preserves historical attempt counts. It cannot reset completed decisions.

## Storage and reproducibility

PostgreSQL and MLflow each use a separate 5 GiB PVC. MLflow uses SQLite plus its artifact directory, one replica and a Recreate strategy. Deleting `red-compass` removes both stores. A fresh install recreates functionality and seed requests, not historical responses, browser sessions or running sandboxes.

Back up business data before removing a deployment:

```sh
umask 077
mkdir -p backups
oc -n red-compass exec deployment/database -- pg_dump -U redcompass -d redcompass -Fc > backups/red-compass.dump
```

Restore into the newly created database before starting the worker. Restore is a separate migration procedure; the launcher does not overwrite existing data. MLflow history needs its own PVC backup. A database dump is not a checkpoint of active sandboxes.

The chart and SDK versions are recorded in `deploy/openshell-release.json`; review their compatibility when updating the supplied ODH images. Some package ranges and base images resolve during builds: mirror verified image digests for binary reproducibility. The dashboard uses the supplied commit tag.

Application images and upstream Python packages on UBI are not automatically supported Red Hat products. All four OpenShell runtime image references above use the supplied ODH builds. To validate the published chart without deployment, run `OPENSHELL_HELM_TEST=1 backend/.venv/bin/pytest backend/tests/test_mlflow_deployment.py -q`.

Latest end-to-end validation (2026-11-09, OpenShift 4.22.9): published Helm chart/SDK 0.1.7 with the supplied ODH runtime images and dashboard tag. All six seed reviews completed with expected decisions, all six MLflow traces were complete and OK, customer isolation passed, and the dashboard reported gateway compatibility as supported. No manual deployment fixes were required.
