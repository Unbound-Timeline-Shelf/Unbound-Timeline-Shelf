"""Which providers confirmed an id-matched refresh result, and the result without the rest.

A record merges every source's metadata, so a value in it cannot be traced back to the
provider that reported it, and an id match from any one provider would otherwise let every
other source's (possibly mismatched) fields and ids ride along unconfirmed (#492 review).
These helpers compute, and then apply, which providers actually confirmed the game."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .handler import Provider, Source

# identity verdicts (Plugin API 1.1.20, C4.4) that stand in for a held-id namespace match:
# the provider itself found this record by id, not by an unreliable title fallback (#492).
CORROBORATING_MATCHES = frozenset({"id", "redirect", "crosswalk"})


def provider_id_claims(
    sources: Mapping[str, Source], group: Iterable[str], providers: Iterable[Provider]
) -> dict[str, dict[str, list[str]]]:
    """The ids each registered provider itself reported for this result.

    A record's `provider_ids` also carries the ids the caller supplied, so it cannot tell
    a provider's confirmation from an echo of the request. A provider's claims are the
    ids in its own search candidate and metadata patches, already confined to the
    namespaces it owns. Every distinct value is kept, so a provider that names two ids for
    one namespace (a candidate and a patch disagreeing) reaches the refresh as a conflict."""
    namespaces = {provider.id: provider.declaration.identifier_namespace for provider in providers}
    claims: dict[str, dict[str, list[str]]] = {}

    def claim(provider_id: str, ids: Mapping[str, str]) -> None:
        for namespace, value in ids.items():
            values = claims.setdefault(provider_id, {}).setdefault(namespace, [])
            if value and value not in values:
                values.append(value)

    for key in group:
        source = sources[key]
        owner = source.candidate.provider
        if owner in namespaces:
            # A focused search result also carries its provider's own id under the
            # provider id (`identities`); the declared namespace already holds it.
            reported = {
                namespace: value
                for namespace, value in source.candidate.provider_ids.items()
                if namespace != owner or namespaces[owner] in (None, owner)
            }
            if reported:
                claim(owner, reported)
        for provider_id, patch in source.metadata.items():
            if provider_id in namespaces and patch.provider_ids:
                claim(provider_id, patch.provider_ids)
    return {
        provider_id: {namespace: values for namespace, values in ids.items() if values}
        for provider_id, ids in claims.items()
    }


def unverified_providers(
    sources: Mapping[str, Source],
    group: Iterable[str],
    providers: Iterable[Provider],
    held: Mapping[str, str],
    claims: Mapping[str, Mapping[str, list[str]]],
    *,
    asset_providers: Mapping[tuple[str, str], Iterable[str]] | None = None,
    assets: Iterable[Mapping[str, Any]] = (),
) -> set[str]:
    """Identity-bearing providers that reached this result without confirming the game.

    A provider confirms the game when an id it reported itself is one the lookup held. A
    provider with an identifier namespace that reported only ids the game does not hold, or
    none at all, may have fallen back to a title lookup and found another game, so a
    refresh must not apply its fields or ids without the person's confirmation (#492
    review). A provider without a namespace (artwork-only sources) is looked up by the held
    ids and cannot report an identity either way, so it is not counted here.

    A provider's own identity verdict (Plugin API 1.1.20, C4.4) is also confirmation: a
    ``match`` of ``id``, ``redirect`` or ``crosswalk`` is identity evidence even when the
    provider's own id claim sits in a namespace the game does not hold yet (an Atlas patch
    matched by Steam id but reporting only its own ``atlas`` id, for one)."""
    namespaces = {provider.id: provider.declaration.identifier_namespace for provider in providers}
    group = tuple(group)
    asset_providers = asset_providers or {}
    verdicts = {
        provider_id: patch.identity.match
        for key in group
        for provider_id, patch in sources[key].metadata.items()
        if patch.identity is not None
    }
    contributed = set(claims)
    contributed.update(provider_id for key in group for provider_id in sources[key].metadata)
    for asset in assets:
        contributed.update(asset_providers.get((asset["kind"], asset["url"]), ()))
    return unverified_from_evidence(verdicts, claims, contributed, namespaces, held)


def unverified_from_evidence(
    verdicts: Mapping[str, str],
    claims: Mapping[str, Mapping[str, list[str]]],
    contributed: Iterable[str],
    namespaces: Mapping[str, str | None],
    held: Mapping[str, str],
) -> set[str]:
    """The ``held``-dependent half of ``unverified_providers``, split out so a route that locked
    the game after the lookup ran can re-decide against the row it actually applies to, rather
    than reusing a verdict computed from the ids the lookup started with (review finding 4):
    while a provider request is in flight, an editor can change the game's id in a namespace a
    *different* source still confirms by its OWN namespace, which tolerates the game's id
    differing elsewhere; the refresh must not keep treating a provider as verified against ids
    that are no longer the ones on the row it is about to write to."""

    def confirms(provider_id: str) -> bool:
        if verdicts.get(provider_id) in CORROBORATING_MATCHES:
            return True
        return any(
            held.get(namespace) in values
            for namespace, values in claims.get(provider_id, {}).items()
        )

    return {
        provider_id
        for provider_id in contributed
        if namespaces.get(provider_id) and not confirms(provider_id)
    }


def verified_record(
    sources: Mapping[str, Source],
    group: Iterable[str],
    unverified: set[str],
    *,
    merged_of: Callable[[Sequence[Any]], tuple[dict[str, Any], dict[str, str]]],
    provider_names: Mapping[str, str] | None = None,
    asset_providers: Mapping[tuple[str, str], Iterable[str]] | None = None,
    assets: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The result's metadata, artwork and field attribution without what the unverified
    providers supplied.

    A value an unverified provider contributed is already excluded from ``members``, but a
    merged record's ``field_providers`` (who gets credit for each field) was, until review
    finding 6, always built from the full, unfiltered merge: a verified lower-priority provider
    agreeing with an unverified higher-priority one on a field's value then had that field
    correctly filled from the verified source, but still attributed to the unverified one. The
    attribution is recomputed from the same filtered ``members`` the value came from."""
    asset_providers = asset_providers or {}
    provider_names = provider_names or {}
    members = [
        replace(
            cast(Any, sources[key]),
            metadata={
                provider_id: patch
                for provider_id, patch in sources[key].metadata.items()
                if provider_id not in unverified
            },
        )
        for key in group
    ]
    verified_assets = []
    for asset in assets:
        contributing = set(asset_providers.get((asset["kind"], asset["url"]), ()))
        if not contributing or contributing - unverified:
            verified_assets.append(asset)
    metadata, origins = merged_of(members)
    return {
        "verified_metadata": metadata,
        "verified_assets": verified_assets,
        "verified_field_providers": {
            name: provider_names[provider_id]
            for name, provider_id in origins.items()
            if provider_id in provider_names
        },
    }


def reverify_providers(
    sources: Mapping[str, Source],
    group: Iterable[str],
    providers: Iterable[Provider],
    claims: Mapping[str, Mapping[str, list[str]]],
    held: Mapping[str, str],
    *,
    merged_of: Callable[[Sequence[Any]], tuple[dict[str, Any], dict[str, str]]],
    provider_names: Mapping[str, str] | None = None,
    asset_providers: Mapping[tuple[str, str], Iterable[str]] | None = None,
    assets: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Recompute ``unverified_providers``/``verified_record`` against a fresh ``held``: a route
    that locked the game row after the lookup ran must check providers' claims against the ids
    that row actually has, not the ones the lookup started with (review finding 4)."""
    unverified = unverified_providers(
        sources,
        group,
        providers,
        dict(held),
        claims,
        asset_providers=asset_providers,
        assets=assets,
    )
    result: dict[str, Any] = {"unverified_providers": sorted(unverified)}
    if unverified:
        result.update(
            verified_record(
                sources,
                group,
                unverified,
                merged_of=merged_of,
                provider_names=provider_names,
                asset_providers=asset_providers,
                assets=assets,
            )
        )
    return result
