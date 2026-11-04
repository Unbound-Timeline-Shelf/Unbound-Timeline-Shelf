"""Developer control only constructs validated Docker invocations."""

from types import SimpleNamespace

import pytest

from src.devcontainer import control


def test_compose_arguments_revalidate_persisted_instance():
    instance = {**control.DEFAULT_INSTANCES[0], "project": "--malicious"}
    with pytest.raises(ValueError, match="project"):
        control.compose_args(instance, "status")


def test_docker_runner_fixes_the_executable(monkeypatch):
    seen = []

    def run(args, **_kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(control.subprocess, "run", run)
    assert control.run_docker_command(["info"])[0] == 0
    assert seen == [["docker", "info"]]


def test_compose_environment_rejects_invalid_persisted_values():
    instance = {**control.DEFAULT_INSTANCES[0], "tag": "--bad"}
    with pytest.raises(ValueError, match="tag"):
        control.compose_env(instance)


def test_compose_arguments_keep_expected_project_and_action():
    args = control.compose_args(control.DEFAULT_INSTANCES[0], "status")
    assert args[0] == "compose"
    assert args[args.index("--project-name") + 1] == "dev"
    assert args[-1] == "ps"
