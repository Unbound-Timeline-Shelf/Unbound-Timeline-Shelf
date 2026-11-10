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
    """A stand-in for the Atlas plugin: knows one book by ISBN and one by title."""

    name = "Atlas"
    declaration = SimpleNamespace(operations=SimpleNamespace(resolve="resolve", search="search"))

    def __init__(self):
        self.calls = 0

    async def resolve(self, request):
        self.calls += 1
        known = {
            "9781111111113": ("identifier", "The Glass Orchard", ["Ada Quill"]),
            "Salt and Ember": ("title", "Salt and Ember", ["Cora Vale"]),
        }
        answers = []
        for item in request.items:
            hit = known.get(item.isbn_13 or item.title or "")
            if hit:
                answers.append({"key": item.key, "matched_by": hit[0], "candidate": MetadataCandidate(
                    external_id="atlas:" + hit[1].replace(" ", ""), title=hit[1], provider="atlas",
                    media_type="book", authors=tuple(hit[2]), provider_ids={"atlas": "atlas:x"})})
        return ProviderResponse.model_validate({"resolved": answers})

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
    "providers": [{"name": "Atlas", "batched": True}],
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
    "row_lookups": 0,
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
