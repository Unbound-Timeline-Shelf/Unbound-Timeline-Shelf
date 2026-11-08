"""A plugin cannot make the runtime buffer one unbounded output line (#398)."""

import io
import json
import sys

import pytest

import runtime
from runtime import PluginSpec, PluginSupervisor, RuntimePolicyError, read_bounded_lines


def test_bounded_lines_pass_short_lines_and_skip_one_oversized_line():
    stream = io.BytesIO(b"ok\n" + b"x" * 50 + b"\n" + b"y" * 10 + b"\nlast")
    lines = list(read_bounded_lines(stream, 16))
    assert lines[0] == b"ok\n"
    assert lines[1] is runtime._OVERSIZED_LINE
    assert lines[2:] == [b"y" * 10 + b"\n", b"last"]


def test_a_line_exactly_at_the_limit_is_kept():
    assert list(read_bounded_lines(io.BytesIO(b"a" * 16 + b"\n"), 16)) == [b"a" * 16 + b"\n"]
    assert list(read_bounded_lines(io.BytesIO(b"a" * 16), 16)) == [b"a" * 16]
    assert [
        line is runtime._OVERSIZED_LINE for line in read_bounded_lines(io.BytesIO(b"a" * 17), 16)
    ] == [True]


@pytest.fixture
def chatty(tmp_path, monkeypatch):
    monkeypatch.setenv("NONBUBBLE_ENV", "true")
    monkeypatch.setattr(runtime, "_MAX_PROTOCOL_LINE_BYTES", 4096)
    supervisor = PluginSupervisor(tmp_path / "workers", tmp_path / "storage")
    package = tmp_path / "package"
    package.mkdir()
    statement = """
import json, sys
request = json.loads(sys.stdin.readline())
print('z' * request['noise'], file=sys.stderr, flush=True)
print('{"plugin_action_result": {"data": "' + 'x' * request['size'] + '"}}', flush=True)
"""
    return supervisor, package, PluginSpec("bounded.lines", (sys.executable, "-c", statement))


def test_an_oversized_result_line_is_refused_before_it_is_buffered(chatty):
    supervisor, package, spec = chatty
    payload = json.dumps({"size": 8192, "noise": 0}).encode()
    with pytest.raises(RuntimePolicyError, match="output line exceeds"):
        supervisor.execute(spec, package, payload)


def test_a_long_log_line_is_dropped_and_the_action_still_returns(chatty):
    supervisor, package, spec = chatty
    payload = json.dumps({"size": 10, "noise": 200 * 1024}).encode()
    assert json.loads(supervisor.execute(spec, package, payload)) == {"data": "x" * 10}
    messages = [entry["message"] for entry in supervisor.logs("bounded.lines")]
    assert "[a log line over 64 KiB was dropped]" in messages
