"""Plugin API 1.1.20 (CONTRACTS-v1 C2.6, C4.4, C5.3, C7): the new fields, their validation, and
the version gate that keeps them away from plugins built for an older host.

A plugin is held to the contract its manifest declares. A 1.1.19 plugin that sends a 1.1.20
field or value gets ``invalid_response`` (as an unknown field always did), and a 1.1.19 plugin
that sends none of them is answered exactly as before."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.database.models.game_identity import RelationshipKind
from src.features.metadata import providers as metadata_providers
from src.features.metadata.namespaces import confine_to_namespaces
from src.plugin_api import PLUGIN_API_CONTRACT_VERSION
from src.plugin_api.metadata_contracts import (
    MetadataAchievement,
    MetadataPatch,
    MetadataProviderRegistration,
    MetadataProviderRequest,
    MetadataRelease,
    ProviderHealth,
    ProviderOperations,
    ProviderResponse,
    contract_context,
)
from src.plugin_api.vocab import (
    ACHIEVEMENT_KINDS,
    ACHIEVEMENT_TIERS,
    CROSSPLAY,
    DATE_PRECISIONS,
    RELATION_TYPES,
    RELEASE_CATEGORIES,
    export,
)

OLD = contract_context("1.1.19")
NEW = contract_context("1.1.20")

LEGACY_PAYLOAD = {
    "metadata": {
        "title": "Portal 2",
        "release_date": "2011-04-19",
        "provider_ids": {"atlas": "atlas:01JPORTAL2"},
        "crossplay": "partial",
        "achievements": [{"external_id": "ak1:one", "name": "One", "tier": "gold"}],
    }
}

NEW_FIELDS: dict[str, dict[str, Any]] = {
    "first_release": {"first_release": {"date": "2011", "precision": "year"}},
    "releases": {"releases": [{"date": "2011-04", "precision": "month", "platform": "ps3"}]},
    "identity": {"identity": {"match": "redirect", "previous_ids": {"atlas": "atlas:OLD"}}},
    "platinum": {"achievements": [{"external_id": "p", "name": "All", "tier": "platinum"}]},
    "aliases": {"achievements": [{"external_id": "a", "name": "A", "aliases": ["ak1:old"]}]},
}


def _response(fields: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"title": "Portal 2", **fields}}


def test_the_host_contract_is_exactly_1_1_20():
    assert PLUGIN_API_CONTRACT_VERSION == "1.1.20"


@pytest.mark.parametrize("name", sorted(NEW_FIELDS))
def test_a_1_1_20_field_from_a_1_1_19_plugin_is_refused(name):
    with pytest.raises(ValidationError, match="require Plugin API 1.1.20"):
        ProviderResponse.model_validate(_response(NEW_FIELDS[name]), context=OLD)
    # the same response from a 1.1.20 plugin is accepted
    ProviderResponse.model_validate(_response(NEW_FIELDS[name]), context=NEW)


def test_a_1_1_19_plugin_without_new_fields_is_answered_as_before():
    old = ProviderResponse.model_validate(LEGACY_PAYLOAD, context=OLD)
    new = ProviderResponse.model_validate(LEGACY_PAYLOAD, context=NEW)
    assert old == new
    patch = old.metadata
    assert patch is not None
    assert (patch.first_release, patch.releases, patch.identity) == (None, (), None)
    assert patch.achievements[0].aliases == ()
    # host code (core providers, page merging) builds models without a declared contract
    MetadataAchievement(external_id="p", name="All", tier="platinum", aliases=("x",))


def test_a_malformed_declared_version_is_not_trusted():
    with pytest.raises(ValueError):
        contract_context("one.two")


@pytest.mark.parametrize(
    ("release", "accepted"),
    [
        ({"date": "2011", "precision": "year"}, True),
        ({"date": "2011-04", "precision": "month"}, True),
        ({"date": "2011-04-19", "precision": "day"}, True),
        ({"date": "1931-07-01", "precision": "day"}, True),  # a pre-1950 day (A14.11)
        ({"date": "2011", "precision": "day"}, False),  # shape and precision disagree
        ({"date": "2011-04", "precision": "year"}, False),
        ({"date": "2011-02-30", "precision": "day"}, False),  # not a calendar date
        ({"date": "2011-13", "precision": "month"}, False),
        ({"date": "1931", "precision": "year"}, False),  # years and months: 1950-2100
        ({"date": "2101-01", "precision": "month"}, False),
        ({"date": "2011-Q3", "precision": "year"}, False),  # quarters are reserved
        ({"date": "2011", "precision": "quarter"}, False),
        ({"date": "2011", "precision": "year", "region": "EU"}, False),
        ({"date": "2011", "precision": "year", "region": "europe"}, False),
        ({"date": "2011", "precision": "year", "region": "jp", "category": "port"}, True),
        ({"date": "2011", "precision": "year", "category": "dlc"}, False),
        ({"date": "2011", "precision": "year", "platform": "p" * 129}, False),
        ({"date": "2011", "precision": "year", "edition": "e" * 101}, False),
    ],
)
def test_release_entries_are_validated(release, accepted):
    if accepted:
        MetadataRelease.model_validate(release)
    else:
        with pytest.raises(ValidationError):
            MetadataRelease.model_validate(release)


def test_releases_are_bounded_unique_and_agree_with_release_date():
    entry = {"date": "2011", "precision": "year"}
    six = [{**entry, "platform": f"p{index}"} for index in range(6)]
    with pytest.raises(ValidationError):
        MetadataPatch.model_validate({"releases": six})
    with pytest.raises(ValidationError, match="repeat"):
        MetadataPatch.model_validate({"releases": [entry, entry]})
    with pytest.raises(ValidationError, match="same day"):
        MetadataPatch.model_validate(
            {
                "release_date": "2011-04-19",
                "first_release": {"date": "2011-04-18", "precision": "day"},
            }
        )
    agreed = MetadataPatch.model_validate(
        {"release_date": "2011-04-19", "first_release": {"date": "2011-04-19", "precision": "day"}}
    )
    assert agreed.first_release is not None
    # a year first_release does not have to repeat a legacy day
    MetadataPatch.model_validate(
        {"release_date": "2011-04-19", "first_release": {"date": "2011", "precision": "year"}}
    )


def test_identity_verdicts_are_closed_and_bounded():
    with pytest.raises(ValidationError):
        MetadataPatch.model_validate({"identity": {"match": "probably"}})
    previous = {f"ns{index}": "x" for index in range(9)}
    with pytest.raises(ValidationError):
        MetadataPatch.model_validate({"identity": {"match": "redirect", "previous_ids": previous}})


def test_a_provider_can_only_move_ids_in_its_own_namespaces():
    response = ProviderResponse.model_validate(
        {
            "metadata": {
                "identity": {
                    "match": "redirect",
                    "previous_ids": {"atlas": "atlas:OLD", "steam": "620"},
                }
            }
        }
    )
    confined = confine_to_namespaces(response, {"atlas"})
    assert confined.metadata is not None and confined.metadata.identity is not None
    assert confined.metadata.identity.previous_ids == {"atlas": "atlas:OLD"}


def test_the_contract_enums_are_the_shared_vocabulary():
    achievement = MetadataAchievement.model_fields
    assert tuple(get_args(get_args(achievement["tier"].annotation)[0])) == ACHIEVEMENT_TIERS
    assert tuple(get_args(get_args(achievement["kind"].annotation)[0])) == ACHIEVEMENT_KINDS
    crossplay = get_args(get_args(MetadataPatch.model_fields["crossplay"].annotation)[0])
    assert set(crossplay) == set(CROSSPLAY)
    release = MetadataRelease.model_fields
    assert tuple(get_args(release["precision"].annotation)) == DATE_PRECISIONS
    assert tuple(get_args(release["category"].annotation)) == RELEASE_CATEGORIES
    # every relation type is a kind the host's relationship graph can store
    assert set(RELATION_TYPES) <= {kind.value for kind in RelationshipKind}
    assert set(export()) == {
        "version",
        "achievement_tier",
        "achievement_kind",
        "crossplay",
        "relation_type",
        "date_precision",
        "release_category",
        "book_format",
        "book_creator_role",
    }


# --- through the plugin adapter, as a refresh or search reaches a plugin ---------------------


@dataclass
class _Runtime:
    payload: dict[str, Any]

    async def action(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return self.payload


async def _invoke(monkeypatch, declared: str, payload: dict[str, Any]) -> ProviderResponse:
    user_id = uuid4()
    adapter = metadata_providers.PluginMetadataProvider(
        declaration=MetadataProviderRegistration(
            provider_id="official.metadata-atlas.atlas",
            name="Atlas",
            media_types=("game",),
            identifier_namespace="atlas",
            operations=ProviderOperations(metadata="metadata", health="health"),
        ),
        plugin_id="official.metadata-atlas",
        installation_id=uuid4(),
        user_id=user_id,
        revision="r1",
        state=ProviderHealth.HEALTHY,
    )
    state = {"plugin_id": adapter.plugin_id, "api_contract_version": declared}

    async def authorized_state(_self, _operation):
        return state

    monkeypatch.setattr(
        metadata_providers.PluginMetadataProvider, "_authorized_state", authorized_state
    )
    monkeypatch.setattr(metadata_providers, "PluginRuntimeClient", lambda: _Runtime(payload))
    request = MetadataProviderRequest(request_id=uuid4(), user_id=user_id, query="Portal 2")
    return await adapter.invoke("metadata", request)


async def test_the_adapter_refuses_a_1_1_20_field_from_a_1_1_19_plugin(monkeypatch):
    payload = _response({**NEW_FIELDS["first_release"], "provider_ids": {"atlas": "atlas:X"}})
    refused = await _invoke(monkeypatch, "1.1.19", payload)
    assert refused.failure is not None and refused.failure.code == "invalid_response"
    assert refused.metadata is None

    accepted = await _invoke(monkeypatch, "1.1.20", payload)
    assert accepted.failure is None and accepted.metadata is not None
    assert accepted.metadata.first_release == MetadataRelease(date="2011", precision="year")


async def test_the_adapter_answers_an_older_plugin_without_new_fields_as_before(monkeypatch):
    for declared in ("1.1.15", "1.1.19"):
        answered = await _invoke(monkeypatch, declared, LEGACY_PAYLOAD)
        assert answered.failure is None and answered.metadata is not None
        assert answered.metadata.crossplay == "partial"
        assert answered.metadata.release_date.isoformat() == "2011-04-19"


async def test_platinum_is_refused_before_the_plugin_declares_1_1_20(monkeypatch):
    payload = {"metadata": NEW_FIELDS["platinum"]}
    refused = await _invoke(monkeypatch, "1.1.19", payload)
    assert refused.failure is not None and refused.failure.code == "invalid_response"
    accepted = await _invoke(monkeypatch, "1.1.20", payload)
    assert accepted.metadata is not None and accepted.metadata.achievements[0].tier == "platinum"
