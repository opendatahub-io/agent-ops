"""Build application images on OpenShift, with an explicit secret-free context.

Usage: python scripts/build_images.py
Requires an existing oc login. Does not install or alter OpenShell.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def oc(*args, data=None):
    return subprocess.run(
        ["oc", *args], input=data, text=True, check=True, capture_output=True
    ).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="red-compass")
    parser.add_argument("--skip-unchanged", action="store_true")
    parser.add_argument(
        "--components",
        nargs="+",
        default=["agent", "api", "web", "worker", "database", "mlflow"],
    )
    args = parser.parse_args()
    ns = args.namespace
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}}
    oc("apply", "-f", "-", data=json.dumps(namespace))
    for component in args.components:
        with tempfile.TemporaryDirectory(prefix="red-compass-build-") as temp:
            stage = Path(temp)
            # Copy only these approved inputs. Never upload .env, kubeconfig,
            # model keys, generated secrets, local databases or node_modules.
            if component in ["api", "agent", "worker"]:
                shutil.copytree(
                    ROOT / "backend" / "red_compass",
                    stage / "backend" / "red_compass",
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
                shutil.copy(
                    ROOT / "backend" / "pyproject.toml",
                    stage / "backend" / "pyproject.toml",
                )
            if component == "api":
                shutil.copytree(ROOT / "policies", stage / "policies")
            if component == "web":
                shutil.copytree(
                    ROOT / "frontend",
                    stage / "frontend",
                    ignore=shutil.ignore_patterns(
                        "node_modules", ".next", "*.tsbuildinfo"
                    ),
                )
            if component == "database":
                (stage / "deploy").mkdir()
                shutil.copy(
                    ROOT / "deploy" / "start-database.sh",
                    stage / "deploy" / "start-database.sh",
                )
            shutil.copy(
                ROOT / "deploy" / f"Containerfile.{component}", stage / "Dockerfile"
            )
            digest = hashlib.sha256()
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    digest.update(str(path.relative_to(stage)).encode())
                    digest.update(path.read_bytes())
            source_hash = digest.hexdigest()
            if args.skip_unchanged:
                try:
                    existing = json.loads(
                        oc("-n", ns, "get", "buildconfig", component, "-o", "json")
                    )
                    version = existing.get("status", {}).get("lastVersion", 0)
                    build = json.loads(
                        oc(
                            "-n",
                            ns,
                            "get",
                            "build",
                            f"{component}-{version}",
                            "-o",
                            "json",
                        )
                    )
                    oc(
                        "-n",
                        ns,
                        "get",
                        "imagestreamtag",
                        component + ":latest",
                        "-o",
                        "name",
                    )
                    if (
                        build.get("metadata", {})
                        .get("annotations", {})
                        .get("red-compass.io/source-hash")
                        == source_hash
                        and build.get("status", {}).get("phase") == "Complete"
                    ):
                        print(
                            f"{component}: verified build unchanged; reusing image",
                            flush=True,
                        )
                        continue
                except subprocess.CalledProcessError:
                    pass
            resources = {
                "apiVersion": "v1",
                "kind": "List",
                "items": [
                    {
                        "apiVersion": "image.openshift.io/v1",
                        "kind": "ImageStream",
                        "metadata": {"name": component, "namespace": ns},
                    },
                    {
                        "apiVersion": "build.openshift.io/v1",
                        "kind": "BuildConfig",
                        "metadata": {
                            "name": component,
                            "namespace": ns,
                        },
                        "spec": {
                            "source": {"type": "Binary", "binary": {}},
                            "strategy": {"type": "Docker", "dockerStrategy": {}},
                            "output": {
                                "to": {
                                    "kind": "ImageStreamTag",
                                    "name": component + ":latest",
                                }
                            },
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "1Gi"},
                                "limits": {"memory": "4Gi"},
                            },
                            "runPolicy": "Serial",
                        },
                    },
                ],
            }
            oc("apply", "-f", "-", data=json.dumps(resources))
            build_name = oc(
                "-n", ns, "start-build", component, "--from-dir=" + temp, "-o", "name"
            ).strip()
            # A missing annotation after an interruption causes a safe rebuild.
            # Reuse requires this exact build to have completed successfully.
            oc(
                "-n",
                ns,
                "annotate",
                build_name,
                "red-compass.io/source-hash=" + source_hash,
            )
            print(build_name + " started", flush=True)
    print("Builds started. Inspect with: oc -n " + ns + " get builds", flush=True)


if __name__ == "__main__":
    main()
