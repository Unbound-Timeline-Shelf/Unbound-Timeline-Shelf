"""The default-branch cache context must use the trusted companion ref."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    ("event", "host_ref", "requested", "allowed", "expected"),
    [
        ("workflow_dispatch", "refs/heads/main", "", True, "main"),
        ("workflow_dispatch", "refs/heads/main", "main", True, "main"),
        ("workflow_dispatch", "refs/heads/main", "feature", False, None),
        ("workflow_dispatch", "refs/heads/feature", "feature", True, "feature"),
        ("pull_request", "refs/pull/1/merge", "feature", True, "feature"),
    ],
)
def test_companion_ref_policy(tmp_path, event, host_ref, requested, allowed, expected):
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/plugin-integration.yml").read_text()
    )
    steps = workflow["jobs"]["lifecycle"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "companion")
    git = tmp_path / "git"
    git.write_text("#!/bin/sh\nexit 1\n")
    git.chmod(0o755)
    output = tmp_path / "output"
    output.touch()
    env = {
        **os.environ,
        "EVENT_NAME": event,
        "HOST_REF": host_ref,
        "REQUESTED_REF": requested,
        "COMPANION_BRANCH": "main",
        "GITHUB_OUTPUT": str(output),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    result = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, check=False)
    assert (result.returncode == 0) is allowed
    if allowed:
        assert output.read_text().strip() == f"ref={expected}"
