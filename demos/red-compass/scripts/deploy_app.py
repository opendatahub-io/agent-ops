"""Deploy built app images and Secrets to the current OpenShift cluster.

Run with the backend venv. Requires completed builds and a configured gateway.
This deploys business services only; gateway/dashboard use the OpenShell installer.
"""

import json
import subprocess
from urllib.parse import quote

from red_compass.config import settings

NS = "red-compass"
REGISTRY = f"image-registry.openshift-image-registry.svc:5000/{NS}"


def apply(resource):
    # Never echo Secret manifests or include them in command-line arguments.
    result = subprocess.run(
        ["oc", "apply", "-f", "-"],
        input=json.dumps(resource),
        text=True,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"Failed to apply {resource['kind']}/{resource['metadata']['name']}"
        )


def main():
    cfg = settings()
    if not all([cfg.session_secret, cfg.agent_token_signing_secret]):
        raise RuntimeError("Generate internal secrets in .env before deployment")
    import os

    from dotenv import dotenv_values
    from red_compass.config import ROOT

    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    password = env.get("DATABASE_PASSWORD")
    if not password:
        raise RuntimeError("DATABASE_PASSWORD is required")
    secrets = {
        "DATABASE_PASSWORD": password,
        "DATABASE_URL": f"postgresql+psycopg://redcompass:{quote(password, safe='')}@database:5432/redcompass",
        "SESSION_SECRET": cfg.session_secret,
        "AGENT_TOKEN_SIGNING_SECRET": cfg.agent_token_signing_secret,
    }
    apply(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "application", "namespace": NS},
            "type": "Opaque",
            "stringData": secrets,
        }
    )
    config = {
        "POLICY_VERSION": cfg.policy_version,
        "API_BASE_URL": f"http://api.{NS}.svc.cluster.local:8000",
        "AGENT_IMAGE": f"{REGISTRY}/agent:latest",
        "OPENSHELL_PROVIDER": "red-compass-model",
        "OPENSHELL_WORKSPACE": cfg.openshell_workspace,
        "MLFLOW_TRACKING_URI": "http://mlflow-tracking.red-compass.svc.cluster.local:5000",
        "MLFLOW_EXPERIMENT_NAME": cfg.mlflow_experiment_name,
        "ANTHROPIC_MODEL": cfg.anthropic_model,
        "ANTHROPIC_THINKING_MODE": cfg.anthropic_thinking_mode,
        "ANTHROPIC_BASE_URL": cfg.anthropic_base_url,
        "AGENT_MAX_TURNS": "30",
        "AGENT_MAX_TOOLS_PER_TURN": str(cfg.agent_max_tools_per_turn),
        "AGENT_TIMEOUT_SECONDS": "300",
        "MAX_CONCURRENT_REVIEWS": "2",
        "MAX_REVIEW_RETRIES": "1",
        "SANDBOX_RETENTION_SECONDS": "600",
        "SERVICE_CIDR": env.get("SERVICE_CIDR", "172.30.0.0/16"),
    }
    for kind in ["PARCELS", "HERITAGE", "ENVIRONMENT"]:
        config[f"{kind}_BASE_URL"] = (
            f"http://{kind.lower()}.{NS}.svc.cluster.local:8000"
        )
    apply(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "application", "namespace": NS},
            "data": config,
        }
    )
    apply(
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "database", "namespace": NS},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "5Gi"}},
            },
        }
    )
    for name, port, image, memory in [
        ("database", 5432, "database", "1Gi"),
        ("api", 8000, "api", "3Gi"),
        ("web", 3000, "web", "512Mi"),
    ]:
        container = {
            "name": name,
            "image": f"{REGISTRY}/{image}:latest",
            "ports": [{"containerPort": port}],
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
            "resources": {
                "requests": {"cpu": "100m", "memory": memory},
                "limits": {"memory": memory},
            },
            "envFrom": [
                {"configMapRef": {"name": "application"}},
                {"secretRef": {"name": "application"}},
            ],
        }
        pod = {
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
        }
        if name == "database":
            container["command"] = [
                "/bin/bash",
                "-c",
                'mkdir -p "$PGDATA" && chmod 700 "$PGDATA" && exec /usr/local/bin/start-database',
            ]
            pod["volumes"] = [
                {"name": "data", "persistentVolumeClaim": {"claimName": "database"}}
            ]
            container["volumeMounts"] = [
                {"name": "data", "mountPath": "/var/lib/pgsql/data"}
            ]
            container["readinessProbe"] = {
                "exec": {
                    "command": ["pg_isready", "-U", "redcompass", "-d", "postgres"]
                },
                "initialDelaySeconds": 10,
                "periodSeconds": 5,
            }
        elif name == "api":
            container["startupProbe"] = {
                "httpGet": {"path": "/healthz", "port": port},
                "failureThreshold": 60,
                "periodSeconds": 5,
            }
            container["readinessProbe"] = {
                "httpGet": {"path": "/healthz", "port": port}
            }
        else:
            container.pop("envFrom")
            container["readinessProbe"] = {
                "httpGet": {"path": "/", "port": port},
                "initialDelaySeconds": 5,
            }
        apply(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": name, "namespace": NS},
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"}
                    if name == "database"
                    else {"type": "RollingUpdate"},
                    "selector": {"matchLabels": {"app": name}},
                    "template": {"metadata": {"labels": {"app": name}}, "spec": pod},
                },
            }
        )
        apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name, "namespace": NS},
                "spec": {
                    "selector": {"app": name},
                    "ports": [{"port": port, "targetPort": port}],
                },
            }
        )
    for name in ["parcels", "heritage", "environment"]:
        apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name, "namespace": NS},
                "spec": {
                    "selector": {"app": "api"},
                    "ports": [{"port": 8000, "targetPort": 8000}],
                },
            }
        )
    apply(
        {
            "apiVersion": "route.openshift.io/v1",
            "kind": "Route",
            "metadata": {"name": "red-compass", "namespace": NS},
            "spec": {
                "to": {"kind": "Service", "name": "web"},
                "port": {"targetPort": 3000},
                "tls": {
                    "termination": "edge",
                    "insecureEdgeTerminationPolicy": "Redirect",
                },
            },
        }
    )
    # Restrict database ingress; sandbox boundary policy is managed by OpenShell.
    apply(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "database", "namespace": NS},
            "spec": {
                "podSelector": {"matchLabels": {"app": "database"}},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {"podSelector": {"matchLabels": {"app": "api"}}},
                            {"podSelector": {"matchLabels": {"app": "worker"}}},
                            {"podSelector": {"matchLabels": {"app": "ingestion"}}},
                        ],
                        "ports": [{"protocol": "TCP", "port": 5432}],
                    }
                ],
            },
        }
    )
    print(
        "Application resources applied. Wait for database and API, then run the policy ingestion Job. Worker deployment follows gateway credential setup."
    )


if __name__ == "__main__":
    main()
