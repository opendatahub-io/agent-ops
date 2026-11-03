"""Installer regressions, using local files and mocked oc calls only."""

import importlib.util
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_images", ROOT / "scripts/build_images.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "build_hash,phase,reuse",
    [
        ("current", "Complete", True),
        ("old", "Complete", False),
        ("current", "Failed", False),
        (None, "Complete", False),
    ],
)
def test_reuse_requires_hash_on_successful_build(monkeypatch, build_hash, phase, reuse):
    builder = load_builder()
    calls = []
    # Use the real staged source hash without depending on its current value.
    monkeypatch.setattr(
        builder.hashlib,
        "sha256",
        lambda: SimpleNamespace(update=lambda _: None, hexdigest=lambda: "current"),
    )

    def oc(*args, data=None):
        calls.append(args)
        if "buildconfig" in args:
            return json.dumps(
                {
                    "metadata": {
                        "annotations": {"red-compass.io/source-hash": "current"}
                    },
                    "status": {"lastVersion": 1},
                }
            )
        if "build" in args and "get" in args:
            return json.dumps(
                {
                    "metadata": {
                        "annotations": {"red-compass.io/source-hash": build_hash}
                    },
                    "status": {"phase": phase},
                }
            )
        if "start-build" in args:
            return "build.build.openshift.io/database-2\n"
        return ""

    monkeypatch.setattr(builder, "oc", oc)
    monkeypatch.setattr(
        "sys.argv", ["build_images.py", "--skip-unchanged", "--components", "database"]
    )
    builder.main()
    assert any("start-build" in call for call in calls) == (not reuse)
    if not reuse:
        assert any(
            "annotate" in call and "build.build.openshift.io/database-2" in call
            for call in calls
        )


@pytest.mark.parametrize("existing", [False, True])
def test_env_is_private_before_secrets_are_written(tmp_path, monkeypatch, existing):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / ".env.example").write_text("SESSION_SECRET=\n")
    env = tmp_path / ".env"
    if existing:
        env.write_text("SESSION_SECRET=\n")
        env.chmod(0o644)
    fdopen = os.fdopen
    modes = []

    def checked_open(fd, *args, **kwargs):
        modes.append(os.fstat(fd).st_mode & 0o777)
        return fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", checked_open)
    code = (ROOT / "scripts/init_env.py").read_text()
    exec(
        compile(code, str(scripts / "init_env.py"), "exec"),
        {"__file__": str(scripts / "init_env.py")},
    )
    assert modes == [0o600]
    assert env.stat().st_mode & 0o777 == 0o600
    assert "SESSION_SECRET=\n" not in env.read_text()


@pytest.mark.parametrize("read_result", ["error", "absent", "exists", "creation_race"])
def test_cookie_secret_is_never_overwritten(monkeypatch, read_result):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "get" in args:
            assert "--ignore-not-found" in args
            return SimpleNamespace(
                returncode=int(read_result == "error"),
                stdout="secret/existing" if read_result == "exists" else "",
            )
        if "create" in args:
            return SimpleNamespace(returncode=int(read_result == "creation_race"))
        assert json.loads(kwargs["input"])["kind"] == "List"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr(
        "sys.argv", ["expose_dashboard.py", "--host", "dashboard.example.org"]
    )
    if read_result in {"error", "creation_race"}:
        with pytest.raises(RuntimeError):
            runpy.run_path(str(ROOT / "scripts/expose_dashboard.py"))
    else:
        runpy.run_path(str(ROOT / "scripts/expose_dashboard.py"))
    assert any("create" in call for call in calls) == (
        read_result in {"absent", "creation_race"}
    )
    if read_result == "error":
        assert len(calls) == 1
