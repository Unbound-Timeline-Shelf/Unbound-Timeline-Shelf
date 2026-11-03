"""What each registered provider itself said about a result (Plugin API 1.1.20).

A record merges every source's metadata, so a value in it cannot be traced back to the provider
that reported it. An identity verdict and a game relation both belong to one provider: a refresh
follows a redirect only in that provider's namespaces (C4.4 rule 2), and a relation's target is
named in that provider's namespace (C6.4). These helpers read them per provider before the merge.
"""

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from src.plugin_api.metadata_contracts import MetadataPatch
from src.plugin_api.vocab import RELATION_TYPES

# a refresh considers at most this many relations from one result
MAX_RELATIONS = 64


# This structural contract describes a data field, not an object with operations.
class _Source(Protocol):  # pylint: disable=too-few-public-methods
    metadata: dict[str, MetadataPatch]


def _patches(
    sources: Mapping[str, _Source], group: Iterable[str], registered: Iterable[str]
) -> Iterable[tuple[str, MetadataPatch]]:
    allowed = set(registered)
    for key in group:
        for provider_id, patch in sources[key].metadata.items():
            if provider_id in allowed:
                yield provider_id, patch


def provider_identities(
    sources: Mapping[str, _Source], group: Iterable[str], registered: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """The identity verdict each registered provider gave this result."""
    return {
        provider_id: patch.identity.model_dump(mode="json")
        for provider_id, patch in _patches(sources, group, registered)
        if patch.identity is not None
    }


def provider_relations(
    sources: Mapping[str, _Source], group: Iterable[str], namespaces: Mapping[str, str | None]
) -> list[dict[str, Any]]:
    """Game relations in the shared vocabulary, each with the ids that name its target.

    ``namespaces`` maps each registered provider to its declared identifier namespace. A target
    is named by the candidate's ``provider_ids`` (already confined to the provider's own
    namespaces) and, when the candidate is the provider's own record, by its ``external_id``
    in the provider's namespace. Relation types outside the vocabulary (anime and film
    relations, recommendations) are not game relations and are left out."""
    found: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
    for provider_id, patch in _patches(sources, group, namespaces):
        own = namespaces[provider_id] or provider_id
        for relation in patch.relations:
            if relation.relation not in RELATION_TYPES:
                continue
            candidate = relation.candidate
            targets = dict(candidate.provider_ids)
            if candidate.provider in {provider_id, own}:
                targets.setdefault(own, candidate.external_id)
            if not targets:
                continue
            key = (relation.relation, tuple(sorted(targets.items())))
            found.setdefault(
                key, {"kind": relation.relation, "targets": targets, "provider": provider_id}
            )
            if len(found) >= MAX_RELATIONS:
                return list(found.values())
    return list(found.values())
