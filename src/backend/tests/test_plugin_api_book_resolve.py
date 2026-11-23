"""The batched identifier lookup a book import uses (C15 6.2, T-H3): the ``resolve`` operation,
``MetadataResolveRequest`` and ``ProviderResponse.resolved``.

They join the one Plugin API release, 1.1.20 (no new minor): a plugin that declares an older
contract may not register a ``resolve`` action, and a ``resolved`` answer from it is an
``invalid_response``. Synthetic books only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.features.metadata import providers as metadata_providers
from src.plugin_api.metadata_contracts import (
    MAX_RESOLVE_ITEMS,
    MediaType,
    MetadataProviderRegistration,
    MetadataResolveItem,
    MetadataResolveRequest,
    ProviderHealth,
    ProviderOperations,
    ProviderResponse,
    contract_context,
)

OLD = contract_context("1.1.19")
NEW = contract_context("1.1.20")
GLASS_13 = "9781111111113"
LANTERN_13 = "9782222222224"


def _request(**fields: Any) -> dict[str, Any]:
    return {
        "request_id": str(uuid4()),
        "user_id": str(uuid4()),
        "media_type": "book",
        "items": [{"key": "r0", "isbn_13": GLASS_13}, {"key": "r1", "title": "Salt and Ember"}],
        **fields,
    }


def _resolved(key: str = "r0", **candidate: Any) -> dict[str, Any]:
    fields = {
        "external_id": "atlas:01GLASS",
        "title": "The Glass Orchard",
        "provider": "official.metadata-atlas.atlas",
        "media_type": "book",
        "provider_ids": {"atlas": "atlas:01GLASS"},
        "isbn_13": GLASS_13,
        **candidate,
    }
    # a field given as None is left out, as a plugin that does not know it would
    return {
        "key": key,
        "matched_by": "identifier",
        "candidate": {name: value for name, value in fields.items() if value is not None},
    }


def test_resolve_request_is_a_unified_contract_construct():
    assert MetadataResolveRequest.model_validate(_request(), context=NEW).items[0].isbn_13 == (
        GLASS_13
    )
    with pytest.raises(ValidationError, match="require Plugin API 1.1.20"):
        MetadataResolveRequest.model_validate(_request(), context=OLD)


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"key": "r0"}], "needs an isbn_13 or a title"),
        ([{"key": "r0", "isbn_13": "9781111111112"}], "valid check digit"),
        ([{"key": "r0", "title": "A"}, {"key": "r0", "title": "B"}], "repeat a key"),
        ([{"key": "bad key", "title": "A"}], "pattern"),
        ([], "at least 1"),
    ],
)
def test_resolve_request_items_are_bounded(items, message):
    with pytest.raises(ValidationError, match=message):
        MetadataResolveRequest.model_validate(_request(items=items))


def test_resolve_request_holds_at_most_200_items():
    assert MAX_RESOLVE_ITEMS == 200
    items = [{"key": f"r{n}", "title": f"Book {n}"} for n in range(201)]
    with pytest.raises(ValidationError, match="at most 200"):
        MetadataResolveRequest.model_validate(_request(items=items))
    MetadataResolveRequest.model_validate(_request(items=items[:200]))


def test_an_isbn_is_only_an_identifier_for_a_book():
    with pytest.raises(ValidationError, match="only an identifier for a book"):
        MetadataResolveRequest.model_validate(_request(media_type="movie"))
    MetadataResolveRequest.model_validate(
        _request(media_type="movie", items=[{"key": "r0", "title": "A Film"}])
    )


def test_resolved_answers_are_gated_and_keyed_once():
    payload = {"resolved": [_resolved()]}
    assert ProviderResponse.model_validate(payload, context=NEW).resolved[0].key == "r0"
    # even a game answer: ``resolved`` itself is the 1.1.20 construct
    game = {"resolved": [_resolved(media_type="game", isbn_13=None)]}
    with pytest.raises(ValidationError, match="resolved require Plugin API 1.1.20"):
        ProviderResponse.model_validate(game, context=OLD)
    # an older plugin's ordinary answer is unaffected
    ProviderResponse.model_validate({"candidates": []}, context=OLD)
    with pytest.raises(ValidationError, match="repeats a key"):
        ProviderResponse.model_validate({"resolved": [_resolved(), _resolved()]}, context=NEW)


def _registration(**operations: Any) -> dict[str, Any]:
    return {
        "provider_id": "official.metadata-atlas.atlas",
        "name": "Atlas",
        "media_types": ["game"],
        "operations": {"search": "search", "health": "health", **operations},
    }


def test_only_a_unified_plugin_registers_a_resolve_action():
    declaration = MetadataProviderRegistration.model_validate(
        _registration(resolve="resolve"), context=NEW
    )
    assert declaration.operations.resolve == "resolve"
    with pytest.raises(ValidationError, match="operations resolve require Plugin API 1.1.20"):
        MetadataProviderRegistration.model_validate(_registration(resolve="resolve"), context=OLD)
    # a stored row is read without context and stays readable
    MetadataProviderRegistration.model_validate(_registration(resolve="resolve"))
    MetadataProviderRegistration.model_validate(_registration(), context=OLD)


# --- through the plugin adapter --------------------------------------------------------------


@dataclass
class _ActionRuntime:
    payload: dict[str, Any]
    calls: list[tuple[str, dict[str, Any]]]

    async def action(self, _plugin_id: str, action_id: str, values: dict, **_: Any) -> dict:
        self.calls.append((action_id, values))
        return self.payload


async def _resolve(
    monkeypatch, payload: dict[str, Any], *, state: dict[str, Any] | None = None, resolve="resolve"
) -> tuple[ProviderResponse, _ActionRuntime, list[str]]:
    user_id = uuid4()
    adapter = metadata_providers.PluginMetadataProvider(
        declaration=MetadataProviderRegistration(
            provider_id="official.metadata-atlas.atlas",
            name="Atlas",
            media_types=("book",),
            identifier_namespace="atlas",
            operations=ProviderOperations(search="search", health="health", resolve=resolve),
        ),
        plugin_id="official.metadata-atlas",
        installation_id=uuid4(),
        user_id=user_id,
        revision="r1",
        state=ProviderHealth.HEALTHY,
    )
    checked: list[str] = []

    async def authorized_state(_self, operation):
        checked.append(operation)
        return {"plugin_id": adapter.plugin_id, **(state or {"api_contract_version": "1.1.20"})}

    runtime = _ActionRuntime(payload, [])
    monkeypatch.setattr(
        metadata_providers.PluginMetadataProvider, "_authorized_state", authorized_state
    )
    monkeypatch.setattr(metadata_providers, "PluginRuntimeClient", lambda: runtime)
    request = MetadataResolveRequest(
        request_id=uuid4(),
        user_id=user_id,
        media_type=MediaType.BOOK,
        items=(
            MetadataResolveItem(key="r0", isbn_13=GLASS_13),
            MetadataResolveItem(key="r1", isbn_13=LANTERN_13),
        ),
    )
    return await adapter.resolve(request), runtime, checked


async def test_the_adapter_sends_one_action_for_the_whole_batch(monkeypatch):
    response, runtime, checked = await _resolve(monkeypatch, {"resolved": [_resolved()]})
    assert response.failure is None
    assert [resolution.key for resolution in response.resolved] == ["r0"]
    (call,) = runtime.calls
    assert call[0] == "resolve"
    assert [item["key"] for item in call[1]["request"]["items"]] == ["r0", "r1"]
    assert call[1]["provider_id"] == "official.metadata-atlas.atlas"
    # a resolve is authorised as a search: the same grant, the same account switch
    assert set(checked) == {"search"}


async def test_the_adapter_refuses_answers_to_rows_it_was_not_asked(monkeypatch):
    response, _, _ = await _resolve(monkeypatch, {"resolved": [_resolved("r9")]})
    assert response.failure is not None and response.failure.code == "invalid_response"


async def test_the_adapter_refuses_a_candidate_of_another_media_type(monkeypatch):
    response, _, _ = await _resolve(
        monkeypatch, {"resolved": [_resolved(media_type="movie", isbn_13=None)]}
    )
    assert response.failure is not None and response.failure.code == "invalid_response"


async def test_the_adapter_keeps_ids_to_the_providers_namespaces(monkeypatch):
    response, _, _ = await _resolve(
        monkeypatch,
        {"resolved": [_resolved(provider_ids={"atlas": "atlas:01GLASS", "steam": "620"})]},
    )
    assert response.resolved[0].candidate.provider_ids == {"atlas": "atlas:01GLASS"}


async def test_an_older_plugins_resolved_answer_is_invalid(monkeypatch):
    response, _, _ = await _resolve(
        monkeypatch, {"resolved": [_resolved()]}, state={"api_contract_version": "1.1.19"}
    )
    assert response.failure is not None and response.failure.code == "invalid_response"


async def test_a_provider_without_resolve_is_never_called(monkeypatch):
    response, runtime, _ = await _resolve(monkeypatch, {"resolved": []}, resolve=None)
    assert runtime.calls == []
    assert response.resolved == ()
