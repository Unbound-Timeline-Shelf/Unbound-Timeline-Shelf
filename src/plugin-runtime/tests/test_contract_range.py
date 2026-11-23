"""The plugin runtime's half of api_contract_max (C16 22.6, W-6).

A plugin may declare the newest contract it supports. The runtime accepts the range only from
1.1.20 on, runs the plugin at min(api_contract_max, host maximum), reports both in the
inventory and tells the worker in its environment. No plugin code is executed here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from runtime import (
    PLUGIN_API_CONTRACT_VERSION,
    PluginRegistry,
    PluginSupervisor,
    contract_environment,
    negotiated_contract,
    plugin_contract_compatibility_reason,
)


def _manifest(version: str, maximum: object = None) -> dict:
    manifest = {"plugin_id": "example.plugin", "api_contract_version": version}
    if maximum is not None:
        manifest["api_contract_max"] = maximum
    return manifest


@pytest.mark.parametrize(
    "maximum",
    [PLUGIN_API_CONTRACT_VERSION, "1.1.99", "1.9.0"],
)
def test_range_from_1_1_20_is_accepted_and_capped_at_the_host(maximum: str) -> None:
    manifest = _manifest("1.1.20", maximum)
    assert plugin_contract_compatibility_reason(manifest) is None
    assert negotiated_contract(manifest) == PLUGIN_API_CONTRACT_VERSION


def test_without_a_maximum_the_declared_contract_is_used() -> None:
    manifest = _manifest("1.1.0")
    assert plugin_contract_compatibility_reason(manifest) is None
    assert negotiated_contract(manifest) == "1.1.0"


@pytest.mark.parametrize(
    ("version", "maximum", "message"),
    [
        # the field itself is only understood from 1.1.20 on
        ("1.1.19", "1.1.30", "range 1.1.19-1.1.30 is invalid"),
        # a maximum below the minimum, or in another major, is no range
        ("1.1.20", "1.1.10", "range 1.1.20-1.1.10 is invalid"),
        ("1.1.20", "2.0.0", "range 1.1.20-2.0.0 is invalid"),
        ("1.1.20", "1.1", "maximum is invalid"),
        ("1.1.20", 120, "maximum is invalid"),
    ],
)
def test_invalid_ranges_are_refused(version: str, maximum: object, message: str) -> None:
    reason = plugin_contract_compatibility_reason(_manifest(version, maximum))
    assert reason is not None and message in reason


def test_a_minimum_above_the_host_is_refused_whatever_the_maximum() -> None:
    reason = plugin_contract_compatibility_reason(_manifest("1.1.99", "1.2.0"))
    assert reason is not None and "not supported by this host" in reason


def test_worker_environment_names_the_negotiated_contract_and_host_range() -> None:
    assert contract_environment(_manifest("1.1.20", "1.4.0")) == {
        "PLUGIN_API_CONTRACT": PLUGIN_API_CONTRACT_VERSION,
        "PLUGIN_API_HOST_MIN": "1.1.0",
        "PLUGIN_API_HOST_MAX": PLUGIN_API_CONTRACT_VERSION,
    }
    assert contract_environment(_manifest("1.0.0"))["PLUGIN_API_HOST_MIN"] == "1.0.0"
    assert contract_environment(_manifest("1.1.3"))["PLUGIN_API_CONTRACT"] == "1.1.3"


def _package(root: Path, manifest_extra: dict) -> Path:
    package = root / "example.plugin"
    package.mkdir(parents=True)
    (package / "main.py").write_text("def main():\n    return None\n", encoding="utf-8")
    (package / "ui.json").write_text(
        json.dumps(
            {
                "api_contract_version": manifest_extra["api_contract_version"],
                "plugin_id": "example.plugin",
                "settings": [],
                "actions": [{"id": "ping", "handler": "main:action"}],
            }
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256()
    for path in sorted(p for p in package.rglob("*") if p.is_file()):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "plugin_id": "example.plugin",
                "name": "Example",
                "version": "1.0.0",
                "entrypoint": "main:main",
                "integrity": {"sha256": digest.hexdigest()},
                **manifest_extra,
            }
        ),
        encoding="utf-8",
    )
    return package


def test_inventory_reports_the_range_and_the_negotiated_contract(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    _package(root, {"api_contract_version": "1.1.20", "api_contract_max": "1.3.0"})
    item = PluginRegistry(root, PluginSupervisor(root=tmp_path / "processes")).list()[0]
    assert item["compatible"] is True
    assert item["api_contract_max"] == "1.3.0"
    assert item["negotiated_api_contract"] == PLUGIN_API_CONTRACT_VERSION
    assert item["plugin_api"] == {"min": "1.1.0", "max": PLUGIN_API_CONTRACT_VERSION}


def test_inventory_negotiates_nothing_for_a_refused_range(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    _package(root, {"api_contract_version": "1.1.20", "api_contract_max": "1.1.2"})
    item = PluginRegistry(root, PluginSupervisor(root=tmp_path / "processes")).list()[0]
    assert item["compatible"] is False
    assert item["negotiated_api_contract"] is None


def test_actions_run_with_the_contract_environment(
    tmp_path: Path, monkeypatch, activate_registry
) -> None:
    root = tmp_path / "plugins"
    _package(root, {"api_contract_version": "1.1.20", "api_contract_max": "1.2.0"})
    registry = PluginRegistry(root, PluginSupervisor(root=tmp_path / "processes"))
    registry._save_state({"example.plugin": {"enabled": True}})
    specs = []
    monkeypatch.setattr(
        registry.supervisor,
        "execute",
        lambda spec, package, payload, **kwargs: specs.append(spec),
    )
    activate_registry(registry, "example.plugin")
    registry.action("example.plugin", "ping", {})
    assert specs[0].environment["PLUGIN_API_CONTRACT"] == PLUGIN_API_CONTRACT_VERSION
    assert specs[0].environment["PLUGIN_API_HOST_MAX"] == PLUGIN_API_CONTRACT_VERSION
    specs[0].validate()  # the names are not reserved worker variables
