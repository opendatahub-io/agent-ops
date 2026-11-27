"""Durable queue worker. OpenShell owns sandbox provisioning and isolation."""

import logging
import os
import time
import uuid
from datetime import timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select

from .auth import issue
from .config import settings
from .db import Event, Job, Session, Ticket, initialize, now

log = logging.getLogger("red_compass.worker")


def sdk():
    from openshell import SandboxClient, TlsConfig

    endpoint = os.getenv("OPENSHELL_GATEWAY_URL")
    if not endpoint:
        return SandboxClient.from_active_cluster()
    from pathlib import Path

    ca, cert, key = [
        os.getenv(n)
        for n in ["OPENSHELL_CA_PATH", "OPENSHELL_CERT_PATH", "OPENSHELL_KEY_PATH"]
    ]
    return SandboxClient(
        endpoint.removeprefix("https://"),
        tls=TlsConfig(
            ca_path=Path(ca) if ca else None,
            cert_path=Path(cert) if cert else None,
            key_path=Path(key) if key else None,
        ),
        bearer_token=os.getenv("OPENSHELL_BEARER_TOKEN"),
    )


def claim():
    cfg = settings()
    with Session.begin() as db:
        job = db.scalar(
            select(Job)
            .where(Job.state == "queued", Job.sandbox_id.is_(None))
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        ticket = db.get(Ticket, job.ticket_id)
        attempt = str(uuid.uuid4())
        ticket.active_attempt = attempt
        ticket.progress = "starting_review"
        job.state = "running"
        job.attempts += 1
        job.lease_until = now() + timedelta(seconds=cfg.agent_timeout_seconds + 180)
        # OpenShell's generated-name budget is 19 characters in this release.
        job.sandbox_id = f"rc-{attempt.replace('-', '')[:16]}"
        db.add(
            Event(
                ticket_id=ticket.id,
                attempt_id=attempt,
                kind="review_started",
                details={"attempt": job.attempts, "sandbox": job.sandbox_id},
            )
        )
        return ticket.id, attempt, ticket.policy_version, job.sandbox_id


def launch(client, ticket, attempt, corpus, name):
    from google.protobuf.json_format import ParseDict
    from openshell._proto import openshell_pb2, sandbox_pb2

    cfg = settings()
    api = cfg.api_base_url
    if not api:
        raise RuntimeError("API_BASE_URL must be reachable from the sandbox")
    image = os.environ.get("AGENT_IMAGE", "")
    if not image:
        raise RuntimeError("AGENT_IMAGE is required")
    env = {
        "TICKET_ID": ticket,
        "SANDBOX_NAME": name,
        "MLFLOW_TRACKING_URI": cfg.mlflow_tracking_uri,
        "MLFLOW_EXPERIMENT_NAME": cfg.mlflow_experiment_name,
        "MLFLOW_HTTP_REQUEST_TIMEOUT": "3",
        "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "1",
        "MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR": "1",
        "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING": "false",
        "MLFLOW_ENABLE_TELEMETRY": "false",
        "MLFLOW_DISABLE_AGENT_HINT": "1",
        "ATTEMPT_ID": attempt,
        "POLICY_VERSION": corpus,
        "API_BASE_URL": api,
        "AGENT_TOKEN": issue(ticket, "agent", 15, attempt_id=attempt),
        "ANTHROPIC_BASE_URL": cfg.anthropic_base_url,
        "ANTHROPIC_MODEL": cfg.anthropic_model,
        "ANTHROPIC_THINKING_MODE": cfg.anthropic_thinking_mode,
        "AGENT_MAX_TURNS": str(cfg.agent_max_turns),
        "AGENT_MAX_TOOLS_PER_TURN": str(cfg.agent_max_tools_per_turn),
        "AGENT_TIMEOUT_SECONDS": str(cfg.agent_timeout_seconds),
        "HOME": "/sandbox",
        "TMPDIR": "/tmp",
    }
    endpoints = []
    for kind in ["PARCELS", "HERITAGE", "ENVIRONMENT"]:
        if value := os.environ.get(f"{kind}_BASE_URL"):
            env[f"{kind}_BASE_URL"] = value
    for url in set(
        [
            api,
            *([cfg.mlflow_tracking_uri] if cfg.mlflow_tracking_uri else []),
            *[
                env[k]
                for k in env
                if k.endswith("_BASE_URL") and k != "ANTHROPIC_BASE_URL"
            ],
        ]
    ):
        parsed = urlparse(url)
        endpoints.append(
            {
                "host": parsed.hostname,
                "port": parsed.port or (443 if parsed.scheme == "https" else 80),
                "protocol": "rest",
                "access": 2,
                "enforcement": 1,
                "allowed_ips": [os.environ.get("SERVICE_CIDR", "172.30.0.0/16")],
            }
        )
    policy = ParseDict(
        {
            "version": 1,
            "filesystem": {
                "include_workdir": True,
                "read_only": [
                    "/usr",
                    "/bin",
                    "/lib",
                    "/lib64",
                    "/etc",
                    "/proc",
                    "/app",
                    "/opt",
                    "/dev/urandom",
                ],
                "read_write": ["/sandbox", "/tmp", "/dev/null"],
            },
            "landlock": {"compatibility": "hard_requirement"},
            "network_policies": {
                "business": {
                    "name": "business-api",
                    "endpoints": endpoints,
                    "binaries": [{"path": "/usr/bin/python3.12"}],
                }
            },
        },
        sandbox_pb2.SandboxPolicy(),
    )
    spec = openshell_pb2.SandboxSpec(
        template=openshell_pb2.SandboxTemplate(image=image),
        environment=env,
        command=["/opt/app-root/bin/python3.12", "-m", "red_compass.agent"],
        providers=[cfg.openshell_provider],
        policy=policy,
    )
    return client.create(
        workspace=cfg.openshell_workspace,
        name=name,
        spec=spec,
        labels={"app": "red-compass", "ticket": ticket, "attempt": attempt},
    )


def sandbox_failed(client, name, workspace):
    from openshell._proto import openshell_pb2

    try:
        return (
            client.get(name, workspace=workspace).phase
            == openshell_pb2.SANDBOX_PHASE_ERROR
        )
    except Exception:
        # Status lookup failures retain lease-based recovery, never a denial.
        log.warning("Sandbox status unavailable; retaining lease-based recovery")
        return False


def utc_deadline(value):
    # SQLite drops timezone information; its stored timestamps are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def reconcile(client):
    cfg = settings()
    with Session.begin() as db:
        for job in db.scalars(select(Job).where(Job.state == "running")):
            ticket = db.get(Ticket, job.ticket_id)
            expired = bool(job.lease_until and utc_deadline(job.lease_until) < now())
            failed = bool(
                job.sandbox_id
                and sandbox_failed(client, job.sandbox_id, cfg.openshell_workspace)
            )
            if ticket.status != "pending" or not (expired or failed):
                continue
            observed_sandbox = job.sandbox_id
            # Serialize recovery against decision submission, and refresh a decision
            # that may have committed while the gateway status request was in flight.
            db.refresh(ticket, with_for_update=True)
            db.refresh(job)
            if (
                ticket.status != "pending"
                or ticket.decision is not None
                or job.state != "running"
                or job.sandbox_id != observed_sandbox
            ):
                continue
            retry = job.attempts <= cfg.max_review_retries
            reason = "sandbox_error" if failed else "lease_expired"
            db.add(
                Event(
                    ticket_id=ticket.id,
                    attempt_id=ticket.active_attempt or "",
                    kind="review_failed",
                    details={
                        "reason": reason,
                        "will_retry": retry,
                        "sandbox": job.sandbox_id,
                    },
                )
            )
            ticket.active_attempt = None
            ticket.progress = "retrying" if retry else "interrupted"
            job.state = "queued" if retry else "failed"
            log.warning(
                "Review attempt failed ticket_id=%s reason=%s retry=%s",
                ticket.id,
                reason,
                retry,
            )
    # Commit token revocation before cleanup RPCs. Keep the old sandbox ID until
    # deletion succeeds so a gateway outage cannot lose the cleanup reference.
    with Session.begin() as db:
        jobs = db.scalars(
            select(Job).where(
                Job.state.in_(["queued", "done", "failed"]), Job.sandbox_id.is_not(None)
            )
        )
        for job in jobs:
            ticket = db.get(Ticket, job.ticket_id)
            if job.state != "queued":
                if not job.cleanup_at:
                    job.cleanup_at = now() + timedelta(
                        seconds=cfg.sandbox_retention_seconds
                    )
                    continue
                if utc_deadline(job.cleanup_at) >= now():
                    continue
            try:
                client.delete(
                    job.sandbox_id,
                    workspace=cfg.openshell_workspace,
                    allow_missing=True,
                )
            except Exception:
                log.warning(
                    "Sandbox cleanup unavailable for ticket %s; will retry", ticket.id
                )
                continue
            job.sandbox_id = None
            job.cleanup_at = None
            if job.state != "queued":
                ticket.active_attempt = None


def main():
    logging.basicConfig(level=logging.INFO)
    initialize()
    with sdk() as client:
        while True:
            try:
                reconcile(client)
                with Session() as db:
                    running = len(
                        db.scalars(select(Job).where(Job.state == "running")).all()
                    )
                if running < int(os.getenv("MAX_CONCURRENT_REVIEWS", "2")) and (
                    item := claim()
                ):
                    try:
                        launch(client, *item)
                        with Session.begin() as db:
                            ticket = db.get(Ticket, item[0])
                            if ticket.status == "pending":
                                ticket.progress = "reviewing"
                        log.info("Sandbox requested for ticket %s", item[0])
                    except Exception:
                        # Do not log SDK exception strings: they can contain launch environments.
                        log.error(
                            "Sandbox launch failed for ticket %s; lease reconciliation will recover it",
                            item[0],
                        )
                time.sleep(3)
            except Exception:
                log.error("Worker reconciliation failed; retrying")
                time.sleep(5)


if __name__ == "__main__":
    main()
