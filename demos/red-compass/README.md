# Red Compass

A fictional construction approvals demo running on OpenShift. A Next.js portal submits a proposal to FastAPI; a worker launches a Python reviewer in an OpenShell sandbox. The reviewer retrieves policies and parcel evidence, then approves or denies with citations. MLflow records its execution trace.

## Launch

Requires an amd64 OpenShift cluster, an administrator `oc` login, Python 3, `uv` and Helm. The cluster needs its image registry, default storage class and NetworkPolicy enforcement.

```sh
python3 scripts/init_env.py
# Configure ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL and ANTHROPIC_MODEL in .env.
./launch.sh
```

The launcher builds and deploys the complete seeded demo and prints URLs for the customer portal, actual OpenShell dashboard and MLflow viewer. Both operator viewers use OpenShift login. Alex and Sam are fictional customer identities. Helm installs the published OCI chart directly; pip installs the published Python SDK. No source checkout, protocol generation, vendored code or local Docker/Podman is needed.

Use an Anthropic Messages-compatible endpoint supporting tool calling and forced tool selection. For endpoints that support it, `ANTHROPIC_THINKING_MODE=disabled` requests shorter responses. The default leaves provider behavior unchanged. Never commit `.env` or put it in an image.

[Installation, operation and troubleshooting](docs/deployment.md) · [Architecture and design](docs/architecture.md) · [Diagram](docs/diagrams/red-compass-architecture.png)

## Follow the code

| Purpose | Entry point |
| --- | --- |
| Model loop and seven fixed tools | [agent.py](backend/red_compass/agent.py) |
| Queue, sandbox image, environment and policy | [worker.py](backend/red_compass/worker.py) |
| Customer API and ticket-scoped authorization | [api.py](backend/red_compass/api.py) |
| Policy search | [retrieval.py](backend/red_compass/retrieval.py) |
| Fictional rules and evidence | [policies/](policies/), [evidence.py](backend/red_compass/evidence.py) |
| Seed requests | [seed_data.json](backend/red_compass/seed_data.json) |
| Safe MLflow spans | [tracing.py](backend/red_compass/tracing.py) |
| Installation | [launch.py](scripts/launch.py), [deploy/](deploy/) |

The model has fixed HTTP tools, with no shell, arbitrary URL or code-execution tool. Reviews have at most 30 turns and stop after an accepted decision. The harness controls available actions, the API authorizes access to each request, and OpenShell constrains the executing process's filesystem and network access.

MLflow shows model/tool timings, token counts, policy/evidence identifiers and outcomes, correlated by ticket and sandbox. It excludes prompts, applicant text, credentials and private model reasoning. OpenShell supplies the lifecycle and enforcement view. Infrastructure failures remain pending/interrupted rather than becoming policy denials.

## Develop and verify

```sh
uv sync --project backend --extra tracing
npm ci --prefix frontend
backend/.venv/bin/pytest backend/tests -q
backend/.venv/bin/ruff check --config backend/pyproject.toml backend scripts
npm run build --prefix frontend
```

For local API work, use a separate development configuration: SQLite is supported when `DATABASE_URL` is empty. Start `backend/.venv/bin/uvicorn red_compass.api:app` and `npm run dev --prefix frontend`. Requests need an OpenShell worker to leave the queue. Cache `sentence-transformers/all-MiniLM-L6-v2` before retrieval; the OpenShift API image does this during its build.

Two integration checks serve different purposes:

```sh
# Deployed cluster: routes, seeded decisions and customer isolation; creates no requests.
.deploy-venv/bin/python scripts/verify_deployment.py
# Fast route/login checks, also useful with --no-seed installations.
.deploy-venv/bin/python scripts/verify_deployment.py --routes-only
# Local business integration: calls your LLM with an in-process API and temporary DB.
backend/.venv/bin/python scripts/smoke_review.py
```

The local smoke check consumes model tokens and needs cached embeddings; it does not exercise sandbox isolation. Full cluster verification compares model decisions with fixture expectations, so it can fail on an incorrect model verdict even when deployment is healthy.

The portal follows the [Red Hat UX reference](https://ux.redhat.com/patterns/alert/style/). Application images use UBI; upstream packages on UBI are not automatically supported Red Hat products. See the deployment guide for image provenance and storage details.

Installed dependencies (`.venv`, `.deploy-venv`, `node_modules`) and generated output (`.next`, caches) are ignored by Git and excluded from image build contexts. They are not part of the demo's source.
