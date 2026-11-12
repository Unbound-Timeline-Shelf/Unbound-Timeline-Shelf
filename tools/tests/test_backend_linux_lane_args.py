"""The Linux lane hands pytest arguments to the container with their boundaries intact (#486).

The wrapper used to join the arguments into one environment string that the container split on
whitespace again, so `-k 'trash and restore'` reached pytest as three arguments. The wrapper now
writes them NUL-separated to the output folder and the entrypoint reads them back unchanged.
These tests run the real wrapper with a stand-in `docker` and the entrypoint's own reader.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "tools" / "backend_linux_lane.sh"
ENTRYPOINT = ROOT / "tools" / "backend-linux-lane" / "entrypoint.sh"
BASH = shutil.which("bash")

FAKE_DOCKER = """#!/usr/bin/env bash
# Stand-in for `docker compose`: record what `run` would hand to the container.
if [ "${3:-}" = "run" ] || [ "${4:-}" = "run" ]; then
  cp "$LANE_OUT/lane-pytest-args" "$CAPTURE" 2>/dev/null || : > "$CAPTURE.missing"
fi
exit 0
"""

ARGS = ["-k", "trash and restore", "tests/a file with spaces.py", "-x", "--tb=line"]


@unittest.skipIf(BASH is None or sys.platform == "win32", "needs a POSIX bash")
class LaneArgumentHandoff(unittest.TestCase):
    def test_the_wrapper_keeps_every_argument_boundary(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(FAKE_DOCKER)
            docker.chmod(0o755)
            capture = tmp_path / "captured"
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "LANE_OUT": str(tmp_path / "out"),
                "CAPTURE": str(capture),
            }
            subprocess.run([BASH, str(WRAPPER), *ARGS], env=env, check=True, capture_output=True)

            self.assertTrue(capture.exists(), "the wrapper wrote no argument file")
            self.assertEqual(capture.read_bytes().split(b"\0")[:-1], [a.encode() for a in ARGS])

    def test_the_entrypoint_reads_the_arguments_back_unchanged(self):
        with TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "lane-pytest-args"
            handoff.write_bytes(b"".join(a.encode() + b"\0" for a in ARGS))
            reader = _entrypoint_reader()
            script = f'{reader}\nread_pytest_args "$1"\nprintf "%s\n" "${{#extra[@]}}"\nprintf "<%s>\n" "${{extra[@]}}"'
            out = subprocess.run(
                [BASH, "-c", script, "lane", str(handoff)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(out, [str(len(ARGS)), *(f"<{a}>" for a in ARGS)])

    def test_no_argument_file_means_no_extra_arguments(self):
        with TemporaryDirectory() as tmp:
            script = f'{_entrypoint_reader()}\nread_pytest_args "$1"\nprintf "%s" "${{#extra[@]}}"'
            out = subprocess.run(
                [BASH, "-c", script, "lane", str(Path(tmp) / "absent")],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(out, "0")


def _entrypoint_reader() -> str:
    """The entrypoint's `read_pytest_args` function, exactly as the container runs it."""
    lines = ENTRYPOINT.read_text().splitlines()
    start = lines.index("read_pytest_args() {")
    end = lines.index("}", start)
    return "\n".join(lines[start : end + 1])


if __name__ == "__main__":
    unittest.main()
