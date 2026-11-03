"""Configure the demo's model provider via the OpenShell Python SDK protocol interfaces.

Requires localhost port-forward 18080:8080. Secret contents are never printed.
"""

import base64
import json
import os
import subprocess
import grpc
from google.protobuf.json_format import ParseDict
from openshell._proto import openshell_pb2 as pb, openshell_pb2_grpc, datamodel_pb2
from red_compass.config import settings
from red_compass.provider import model_provider_profile


def main():
    secret = json.loads(
        subprocess.check_output(
            [
                "oc",
                "-n",
                "openshell",
                "get",
                "secret",
                "openshell-client-tls",
                "-o",
                "json",
            ]
        )
    )
    material = {k: base64.b64decode(v) for k, v in secret["data"].items()}
    credentials = grpc.ssl_channel_credentials(
        material["ca.crt"], material["tls.key"], material["tls.crt"]
    )
    with grpc.secure_channel(
        os.environ.get("GATEWAY_SETUP_ADDRESS", "127.0.0.1:18080"),
        credentials,
        options=[
            ("grpc.ssl_target_name_override", "openshell.openshell.svc.cluster.local")
        ],
    ) as channel:
        stub = openshell_pb2_grpc.OpenShellStub(channel)
        stub.Health(pb.HealthRequest(), timeout=15)
        scope = datamodel_pb2.WorkspaceSelector(
            workspace=settings().openshell_workspace
        )
        cfg = settings()
        profile = ParseDict(model_provider_profile(cfg), pb.ProviderProfile())
        stub.ImportProviderProfiles(
            pb.ImportProviderProfilesRequest(
                workspace_scope=scope,
                profiles=[
                    pb.ProviderProfileImportItem(
                        profile=profile, source="red-compass-demo"
                    )
                ],
            ),
            timeout=15,
        )
        provider = datamodel_pb2.Provider(
            metadata=datamodel_pb2.ObjectMeta(
                name=cfg.openshell_provider, workspace=cfg.openshell_workspace
            ),
            type=cfg.openshell_provider,
            profile_workspace=cfg.openshell_workspace,
            credentials={"ANTHROPIC_API_KEY": cfg.anthropic_api_key},
        )
        try:
            stub.CreateProvider(
                pb.CreateProviderRequest(workspace_scope=scope, provider=provider),
                timeout=15,
            )
        except grpc.RpcError as exc:
            if exc.code() != grpc.StatusCode.ALREADY_EXISTS:
                raise RuntimeError(f"Provider creation failed: {exc.code()}") from None
        print("Gateway health and scoped model provider configured")
    # Copy only the operator TLS secret to the worker namespace. No kube token
    # or model key is delivered to the business API or frontend.
    resource = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "openshell-client-tls", "namespace": "red-compass"},
        "type": "Opaque",
        "data": secret["data"],
    }
    result = subprocess.run(
        ["oc", "apply", "-f", "-"],
        input=json.dumps(resource),
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("Could not provision worker TLS credentials")
    print("Worker TLS credentials provisioned")


if __name__ == "__main__":
    main()
