"""Builds the stubbed API answers for the screenshots from the real parser.

The rows (titles, authors, ISBNs, statuses, last read dates) come from
``features.imports.books`` run on ``goodreads-synthetic.csv``; only what needs
a database or a provider (which rows are already in the library, the provider
list, the import counts) is stated here. Synthetic books only.
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from src.features.imports.book_matching import match_books
from src.features.imports.books import parse_book_import
from src.plugin_api.metadata_contracts import MetadataCandidate, ProviderResponse


class StandIn:
    """A stand-in for the Atlas plugin as it is today: no batched ``resolve`` (metadata-atlas does
    not declare it yet), so the host searches it row by row. It knows one book by ISBN and one by
    title."""

    name = "Atlas"
    declaration = SimpleNamespace(
        operations=SimpleNamespace(resolve=None, search="search"),
        provider_id="atlas.books",
        identifier_namespace="atlas",
    )

    async def invoke(self, operation, request, candidate=None):
        assert operation == "search"
        known = {
            "9781111111113": ("The Glass Orchard", ["Ada Quill"], "9781111111113"),
            "Salt and Ember Cora Vale": ("Salt and Ember", ["Cora Vale"], None),
        }
        hit = known.get(request.options.get("isbn") or request.query)
        if not hit:
            return ProviderResponse()
        return ProviderResponse(candidates=(MetadataCandidate(
            external_id="atlas:" + hit[0].replace(" ", ""), title=hit[0], provider="atlas",
            media_type="book", authors=tuple(hit[1]), isbn_13=hit[2],
            provider_ids={"atlas": "atlas:x"}),))


here = Path(sys.argv[1])
parsed = parse_book_import((here / "goodreads-synthetic.csv").read_bytes(), "goodreads")
ALREADY_HERE = {"Ninth Lantern"}
rows = [
    {
        "line": row.line,
        "title": row.title,
        "authors": row.authors[:3],
        "isbn_13": row.isbn_13,
        "invalid_isbn": row.invalid_isbn,
        "status": row.status.value,
        "last_read": next(
            (r.finished_on.isoformat() for r in reversed(row.reads) if r.finished_on), None
        ),
        "already_here": row.title in ALREADY_HERE,
        "repeated": False,
    }
    for row in parsed.rows
]
new = sum(not r["already_here"] for r in rows)
preview = {
    "source": "goodreads",
    "total": len(rows),
    "new": new,
    "already_here": len(rows) - new,
    "repeated": 0,
    "with_isbn": sum(1 for r in rows if r["isbn_13"]),
    "invalid_isbn": sum(1 for r in rows if r["invalid_isbn"]),
    "invalid_entries": [],
    "providers": [{"name": "Atlas", "batched": False}],
    "rows": rows,
}
stand_in = StandIn()
new_rows = [row for row in parsed.rows if row.title not in ALREADY_HERE]
report = asyncio.run(match_books(new_rows, [stand_in], uuid4()))
by = [m.matched_by for m in report.matches.values()]
result = {
    "source": "goodreads",
    "total": len(rows),
    "created": new,
    "kept": len(rows) - new,
    "repeated": 0,
    "matched_by_isbn": by.count("identifier"),
    "matched_by_title": by.count("title"),
    "manual": new - len(by),
    "resolve_calls": report.resolve_calls,
    "row_lookups": report.row_lookups,
    "not_looked_up": 0,
    "failed_providers": [],
    "invalid_isbn": preview["invalid_isbn"],
    "invalid_entries": [],
    "errors": [],
}
(here / "stub-answers.json").write_text(
    json.dumps({"preview": preview, "result": result}, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(preview["rows"], indent=1))
