"""Plugin API 1.1.20 for books (C15 6.3-6.4, T-H1, T-H2, T-H4, T-H5) and the contract range
(C16 22.6, W-6).

Books join the one Plugin API release, 1.1.20: ``MediaType.BOOK``, five media types,
``MetadataPatch.book``, a candidate's ``authors`` and ``isbn_13`` and the reserved ``isbn``
request option are accepted only from a plugin that declares 1.1.20, and every one of these gates
compares against the single ``UNIFIED_CONTRACT``. A plugin may also declare ``api_contract_max``;
the host then runs it at min(that maximum, host maximum)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.routes.metadata import book_lookup_options
from src.database.models.book import BookFormat
from src.features.metadata import providers as metadata_providers
from src.plugin_api import base_contracts, metadata_contracts
from src.plugin_api.base_contracts import PLUGIN_API_CONTRACT_VERSION, UNIFIED_CONTRACT
from src.plugin_api.compatibility import (
    host_plugin_api,
    manifest_compatibility_checks,
    negotiated_contract,
    plugin_contract_compatibility_reason,
)
from src.plugin_api.metadata_contracts import (
    ISBN_OPTION,
    MediaType,
    MetadataBook,
    MetadataBookAuthor,
    MetadataBookEdition,
    MetadataCandidate,
    MetadataPatch,
    MetadataProviderRegistration,
    MetadataProviderRequest,
    ProviderHealth,
    ProviderOperations,
    ProviderResponse,
    contract_context,
)
from src.plugin_api.vocab import BOOK_CREATOR_ROLES, BOOK_FORMATS

OLD = contract_context("1.1.19")
NEW = contract_context("1.1.20")
REPOSITORY = Path(__file__).resolve().parents[3]

# Synthetic books only (Open Library data is not redistributable).
DUNE_LIKE = {
    "authors": [
        {"name": "Ada Quill", "role": "author"},
        {"name": "Ben Marsh", "role": "translator"},
    ],
    "subtitle": "A Lantern Archive Novel",
    "isbn_13": "9780000000002",
    "page_count": 412,
    "series_name": "Lantern Archive",
    "series_position": "1",
    "original_language": "en",
    "first_published": {"date": "1813", "precision": "year"},
    "editions": [
        {
            "key": "ED1",
            "name": "Paperback, Harbor 2005",
            "isbn_13": "9780000000002",
            "format": "paperback",
            "publisher": "Harbor Books",
            "publish_date": {"date": "2005-08", "precision": "month"},
            "page_count": 528,
            "language": "en",
        },
        {"key": "ED2", "isbn_10": "0000000019", "format": "audiobook"},
    ],
}


def _book_response(**metadata: Any) -> dict[str, Any]:
    return {"metadata": {"title": "The Lantern Archive", **metadata}}


def _registration(media_types: list[str]) -> dict[str, Any]:
    return {
        "provider_id": "official.metadata-books.books",
        "name": "Books",
        "media_types": media_types,
        "operations": {"search": "search", "metadata": "metadata", "health": "health"},
    }


# --- T-H5: one constant ----------------------------------------------------------------------


def test_every_gate_uses_the_one_unified_constant():
    # M7's name and the book gate are the same object, and the host's own contract equals it
    assert metadata_contracts.UNIFIED_CONTRACT is UNIFIED_CONTRACT
    assert metadata_contracts.RELEASES_CONTRACT is UNIFIED_CONTRACT
    assert base_contracts.parse_semver(PLUGIN_API_CONTRACT_VERSION) == UNIFIED_CONTRACT
    source = (REPOSITORY / "src/plugin-runtime/runtime.py").read_text(encoding="utf-8")
    runtime = re.search(r'^PLUGIN_API_CONTRACT_VERSION = "([^"]+)"', source, re.MULTILINE)
    assert runtime is not None and runtime.group(1) == PLUGIN_API_CONTRACT_VERSION
    since = re.search(r"^_CONTRACT_RANGE_SINCE = \((\d+), (\d+), (\d+)\)", source, re.MULTILINE)
    assert since is not None and tuple(map(int, since.groups())) == UNIFIED_CONTRACT


def test_the_refusal_message_is_built_from_the_constant(monkeypatch):
    monkeypatch.setattr(metadata_contracts, "UNIFIED_CONTRACT", (1, 1, 21))
    monkeypatch.setattr(metadata_contracts, "UNIFIED_CONTRACT_VERSION", "1.1.21")
    with pytest.raises(ValidationError, match="book require Plugin API 1.1.21"):
        ProviderResponse.model_validate(_book_response(book={}), context=NEW)


# --- T-H1: book fields from an older plugin --------------------------------------------------

BOOK_FIELDS: dict[str, dict[str, Any]] = {
    "book": _book_response(book=DUNE_LIKE),
    "null book": _book_response(book=None),
    "candidate media_type": {
        "candidates": [{"external_id": "W1", "title": "T", "provider": "p", "media_type": "book"}]
    },
    "candidate authors": {
        "candidates": [{"external_id": "W1", "title": "T", "provider": "p", "authors": ["A"]}]
    },
    "candidate isbn_13": {
        "candidates": [
            {"external_id": "W1", "title": "T", "provider": "p", "isbn_13": "9780000000002"}
        ]
    },
    "relation to a book": _book_response(
        relations=[
            {
                "candidate": {
                    "external_id": "W2",
                    "title": "T2",
                    "provider": "p",
                    "media_type": "book",
                },
                "relation": "sequel",
            }
        ]
    ),
}


@pytest.mark.parametrize("name", sorted(BOOK_FIELDS))
def test_book_fields_refused_below_unified_contract(name):
    with pytest.raises(ValidationError, match="require Plugin API 1.1.20"):
        ProviderResponse.model_validate(BOOK_FIELDS[name], context=OLD)
    ProviderResponse.model_validate(BOOK_FIELDS[name], context=NEW)


def test_an_older_plugin_without_book_fields_is_answered_as_before():
    legacy = {"metadata": {"title": "Portal 2", "developer": "Valve"}}
    assert ProviderResponse.model_validate(legacy, context=OLD) == ProviderResponse.model_validate(
        legacy, context=NEW
    )


# --- T-H2: registration ----------------------------------------------------------------------


def test_media_types_five_allowed_only_unified():
    every = [media.value for media in MediaType]
    assert len(every) == 5
    registration = MetadataProviderRegistration.model_validate(_registration(every), context=NEW)
    assert MediaType.BOOK in registration.media_types
    with pytest.raises(ValidationError, match="media_types book require Plugin API 1.1.20"):
        MetadataProviderRegistration.model_validate(_registration(every), context=OLD)
    # a 1.1.19 plugin still registers its four types
    MetadataProviderRegistration.model_validate(_registration(every[:4]), context=OLD)
    # a stored row is read without a context, so it never becomes unreadable
    MetadataProviderRegistration.model_validate(_registration(["book"]))
    with pytest.raises(ValidationError):
        MetadataProviderRegistration.model_validate(_registration(every + ["game"]))


def test_the_media_types_bound_follows_the_enum():
    field = MetadataProviderRegistration.model_fields["media_types"]
    assert any(getattr(item, "max_length", None) == len(MediaType) for item in field.metadata)


@dataclass
class _Runtime:
    document: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)

    async def plugin_ui(self, _plugin_id: str) -> dict[str, Any]:
        return self.document

    async def plugin_state(self, _plugin_id: str) -> dict[str, Any]:
        return self.state


class _Database:
    def __init__(self) -> None:
        self.added: list[Any] = []

    async def get(self, *_args: Any) -> None:
        return None

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        return None


def _ui(contract: str) -> dict[str, Any]:
    return {
        "api_contract_version": contract,
        "actions": [
            {"id": name, "capability": {"name": f"metadata_providers.{name}"}}
            for name in ("search", "metadata", "health")
        ],
    }


async def test_registration_upsert_refuses_book_from_an_older_plugin(monkeypatch):
    plugin_id = "official.metadata-books"
    monkeypatch.setattr(metadata_providers, "PluginRuntimeClient", lambda: _Runtime(_ui("1.1.19")))
    with pytest.raises(ValidationError, match="require Plugin API 1.1.20"):
        await metadata_providers.register_provider(
            _Database(), plugin_id, uuid4(), _registration(["book"])
        )
    database = _Database()
    await metadata_providers.register_provider(
        database, plugin_id, uuid4(), _registration(["movie"])
    )
    assert database.added and database.added[0].declaration["media_types"] == ["movie"]

    monkeypatch.setattr(metadata_providers, "PluginRuntimeClient", lambda: _Runtime(_ui("1.1.20")))
    database = _Database()
    await metadata_providers.register_provider(
        database, plugin_id, uuid4(), _registration(["book", "movie"])
    )
    assert database.added[0].declaration["media_types"] == ["book", "movie"]


async def test_registration_is_gated_on_the_negotiated_contract(monkeypatch):
    """The same contract ``invoke`` and discovery use (C16 22.6), not only the declared one."""
    plugin_id = "official.metadata-books"
    # Declared 1.1.19 but negotiated up to 1.1.20 through api_contract_max: book registers.
    runtime = _Runtime(_ui("1.1.19"), {"negotiated_api_contract": "1.1.20"})
    monkeypatch.setattr(metadata_providers, "PluginRuntimeClient", lambda: runtime)
    database = _Database()
    await metadata_providers.register_provider(
        database, plugin_id, uuid4(), _registration(["book"])
    )
    assert database.added[0].declaration["media_types"] == ["book"]
    # A runtime that reports no negotiated contract falls back to the declared one.
    runtime = _Runtime(_ui("1.1.19"), {"negotiated_api_contract": None})
    monkeypatch.setattr(metadata_providers, "PluginRuntimeClient", lambda: runtime)
    with pytest.raises(ValidationError, match="require Plugin API 1.1.20"):
        await metadata_providers.register_provider(
            _Database(), plugin_id, uuid4(), _registration(["book"])
        )


# --- the book contract itself ----------------------------------------------------------------


def test_a_book_patch_is_validated_and_bounded():
    book = MetadataPatch.model_validate({"book": DUNE_LIKE}, context=NEW).book
    assert book is not None
    assert [author.name for author in book.authors] == ["Ada Quill", "Ben Marsh"]
    assert book.first_published is not None and book.first_published.date == "1813"
    assert book.editions[1].isbn_10 == "0000000019"
    with pytest.raises(ValidationError):
        MetadataBook.model_validate({"authors": [{"name": f"A{i}"} for i in range(31)]})
    with pytest.raises(ValidationError):
        MetadataBook.model_validate({"editions": [{"key": f"E{i}"} for i in range(51)]})
    with pytest.raises(ValidationError, match="repeat an ISBN-13"):
        MetadataBook.model_validate(
            {"editions": [{"isbn_13": "9780000000002"}, {"isbn_13": "9780000000002"}]}
        )
    with pytest.raises(ValidationError):
        MetadataBookAuthor.model_validate({"name": "A", "role": "ghostwriter"})
    with pytest.raises(ValidationError):
        MetadataBookEdition.model_validate({"format": "scroll"})


@pytest.mark.parametrize(
    ("fields", "accepted"),
    [
        ({"isbn_13": "9780000000002"}, True),
        ({"isbn_13": "9790000000001"}, True),  # a 979 ISBN has no ISBN-10
        ({"isbn_13": "9780000000003"}, False),  # wrong check digit
        ({"isbn_13": "978-0-00-000000-2"}, False),  # not normalised
        ({"isbn_10": "0000000019"}, True),
        ({"isbn_10": "000000001X"}, False),  # wrong check digit
        ({"isbn_10": "080442957x"}, False),  # lowercase x is not normalised
        ({"isbn_10": "080442957X"}, True),
        ({"isbn_13": "9780000000002", "isbn_10": "0000000019"}, False),  # different books
        ({"isbn_13": "9780000000019", "isbn_10": "0000000019"}, True),
    ],
)
def test_isbns_must_be_normalised_and_checked(fields, accepted):
    if accepted:
        MetadataBookEdition.model_validate(fields)
    else:
        with pytest.raises(ValidationError):
            MetadataBookEdition.model_validate(fields)


@pytest.mark.parametrize(
    ("date", "accepted"),
    [
        ({"date": "1813", "precision": "year"}, True),  # older than C5.1's 1950 floor
        ({"date": "1965-08", "precision": "month"}, True),
        ({"date": "2005-08-01", "precision": "day"}, True),
        ({"date": "0999", "precision": "year"}, False),
        ({"date": "2101", "precision": "year"}, False),
        ({"date": "2005", "precision": "month"}, False),
        ({"date": "2005-02-30", "precision": "day"}, False),
    ],
)
def test_book_dates_are_partial_dates_in_book_years(date, accepted):
    if accepted:
        MetadataBook.model_validate({"first_published": date})
    else:
        with pytest.raises(ValidationError):
            MetadataBook.model_validate({"first_published": date})


def test_book_vocabularies_are_the_host_book_format_and_the_shared_roles():
    assert BOOK_FORMATS == tuple(item.value for item in BookFormat)
    edition_format = MetadataBookEdition.model_fields["format"].annotation
    assert set(get_args(get_args(edition_format)[0])) == set(BOOK_FORMATS)
    role = MetadataBookAuthor.model_fields["role"].annotation
    assert get_args(role) == BOOK_CREATOR_ROLES


def test_a_candidate_isbn_is_checked():
    with pytest.raises(ValidationError):
        MetadataCandidate(
            external_id="W", title="T", provider="p", media_type="book", isbn_13="9780000000003"
        )
    candidate = MetadataCandidate(
        external_id="W",
        title="T",
        provider="p",
        media_type="book",
        authors=("Ada Quill",),
        isbn_13="9780000000002",
    )
    assert candidate.authors == ("Ada Quill",)


# --- the isbn option -------------------------------------------------------------------------


def _request(**fields: Any) -> dict[str, Any]:
    return {"request_id": str(uuid4()), "user_id": str(uuid4()), "query": "q", **fields}


def test_the_isbn_option_is_a_normalised_isbn13_on_a_book_request():
    request = MetadataProviderRequest.model_validate(
        _request(media_type="book", options={ISBN_OPTION: "9780000000002"})
    )
    assert request.options[ISBN_OPTION] == "9780000000002"
    for bad in (
        _request(media_type="book", options={ISBN_OPTION: "978-0-00-000000-2"}),
        _request(media_type="book", options={ISBN_OPTION: 9780000000002}),
        _request(media_type="movie", options={ISBN_OPTION: "9780000000002"}),
    ):
        with pytest.raises(ValidationError, match="isbn option"):
            MetadataProviderRequest.model_validate(bad)
    with pytest.raises(ValidationError, match="require Plugin API 1.1.20"):
        MetadataProviderRequest.model_validate(_request(media_type="book"), context=OLD)


@pytest.mark.parametrize(
    ("media_type", "query", "options"),
    [
        (MediaType.BOOK, "978-0-00-000000-2", {ISBN_OPTION: "9780000000002"}),
        (MediaType.BOOK, "0 00 000001 9", {ISBN_OPTION: "9780000000019"}),
        (MediaType.BOOK, "9780000000003", {}),  # a wrong check digit is a title search
        (MediaType.BOOK, "The Lantern Archive", {}),
        (MediaType.MOVIE, "9780000000002", {}),
    ],
)
def test_an_isbn_typed_into_a_book_search_is_an_identifier_lookup(media_type, query, options):
    assert book_lookup_options(media_type, query) == options


# --- through the plugin adapter --------------------------------------------------------------


@dataclass
class _ActionRuntime:
    payload: dict[str, Any]

    async def action(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return self.payload


async def _invoke(monkeypatch, state: dict[str, Any], payload: dict[str, Any]) -> ProviderResponse:
    user_id = uuid4()
    adapter = metadata_providers.PluginMetadataProvider(
        declaration=MetadataProviderRegistration(
            provider_id="official.metadata-atlas.atlas",
            name="Atlas",
            media_types=("book",),
            identifier_namespace="atlas",
            operations=ProviderOperations(metadata="metadata", health="health"),
        ),
        plugin_id="official.metadata-atlas",
        installation_id=uuid4(),
        user_id=user_id,
        revision="r1",
        state=ProviderHealth.HEALTHY,
    )

    async def authorized_state(_self, _operation):
        return {"plugin_id": adapter.plugin_id, **state}

    monkeypatch.setattr(
        metadata_providers.PluginMetadataProvider, "_authorized_state", authorized_state
    )
    monkeypatch.setattr(metadata_providers, "PluginRuntimeClient", lambda: _ActionRuntime(payload))
    request = MetadataProviderRequest(
        request_id=uuid4(), user_id=user_id, query="Lantern", media_type=MediaType.BOOK
    )
    return await adapter.invoke("metadata", request)


async def test_the_adapter_holds_book_output_to_the_negotiated_contract(monkeypatch):
    payload = _book_response(book=DUNE_LIKE, provider_ids={"atlas": "atlas:01BOOK"})
    refused = await _invoke(monkeypatch, {"api_contract_version": "1.1.19"}, payload)
    assert refused.failure is not None and refused.failure.code == "invalid_response"
    accepted = await _invoke(monkeypatch, {"api_contract_version": "1.1.20"}, payload)
    assert accepted.metadata is not None and accepted.metadata.book is not None
    # the runtime's negotiated contract wins over the declared minimum
    negotiated = await _invoke(
        monkeypatch,
        {"api_contract_version": "1.1.20", "negotiated_api_contract": "1.1.20"},
        payload,
    )
    assert negotiated.failure is None
    garbled = await _invoke(monkeypatch, {"negotiated_api_contract": "one.two"}, payload)
    assert garbled.failure is not None and garbled.failure.code == "invalid_response"


def test_plugin_contract_prefers_the_negotiated_value():
    assert metadata_providers.plugin_contract({}) == "1.0.0"
    assert metadata_providers.plugin_contract({"api_contract_version": "1.1.19"}) == "1.1.19"
    assert (
        metadata_providers.plugin_contract(
            {"api_contract_version": "1.1.20", "negotiated_api_contract": "1.1.20"}
        )
        == "1.1.20"
    )


# --- W-6: api_contract_max -------------------------------------------------------------------


def _manifest(**fields: Any) -> dict[str, Any]:
    return {
        "plugin_id": "official.metadata-books",
        "name": "Books",
        "version": "1.0.0",
        "api_contract_version": "1.1.20",
        "entrypoint": "plugin:main",
        "sdk_version_range": ">=1.1.0",
        "application_version_range": ">=1.0.0",
        "integrity": {"sha256": "0" * 64},
        **fields,
    }


def test_the_manifest_accepts_a_contract_range_from_1_1_20():
    manifest = base_contracts.PluginManifest.model_validate(_manifest(api_contract_max="1.1.25"))
    assert manifest.api_contract_max == "1.1.25"
    assert base_contracts.PluginManifest.model_validate(_manifest()).api_contract_max is None
    for bad, message in (
        ({"api_contract_version": "1.1.19", "api_contract_max": "1.1.25"}, "requires"),
        ({"api_contract_max": "1.1.19"}, "not below"),
        ({"api_contract_max": "2.0.0"}, "same major"),
        ({"api_contract_max": "latest"}, "semantic version"),
    ):
        with pytest.raises(ValidationError, match=message):
            base_contracts.PluginManifest.model_validate(_manifest(**bad))


def test_the_negotiated_contract_is_the_highest_both_support():
    host = PLUGIN_API_CONTRACT_VERSION
    assert negotiated_contract("1.1.20", "1.1.25") == host
    assert negotiated_contract("1.1.20") == "1.1.20"
    assert negotiated_contract("1.1.15") == "1.1.15"
    assert negotiated_contract("1.1.20", "1.1.25", host_version="1.1.22") == "1.1.22"
    assert negotiated_contract("1.1.20", "1.1.21", host_version="1.1.30") == "1.1.21"
    # refused only when the plugin's minimum is above the host's maximum or the majors differ
    with pytest.raises(ValueError, match=r"1\.1\.21-1\.1\.25 is not supported .*1\.1\.0-1\.1\.20"):
        negotiated_contract("1.1.21", "1.1.25")
    with pytest.raises(ValueError, match="not supported by this host"):
        negotiated_contract("1.1.21")
    with pytest.raises(ValueError, match="needs api_contract_version 1.1.20"):
        negotiated_contract("1.1.19", "1.1.20")
    assert plugin_contract_compatibility_reason("1.1.20", declared_max="1.1.99") is None


def test_the_host_advertises_its_range():
    assert host_plugin_api() == {"min": "1.1.0", "max": PLUGIN_API_CONTRACT_VERSION}
    assert host_plugin_api(allow_legacy=True)["min"] == "1.0.0"


def test_the_compatibility_check_shows_the_declared_range():
    manifest = base_contracts.PluginManifest.model_validate(_manifest(api_contract_max="1.1.25"))
    checks = manifest_compatibility_checks(manifest, PLUGIN_API_CONTRACT_VERSION, "1.0.0")
    contract = next(check for check in checks if check["key"] == "api_contract")
    assert contract["status"] == "supported"
    assert contract["required"] == "1.1.20-1.1.25"


# --- T-H4: schema snapshots ------------------------------------------------------------------


def _shape(schema: Any) -> Any:
    """What a schema accepts, without prose a Pydantic release may word differently."""
    if isinstance(schema, dict):
        return {
            key: _shape(value)
            for key, value in schema.items()
            if key not in {"description", "title", "examples"}
        }
    if isinstance(schema, list):
        return [_shape(value) for value in schema]
    return schema


def test_the_published_schema_snapshots_match_the_models(tmp_path):
    snapshots = REPOSITORY / "wiki/docs/assets/plugin-metadata"
    exporter = REPOSITORY / "tools/export_metadata_contract.py"
    if not snapshots.is_dir() or not exporter.is_file():
        pytest.skip("the repository's wiki and tools are not in this checkout")
    namespace: dict[str, Any] = {"__name__": "export_metadata_contract"}
    exec(compile(exporter.read_text(encoding="utf-8"), str(exporter), "exec"), namespace)
    namespace["export"](tmp_path)
    for generated in sorted(tmp_path.glob("*.schema.json")):
        published = snapshots / generated.name
        assert _shape(json.loads(published.read_text(encoding="utf-8"))) == _shape(
            json.loads(generated.read_text(encoding="utf-8"))
        ), f"{generated.name} is stale: run tools/export_metadata_contract.py"
    registration = json.loads(
        (snapshots / "metadata-registration-v1.schema.json").read_text(encoding="utf-8")
    )
    assert "book" in registration["$defs"]["MediaType"]["enum"]
    response = json.loads(
        (snapshots / "metadata-response-v1.schema.json").read_text(encoding="utf-8")
    )
    assert "MetadataBook" in response["$defs"]
