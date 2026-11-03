"""Reuse the dashboard OAuth/Route pattern for the trace viewer."""

from copy import deepcopy

NAMESPACE = "red-compass"
INTERNAL_HOST = "mlflow-tracking.red-compass.svc.cluster.local"


def resources(dashboard_template, host, proxy_image):
    items = deepcopy(dashboard_template)
    for item in items:
        item["metadata"].update(name="mlflow", namespace=NAMESPACE)
        kind = item["kind"]
        if kind == "ServiceAccount":
            item["metadata"]["annotations"] = {
                "serviceaccounts.openshift.io/oauth-redirectreference.primary": '{"kind":"OAuthRedirectReference","apiVersion":"v1",'
                '"reference":{"kind":"Route","name":"mlflow"}}'
            }
        elif kind == "Deployment":
            spec = item["spec"]
            spec["strategy"] = {"type": "Recreate"}
            spec["selector"]["matchLabels"] = {"app": "mlflow"}
            spec["template"]["metadata"]["labels"] = {"app": "mlflow"}
            pod = spec["template"]["spec"]
            pod["serviceAccountName"] = "mlflow"
            backend, proxy = pod["containers"]
            backend.clear()
            backend.update(
                name="mlflow",
                image="image-registry.openshift-image-registry.svc:5000/red-compass/mlflow:latest",
                env=[
                    {
                        "name": "MLFLOW_SERVER_ALLOWED_HOSTS",
                        "value": f"{host},{INTERNAL_HOST}:5000,localhost:*,127.0.0.1:*",
                    },
                    {
                        "name": "MLFLOW_SERVER_CORS_ALLOWED_ORIGINS",
                        "value": f"https://{host}",
                    },
                ],
                ports=[{"name": "tracking", "containerPort": 5000}],
                volumeMounts=[{"name": "data", "mountPath": "/data"}],
                securityContext={
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                },
                resources={
                    "requests": {"cpu": "100m", "memory": "512Mi"},
                    "limits": {"memory": "2Gi"},
                },
                startupProbe={
                    "exec": {
                        "command": [
                            "python3.12",
                            "-c",
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)",
                        ]
                    },
                    "periodSeconds": 5,
                    "failureThreshold": 60,
                },
                readinessProbe={
                    "exec": {
                        "command": [
                            "python3.12",
                            "-c",
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)",
                        ]
                    },
                    "periodSeconds": 10,
                },
            )
            proxy["image"] = proxy_image
            replacements = {
                "--openshift-service-account=": "--openshift-service-account=mlflow",
                "--upstream=": "--upstream=http://127.0.0.1:5000",
                "--redirect-url=": f"--redirect-url=https://{host}/oauth/callback",
                "--openshift-sar=": '--openshift-sar={"namespace":"red-compass","resource":"configmaps","resourceName":"application","verb":"get"}',
            }
            proxy["args"] = [
                next(
                    (
                        new
                        for prefix, new in replacements.items()
                        if arg.startswith(prefix)
                    ),
                    arg,
                )
                for arg in proxy["args"]
            ]
            pod["volumes"] = [v for v in pod["volumes"] if v["name"] != "tls"]
            next(v for v in pod["volumes"] if v["name"] == "oauth-cookie")["secret"][
                "secretName"
            ] = "mlflow-oauth"
            pod["volumes"].append(
                {"name": "data", "persistentVolumeClaim": {"claimName": "mlflow"}}
            )
        elif kind == "Service":
            item["spec"]["selector"] = {"app": "mlflow"}
        elif kind == "NetworkPolicy":
            item["spec"]["podSelector"]["matchLabels"] = {"app": "mlflow"}
            item["spec"]["ingress"].append(
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": "openshell"
                                }
                            }
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 5000}],
                }
            )
        elif kind == "Route":
            item["spec"]["host"] = host
            item["spec"]["to"]["name"] = "mlflow"
    items.extend(
        [
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "mlflow", "namespace": NAMESPACE},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "5Gi"}},
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "mlflow-tracking", "namespace": NAMESPACE},
                "spec": {
                    "selector": {"app": "mlflow"},
                    "ports": [{"port": 5000, "targetPort": 5000}],
                },
            },
        ]
    )
    # Provision the claim before creating its consumer; avoid scheduler backoff.
    items.sort(key=lambda item: item["kind"] != "PersistentVolumeClaim")
    return items
