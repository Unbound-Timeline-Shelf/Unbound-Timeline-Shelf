"""#397: startup and shutdown run through a lifespan handler instead of deprecated on_event."""

import warnings

import pytest

from src import main


def test_no_deprecated_event_handlers_remain():
    assert main.app.router.on_startup == []
    assert main.app.router.on_shutdown == []
    names = [handler.__name__ for handler in main.STARTUP_HANDLERS]
    # Same order as before the conversion: the primary user and provider registrations come
    # first, then the background loops.
    assert names[0] == "bootstrap_primary_user"
    assert names == [
        "bootstrap_primary_user",
        "start_image_prefetch",
        "start_provider_health_loop",
        "start_session_retention_loop",
        "start_jobs_loop",
        "start_direct_metrics_listener",
        "start_cache_inventory_loop",
        "start_notification_backlog_loop",
    ]
    assert [handler.__name__ for handler in main.SHUTDOWN_HANDLERS] == [
        "close_auth_limit_connections",
        "stop_image_prefetch",
        "stop_provider_health_loop",
        "stop_direct_metrics_listener",
        "stop_cache_inventory_loop",
        "stop_notification_backlog_loop",
        "close_plugin_runtime_transport",
    ]


async def _run(monkeypatch, startup, shutdown, *, fail_at=None, calls=None):
    calls = [] if calls is None else calls

    def recorder(name):
        async def handler():
            calls.append(name)
            if name == fail_at:
                raise RuntimeError(name)

        return handler

    monkeypatch.setattr(main, "STARTUP_HANDLERS", [recorder(name) for name in startup])
    monkeypatch.setattr(main, "SHUTDOWN_HANDLERS", [recorder(name) for name in shutdown])
    async with main.app.router.lifespan_context(main.app):
        calls.append("serving")
    return calls


async def test_lifespan_runs_startup_then_shutdown_in_registration_order(monkeypatch):
    calls = await _run(monkeypatch, ["a", "b"], ["x", "y"])
    assert calls == ["a", "b", "serving", "x", "y"]


async def test_a_startup_failure_stops_before_serving_or_shutdown(monkeypatch):
    calls = []
    with pytest.raises(RuntimeError):
        await _run(monkeypatch, ["a", "b", "c"], ["x"], fail_at="b", calls=calls)
    assert calls == ["a", "b"]


async def test_the_lifespan_raises_no_deprecation_warning(monkeypatch):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await _run(monkeypatch, ["a"], ["x"])
